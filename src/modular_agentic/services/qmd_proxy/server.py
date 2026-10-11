"""qmd MCP 프록시의 HTTP 서버(Starlette). 인증·Origin 검사만 하고 MCP 처리는 protocol.py에 맡긴다.

주소
  POST /mcp     MCP (Streamable HTTP, 상태 없음, JSON 응답). 인증 필요.
  GET  /health  살아 있는지만(데이터 없음, 인증 없음)
  GET·DELETE /mcp 는 405 (서버가 먼저 보내는 스트림을 제공하지 않는다)

인증: `Authorization: Bearer <키>` 또는 `X-Api-Key: <키>`. 키가 없거나 틀리면 401.
Origin: 브라우저가 보낸 `Origin` 헤더가 있는데 허용 목록에 없으면 403(DNS 리바인딩 방지, MCP 규격이 요구). Origin이 없는 요청(MCP 클라이언트, curl)은 영향 없다.
요청 본문은 JSON-RPC 메시지 하나 또는 배열이다.
"""
from __future__ import annotations

import json
import logging

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .config import KeyInfo, ProxyConfig
from .protocol import handle_message
from .registry import ToolRegistry

_log = logging.getLogger("qmd_proxy")


def _extract_key(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def create_app(config: ProxyConfig, registry: ToolRegistry) -> Starlette:
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def mcp(request: Request) -> Response:
        origin = request.headers.get("origin")
        if origin and origin not in config.allowed_origins:
            return JSONResponse({"error": "Origin이 허용되지 않았다"}, status_code=403)
        info: KeyInfo | None = config.authenticate(_extract_key(request))
        if info is None:
            return JSONResponse({"error": "인증이 필요하다"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        try:
            body = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400
            )

        def audit(tool: str, ok: bool, seconds: float) -> None:
            _log.info("tool=%s key=%s role=%s ok=%s seconds=%.2f", tool, info.name, info.role, ok, seconds)

        def handle(message):
            return handle_message(message, registry, info.role, audit=audit)

        if isinstance(body, list):
            responses = [r for r in [await run_in_threadpool(handle, m) for m in body] if r is not None]
            return JSONResponse(responses) if responses else Response(status_code=202)
        response = await run_in_threadpool(handle, body)
        return JSONResponse(response) if response is not None else Response(status_code=202)

    return Starlette(routes=[Route("/health", health, methods=["GET"]), Route("/mcp", mcp, methods=["POST"])])
