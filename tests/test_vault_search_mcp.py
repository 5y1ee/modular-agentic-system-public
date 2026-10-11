"""vault_search의 mcp 방식(qmd 프록시·AgentCore Gateway의 검색 도구 호출). 가짜 MCP 클라이언트로 서버 없이 검증한다.

핵심: (1) MCP 결과(structuredContent.results)를 기존 HTTP와 같은 VaultHit으로 바꾼다(컬렉션 접두어 제거, 줄 번호·@@ 머리말 제거)
(2) 도구 오류·결과 없음·서버 오류는 VaultSearchError (3) 키는 Authorization 헤더로만 가고 오류 메시지에 안 남는다
(4) `MAS_MCP_URL`이 있으면 auto가 mcp를 고르고 없으면 기존 방식 (5) 제외 목록과 n개 제한 (6) 공유 MCP 클라이언트의 파싱
"""
import json

import pytest

from modular_agentic.tools import vault_search as vs
from modular_agentic.tools.mcp_http import McpError, McpHttpClient, parse_mcp_body

RESULTS = {
    "results": [
        {"docid": "#a1", "file": "vault/04_project/FINT.md", "title": "FINT", "score": 1, "context": None, "line": 1,
         "snippet": "1: @@ -1,3 @@ (0 before, 42 after)\n2: # FINT\n3: \n4: #project"},
        {"docid": "#b2", "file": "vault/06_career/자소서/26.09-지원처 7.md", "title": "지원처 7", "score": 0.5, "line": 41,
         "snippet": "41: FINT를 개발했다"},
        {"docid": "#c3", "file": "vault/02_concept/Modular%20Agentic%20System.md", "title": "MAS", "score": 0.3, "line": 11,
         "snippet": "11: 템플릿"},
    ]
}  # fmt: skip


class FakeClient:
    def __init__(self, result):
        self.result, self.calls = result, []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def ok(structured=RESULTS):
    return {"content": [{"type": "text", "text": "Found"}], "structuredContent": structured}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (vs.MCP_URL_ENV, vs.MCP_KEY_ENV, vs.MCP_TOOL_ENV, vs.LOG_ENV):
        monkeypatch.delenv(name, raising=False)


def test_mcp_hits_are_converted_like_http_hits():
    client = FakeClient(ok())

    hits = vs.search_mcp_raw("FINT RAG", 32, url="http://x/mcp", client=client)

    assert client.calls == [("search_vault", {"query": "FINT RAG", "limit": 32})]
    assert [(h.path, h.line, h.docid) for h in hits] == [
        ("04_project/FINT.md", 1, "#a1"),  # 컬렉션 접두어(vault/) 제거
        ("06_career/자소서/26.09-지원처 7.md", 41, "#b2"),
        ("02_concept/Modular Agentic System.md", 11, "#c3"),  # 퍼센트 인코딩 해제
    ]
    assert hits[0].snippet == "# FINT\n\n#project"  # 줄 번호 접두어와 @@ 머리말 제거


def test_search_applies_exclusions_and_limit_for_mcp(monkeypatch):
    client = FakeClient(ok())

    hits = vs.search("q", n=1, exclude_prefixes=("04_project/",), backend="mcp", mcp_url="http://x/mcp", mcp_client=client)
    all_hits = vs.search("q", n=10, backend="mcp", mcp_url="http://x/mcp", mcp_client=client)

    assert [h.path for h in hits] == ["06_career/자소서/26.09-지원처 7.md"]  # 제외 후 n개
    assert len(all_hits) == 3
    assert client.calls[0][1]["limit"] == max(1 * vs.FETCH_MULTIPLIER, vs.MIN_FETCH)  # 제외를 거르려고 더 가져온다


@pytest.mark.parametrize(
    "result,message",
    [
        ({"isError": True, "content": [{"type": "text", "text": "인자 오류: limit은 50 이하여야 한다"}]}, "인자 오류"),
        ({"content": [{"type": "text", "text": "x"}]}, "structuredContent.results"),  # Gateway 등이 구조화 결과를 떨어뜨린 경우
        (McpError("MCP 서버에 닿지 못했다"), "닿지 못했다"),
        (ValueError("깨진 JSON"), "깨진 JSON"),
    ],
)
def test_failures_become_vault_search_error(result, message):
    with pytest.raises(vs.VaultSearchError, match=message):
        vs.search_mcp_raw("q", 20, url="http://x/mcp", client=FakeClient(result))


def test_api_key_goes_only_in_header_and_never_in_errors():
    seen = {}

    def post(url, data, headers, timeout):
        seen["headers"] = headers
        raise McpError(f"MCP 서버에 닿지 못했다({url}): refused")

    with pytest.raises(vs.VaultSearchError) as error:
        vs.search_mcp_raw("q", 20, url="http://x/mcp", api_key="qmdp_SECRET", client=McpHttpClient("http://x/mcp", headers={"Authorization": "Bearer qmdp_SECRET"}, post=post))

    assert seen["headers"]["Authorization"] == "Bearer qmdp_SECRET"
    assert "qmdp_SECRET" not in str(error.value)


def test_env_selects_mcp_in_auto_and_reads_tool_and_key(monkeypatch):
    captured = {}

    class Spy:
        def __init__(self, url, headers=None, timeout=0):
            captured.update(url=url, headers=headers)

        def call_tool(self, name, arguments):
            captured["tool"] = name
            return ok()

    monkeypatch.setattr(vs, "McpHttpClient", Spy)
    monkeypatch.setenv(vs.MCP_URL_ENV, "http://gw/mcp")
    monkeypatch.setenv(vs.MCP_KEY_ENV, "k1")
    monkeypatch.setenv(vs.MCP_TOOL_ENV, "vault-target___search_vault")  # Gateway는 도구 이름에 대상 이름을 붙인다

    hits = vs.search("q", n=2)  # backend="auto"

    assert len(hits) == 2 and captured["url"] == "http://gw/mcp" and captured["tool"] == "vault-target___search_vault"
    assert captured["headers"] == {"Authorization": "Bearer k1"}
    assert vs.current_backend() == "mcp http://gw/mcp"


def test_without_env_auto_does_not_pick_mcp_and_explicit_mcp_needs_url(monkeypatch):
    assert not vs.current_backend().startswith("mcp")
    with pytest.raises(vs.VaultSearchError, match="주소가 필요"):
        vs.search("q", backend="mcp")
    with pytest.raises(ValueError):
        vs.search("q", backend="grpc")


def test_search_log_records_mcp_method(tmp_path, monkeypatch):
    log = tmp_path / "s.jsonl"
    monkeypatch.setenv(vs.LOG_ENV, str(log))

    vs.search_mcp_raw("로그 질의", 20, url="http://x/mcp", client=FakeClient(ok()), tag="t")

    record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert (record["method"], record["tag"], record["query"], len(record["hits"])) == ("mcp-vec", "t", "로그 질의", 3)


def test_shared_client_parses_event_stream_and_raises_on_rpc_error():
    event = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"a"}]}}\n\n'
    assert parse_mcp_body(event)["result"]["tools"][0]["name"] == "a"
    with pytest.raises(McpError):
        parse_mcp_body("event: ping\n\n")
    client = McpHttpClient("http://x/mcp", post=lambda *a: '{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"boom"}}')
    with pytest.raises(McpError):
        client.list_tools()
