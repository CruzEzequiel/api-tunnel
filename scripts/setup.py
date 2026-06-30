#!/usr/bin/env python3
import secrets
from pathlib import Path

ROOT = Path(__file__).parent.parent
CLIENT_ENV = ROOT / "client" / ".env"
BRIDGE_ENV = ROOT / "bridge" / ".env"

CLIENT_ENV_TEMPLATE = """\
# Dirección del bridge deployado en Cloud Run
BRIDGE_URL={bridge_url}

# Puerto local a exponer
TARGET_URL={target_url}

# Tokens de autenticación mutua
SEND_TOKEN={send_token}
RECV_TOKEN={recv_token}

# Reconexión automática
RECONNECT_BACKOFF=5
RECONNECT_BACKOFF_MAX=60
"""

BRIDGE_ENV_TEMPLATE = """\
# Token que el cliente debe enviar para conectarse
SEND_TOKEN={send_token}

# Token que el bridge devuelve para que el cliente lo verifique
RECV_TOKEN={recv_token}
"""


def main() -> None:
    print("╔══════════════════════════════════════════╗")
    print("║         baxe-tunnel setup                ║")
    print("╚══════════════════════════════════════════╝")
    print()

    if CLIENT_ENV.exists() or BRIDGE_ENV.exists():
        existing = [str(f) for f in (CLIENT_ENV, BRIDGE_ENV) if f.exists()]
        overwrite = input(f"{', '.join(existing)} ya existe(n). ¿Sobrescribir? [y/N] ")
        if overwrite.strip().lower() != "y":
            print("Cancelado.")
            return

    print("Generando tokens...")
    send_token = secrets.token_hex(32)
    recv_token = secrets.token_hex(32)
    print("✓ SEND_TOKEN y RECV_TOKEN generados")
    print()

    bridge_url = input(
        "BRIDGE_URL (URL del bridge en Cloud Run, dejar vacío si aún no lo desplegaste): "
    ).strip()
    target_url = input("TARGET_URL — dirección local adonde el bridge redirige el tráfico entrante [http://localhost:8000]: ").strip() or "http://localhost:8000"

    CLIENT_ENV.parent.mkdir(parents=True, exist_ok=True)
    CLIENT_ENV.write_text(
        CLIENT_ENV_TEMPLATE.format(
            bridge_url=bridge_url,
            target_url=target_url,
            send_token=send_token,
            recv_token=recv_token,
        )
    )
    BRIDGE_ENV.parent.mkdir(parents=True, exist_ok=True)
    BRIDGE_ENV.write_text(
        BRIDGE_ENV_TEMPLATE.format(
            send_token=send_token,
            recv_token=recv_token,
        )
    )

    print(f"✓ {CLIENT_ENV} creado")
    print(f"✓ {BRIDGE_ENV} creado")
    print()
    print("──────────────────────────────────────────")
    print("Próximo paso — desplegar el bridge en Cloud Run (consola de GCP):")
    print()
    print()
    print(f"Luego completá BRIDGE_URL en {CLIENT_ENV} con la URL que muestre la consola de Cloud Run")
    print("y arrancá el cliente con: cd client && python tunnel.py")


if __name__ == "__main__":
    main()
