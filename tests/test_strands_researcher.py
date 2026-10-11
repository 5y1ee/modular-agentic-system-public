"""Strands Researcher·Transcriber 테스트. 가짜 에이전트로 AWS 없이 검증한다.

핵심: (1) 읽을 자료가 없으면 모델을 부르지 않는다 (2) 모델이 낸 인용이 자료에 실제로 있어야 주장이 남는다(공백 차이는 무시)
(3) 인용이 안 맞는 주장·모르는 자료 id·너무 짧은 인용은 버리고 경고를 남긴다 (4) 주장 id는 코드가 붙인다
(5) 주장이 하나도 없는 주제는 gaps에 오른다 (6) 못 읽은 자료는 Brief.unread로 남는다 (7) 길어서 잘린 자료는 경고하고 잘린 뒤 기준으로 대조한다
(8) Researcher는 도구를 쓰지 않는다 (9) Transcriber는 이미지·문서 블록을 모델에 넘긴다.
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("strands")

from modular_agentic.core.types import Item, ReadStatus, SourceDoc, SourceKind, TextOrigin  # noqa: E402
from modular_agentic.engines.strands import researcher as researcher_module  # noqa: E402
from modular_agentic.engines.strands.researcher import StrandsResearcher, subject_of  # noqa: E402
from modular_agentic.engines.strands.schemas import BriefOut, CitationOut, ClaimOut  # noqa: E402
from modular_agentic.engines.strands.transcriber import StrandsTranscriber  # noqa: E402

ITEM = Item(id="i1", source="t", title="지원처 3. AX 공고", body="본문", meta={"company": "지원처 3."})
TOPICS = ("사업·주력 제품", "최근 동향")
DOC_TEXT = "지원처 3.는 금융 IT 서비스를\n제공한다.   2025년에는 AI 에이전트 플랫폼을 출시했다."
DOC = SourceDoc(id="d1", kind=SourceKind.WEB, locator="https://example.com/about", text=DOC_TEXT)


def claim(text, topic, *citations):
    return ClaimOut(text=text, topic=topic, citations=[CitationOut(doc_id=d, quote=q) for d, q in citations])


def fake_factory(out: BriefOut, prompts: list[str]):
    class FakeAgent:
        def __init__(self, tools):
            assert tools == []  # Researcher는 도구를 쓰지 않는다

        def __call__(self, prompt, structured_output_model=None):
            prompts.append(prompt)
            assert structured_output_model is BriefOut
            return SimpleNamespace(structured_output=out)

    return FakeAgent


def research(out: BriefOut, docs=(DOC,), item=ITEM):
    prompts: list[str] = []
    brief = StrandsResearcher(topics=TOPICS, agent_factory=fake_factory(out, prompts)).research(item, list(docs))
    return brief, prompts


def test_no_readable_docs_means_no_model_call_and_everything_is_a_gap():
    class Boom:
        def __init__(self, tools):
            raise AssertionError("모델을 부르면 안 된다")

    unread = SourceDoc(id="d1", kind=SourceKind.WEB, locator="https://x", status=ReadStatus.UNREAD, reason="HTTP 403")
    brief = StrandsResearcher(topics=TOPICS, agent_factory=Boom).research(ITEM, [unread])
    assert brief.claims == [] and brief.unread == [unread] and brief.gaps == list(TOPICS) and brief.warnings


def test_valid_quote_survives_even_when_whitespace_differs_and_ids_are_assigned_by_code():
    out = BriefOut(
        claims=[
            claim("금융 IT 서비스를 제공한다", "사업·주력 제품", ("d1", "지원처 3.는 금융 IT 서비스를 제공한다.")),
            claim("2025년에 AI 에이전트 플랫폼을 출시했다", "최근 동향", ("d1", "2025년에는 AI 에이전트 플랫폼을 출시했다.")),
        ],
        gaps=[],
    )
    brief, _ = research(out)
    assert [c.id for c in brief.claims] == ["c1", "c2"]
    assert brief.claims[0].citations[0].quote == "지원처 3.는 금융 IT 서비스를 제공한다."  # 줄바꿈이 정규화된 모양으로 남는다
    assert brief.gaps == [] and brief.warnings == [] and brief.subject == "지원처 3."


def test_claims_with_bad_quotes_unknown_docs_or_too_short_quotes_are_dropped_with_a_warning():
    out = BriefOut(
        claims=[
            claim("지어낸 사실", "사업·주력 제품", ("d1", "지원처 3.는 우주 사업을 한다고 발표했다.")),
            claim("없는 자료", "사업·주력 제품", ("d9", "지원처 3.는 금융 IT 서비스를 제공한다.")),
            claim("너무 짧은 인용", "사업·주력 제품", ("d1", "금융 IT")),
            claim("멀쩡한 주장", "최근 동향", ("d1", "2025년에는 AI 에이전트 플랫폼을 출시했다.")),
        ],
        gaps=[],
    )
    brief, _ = research(out)
    assert [(c.id, c.text) for c in brief.claims] == [("c1", "멀쩡한 주장")]
    assert any("버린 주장이 3개" in w for w in brief.warnings)
    listed = [w for w in brief.warnings if w.startswith("버린 주장: ")]
    assert len(listed) == 3  # 무엇을 버렸는지 남는다
    assert '버린 주장: 지어낸 사실 — 인용: "지원처 3.는 우주 사업을 한다고 발표했다."' in listed  # 왜 안 맞는지 보이게 인용도 남는다
    assert any(w.startswith("버린 주장: 없는 자료") for w in listed) and any(w.startswith("버린 주장: 너무 짧은 인용") for w in listed)
    assert "사업·주력 제품 (자료에서 근거를 찾지 못함)" in brief.gaps  # 주장이 다 버려진 주제는 빈 곳으로 드러난다


def _research_with_quote(text, quote):
    doc = SourceDoc(id="d1", kind=SourceKind.WEB, locator="https://x", text=text)
    out = BriefOut(claims=[claim("주장", "사업·주력 제품", ("d1", quote))], gaps=[])
    brief, _ = research(out, docs=[doc])
    return brief


def test_quote_notation_differences_do_not_drop_a_true_claim():
    # 곡선 따옴표를 직선으로, 위키 각주 번호와 마크다운 강조를 빼고 옮긴 경우(실제로 있었던 사례)
    assert _research_with_quote("플랫폼 ‘모빌진’을 전 차종에 확대 적용한다", "플랫폼 '모빌진'을 전 차종에 확대 적용한다").claims
    assert _research_with_quote("신임 대표 류석문 전무[4]의 여러 행보로 인해", "신임 대표 류석문 전무의 여러 행보로 인해").claims
    assert _research_with_quote("ACT *10/18 응시 **코딩테스트** *10/25 응시", "ACT *10/18 응시 코딩테스트 *10/25 응시").claims


def test_ellipsis_quotes_need_every_fragment_in_order_and_long_enough():
    text = "회사는 금융 IT 서비스를 제공한다. 중간에 다른 이야기가 있다. 2025년에는 플랫폼을 출시했다."
    assert _research_with_quote(text, "금융 IT 서비스를 제공한다 … 2025년에는 플랫폼을 출시했다").claims  # 조각이 순서대로 있다
    assert not _research_with_quote(text, "2025년에는 플랫폼을 출시했다 … 금융 IT 서비스를 제공한다").claims  # 순서가 바뀜
    assert not _research_with_quote(text, "금융 IT 서비스를 제공한다 … 우주 사업을 한다").claims  # 없는 조각
    assert not _research_with_quote(text, "금융 IT 서비스를 제공한다 … IT … 플랫폼을 출시했다").claims  # 너무 짧은 조각(어디에나 맞는다)
    assert _research_with_quote(text, "금융 IT 서비스를 제공한다...").claims  # 끝의 생략 표시


def test_changed_separators_still_drop_the_claim():
    # 목록 기호를 바꿔 쓴 인용은 글자가 달라진 것이라 계속 버린다(보정하지 않는다)
    assert not _research_with_quote("Product Manager - Financial Analysis - HRM", "Product Manager / Financial Analysis / HRM").claims


def test_a_claim_keeps_only_its_valid_citations():
    out = BriefOut(
        claims=[claim("금융 IT", "사업·주력 제품", ("d1", "지원처 3.는 금융 IT 서비스를 제공한다."), ("d1", "존재하지 않는 문장입니다 정말로."))],
        gaps=[],
    )
    brief, _ = research(out)
    assert len(brief.claims[0].citations) == 1
    assert any("뺀 인용이 1개" in w for w in brief.warnings)


def test_model_gaps_are_kept_and_uncovered_topics_are_added_once():
    out = BriefOut(claims=[claim("금융 IT", "사업·주력 제품", ("d1", "지원처 3.는 금융 IT 서비스를 제공한다."))], gaps=["최근 동향: 자료에 날짜가 없음"])
    brief, _ = research(out)
    assert brief.gaps == ["최근 동향: 자료에 날짜가 없음"]  # 모델이 이미 말한 주제를 중복해서 더하지 않는다


def test_unread_docs_are_listed_and_blank_read_docs_count_as_unread():
    blank = SourceDoc(id="d2", kind=SourceKind.UPLOAD, locator="a.png", text="  ", text_origin=TextOrigin.TRANSCRIBED)
    failed = SourceDoc(id="d3", kind=SourceKind.WEB, locator="https://x", status=ReadStatus.UNREAD, reason="HTTP 403")
    out = BriefOut(claims=[claim("금융 IT", "사업·주력 제품", ("d1", "지원처 3.는 금융 IT 서비스를 제공한다."))], gaps=[])
    brief, prompts = research(out, docs=[DOC, blank, failed])
    assert {d.id for d in brief.unread} == {"d2", "d3"}
    assert all(d.status is ReadStatus.UNREAD for d in brief.unread)
    assert "[자료 d1" in prompts[0] and "[자료 d2" not in prompts[0] and "[자료 d3" not in prompts[0]


def test_prompt_labels_each_doc_with_kind_origin_and_place():
    shot = SourceDoc(id="d2", kind=SourceKind.UPLOAD, locator="inputs/캡처.png", text="캡처에서 옮긴 글", text_origin=TextOrigin.TRANSCRIBED)
    _, prompts = research(BriefOut(claims=[], gaps=[]), docs=[DOC, shot])
    assert "조사 주제: 사업·주력 제품, 최근 동향" in prompts[0]
    assert "[자료 d1 | 웹 | 원문 | https://example.com/about]" in prompts[0]
    assert "[자료 d2 | 업로드 | 전사 | inputs/캡처.png]" in prompts[0]


def test_long_input_is_truncated_with_a_warning_and_quotes_are_checked_against_what_the_model_saw(monkeypatch):
    monkeypatch.setattr(researcher_module, "MAX_INPUT_CHARS", 60)
    text = "앞부분에는 금융 IT 서비스를 제공한다고 적혀 있다. " + "가" * 200 + " 뒷부분에는 해외 진출을 발표했다고 적혀 있다."
    doc = SourceDoc(id="d1", kind=SourceKind.WEB, locator="https://x", text=text)
    out = BriefOut(
        claims=[
            claim("앞부분 사실", "사업·주력 제품", ("d1", "앞부분에는 금융 IT 서비스를 제공한다고 적혀 있다.")),
            claim("잘린 뒷부분 사실", "최근 동향", ("d1", "뒷부분에는 해외 진출을 발표했다고 적혀 있다.")),
        ],
        gaps=[],
    )
    brief, _ = research(out, docs=[doc])
    assert [c.text for c in brief.claims] == ["앞부분 사실"]
    assert any("잘라" in w and "d1" in w for w in brief.warnings)


def test_subject_prefers_company_meta_then_korean_key_then_title():
    assert subject_of(Item(id="i", source="t", title="공고", body="b", meta={"company": "A"})) == "A"
    assert subject_of(Item(id="i", source="t", title="공고", body="b", meta={"회사": "B"})) == "B"
    assert subject_of(Item(id="i", source="t", title="공고", body="b")) == "공고"


# ── Transcriber ──


def test_transcriber_sends_image_and_document_blocks_and_returns_the_text():
    seen: list[list] = []

    class FakeAgent:
        def __init__(self, tools):
            assert tools == []

        def __call__(self, prompt):
            seen.append(prompt)
            return "  옮긴 글  "

    t = StrandsTranscriber(agent_factory=FakeAgent)
    assert t.transcribe(b"img", kind="image", fmt="png") == "옮긴 글"
    assert t.transcribe(b"pdf", kind="document", fmt="pdf") == "옮긴 글"
    assert seen[0][1] == {"image": {"format": "png", "source": {"bytes": b"img"}}}
    assert seen[1][1] == {"document": {"format": "pdf", "name": "document", "source": {"bytes": b"pdf"}}}
    assert "text" in seen[0][0]

    with pytest.raises(ValueError):
        t.transcribe(b"x", kind="audio", fmt="mp3")
