"""eval/recall_from_log의 순위 계산과 로그 읽기를 확인한다."""
import json

from modular_agentic.eval.recall_from_log import load_latest, rank_in_view


def hits(*paths: str) -> list[dict]:
    return [{"rank": i, "path": p} for i, p in enumerate(paths, 1)]


def test_rank_in_view_skips_excluded_prefixes():
    h = hits("06_career/자소서/a.md", "06_career/지원처 11 면접 준비.md", "04_project/FINT.md")

    assert rank_in_view("04_project/FINT.md", h, ()) == 3
    assert rank_in_view("04_project/FINT.md", h, ("06_career/자소서/",)) == 2
    assert rank_in_view("04_project/FINT.md", h, ("06_career/자소서/", "06_career/지원처 11")) == 1
    assert rank_in_view("없는.md", h, ()) is None


def test_load_latest_uses_last_record_of_tag(tmp_path):
    log = tmp_path / "s.jsonl"
    records = [
        {"tag": "recall", "query": "q", "hits": hits("old.md")},
        {"tag": "h1:x", "query": "q", "hits": hits("other.md")},  # 다른 태그는 무시
        {"tag": "recall", "query": "q", "hits": hits("new.md")},
    ]
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")

    assert load_latest(log) == {"q": hits("new.md")}
