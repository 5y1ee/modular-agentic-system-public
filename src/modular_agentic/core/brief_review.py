"""조사 보고서(Brief)의 사용자 확인 지점. 파이프라인이 멈춰서 사용자에게 Brief를 보이고, 승인된 것만 작성에 넘긴다.

왜 필요한가: Brief는 자소서의 방향을 정한다. 틀린 회사 사실 하나가 들어가면 초안 여러 개가 모두 오염된다.
초안을 하나씩 고치는 것보다 보고서에서 걸러내는 편이 싸다(docs/design-research-stage.md 9절).

흐름
  1. Researcher가 Brief를 만든다.
  2. save_record()가 Brief와 자료(SourceDoc)를 JSON으로, render_brief_md()가 읽기 좋은 마크다운으로 저장한다.
  3. 사용자가 마크다운을 읽고 틀린 주장은 drop_claims()로 지우고, 자료를 더 넣어 조사를 다시 돌린다.
  4. 사용자가 approve하면(approved=True) 그 Brief만 작성 단계가 쓴다. 지우거나 다시 돌리면 승인은 풀린다.

저장 파일은 JSON이 원본이고 마크다운은 보여 주는 용도다(마크다운을 고쳐도 반영되지 않는다).
자료 본문이 들어 있으므로 logs/ 아래에 둔다(git에 올리지 않는다).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from .serialize import brief_from_dict, brief_to_dict, source_doc_from_dict, source_doc_to_dict
from .types import Brief, SourceDoc, SourceKind, TextOrigin


@dataclass(frozen=True)
class BriefRecord:
    """저장 파일 하나의 내용: Brief, 그것을 만든 자료 전체, 사용자의 승인 여부."""

    brief: Brief
    docs: list[SourceDoc] = field(default_factory=list)
    approved: bool = False


def save_record(path: Path, record: BriefRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "approved": record.approved,
        "brief": brief_to_dict(record.brief),
        "docs": [source_doc_to_dict(d) for d in record.docs],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_record(path: Path) -> BriefRecord:
    data = json.loads(path.read_text(encoding="utf-8"))
    return BriefRecord(
        brief=brief_from_dict(data["brief"]),
        docs=[source_doc_from_dict(d) for d in data.get("docs", [])],
        approved=bool(data.get("approved", False)),
    )


def without_posting_only_claims(record: BriefRecord) -> tuple[Brief, list[str]]:
    """인용이 전부 공고 자체(SourceKind.POSTING)인 주장을 뺀 Brief와, 뺀 주장 id들.

    왜: 요구는 공고에서 뽑은 것이라, 공고에서만 나온 주장(예: "이번 공고의 주요 업무는 …")에 요구를 연결하면 "이 요구는 공고에 있다"는 되풀이다(2026-10-11,
    회사 맥락 17개 중 11개가 이랬다). 사용자가 직접 올린 파일(UPLOAD)이나 웹 자료의 주장은 요구에 없는 새 정보라 남긴다.
    """
    posting_ids = {d.id for d in record.docs if d.kind is SourceKind.POSTING}
    only_posting = [c.id for c in record.brief.claims if c.citations and all(ci.doc_id in posting_ids for ci in c.citations)]
    kept = [c for c in record.brief.claims if c.id not in set(only_posting)]
    return replace(record.brief, claims=kept), only_posting


def drop_claims(record: BriefRecord, claim_ids: list[str]) -> BriefRecord:
    """주장들을 지운다. 없는 id가 있으면 ValueError(오타로 엉뚱한 주장이 남는 것을 막으려고). 승인은 풀린다."""
    known = {c.id for c in record.brief.claims}
    unknown = [i for i in claim_ids if i not in known]
    if unknown:
        raise ValueError(f"없는 주장 id: {unknown}. 있는 id: {sorted(known)}")
    kept = [c for c in record.brief.claims if c.id not in set(claim_ids)]
    return replace(record, brief=replace(record.brief, claims=kept), approved=False)


def approve(record: BriefRecord) -> BriefRecord:
    return replace(record, approved=True)


def render_brief_md(record: BriefRecord) -> str:
    """사용자가 읽을 마크다운. 주제별로 묶고, 주장마다 출처·자료 종류·텍스트를 얻은 방식을 보인다."""
    brief = record.brief
    docs = {d.id: d for d in record.docs}
    lines = [f"# 조사 보고서: {brief.subject}", ""]
    lines.append(f"- 상태: {'승인됨' if record.approved else '**미승인** (승인 전에는 작성에 쓰이지 않는다)'}")
    lines.append(f"- 주장 {len(brief.claims)}개, 못 읽은 자료 {len(brief.unread)}건")
    lines.append("- 틀린 주장은 id로 지운다(`research_brief.py drop`). 못 읽은 자료는 파일로 받아 입력 폴더에 넣고 다시 돌린다.")
    lines.append("")

    topics: list[str] = []
    for c in brief.claims:
        if c.topic not in topics:
            topics.append(c.topic)
    for topic in topics:
        lines.append(f"## {topic or '(주제 없음)'}")
        for c in [c for c in brief.claims if c.topic == topic]:
            lines.append(f"- **[{c.id}]** {c.text}")
            for cit in c.citations:
                doc = docs.get(cit.doc_id)
                if doc is None:
                    lines.append(f"  - 출처 {cit.doc_id} (자료 기록 없음): \"{cit.quote}\"")
                    continue
                found = f" · **{doc.found_by}**" if doc.found_by else ""  # 시스템이 검색으로 찾은 자료는 표시한다
                lines.append(f"  - 출처 {doc.id} · {doc.kind.value} · {doc.locator} · {doc.text_origin.value}{found}: \"{cit.quote}\"")
                if doc.text_origin is TextOrigin.TRANSCRIBED:
                    lines.append("    - ⚠ 모델이 이미지·PDF를 읽어 옮긴 텍스트다. 원본과 다를 수 있으니 원본과 대조한다.")
        lines.append("")

    lines.append("## 못 읽은 자료")
    if brief.unread:
        lines += [f"- {d.id} · {d.kind.value} · {d.locator}{' · ' + d.found_by if d.found_by else ''} — {d.reason}" for d in brief.unread]
    else:
        lines.append("- 없음")
    lines.append("")
    lines.append("## 자료가 없어 못 다룬 주제")
    lines += [f"- {g}" for g in brief.gaps] if brief.gaps else ["- 없음"]
    if brief.warnings:
        lines += ["", "## 경고"]
        lines += [f"- {w}" for w in brief.warnings]
    lines.append("")
    return "\n".join(lines)


def approved_brief(path: Path) -> tuple[Brief | None, str]:
    """작성에 쓸 Brief와 사용자에게 보일 안내 문구. 파일이 없거나 승인 전이면 (None, 이유)."""
    if not path.exists():
        return None, f"조사 보고서가 없다({path}). 회사 사실 없이 쓴다."
    record = load_record(path)
    if not record.approved:
        return None, f"조사 보고서가 아직 승인되지 않아 쓰지 않는다({path}). 읽고 승인하면 반영된다."
    return record.brief, f"승인된 조사 보고서를 쓴다(주장 {len(record.brief.claims)}개)."
