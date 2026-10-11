"""분석 결과(core/analysis_review.py): 저장·읽기, 기본 요건 표시, 회사 맥락, 읽는 용 문서. 모델은 부르지 않는다."""
import pytest

from modular_agentic.core.analysis_review import analysis_path, load_analysis, mark_basic, render_analysis_md, save_analysis, with_context
from modular_agentic.core.types import Analysis, Brief, Citation, ContextNote, Claim, Requirement

REQS = [Requirement(f"job-r{i}", "job", text, kind) for i, (text, kind) in enumerate([("Java/Spring 백엔드", "필수"), ("AI 기능 연계(코드 생성·리뷰)", "담당업무"), ("문서화·온보딩", "")], 1)]
BRIEF = Brief("job", "회사", claims=[Claim("c3", "H Chat은 코드 작성·리뷰에도 쓰인다. " + "길게 " * 30, "제품", [Citation("d", "인용 구절입니다 충분히")])])


def test_save_and_load_roundtrip_with_basic_and_context_and_missing_file(tmp_path):
    path = analysis_path(tmp_path / "02_analyze", "job")
    assert path.name == "job.json" and load_analysis(path) is None
    analysis = Analysis("job", REQS, basic_requirement_ids=["job-r1"], context={"job-r2": [ContextNote("c3", "코드 리뷰에 쓰이는 AI 도구다")]}, warnings=["경고"])
    save_analysis(path, analysis, BRIEF)
    assert load_analysis(path) == analysis  # 폴더가 없어도 만들고 원본 JSON을 그대로 되살린다
    assert path.with_suffix(".md").exists()


def test_mark_basic_uses_one_based_numbers_replaces_the_old_marks_and_rejects_out_of_range():
    analysis = Analysis("job", REQS)
    marked = mark_basic(analysis, [1, 3, 1])
    assert marked.basic_requirement_ids == ["job-r1", "job-r3"]  # 중복은 한 번
    assert mark_basic(marked, [2]).basic_requirement_ids == ["job-r2"]  # 이전 표시는 대체
    assert mark_basic(marked, []).basic_requirement_ids == []
    with pytest.raises(ValueError, match=r"\[0, 4\].*1~3"):
        mark_basic(analysis, [0, 4])
    assert analysis.basic_requirement_ids == []  # 원본은 안 바뀐다


def test_with_context_replaces_context_and_appends_warnings():
    analysis = Analysis("job", REQS, context={"job-r1": [ContextNote("c0", "이전")]}, warnings=["앞 경고"])
    updated = with_context(analysis, {"job-r2": [ContextNote("c3", "새 맥락")]}, ["새 경고"])
    assert list(updated.context) == ["job-r2"] and updated.warnings == ["앞 경고", "새 경고"]


def test_markdown_shows_numbers_basic_marks_context_with_claim_preview_and_warnings():
    analysis = Analysis("job", REQS, basic_requirement_ids=["job-r1"], context={"job-r2": [ContextNote("c3", "코드 리뷰에 쓰이는 AI 도구다")]}, warnings=["연결 1개를 버렸다"])
    md = render_analysis_md(analysis, BRIEF)
    assert "요구 3개, 기본 요건 표시 1개, 회사 맥락이 붙은 요구 1개" in md
    assert "1. [필수] Java/Spring 백엔드 — **기본(어필 안 함)**" in md and "2. [담당업무] AI 기능 연계(코드 생성·리뷰)\n" in md and "3. 문서화·온보딩\n" in md
    assert "   - 회사 맥락 `c3`: 코드 리뷰에 쓰이는 AI 도구다 — 주장: H Chat은 코드 작성·리뷰에도 쓰인다." in md and "…" in md  # 주장 본문은 앞부분만 보인다
    assert "⚠ 연결 1개를 버렸다" in md
    no_brief = render_analysis_md(analysis)  # 조사 보고서가 없어도 문서는 만들어지고 주장 본문만 빠진다
    assert "회사 맥락 `c3`: 코드 리뷰에 쓰이는 AI 도구다\n" in no_brief and "— 주장:" not in no_brief


def test_claims_that_only_come_from_the_posting_are_left_out_of_company_context():
    from modular_agentic.core.brief_review import BriefRecord, without_posting_only_claims
    from modular_agentic.core.types import SourceDoc, SourceKind

    posting, web, upload = SourceDoc("d1", SourceKind.POSTING, "공고"), SourceDoc("d2", SourceKind.WEB, "https://x"), SourceDoc("d3", SourceKind.UPLOAD, "inputs/a.md")
    cite = lambda *ids: [Citation(i, "인용 구절입니다 충분히") for i in ids]  # noqa: E731
    brief = Brief("job", "회사", claims=[
        Claim("c1", "공고의 주요 업무는 …이다", "직무", cite("d1")),  # 공고만 → 뺀다(되풀이)
        Claim("c2", "회사는 …를 만들었다", "제품", cite("d2")),  # 웹 → 남긴다
        Claim("c3", "공고와 웹이 함께 말한다", "사업", cite("d1", "d2")),  # 하나라도 공고 밖이면 남긴다
        Claim("c4", "직접 올린 자료의 Talent Value", "가치", cite("d3")),  # 사용자가 올린 파일 → 남긴다
    ])
    kept, dropped = without_posting_only_claims(BriefRecord(brief, [posting, web, upload], approved=True))
    assert dropped == ["c1"] and [c.id for c in kept.claims] == ["c2", "c3", "c4"]
    assert [c.id for c in brief.claims] == ["c1", "c2", "c3", "c4"]  # 원본 보고서는 바뀌지 않는다
