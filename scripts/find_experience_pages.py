"""경험별로 그 경험을 담고 있을 만한 vault 페이지 후보를 키워드로 찾는다 (골든셋 v2 라벨링용).

사용: uv run python scripts/find_experience_pages.py   (윈도우에서는 PYTHONUTF8=1 필요)

경험마다 키워드가 든 페이지를 모아서, 키워드 개수·페이지 종류·첫 일치 부분의 발췌를 보여 준다.
키워드가 든다고 그 경험을 서술하는 페이지는 아니다(예: 구직 이력 표에 회사 이름만 나오는 경우).
그래서 이 출력은 **후보**일 뿐이고, 정답으로 인정할지는 발췌를 읽고 사람이 정한다.
순위·점수를 쓰지 않는다. 라벨을 검색 결과를 보고 정하지 않기 위해서다. 읽기 전용이다.
"""
import re
import sys
from pathlib import Path

from modular_agentic.instances.cover_letter.config import (
    DERIVED_NARRATIVE_PREFIXES,
    VAULT_DIR,
    VAULT_EXCLUDE_PREFIXES,
)

# 경험 이름 → (키워드 중 하나라도 있어야 함, 함께 있어야 하는 키워드 중 하나(없으면 생략))
EXPERIENCES: dict[str, tuple[list[str], list[str]]] = {
    "DevCraft": (["DevCraft"], []),
    "회사C 인턴(오토체스 시뮬레이터)": (["회사C", "회사C", "오토체스"], []),
    "FINT": (["FINT"], []),
    "졸업작품(Unity ML-Agents)": (["ML-Agents", "졸업작품", "졸업 작품"], []),
    "공공기관 공모전": (["공공기관"], []),
    "학부연구생": (["학부연구생", "학부 연구생"], []),
    "qmd(knowledge-base 검색)": (["qmd"], []),
    "프로젝트A의 LLM 대화 기능": (["프로젝트A", "프로젝트A"], ["tool_call", "tool call", "스트리밍", "chatting", "LLM"]),
}
# qmd가 색인하지 않는 곳(vault CLAUDE.md의 ignore 목록)
IGNORED = ("_scratch/", "98_templates/", "00_raw/notion/자소서/", "00_raw/notion/drimo_export/", "index.md", "log.md", ".git/")
TOP_N = 12
CONTEXT = 45  # 첫 일치 앞뒤로 보여 줄 글자 수


def kind_of(path: str) -> str:
    if path.startswith("04_project/"):
        return "프로젝트 페이지"
    if path.startswith("06_career/자소서/"):
        return "자소서 서술"
    if path.startswith("06_career/지원처 11"):
        return "면접 서술"
    if path.startswith("06_career/포트폴리오/"):
        return "포트폴리오 서술"
    if path.startswith("06_career/"):
        return "커리어 요약·이력"
    if path.startswith("01_daily/"):
        return "daily"
    if path.startswith("02_concept/"):
        return "개념"
    if path.startswith("03_study/"):
        return "학습"
    if path.startswith("00_raw/"):
        return "raw"
    return "기타"


def excerpt(text: str, pattern: re.Pattern[str]) -> str:
    m = pattern.search(text)
    if not m:
        return ""
    start, end = max(0, m.start() - CONTEXT), min(len(text), m.end() + CONTEXT)
    return " ".join(text[start:end].split())


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    files = [
        (p.relative_to(VAULT_DIR).as_posix(), p)
        for p in sorted(VAULT_DIR.rglob("*.md"))
        if not p.relative_to(VAULT_DIR).as_posix().startswith(IGNORED)
    ]
    for name, (any_of, also) in EXPERIENCES.items():
        pattern = re.compile("|".join(re.escape(k) for k in any_of))
        found = []
        for rel, path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            count = len(pattern.findall(text))
            if count and (not also or any(a in text for a in also)):
                found.append((count, rel, text))
        found.sort(key=lambda item: -item[0])
        print(f"\n## {name}  (키워드 {any_of}{' + ' + str(also) if also else ''}) — 후보 {len(found)}개, 상위 {TOP_N}개")
        for count, rel, text in found[:TOP_N]:
            tags = []
            if rel.startswith(VAULT_EXCLUDE_PREFIXES):
                tags.append("현재 제외 목록")
            if rel.startswith(DERIVED_NARRATIVE_PREFIXES):
                tags.append("가공된 서술 목록")
            tag = f"  [{', '.join(tags)}]" if tags else ""
            print(f"- {rel}  ({kind_of(rel)}, {count}회){tag}")
            print(f"    …{excerpt(text, pattern)}…")


if __name__ == "__main__":
    main()
