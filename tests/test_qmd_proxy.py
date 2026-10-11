"""qmd MCP 프록시의 로직(설정·백엔드 파싱·도구 레지스트리·MCP 프로토콜). 웹 서버와 qmd 없이 가짜로 검증한다.

핵심 보안·정확성 규칙
  (1) 키는 해시로만 비교하고 틀리면 인증 실패 (2) 관리 도구는 admin 역할 + 전역 스위치가 모두 있어야 보이고 실행된다
  (3) 리랭킹은 서버 설정이 허용하지 않으면 항상 끈다, 질의 문자열만 오면 vec 하위 질의로 바꾼다
  (4) 모르는 인자는 오류(qmd는 조용히 무시한다) (5) 명령줄 값은 `-`로 시작하거나 형식이 틀리면 거부
  (6) collection_add 경로는 허용 루트 안(심볼릭 링크 탈출 포함)만 (7) `collection update-cmd`는 어디에도 없다
"""
import json
import os

import pytest

from modular_agentic.services.qmd_proxy.backend import BackendError, CliResult, QmdMcpBackend, parse_mcp_body
from modular_agentic.services.qmd_proxy.config import KeyInfo, ProxyConfig, hash_key, load_config, new_key
from modular_agentic.services.qmd_proxy.protocol import SUPPORTED_VERSIONS, handle_message
from modular_agentic.services.qmd_proxy.registry import (
    InvalidArguments,
    UnknownTool,
    build_registry,
    check_collection_root,
)


# ---- 가짜 qmd ---------------------------------------------------------------------------------------------------
class FakeBackend:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"content": [{"type": "text", "text": f"fake {name}"}]}


class FakeCli:
    def __init__(self, result: CliResult | None = None):
        self.calls: list[tuple[list[str], float]] = []
        self.result = result or CliResult(0, "ok\n", "")

    def run(self, args, timeout):
        self.calls.append((list(args), timeout))
        return self.result


def make_config(tmp_path, **overrides) -> ProxyConfig:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    values = dict(
        keys={hash_key("reader-key"): KeyInfo("r", "reader"), hash_key("admin-key"): KeyInfo("a", "admin")},
        allow_admin_tools=True,
        collection_roots=(str(root),),
    )
    values.update(overrides)
    return ProxyConfig(**values)


def make_registry(tmp_path, backend=None, cli=None, **overrides):
    backend, cli = backend or FakeBackend(), cli or FakeCli()
    return build_registry(make_config(tmp_path, **overrides), backend, cli), backend, cli


# ---- 설정·인증 --------------------------------------------------------------------------------------------------
def test_authenticate_compares_hash_and_rejects_unknown_or_missing(tmp_path):
    config = make_config(tmp_path)

    assert config.authenticate("reader-key") == KeyInfo("r", "reader")
    assert config.authenticate("admin-key").role == "admin"
    assert config.authenticate("wrong") is None and config.authenticate("") is None and config.authenticate(None) is None
    assert all(len(h) == 64 for h in config.keys)  # 평문이 아니라 sha256 해시를 저장한다


def test_new_key_is_random_and_load_config_roundtrip(tmp_path):
    key = new_key()
    assert key.startswith("qmdp_") and key != new_key()
    path = tmp_path / "proxy.json"
    path.write_text(
        json.dumps({"keys": [{"name": "gw", "sha256": hash_key(key), "role": "reader"}], "max_limit": 7, "collection_roots": ["/x"]}),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.authenticate(key) == KeyInfo("gw", "reader")
    assert (config.max_limit, config.collection_roots, config.allow_admin_tools) == (7, ("/x",), False)  # 관리 도구는 기본 꺼짐
    path.write_text(json.dumps({"keys": [{"name": "x", "sha256": "a" * 64, "role": "root"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


# ---- 백엔드 응답 파싱 -------------------------------------------------------------------------------------------
def test_parse_mcp_body_reads_event_stream_and_plain_json():
    event = 'event: message\ndata: {"result":{"content":[{"type":"text","text":"안녕"}]},"jsonrpc":"2.0","id":1}\n\n'
    assert parse_mcp_body(event)["result"]["content"][0]["text"] == "안녕"
    assert parse_mcp_body('{"jsonrpc":"2.0","id":1,"result":{}}')["result"] == {}
    with pytest.raises(BackendError):
        parse_mcp_body("event: ping\n\n")


def test_backend_sends_jsonrpc_and_raises_on_error():
    seen = {}

    def post(url, data, headers, timeout):
        seen.update(url=url, body=json.loads(data), headers=headers)
        return 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"content":[]}}\n\n'

    backend = QmdMcpBackend("http://localhost:8181/", post=post)
    assert backend.call_tool("query", {"searches": []}) == {"content": []}
    assert seen["url"] == "http://localhost:8181/mcp"
    assert seen["body"]["method"] == "tools/call" and seen["body"]["params"] == {"name": "query", "arguments": {"searches": []}}
    assert "text/event-stream" in seen["headers"]["Accept"]  # qmd가 요구하는 Accept

    error_backend = QmdMcpBackend(post=lambda *a: '{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"boom"}}')
    with pytest.raises(BackendError):
        error_backend.call_tool("get", {})


# ---- 도구 노출 범위 ---------------------------------------------------------------------------------------------
READ_TOOLS = {"search_vault", "query", "get", "multi_get", "status", "ls", "collection_list", "collection_show", "context_list"}
ADMIN_TOOLS = {
    "update", "embed", "cleanup", "collection_add", "collection_remove", "collection_rename",
    "collection_include", "collection_exclude", "context_add", "context_remove",
}  # fmt: skip


def names(registry, role):
    return {t.name for t in registry.visible(role)}


def test_visibility_by_role_and_global_switch(tmp_path):
    registry, _, _ = make_registry(tmp_path)
    assert names(registry, "reader") == READ_TOOLS
    assert names(registry, "admin") == READ_TOOLS | ADMIN_TOOLS

    off, _, _ = make_registry(tmp_path, allow_admin_tools=False)
    assert names(off, "admin") == READ_TOOLS  # 전역 스위치가 꺼져 있으면 admin 키도 관리 도구를 못 본다
    with pytest.raises(UnknownTool):
        off.call("update", {}, "admin")
    with pytest.raises(UnknownTool):
        registry.call("update", {}, "reader")  # 있는지도 알려 주지 않는다(없는 도구와 같은 오류)


def test_dangerous_update_cmd_is_not_exposed_anywhere(tmp_path):
    registry, _, _ = make_registry(tmp_path)
    everything = names(registry, "admin")
    assert not any("update_cmd" in n or "update-cmd" in n or n in {"init", "trust", "bench", "skills"} for n in everything)
    for tool in registry.visible("admin"):
        assert "update-cmd" not in json.dumps(tool.listing())


# ---- 검색 인자 통제 ---------------------------------------------------------------------------------------------
def test_search_vault_forces_vec_and_no_rerank(tmp_path):
    registry, backend, _ = make_registry(tmp_path)

    registry.call("search_vault", {"query": "FINT RAG 경험", "limit": 5, "collections": ["vault"]}, "reader")

    name, args = backend.calls[0]
    assert name == "query"
    assert args == {"searches": [{"type": "vec", "query": "FINT RAG 경험"}], "limit": 5, "rerank": False, "collections": ["vault"]}


def test_query_turns_plain_string_into_vec_and_forces_rerank_off(tmp_path):
    registry, backend, _ = make_registry(tmp_path)

    registry.call("query", {"query": "문장", "rerank": True, "limit": 5}, "reader")  # 모델이 리랭킹을 켜려 해도
    registry.call("query", {"searches": [{"query": "a"}, {"type": "lex", "query": "b"}], "candidateLimit": 999, "minScore": 0.5}, "reader")

    assert backend.calls[0][1]["rerank"] is False and backend.calls[0][1]["searches"] == [{"type": "vec", "query": "문장"}]
    second = backend.calls[1][1]
    assert second["searches"] == [{"type": "vec", "query": "a"}, {"type": "lex", "query": "b"}]  # 타입 생략은 기본 vec
    assert (second["candidateLimit"], second["minScore"]) == (100, 0.5)  # 후보 개수는 상한(100)으로 자른다
    with pytest.raises(InvalidArguments):
        registry.call("query", {"query": "x", "minScore": 5}, "reader")  # 스키마 범위(0~1)를 벗어나면 오류


def test_rerank_only_when_server_allows_and_limit_is_capped(tmp_path):
    registry, backend, _ = make_registry(tmp_path, allow_rerank=True, max_limit=8)
    registry.call("query", {"query": "x", "rerank": True}, "reader")
    registry.call("query", {"query": "x"}, "reader")

    assert backend.calls[0][1]["rerank"] is True and backend.calls[1][1]["rerank"] is False  # 요청하지 않으면 끄고, 허용 + 요청이면 켠다
    assert backend.calls[1][1]["limit"] == 8  # 기본 개수는 min(10, 상한)
    with pytest.raises(InvalidArguments):
        registry.call("query", {"query": "x", "limit": 50}, "reader")  # 상한 초과는 오류로 알려 준다


def test_unknown_or_missing_arguments_are_errors_not_silently_ignored(tmp_path):
    registry, backend, _ = make_registry(tmp_path)

    with pytest.raises(InvalidArguments) as unknown:
        registry.call("query", {"query": "x", "collection": "vault"}, "reader")  # qmd는 단수형을 조용히 무시한다
    assert "collection" in str(unknown.value) and "collections" in str(unknown.value)
    for bad in ({}, {"searches": []}, {"searches": [{"type": "sql", "query": "x"}]}, {"searches": [{"query": "x", "bogus": 1}]}, {"query": "x", "limit": True}):
        with pytest.raises(InvalidArguments):
            registry.call("query", bad, "reader")
    with pytest.raises(InvalidArguments):
        registry.call("get", {}, "reader")  # 필수 인자 없음
    assert backend.calls == []  # 잘못된 호출은 qmd까지 가지 않는다


def test_get_and_multi_get_are_capped(tmp_path):
    registry, backend, _ = make_registry(tmp_path)
    registry.call("get", {"file": "#abc", "maxLines": 99999}, "reader")
    registry.call("multi_get", {"pattern": "a/*.md", "maxBytes": 10**9, "maxLines": 99999}, "reader")
    registry.call("status", {}, "reader")

    assert backend.calls[0][1]["maxLines"] == 2000
    assert (backend.calls[1][1]["maxBytes"], backend.calls[1][1]["maxLines"]) == (200000, 2000)
    assert backend.calls[2] == ("status", {})


def test_backend_failure_becomes_tool_error_result(tmp_path):
    class Down:
        def call_tool(self, name, arguments):
            raise BackendError("qmd에 닿지 못했다")

    registry, _, _ = make_registry(tmp_path, backend=Down())
    result = registry.call("status", {}, "reader")
    assert result["isError"] is True and "닿지 못했다" in result["content"][0]["text"]


# ---- CLI 도구: 값 검사 ------------------------------------------------------------------------------------------
def test_cli_read_tools_build_argv_without_shell_and_return_output(tmp_path):
    registry, _, cli = make_registry(tmp_path)

    registry.call("ls", {}, "reader")
    registry.call("ls", {"path": "vault/04_project"}, "reader")
    registry.call("collection_list", {}, "reader")
    out = registry.call("collection_show", {"name": "vault"}, "reader")
    registry.call("context_list", {}, "reader")

    assert [c[0] for c in cli.calls] == [["ls"], ["ls", "vault/04_project"], ["collection", "list"], ["collection", "show", "vault"], ["context", "list"]]
    assert out["content"][0]["text"] == "ok" and "isError" not in out


@pytest.mark.parametrize(
    "tool,args",
    [
        ("collection_show", {"name": "--help"}),  # 옵션 주입
        ("collection_show", {"name": "a b"}),
        ("collection_show", {"name": "x;rm -rf /"}),
        ("ls", {"path": "-v"}),
        ("ls", {"path": "../etc"}),
        ("ls", {"path": "/etc"}),
        ("ls", {"path": "a\nb"}),
    ],
)
def test_cli_values_that_look_like_options_or_escape_are_rejected(tmp_path, tool, args):
    registry, _, cli = make_registry(tmp_path)
    with pytest.raises(InvalidArguments):
        registry.call(tool, args, "reader" if tool in READ_TOOLS else "admin")
    assert cli.calls == []


def test_cli_nonzero_exit_is_tool_error_and_long_output_is_cut(tmp_path):
    registry, _, _ = make_registry(tmp_path, cli=FakeCli(CliResult(2, "o", "나쁨")))
    failed = registry.call("collection_list", {}, "reader")
    assert failed["isError"] is True and "종료 코드 2" in failed["content"][0]["text"]

    registry, _, _ = make_registry(tmp_path, cli=FakeCli(CliResult(0, "가" * 30000, "")))
    text = registry.call("collection_list", {}, "reader")["content"][0]["text"]
    assert len(text) < 21000 and "잘랐다" in text


# ---- 관리 도구 --------------------------------------------------------------------------------------------------
def test_admin_tools_build_argv_and_use_admin_timeout(tmp_path):
    registry, _, cli = make_registry(tmp_path, admin_timeout_sec=999)
    root = tmp_path / "root"
    (root / "notes").mkdir()

    registry.call("update", {}, "admin")
    registry.call("embed", {"force": True}, "admin")
    registry.call("embed", {}, "admin")
    registry.call("cleanup", {}, "admin")
    registry.call("collection_add", {"path": str(root / "notes"), "name": "notes", "mask": "**/*.md,CO - *.md"}, "admin")
    registry.call("collection_rename", {"old": "a", "new": "b"}, "admin")
    registry.call("collection_remove", {"name": "b"}, "admin")
    registry.call("collection_include", {"name": "notes"}, "admin")
    registry.call("collection_exclude", {"name": "notes"}, "admin")
    registry.call("context_add", {"target": "qmd://notes/work", "text": "업무 메모"}, "admin")
    registry.call("context_add", {"target": "/", "text": "전체 설명"}, "admin")
    registry.call("context_remove", {"target": "qmd://notes/work"}, "admin")

    argvs = [c[0] for c in cli.calls]
    assert argvs[:4] == [["update"], ["embed", "-f"], ["embed"], ["cleanup"]]
    assert argvs[4] == ["collection", "add", os.path.realpath(root / "notes"), "--name", "notes", "--mask", "**/*.md,CO - *.md"]
    assert argvs[5:9] == [["collection", "rename", "a", "b"], ["collection", "remove", "b"], ["collection", "include", "notes"], ["collection", "exclude", "notes"]]
    assert argvs[9:] == [["context", "add", "qmd://notes/work", "업무 메모"], ["context", "add", "/", "전체 설명"], ["context", "rm", "qmd://notes/work"]]
    assert all(c[1] == 999 for c in cli.calls)  # 관리 작업은 긴 제한 시간


def test_collection_add_path_must_be_inside_allowed_roots_even_via_symlink(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "ok").mkdir()
    (root / "escape").symlink_to(outside)  # 루트 안에 있는 것처럼 보이지만 실제로는 밖
    roots = (str(root),)

    assert check_collection_root(str(root / "ok"), roots) == os.path.realpath(root / "ok")
    assert check_collection_root(str(root), roots) == os.path.realpath(root)
    for bad in (str(outside), str(root / "escape"), str(root / "missing"), "/etc", str(tmp_path / "root-evil")):
        with pytest.raises(InvalidArguments):
            check_collection_root(bad, roots)
    with pytest.raises(InvalidArguments):
        check_collection_root(str(root / "ok"), ())  # 허용 루트가 없으면 항상 거부


@pytest.mark.parametrize(
    "tool,args",
    [
        ("context_add", {"target": "notes", "text": "x"}),  # qmd:// 형식이 아님
        ("context_add", {"target": "qmd://notes", "text": "--help"}),
        ("context_remove", {"target": "--all"}),
        ("collection_add", {"path": "/etc", "name": "etc"}),
        ("collection_add", {"path": "ROOT/notes", "name": "n", "mask": "-r"}),
        ("collection_add", {"path": "ROOT/notes", "name": "n", "mask": "../../**"}),
        ("collection_rename", {"old": "a", "new": "--x"}),
    ],
)
def test_admin_values_are_validated(tmp_path, tool, args):
    registry, _, cli = make_registry(tmp_path)
    root = tmp_path / "root"
    (root / "notes").mkdir()
    args = {k: (v.replace("ROOT", str(root)) if isinstance(v, str) else v) for k, v in args.items()}
    with pytest.raises(InvalidArguments):
        registry.call(tool, args, "admin")
    assert cli.calls == []


def test_only_one_admin_job_at_a_time(tmp_path):
    class Reentrant(FakeCli):
        def run(self, args, timeout):
            inner = registry.call("cleanup", {}, "admin")  # 실행 중에 다른 관리 작업이 들어오면
            self.inner = inner
            return super().run(args, timeout)

    registry, _, reentrant = make_registry(tmp_path, cli=Reentrant())
    registry.call("update", {}, "admin")

    assert reentrant.inner["isError"] is True and "실행 중" in reentrant.inner["content"][0]["text"]


# ---- MCP 프로토콜 -----------------------------------------------------------------------------------------------
def rpc(method, params=None, id=1):
    message = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def test_initialize_negotiates_version_and_lists_tools_per_role(tmp_path):
    registry, _, _ = make_registry(tmp_path)

    ok = handle_message(rpc("initialize", {"protocolVersion": "2025-06-18"}), registry, "reader")["result"]
    unsupported = handle_message(rpc("initialize", {"protocolVersion": "1999-01-01"}), registry, "reader")["result"]
    listing = handle_message(rpc("tools/list"), registry, "reader")["result"]["tools"]
    admin_listing = handle_message(rpc("tools/list"), registry, "admin")["result"]["tools"]

    assert ok["protocolVersion"] == "2025-06-18" and unsupported["protocolVersion"] == SUPPORTED_VERSIONS[0]
    assert ok["capabilities"] == {"tools": {"listChanged": False}} and ok["serverInfo"]["name"] == "qmd-proxy"
    assert {t["name"] for t in listing} == READ_TOOLS and {t["name"] for t in admin_listing} == READ_TOOLS | ADMIN_TOOLS
    assert all({"name", "description", "inputSchema"} <= set(t) for t in listing)


def test_tools_call_success_invalid_args_unknown_tool_and_audit(tmp_path):
    registry, backend, _ = make_registry(tmp_path)
    audited = []

    ok = handle_message(rpc("tools/call", {"name": "search_vault", "arguments": {"query": "x"}}), registry, "reader", audit=lambda *a: audited.append(a))
    bad = handle_message(rpc("tools/call", {"name": "search_vault", "arguments": {"qeury": "x"}}), registry, "reader")
    unknown = handle_message(rpc("tools/call", {"name": "update", "arguments": {}}), registry, "reader")

    assert ok["result"]["content"][0]["text"] == "fake query" and audited[0][:2] == ("search_vault", True)
    assert bad["result"]["isError"] is True and "인자 오류" in bad["result"]["content"][0]["text"]  # 모델이 읽고 고친다
    assert unknown["error"]["code"] == -32602 and len(backend.calls) == 1


def test_notifications_get_no_response_and_bad_requests_are_errors(tmp_path):
    registry, _, _ = make_registry(tmp_path)

    assert handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, registry, "reader") is None
    assert handle_message(rpc("ping"), registry, "reader")["result"] == {}
    assert handle_message(rpc("resources/list"), registry, "reader")["error"]["code"] == -32601
    assert handle_message("not a dict", registry, "reader")["error"]["code"] == -32600
    assert handle_message({"jsonrpc": "1.0", "id": 1, "method": "ping"}, registry, "reader")["error"]["code"] == -32600
