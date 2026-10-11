"""소재 계획(Plan)의 사용자 확인 지점. 조사 보고서(brief_review.py)와 같은 방식이다.

소재 계획은 문항마다 어떤 경험을 쓸지 정하므로 틀리면 글 전체가 틀어진다. 사용자가 읽고 승인한 것만 작성이 쓴다.
저장 파일은 JSON이 원본(logs/04_plan/<공고>__<이름>.json)이고 마크다운은 읽는 용이다.

읽는 용 문서의 첫 표는 vault 자소서 작성 규칙의 대응표(요구 / 내 근거 / 강도 / 다른 문항에서 쓴 여부)다.
표는 모델이 아니라 코드가 만든다(어느 문항의 중심 경험이 가리키는 요구인지를 모아 쓴다).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from .serialize import plan_from_dict, plan_to_dict
from .types import Brief, Mapping, Plan, Verdict, VerdictKind

REQUIREMENT_TABLE_CHARS = 60  # 소재 계획 표에 보이는 요구 문구의 최대 글자 수. 넘으면 단어 끝에서 자르고 …를 붙인다


@dataclass(frozen=True)
class PlanRecord:
    plan: Plan
    approved: bool = False


def slug(text: str) -> str:
    """파일 이름에 쓸 수 있게 공백·특수문자를 -로 바꾼다."""
    cleaned = "".join(c if c.isalnum() else "-" for c in text.strip())
    return "-".join(part for part in cleaned.split("-") if part) or "plan"


def plan_path(directory: Path, item_id: str, variant: str) -> Path:
    return directory / f"{item_id}__{slug(variant)}.json"


def save_record(path: Path, record: PlanRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"approved": record.approved, "plan": plan_to_dict(record.plan)}, ensure_ascii=False, indent=2), encoding="utf-8")


def load_record(path: Path) -> PlanRecord:
    data = json.loads(path.read_text(encoding="utf-8"))
    return PlanRecord(plan=plan_from_dict(data["plan"]), approved=bool(data.get("approved", False)))


def approve(record: PlanRecord) -> PlanRecord:
    return replace(record, approved=True)


def render_plan_md(record: PlanRecord, mappings: list[Mapping], verdicts: list[Verdict], brief: Brief | None = None) -> str:
    plan = record.plan
    flagged = {v.requirement_id for v in verdicts if v.kind is VerdictKind.FLAGGED}
    claims = {c.id: c for c in brief.claims} if brief is not None else {}
    used_by: dict[str, list[int]] = {}
    for q in plan.questions:
        for exp in q.experiences:
            for rid in exp.requirement_ids:
                used_by.setdefault(rid, []).append(q.number)

    title = f"소재 계획: {plan.variant}" if plan.variant else "소재 계획"
    lines = [f"# {title} — {plan.item_id}", "", f"- 상태: {'승인됨' if record.approved else '**미승인** (승인 전에는 작성에 쓰이지 않는다)'}", ""]

    lines += ["## 요구 대응표 (요구 / 강도 / 내 근거 / 쓰는 문항)", "", "| # | 요구 | 강도 | 대표 근거 | 쓰는 곳 |", "|---|---|---|---|---|"]
    basic = set(plan.basic_requirement_ids)
    for i, m in enumerate(mappings, 1):
        rid = m.requirement.id
        strength = m.status.value + (" ⚑" if rid in flagged else "")
        where = "기본(어필 안 함)" if rid in basic else (", ".join(f"문항 {n}" for n in sorted(set(used_by.get(rid, [])))) or "쓰지 않음")
        ref = f"`{m.evidence[0].ref}`" if m.evidence else "없음"
        text = m.requirement.text.strip()
        text = text if len(text) <= REQUIREMENT_TABLE_CHARS else text[:REQUIREMENT_TABLE_CHARS].rsplit(" ", 1)[0] + " …"
        lines.append(f"| {i} | {text} | {strength} | {ref} | {where} |")
    lines.append("")

    for q in plan.questions:
        lines += [f"## 문항 {q.number}", "", f"> {q.question}", ""]
        lines += [f"- **문항이 묻는 것**: {q.reading or '(없음)'}", f"- **각도**: {q.angle or '(없음)'}"]
        lines.append("- **중심 경험**" + ("" if q.experiences else ": 없음"))
        for n, exp in enumerate(q.experiences, 1):
            lines.append(f"  {n}. **{exp.name}** — {exp.why}")
            lines.append("     - 근거: " + ", ".join(f"`{r}`" for r in exp.refs))
            if exp.requirement_ids:
                nums = [str(i) for i, m in enumerate(mappings, 1) if m.requirement.id in exp.requirement_ids]
                lines.append(f"     - 뒷받침하는 요구: #{', #'.join(nums)}" if nums else "     - 뒷받침하는 요구: 없음")
        if q.claim_ids:
            lines.append("- **쓸 회사 사실**")
            lines += [f"  - **[{cid}]** {claims[cid].text}" if cid in claims else f"  - [{cid}]" for cid in q.claim_ids]
        if q.avoid:
            lines.append("- **이 문항에서 쓰지 않을 소재(다른 문항 몫)**: " + ", ".join(q.avoid))
        if q.notes:
            lines.append(f"- **조심할 점**: {q.notes}")
        lines.append("")

    if plan.warnings:
        lines += ["## 경고", ""] + [f"- {w}" for w in plan.warnings] + [""]
    return "\n".join(lines)


def approved_plan(path: Path) -> tuple[Plan | None, str]:
    """작성에 쓸 소재 계획과 안내 문구. 파일이 없거나 승인 전이면 (None, 이유)."""
    if not path.exists():
        return None, f"소재 계획이 없다({path})."
    record = load_record(path)
    if not record.approved:
        return None, f"소재 계획이 아직 승인되지 않아 쓰지 않는다({path})."
    return record.plan, "승인된 소재 계획을 쓴다."
