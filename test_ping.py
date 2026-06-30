#!/usr/bin/env python3
"""
Verificación rápida del tunnel end-to-end.

El script es autocontenido: levanta un servidor de eco local en TARGET_URL,
arranca tunnel.py, espera que conecte al bridge, manda el request, muestra
la respuesta y limpia todo al terminar.

Requiere que BRIDGE_URL esté configurado en client/.env y que el bridge
esté deployado y accesible.

Ejemplos:
  python3 test_ping.py /
  python3 test_ping.py /webhook/test --method POST --json '{"event":"ping"}'
  python3 test_ping.py /webhook/test --method POST --data 'ping' --timeout 30
"""
import argparse
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
CLIENT_ENV = ROOT / "client" / ".env"
CLIENT_SCRIPT = ROOT / "client" / "tunnel.py"

TUNNEL_CONNECT_TIMEOUT = 15


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verifica el tunnel end-to-end contra el bridge real."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="/ping",
        help="Path que se enviara al bridge, por ejemplo /webhook/test.",
    )
    parser.add_argument(
        "-X",
        "--method",
        default="GET",
        help="Metodo HTTP a usar. Default: GET.",
    )
    parser.add_argument(
        "--json",
        dest="json_body",
        help="Body JSON a enviar. Ejemplo: '{\"event\":\"ping\"}'.",
    )
    parser.add_argument(
        "--data",
        help="Body plano a enviar. Util para probar payloads no JSON.",
    )
    parser.add_argument(
        "-H",
        "--header",
        action="append",
        default=[],
        help="Header extra en formato 'Nombre: valor'. Se puede repetir.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Timeout en segundos para el request al bridge. Default: 30.",
    )
    return parser.parse_args()


def parse_headers(raw_headers: list[str]) -> dict[str, str]:
    headers = {}
    for raw_header in raw_headers:
        if ":" not in raw_header:
            raise ValueError(f"Header invalido: {raw_header!r}. Usa 'Nombre: valor'.")
        name, value = raw_header.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


def load_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def build_request(args: argparse.Namespace, url: str, method: str) -> Request:
    headers = parse_headers(args.header)
    data = None

    if args.json_body and args.data:
        raise ValueError("Usa --json o --data, pero no ambos.")

    if args.json_body:
        try:
            parsed_json = json.loads(args.json_body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON invalido: {exc}") from exc
        data = json.dumps(parsed_json).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif args.data is not None:
        data = args.data.encode("utf-8")

    return Request(url, data=data, headers=headers, method=method)


def print_response(status_code: int, headers: dict[str, str], body: bytes, elapsed: float) -> None:
    print(f"HTTP {status_code} ({elapsed:.2f}s)")
    content_type = headers.get("content-type", "")
    text = body.decode("utf-8", errors="replace")

    if "application/json" in content_type:
        try:
            print(json.dumps(json.loads(text), indent=2, ensure_ascii=False))
            return
        except json.JSONDecodeError:
            pass

    print(text)


class EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self): self._echo()
    def do_POST(self): self._echo()
    def do_PUT(self): self._echo()
    def do_PATCH(self): self._echo()
    def do_DELETE(self): self._echo()

    def _echo(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        response = json.dumps({
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers),
            "body": body.decode("utf-8", errors="replace"),
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *args):
        pass  # silenciar logs del servidor de eco


def start_echo_server(host: str, port: int) -> HTTPServer:
    server = HTTPServer((host, port), EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def parse_target_url(target_url: str) -> tuple[str, int]:
    url = target_url.rstrip("/")
    if "://" in url:
        url = url.split("://", 1)[1]
    host, _, port_str = url.rpartition(":")
    host = host or "localhost"
    try:
        port = int(port_str)
    except ValueError:
        port = 80
    return host, port


def wait_for_tunnel(bridge_url: str, timeout: float) -> bool:
    status_url = bridge_url.rstrip("/") + "/_tunnel/status"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(status_url, timeout=3) as resp:
                data = json.loads(resp.read())
                if data.get("connected"):
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    try:
        args = parse_args()
    except ValueError as exc:
        print(f"✗ {exc}")
        return 1

    if not CLIENT_ENV.exists():
        print(f"✗ {CLIENT_ENV} no existe. Corré setup.py primero.")
        return 1

    config = load_env(CLIENT_ENV)
    bridge_url = (config.get("BRIDGE_URL") or "").rstrip("/")
    target_url = (config.get("TARGET_URL") or "http://localhost:8000").rstrip("/")

    if not bridge_url:
        print(f"✗ BRIDGE_URL no está configurado en {CLIENT_ENV}")
        return 1

    host, port = parse_target_url(target_url)

    print(f"  BRIDGE_URL   {bridge_url}")
    print(f"  TARGET_URL   {target_url}")
    print()

    # levantar servidor de eco
    print(f"[1/3] Levantando servidor de eco en {target_url}...")
    echo_server = start_echo_server(host, port)
    print(f"      ✓ escuchando en {host}:{port}")

    # levantar tunnel.py
    print(f"[2/3] Arrancando tunnel.py...")
    tunnel_proc = subprocess.Popen(
        [sys.executable, str(CLIENT_SCRIPT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # esperar que conecte
    print(f"      esperando conexión al bridge (hasta {TUNNEL_CONNECT_TIMEOUT}s)...")
    connected = wait_for_tunnel(bridge_url, TUNNEL_CONNECT_TIMEOUT)
    if not connected:
        print(f"      ✗ El tunnel no conectó en {TUNNEL_CONNECT_TIMEOUT}s.")
        print(f"        Verificá que BRIDGE_URL sea correcto y el bridge esté deployado.")
        tunnel_proc.terminate()
        echo_server.shutdown()
        return 1
    print(f"      ✓ Tunnel activo")

    # mandar el request
    path = args.path if args.path.startswith("/") else f"/{args.path}"
    url = bridge_url + path
    method = args.method.upper()

    print(f"[3/3] {method} {url}")
    exit_code = 0
    try:
        request = build_request(args, url, method)
    except ValueError as exc:
        print(f"✗ {exc}")
        exit_code = 1
    else:
        try:
            started = time.monotonic()
            with urlopen(request, timeout=args.timeout) as response:
                status_code = response.status
                headers = dict(response.headers.items())
                body = response.read()
            elapsed = time.monotonic() - started
        except HTTPError as exc:
            elapsed = time.monotonic() - started
            status_code = exc.code
            headers = dict(exc.headers.items())
            body = exc.read()
        except URLError as exc:
            print(f"✗ No se pudo contactar el bridge: {exc}")
            exit_code = 1
        else:
            print()
            print_response(status_code, headers, body, elapsed)
            if status_code >= 400:
                exit_code = 1

    tunnel_proc.terminate()
    echo_server.shutdown()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
