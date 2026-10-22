"""콘솔의 보기 전용 전시 묶음 만들기: 결과 문서를 규칙대로 마스킹해 `logs/console/showcase/<이름>/`에 복사한다.

왜: 제안서처럼 발주처·제품명·요구 번호가 든 결과를 발표 화면에 보이려면 가려야 한다. 원본은 고치지 않는다.
치환·금지어 검사는 공개용 복사 도구(make_public_copy.py)와 같은 함수를 쓴다(이름은 단어 경계를 지켜 찾는다).

사용: uv run python scripts/mask_showcase.py --rules logs/console/showcase_rules/<이름>.json
규칙 파일(JSON, 이름 목록이 곧 비공개 정보라 git에 안 올라가는 logs/ 아래에 둔다):
  {"name": "proposal_demo",
   "files": {"logs/02_analyze/x.md": "02_요구 목록.md", ...},   # 원본 → 전시 파일 이름(앞 두 자리 00~05가 단계)
   "task": "제안서",                                            # 업무(콘솔 왼쪽의 업무 폴더). 없으면 자소서
   "replace": [...], "forbidden": [...]}                            # make_public_copy.py의 규칙 모양과 같다
금지 문구가 하나라도 남으면 아무것도 쓰지 않고 종료 코드 1로 끝나며 어디에 남았는지 보고한다.
"""
import argparse
import json
import sys
from pathlib import Path

from make_public_copy import apply_replacements, build_replacements, find_forbidden, forbidden_patterns

SHOWCASE_DIR = Path("logs") / "console" / "showcase"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rules", type=Path, required=True)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    sources = {Path(src): name for src, name in rules["files"].items()}
    texts = {src: src.read_text(encoding="utf-8") for src in sources}
    replacements = build_replacements(rules, list(texts.values()))
    patterns = forbidden_patterns(rules)
    masked = {name: apply_replacements(texts[src], replacements) for src, name in sources.items()}
    leftovers = {name: hits for name, text in masked.items() if (hits := find_forbidden(text, patterns))}
    if leftovers:
        for name, hits in leftovers.items():
            print(f"금지 문구 남음: {name}: {hits}")
        sys.exit(1)
    out = SHOWCASE_DIR / rules["name"]
    out.mkdir(parents=True, exist_ok=True)
    for name, text in masked.items():
        (out / name).write_text(text, encoding="utf-8")
        print(f"썼다: {out / name}")
    if rules.get("task"):
        (out / "업무.txt").write_text(rules["task"] + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
