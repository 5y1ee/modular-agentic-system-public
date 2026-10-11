"""MCP(Model Context Protocol) JSON-RPC 처리. 웹 프레임워크를 모른다 — 메시지 dict를 받아 응답 dict를 돌려준다.

지원하는 것: initialize, ping, tools/list, tools/call, `notifications/*`(응답 없음). 서버는 **상태가 없다**(세션 id를 쓰지 않는다).
MCP "Streamable HTTP" 전송에서 서버는 JSON 응답으로 답해도 된다(qmd의 서버도 stateless JSON이다). 서버가 먼저 보내는 알림·스트림은 없다.

오류 처리(MCP 권고를 따랐다)
  - 없거나 이 역할에게 안 보이는 도구, 모르는 메소드 → JSON-RPC 오류.
  - 인자가 잘못된 호출 → 도구 실행 오류(`isError: true`) + 이유 문장. 모델이 읽고 스스로 고치게 하려는 것이다.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from .registry import InvalidArguments, ToolRegistry, UnknownTool, text_result

SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO = {"name": "qmd-proxy", "version": "0.1.0"}
INSTRUCTIONS = (
    "개인 지식 베이스(vault)를 qmd로 검색하는 서버다. 보통은 search_vault로 찾고, 결과의 경로·줄 번호로 get을 불러 앞뒤 문맥을 읽는다. "
    "검색은 벡터 검색이고 리랭킹은 꺼져 있다. 점수는 관련 여부를 보장하지 않으니 발췌를 읽고 판단한다."
)

_log = logging.getLogger("qmd_proxy")

Audit = Callable[[str, bool, float], None]  # (도구 이름, 성공 여부, 걸린 초)


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def handle_message(
    message: Any, registry: ToolRegistry, role: str, *, audit: Audit | None = None
) -> dict[str, Any] | None:
    """JSON-RPC 메시지 하나를 처리한다. 알림(id가 없는 메시지)이면 None(= 응답하지 않는다)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
    method: str = message["method"]
    message_id = message.get("id")
    params = message.get("params") or {}
    if "id" not in message:  # 알림: 처리할 것이 있어도 응답하지 않는다
        return None

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _result(
            message_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(message_id, {})
    if method == "tools/list":
        return _result(message_id, {"tools": [t.listing() for t in registry.visible(role)]})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return _error(message_id, -32602, "name이 필요하다")
        started = time.monotonic()
        ok = True
        try:
            result = registry.call(name, params.get("arguments"), role)
            ok = not result.get("isError", False)
        except UnknownTool:
            return _error(message_id, -32602, f"알 수 없는 도구: {name}")
        except InvalidArguments as exc:
            result, ok = text_result(f"인자 오류: {exc}", is_error=True), False
        except Exception:  # noqa: BLE001 (도구 안의 예상 못 한 오류가 서버를 죽이지 않게)
            _log.exception("도구 실행 중 오류: %s", name)
            result, ok = text_result("서버 내부 오류로 도구를 실행하지 못했다.", is_error=True), False
        if audit is not None:
            audit(name, ok, time.monotonic() - started)
        return _result(message_id, result)
    return _error(message_id, -32601, f"Method not found: {method}")
