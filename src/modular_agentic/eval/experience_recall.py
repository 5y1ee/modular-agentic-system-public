"""경험 단위 골든셋(v2)으로 저장된 검색 로그를 채점한다. qmd를 부르지 않는다.

사용: uv run python -m modular_agentic.eval.experience_recall logs/search/<파일>.jsonl [--verbose]

골든셋 v2(golden/cover_letter_experiences_v2_draft.json)는 정답을 페이지가 아니라 경험으로 둔다.
경험마다 `origin_pages`(원본 페이지)와 `keywords`(그 경험을 알아보는 단어)가 있다.

채점 규칙
  경험이 걸렸다  제외 관점을 적용한 상위 결과 중 (1) 원본 페이지이거나 (2) 발췌 또는 제목에 키워드가 든 결과가 있다.
                걸린 순위는 그런 결과 중 가장 앞선 것(제외 관점을 적용한 뒤의 순위)이다.
  원본 recall    원본 페이지만 따로 센다. origin_pages가 비어 있는 경험은 여기서 빠진다.
점수는 `core` 경험 기준이고 `maybe`는 순위만 보여 준다.

한계: 키워드가 발췌에 있다고 그 경험을 제대로 서술하는 것은 아니다(우연한 일치). 그래서 어떤 결과 덕분에
걸렸는지 같이 보여 준다(`--verbose`는 키워드 주변 발췌까지). 발췌는 로그에 600자까지만 저장돼 있다.
읽기 전용이다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from modular_agentic.eval.recall_from_log import RECALL_TAG, VIEWS, load_latest
from modular_agentic.eval.search_recall import CUTOFFS, recall_at

GOLDEN_V2_PATH = Path(__file__).parent / "golden" / "cover_letter_experiences_v2_draft.json"
CONTEXT = 40  # --verbose에서 키워드 앞뒤로 보여 줄 글자 수


def kept_hits(hits: Sequence[dict], exclude: Sequence[str]) -> list[dict]:
    """제외 경로를 뺀 결과. 순서(순위)는 그대로다."""
    return [h for h in hits if not h["path"].startswith(tuple(exclude))]


def credit_reason(experience: dict, hit: dict) -> str | None:
    """이 결과가 경험을 걸리게 하는 이유. 원본이면 'origin', 키워드면 'keyword:<단어>', 아니면 None."""
    if hit["path"] in experience["origin_pages"]:
        return "origin"
    text = f"{hit.get('title', '')} {hit['snippet']}".lower()
    for keyword in experience["keywords"]:
        if keyword.lower() in text:
            return f"keyword:{keyword}"
    return None


def first_credit(experience: dict, hits_in_view: Sequence[dict]) -> tuple[int, dict, str] | None:
    """제외 관점의 결과에서 경험을 걸리게 하는 가장 앞선 결과 (순위, 결과, 이유). 없으면 None."""
    for rank, hit in enumerate(hits_in_view, 1):
        reason = credit_reason(experience, hit)
        if reason:
            return rank, hit, reason
    return None


def origin_rank(experience: dict, hits_in_view: Sequence[dict]) -> int | None:
    """원본 페이지의 순위. 원본 페이지가 없거나 결과에 없으면 None."""
    for rank, hit in enumerate(hits_in_view, 1):
        if hit["path"] in experience["origin_pages"]:
            return rank
    return None


def snippet_window(hit: dict, reason: str) -> str:
    """키워드 주변 발췌(원본으로 걸린 경우는 발췌 앞부분)."""
    text = " ".join(hit["snippet"].split())
    if reason.startswith("keyword:"):
        pos = text.lower().find(reason.removeprefix("keyword:").lower())
        if pos >= 0:
            return text[max(0, pos - CONTEXT) : pos + CONTEXT * 2]
    return text[: CONTEXT * 3]


def _fmt(credit: tuple[int, dict, str] | None) -> str:
    if credit is None:
        return "없음"
    rank, hit, reason = credit
    return f"{rank}위({reason.removeprefix('keyword:') if reason != 'origin' else '원본'}, {Path(hit['path']).stem})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--verbose", action="store_true", help="걸리게 한 결과의 발췌도 보여 준다(관점별 첫 번째만)")
    parser.add_argument("--tag", default=RECALL_TAG, help="채점할 로그 기록의 태그(기본 recall). 리랭킹 결과는 recall-rerank")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    golden = json.loads(GOLDEN_V2_PATH.read_text(encoding="utf-8"))
    experiences = {e["id"]: e for e in golden["experiences"]}
    latest = load_latest(args.log, args.tag)

    core_exp: dict[str, list[int | None]] = {view: [] for view in VIEWS}
    core_origin: dict[str, list[int | None]] = {view: [] for view in VIEWS}

    for case in golden["cases"]:
        hits = latest.get(case["query"])
        print(f"\n질의: {case['query']}  (기대 상태: {case['expected_status']})")
        if hits is None:
            print("  (로그에 이 질의의 recall 기록이 없음)")
            continue
        for item in case["expected_experiences"]:
            exp = experiences[item["id"]]
            print(f"  [{item['strength']}] {exp['id']}")
            for view, exclude in VIEWS.items():
                in_view = kept_hits(hits, exclude)
                credit = first_credit(exp, in_view)
                origin = origin_rank(exp, in_view)
                origin_text = f", 원본 {origin}위" if origin else (", 원본 없음" if exp["origin_pages"] else "")
                print(f"      {view}: {_fmt(credit)}{origin_text}")
                if args.verbose and credit:
                    print(f"          ↳ …{snippet_window(credit[1], credit[2])}…")
                if item["strength"] == "core":
                    core_exp[view].append(credit[0] if credit else None)
                    if exp["origin_pages"]:
                        core_origin[view].append(origin)

    for title, table in (("core 경험 recall (경험이 걸린 비율)", core_exp), ("core 원본 recall (원본 페이지가 걸린 비율)", core_origin)):
        print(f"\n{title}")
        for view, ranks in table.items():
            parts = [f"@{k}: {recall_at(ranks, k):.0%}" for k in CUTOFFS if recall_at(ranks, k) is not None]
            print(f"  {view}: {', '.join(parts)}  (대상 {len(ranks)}개)")


if __name__ == "__main__":
    main()
