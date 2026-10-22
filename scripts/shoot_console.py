"""콘솔 화면을 장면별로 찍는다(발표 자료용). headless Chrome을 디버깅 포트로 띄우고 장면마다 주소를 열어 캡처한다.

사용 (콘솔 서버가 켜져 있어야 한다, 레포 루트에서):
  uv run --extra dev python scripts/shoot_console.py --out logs/demo-day/shots \\
      "01_조사|item=hyundai_autoever_2026q4_backend&stage=research" "05_팝업|collect=cover_letter&form=collect.search"
장면은 "파일 이름|주소의 # 뒤" 모양이다(콘솔이 #item·#collect·stage·doc·form을 읽는다). 값에 한글·공백이 있어도 된다.
_로 시작하는 값은 콘솔에 넘기지 않고 이 스크립트가 쓴다:
  _clip=<CSS 선택자>  그 영역만 잘라 찍는다(예: #viewer 결과 문서 칸, #dlg 팝업, #right 작업·대화 칸)
  _chat=<메시지>      채팅 칸에 보내고 답이 올 때까지 기다린 뒤 찍는다(Bedrock을 부른다 — 콘솔이 --chat으로 켜져 있어야 한다)
--scale 2로 찍으면 잘라 낸 영역도 글자가 선명하다.

왜 Chrome의 --screenshot 옵션을 쓰지 않나: 그 옵션은 틀(iframe) 안의 HTML 문서가 그려지기 전에 찍어서 HTML 결과 문서가
빈 화면으로 나왔다(2026-10-21 확인: 같은 페이지를 디버깅 연결로 찍으면 보인다). 결과는 git에 올리지 않는 곳에 둔다(자소서 본문이 보인다).
"""
import argparse
import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

PORT = 9333


def scene_url(base: str, spec: str) -> tuple[str, dict[str, str]]:
    """콘솔 주소와, 이 스크립트가 쓸 _ 값들."""
    params = urllib.parse.parse_qsl(spec, keep_blank_values=True)
    extra = {k: v for k, v in params if k.startswith("_")}
    rest = [(k, v) for k, v in params if not k.startswith("_")]
    return f"{base}/#" + urllib.parse.urlencode(rest, quote_via=urllib.parse.quote), extra


async def shoot(ws_url: str, shots: list[tuple[Path, str, dict[str, str]]], wait: float, size: tuple[int, int], scale: float) -> None:
    import websockets

    async with websockets.connect(ws_url, max_size=None) as ws:
        counter = 0

        async def call(method: str, params: dict | None = None) -> dict:
            nonlocal counter
            counter += 1
            await ws.send(json.dumps({"id": counter, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == counter:
                    return msg.get("result", {})

        async def js(expr: str):
            return (await call("Runtime.evaluate", {"expression": expr, "returnByValue": True}))["result"].get("value")

        await call("Emulation.setDeviceMetricsOverride", {"width": size[0], "height": size[1], "deviceScaleFactor": scale, "mobile": False})
        for out, url, extra in shots:
            await call("Page.navigate", {"url": "about:blank"})  # 같은 페이지의 # 만 바꾸면 다시 읽지 않아서 비우고 연다
            await call("Page.navigate", {"url": url})
            await asyncio.sleep(wait)
            if "_chat" in extra:
                before = await js("document.querySelectorAll('#msgs .msg').length")
                await js(f"document.getElementById('chatInput').value = {json.dumps(extra['_chat'])}; document.getElementById('chatForm').requestSubmit()")
                for _ in range(300):  # 답이 오면 마지막 말풍선의 "…"가 바뀐다. 최대 약 5분
                    await asyncio.sleep(1)
                    if await js(f"(() => {{ const m = document.querySelectorAll('#msgs .msg'); return m.length > {before} + 1 && m[m.length - 1].textContent !== '…'; }})()"):
                        break
                else:
                    print("채팅 답을 기다리다 멈춤(5분)")
                await asyncio.sleep(3)  # 답 뒤에 작업 목록을 새로 읽는다
            params = {"format": "png"}
            if "_clip" in extra:
                rect = await js(f"JSON.stringify(document.querySelector({json.dumps(extra['_clip'])}).getBoundingClientRect())")
                r = json.loads(rect)
                height = min(r["y"] + r["height"], size[1]) - r["y"]  # 문서 칸은 문서 길이만큼 길다. 화면 밖은 그려지지 않아 빈칸이 되므로 화면 안까지만
                params |= {"clip": {"x": r["x"], "y": r["y"], "width": r["width"], "height": height, "scale": 1}}
            data = (await call("Page.captureScreenshot", params))["data"]
            out.write_bytes(base64.b64decode(data))
            print(f"찍음: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenes", nargs="+", help='"파일 이름|주소의 # 뒤"')
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8300")
    parser.add_argument("--size", default="1500,900", help="창 크기 너비,높이")
    parser.add_argument("--wait", type=float, default=4.0, help="장면마다 기다릴 초(문서·팝업이 그려질 시간)")
    parser.add_argument("--scale", type=float, default=1.0, help="화면 배율(2면 글자가 선명하고 파일이 커진다)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    args.out.mkdir(parents=True, exist_ok=True)
    width, height = (int(v) for v in args.size.split(","))
    shots = []
    for spec in args.scenes:
        name, _, query = spec.partition("|")
        shots.append((args.out / f"{name}.png", *scene_url(args.base, query)))
    with tempfile.TemporaryDirectory() as profile:
        chrome = subprocess.Popen(["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                                   f"--remote-debugging-port={PORT}", "--remote-debugging-address=127.0.0.1",
                                   f"--window-size={args.size}", f"--user-data-dir={profile}", "about:blank"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                sys.exit("Chrome 디버깅 포트에 닿지 못했다")
            ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
            asyncio.run(shoot(ws_url, shots, args.wait, (width, height), args.scale))
        finally:
            chrome.terminate()
            chrome.wait(timeout=10)  # 다 꺼지기 전에 임시 프로필 폴더를 지우면 "Directory not empty"로 실패한다


if __name__ == "__main__":
    main()
