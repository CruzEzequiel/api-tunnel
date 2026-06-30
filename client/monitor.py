from __future__ import annotations

import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from traffic import TrafficEntry, TrafficLog


MAX_BODY_PREVIEW = 20_000


def _body_payload(body: bytes) -> dict:
    truncated = len(body) > MAX_BODY_PREVIEW
    preview = body[:MAX_BODY_PREVIEW]
    return {
        "text": preview.decode("utf-8", errors="replace"),
        "base64": base64.b64encode(preview).decode("ascii"),
        "size": len(body),
        "truncated": truncated,
    }


def _entry_summary(entry: TrafficEntry) -> dict:
    return {
        "id": entry.id,
        "method": entry.method,
        "path": entry.display_path,
        "status": entry.status,
        "elapsed_ms": entry.elapsed_ms,
        "error": entry.error,
    }


def _entry_detail(entry: TrafficEntry) -> dict:
    data = _entry_summary(entry)
    data.update(
        {
            "request_headers": entry.request_headers,
            "request_body": _body_payload(entry.request_body),
            "response_headers": entry.response_headers,
            "response_body": _body_payload(entry.response_body),
        }
    )
    return data


HTML = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>baxe-tunnel monitor</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d8dee8;
      --text: #161a22;
      --muted: #667085;
      --good: #137333;
      --warn: #9a6700;
      --bad: #b42318;
      --selected: #e8f0fe;
      --shadow: 0 1px 2px rgba(16, 24, 40, .08);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #101418;
        --panel: #171c22;
        --line: #2b333d;
        --text: #edf2f7;
        --muted: #9aa4b2;
        --selected: #20314f;
        --shadow: none;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    .status {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
      font-weight: 700;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--warn);
      flex: 0 0 auto;
    }
    .dot.connected { background: var(--good); }
    .target {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .actions {
      display: flex;
      gap: 8px;
      flex-shrink: 0;
    }
    .btn {
      padding: 6px 14px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      font: inherit;
      font-size: 13px;
      cursor: pointer;
    }
    .btn:hover { background: var(--selected); }
.meta {
      color: var(--muted);
      white-space: nowrap;
    }
    main {
      display: grid;
      grid-template-columns: minmax(360px, 45%) 1fr;
      height: calc(100vh - 57px);
      overflow: hidden;
    }
    .list, .detail {
      min-width: 0;
      padding: 14px;
      overflow-y: auto;
      height: 100%;
    }
    .list {
      border-right: 1px solid var(--line);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      box-shadow: var(--shadow);
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 9px 10px;
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .04em;
      position: sticky;
      top: 0;
      background: var(--panel);
      z-index: 1;
    }
    tr.request-row {
      cursor: pointer;
    }
    tr.request-row:hover, tr.request-row.selected {
      background: var(--selected);
    }
    .method { width: 74px; font-weight: 700; }
    .status-cell { width: 74px; }
    .elapsed { width: 70px; color: var(--muted); }
    .path {
      overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
    }
    .empty {
      color: var(--muted);
      padding: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
    }
    .detail-card {
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      min-height: calc(100vh - 86px);
    }
    .detail-head {
      padding: 14px;
      border-bottom: 1px solid var(--line);
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    section {
      padding: 14px;
      border-bottom: 1px solid var(--line);
    }
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
    }
    h2 {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }
    .copy-btn {
      padding: 2px 10px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: var(--panel);
      color: var(--muted);
      font: 12px system-ui, sans-serif;
      cursor: pointer;
    }
    .copy-btn:hover { color: var(--text); background: var(--selected); }
    .copy-btn.copied { color: var(--good); border-color: var(--good); }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .error { color: var(--bad); }
    @media (max-width: 820px) {
      header { align-items: flex-start; flex-direction: column; }
      main { grid-template-columns: 1fr; }
      .list { border-right: 0; border-bottom: 1px solid var(--line); }
      .detail-card { min-height: 360px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="status">
      <span id="dot" class="dot"></span>
      <span id="status-text">Conectando...</span>
      <span id="target" class="target"></span>
    </div>
    <div class="actions">
      <span id="meta" class="meta">0 requests</span>
      <button class="btn" onclick="clearLogs()">Borrar logs</button>
    </div>
  </header>
  <main>
    <div class="list">
      <table>
        <thead>
          <tr>
            <th>Metodo</th>
            <th>Status</th>
            <th>ms</th>
            <th>Path</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
      <div id="empty" class="empty">Esperando requests...</div>
    </div>
    <div class="detail">
      <div class="detail-card" id="detail">
        <div class="detail-head">Selecciona un request</div>
      </div>
    </div>
  </main>
  <script>
    let selectedId = null;
    let entries = [];

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      }[char]));
    }

    function headersText(headers) {
      if (!headers || headers.length === 0) return "  (sin headers)";
      return headers.map(([key, value]) => `  ${key}: ${value}`).join("\\n");
    }

    function bodyText(body) {
      if (!body || body.size === 0) return "  (vacio)";
      return body.text + (body.truncated ? "\\n  ... cuerpo truncado ..." : "");
    }

    async function loadState() {
      const [stateRes, entriesRes] = await Promise.all([
        fetch("/api/state"),
        fetch("/api/entries")
      ]);
      const state = await stateRes.json();
      entries = await entriesRes.json();

      document.getElementById("dot").classList.toggle("connected", state.connected);
      document.getElementById("status-text").textContent = state.connected ? "Tunnel activo" : "Conectando...";
      document.getElementById("target").textContent = `-> ${state.target_url}`;
      document.getElementById("meta").textContent = `${entries.length} request${entries.length !== 1 ? "s" : ""}`;
      const list = document.querySelector(".list");
      const wasAtBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 40;
      renderRows();
      if (wasAtBottom) list.scrollTop = list.scrollHeight;

      if (selectedId) {
        const stillExists = entries.some(entry => entry.id === selectedId);
        if (stillExists) await showDetail(selectedId);
      }
    }

    function renderRows() {
      const rows = document.getElementById("rows");
      const empty = document.getElementById("empty");
      rows.innerHTML = "";
      empty.style.display = entries.length ? "none" : "block";

      for (const entry of entries) {
        const row = document.createElement("tr");
        row.className = `request-row${entry.id === selectedId ? " selected" : ""}`;
        row.innerHTML = `
          <td class="method">${esc(entry.method)}</td>
          <td class="status-cell ${entry.error ? "error" : ""}">${esc(entry.status ?? "ERR")}</td>
          <td class="elapsed">${esc(entry.elapsed_ms ?? "-")}</td>
          <td class="path">${esc(entry.path)}</td>
        `;
        row.addEventListener("click", () => showDetail(entry.id));
        rows.appendChild(row);
      }
    }

    async function showDetail(id) {
      selectedId = id;
      renderRows();
      const res = await fetch(`/api/entries/${encodeURIComponent(id)}`);
      if (!res.ok) return;
      const entry = await res.json();
      const status = entry.status ?? "ERR";
      const elapsed = entry.elapsed_ms ?? "-";
      const reqBody = bodyText(entry.request_body);
      const resBody = bodyText(entry.response_body);
      document.getElementById("detail").innerHTML = `
        <div class="detail-head">${esc(entry.method)} ${esc(status)} ${esc(elapsed)}ms ${esc(entry.path)}</div>
        <section>
          <div class="section-head"><h2>Request headers</h2><button class="copy-btn" data-copy="${esc(headersText(entry.request_headers))}">Copiar</button></div>
          <pre>${esc(headersText(entry.request_headers))}</pre>
        </section>
        <section>
          <div class="section-head"><h2>Request body</h2><button class="copy-btn" data-copy="${esc(reqBody)}">Copiar</button></div>
          <pre>${esc(reqBody)}</pre>
        </section>
        <section>
          <div class="section-head"><h2>Response headers</h2><button class="copy-btn" data-copy="${esc(headersText(entry.response_headers))}">Copiar</button></div>
          <pre>${esc(headersText(entry.response_headers))}</pre>
        </section>
        <section>
          <div class="section-head"><h2>Response body</h2><button class="copy-btn" data-copy="${esc(resBody)}">Copiar</button></div>
          <pre>${esc(resBody)}</pre>
        </section>
        ${entry.error ? `<section><h2>Error</h2><pre class="error">${esc(entry.error)}</pre></section>` : ""}
      `;
      document.querySelectorAll(".copy-btn").forEach(btn => {
        btn.addEventListener("click", () => {
          navigator.clipboard.writeText(btn.dataset.copy).then(() => {
            btn.textContent = "Copiado";
            btn.classList.add("copied");
            setTimeout(() => { btn.textContent = "Copiar"; btn.classList.remove("copied"); }, 1500);
          });
        });
      });
    }

    async function clearLogs() {
      await fetch("/api/clear", { method: "POST" });
      selectedId = null;
      document.getElementById("detail").innerHTML = "<div class='detail-head'>Selecciona un request</div>";
      await loadState();
    }

loadState().catch(console.error);
    setInterval(() => loadState().catch(console.error), 1000);
  </script>
</body>
</html>
"""


class MonitorServer:
    def __init__(self, traffic_log: TrafficLog, config: dict, port: int = 4040) -> None:
        self.log = traffic_log
        self.config = config
        self.port = port
        self.connected = False
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def set_connected(self, connected: bool) -> None:
        self.connected = connected

    async def start(self) -> None:
        handler = self._make_handler()
        ThreadingHTTPServer.allow_reuse_address = True
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    async def stop(self) -> None:
        if self.server is None:
            return
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)

    def _state(self) -> dict:
        return {
            "connected": self.connected,
            "target_url": self.config.get("TARGET_URL", ""),
            "bridge_url": self.config.get("BRIDGE_URL", ""),
        }

    def _entry_by_id(self, entry_id: str) -> TrafficEntry | None:
        for entry in self.log.entries():
            if entry.id == entry_id:
                return entry
        return None

    def _make_handler(self):
        monitor = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/":
                    self._send_text(HTML, "text/html; charset=utf-8")
                    return
                if path == "/api/state":
                    self._send_json(monitor._state())
                    return
                if path == "/api/entries":
                    entries = [_entry_summary(entry) for entry in monitor.log.entries()]
                    self._send_json(entries)
                    return
                if path.startswith("/api/entries/"):
                    entry_id = unquote(path.removeprefix("/api/entries/"))
                    entry = monitor._entry_by_id(entry_id)
                    if entry is None:
                        self._send_text("Request not found", "text/plain; charset=utf-8", status=404)
                        return
                    self._send_json(_entry_detail(entry))
                    return
                self._send_text("Not found", "text/plain; charset=utf-8", status=404)

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                if path == "/api/clear":
                    with monitor.log._lock:
                        monitor.log._entries.clear()
                    self._send_json({"ok": True})
                    return
                self._send_text("Not found", "text/plain; charset=utf-8", status=404)

            def log_message(self, _format: str, *_args) -> None:
                return

            def _send_json(self, payload: object) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_text(self, text: str, content_type: str, status: int = 200) -> None:
                body = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler
