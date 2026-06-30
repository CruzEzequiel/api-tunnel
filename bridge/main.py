import os

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

SEND_TOKEN = os.environ.get("SEND_TOKEN", "")
RECV_TOKEN = os.environ.get("RECV_TOKEN", "")

app = FastAPI()

state = {"target_url": None}

http_client = httpx.AsyncClient()

HOP_BY_HOP_HEADERS = {"host", "content-length"}


@app.post("/_tunnel/connect")
async def tunnel_connect(request: Request):
    body = await request.json()

    if body.get("send_token") != SEND_TOKEN:
        return JSONResponse({"error": "invalid send_token"}, status_code=401)

    target_url = body.get("target_url")
    if not target_url:
        return JSONResponse({"error": "target_url required"}, status_code=400)

    state["target_url"] = target_url.rstrip("/")

    return {"recv_token": RECV_TOKEN}


@app.delete("/_tunnel/disconnect")
async def tunnel_disconnect():
    state["target_url"] = None
    return {"status": "disconnected"}


@app.get("/_tunnel/status")
async def tunnel_status():
    return {
        "connected": state["target_url"] is not None,
        "target_url": state["target_url"],
    }


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy(full_path: str, request: Request):
    target_url = state["target_url"]
    if not target_url:
        return JSONResponse({"error": "No tunnel active"}, status_code=503)

    url = f"{target_url}/{full_path}"

    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
    }

    body = await request.body()

    try:
        upstream_response = await http_client.request(
            request.method,
            url,
            headers=headers,
            params=request.query_params,
            content=body,
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        return JSONResponse(
            {"error": f"Failed to reach target: {exc}"}, status_code=502
        )

    response_headers = {
        k: v
        for k, v in upstream_response.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
    }

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
    )
