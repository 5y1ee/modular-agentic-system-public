"""deploy/tailscale_probe/main.py(Runtime 안에서 Tailscale이 도는지 확인하는 시험용 프로그램)의 로컬 검증. Tailscale·AWS 없이 돈다.

핵심: (1) SOCKS5로 IPv4·도메인 목적지에 접속해 HTTP를 읽는다, 거절 코드는 이유를 담은 오류 (2) 인증 키는 결과 어디에도 안 남는다
(3) 한 단계가 실패해도 나머지를 계속하고 실패 원인을 남긴다 (4) AgentCore Runtime 규약: /ping, /invocations, 잘못된 steps는 400
"""
import importlib.util
import json
import socket
import struct
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

PROBE_PATH = Path(__file__).resolve().parent.parent / "deploy" / "tailscale_probe" / "main.py"
spec = importlib.util.spec_from_file_location("tailscale_probe_main", PROBE_PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def fake_socks_server(reply_code: int = 0):
    """SOCKS5 인사·연결 요청을 받고 HTTP 요청에 한 번 응답한 뒤 닫는 가짜 서버. (주소, 포트), 받은 요청 기록을 돌려준다."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    seen: dict = {}

    def serve():
        conn, _ = listener.accept()
        with conn:
            seen["greeting"] = conn.recv(3)
            conn.sendall(b"\x05\x00")
            head = conn.recv(4)  # 버전, 명령, 예약, 주소 종류
            seen["atyp"] = head[3]
            if head[3] == 1:
                seen["host"] = socket.inet_ntoa(conn.recv(4))
            else:
                seen["host"] = conn.recv(conn.recv(1)[0]).decode()
            seen["port"] = struct.unpack("!H", conn.recv(2))[0]
            conn.sendall(bytes([5, reply_code, 0, 1]) + b"\x00\x00\x00\x00\x00\x00")
            if reply_code != 0:
                return
            seen["request"] = conn.recv(4096).decode()
            conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{"status":"ok"}')

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return listener.getsockname(), seen, thread


def test_socks5_get_to_ipv4_and_domain_targets():
    addr, seen, thread = fake_socks_server()
    status_line, body = probe.socks5_http_get("100.64.0.1", 8282, "/health", socks=addr)
    thread.join(timeout=5)

    assert (status_line, body) == ("HTTP/1.1 200 OK", '{"status":"ok"}')
    assert (seen["atyp"], seen["host"], seen["port"]) == (1, "100.64.0.1", 8282)
    assert seen["request"].startswith("GET /health HTTP/1.1") and "Host: 100.64.0.1:8282" in seen["request"]

    addr, seen, thread = fake_socks_server()
    probe.socks5_http_get("server-host.example.ts.net", 8282, socks=addr)
    thread.join(timeout=5)
    assert (seen["atyp"], seen["host"]) == (3, "server-host.example.ts.net")  # 도메인은 이름 그대로 프록시에 맡긴다


def test_socks5_rejection_has_actionable_message():
    addr, _, thread = fake_socks_server(reply_code=2)  # 규칙에 의해 허용되지 않음(ACL 등)
    with pytest.raises(OSError, match="ACL"):
        probe.socks5_http_get("100.64.0.1", 8282, socks=addr)
    thread.join(timeout=5)


def test_failed_steps_continue_and_auth_key_never_appears(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "BIN_DIR", tmp_path / "없는-폴더")
    monkeypatch.setattr(probe, "STATE_DIR", tmp_path / "state")
    monkeypatch.setenv("TS_AUTHKEY", "tskey-SECRET-123")
    monkeypatch.delenv("PROBE_TARGET", raising=False)

    result = probe.run_probe(["env", "tailscaled_start", "tailscale_up", "socks_http", "tailscaled_log"])

    by_step = {s["step"]: s for s in result["steps"]}
    assert result["all_ok"] is False and [s["step"] for s in result["steps"]][-1] == "tailscaled_log"  # 실패해도 끝까지 간다
    assert by_step["env"]["ok"] is True and by_step["env"]["detail"]["auth_key_set"] is True
    assert by_step["tailscaled_start"]["ok"] is False and "없는-폴더" in by_step["tailscaled_start"]["detail"]  # 어디서 막혔는지 남는다
    assert "PROBE_TARGET" in by_step["socks_http"]["detail"]
    assert "tskey-SECRET-123" not in json.dumps(result, ensure_ascii=False)  # 키는 결과에 없다


def test_scrub_hides_key_in_error_text(monkeypatch):
    monkeypatch.setenv("TS_AUTHKEY", "tskey-SECRET-123")
    assert probe._scrub("tailscale up --authkey=tskey-SECRET-123 실패") == "tailscale up --authkey=<TS_AUTHKEY> 실패"


def test_runtime_http_contract_ping_invocations_and_bad_steps(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "BIN_DIR", tmp_path)
    monkeypatch.setattr(probe, "STATE_DIR", tmp_path / "state")
    server = ThreadingHTTPServer(("127.0.0.1", 0), probe.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/ping", timeout=5) as r:
            assert json.loads(r.read()) == {"status": "Healthy"}
        ok = urllib.request.Request(f"{base}/invocations", data=b'{"steps":["env"]}', method="POST")
        with urllib.request.urlopen(ok, timeout=10) as r:
            body = json.loads(r.read())
        assert body["all_ok"] is True and body["steps"][0]["step"] == "env"
        bad = urllib.request.Request(f"{base}/invocations", data=b'{"steps":["rm -rf /"]}', method="POST")
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(bad, timeout=5)
        assert error.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"{base}/nope", timeout=5)
        assert missing.value.code == 404
    finally:
        server.shutdown()
