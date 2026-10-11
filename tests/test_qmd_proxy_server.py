"""qmd MCP 프록시의 HTTP 서버(인증·Origin·상태 코드). Starlette TestClient로 qmd 없이 검증한다.

핵심: (1) 키가 없거나 틀리면 401, Bearer와 X-Api-Key 둘 다 된다 (2) 허용되지 않은 Origin은 403, Origin이 없는 요청은 통과
(3) /health는 인증 없이 되지만 데이터가 없다 (4) GET·DELETE /mcp는 405 (5) 알림은 202 (6) 키 이름이 감사 로그에 남고 키 자체는 안 남는다
(7) 가짜 qmd까지 이어지는 종단 경로 (8) 공식 MCP 클라이언트 SDK가 이 서버와 대화한다(SDK가 있을 때만).
"""
import logging
import socket
import threading
import time

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402
from test_qmd_proxy import FakeBackend, FakeCli, make_config  # noqa: E402

from modular_agentic.services.qmd_proxy.registry import build_registry  # noqa: E402
from modular_agentic.services.qmd_proxy.server import create_app  # noqa: E402


@pytest.fixture
def served(tmp_path):
    backend, cli = FakeBackend(), FakeCli()
    config = make_config(tmp_path, allowed_origins=("https://ok.example",))
    app = create_app(config, build_registry(config, backend, cli))
    return TestClient(app), backend, cli


def post(client, body, key="reader-key", **headers):
    h = {"Authorization": f"Bearer {key}"} if key else {}
    h.update(headers)
    return client.post("/mcp", json=body, headers=h)


PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def test_auth_bearer_and_api_key_header_and_rejections(served):
    client, _, _ = served

    assert post(client, PING).status_code == 200
    assert client.post("/mcp", json=PING, headers={"X-Api-Key": "reader-key"}).status_code == 200
    unauthorized = post(client, PING, key=None)
    assert unauthorized.status_code == 401 and unauthorized.headers["www-authenticate"] == "Bearer"
    assert post(client, PING, key="wrong-key").status_code == 401
    assert client.post("/mcp", json=PING, headers={"Authorization": "Basic abc"}).status_code == 401


def test_origin_check_only_applies_when_header_present(served):
    client, _, _ = served

    assert post(client, PING, Origin="https://evil.example").status_code == 403  # 브라우저 경유(DNS 리바인딩) 차단
    assert post(client, PING, Origin="https://ok.example").status_code == 200
    assert post(client, PING).status_code == 200  # Origin이 없는 MCP 클라이언트·curl은 영향 없다


def test_health_open_methods_and_bad_bodies(served):
    client, _, _ = served

    health = client.get("/health")
    assert health.status_code == 200 and health.json() == {"status": "ok"}  # 인증 없이 되지만 데이터가 없다
    assert client.get("/mcp", headers={"Authorization": "Bearer reader-key"}).status_code == 405
    assert client.delete("/mcp", headers={"Authorization": "Bearer reader-key"}).status_code == 405
    bad = client.post("/mcp", content=b"{not json", headers={"Authorization": "Bearer reader-key"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == -32700
    assert post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202  # 알림은 본문 없이 202


def test_batch_returns_only_responses_to_requests(served):
    client, _, _ = served

    both = post(client, [PING, {"jsonrpc": "2.0", "method": "notifications/initialized"}, {**PING, "id": 2}])
    only_notifications = post(client, [{"jsonrpc": "2.0", "method": "notifications/initialized"}])

    assert [r["id"] for r in both.json()] == [1, 2]
    assert only_notifications.status_code == 202


def test_end_to_end_search_through_fake_qmd_and_role_gating(served):
    client, backend, cli = served
    call = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "search_vault", "arguments": {"query": "FINT"}}}

    ok = post(client, call).json()
    admin_call = {**call, "params": {"name": "update", "arguments": {}}}
    denied = post(client, admin_call).json()  # reader 키로는 관리 도구가 없는 것과 같다
    allowed = post(client, admin_call, key="admin-key").json()

    assert ok["result"]["content"][0]["text"] == "fake query" and backend.calls[0][1]["rerank"] is False
    assert denied["error"]["code"] == -32602 and allowed["result"]["content"][0]["text"] == "ok"
    assert cli.calls[0][0] == ["update"]


def test_audit_log_has_key_name_but_never_the_key(served, caplog):
    client, _, _ = served
    caplog.set_level(logging.INFO, logger="qmd_proxy")

    post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "status", "arguments": {}}})

    text = " ".join(r.getMessage() for r in caplog.records)
    assert "tool=status" in text and "key=r" in text and "reader-key" not in text


# ---- 공식 MCP 클라이언트 SDK와의 호환 ---------------------------------------------------------------------------
def test_official_mcp_client_can_talk_to_the_proxy(tmp_path):
    pytest.importorskip("uvicorn")
    mcp = pytest.importorskip("mcp")
    anyio = pytest.importorskip("anyio")
    import contextlib

    try:  # mcp 2.x: 헤더를 가진 http 클라이언트를 만들어 넘긴다
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        @contextlib.asynccontextmanager
        async def open_streams(url, headers):
            async with create_mcp_http_client(headers=headers) as http:
                async with streamable_http_client(url, http_client=http) as streams:
                    yield streams

    except ImportError:
        try:  # mcp 1.x
            from mcp.client.streamable_http import streamablehttp_client

            open_streams = lambda url, headers: streamablehttp_client(url, headers=headers)  # noqa: E731
        except ImportError:
            pytest.skip(f"이 mcp 버전({getattr(mcp, '__version__', '?')})에서 streamable HTTP 클라이언트를 찾지 못했다")
    import uvicorn
    from mcp import ClientSession

    backend, cli = FakeBackend(), FakeCli()
    config = make_config(tmp_path)
    app = create_app(config, build_registry(config, backend, cli))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started

    async def run():
        async with open_streams(f"http://127.0.0.1:{port}/mcp", {"Authorization": "Bearer reader-key"}) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("search_vault", {"query": "FINT RAG"})
                return {t.name for t in tools.tools}, result

    try:
        names, result = anyio.run(run)
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    assert {"search_vault", "query", "get"} <= names and "update" not in names
    assert result.content[0].text == "fake query" and backend.calls[0][1]["rerank"] is False
