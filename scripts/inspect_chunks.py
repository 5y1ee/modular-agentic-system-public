"""qmd 색인이 문서를 어떻게 조각(chunk)으로 나눠 임베딩했는지 본다.

사용: uv run python scripts/inspect_chunks.py [경로 일부 ...]   (윈도우에서는 PYTHONUTF8=1 필요)
인자가 없으면 골든셋 정답 페이지들을 본다.

qmd 색인 DB(`~/.cache/qmd/index.sqlite`)를 읽기 전용으로 열어서 아래를 출력한다.
  - 문서마다 조각 수, 각 조각의 시작 위치(글자 offset), 문서 길이, 임베딩 시각
  - 활성 문서 중 임베딩 조각이 하나도 없는 문서 (있으면 벡터 검색에 전혀 안 걸린다)
  - 문서당 조각 수 분포
README에 따르면 qmd는 문서를 약 900토큰 단위로, 15% 겹치게, 제목·구분선·빈 줄 같은 경계에서 자르고
각 조각을 "제목 | 본문" 형태로 임베딩한다. 이 스크립트는 그 결과를 DB에서 확인하는 것이다.
"""
import sqlite3
import statistics
import sys
from pathlib import Path

DB = Path.home() / ".cache" / "qmd" / "index.sqlite"
DEFAULT_TARGETS = [
    "04_project/FINT.md",
    "04_project/FINT-Data.md",
    "04_project/오토체스_RL_시뮬레이터.md",
    "04_project/DevCraft 2026.md",
    "04_project/프로젝트A/프로젝트A.md",
    "06_career/커리어 개요.md",
    "06_career/지원서 공통 항목.md",
    "03_study/qmd.md",
    "06_career/자소서/2026년 하반기/26.09-지원처 7.md",
]


def doc_length_column(conn: sqlite3.Connection) -> str | None:
    """content 표에서 본문 텍스트가 든 열 이름을 찾는다(없으면 None)."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(content)")]
    return next((c for c in ("doc", "body", "content", "text") if c in cols), None)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    text_col = doc_length_column(conn)

    for pattern in sys.argv[1:] or DEFAULT_TARGETS:
        rows = conn.execute(
            "SELECT path, title, hash FROM documents WHERE active = 1 AND path LIKE ?", (f"%{pattern}%",)
        ).fetchall()
        if not rows:
            print(f"\n{pattern}: 색인에 없음")
        for path, title, doc_hash in rows:
            chunks = conn.execute(
                "SELECT seq, pos, total_chunks, embedded_at FROM content_vectors WHERE hash = ? ORDER BY seq",
                (doc_hash,),
            ).fetchall()
            length = ""
            if text_col:
                (n,) = conn.execute(f"SELECT length({text_col}) FROM content WHERE hash = ?", (doc_hash,)).fetchone()
                length = f", 문서 길이 {n}자"
            print(f"\n{path}  (제목: {title}{length})")
            if not chunks:
                print("  조각 0개 — 임베딩이 없다")
                continue
            positions = ", ".join(str(pos) for _, pos, _, _ in chunks)
            print(f"  조각 {len(chunks)}개 (total_chunks={chunks[0][2]}), 시작 위치(글자): {positions}")
            print(f"  임베딩 시각: {chunks[0][3]}")

    counts = conn.execute(
        "SELECT d.path, COUNT(v.seq) FROM documents d LEFT JOIN content_vectors v ON v.hash = d.hash "
        "WHERE d.active = 1 GROUP BY d.id"
    ).fetchall()
    missing = [path for path, n in counts if n == 0]
    sizes = [n for _, n in counts if n > 0]
    print(f"\n활성 문서 {len(counts)}개, 임베딩 조각이 없는 문서 {len(missing)}개")
    for path in missing[:15]:
        print(f"  - {path}")
    if sizes:
        print(
            f"문서당 조각 수: 최소 {min(sizes)}, 중앙값 {statistics.median(sizes)}, 최대 {max(sizes)}, "
            f"1조각짜리 문서 {sum(1 for s in sizes if s == 1)}개"
        )


if __name__ == "__main__":
    main()
