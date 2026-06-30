#!/usr/bin/env python3
"""
Levanta el tunnel apuntando a un puerto local.

  python3 start_listen.py          # usa TARGET_URL del .env
  python3 start_listen.py 5173     # sobreescribe el puerto
  python3 start_listen.py 8080     # ej. Vite, backend, etc.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
VENV_DIR = ROOT / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
CLIENT_ENV = ROOT / "client" / ".env"
CLIENT_REQ = ROOT / "client" / "requirements.txt"
CLIENT_DIR = ROOT / "client"


def load_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def ensure_venv() -> None:
    if VENV_PYTHON.exists():
        return
    print("Creando entorno virtual...")
    subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
    print("✓ Entorno virtual creado")


def ensure_deps() -> None:
    marker = VENV_DIR / ".deps_installed"
    requirements = CLIENT_REQ.read_text()
    if marker.exists() and marker.read_text() == requirements:
        return
    print("Instalando dependencias del cliente...")
    subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "-q", "-r", str(CLIENT_REQ)],
        check=True,
    )
    marker.write_text(requirements)
    print("✓ Dependencias instaladas")


def relaunch_with_venv() -> None:
    if Path(sys.prefix).resolve() == VENV_DIR.resolve():
        return
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])


def validate_config(config: dict) -> None:
    missing = [v for v in ("BRIDGE_URL", "SEND_TOKEN", "RECV_TOKEN") if not config.get(v)]
    if missing:
        print(f"✗ Falta configuración en {CLIENT_ENV}: {', '.join(missing)}")
        print(f"  Corré python3 scripts/setup.py primero.")
        sys.exit(1)


async def run_tunnel_loop(config: dict, monitor) -> None:
    import asyncio
    import tunnel

    backoff_state = {"current": config["RECONNECT_BACKOFF"]}
    backoff_max = config["RECONNECT_BACKOFF_MAX"]

    while True:
        await tunnel.run_session(config, backoff_state, on_connected=monitor.set_connected)
        tunnel.log(f"✗ Conexión perdida — reintentando en {backoff_state['current']}s")
        await asyncio.sleep(backoff_state["current"])
        backoff_state["current"] = min(backoff_state["current"] * 2, backoff_max)


async def run_all(config: dict) -> None:
    import asyncio
    from monitor import MonitorServer
    from traffic import log as traffic_log

    monitor_port = int(os.environ.get("MONITOR_PORT", "4040"))
    monitor = MonitorServer(traffic_log, config, port=monitor_port)
    await monitor.start()
    print(f"  MONITOR      http://localhost:{monitor_port}")
    print()

    tunnel_task = asyncio.create_task(run_tunnel_loop(config, monitor))
    try:
        await asyncio.sleep(float("inf"))
    except asyncio.CancelledError:
        pass
    finally:
        tunnel_task.cancel()
        try:
            await tunnel_task
        except asyncio.CancelledError:
            pass
        await monitor.stop()


def main() -> None:
    port = None
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"✗ Puerto inválido: {sys.argv[1]!r}. Debe ser un número, ej: 5173")
            sys.exit(1)

    if not CLIENT_ENV.exists():
        print(f"✗ {CLIENT_ENV} no existe. Corré python3 scripts/setup.py primero.")
        sys.exit(1)

    config = load_env(CLIENT_ENV)
    validate_config(config)

    target_url = f"http://localhost:{port}" if port else config.get("TARGET_URL", "http://localhost:8000")

    ensure_venv()
    ensure_deps()
    relaunch_with_venv()

    print("╔══════════════════════════════════════════╗")
    print("║           baxe-tunnel                    ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print(f"  BRIDGE_URL   {config['BRIDGE_URL']}")
    print(f"  TARGET_URL   {target_url}")

    print("──────────────────────────────────────────")

    sys.path.insert(0, str(CLIENT_DIR))
    os.environ.update(config)
    os.environ["TARGET_URL"] = target_url

    import asyncio
    import tunnel
    runtime_config = tunnel.load_config()

    try:
        asyncio.run(run_all(runtime_config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
