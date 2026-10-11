"""vault 검색이 골든셋의 정답 페이지를 몇 위에 올리는지 잰다.

사용: uv run python -m modular_agentic.eval.search_recall

골든셋(golden/cover_letter_vault.json)의 질의마다 qmd를 한 번 부르고(약 5초),
같은 결과에서 "제외 경로를 적용하기 전"과 "적용한 뒤"의 순위를 둘 다 계산한다.
읽기 전용이다.

점수는 `core` 정답 페이지 기준이다. `maybe`는 순위만 보여 주고 점수에는 넣지 않는다.
정답이 없는 질의(TODO가 기대 상태인 경우)는 core가 없어 점수 계산에서 빠진다.
이 모듈은 "검색이 찾아 오는가"만 잰다. 찾아 온 후보가 실제 근거인지 판단하는 일(매핑 에이전트의 몫)은 재지 않는다.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from modular_agentic.instances.cover_letter.config import VAULT_EXCLUDE_PREFIXES
from modular_agentic.tools.vault_search import VaultHit, search

GOLDEN_PATH = Path(__file__).parent / "golden" / "cover_letter_vault.json"
FETCH_N = 40  # 질의당 상위 몇 건까지 순위를 보나
CUTOFFS = (5, 10)


def rank_of(path: str, hits: Sequence[VaultHit]) -> int | None:
    """hits에서 path의 순위(1부터). 없으면 None."""
    for i, hit in enumerate(hits, 1):
        if hit.path == path:
            return i
    return None


def recall_at(ranks: Sequence[int | None], k: int) -> float | None:
    """ranks 중 k위 안에 든 비율. 대상이 없으면 None."""
    if not ranks:
        return None
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def _fmt(rank: int | None) -> str:
    return f"{rank}위" if rank is not None else f">{FETCH_N}위/없음"


def main() -> None:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    totals = {"전체": [], "제외 적용": []}  # core 페이지들의 순위 모음

    for case in golden["cases"]:
        query = case["query"]
        print(f"\n질의: {query}  (기대 상태: {case['expected_status']})")
        everything = search(query, n=FETCH_N, tag="recall")
        filtered = [h for h in everything if not h.path.startswith(VAULT_EXCLUDE_PREFIXES)]

        for page in case["expected_pages"]:
            r_all = rank_of(page["path"], everything)
            r_filtered = rank_of(page["path"], filtered)
            print(f"  [{page['strength']}] {page['path']}  전체 {_fmt(r_all)} / 제외 적용 {_fmt(r_filtered)}")
            if page["strength"] == "core":
                totals["전체"].append(r_all)
                totals["제외 적용"].append(r_filtered)

    print("\ncore 페이지 recall (찾아 온 비율)")
    for label, ranks in totals.items():
        parts = [f"@{k}: {recall_at(ranks, k):.0%}" for k in CUTOFFS if recall_at(ranks, k) is not None]
        print(f"  {label}: {', '.join(parts)}  (core {len(ranks)}개)")


if __name__ == "__main__":
    main()
