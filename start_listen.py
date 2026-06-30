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
CLIENT_SCRIPT = ROOT / "client" / "tunnel.py"


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
    if marker.exists():
        return
    print("Instalando dependencias del cliente...")
    subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "-q", "-r", str(CLIENT_REQ)],
        check=True,
    )
    marker.touch()
    print("✓ Dependencias instaladas")


def validate_config(config: dict) -> None:
    missing = [v for v in ("BRIDGE_URL", "SEND_TOKEN", "RECV_TOKEN") if not config.get(v)]
    if missing:
        print(f"✗ Falta configuración en {CLIENT_ENV}: {', '.join(missing)}")
        print(f"  Corré python3 scripts/setup.py primero.")
        sys.exit(1)


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

    print("╔══════════════════════════════════════════╗")
    print("║           baxe-tunnel                    ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print(f"  BRIDGE_URL   {config['BRIDGE_URL']}")
    print(f"  TARGET_URL   {target_url}")
    print()

    ensure_venv()
    ensure_deps()

    print()
    print("──────────────────────────────────────────")

    env = os.environ.copy()
    env["TARGET_URL"] = target_url

    try:
        subprocess.run(
            [str(VENV_PYTHON), str(CLIENT_SCRIPT)],
            env=env,
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
