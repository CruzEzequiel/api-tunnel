import asyncio
import base64
import json
import os
import sys
from datetime import datetime

import httpx
import websockets
from dotenv import load_dotenv
from websockets.exceptions import InvalidStatus, WebSocketException

load_dotenv()

REQUIRED_VARS = ["BRIDGE_URL", "TARGET_URL", "SEND_TOKEN", "RECV_TOKEN"]

target_client = httpx.AsyncClient()


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


def to_ws_url(bridge_url: str) -> str:
    url = bridge_url.rstrip("/")
    if url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    return url + "/_tunnel/ws"


async def handle_request(config: dict, websocket, message: dict) -> None:
    headers = {k: v for k, v in message["headers"]}
    body = base64.b64decode(message["body_b64"])
    url = config["TARGET_URL"].rstrip("/") + message["path"]

    try:
        upstream = await target_client.request(
            message["method"],
            url,
            headers=headers,
            params=message["query"],
            content=body,
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        response = {
            "type": "http_response",
            "id": message["id"],
            "error": "target_unreachable",
            "detail": str(exc),
        }
        await websocket.send(json.dumps(response))
        return

    response = {
        "type": "http_response",
        "id": message["id"],
        "status": upstream.status_code,
        "headers": list(upstream.headers.items()),
        "body_b64": base64.b64encode(upstream.content).decode("ascii"),
    }
    await websocket.send(json.dumps(response))


async def run_session(config: dict, backoff_state: dict) -> None:
    log("Conectando...")

    try:
        async with websockets.connect(to_ws_url(config["BRIDGE_URL"]), open_timeout=10) as websocket:
            await websocket.send(json.dumps({"type": "hello", "send_token": config["SEND_TOKEN"]}))
            hello_response = json.loads(await websocket.recv())

            if hello_response.get("type") != "hello_ack":
                log(f"✗ Conexión rechazada por el bridge: {hello_response.get('error')}")
                return

            if hello_response.get("recv_token") != config["RECV_TOKEN"]:
                log("⚠️  Bridge no verificado — abortando")
                print("                          El RECV_TOKEN recibido no coincide con el esperado.")
                print("                          Verifica que ambos lados usen los mismos tokens.")
                sys.exit(1)

            log(f"✓ Tunnel activo → {config['TARGET_URL']}")
            backoff_state["current"] = config["RECONNECT_BACKOFF"]

            async for raw in websocket:
                message = json.loads(raw)
                if message.get("type") == "http_request":
                    asyncio.create_task(handle_request(config, websocket, message))

    except (OSError, InvalidStatus, WebSocketException, asyncio.TimeoutError, TimeoutError):
        return


async def run_async() -> None:
    config = load_config()
    print_banner(config)
    validate_config(config)

    backoff_state = {"current": config["RECONNECT_BACKOFF"]}
    backoff_max = config["RECONNECT_BACKOFF_MAX"]

    while True:
        await run_session(config, backoff_state)
        log(f"✗ Conexión perdida — reintentando en {backoff_state['current']}s")
        await asyncio.sleep(backoff_state["current"])
        backoff_state["current"] = min(backoff_state["current"] * 2, backoff_max)


def run() -> None:
    try:
        asyncio.run(run_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
