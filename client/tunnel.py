import os
import sys
import time
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = ["BRIDGE_URL", "TARGET_URL", "SEND_TOKEN", "RECV_TOKEN"]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}")


def truncate_token(value: str) -> str:
    if len(value) < 6:
        return f"{value}  ({len(value)} chars)"
    return f"{value[:3]}...{value[-3:]}  ({len(value)} chars)"


def load_config() -> dict:
    config = {
        "BRIDGE_URL": os.environ.get("BRIDGE_URL", ""),
        "TARGET_URL": os.environ.get("TARGET_URL", ""),
        "SEND_TOKEN": os.environ.get("SEND_TOKEN", ""),
        "RECV_TOKEN": os.environ.get("RECV_TOKEN", ""),
        "RECONNECT_BACKOFF": int(os.environ.get("RECONNECT_BACKOFF", "5")),
        "RECONNECT_BACKOFF_MAX": int(os.environ.get("RECONNECT_BACKOFF_MAX", "60")),
    }
    return config


def print_banner(config: dict) -> None:
    print("╔══════════════════════════════════════════╗")
    print("║           baxe-tunnel client             ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print(f"  BRIDGE_URL   {config['BRIDGE_URL'] or '✗ NO CONFIGURADO'}")
    print(f"  TARGET_URL   {config['TARGET_URL'] or '✗ NO CONFIGURADO'}")

    for key in ("SEND_TOKEN", "RECV_TOKEN"):
        value = config[key]
        if value:
            print(f"  {key}   {truncate_token(value)}")
        else:
            print(f"  {key}   ✗ NO CONFIGURADO")

    print(
        f"  BACKOFF      {config['RECONNECT_BACKOFF']}s → "
        f"{config['RECONNECT_BACKOFF_MAX']}s max"
    )
    print()
    print("──────────────────────────────────────────")


def validate_config(config: dict) -> None:
    for var in REQUIRED_VARS:
        if not config[var]:
            log(f"✗ Variable requerida no encontrada: {var}")
            print("                          Revisa tu archivo .env y vuelve a intentar.")
            sys.exit(1)


def connect(config: dict) -> bool:
    log("Conectando...")

    url = f"{config['BRIDGE_URL'].rstrip('/')}/_tunnel/connect"

    try:
        response = httpx.post(
            url,
            json={
                "send_token": config["SEND_TOKEN"],
                "target_url": config["TARGET_URL"],
            },
            timeout=10.0,
        )
    except httpx.RequestError:
        return False

    if response.status_code != 200:
        return False

    data = response.json()
    if data.get("recv_token") != config["RECV_TOKEN"]:
        log("⚠️  Bridge no verificado — abortando")
        print("                          El RECV_TOKEN recibido no coincide con el esperado.")
        print("                          Verifica que ambos lados usen los mismos tokens.")
        sys.exit(1)

    log(f"✓ Tunnel activo → {config['TARGET_URL']}")
    return True


def run() -> None:
    config = load_config()
    print_banner(config)
    validate_config(config)

    backoff = config["RECONNECT_BACKOFF"]
    backoff_max = config["RECONNECT_BACKOFF_MAX"]

    while True:
        if connect(config):
            break

        log(f"✗ Conexión fallida — reintentando en {backoff}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, backoff_max)

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    run()
