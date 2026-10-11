"""core 타입을 JSON으로 저장하고 다시 읽는다.

왜 필요한가: 분석·매핑·검증은 시간과 비용이 든다(요구 8개에 약 3분 30초 추정, Bedrock 호출). 작성은 사용자 피드백을 받아
여러 번 다시 쓰는 단계라, 앞 단계 결과를 저장해 두고 작성만 반복해야 한다.

저장하는 것: Item, Requirement 목록, Mapping 목록, Verdict 목록. Draft는 저장하지 않는다(피드백 기록은 따로).
조사 보고서(Brief)와 그 자료(SourceDoc)도 여기서 JSON으로 바꾼다(저장 파일의 구성은 brief_review.py).
"""
from __future__ import annotations

from typing import Any

from .types import (
    Analysis,
    Brief,
    Citation,
    Claim,
    ContextNote,
    Evidence,
    Item,
    Mapping,
    MappingStatus,
    Plan,
    PlannedExperience,
    QuestionPlan,
    ReadStatus,
    Requirement,
    SourceDoc,
    SourceKind,
    TextOrigin,
    Verdict,
    VerdictKind,
)


def item_to_dict(item: Item) -> dict[str, Any]:
    return {"id": item.id, "source": item.source, "title": item.title, "body": item.body, "meta": dict(item.meta)}


def item_from_dict(d: dict[str, Any]) -> Item:
    return Item(id=d["id"], source=d["source"], title=d["title"], body=d["body"], meta=dict(d.get("meta", {})))


def requirement_to_dict(r: Requirement) -> dict[str, Any]:
    return {"id": r.id, "item_id": r.item_id, "text": r.text, "kind": r.kind}


def requirement_from_dict(d: dict[str, Any]) -> Requirement:
    return Requirement(id=d["id"], item_id=d["item_id"], text=d["text"], kind=d.get("kind", ""))


def mapping_to_dict(m: Mapping) -> dict[str, Any]:
    return {
        "requirement": requirement_to_dict(m.requirement),
        "evidence": [{"ref": e.ref, "excerpt": e.excerpt} for e in m.evidence],
        "status": m.status.value,
        "note": m.note,
    }


def mapping_from_dict(d: dict[str, Any]) -> Mapping:
    return Mapping(
        requirement=requirement_from_dict(d["requirement"]),
        evidence=[Evidence(ref=e["ref"], excerpt=e["excerpt"]) for e in d["evidence"]],
        status=MappingStatus(d["status"]),
        note=d.get("note", ""),
    )


def verdict_to_dict(v: Verdict) -> dict[str, Any]:
    return {"requirement_id": v.requirement_id, "kind": v.kind.value, "reason": v.reason}


def verdict_from_dict(d: dict[str, Any]) -> Verdict:
    return Verdict(requirement_id=d["requirement_id"], kind=VerdictKind(d["kind"]), reason=d.get("reason", ""))


def dump_state(item: Item, mappings: list[Mapping], verdicts: list[Verdict]) -> dict[str, Any]:
    """분석·매핑·검증 결과를 JSON으로 쓸 수 있는 dict로 만든다. 요구 목록은 매핑에 들어 있다."""
    return {
        "item": item_to_dict(item),
        "mappings": [mapping_to_dict(m) for m in mappings],
        "verdicts": [verdict_to_dict(v) for v in verdicts],
    }


def load_state(d: dict[str, Any]) -> tuple[Item, list[Mapping], list[Verdict]]:
    return (
        item_from_dict(d["item"]),
        [mapping_from_dict(m) for m in d["mappings"]],
        [verdict_from_dict(v) for v in d["verdicts"]],
    )


def source_doc_to_dict(d: SourceDoc) -> dict[str, Any]:
    return {
        "id": d.id,
        "kind": d.kind.value,
        "locator": d.locator,
        "text": d.text,
        "text_origin": d.text_origin.value,
        "status": d.status.value,
        "reason": d.reason,
        "found_by": d.found_by,
    }


def source_doc_from_dict(d: dict[str, Any]) -> SourceDoc:
    return SourceDoc(
        id=d["id"],
        kind=SourceKind(d["kind"]),
        locator=d["locator"],
        text=d.get("text", ""),
        text_origin=TextOrigin(d.get("text_origin", TextOrigin.ORIGINAL.value)),
        status=ReadStatus(d.get("status", ReadStatus.READ.value)),
        reason=d.get("reason", ""),
        found_by=d.get("found_by", ""),
    )


def claim_to_dict(c: Claim) -> dict[str, Any]:
    return {
        "id": c.id,
        "text": c.text,
        "topic": c.topic,
        "citations": [{"doc_id": x.doc_id, "quote": x.quote} for x in c.citations],
    }


def claim_from_dict(d: dict[str, Any]) -> Claim:
    return Claim(
        id=d["id"],
        text=d["text"],
        topic=d.get("topic", ""),
        citations=[Citation(doc_id=x["doc_id"], quote=x["quote"]) for x in d["citations"]],
    )


def brief_to_dict(b: Brief) -> dict[str, Any]:
    return {
        "item_id": b.item_id,
        "subject": b.subject,
        "claims": [claim_to_dict(c) for c in b.claims],
        "unread": [source_doc_to_dict(d) for d in b.unread],
        "gaps": list(b.gaps),
        "warnings": list(b.warnings),
    }


def brief_from_dict(d: dict[str, Any]) -> Brief:
    return Brief(
        item_id=d["item_id"],
        subject=d.get("subject", ""),
        claims=[claim_from_dict(c) for c in d.get("claims", [])],
        unread=[source_doc_from_dict(x) for x in d.get("unread", [])],
        gaps=list(d.get("gaps", [])),
        warnings=list(d.get("warnings", [])),
    )


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    return {
        "item_id": plan.item_id,
        "variant": plan.variant,
        "basic_requirement_ids": list(plan.basic_requirement_ids),
        "warnings": list(plan.warnings),
        "questions": [
            {
                "number": q.number,
                "question": q.question,
                "reading": q.reading,
                "angle": q.angle,
                "claim_ids": list(q.claim_ids),
                "avoid": list(q.avoid),
                "notes": q.notes,
                "experiences": [
                    {"name": e.name, "why": e.why, "refs": list(e.refs), "requirement_ids": list(e.requirement_ids)} for e in q.experiences
                ],
            }
            for q in plan.questions
        ],
    }


def plan_from_dict(d: dict[str, Any]) -> Plan:
    return Plan(
        item_id=d["item_id"],
        variant=d.get("variant", ""),
        basic_requirement_ids=list(d.get("basic_requirement_ids", [])),
        warnings=list(d.get("warnings", [])),
        questions=[
            QuestionPlan(
                number=q["number"],
                question=q["question"],
                reading=q.get("reading", ""),
                angle=q.get("angle", ""),
                claim_ids=list(q.get("claim_ids", [])),
                avoid=list(q.get("avoid", [])),
                notes=q.get("notes", ""),
                experiences=[
                    PlannedExperience(name=e["name"], why=e.get("why", ""), refs=list(e.get("refs", [])), requirement_ids=list(e.get("requirement_ids", [])))
                    for e in q.get("experiences", [])
                ],
            )
            for q in d["questions"]
        ],
    )


def analysis_to_dict(a: Analysis) -> dict[str, Any]:
    return {
        "item_id": a.item_id,
        "requirements": [requirement_to_dict(r) for r in a.requirements],
        "basic_requirement_ids": list(a.basic_requirement_ids),
        "context": {rid: [{"claim_id": n.claim_id, "reason": n.reason} for n in notes] for rid, notes in a.context.items()},
        "warnings": list(a.warnings),
    }


def analysis_from_dict(d: dict[str, Any]) -> Analysis:
    return Analysis(
        item_id=d["item_id"],
        requirements=[requirement_from_dict(r) for r in d["requirements"]],
        basic_requirement_ids=list(d.get("basic_requirement_ids", [])),
        context={rid: [ContextNote(n["claim_id"], n["reason"]) for n in notes] for rid, notes in d.get("context", {}).items()},
        warnings=list(d.get("warnings", [])),
    )

