"""eval/experience_recall의 경험 단위 채점 규칙과 골든셋 v2 파일 형식을 확인한다."""
import json

from modular_agentic.eval.experience_recall import (
    GOLDEN_V2_PATH,
    credit_reason,
    first_credit,
    kept_hits,
    origin_rank,
)

EXP = {
    "id": "fint",
    "origin_pages": ["04_project/FINT.md"],
    "keywords": ["FINT", "모의투자"],
}


def hit(path: str, snippet: str = "", title: str = "") -> dict:
    return {"path": path, "snippet": snippet, "title": title}


def test_credit_by_origin_or_keyword_case_insensitive():
    assert credit_reason(EXP, hit("04_project/FINT.md")) == "origin"
    assert credit_reason(EXP, hit("a.md", snippet="우리는 fint를 만들었다")) == "keyword:FINT"
    assert credit_reason(EXP, hit("a.md", title="모의투자 서비스")) == "keyword:모의투자"
    assert credit_reason(EXP, hit("a.md", snippet="관련 없음")) is None


def test_first_credit_uses_rank_after_exclusion():
    hits = [
        hit("06_career/자소서/x.md", snippet="FINT 서술"),  # 제외 대상, 앞서지만 제외 관점에서는 빠짐
        hit("b.md", snippet="무관"),
        hit("c.md", snippet="모의투자 경험"),
    ]
    assert first_credit(EXP, hits)[0] == 1  # 전체
    in_view = kept_hits(hits, ("06_career/자소서/",))
    rank, found, reason = first_credit(EXP, in_view)
    assert (rank, found["path"], reason) == (2, "c.md", "keyword:모의투자")


def test_origin_rank_counts_only_origin_pages():
    hits = [hit("a.md", snippet="FINT"), hit("04_project/FINT.md")]
    assert origin_rank(EXP, hits) == 2
    assert origin_rank({**EXP, "origin_pages": []}, hits) is None


def test_golden_v2_file_is_well_formed():
    golden = json.loads(GOLDEN_V2_PATH.read_text(encoding="utf-8"))
    ids = {e["id"] for e in golden["experiences"]}
    assert ids
    for exp in golden["experiences"]:
        assert exp["keywords"]  # 키워드가 없으면 발췌로 경험을 알아볼 수 없다
        assert all(p.endswith(".md") for p in exp["origin_pages"])
    for case in golden["cases"]:
        assert case["expected_status"] in {"CONFIRMED", "PARTIAL", "TODO"}
        for item in case["expected_experiences"]:
            assert item["id"] in ids
            assert item["strength"] in {"core", "maybe"}
