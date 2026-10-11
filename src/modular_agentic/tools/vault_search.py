"""knowledge-base를 검색하는 도구. `qmd vsearch`(벡터 검색)를 감싼다.

표준 라이브러리만 쓴다. qmd 자체는 외부 프로그램이라 PATH에 있어야 한다.

알아 둘 점 (2026-10-03 실제 실행으로 확인한 것):
- `--format json`의 결과 필드: docid, score, file, line, title, snippet.
- file은 디스크 경로가 아니라 `qmd://<컬렉션>/<vault 안 경로>` 형태다. 여기서는 컬렉션을
  떼고 vault 안 경로(`06_career/...`)만 쓴다.
- snippet은 `@@ -6,4 @@ (5 before, 70 after)` 같은 diff 머리말로 시작해서 떼어 낸다.
  끝이 `...`으로 잘려 있을 수 있다.
- 점수는 관련 여부를 가르지 못한다(무관한 결과가 관련 결과와 같은 점수가 나온 적 있음).
  그래서 이 도구는 점수로 거르지 않고, 관련 판단은 호출하는 쪽(매핑 에이전트)이 내용을 읽고 한다.
- JSON 앞에 진행 표시가 같이 출력된다. stdout과 stderr 중 어디로 나오는지는 확인하지 못해서,
  stdout 전체를 먼저 JSON으로 읽고 안 되면 첫 `[` 줄부터 읽는다.
- `qmd query`(리랭킹)는 이 서버에서 쿼리당 9~21분이라 쓰지 않는다.
- 환경변수 `MAS_SEARCH_LOG`에 파일 경로를 주면 검색 한 번마다 결과를 JSON Lines로 덧붙인다
  (검색어, 상위 결과 전체의 경로·점수·줄·발췌, 소요 시간, 기기). 제외 경로를 적용하기 전의 전체 결과를
  남기므로 제외한 관점과 안 한 관점을 나중에 둘 다 만들 수 있다. 자소서 본문 발췌가 들어가므로
  `logs/`는 git에 올리지 않는다.
- 검색 방식이 둘이다. (1) CLI: `qmd vsearch`를 호출마다 새 프로세스로 실행(질의 확장 포함, 새 질의 하나가 집 PC 12.6초).
  (2) HTTP: 상시 떠 있는 `qmd mcp --http` 서버의 `POST /query`에 `vec` 하위 질의 하나를 보냄(모델이 올라가 있어 집 PC 0.07초).
  서버 주소는 환경변수 `MAS_QMD_URL`, 없으면 기본 `http://localhost:8181`이다. 그 서버가 `/health`에 응답하면 HTTP를,
  아니면 CLI를 쓴다(주소를 직접 줬는데 죽어 있으면 경고를 남기고 CLI로 돌아간다). 서버가 없는 기기는 확인이
  바로 실패하므로 지연이 거의 없다. `search(..., backend="cli"|"http"|"auto")`로 직접 고를 수도 있다.
  HTTP는 `vsearch`와 알고리즘이 다르다(구조화 검색). 같은 질의여도 순위·조각 선택·점수 척도가 다르다
  (점수는 코사인 유사도가 아니라 결합된 값). 그래서 발췌의 줄 번호 접두어와 `@@` 머리말도 따로 떼어 낸다.
  윈도우에서 `localhost`는 IPv6(`::1`)로만 풀릴 수 있어 `127.0.0.1` 대신 `localhost`를 쓴다.
- CPU 강제(`QMD_FORCE_CPU`)는 기본으로 켜지 않는다. 처음엔 서버(GPU 없음)에 맞춰 기본을 켰는데,
  GPU가 있는 집 PC에서도 CPU를 강제해 H1이 질의당 약 46초로 느려졌다(2026-10-03). 필요하면
  `force_cpu=True`를 주거나 환경변수로 직접 켠다.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from modular_agentic.core.types import Evidence
from modular_agentic.tools.mcp_http import McpError, McpHttpClient

# 제외한 뒤에도 n건을 채우려고, 실제로는 n의 이 배수만큼 받아 온다.
FETCH_MULTIPLIER = 4
MIN_FETCH = 20

_SNIPPET_HEADER_RE = re.compile(r"\A@@ .*?@@[^\n]*\n+")

Runner = Callable[[list[str], dict[str, str]], subprocess.CompletedProcess[str]]


class VaultSearchError(RuntimeError):
    """qmd 실행이나 출력 해석에 실패했을 때."""


@dataclass(frozen=True)
class VaultHit:
    path: str  # vault 안 경로 (예: "04_project/FINT.md")
    title: str
    score: float
    line: int
    snippet: str
    docid: str

    def to_evidence(self) -> Evidence:
        """매핑 단계가 쓰는 Evidence로 바꾼다. ref에 줄 번호를 붙여 검증이 다시 열 수 있게 한다."""
        return Evidence(ref=f"{self.path}:{self.line}", excerpt=self.snippet)


def resolve_qmd_argv(*, is_windows: bool | None = None, which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """qmd를 실행하는 명령의 앞부분을 돌려준다.

    윈도우에서 npm이 만드는 `qmd.cmd`는 Python의 subprocess가 직접 실행하지 못한다.
    `shell=True`나 cmd.exe를 거치면 검색어의 특수문자(& | " % 등)가 명령으로 해석될 위험이 있다.
    검색어는 공고처럼 외부 텍스트에서 올 수 있으므로, cmd를 거치지 않고 `node`로
    npm 패키지의 진입 스크립트를 직접 호출한다. (2026-10-03 집 PC에서 FileNotFoundError로 확인)
    """
    exe = which("qmd")
    if exe is None:
        raise VaultSearchError("qmd를 PATH에서 찾을 수 없다")
    windows = os.name == "nt" if is_windows is None else is_windows
    if windows and exe.lower().endswith((".cmd", ".bat")):
        entry = Path(exe).parent / "node_modules" / "@tobilu" / "qmd" / "bin" / "qmd"
        node = which("node")
        if node is None or not entry.exists():
            raise VaultSearchError(f"qmd.cmd를 node로 직접 호출할 경로를 찾지 못했다: {exe}")
        return [node, str(entry)]
    return [exe]


def _default_runner(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    argv = resolve_qmd_argv() + cmd[1:]  # cmd[0]은 "qmd" 자리표시자
    # qmd는 UTF-8로 출력한다. 윈도우 기본 인코딩(CP949)에 기대지 않고 명시한다.
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)


def _parse_json(stdout: str) -> list[dict]:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("["):
            return json.loads("\n".join(lines[i:]))
    raise VaultSearchError(f"qmd 출력에서 JSON을 찾지 못했다: {stdout[:200]!r}")


def _relative_path(qmd_file: str) -> str:
    """`qmd://vault/06_career/a.md` → `06_career/a.md`. 컬렉션 이름은 버린다."""
    rest = qmd_file.removeprefix("qmd://")
    _collection, _, path = rest.partition("/")
    return path or rest


def _clean_snippet(snippet: str) -> str:
    return _SNIPPET_HEADER_RE.sub("", snippet, count=1).strip()


LOG_ENV = "MAS_SEARCH_LOG"
LOG_SNIPPET_CHARS = 600  # 로그에 남기는 발췌 길이


def parse_hits(stdout: str) -> list[VaultHit]:
    """qmd의 JSON 출력을 VaultHit 목록으로 바꾼다. 제외 없이 전부."""
    return [
        VaultHit(
            path=_relative_path(raw["file"]),
            title=raw.get("title", ""),
            score=float(raw.get("score", 0.0)),
            line=int(raw.get("line", 0)),
            snippet=_clean_snippet(raw.get("snippet", "")),
            docid=raw.get("docid", ""),
        )
        for raw in _parse_json(stdout)
    ]


def append_log(
    query: str,
    hits: Sequence[VaultHit],
    *,
    elapsed_sec: float,
    method: str = "vsearch",
    tag: str = "",
    fetch_n: int | None = None,
    force_cpu: bool = False,
) -> None:
    """`MAS_SEARCH_LOG`가 설정돼 있으면 검색 한 번의 결과를 JSON Lines로 덧붙인다. 없으면 아무것도 안 한다."""
    path = os.environ.get(LOG_ENV)
    if not path:
        return
    record = {
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "machine": platform.node(),
        "method": method,  # 검색 방식 이름. 실험 스크립트가 구분하려고 붙인다
        "tag": tag,  # 어느 실험의 호출인지
        "query": query,
        "vault_rev": os.environ.get("MAS_VAULT_REV", ""),  # 어느 vault 상태에서 검색했는지(tools/vault_revision.py)
        "fetch_n": fetch_n,
        "force_cpu": force_cpu,
        "elapsed_sec": round(elapsed_sec, 2),
        "hits": [
            {
                "rank": i,
                "path": h.path,
                "title": h.title,
                "score": h.score,
                "line": h.line,
                "snippet": h.snippet[:LOG_SNIPPET_CHARS],
            }
            for i, h in enumerate(hits, 1)
        ],
    }
    log_file = Path(path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def search_raw(
    query: str,
    fetch_n: int = 40,
    *,
    force_cpu: bool = False,
    runner: Runner = _default_runner,
    tag: str = "",
) -> list[VaultHit]:
    """query로 vault를 검색해 제외 없이 상위 fetch_n건을 돌려준다. 로그는 여기서 남긴다."""
    cmd = ["qmd", "vsearch", query, "--format", "json", "-n", str(fetch_n)]
    env = dict(os.environ)
    if force_cpu:
        env["QMD_FORCE_CPU"] = "1"

    start = time.monotonic()
    proc = runner(cmd, env)
    elapsed = time.monotonic() - start
    if proc.returncode != 0:
        raise VaultSearchError(f"qmd 종료 코드 {proc.returncode}: {proc.stderr[-300:]}")

    hits = parse_hits(proc.stdout)
    append_log(query, hits, elapsed_sec=elapsed, tag=tag, fetch_n=fetch_n, force_cpu=force_cpu)
    return hits


QMD_URL_ENV = "MAS_QMD_URL"
DEFAULT_QMD_URL = "http://localhost:8181"  # qmd mcp --http의 기본 주소. 환경변수가 없어도 이 주소에 서버가 있으면 쓴다
_LINE_PREFIX_RE = re.compile(r"^\d+: ?")
_LOG = logging.getLogger(__name__)


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost는 프록시를 거치지 않는다


def _http_get_json(url: str, timeout: float = 2.0) -> dict:
    with _opener().open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post_json(url: str, payload: dict, timeout: float = 300.0) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with _opener().open(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_available(base_url: str) -> bool:
    """상시 서버가 떠 있고 응답하는지(`GET /health`). 어떤 오류든 False."""
    try:
        return _http_get_json(f"{base_url.rstrip('/')}/health").get("status") == "ok"
    except Exception:
        return False


def clean_http_snippet(snippet: str) -> str:
    """HTTP 응답의 발췌에서 줄 번호 접두어(`7: `)와 `@@ … @@` 머리말 줄을 떼어 낸다."""
    lines = [_LINE_PREFIX_RE.sub("", line, count=1) for line in snippet.splitlines()]
    return "\n".join(line for line in lines if not line.startswith("@@ ")).strip()


def parse_http_hits(payload: dict) -> list[VaultHit]:
    """`/query` 응답의 `results`를 VaultHit 목록으로 바꾼다. 제외 없이 전부."""
    return [
        VaultHit(
            path=unquote(_relative_path(r["file"])),  # 경로가 퍼센트 인코딩돼 있을 수 있어 풀어 준다
            title=r.get("title", ""),
            score=float(r.get("score", 0.0)),
            line=int(r.get("line", 0)),
            snippet=clean_http_snippet(r.get("snippet", "")),
            docid=r.get("docid", ""),
        )
        for r in payload.get("results", [])
    ]


def search_http_raw(
    query: str,
    fetch_n: int = 40,
    *,
    base_url: str,
    rerank: bool = False,
    tag: str = "",
    post: Callable[[str, dict], dict] | None = None,
) -> list[VaultHit]:
    """상시 서버의 `/query`에 `vec` 하위 질의 하나를 보내 제외 없이 상위 fetch_n건을 돌려준다. 로그는 여기서 남긴다.

    rerank를 켜면 서버의 리랭커가 후보를 다시 채점한다(집 PC에서 호출당 약 4.6~9.4초 더 든다).
    """
    payload = {"searches": [{"type": "vec", "query": query}], "limit": fetch_n, "rerank": rerank}
    start = time.monotonic()
    try:
        data = (post or _http_post_json)(f"{base_url.rstrip('/')}/query", payload)
    except (OSError, ValueError) as exc:  # URLError·타임아웃은 OSError, 깨진 JSON은 ValueError
        raise VaultSearchError(f"qmd HTTP 서버 호출 실패({base_url}): {exc}") from exc
    elapsed = time.monotonic() - start
    hits = parse_http_hits(data)
    append_log(
        query,
        hits,
        elapsed_sec=elapsed,
        method="http-vec-rerank" if rerank else "http-vec",
        tag=tag,
        fetch_n=fetch_n,
    )
    return hits


MCP_URL_ENV = "MAS_MCP_URL"
MCP_KEY_ENV = "MAS_MCP_KEY"
MCP_TOOL_ENV = "MAS_MCP_TOOL"
DEFAULT_MCP_TOOL = "search_vault"


def search_mcp_raw(
    query: str,
    fetch_n: int = 40,
    *,
    url: str,
    api_key: str | None = None,
    tool: str = DEFAULT_MCP_TOOL,
    tag: str = "",
    client: McpHttpClient | None = None,
) -> list[VaultHit]:
    """MCP 서버(qmd 프록시, 나중에는 AgentCore Gateway)의 검색 도구를 불러 제외 없이 상위 fetch_n건을 돌려준다. 로그는 여기서 남긴다.

    도구는 `{"query": 문장, "limit": 개수}`를 받고 qmd의 `structuredContent.results`(HTTP `/query`와 같은 모양)를 돌려준다고 가정한다.
    서버가 개수 상한(프록시의 `max_limit`)보다 큰 limit을 거부하면 그 이유가 오류 메시지로 올라온다.
    api_key는 `Authorization: Bearer`로만 보내고 오류 메시지·로그에는 넣지 않는다.
    """
    http = client or McpHttpClient(url, headers={"Authorization": f"Bearer {api_key}"} if api_key else None, timeout=300)
    start = time.monotonic()
    try:
        result = http.call_tool(tool, {"query": query, "limit": fetch_n})
    except (McpError, ValueError) as exc:  # 깨진 JSON은 ValueError
        raise VaultSearchError(f"MCP 검색 호출 실패({url}): {exc}") from exc
    elapsed = time.monotonic() - start
    if result.get("isError"):
        text = " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
        raise VaultSearchError(f"MCP 도구 `{tool}` 오류: {text[:300]}")
    structured = result.get("structuredContent")
    if not isinstance(structured, dict) or "results" not in structured:
        raise VaultSearchError(f"MCP 도구 `{tool}` 결과에 structuredContent.results가 없다")
    hits = parse_http_hits(structured)
    append_log(query, hits, elapsed_sec=elapsed, method="mcp-vec", tag=tag, fetch_n=fetch_n)
    return hits


def current_backend(base_url: str | None = None) -> str:
    """지금 `search(..., backend="auto")`가 고를 방식을 사람이 읽는 문자열로: 'mcp <주소>', 'http <주소>', 'cli'."""
    mcp_url = os.environ.get(MCP_URL_ENV)
    if mcp_url:
        return f"mcp {mcp_url}"
    url = base_url or os.environ.get(QMD_URL_ENV) or DEFAULT_QMD_URL
    return f"http {url}" if http_available(url) else "cli"


def search(
    query: str,
    n: int = 5,
    exclude_prefixes: Sequence[str] = (),
    *,
    force_cpu: bool = False,
    runner: Runner = _default_runner,
    tag: str = "",
    backend: str = "auto",
    base_url: str | None = None,
    rerank: bool = False,
    http_post: Callable[[str, dict], dict] | None = None,
    mcp_url: str | None = None,
    mcp_client: McpHttpClient | None = None,
) -> list[VaultHit]:
    """query로 vault를 검색해 exclude_prefixes로 시작하는 경로를 뺀 상위 n건을 돌려준다.

    exclude_prefixes는 vault 안 경로 기준이다 (예: "06_career/자소서/").
    qmd의 `-c`는 컬렉션 단위 필터라 폴더를 못 거르므로, 검색한 뒤 여기서 직접 거른다.

    backend: "cli"(vsearch를 새 프로세스로), "http"(상시 서버), "mcp"(MCP 서버: qmd 프록시·AgentCore Gateway),
    "auto"(기본: `MAS_MCP_URL`이 설정돼 있으면 mcp, 아니면 `MAS_QMD_URL` 서버가 응답하면 http, 아니면 cli).
    base_url은 http 서버 주소이고 없으면 `MAS_QMD_URL`을 쓴다. rerank는 http에서만 의미가 있다.
    mcp는 주소를 `MAS_MCP_URL`(또는 mcp_url), 키를 `MAS_MCP_KEY`, 도구 이름을 `MAS_MCP_TOOL`(기본 search_vault)에서 읽는다.
    """
    if backend not in ("auto", "cli", "http", "mcp"):
        raise ValueError(f"backend는 auto/cli/http/mcp 중 하나여야 한다: {backend!r}")
    fetch_n = max(n * FETCH_MULTIPLIER, MIN_FETCH)
    mcp_target = mcp_url or os.environ.get(MCP_URL_ENV)
    if backend == "mcp" or (backend == "auto" and mcp_target):
        if not mcp_target and mcp_client is None:
            raise VaultSearchError(f"mcp 방식에는 주소가 필요하다(`{MCP_URL_ENV}` 환경변수 또는 mcp_url)")
        mcp_hits = search_mcp_raw(
            query,
            fetch_n,
            url=mcp_target or "",
            api_key=os.environ.get(MCP_KEY_ENV),
            tool=os.environ.get(MCP_TOOL_ENV, DEFAULT_MCP_TOOL),
            tag=tag,
            client=mcp_client,
        )
        return [h for h in mcp_hits if not any(h.path.startswith(prefix) for prefix in exclude_prefixes)][:n]
    explicit_url = base_url or os.environ.get(QMD_URL_ENV)
    url = explicit_url or DEFAULT_QMD_URL
    use_http = backend == "http" or (backend == "auto" and http_available(url))
    if backend == "auto" and explicit_url and not use_http:  # 주소를 직접 줬는데 죽어 있을 때만 알린다
        _LOG.warning("qmd HTTP 서버(%s)가 응답하지 않아 CLI로 검색한다", url)

    if use_http:
        hits = search_http_raw(query, fetch_n, base_url=url, rerank=rerank, tag=tag, post=http_post)
    else:
        hits = search_raw(query, fetch_n, force_cpu=force_cpu, runner=runner, tag=tag)
    kept = [h for h in hits if not any(h.path.startswith(prefix) for prefix in exclude_prefixes)]
    return kept[:n]
