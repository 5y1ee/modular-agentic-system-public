"""MCP(Model Context Protocol) 서버를 HTTP로 부르는 최소 클라이언트. 표준 라이브러리만 쓴다.

두 곳이 같이 쓴다
  - `tools/vault_search`의 `mcp` 검색 방식: 프록시(나중에는 AgentCore Gateway)의 도구로 vault를 검색한다.
  - `services/qmd_proxy/backend`: 프록시가 뒤의 qmd MCP 서버를 부른다.

MCP "Streamable HTTP"에서 서버는 응답을 JSON이나 `event: message` / `data: {…}` 형식(스트림 이벤트)으로 준다. 둘 다 읽는다.
서버가 상태를 갖지 않는(stateless) 경우만 가정한다 — 세션 id를 주고받지 않고 호출마다 `tools/call` 하나를 보낸다(qmd와 프록시가 그렇다).
다른 서버(예: Gateway)가 `initialize`를 먼저 요구하면 이 클라이언트는 그 호출을 하지 않으므로 실패한다 → Gateway에 연결할 때 확인한다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any


class McpError(RuntimeError):
    """MCP 서버에 닿지 못했거나 서버가 JSON-RPC 오류를 돌려줬다."""


def parse_mcp_body(body: str) -> dict[str, Any]:
    """MCP 응답 본문에서 JSON-RPC 응답 하나를 꺼낸다. JSON이거나 `data:` 줄(스트림 이벤트) 형식이다."""
    text = body.strip()
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
            if isinstance(payload, dict) and ("result" in payload or "error" in payload):
                return payload
    raise McpError(f"MCP 응답에서 JSON-RPC 응답을 찾지 못했다: {text[:200]!r}")


Post = Callable[[str, bytes, dict[str, str], float], str]


def default_post(url: str, data: bytes, headers: dict[str, str], timeout: float) -> str:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (주소는 설정값이다)
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise McpError(f"MCP 서버가 HTTP {exc.code}로 응답했다({url})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise McpError(f"MCP 서버에 닿지 못했다({url}): {exc}") from exc


class McpHttpClient:
    """상태 없는 MCP 서버에 `tools/call`·`tools/list`를 보낸다. url은 전체 주소(예: http://host:8282/mcp)."""

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        post: Post = default_post,
        timeout: float = 120,
    ) -> None:
        self._url = url
        self._headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})}
        self._post = post
        self._timeout = timeout
        self._next_id = 0

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            request["params"] = params
        body = self._post(self._url, json.dumps(request, ensure_ascii=False).encode("utf-8"), self._headers, self._timeout)
        response = parse_mcp_body(body)
        if "error" in response:
            raise McpError(f"MCP 오류: {response['error']}")
        return response["result"]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """도구 하나를 부르고 MCP 결과(content, structuredContent, isError)를 그대로 돌려준다."""
        return self._rpc("tools/call", {"name": name, "arguments": arguments})

    def list_tools(self) -> list[dict[str, Any]]:
        return self._rpc("tools/list")["tools"]
