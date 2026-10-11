"""qmd MCP 프록시를 켜거나 API 키를 만든다.

사용
  # 새 키 만들기: 평문은 이때 한 번만 보인다. 설정 파일에는 해시만 넣는다.
  uv run python scripts/run_qmd_proxy.py make-key --name gateway --role reader

  # 서버 켜기 (qmd 상시 서버가 떠 있어야 한다: scripts/with_qmd_server.sh 또는 `qmd mcp --http`)
  uv run --extra proxy python scripts/run_qmd_proxy.py serve --config proxy.json [--port 8282] [--qmd-url http://localhost:8181]

설정 파일(proxy.json)에는 키의 해시가 들어 있어 git에 올리지 않는다(.gitignore 확인). 형식은 services/qmd_proxy/config.py의 docstring.
기본으로 127.0.0.1에만 묶는다. 밖에서 닿게 하려면 --host를 바꾸되 인증이 있는 HTTPS 앞단(리버스 프록시)을 두는 것을 권한다.
이 프록시가 qmd 서버(인증 없음)를 대신해 인증·도구 범위·리랭킹 끄기를 맡는다. 서버가 열려 있는 동안 vault가 열려 있다.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from modular_agentic.services.qmd_proxy.config import ROLES, hash_key, load_config, new_key


def make_key(args: argparse.Namespace) -> None:
    key = new_key()
    print(f"API 키(지금 한 번만 보인다, 저장하지 않는다):\n  {key}\n")
    print("설정 파일의 keys 배열에 넣을 항목:")
    print(json.dumps({"name": args.name, "sha256": hash_key(key), "role": args.role}, ensure_ascii=False, indent=2))


def serve(args: argparse.Namespace) -> None:
    import uvicorn

    from modular_agentic.services.qmd_proxy.backend import QmdCli, QmdMcpBackend
    from modular_agentic.services.qmd_proxy.registry import build_registry
    from modular_agentic.services.qmd_proxy.server import create_app

    config = load_config(Path(args.config))
    if not config.keys:
        sys.exit("설정에 키가 없다. make-key로 만들어 keys 배열에 넣는다.")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"주의: {args.host}에 묶는다. 인증은 API 키뿐이고 HTTPS가 아니다. 앞단에 TLS를 두는 것을 권한다.", file=sys.stderr)
    registry = build_registry(config, QmdMcpBackend(args.qmd_url), QmdCli(force_cpu=config.force_cpu))
    admin = "켜짐" if config.allow_admin_tools else "꺼짐"
    print(f"qmd 프록시: http://{args.host}:{args.port}/mcp (키 {len(config.keys)}개, 관리 도구 {admin}, qmd {args.qmd_url})")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    uvicorn.run(create_app(config, registry), host=args.host, port=args.port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    mk = sub.add_parser("make-key", help="새 API 키와 설정에 넣을 해시를 만든다")
    mk.add_argument("--name", required=True, help="키 이름(로그에 남는다)")
    mk.add_argument("--role", choices=ROLES, default="reader")
    mk.set_defaults(func=make_key)
    sv = sub.add_parser("serve", help="프록시 서버를 켠다")
    sv.add_argument("--config", required=True)
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8282)
    sv.add_argument("--qmd-url", default="http://localhost:8181", help="qmd MCP 상시 서버 주소")
    sv.set_defaults(func=serve)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
