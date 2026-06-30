# baxe-tunnel

Tunnel HTTP para exponer un puerto local a internet a través de un bridge propio (Cloud Run), sin ngrok ni dependencias de terceros.

> Documentación técnica (arquitectura, protocolo, endpoints): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Casos de uso

- Recibir webhooks de WhatsApp / Stripe / GitHub en desarrollo local
- Exponer una API FastAPI local para pruebas con clientes externos
- Compartir un servidor de desarrollo sin ngrok ni dependencias externas

---

## Setup inicial (una sola vez)

### 1. Generar tokens y configurar el cliente

```bash
python setup.py
```

El script genera `SEND_TOKEN`/`RECV_TOKEN`, pide `BRIDGE_URL` (se puede dejar vacío si todavía no desplegaste el bridge) y `TARGET_URL`, y crea `client/.env` listo para usar. Al final imprime los tokens para pegarlos en la configuración del servicio en Cloud Run.

Si preferís hacerlo a mano:

```bash
python -c "import secrets; print('SEND_TOKEN=' + secrets.token_hex(32))"
python -c "import secrets; print('RECV_TOKEN=' + secrets.token_hex(32))"
cd client && cp .env.example .env   # y completar a mano
```

### 2. Desplegar el bridge en Cloud Run

Usando la [consola de Google Cloud](https://console.cloud.google.com/run):

1. **Cloud Run → Create Service**.
2. Elegí **Continuously deploy from a repository** (o **Deploy one revision from an existing container** si preferís subir la imagen vos mismo) y apuntá al repo — el `Dockerfile` está en la raíz, así que no hace falta indicar un subdirectorio de build.
3. En **Authentication**, marcá **Allow unauthenticated invocations** (la autenticación la maneja el doble token de la app, no IAM).
4. En **Variables & Secrets → Environment variables**, agregá `SEND_TOKEN` y `RECV_TOKEN` con los valores generados en el paso 1.
5. Creá el servicio. Cloud Run inyecta `PORT` automáticamente; la imagen ya escucha en 8080.

Al finalizar, la consola muestra la URL pública del servicio (algo como `https://baxe-tunnel-bridge-xxxxx.run.app`). Completá esa URL como `BRIDGE_URL` en `client/.env`.

### 3. Instalar dependencias del cliente

```bash
cd client
pip install -r requirements.txt
```

---

## Uso diario

### Conectar el tunnel

```bash
cd client
python tunnel.py
```

El cliente imprime su configuración, se conecta al bridge y queda corriendo en primer plano. `Ctrl+C` para cortar.

---

## Verificar que el tunnel está activo

```bash
curl https://tu-bridge.run.app/_tunnel/status
```

```json
{"connected": true, "target_url": "http://localhost:8000"}
```

---

## Variables de entorno del cliente (`client/.env`)

| Variable | Descripción | Default |
|----------|-------------|---------|
| `BRIDGE_URL` | URL del bridge en Cloud Run | — (requerida) |
| `TARGET_URL` | Puerto/servicio local a exponer | — (requerida) |
| `SEND_TOKEN` | Token que se envía al bridge | — (requerida) |
| `RECV_TOKEN` | Token que se espera del bridge | — (requerida) |
| `RECONNECT_BACKOFF` | Espera inicial entre reintentos (seg) | `5` |
| `RECONNECT_BACKOFF_MAX` | Tope del backoff exponencial (seg) | `60` |

Si falta alguna variable requerida, el cliente aborta inmediatamente sin intentar conectar.

---

## Troubleshooting

**`HTTP 503 — No tunnel active`** al pegarle al bridge → no hay ningún cliente conectado. Arrancá `tunnel.py`.

**`⚠️ Bridge no verificado — abortando`** → el `RECV_TOKEN` del cliente no coincide con el que devuelve el bridge. El cliente no reintenta en este caso (posible token mal copiado o bridge incorrecto). Verificá que los tokens en `client/.env` coincidan exactamente con los configurados en Cloud Run.

**`✗ Variable requerida no encontrada: ...`** → falta una variable en `client/.env`. Revisar contra `.env.example`.

**Reintentos infinitos con backoff** → el bridge no responde (caído, URL incorrecta, o problema de red). Verificar `BRIDGE_URL` y que el servicio esté arriba en Cloud Run.

---

## Rotar tokens

1. Generar nuevos valores (paso 1 del setup).
2. En la consola de GCP: **Cloud Run → tu servicio → Edit & Deploy New Revision → Variables & Secrets**, actualizar `SEND_TOKEN` y `RECV_TOKEN` con los nuevos valores y desplegar la revisión.
3. Actualizar `client/.env` con los mismos valores.
4. Reiniciar el cliente (`Ctrl+C` y volver a correr `python tunnel.py`).

---

## Notas de seguridad

- El bridge verifica `SEND_TOKEN` antes de registrar cualquier target
- El cliente verifica `RECV_TOKEN` antes de aceptar el tunnel — previene MITM
- Sin token válido en ambos sentidos, el tunnel no se establece
