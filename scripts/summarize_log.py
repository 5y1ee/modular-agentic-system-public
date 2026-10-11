"""검색 로그(JSON Lines)를 사람이 읽는 표로 정리한다.

사용: uv run python scripts/summarize_log.py logs/search/<파일>.jsonl [--top 5] [--snippet 90]

호출 하나마다 검색어, 소요 시간, 상위 결과를 두 관점으로 보여 준다.
  전체       qmd가 돌려준 순서 그대로. 제외 경로에 해당하는 페이지는 [제외 대상]으로 표시
  제외 적용  instances/cover_letter/config.py의 VAULT_EXCLUDE_PREFIXES를 뺀 순서

각 줄에는 qmd가 돌려준 발췌(맞은 조각의 앞부분)를 붙여서, 왜 그 페이지가 잡혔는지 내용으로 볼 수 있다.
로그에는 자소서 본문 발췌가 들어 있으니 출력을 외부에 공유할 때 주의한다. 읽기 전용이다.
"""
import argparse
import json
import sys
from pathlib import Path

from modular_agentic.instances.cover_letter.config import VAULT_EXCLUDE_PREFIXES


def one_line(text: str, width: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[:width] + "…"


def show(hits: list[dict], top: int, width: int, mark_excluded: bool) -> None:
    for h in hits[:top]:
        flag = " [제외 대상]" if mark_excluded and h["path"].startswith(VAULT_EXCLUDE_PREFIXES) else ""
        print(f"    {h['rank']:>2}. {h['score']:.2f}  {h['path']}:{h['line']}{flag}")
        print(f"        {one_line(h['snippet'], width)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--snippet", type=int, default=90)
    parser.add_argument("--tag", default="", help="태그에 이 문자열이 들어 있는 호출만 보여 준다 (예: recall, probe:head, FINT)")
    parser.add_argument("--method", default="", help="검색 방식 이름이 이 문자열과 같은 호출만 보여 준다 (예: vsearch)")
    args = parser.parse_args()

    # 윈도우 콘솔(CP949)에서도 한글·특수문자가 깨지지 않게 한다.
    sys.stdout.reconfigure(encoding="utf-8")

    for line in args.log.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if args.tag not in rec["tag"] or (args.method and rec["method"] != args.method):
            continue
        hits = rec["hits"]
        print(f"\n[{rec['tag']}] {rec['method']}  {rec['elapsed_sec']}초  {rec['machine']}  CPU강제={rec['force_cpu']}")
        print(f"  검색어: {one_line(rec['query'], 100)}")
        print("  전체 상위:")
        show(hits, args.top, args.snippet, mark_excluded=True)
        kept = [h for h in hits if not h["path"].startswith(VAULT_EXCLUDE_PREFIXES)]
        print("  제외 적용 상위:")
        show(kept, args.top, args.snippet, mark_excluded=False)


if __name__ == "__main__":
    main()
