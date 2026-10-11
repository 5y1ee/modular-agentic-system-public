"""프록시가 qmd를 부르는 두 가지 길.

QmdMcpBackend  qmd의 MCP 서버(`qmd mcp --http`, `POST /mcp`)를 JSON-RPC로 부른다. 검색·조회 도구(query, get, multi_get, status)용.
               호출·응답 파싱은 `tools/mcp_http.py`(qmd는 응답을 `event: message` / `data: {…}` 형식이나 JSON으로 준다, 2026-10-05 직접 확인).
QmdCli         qmd 명령줄을 부른다. MCP에 없는 명령(ls, collection …, context …)과 관리 명령(update, embed …)용.
               `shell=True`나 cmd.exe를 거치지 않고 인자 목록으로만 실행한다(검색어·이름 같은 외부 텍스트가 명령으로 해석되지 않게).
               윈도우의 `qmd.cmd`는 기존 `resolve_qmd_argv`가 `node`로 직접 호출한다.

둘 다 호출 함수를 주입받는다 — 테스트는 qmd 없이 가짜로 돌린다.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from modular_agentic.tools.mcp_http import McpError, McpHttpClient, Post, default_post
from modular_agentic.tools.mcp_http import parse_mcp_body as _parse_mcp_body
from modular_agentic.tools.vault_search import resolve_qmd_argv


# MCP 호출 코드는 tools/mcp_http.py 한 곳에 있다(검색 도구의 mcp 방식과 같이 쓴다).
BackendError = McpError  # 프록시 안에서는 "qmd에 닿지 못했다"는 뜻으로 부른다
parse_mcp_body = _parse_mcp_body


class QmdMcpBackend(McpHttpClient):
    """qmd의 MCP 서버(`qmd mcp --http`)를 부른다. 검색·조회 도구(query, get, multi_get, status)용."""

    def __init__(self, base_url: str = "http://localhost:8181", *, post: Post = default_post, timeout: float = 120) -> None:
        super().__init__(base_url.rstrip("/") + "/mcp", post=post, timeout=timeout)


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], dict[str, str], float], CliResult]


def _default_runner(argv: list[str], env: dict[str, str], timeout: float) -> CliResult:
    try:
        done = subprocess.run(  # noqa: S603 (shell 없이 인자 목록, 값은 registry가 검증했다)
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return CliResult(124, "", f"시간 초과({timeout:.0f}초)")
    return CliResult(done.returncode, done.stdout, done.stderr)


class QmdCli:
    def __init__(
        self,
        *,
        argv_prefix: Sequence[str] | None = None,
        runner: Runner = _default_runner,
        force_cpu: bool = False,
    ) -> None:
        self._argv_prefix = list(argv_prefix) if argv_prefix is not None else None
        self._runner = runner
        self._force_cpu = force_cpu

    def run(self, args: Sequence[str], timeout: float) -> CliResult:
        prefix = self._argv_prefix if self._argv_prefix is not None else resolve_qmd_argv()
        env = dict(os.environ)
        if self._force_cpu:
            env["QMD_FORCE_CPU"] = "1"
        return self._runner([*prefix, *args], env, timeout)
