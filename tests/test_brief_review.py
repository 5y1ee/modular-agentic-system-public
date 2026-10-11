"""core/brief_review: 조사 보고서의 사용자 확인 지점. 모델 없이 검증한다.

핵심: (1) 저장하고 다시 읽으면 같다 (2) 주장을 지우면 승인이 풀리고 없는 id는 오류다 (3) 승인된 보고서만 작성에 쓰인다
(4) 보여 주는 글에 주장 id·출처·전사 경고·못 읽은 자료·빈 주제가 들어간다.
"""
import pytest

from modular_agentic.core.brief_review import (
    BriefRecord,
    approve,
    approved_brief,
    drop_claims,
    load_record,
    render_brief_md,
    save_record,
)
from modular_agentic.core.types import (
    Brief,
    Citation,
    Claim,
    ReadStatus,
    SourceDoc,
    SourceKind,
    TextOrigin,
)

WEB = SourceDoc(id="d1", kind=SourceKind.WEB, locator="https://example.com/about", text="원문", text_origin=TextOrigin.ORIGINAL)
SHOT = SourceDoc(id="d2", kind=SourceKind.UPLOAD, locator="inputs/i1/캡처.png", text="전사", text_origin=TextOrigin.TRANSCRIBED)
BLOCKED = SourceDoc(id="d3", kind=SourceKind.WEB, locator="https://login.example.com", status=ReadStatus.UNREAD, reason="HTTP 403: 로그인이 필요함")
BRIEF = Brief(
    item_id="i1",
    subject="지원처 3.",
    claims=[
        Claim("c1", "금융 IT를 한다", "사업", [Citation("d1", "금융 IT 서비스를 제공한다")]),
        Claim("c2", "신제품을 냈다", "최근 동향", [Citation("d2", "신제품 출시")]),
    ],
    unread=[BLOCKED],
    gaps=["조직·문화 (자료에서 근거를 찾지 못함)"],
    warnings=["버린 주장이 1개 있다."],
)
RECORD = BriefRecord(brief=BRIEF, docs=[WEB, SHOT, BLOCKED])


def test_record_roundtrips_through_a_file(tmp_path):
    path = tmp_path / "briefs" / "i1.json"
    save_record(path, approve(RECORD))
    assert load_record(path) == approve(RECORD)


def test_drop_removes_claims_and_clears_approval_and_rejects_unknown_ids():
    after = drop_claims(approve(RECORD), ["c2"])
    assert [c.id for c in after.brief.claims] == ["c1"] and after.approved is False
    with pytest.raises(ValueError, match="c9"):
        drop_claims(RECORD, ["c1", "c9"])
    assert len(RECORD.brief.claims) == 2  # 원본은 바뀌지 않는다


def test_only_approved_brief_is_used_for_writing(tmp_path):
    path = tmp_path / "i1.json"
    brief, why = approved_brief(path)
    assert brief is None and "없다" in why

    save_record(path, RECORD)
    brief, why = approved_brief(path)
    assert brief is None and "승인되지 않아" in why

    save_record(path, approve(RECORD))
    brief, why = approved_brief(path)
    assert brief == BRIEF and "2개" in why


def test_rendered_markdown_shows_ids_sources_warnings_unread_and_gaps():
    md = render_brief_md(RECORD)
    assert "# 조사 보고서: 지원처 3." in md and "미승인" in md
    assert "**[c1]** 금융 IT를 한다" in md and "출처 d1 · 웹 · https://example.com/about · 원문" in md
    assert "신제품 출시" in md and "모델이 이미지·PDF를 읽어 옮긴 텍스트" in md  # 전사 자료의 주장에만 경고
    assert md.count("모델이 이미지·PDF를 읽어 옮긴 텍스트") == 1
    assert "d3 · 웹 · https://login.example.com — HTTP 403" in md
    assert "조직·문화 (자료에서 근거를 찾지 못함)" in md and "버린 주장이 1개" in md
    assert "승인됨" in render_brief_md(approve(RECORD))


def test_search_found_sources_are_marked_in_the_report():
    from dataclasses import replace

    found = replace(WEB, found_by="검색: A 최근 실적")
    blocked = replace(BLOCKED, found_by="검색: A 회사 소개")
    record = BriefRecord(brief=replace(BRIEF, unread=[blocked]), docs=[found, SHOT, blocked])
    md = render_brief_md(record)
    assert "원문 · **검색: A 최근 실적**:" in md  # 시스템이 찾은 출처
    assert "출처 d2 · 업로드 · inputs/i1/캡처.png · 전사:" in md and "캡처.png · 전사 · **" not in md  # 사용자가 준 것은 표시 없음
    assert "d3 · 웹 · https://login.example.com · 검색: A 회사 소개 — HTTP 403" in md


def test_rendering_survives_a_citation_whose_doc_is_missing():
    record = BriefRecord(brief=BRIEF, docs=[])
    assert "자료 기록 없음" in render_brief_md(record)
