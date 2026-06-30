# Arquitectura — baxe-tunnel

Documentación técnica del funcionamiento interno. Para instrucciones de uso ver el [README](../README.md).

## Cómo funciona

```
Internet → Bridge (Cloud Run) ⇄ WebSocket persistente ⇄ Cliente (tu máquina)
           dominio fijo                                  localhost:8000
```

El cliente abre una conexión WebSocket saliente y persistente hacia el bridge — es un túnel inverso real, no un proxy que el bridge pueda alcanzar directamente. El bridge no puede conectarse a `localhost` de tu máquina (no existe ruta de red para eso); en cambio, reenvía cada request HTTP entrante a través de esa conexión WebSocket, el cliente lo ejecuta contra `TARGET_URL` y devuelve la respuesta por el mismo canal.

Ambos lados se autentican mutuamente en el handshake del WebSocket: el cliente debe presentar el `SEND_TOKEN` correcto, y el bridge debe responder con el `RECV_TOKEN` correcto.

---

## Estructura del proyecto

```
baxe-tunnel/
├── bridge/
│   └── main.py
│   └── requirements.txt
├── client/
│   ├── tunnel.py
│   ├── requirements.txt
│   └── .env.example
├── docs/
│   └── ARCHITECTURE.md
├── Dockerfile
├── .dockerignore
├── .gcloudignore
├── setup.py
└── README.md
```

El `Dockerfile` vive en la raíz del repo (no dentro de `bridge/`) para que `gcloud run deploy --source .` funcione directo desde root sin flags adicionales. Solo copia `bridge/main.py` y `bridge/requirements.txt` al construir la imagen.

---

## Bridge (`bridge/main.py`)

FastAPI app deployada en Cloud Run. Mantiene como mucho un túnel activo a la vez: la conexión WebSocket del cliente actual vive en `state["ws"]` (en memoria del proceso).

### Endpoints

| Método | Path | Descripción |
|--------|------|-------------|
| WS | `/_tunnel/ws` | Conexión persistente del cliente. Handshake + canal de mensajes `http_request`/`http_response`. |
| GET | `/_tunnel/status` | `{"connected": true/false}` según si hay un WebSocket activo. |

Cualquier otro request (`/{full_path:path}`, cualquier método) se trata como tráfico a reenviar al cliente conectado.

### Protocolo sobre el WebSocket

**Handshake:**

1. Cliente conecta a `wss://<bridge>/_tunnel/ws` y manda:
   ```json
   {"type": "hello", "send_token": "..."}
   ```
2. Bridge valida `send_token`. Si es inválido, o ya hay un túnel activo, responde `hello_rejected` y cierra la conexión:
   ```json
   {"type": "hello_rejected", "error": "invalid_send_token"}
   {"type": "hello_rejected", "error": "tunnel_already_active"}
   ```
3. Si es válido, responde:
   ```json
   {"type": "hello_ack", "recv_token": "..."}
   ```
   y el cliente queda como túnel activo.

**Reenvío de requests (bridge → cliente):**

Por cada request HTTP externo, el bridge genera un `id` único y manda:

```json
{
  "type": "http_request",
  "id": "a1b2c3d4-...",
  "method": "POST",
  "path": "/webhook/test",
  "query": [["key", "value"]],
  "headers": [["content-type", "application/json"]],
  "body_b64": "eyJldmVudCI6InBpbmcifQ=="
}
```

`headers` y `query` van como listas de pares (no dict) porque HTTP permite valores repetidos. El body siempre viaja en base64, sin distinguir texto de binario.

**Respuesta (cliente → bridge):**

```json
{
  "type": "http_response",
  "id": "a1b2c3d4-...",
  "status": 200,
  "headers": [["content-type", "application/json"]],
  "body_b64": "eyJvayI6dHJ1ZX0="
}
```

Si el cliente no pudo alcanzar `TARGET_URL`:

```json
{"type": "http_response", "id": "a1b2c3d4-...", "error": "target_unreachable", "detail": "Connection refused"}
```

El bridge correla cada respuesta con su request original por `id` (puede haber varios requests concurrentes compartiendo la misma conexión WebSocket). Si no hay túnel activo, responde `503 — No tunnel active`. Si el cliente no responde dentro de `REQUEST_TIMEOUT` (default 30s, configurable por env var), responde `504`.

### Variables de entorno del bridge

```env
SEND_TOKEN=        # token que el cliente debe enviar para conectarse
RECV_TOKEN=        # token que el bridge devuelve para que el cliente lo verifique
REQUEST_TIMEOUT=30 # segundos de espera por una respuesta del cliente antes de devolver 504
```

En Cloud Run, `PORT` lo inyecta la plataforma automáticamente — no hace falta configurarlo. La imagen expone 8080 como fallback para correrla localmente.

---

## Cliente (`client/tunnel.py`)

Script Python que corre como daemon en la máquina local. Toda la configuración viene de variables de entorno — no acepta argumentos de línea de comandos. Abre una conexión WebSocket persistente hacia el bridge y, por cada `http_request` que recibe, hace la llamada real contra `TARGET_URL` (con `httpx`) y devuelve la respuesta por el mismo canal. Procesa requests concurrentes en paralelo (una tarea async por request).

Al arrancar imprime la configuración completa en terminal antes de intentar conectar.

### Variables de entorno del cliente

```env
BRIDGE_URL=https://tu-bridge.run.app        # dirección del bridge en Cloud Run
TARGET_URL=http://localhost:8000            # puerto local a exponer
SEND_TOKEN=                                 # token que se envía al bridge
RECV_TOKEN=                                 # token que se espera del bridge
RECONNECT_BACKOFF=5                         # espera inicial entre reintentos (segundos)
RECONNECT_BACKOFF_MAX=60                    # tope máximo del backoff exponencial
```

### Log de arranque

```
╔══════════════════════════════════════════╗
║           baxe-tunnel client             ║
╚══════════════════════════════════════════╝

  BRIDGE_URL   https://tu-bridge.run.app
  TARGET_URL   http://localhost:8000
  SEND_TOKEN   aef3c1...d72  (64 chars)
  RECV_TOKEN   b72ca0...f91  (64 chars)
  BACKOFF      5s → 60s max

──────────────────────────────────────────
[2024-01-15 10:00:00] Conectando...
[2024-01-15 10:00:00] ✓ Tunnel activo → http://localhost:8000
```

Los tokens se muestran truncados — solo los primeros y últimos 3 caracteres.

Si falta alguna variable de entorno requerida, el cliente aborta inmediatamente:

```
[2024-01-15 10:00:00] ✗ Variable requerida no encontrada: SEND_TOKEN
                          Revisa tu archivo .env y vuelve a intentar.
```

---

## Reconexión automática

```
cliente arranca
    └── imprime configuración en terminal
    └── loop de reconexión con backoff
            ├── conecta WebSocket + handshake
            │       ├── éxito → backoff se resetea, queda escuchando requests
            │       │           hasta que la conexión se corte
            │       └── fallo / rechazo → log de error
            └── espera backoff, reintenta
```

Cloud Run cierra requests HTTP de larga duración (incluido WebSocket) después de un timeout configurable (hasta 60 minutos) — el cliente reconecta automáticamente cuando eso pase. Mientras el WebSocket está abierto, Cloud Run considera la instancia activa y no la apaga; cuando el cliente se desconecta (PC apagada, `Ctrl+C`), la instancia queda idle y Cloud Run puede escalarla a cero.

No hace falta polling separado para mantener viva la instancia: el WebSocket mismo actúa como keepalive. El ciclo natural es: conexión activa ~60 min → Cloud Run corta por timeout → cliente reconecta de inmediato → otros ~60 min.

### Backoff exponencial

```
intento 1 → espera 5s
intento 2 → espera 10s
intento 3 → espera 20s
intento 4 → espera 40s
intento 5+ → espera 60s (tope)
```

### RECV_TOKEN incorrecto (MITM)

```
[2024-01-15 10:00:01] ⚠️  Bridge no verificado — abortando
                          El RECV_TOKEN recibido no coincide con el esperado.
                          Verifica que ambos lados usen los mismos tokens.
```

Este es el único caso donde el daemon **no** reintenta — indica configuración incorrecta o MITM.

---

## Notas de seguridad

- Los tokens nunca van en el código fuente — solo en variables de entorno
- El bridge verifica `SEND_TOKEN` antes de aceptar la conexión del cliente
- El cliente verifica `RECV_TOKEN` antes de aceptar el tunnel — previene MITM
- Sin token válido en ambos sentidos, el tunnel no se establece
- Rotar tokens: generar nuevos valores y actualizar env vars en Cloud Run y local

---

## Despliegue del bridge en Cloud Run

El bridge es la única pieza que se despliega — el cliente nunca se containeriza ni se sube a la nube, corre siempre en la máquina local.

Como el bridge mantiene el túnel activo en memoria de proceso, **`max-instances=1` es imprescindible**: si Cloud Run corriera más de una instancia, un request HTTP externo podría caer en una instancia distinta a la que tiene el WebSocket activo y respondería 503.

`min-instances=0` está bien para uso personal — Cloud Run puede escalar a cero cuando el cliente se desconecta (PC apagada). Cuando `tunnel.py` arranca, su conexión WebSocket despierta la instancia. Los primeros 1-3 segundos del cold start pueden dar 503 si llega algo justo en ese momento, pero para uso propio eso es irrelevante.

El timeout hay que subirlo al máximo (60 minutos) para minimizar reconexiones.

```bash
gcloud run deploy baxe-tunnel-bridge \
  --source . \
  --min-instances=0 --max-instances=1 \
  --timeout=3600 \
  --allow-unauthenticated \
  --set-env-vars SEND_TOKEN=...,RECV_TOKEN=...
```

Nota de costo: con `min-instances=0` el servicio escala a cero cuando no está en uso — solo factura mientras `tunnel.py` está corriendo.

El `.gcloudignore` en la raíz excluye `client/`, docs y archivos no relevantes del build context.
