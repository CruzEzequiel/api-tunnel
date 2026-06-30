import asyncio
import base64
import json
import os
import uuid

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

SEND_TOKEN = os.environ.get("SEND_TOKEN", "")
RECV_TOKEN = os.environ.get("RECV_TOKEN", "")
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "30"))

app = FastAPI()

state = {
    "ws": None,
    "ws_send_lock": None,
    "pending": {},
}

HOP_BY_HOP_HEADERS = {"content-length"}


def _resolve_pending(message: dict) -> None:
    future = state["pending"].pop(message.get("id"), None)
    if future is not None and not future.done():
        future.set_result(message)


def _fail_all_pending(reason: str) -> None:
    for future in state["pending"].values():
        if not future.done():
            future.set_exception(RuntimeError(reason))
    state["pending"].clear()


@app.websocket("/_tunnel/ws")
async def tunnel_ws(websocket: WebSocket):
    await websocket.accept()

    try:
        hello_raw = await websocket.receive_text()
    except WebSocketDisconnect:
        return

    try:
        hello = json.loads(hello_raw)
    except json.JSONDecodeError:
        await websocket.close(code=4400)
        return

    if hello.get("type") != "hello" or hello.get("send_token") != SEND_TOKEN:
        await websocket.send_json({"type": "hello_rejected", "error": "invalid_send_token"})
        await websocket.close(code=4401)
        return

    if state["ws"] is not None:
        old_ws = state["ws"]
        state["ws"] = None
        state["ws_send_lock"] = None
        _fail_all_pending("tunnel_replaced")
        try:
            await old_ws.close(code=1001)
        except Exception:
            pass

    state["ws"] = websocket
    state["ws_send_lock"] = asyncio.Lock()
    await websocket.send_json({"type": "hello_ack", "recv_token": RECV_TOKEN})

    try:
        while True:
            raw = await websocket.receive_text()
            message = json.loads(raw)
            if message.get("type") == "http_response":
                _resolve_pending(message)
    except WebSocketDisconnect:
        pass
    finally:
        _fail_all_pending("tunnel_disconnected")
        state["ws"] = None
        state["ws_send_lock"] = None


@app.get("/_tunnel/status")
async def tunnel_status():
    return {"connected": state["ws"] is not None}


@app.delete("/_tunnel/disconnect")
async def tunnel_disconnect():
    ws = state["ws"]
    if ws is None:
        return JSONResponse({"error": "No tunnel active"}, status_code=404)
    await ws.close(code=1001)
    return {"status": "disconnected"}


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy(full_path: str, request: Request):
    websocket = state["ws"]
    if websocket is None:
        return JSONResponse({"error": "No tunnel active"}, status_code=503)

    request_id = str(uuid.uuid4())
    body = await request.body()

    message = {
        "type": "http_request",
        "id": request_id,
        "method": request.method,
        "path": f"/{full_path}",
        "query": list(request.query_params.multi_items()),
        "headers": [
            [k, v] for k, v in request.headers.items()
            if k.lower() not in HOP_BY_HOP_HEADERS
        ],
        "body_b64": base64.b64encode(body).decode("ascii"),
    }

    future = asyncio.get_running_loop().create_future()
    state["pending"][request_id] = future

    try:
        async with state["ws_send_lock"]:
            await websocket.send_text(json.dumps(message))

        response_message = await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT)
    except asyncio.TimeoutError:
        state["pending"].pop(request_id, None)
        return JSONResponse({"error": "Tunnel client timed out"}, status_code=504)
    except Exception as exc:
        state["pending"].pop(request_id, None)
        return JSONResponse({"error": f"Tunnel disconnected: {exc}"}, status_code=502)

    if "error" in response_message:
        detail = response_message.get("detail", response_message["error"])
        return JSONResponse(
            {"error": f"Failed to reach target: {detail}"}, status_code=502
        )

    response_headers = {
        k: v for k, v in response_message["headers"]
        if k.lower() not in HOP_BY_HOP_HEADERS
    }
    response_body = base64.b64decode(response_message["body_b64"])

    return Response(
        content=response_body,
        status_code=response_message["status"],
        headers=response_headers,
    )
