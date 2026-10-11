"""eval/search_recall의 순위·recall 계산과 골든셋 파일 형식을 확인한다."""
import json

from modular_agentic.eval.search_recall import GOLDEN_PATH, rank_of, recall_at
from modular_agentic.tools.vault_search import VaultHit


def hit(path: str) -> VaultHit:
    return VaultHit(path=path, title="", score=0.5, line=1, snippet="", docid="#x")


def test_rank_of():
    hits = [hit("a.md"), hit("b.md")]
    assert rank_of("b.md", hits) == 2
    assert rank_of("c.md", hits) is None


def test_recall_at():
    assert recall_at([1, 7, None, 3], 5) == 0.5  # 1위와 3위만 5위 안
    assert recall_at([], 5) is None  # 대상이 없으면 점수 계산에서 빠진다


def test_golden_file_is_well_formed():
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert golden["cases"]
    for case in golden["cases"]:
        assert case["expected_status"] in {"CONFIRMED", "PARTIAL", "TODO"}
        for page in case["expected_pages"]:
            assert page["strength"] in {"core", "maybe"}
            assert page["path"].endswith(".md")
