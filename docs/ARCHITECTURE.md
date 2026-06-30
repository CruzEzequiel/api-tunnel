# Arquitectura — baxe-tunnel

Documentación técnica del funcionamiento interno. Para instrucciones de uso ver el [README](../README.md).

## Cómo funciona

```
Internet → Bridge (Cloud Run) → Tu máquina local
           dominio fijo          localhost:8000
```

El bridge actúa como proxy transparente: preserva método, path, headers, body y query params. Solo acepta conexiones que presenten el `SEND_TOKEN` correcto, y el cliente verifica que el bridge responda con el `RECV_TOKEN` correcto — ambos lados se autentican mutuamente.

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

FastAPI app deployada en Cloud Run. Recibe el tráfico de internet y lo reenvía al cliente local registrado. Es completamente stateless excepto por el `target_url` actual, que vive en memoria del proceso.

### Endpoints internos

| Método | Path | Descripción |
|--------|------|-------------|
| POST | `/_tunnel/connect` | Registra el target local |
| DELETE | `/_tunnel/disconnect` | Limpia el target |
| GET | `/_tunnel/status` | Estado actual del tunnel |

### Endpoint proxy

Cualquier request que no empiece con `/_tunnel/` se reenvía al target registrado preservando:
- Método HTTP
- Path completo
- Query params
- Headers (excepto `host` y `content-length`)
- Body

Si no hay ningún cliente conectado, responde `503 — No tunnel active`.

### Variables de entorno del bridge

```env
SEND_TOKEN=     # token que el cliente debe enviar para conectarse
RECV_TOKEN=     # token que el bridge devuelve para que el cliente lo verifique
```

En Cloud Run, `PORT` lo inyecta la plataforma automáticamente — no hace falta configurarlo. La imagen expone 8080 como fallback para correrla localmente.

---

## Cliente (`client/tunnel.py`)

Script Python que corre como daemon en la máquina local. Toda la configuración viene de variables de entorno — no acepta argumentos de línea de comandos. No expone ningún puerto ni levanta servidor: solo hace una request saliente (`POST /_tunnel/connect`) al bridge y, si tiene éxito, duerme indefinidamente. El bridge es quien reenvía tráfico hacia `TARGET_URL` directamente.

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

Al iniciar, el cliente imprime toda la configuración activa antes de intentar conectar:

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
```

Los tokens se muestran truncados — solo los primeros y últimos 3 caracteres — para verificar visualmente que son los correctos sin exponerlos completos.

Si falta alguna variable de entorno requerida, el cliente debe abortar inmediatamente con un mensaje claro indicando cuál falta — nunca conectar con configuración incompleta.

```
[2024-01-15 10:00:00] ✗ Variable requerida no encontrada: SEND_TOKEN
                          Revisa tu archivo .env y vuelve a intentar.
```

---

## Reconexión automática

El cliente corre como daemon. Solo necesita conectarse una vez al arrancar — después no hace nada más que existir. No hay heartbeat ni polling.

```
cliente arranca
    └── imprime configuración en terminal
    └── loop de conexión con backoff
            ├── POST /_tunnel/connect
            │       ├── éxito → queda conectado, duerme indefinidamente
            │       └── fallo → imprime error y espera backoff
            └── repetir hasta conectar
```

Si el bridge cae, Cloud Run lo levanta solo en el siguiente request (cold start) o mantiene la instancia mínima si está configurada. Si el cliente se reinicia manualmente, al arrancar vuelve a conectarse. El bridge sobreescribe el `target_url` con cada nueva conexión — sin estado que limpiar.

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
- El bridge verifica `SEND_TOKEN` antes de registrar cualquier target
- El cliente verifica `RECV_TOKEN` antes de aceptar el tunnel — previene MITM
- Sin token válido en ambos sentidos, el tunnel no se establece
- Rotar tokens: generar nuevos valores y actualizar env vars en Cloud Run y local

---

## Despliegue del bridge en Cloud Run

El bridge es la única pieza que se despliega — el cliente nunca se containeriza ni se sube a la nube, corre siempre en la máquina local.

El despliegue se hace desde la [consola de Google Cloud](https://console.cloud.google.com/run) (ver pasos en el [README](../README.md)), apuntando el servicio al repo. El `Dockerfile` está en la raíz para que Cloud Run lo detecte sin necesidad de indicar un subdirectorio de build. Cloud Run inyecta `PORT` automáticamente; el `Dockerfile` ya escucha en 8080 por defecto.

El `.gcloudignore` en la raíz excluye `client/`, docs y archivos no relevantes del build context.
