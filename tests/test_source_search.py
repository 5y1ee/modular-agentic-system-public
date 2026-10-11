"""tools/source_search: 검색으로 조사 자료 URL 찾기. 가짜 제공자·가짜 HTTP로 네트워크와 키 없이 검증한다.

핵심: (1) 질의 틀에 회사 이름이 채워지고 회사 이름이 없으면 검색하지 않는다 (2) 사용자가 이미 준 URL과 중복은 빠진다
(3) 질의 사이를 번갈아 골라 한 질의가 상한을 다 차지하지 않는다 (4) 키가 없거나 거절되면 예외 대신 건너뛴 이유를 돌려주고
한 질의만 실패하면 나머지는 계속한다 (5) 키는 환경변수·권한 600 파일에서만 읽고 어떤 메시지에도 나오지 않는다
(6) Tavily 요청·응답 모양 (7) 찾은 URL은 사용자가 준 자료 뒤에 붙고 "검색:" 표시가 남는다.
"""
import os
import urllib.error

import pytest

from modular_agentic.core.types import Item, ReadStatus
from modular_agentic.tools.source_search import (
    QuerySpec,
    SearchError,
    SearchHit,
    SearchUnavailable,
    TavilySearch,
    find_sources,
    load_tavily_key,
    normalize_url,
    plan_queries,
)
from modular_agentic.tools.sources import collect_docs

SPECS = [QuerySpec("사업", "{company} 회사 소개"), QuerySpec("동향", "{company} 최근 실적", news=True)]


def hit(url, query="q", title="t"):
    return SearchHit(title=title, url=url, snippet="요약", query=query)


class FakeProvider:
    def __init__(self, results: dict[str, list[SearchHit]] | None = None, fail: dict[str, Exception] | None = None):
        self.results = results or {}
        self.fail = fail or {}
        self.calls: list[dict] = []

    def search(self, query, *, max_results=3, news=False, include_domains=(), exclude_domains=()):
        self.calls.append({"query": query, "max_results": max_results, "news": news, "exclude_domains": tuple(exclude_domains)})
        if query in self.fail:
            raise self.fail[query]
        return self.results.get(query, [])[:max_results]


# ── 질의 만들기 ──


def test_plan_fills_company_name_and_keeps_news_flag():
    planned = plan_queries("현대오토에버", SPECS)
    assert [(p.topic, p.query, p.news) for p in planned] == [
        ("사업", "현대오토에버 회사 소개", False),
        ("동향", "현대오토에버 최근 실적", True),
    ]


def test_blank_company_name_makes_no_queries_and_no_search_happens():
    provider = FakeProvider()
    result = find_sources("  ", SPECS, provider)
    assert plan_queries("", SPECS) == [] and provider.calls == []
    assert result.hits == [] and "회사 이름" in result.skipped_reason


# ── 고르기 ──


def test_hits_are_taken_round_robin_across_queries_and_capped():
    provider = FakeProvider(
        {
            "A 회사 소개": [hit("https://a.com/1", "A 회사 소개"), hit("https://a.com/2"), hit("https://a.com/3")],
            "A 최근 실적": [hit("https://b.com/1", "A 최근 실적"), hit("https://b.com/2")],
        }
    )
    result = find_sources("A", SPECS, provider, per_query=3, max_total=3)
    # 1위들(a1, b1)이 먼저 들어가고 상한 3에서 2위 중 첫 질의의 것이 들어간다. 한 질의가 상한을 독점하지 않는다.
    assert [h.url for h in result.hits] == ["https://a.com/1", "https://b.com/1", "https://a.com/2"]


def test_duplicates_and_user_given_urls_are_dropped():
    provider = FakeProvider(
        {
            "A 회사 소개": [hit("https://www.a.com/x/?utm_source=z#top"), hit("https://user.com/mine")],
            "A 최근 실적": [hit("https://a.com/x"), hit("https://c.com/y")],
        }
    )
    result = find_sources("A", SPECS, provider, exclude_urls=["https://user.com/mine/"])
    assert [h.url for h in result.hits] == ["https://www.a.com/x/?utm_source=z#top", "https://c.com/y"]


def test_normalize_url_ignores_scheme_case_www_fragment_tracking_and_trailing_slash():
    assert normalize_url("HTTPS://WWW.Example.com/a/b/?utm_campaign=x&id=3#frag") == "https://example.com/a/b?id=3"
    assert normalize_url("https://example.com/a") != normalize_url("https://example.com/b")


def test_extra_queries_from_the_user_are_searched_too():
    provider = FakeProvider({"내가 쓴 검색어": [hit("https://d.com/1", "내가 쓴 검색어")]})
    result = find_sources("A", [], provider, extra_queries=["내가 쓴 검색어", "  "])
    assert [c["query"] for c in provider.calls] == ["내가 쓴 검색어"] and len(result.hits) == 1


# ── 실패 ──


def test_unavailable_search_is_reported_as_skipped_not_raised():
    provider = FakeProvider(fail={"A 회사 소개": SearchUnavailable("Tavily 키가 없다")})
    result = find_sources("A", SPECS, provider)
    assert result.hits == [] and result.skipped_reason == "Tavily 키가 없다"
    assert [c["query"] for c in provider.calls] == ["A 회사 소개"]  # 같은 이유로 실패할 나머지 질의는 부르지 않는다


def test_one_failed_query_does_not_stop_the_others():
    provider = FakeProvider({"A 최근 실적": [hit("https://b.com/1")]}, fail={"A 회사 소개": SearchError("검색 실패(HTTP 500)")})
    result = find_sources("A", SPECS, provider)
    assert [h.url for h in result.hits] == ["https://b.com/1"]
    assert result.errors == ["질의 'A 회사 소개': 검색 실패(HTTP 500)"] and result.skipped_reason == ""


# ── 키 ──


def test_key_comes_from_env_first_then_a_private_file(tmp_path):
    path = tmp_path / "tavily_key"
    path.write_text("file-key\n", encoding="utf-8")
    path.chmod(0o600)
    assert load_tavily_key(path, env={"TAVILY_API_KEY": " env-key "}) == "env-key"
    assert load_tavily_key(path, env={}) == "file-key"


def test_missing_or_empty_key_is_unavailable_without_leaking_anything(tmp_path):
    with pytest.raises(SearchUnavailable, match="키가 없다"):
        load_tavily_key(tmp_path / "없음", env={})
    empty = tmp_path / "empty"
    empty.write_text("  \n", encoding="utf-8")
    empty.chmod(0o600)
    with pytest.raises(SearchUnavailable, match="비어"):
        load_tavily_key(empty, env={})


@pytest.mark.skipif(os.name != "posix", reason="파일 권한 검사는 POSIX만")
def test_key_file_readable_by_others_is_refused(tmp_path):
    path = tmp_path / "tavily_key"
    path.write_text("secret-value-123", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(SearchUnavailable) as exc:
        load_tavily_key(path, env={})
    assert "chmod 600" in str(exc.value) and "secret-value-123" not in str(exc.value)


# ── Tavily 요청·응답 ──


def fake_post(status=200, data=None, seen=None):
    def post(url, payload, headers):
        if seen is not None:
            seen.append((url, payload, headers))
        return status, data if data is not None else {}

    return post


def test_tavily_sends_the_expected_request_and_parses_results():
    seen: list = []
    data = {
        "results": [
            {"title": "회사 소개", "url": "https://a.com/about", "content": "요약문", "score": 0.9, "published_date": "2026-08-14"},
            {"title": "이상한 주소", "url": "javascript:alert(1)", "content": "x"},
            {"title": "ftp", "url": "ftp://a.com/f", "content": "x"},
        ]
    }
    provider = TavilySearch(key_loader=lambda: "KEY", post=fake_post(data=data, seen=seen))

    hits = provider.search("A 최근 실적", max_results=4, news=True, exclude_domains=["spam.com"])

    [(url, payload, headers)] = seen
    assert url == "https://api.tavily.com/search" and headers["Authorization"] == "Bearer KEY"
    assert payload == {"query": "A 최근 실적", "max_results": 4, "search_depth": "basic", "topic": "news", "exclude_domains": ["spam.com"]}
    assert hits == [SearchHit(title="회사 소개", url="https://a.com/about", snippet="요약문", published="2026-08-14", query="A 최근 실적")]  # http(s)가 아닌 주소는 버린다


@pytest.mark.parametrize("status,expected", [(401, SearchUnavailable), (403, SearchUnavailable), (429, SearchUnavailable), (500, SearchError)])
def test_tavily_http_errors_map_to_unavailable_or_error_without_the_key(status, expected):
    provider = TavilySearch(key_loader=lambda: "SECRET-KEY", post=fake_post(status=status))
    with pytest.raises(expected) as exc:
        provider.search("q")
    assert "SECRET-KEY" not in str(exc.value)
    assert (expected is SearchUnavailable) == isinstance(exc.value, SearchUnavailable)


def test_tavily_network_failure_is_a_search_error_and_missing_key_is_unavailable():
    def down(url, payload, headers):
        raise urllib.error.URLError("연결 안 됨 SECRET-KEY")

    with pytest.raises(SearchError) as exc:
        TavilySearch(key_loader=lambda: "SECRET-KEY", post=down).search("q")
    assert "SECRET-KEY" not in str(exc.value) and not isinstance(exc.value, SearchUnavailable)

    def no_key():
        raise SearchUnavailable("Tavily 키가 없다")

    with pytest.raises(SearchUnavailable):
        TavilySearch(key_loader=no_key, post=fake_post()).search("q")


# ── 사용자 자료와 합치기 ──


def test_found_urls_come_after_user_sources_are_marked_and_not_read_twice(tmp_path):
    (tmp_path / "메모.txt").write_text("사용자 메모", encoding="utf-8")
    pages = {
        "https://user.com/mine": "<html><body><p>" + "사용자가 준 페이지 본문. " * 20 + "</p></body></html>",
        "https://found.com/news": "<html><body><p>" + "검색으로 찾은 기사 본문. " * 20 + "</p></body></html>",
    }
    fetched: list[str] = []

    def get(url):
        fetched.append(url)
        return 200, "text/html; charset=utf-8", pages[url].encode()

    found = [hit("https://found.com/news", "A 최근 실적"), hit("https://www.user.com/mine/", "A 회사 소개")]  # 두 번째는 사용자가 준 URL과 같은 페이지
    item = Item(id="i", source="t", title="공고", body="공고 본문")

    docs = collect_docs(item, upload_dir=tmp_path, urls=["https://user.com/mine"], found=found, get=get)

    assert [d.id for d in docs] == ["d1", "d2", "d3", "d4"]
    assert [d.found_by for d in docs] == ["", "", "", "검색: A 최근 실적"]  # 공고·업로드·사용자 URL은 표시가 없다
    assert fetched == ["https://user.com/mine", "https://found.com/news"]  # 겹친 페이지는 한 번만 읽는다
    assert all(d.status is ReadStatus.READ for d in docs)
