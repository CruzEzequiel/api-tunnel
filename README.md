# baxe-tunnel

Túnel inverso para exponer un puerto local a internet a través de un bridge propio (Cloud Run), sin ngrok ni dependencias de terceros.

```
Internet → Bridge (Cloud Run) ⇄ WebSocket persistente ⇄ start_listen.py (tu PC)
           URL pública fija                               localhost:PUERTO
```

El cliente abre una conexión WebSocket saliente hacia el bridge — no hay puertos que abrir ni configuración de red. El bridge reenvía cada request HTTP entrante por esa conexión, el cliente lo ejecuta contra tu app local y devuelve la respuesta.

> Documentación técnica (arquitectura, protocolo, WebSocket): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Casos de uso

- Recibir webhooks de WhatsApp / Stripe / GitHub en desarrollo local
- Exponer una app React/Vite, FastAPI, o cualquier servidor local
- Compartir un servidor de desarrollo sin ngrok ni dependencias externas

---

## Setup inicial (una sola vez)

### 1. Generar tokens

```bash
python3 scripts/setup.py
```

Genera `SEND_TOKEN`/`RECV_TOKEN`, pide la `BRIDGE_URL` (se puede dejar vacío por ahora) y el puerto local, y crea `client/.env` listo para usar.

### 2. Desplegar el bridge en Cloud Run

Desde la [consola de Google Cloud](https://console.cloud.google.com/run):

1. **Cloud Run → Create Service** → apuntá al repo (el `Dockerfile` está en la raíz).
2. **Authentication** → **Allow unauthenticated invocations**.
3. **Variables & Secrets** → agregá `SEND_TOKEN` y `RECV_TOKEN` con los valores del paso 1.
4. **Capacity** → **Minimum instances: 0**, **Maximum instances: 1**, **Request timeout: 3600**.
5. Creá el servicio y copiá la URL pública que muestra la consola.

Completá esa URL como `BRIDGE_URL` en `client/.env`.

> `max-instances=1` es imprescindible — el túnel vive en memoria del proceso del bridge. Con `min-instances=0` el servicio escala a cero cuando no usás la PC, sin costo en reposo.

---

## Uso diario

```bash
python3 start_listen.py           # usa TARGET_URL del .env
python3 start_listen.py 5173      # Vite / React
python3 start_listen.py 8080      # cualquier otro puerto
python3 start_listen.py 3000      # Next.js, Express, etc.
```

La primera vez crea el entorno virtual e instala dependencias automáticamente. Las siguientes arranca directo. `Ctrl+C` para cortar.

Si ya había una sesión activa (túnel zombie de una sesión anterior), el bridge la desconecta automáticamente y conecta la nueva.

---

## Cómo llegan los requests a tu app local

El bridge preserva método, path, query params y headers. Al llegar a tu app local:

- `host` → `localhost:PUERTO` (para que servidores de dev como Vite no rechacen el request)
- `x-forwarded-host` → host original del bridge (`tu-bridge.run.app`)
- `x-forwarded-for` → IP del cliente externo (agregado por Cloud Run)
- `x-forwarded-proto` → `https`

---

## Verificar que el tunnel está activo

```bash
curl https://tu-bridge.run.app/_tunnel/status
# {"connected": true}
```

---

## Estructura

```
baxe-tunnel/
├── start_listen.py       # punto de entrada diario
├── scripts/
│   ├── setup.py          # configuración inicial (una sola vez)
│   └── test_ping.py      # verificación end-to-end autocontenida
├── bridge/               # FastAPI app → Cloud Run
├── client/               # cliente WebSocket → corre en tu PC
└── docs/
    └── ARCHITECTURE.md
```

---

## Variables de entorno (`client/.env`)

| Variable | Descripción | Default |
|----------|-------------|---------|
| `BRIDGE_URL` | URL del bridge en Cloud Run | — (requerida) |
| `TARGET_URL` | Puerto local a exponer (ej. `http://localhost:8000`) | — (requerida) |
| `SEND_TOKEN` | Token de autenticación al bridge | — (requerida) |
| `RECV_TOKEN` | Token que el bridge devuelve para verificarse | — (requerida) |
| `RECONNECT_BACKOFF` | Espera inicial entre reintentos (seg) | `5` |
| `RECONNECT_BACKOFF_MAX` | Tope del backoff exponencial (seg) | `60` |

`start_listen.py PUERTO` sobreescribe `TARGET_URL` sin modificar el `.env`.

---

## Troubleshooting

**`HTTP 503 — No tunnel active`** → el cliente no está conectado. Corré `start_listen.py`.

**`⚠️ Bridge no verificado — abortando`** → el `RECV_TOKEN` no coincide. Verificá que los tokens en `client/.env` sean exactamente los mismos que en Cloud Run. El cliente no reintenta en este caso.

**`✗ Falta configuración`** → falta alguna variable en `client/.env`. Corré `python3 scripts/setup.py`.

**`HTTP 502`** → el cliente está conectado pero no pudo alcanzar tu app local. Verificá que tu app esté corriendo en el puerto correcto.

**Reconexión en loop** → dos instancias de Cloud Run corriendo. Verificá que `max-instances=1` esté configurado en el servicio.

**Backoff creciente sin conectar** → el bridge no responde. Verificá `BRIDGE_URL` y que el servicio esté arriba en Cloud Run.

---

## Rotar tokens

1. Corré `python3 scripts/setup.py` de nuevo.
2. En GCP: **Cloud Run → tu servicio → Edit & Deploy New Revision → Variables & Secrets** → actualizá `SEND_TOKEN` y `RECV_TOKEN`.
3. `Ctrl+C` y volvé a correr `start_listen.py`.

---

## Seguridad

- El bridge valida `SEND_TOKEN` antes de aceptar cualquier conexión.
- El cliente valida `RECV_TOKEN` antes de activar el túnel — previene MITM.
- Sin ambos tokens correctos, el túnel no se establece.
- Los tokens nunca van en el código fuente — solo en variables de entorno y en Cloud Run Secrets.
