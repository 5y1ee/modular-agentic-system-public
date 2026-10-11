"""core/serialize: 저장했다가 읽은 결과가 원래와 같아야 한다(작성만 반복하려고 앞 단계 결과를 파일로 쓴다)."""
import json

from modular_agentic.core.serialize import dump_state, load_state
from modular_agentic.core.types import (
    Evidence,
    Item,
    Mapping,
    MappingStatus,
    Requirement,
    Verdict,
    VerdictKind,
)


def test_state_roundtrip_through_json():
    item = Item(id="i1", source="file", title="공고", body="본문", meta={"company": "A"})
    r1 = Requirement(id="i1-r1", item_id="i1", text="요구 한글", kind="우대사항")
    r2 = Requirement(id="i1-r2", item_id="i1", text="없는 경험")
    mappings = [
        Mapping(r1, [Evidence(ref="04_project/FINT.md:7", excerpt="발췌")], MappingStatus.PARTIAL, "이유"),
        Mapping(r2, [], MappingStatus.TODO, ""),
    ]
    verdicts = [Verdict("i1-r1", VerdictKind.FLAGGED, "과장"), Verdict("i1-r2", VerdictKind.APPROVED, "")]

    text = json.dumps(dump_state(item, mappings, verdicts), ensure_ascii=False)  # 실제 파일처럼 문자열을 거친다
    item2, mappings2, verdicts2 = load_state(json.loads(text))

    assert (item2, mappings2, verdicts2) == (item, mappings, verdicts)


def test_brief_and_source_docs_roundtrip_through_json():
    from modular_agentic.core.serialize import brief_from_dict, brief_to_dict, source_doc_from_dict, source_doc_to_dict
    from modular_agentic.core.types import Brief, Citation, Claim, ReadStatus, SourceDoc, SourceKind, TextOrigin

    unread = SourceDoc(id="d2", kind=SourceKind.WEB, locator="https://x", status=ReadStatus.UNREAD, reason="HTTP 403", found_by="검색: 질의")
    shot = SourceDoc(id="d3", kind=SourceKind.UPLOAD, locator="캡처.png", text="전사", text_origin=TextOrigin.TRANSCRIBED)
    brief = Brief(
        item_id="i1",
        subject="지원처 3.",
        claims=[Claim("c1", "사실", "사업", [Citation("d3", "전사 발췌 문장입니다")])],
        unread=[unread],
        gaps=["최근 동향"],
        warnings=["경고"],
    )
    text = json.dumps({"brief": brief_to_dict(brief), "doc": source_doc_to_dict(shot)}, ensure_ascii=False)
    data = json.loads(text)
    assert brief_from_dict(data["brief"]) == brief and source_doc_from_dict(data["doc"]) == shot
