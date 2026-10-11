"""검색 상위 결과가 조각(chunk)이 많은 문서에 치우치는지 본다.

사용: uv run python scripts/chunk_bias.py logs/search/<파일>.jsonl [--top 10]

qmd의 `vsearch`는 문서 점수를 "그 문서의 가장 비슷한 조각 하나의 코사인 유사도"로 정한다
(store.js의 searchVec: 조각을 문서별로 묶어 최소 거리 하나만 남김). 점수식에 조각 수는 없지만, 조각이
많은 문서는 그만큼 비교 기회가 많아서 최고점이 높게 나오기 쉽다. 이 스크립트는 그 치우침이 실제 로그에서
보이는지 확인한다.

비교: 전체 문서의 조각 수 분포 vs 검색 상위 N건 문서의 조각 수 분포.
조각 수는 qmd 색인 DB(`~/.cache/qmd/index.sqlite`, 읽기 전용)에서, 상위 결과는 `MAS_SEARCH_LOG` 로그에서 가져온다.
캐시 회피용 변형 질의(method가 vsearch-cachebust)와 vsearch가 아닌 방식은 뺀다. 읽기 전용이다.
"""
import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

DB = Path.home() / ".cache" / "qmd" / "index.sqlite"
BIG = 8  # '조각이 많은 문서'의 기준


def chunk_counts() -> dict[str, int]:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT d.path, COUNT(v.seq) FROM documents d JOIN content_vectors v ON v.hash = d.hash "
        "WHERE d.active = 1 GROUP BY d.id"
    ).fetchall()
    return dict(rows)


def describe(label: str, counts: list[int]) -> None:
    big = sum(1 for c in counts if c >= BIG)
    print(
        f"  {label}: {len(counts)}건, 조각 수 평균 {statistics.mean(counts):.1f} / 중앙값 {statistics.median(counts)}, "
        f"{BIG}조각 이상 {big / len(counts):.0%}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--tag", default="", help="태그에 이 문자열이 든 기록만 쓴다 (예: recall). 자기 문장 질의는 정답 페이지가 길어서 결과가 치우칠 수 있다")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    counts = chunk_counts()
    print(f"조각 수 출처: 이 기기의 qmd 색인 {DB} (문서 {len(counts)}개)")
    describe("전체 문서", list(counts.values()))

    top_counts: list[int] = []
    queries = 0
    for line in args.log.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["method"] != "vsearch" or args.tag not in rec["tag"]:
            continue
        queries += 1
        top_counts += [counts[h["path"]] for h in rec["hits"][: args.top] if h["path"] in counts]
    print(f"\n로그의 vsearch 질의 {queries}개, 각 상위 {args.top}건")
    if top_counts:
        describe(f"상위 {args.top}건 문서", top_counts)
    else:
        print("  비교할 결과가 없다(로그의 경로가 색인 DB에 없음)")


if __name__ == "__main__":
    main()
