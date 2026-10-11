"""AgentCore Runtime 안에서 Tailscale이 도는지 확인하는 시험용 프로그램. 표준 라이브러리만 쓴다.

무엇을 확인하나 (docs/agentcore-research.md, 2026-10-06)
  사용자 서버(vault·qmd 프록시)는 인터넷에 열려 있지 않고 Tailscale 사설 통로(tailnet)로만 닿는다. AgentCore Gateway는 Tailscale을
  설치할 수 없으므로, Runtime에 호스팅한 "브리지"가 tailnet으로 서버에 닿는 구조를 생각한다. 그 전제 — **Runtime 컨테이너(microVM) 안에서
  Tailscale이 도는가** — 가 문서로 확인되지 않아서 이 프로그램으로 직접 시험한다.

동작
  AgentCore Runtime의 HTTP 규약(포트 8080, `GET /ping`, `POST /invocations`)을 직접 구현한다. `POST /invocations`가 오면 아래 단계를 차례로 실행하고
  단계별 성공 여부·걸린 시간·세부 내용을 JSON으로 돌려준다. 한 단계가 실패해도 나머지는 계속 시도한다(어디서 막히는지 보려는 것).
    env              OS·사용자·쓰기 가능한 곳·`/dev/net/tun` 유무·Tailscale 바이너리
    internet         Tailscale 제어 서버(HTTPS)와 DNS가 닿는가 (외부 인터넷 연결 확인)
    tailscaled_start `tailscaled --tun=userspace-networking`(장치 없이 SOCKS5 프록시로 동작) 기동
    tailscale_up     인증 키로 tailnet 가입 (키는 환경변수 TS_AUTHKEY, 결과에 남기지 않는다)
    tailscale_status 가입 상태·tailnet 주소·피어 수
    tailscale_ping   서버(PROBE_TARGET의 호스트)에 tailnet으로 닿는가
    socks_http       SOCKS5 프록시를 거쳐 `http://PROBE_TARGET/health`를 호출
    tailscaled_log   tailscaled 로그 끝부분(비밀 값은 가린다)

환경변수: TS_AUTHKEY(필수, 태그가 붙은 임시 키), PROBE_TARGET(예: 100.64.0.1:8282), PROBE_HOSTNAME(기본 agentcore-probe),
          TS_TAGS(선택, 예: tag:agentcore-probe), PROBE_BIN_DIR·PROBE_STATE_DIR(시험용).
이 프로그램은 서버를 제어하지 않는다. 읽기 전용 확인(`/health`)만 한다.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN_DIR = Path(os.environ.get("PROBE_BIN_DIR", HERE / "bin"))
STATE_DIR = Path(os.environ.get("PROBE_STATE_DIR", "/tmp/ts-probe"))
SOCKS_HOST, SOCKS_PORT = "127.0.0.1", 1055
KEY_ENV = "TS_AUTHKEY"
STEP_NAMES = ("env", "internet", "tailscaled_start", "tailscale_up", "tailscale_status", "tailscale_ping", "socks_http", "tailscaled_log")

_busy = False
_tailscaled: subprocess.Popen | None = None


def _scrub(text: str) -> str:
    key = os.environ.get(KEY_ENV)
    if key:
        text = text.replace(key, "<TS_AUTHKEY>")
    return text


# ---- SOCKS5 (tailscaled의 프록시를 거쳐 접속) ---------------------------------------------------------------------
def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise OSError("SOCKS5 응답이 중간에 끊겼다")
        data += chunk
    return data


def socks5_http_get(host: str, port: int, path: str = "/", *, socks: tuple[str, int] = (SOCKS_HOST, SOCKS_PORT), timeout: float = 15) -> tuple[str, str]:
    """SOCKS5 프록시를 거쳐 `http://host:port/path`를 GET 한다. (상태 줄, 본문 앞부분)을 돌려준다."""
    with socket.create_connection(socks, timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(b"\x05\x01\x00")  # 버전 5, 인증 방식 1개, 인증 없음
        if _recv_exact(sock, 2) != b"\x05\x00":
            raise OSError("SOCKS5 프록시가 인증 없는 방식을 받지 않았다")
        try:
            target = b"\x01" + socket.inet_aton(host)  # IPv4 주소
        except OSError:
            name = host.encode("idna")
            target = b"\x03" + bytes([len(name)]) + name  # 도메인 이름
        sock.sendall(b"\x05\x01\x00" + target + struct.pack("!H", port))
        header = _recv_exact(sock, 4)
        if header[1] != 0:
            raise OSError(f"SOCKS5 연결 거절(코드 {header[1]}): tailnet에 닿지 못했거나 ACL이 막았을 수 있다")
        atyp = header[3]
        skip = 4 if atyp == 1 else 16 if atyp == 4 else _recv_exact(sock, 1)[0]
        _recv_exact(sock, skip + 2)  # 바인드 주소와 포트는 버린다
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode())
        body = b""
        while len(body) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            body += chunk
    head, _, rest = body.decode("utf-8", errors="replace").partition("\r\n\r\n")
    return head.splitlines()[0] if head else "(응답 없음)", rest[:300]


# ---- 단계 -----------------------------------------------------------------------------------------------------
def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603 (shell 없이 인자 목록)


def _tailscale_cmd(*args: str) -> list[str]:
    return [str(BIN_DIR / "tailscale"), f"--socket={STATE_DIR / 'tailscaled.sock'}", *args]


def _target() -> tuple[str, int]:
    target = os.environ.get("PROBE_TARGET", "")
    if not target:
        raise RuntimeError("PROBE_TARGET이 설정돼 있지 않다(예: 100.64.0.1:8282)")
    host, _, port = target.rpartition(":")
    return host, int(port)


def step_env() -> dict:
    meminfo = Path("/proc/meminfo")
    return {
        "uname": " ".join(os.uname()),
        "uid": os.getuid(),
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
        "tmp_writable": os.access("/tmp", os.W_OK),
        "dev_net_tun": Path("/dev/net/tun").exists(),
        "bin_dir": str(BIN_DIR),
        "binaries": {n: {"exists": (BIN_DIR / n).exists(), "executable": os.access(BIN_DIR / n, os.X_OK)} for n in ("tailscale", "tailscaled")},
        "mem": meminfo.read_text().splitlines()[0] if meminfo.exists() else None,
        "auth_key_set": bool(os.environ.get(KEY_ENV)),
        "target": os.environ.get("PROBE_TARGET"),
    }


def step_internet() -> dict:
    out: dict = {}
    host = "controlplane.tailscale.com"
    out["dns"] = socket.gethostbyname(host)
    with urllib.request.urlopen(f"https://{host}/key?v=71", timeout=15) as response:  # noqa: S310 (고정 주소)
        out["https_status"] = response.status
    return out


def step_tailscaled_start() -> dict:
    global _tailscaled
    if _tailscaled is not None and _tailscaled.poll() is None:
        return {"already_running": True, "pid": _tailscaled.pid}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(STATE_DIR / "tailscaled.log", "ab")  # noqa: SIM115 (프로세스가 계속 쓴다)
    _tailscaled = subprocess.Popen(  # noqa: S603
        [
            str(BIN_DIR / "tailscaled"),
            "--tun=userspace-networking",
            f"--socks5-server={SOCKS_HOST}:{SOCKS_PORT}",
            f"--statedir={STATE_DIR}",
            f"--socket={STATE_DIR / 'tailscaled.sock'}",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(50):
        if (STATE_DIR / "tailscaled.sock").exists():
            return {"pid": _tailscaled.pid, "socket_ready": True}
        if _tailscaled.poll() is not None:
            raise RuntimeError(f"tailscaled가 바로 종료됐다(코드 {_tailscaled.returncode}) — tailscaled_log 단계를 본다")
        time.sleep(0.2)
    raise RuntimeError("tailscaled 소켓이 10초 안에 만들어지지 않았다")


def step_tailscale_up() -> dict:
    key = os.environ.get(KEY_ENV)
    if not key:
        raise RuntimeError(f"{KEY_ENV} 환경변수가 없다")
    cmd = _tailscale_cmd("up", f"--authkey={key}", f"--hostname={os.environ.get('PROBE_HOSTNAME', 'agentcore-probe')}", "--accept-dns=false", "--timeout=60s")
    if os.environ.get("TS_TAGS"):
        cmd.append(f"--advertise-tags={os.environ['TS_TAGS']}")
    done = _run(cmd, 90)
    if done.returncode != 0:
        raise RuntimeError(_scrub(f"tailscale up 실패(코드 {done.returncode}): {(done.stderr or done.stdout)[-400:]}"))
    return {"returncode": 0}


def step_tailscale_status() -> dict:
    done = _run(_tailscale_cmd("status", "--json"), 20)
    if done.returncode != 0:
        raise RuntimeError(_scrub(f"tailscale status 실패: {(done.stderr or done.stdout)[-300:]}"))
    status = json.loads(done.stdout)
    peers = status.get("Peer") or {}
    return {
        "backend_state": status.get("BackendState"),
        "self_hostname": (status.get("Self") or {}).get("HostName"),
        "self_ips": (status.get("Self") or {}).get("TailscaleIPs"),
        "health": status.get("Health"),
        "peer_count": len(peers),
        "peers_online": sum(1 for p in peers.values() if p.get("Online")),
    }


def step_tailscale_ping() -> dict:
    host, _ = _target()
    done = _run(_tailscale_cmd("ping", "--c=3", "--timeout=8s", host), 40)
    return {"returncode": done.returncode, "output": _scrub((done.stdout or done.stderr)[-300:])}


def step_socks_http() -> dict:
    host, port = _target()
    status_line, body = socks5_http_get(host, port, "/health")
    return {"status_line": status_line, "body": body}


def step_tailscaled_log() -> dict:
    path = STATE_DIR / "tailscaled.log"
    lines = path.read_text(errors="replace").splitlines()[-30:] if path.exists() else []
    return {"tail": [_scrub(line)[:240] for line in lines]}


STEPS = {
    "env": step_env,
    "internet": step_internet,
    "tailscaled_start": step_tailscaled_start,
    "tailscale_up": step_tailscale_up,
    "tailscale_status": step_tailscale_status,
    "tailscale_ping": step_tailscale_ping,
    "socks_http": step_socks_http,
    "tailscaled_log": step_tailscaled_log,
}
assert tuple(STEPS) == STEP_NAMES


def run_probe(only: list[str] | None = None) -> dict:
    """단계를 차례로 실행한다. 한 단계가 실패해도 다음으로 간다."""
    results = []
    for name in only or STEP_NAMES:
        started = time.monotonic()
        try:
            ok, detail = True, STEPS[name]()
        except Exception as exc:  # noqa: BLE001 (실패 원인을 결과에 남기는 것이 목적)
            ok, detail = False, _scrub(f"{type(exc).__name__}: {exc}")
        results.append({"step": name, "ok": ok, "seconds": round(time.monotonic() - started, 2), "detail": detail})
    return {"all_ok": all(r["ok"] for r in results if r["step"] != "tailscaled_log"), "steps": results}


# ---- AgentCore Runtime HTTP 규약 ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] == "/ping":
            self._send(200, {"status": "HealthyBusy" if _busy else "Healthy"})  # 실행 중에도 /ping이 응답해야 세션이 끊기지 않는다
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        global _busy
        if self.path.split("?")[0] != "/invocations":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "JSON이 아니다"})
            return
        only = payload.get("steps")
        if only is not None and not (isinstance(only, list) and all(s in STEPS for s in only)):
            self._send(400, {"error": f"steps는 {list(STEPS)} 중의 목록이어야 한다"})
            return
        _busy = True
        try:
            self._send(200, run_probe(only))
        finally:
            _busy = False

    def log_message(self, fmt: str, *args) -> None:  # 요청 줄 로그는 CloudWatch에 남겨 두면 충분하다
        print(fmt % args, flush=True)


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    print(f"tailscale probe: 0.0.0.0:{port} (/ping, /invocations)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()  # 스레드 서버라 실행 중에도 /ping이 응답한다


if __name__ == "__main__":
    main()
