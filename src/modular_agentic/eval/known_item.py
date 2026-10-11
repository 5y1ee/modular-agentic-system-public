"""실험 H1: 알려진 항목 찾기 (docs/experiments/001-vault-search-recall.md).

사용: uv run python -m modular_agentic.eval.known_item

골든셋의 정답 페이지마다 본문에서 문장을 그대로 떼어 질의로 쓰고, 그 페이지가 몇 위에 나오는지 본다.
자기 문장을 질의로 줬는데도 그 페이지가 상위에 안 나오면, 질의와 페이지 사이의 어휘 차이가 아니라
임베딩이 없거나 오래됐다는 신호다. 읽기 전용이다.

문장은 사람이 고르지 않고 규칙으로 고른다(고르는 쪽의 편향을 막기 위해):
  - 제목(#), 표(|), 코드 블록, 구분선, URL이 든 줄은 뺀다. 목록 기호는 떼어 낸다.
  - 남은 줄 중 길이가 MIN_LEN~MAX_LEN인 줄을 후보로 삼고, 첫 번째와 가운데 것을 쓴다.
"""
from __future__ import annotations

import json
import re

from modular_agentic.eval.search_recall import GOLDEN_PATH, rank_of
from modular_agentic.instances.cover_letter.config import VAULT_DIR
from modular_agentic.tools.vault_search import search

MIN_LEN = 40
MAX_LEN = 200
FETCH_N = 40
TOP_OK = 3  # 자기 문장이면 이 순위 안에 나와야 정상으로 본다

_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")


def candidate_lines(text: str) -> list[str]:
    """질의로 쓸 만한 줄들. 규칙은 모듈 docstring 참고."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":  # frontmatter 건너뛰기
        try:
            end = next(i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---")
            lines = lines[end + 1 :]
        except StopIteration:
            pass
    out: list[str] = []
    in_code = False
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped:
            continue
        if stripped.startswith(("#", "|", "---", "!")) or "http" in stripped:
            continue
        line = _LIST_MARKER_RE.sub("", stripped)
        if MIN_LEN <= len(line) <= MAX_LEN:
            out.append(line)
    return out


def pick(text: str) -> list[str]:
    """후보 중 첫 번째와 가운데 줄 (후보가 하나면 하나)."""
    cands = candidate_lines(text)
    if not cands:
        return []
    picks = [cands[0]]
    middle = cands[len(cands) // 2]
    if middle != cands[0]:
        picks.append(middle)
    return picks


def target_paths() -> list[str]:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    paths: list[str] = []
    for case in golden["cases"]:
        for page in case["expected_pages"]:
            if page["path"] not in paths:
                paths.append(page["path"])
    return paths


def main() -> None:
    ok = total = 0
    for path in target_paths():
        text = (VAULT_DIR / path).read_text(encoding="utf-8")
        sentences = pick(text)
        print(f"\n{path}")
        if not sentences:
            print("  (질의로 쓸 만한 줄이 없음)")
            continue
        for sentence in sentences:
            rank = rank_of(path, search(sentence, n=FETCH_N, tag=f"h1:{path}"))
            total += 1
            ok += rank is not None and rank <= TOP_OK
            shown = f"{rank}위" if rank is not None else f">{FETCH_N}위/없음"
            print(f"  {shown}  ← {sentence[:70]}{'…' if len(sentence) > 70 else ''}")
    print(f"\n자기 문장이 {TOP_OK}위 안에 나온 비율: {ok}/{total}")


if __name__ == "__main__":
    main()
