"""저장된 검색 로그로 recall을 다시 계산한다. qmd를 부르지 않는다.

사용: uv run python -m modular_agentic.eval.recall_from_log logs/search/<파일>.jsonl

로그(`MAS_SEARCH_LOG`)에는 제외 경로를 적용하기 전의 검색 결과 전체가 들어 있다. 그래서 제외 목록이나
골든셋을 바꿔도 검색을 다시 하지 않고 같은 검색 결과를 새 기준으로 채점할 수 있다. 검색 조건이 같으니
기준만 바꾼 효과를 깨끗하게 비교할 수 있다.

골든셋의 질의와 같은 검색어를 가진 `recall` 태그 기록 중 가장 나중 것을 쓴다.
세 가지 관점으로 계산한다.
  전체                          제외 없이 qmd가 돌려준 순서
  제외(현재 목록)               config.VAULT_EXCLUDE_PREFIXES를 뺌
  제외 + 가공된 서술            위에 면접 페이지·포트폴리오(config.DERIVED_NARRATIVE_PREFIXES)를 더 뺌
점수는 `core` 정답 페이지 기준이고 `maybe`는 순위만 보여 준다. 읽기 전용이다.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from modular_agentic.eval.search_recall import CUTOFFS, GOLDEN_PATH, recall_at
from modular_agentic.instances.cover_letter.config import VAULT_EXCLUDE_PREFIXES, VAULT_EXCLUDE_WITH_DERIVED

VIEWS: dict[str, tuple[str, ...]] = {
    "전체": (),
    "제외(현재 목록)": VAULT_EXCLUDE_PREFIXES,
    "제외 + 가공된 서술": VAULT_EXCLUDE_WITH_DERIVED,
}
RECALL_TAG = "recall"


def load_latest(log_path: Path, tag: str = RECALL_TAG) -> dict[str, list[dict]]:
    """검색어 → 그 검색어의 가장 나중 기록의 hits. 해당 태그의 기록만 쓴다."""
    latest: dict[str, list[dict]] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["tag"] == tag:
            latest[rec["query"]] = rec["hits"]
    return latest


def rank_in_view(path: str, hits: Sequence[dict], exclude: Sequence[str]) -> int | None:
    """제외 경로를 뺀 목록에서 path의 순위(1부터). 없으면 None."""
    kept = [h["path"] for h in hits if not h["path"].startswith(tuple(exclude))]
    return kept.index(path) + 1 if path in kept else None


def _fmt(rank: int | None) -> str:
    return f"{rank}위" if rank is not None else "없음"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("사용: python -m modular_agentic.eval.recall_from_log <로그 파일>")
    sys.stdout.reconfigure(encoding="utf-8")
    latest = load_latest(Path(sys.argv[1]))
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    core_ranks: dict[str, list[int | None]] = {name: [] for name in VIEWS}

    for case in golden["cases"]:
        hits = latest.get(case["query"])
        print(f"\n질의: {case['query']}  (기대 상태: {case['expected_status']})")
        if hits is None:
            print("  (로그에 이 질의의 recall 기록이 없음)")
            continue
        for page in case["expected_pages"]:
            ranks = {name: rank_in_view(page["path"], hits, excl) for name, excl in VIEWS.items()}
            cells = " / ".join(f"{name} {_fmt(r)}" for name, r in ranks.items())
            print(f"  [{page['strength']}] {page['path']}  {cells}")
            if page["strength"] == "core":
                for name, r in ranks.items():
                    core_ranks[name].append(r)

    print("\ncore 페이지 recall")
    for name, ranks in core_ranks.items():
        parts = [f"@{k}: {recall_at(ranks, k):.0%}" for k in CUTOFFS if recall_at(ranks, k) is not None]
        print(f"  {name}: {', '.join(parts)}  (core {len(ranks)}개)")


if __name__ == "__main__":
    main()
