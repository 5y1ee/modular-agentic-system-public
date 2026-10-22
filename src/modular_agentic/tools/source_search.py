"""조사 자료의 URL을 검색으로 찾는다. 찾기만 하고 읽거나 판단하지 않는다(읽기는 tools/sources.py, 판단은 Researcher).

흐름 (docs/design-research-stage.md 18절)
  1. 인스턴스가 질의 템플릿을 정한다(QuerySpec: 조사 주제, "{company} 최근 동향" 같은 틀, 뉴스 여부).
  2. plan_queries()가 회사 이름을 채워 질의를 만든다.
  3. SearchProvider가 질의마다 후보(SearchHit)를 가져온다. 제공자는 바꿔 끼울 수 있다(지금은 Tavily만).
  4. find_sources()가 중복(같은 URL, 사용자가 이미 준 URL)을 빼고, 질의별로 번갈아 가며 상위 결과를 골라 상한까지 채운다.
  찾은 URL을 읽어 SourceDoc으로 만드는 일은 tools/sources.collect_docs가 한다. 사용자가 직접 준 URL·파일이 항상 먼저다.

무엇을 믿으면 안 되나
  검색 결과가 관련 있다는 보장은 없다(동명 회사, 광고성 글, 오래된 글). 여기서는 관련성을 판단하지 않는다. 읽은 뒤
  Researcher가 내용으로 주장을 뽑고, 사용자가 보고서에서 "검색으로 찾음" 표시가 붙은 출처를 보고 거른다.
  검색 서비스가 주는 요약 문장(snippet)은 후보를 보여 주는 데만 쓰고 사실로 쓰지 않는다.

비밀(API 키)
  키는 환경변수 TAVILY_API_KEY 또는 파일 ~/.config/key/tavily_key(권한 600)에서만 읽는다. 로그·오류 메시지·예외에 넣지 않는다.
  파일 권한이 넓으면(그룹·다른 사용자가 읽을 수 있으면) 쓰지 않고 거절한다(POSIX만 확인한다).

확인하지 못한 것
  Tavily 서비스가 지금 정상인지, 무료 한도가 얼마인지는 키로 호출해 보기 전에는 모른다. Strands에 들어 있는 tavily 도구는
  deprecated(공식 MCP 서버로 옮기라고 안내)지만, 이 모듈은 그 도구가 아니라 REST 주소(POST /search)를 직접 부른다.
  이 주소·응답 모양은 설치된 Strands 코드에서 읽은 것이고 실제 호출로 확인한 것이 아니다.
"""
from __future__ import annotations

import json
import os
import stat
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_KEY_ENV = "TAVILY_API_KEY"
TAVILY_KEY_PATH = Path.home() / ".config" / "key" / "tavily_key"
SEARCH_TIMEOUT_SECONDS = 20

DEFAULT_PER_QUERY = 3
DEFAULT_MAX_TOTAL = 10
_TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "ref", "referrer")


class SearchError(RuntimeError):
    """검색 한 번이 실패했다(네트워크, 서버 오류 등). 다른 질의는 계속할 수 있다."""


class SearchUnavailable(SearchError):
    """검색을 쓸 수 없다(키가 없음·거절됨·한도 초과). 다른 질의도 같은 이유로 실패하므로 검색 전체를 접는다."""


@dataclass(frozen=True)
class SearchHit:
    """검색 후보 하나. snippet은 서비스가 준 요약이라 사실로 쓰지 않는다."""

    title: str
    url: str
    snippet: str = ""
    published: str = ""
    query: str = ""  # 이 후보를 낸 질의


class SearchProvider(Protocol):
    def search(
        self,
        query: str,
        *,
        max_results: int = DEFAULT_PER_QUERY,
        news: bool = False,
        include_domains: Sequence[str] = (),
        exclude_domains: Sequence[str] = (),
    ) -> list[SearchHit]:
        """질의의 후보를 순위 순으로 돌려준다. 키가 없거나 거절되면 SearchUnavailable, 그 밖의 실패는 SearchError."""
        ...


@dataclass(frozen=True)
class QuerySpec:
    """질의 틀. template의 {company}가 회사 이름으로 바뀐다. news=True면 뉴스 검색으로 부른다."""

    topic: str
    template: str
    news: bool = False


@dataclass(frozen=True)
class PlannedQuery:
    topic: str
    query: str
    news: bool = False


@dataclass(frozen=True)
class FindResult:
    hits: list[SearchHit]
    queries: list[PlannedQuery]
    errors: list[str] = field(default_factory=list)  # 일부 질의가 실패한 경우의 이유(키·URL은 넣지 않는다)
    skipped_reason: str = ""  # 검색을 아예 못 했을 때의 이유(예: 키 없음)


# ── 비밀 읽기 ───────────────────────────────────────────────────────────────


def load_tavily_key(path: Path = TAVILY_KEY_PATH, env: Mapping[str, str] | None = None) -> str:
    """Tavily 키를 환경변수 또는 파일에서 읽는다. 없으면 SearchUnavailable. 키 값은 어디에도 출력하지 않는다."""
    env = os.environ if env is None else env
    key = env.get(TAVILY_KEY_ENV, "").strip()
    if key:
        return key
    try:
        info = path.stat()
    except OSError:
        raise SearchUnavailable(f"Tavily 키가 없다(환경변수 {TAVILY_KEY_ENV} 또는 파일 {path})") from None
    if os.name == "posix" and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SearchUnavailable(f"키 파일의 권한이 넓다. `chmod 600 {path}`로 줄이고 다시 한다")
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise SearchUnavailable(f"키 파일을 읽을 수 없다({path})") from None
    if not key:
        raise SearchUnavailable(f"키 파일이 비어 있다({path})")
    return key


# ── Tavily ──────────────────────────────────────────────────────────────────

PostJson = Callable[[str, dict, dict], tuple[int, dict]]  # (주소, 요청 본문, 헤더) → (상태 코드, 응답 JSON)


def post_json(url: str, payload: dict, headers: dict) -> tuple[int, dict]:
    """실제 POST. 테스트는 이 함수를 가짜로 바꿔 끼운다. 오류 상태 코드는 예외가 아니라 값으로 돌려준다."""
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:  # noqa: S310 — 주소는 상수(https)
            return response.status, json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
        except ValueError:
            body = {}
        return exc.code, body


class TavilySearch:
    """SearchProvider를 Tavily REST API로 구현한다(외부 의존성 없이 표준 라이브러리로 호출)."""

    def __init__(self, *, key_loader: Callable[[], str] = load_tavily_key, post: PostJson = post_json) -> None:
        self._key_loader = key_loader
        self._post = post

    def search(
        self,
        query: str,
        *,
        max_results: int = DEFAULT_PER_QUERY,
        news: bool = False,
        include_domains: Sequence[str] = (),
        exclude_domains: Sequence[str] = (),
    ) -> list[SearchHit]:
        key = self._key_loader()  # 없으면 SearchUnavailable
        payload: dict = {"query": query, "max_results": max_results, "search_depth": "basic", "topic": "news" if news else "general"}
        if include_domains:
            payload["include_domains"] = list(include_domains)
        if exclude_domains:
            payload["exclude_domains"] = list(exclude_domains)
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            status, data = self._post(TAVILY_SEARCH_URL, payload, headers)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SearchError(f"검색 접속 실패: {type(exc).__name__}") from None  # 원 예외 문구에 키가 섞일 일은 없지만 종류만 남긴다
        if status in (401, 403):
            raise SearchUnavailable(f"검색 키가 거절됐다(HTTP {status})")
        if status == 429:
            raise SearchUnavailable("검색 사용 한도를 넘었다(HTTP 429)")
        if status >= 400:
            raise SearchError(f"검색 실패(HTTP {status})")
        hits = []
        for rank in data.get("results", []) if isinstance(data, dict) else []:
            url = str(rank.get("url", "")).strip()
            if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
                continue
            hits.append(
                SearchHit(
                    title=str(rank.get("title", "")).strip(),
                    url=url,
                    snippet=str(rank.get("content", "")).strip(),
                    published=str(rank.get("published_date", "") or "").strip(),
                    query=query,
                )
            )
        return hits


# ── 질의 만들기와 고르기 ────────────────────────────────────────────────────


def plan_queries(company: str, specs: Iterable[QuerySpec]) -> list[PlannedQuery]:
    """질의 틀의 {company}를 회사 이름으로 채운다. 회사 이름이 비어 있으면 질의를 만들지 않는다(엉뚱한 검색을 막으려고)."""
    company = company.strip()
    if not company:
        return []
    return [PlannedQuery(topic=s.topic, query=s.template.replace("{company}", company).strip(), news=s.news) for s in specs]


def normalize_url(url: str) -> str:
    """같은 페이지를 같게 보려고 주소를 정리한다: 스킴·호스트 소문자, 조각(#…)·추적 파라미터·끝 슬래시 제거, www 제거."""
    parts = urllib.parse.urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING_PARAMS)]
    )
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    return f"{parts.scheme.lower()}://{host}{port}{parts.path.rstrip('/')}{'?' + query if query else ''}"


def find_sources(
    company: str,
    specs: Iterable[QuerySpec],
    provider: SearchProvider,
    *,
    extra_queries: Iterable[str] = (),
    exclude_urls: Iterable[str] = (),
    exclude_domains: Sequence[str] = (),
    per_query: int = DEFAULT_PER_QUERY,
    max_total: int = DEFAULT_MAX_TOTAL,
) -> FindResult:
    """질의마다 검색해 후보 URL을 고른다.

    exclude_urls: 사용자가 이미 준 URL. 같은 페이지를 또 읽지 않는다.
    extra_queries: 사용자가 직접 준 검색어(회사 이름 없이도 된다).
    고르는 법: 질의마다 상위 per_query개를 받아 질의 사이를 번갈아 가며(1위들, 2위들…) 중복 없이 max_total개까지 채운다.
    한 질의가 상한을 다 차지하지 않게 하려는 것이다. 순위는 검색 서비스의 것이고 여기서 점수로 걸러내지 않는다.
    검색을 쓸 수 없으면(SearchUnavailable) 예외 대신 skipped_reason을 채워 돌려준다. 호출한 쪽은 사용자 자료만으로 계속 간다.
    """
    planned = plan_queries(company, specs) + [PlannedQuery(topic="", query=q.strip()) for q in extra_queries if q.strip()]
    if not planned:
        return FindResult(hits=[], queries=[], skipped_reason="검색할 질의가 없다(회사 이름이 비어 있음)")

    per_query_hits: list[list[SearchHit]] = []
    errors: list[str] = []
    for q in planned:
        try:
            per_query_hits.append(provider.search(q.query, max_results=per_query, news=q.news, exclude_domains=exclude_domains))
        except SearchUnavailable as exc:
            return FindResult(hits=[], queries=planned, errors=errors, skipped_reason=str(exc))
        except SearchError as exc:
            errors.append(f"질의 '{q.query}': {exc}")
            per_query_hits.append([])

    seen = {normalize_url(u) for u in exclude_urls}
    chosen: list[SearchHit] = []
    for rank in range(per_query):
        for hits in per_query_hits:
            if rank >= len(hits) or len(chosen) >= max_total:
                continue
            key = normalize_url(hits[rank].url)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(hits[rank])
    return FindResult(hits=chosen, queries=planned, errors=errors)
