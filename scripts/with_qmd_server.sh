#!/usr/bin/env bash
# qmd 상시 서버(HTTP)를 켜고, 주어진 명령을 MAS_QMD_URL을 설정해 실행한 뒤, 이 스크립트가 띄운 서버만 끈다. (리눅스 서버용)
# 윈도우 버전은 scripts/with_qmd_server.ps1.
#
# 사용 (레포 폴더에서, 스크립트 이름 뒤가 전부 실행할 명령):
#   scripts/with_qmd_server.sh uv run --extra strands python scripts/run_strands_demo.py samples/db_inc_sw_engineer_ax.txt --max 5
#
# 왜 필요할 때만 켜는가: 서버는 인증이 없어서 켜 둔 시간만큼 vault(자소서 포함)가 열려 있다. 켜는 데는 2~3초밖에 안 든다.
#   (docs/experiments/002 7절). 색인을 갱신한 직후에도 새로 켜므로 "서버가 새 문서를 보는가" 문제가 생기지 않는다.
# 안전: 자기가 띄운 서버(프로세스 그룹)만 끈다(node 전체를 죽이지 않는다 — Claude Code도 node로 돈다). 명령이 실패하거나 Ctrl+C를 눌러도 EXIT 트랩이 끈다.
#   이미 서버가 떠 있으면(/health 응답) 새로 띄우지도 끄지도 않고 그대로 쓴다.
# 서버는 GPU 없는 이 기기의 정책(QMD_FORCE_CPU=1)으로 띄운다. 기본 주소는 http://localhost:8181 (MAS_QMD_URL로 바꿀 수 있다).
set -u

if [ "$#" -eq 0 ]; then
  echo "실행할 명령을 스크립트 이름 뒤에 적어 주세요." >&2
  exit 2
fi

BASE="${MAS_QMD_URL:-http://localhost:8181}"
LOG="${XDG_CACHE_HOME:-$HOME/.cache}/qmd/mcp-with-server.log"
mkdir -p "$(dirname "$LOG")"
SERVER_PID=""

stop_server() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    # qmd 런처가 node를 자식으로 띄우므로 PID 하나만 끄면 서버가 남는다(2026-10-04 시험에서 확인). 이 서버만의 프로세스 그룹 전체를 끈다.
    kill -- "-$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
    echo "qmd 서버 종료: PID $SERVER_PID" >&2
  fi
}
trap stop_server EXIT
trap 'exit 130' INT TERM

if curl -fsS -m 2 "$BASE/health" >/dev/null 2>&1; then
  echo "이미 떠 있는 서버를 그대로 쓴다($BASE). 이 스크립트가 끄지 않는다." >&2
else
  QMD_FORCE_CPU=1 setsid qmd mcp --http >"$LOG" 2>&1 &
  SERVER_PID=$!
  echo "qmd 서버 시작: PID $SERVER_PID (로그: $LOG)" >&2
  ready=0
  for i in $(seq 1 90); do
    if curl -fsS -m 2 "$BASE/health" >/dev/null 2>&1; then ready=1; break; fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi
    sleep 1
  done
  if [ "$ready" -ne 1 ]; then
    echo "서버가 90초 안에 $BASE/health에 응답하지 않았다(로그: $LOG)" >&2
    exit 1
  fi
  echo "서버 준비됨(약 ${i}초). 실행: $*" >&2
fi

MAS_QMD_URL="$BASE" "$@"
status=$?
[ "$status" -ne 0 ] && echo "명령이 종료 코드 $status로 끝났다" >&2
exit "$status"
