"""파이프라인 콘솔(웹 화면)을 켠다.

사용 (레포 루트에서):
  AWS_PROFILE=user uv run --extra strands --extra sources --extra proxy python scripts/run_console.py [--port 8300] [--chat]

화면: http://127.0.0.1:8300 . 다른 기기에서 볼 때는 SSH 포트 포워딩(`ssh -L 8300:127.0.0.1:8300 서버`)으로 연다.
인증이 없어서 127.0.0.1에만 묶는다(결과 문서에 자소서 본문이 있다).
--chat을 주면 채팅 에이전트(Strands + Bedrock)를 켠다. 말을 걸 때마다 Bedrock 호출이 생긴다.
버튼·채팅으로 만든 작업은 화면에서 [실행]을 눌러야 시작되고, 그때 해당 단계의 비용이 든다(실행 환경의 AWS_PROFILE을 그대로 쓴다).
구조는 src/modular_agentic/services/console/__init__.py.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8300)
    parser.add_argument("--chat", action="store_true", help="채팅 에이전트를 켠다(Bedrock 호출)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    import os

    import uvicorn

    from modular_agentic.services.console.jobs import JobRunner
    from modular_agentic.services.console.server import create_app

    os.chdir(ROOT)  # 단계 폴더·스크립트 경로가 레포 루트 기준이다
    runner = JobRunner(cwd=ROOT)
    chat = None
    if args.chat:
        from modular_agentic.engines.strands.console_agent import ConsoleChat

        chat = ConsoleChat(runner)
    print(f"콘솔: http://127.0.0.1:{args.port} (채팅 {'켜짐' if chat else '꺼짐'})")
    uvicorn.run(create_app(runner, chat), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
