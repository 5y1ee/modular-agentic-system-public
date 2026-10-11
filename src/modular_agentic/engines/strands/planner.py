"""StrandsPlanner — 문항 여러 개에 쓸 소재를 먼저 배정한다(소재 계획).

왜 필요한가: 문항을 하나씩 따로 쓰면 문항마다 가장 강한 경험을 골라서 같은 이야기가 반복된다. 소재를 먼저 정하면 문항마다
초점이 달라지고(지원 이유·역량·가치), 근거가 많은 경험을 여러 문항에 나눠 쓸 수 있다. vault의 자소서 작성 규칙(요구 / 내 근거 /
강도 / 다른 문항에서 쓴 여부 대응표를 먼저 보이고 쓴다)과 같은 절차다.

역할 분담 (모델이 지어내지 못하게 하려는 것)
  코드가 하는 일   모델이 적은 요구 id·근거 위치(경로:줄)·회사 사실 id가 입력에 있던 것인지 대조해 걸러낸다, 기본 요건(어필 안 하기로 한 것)을
                  경험에서 뺀다, 문항 번호를 맞춘다, 같은 근거 페이지가 두 문항의 중심 경험이 되면 경고한다,
                  `avoid`(다른 문항의 중심 경험 이름)를 계산해 채운다.
  모델이 하는 일   도구 없이 한 번 호출로 문항 해독, 각도, 중심 경험, 회사 사실을 정한다. 검색하지 않는다.

배정은 제안이다. 사용자가 읽고 승인한 것만 작성 단계가 쓴다(core/plan_review.py).

한계
  모델이 근거의 한계(수준이 낮음, 학습 단계, 검증이 지적한 점)를 지키는지는 코드가 확인하지 못한다. 한계는 입력에 같이 주지만 사람이 본다.
  "같은 근거 페이지"는 경로로 비교한다. 같은 페이지의 서로 다른 일화도 겹침으로 경고될 수 있다(안전한 쪽의 오탐).
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

from modular_agentic.core.types import (
    Brief,
    ContextNote,
    Item,
    Mapping,
    MappingStatus,
    Plan,
    PlannedExperience,
    QuestionPlan,
    Verdict,
    VerdictKind,
)
from modular_agentic.engines.strands.config import build_agent
from modular_agentic.engines.strands.schemas import PlanOut

SYSTEM_PROMPT = """너는 지원자의 자기소개서 소재를 문항별로 배정하는 역할이다. 입력으로 문항들, 공고의 요구와 요구별로 찾아 둔 근거, 회사 사실, 필요하면 문항 하나를 위한 추가 재료가 주어진다. 글을 쓰는 것이 아니라 "어느 문항에 어느 경험을 쓸지"를 정한다.

규칙
- 문항마다 먼저 이 문항이 실제로 묻는 것을 해독한다(reading). 소재부터 고르지 않는다. 지원 이유·커리어 계획을 묻는지, 강점 역량과 그 노력을 묻는지, 가치 하나를 실천한 경험을 묻는지에 따라 쓸 소재의 성격이 다르다.
- 문항마다 중심 경험을 1~2개 고른다(보통 2개를 깊게, 하나만 묻는 문항은 1개). 사용자가 준 재료를 다 넣지 않는다. 문항의 논지를 강화하는 경험만 고르고 약한 것은 뺀다.
- **한 경험(같은 프로젝트·사건)은 한 문항의 중심 경험으로만 쓴다.** 근거가 많은 경험을 여러 문항에 반복하지 말고, 문항마다 다른 경험을 배정한다. 근거가 겹치는 문항이 있으면 각 문항이 그 경험의 서로 다른 일화·측면만 쓰게 하고 notes에 적는다.
- 문항 3개의 각도(angle)는 서로 달라야 한다.
- 근거(refs)와 요구(requirement_ids), 회사 사실(claim_ids)은 입력에 나온 것을 글자 그대로 쓴다. 입력에 없는 경험·근거·주장을 만들지 않는다.
- "기본"으로 표시된 요구는 어필하지 않는다. 그 요구를 근거로 중심 경험을 고르지 않는다.
- 근거가 "부분확인"이거나 "한계"가 적혀 있으면 그 범위를 넘겨 쓰지 않는다. 한계가 중요하면 notes에 적는다.
- 근거가 없는 요구(TODO)로 경험을 배정하지 않는다.
- 문항이 가치 하나를 골라 쓰라고 하면 입력의 "선택 가치"를 그 가치로 쓰고, 그 가치의 뜻에 맞는 경험을 배정한다. 입력의 "추가 재료"에서 근거를 고를 수 있다.
- 근거 위치가 `사용자:`로 시작하는 것은 사용자가 직접 말한 사실이다(vault와 대조하지 않았다). 문항에 맞으면 어느 문항에든 배정할 수 있고, 그 발췌에 적힌 범위만 근거로 본다. 그 범위를 넘는 세부(기간·횟수·수치·사건)를 소재로 약속하지 않으며, 필요하면 notes에 그 한계를 적는다.
- 요구 아래의 "회사 맥락"은 그 요구가 이 회사에서 어떤 일인지 보여 주는 참고이고 지원자의 근거가 아니다. 그 맥락에 나온 주장을 회사 사실로 배정할 때는 입력의 주장 id를 글자 그대로 쓴다.
- 회사 사실(claim_ids)은 지원 이유·커리어 계획처럼 회사에 대한 이해가 필요한 문항에 주로 배정한다. 회사 이야기가 필요 없는 문항에는 비워도 된다.
- notes에는 그 문항을 쓸 때 조심할 점을 적는다."""

AgentFactory = Callable[[Sequence[Any]], Any]
PLANNER_MAX_TOKENS = 8192
EVIDENCE_PER_REQUIREMENT = 4  # 요구 하나당 보여 주는 근거 수. 넘으면 "N개 중 4개만 보임"을 적는다(발췌는 자르지 않는다: 작성이 보는 것과 같게)


_CLAIM_ID = re.compile(r"c\d+")


def _claim_id(raw: str) -> str:
    """모델이 `[주장 c3]`·`C3`처럼 적어도 c3으로 읽는다. 읽을 수 없으면 원문 그대로(그러면 입력에 없는 id로 걸러진다)."""
    found = _CLAIM_ID.search(raw.lower())
    return found.group(0) if found else raw.strip()


def _path(ref: str) -> str:
    """`경로:줄`에서 경로. 같은 페이지 비교에 쓴다."""
    return ref.rsplit(":", 1)[0] if ":" in ref and ref.rsplit(":", 1)[1].isdigit() else ref


def _render_mapping(label: str, m: Mapping, verdict: Verdict | None, *, basic: bool, number: int | None = None, limit: int | None = EVIDENCE_PER_REQUIREMENT, context: Sequence[ContextNote] = ()) -> str:
    """limit은 보여 줄 근거 수의 상한이다. 추가 재료는 None(전부)이다: 사용자가 직접 지정한 근거가 개수 제한에 조용히 잘리면 안 된다."""
    head = f"[{label} {m.requirement.id}]" + (f" (#{number})" if number is not None else "")
    lines = [f"{head} {m.requirement.text} — {m.status.value}" + (" — 기본(어필 안 함)" if basic else "")]
    if context:
        lines.append("  회사 맥락(참고. 내 근거가 아니다): " + "; ".join(f"[{n.claim_id}] {n.reason}" for n in context))
    if m.note:
        lines.append(f"  판단: {m.note}")
    if verdict is not None and verdict.kind is VerdictKind.FLAGGED:
        lines.append(f"  한계(검증이 지적함. 이 범위를 넘겨 쓰지 않는다): {verdict.reason}")
    lines += [f"  - [{e.ref}] {' '.join(e.excerpt.split())}" for e in m.evidence[:limit]]
    if limit is not None and len(m.evidence) > limit:
        lines.append(f"  (근거 {len(m.evidence)}개 중 {limit}개만 보임)")
    return "\n".join(lines)


def _render_input(
    item: Item,
    questions: Sequence[str],
    mappings: Sequence[Mapping],
    verdicts: Sequence[Verdict],
    brief: Brief | None,
    extra: Sequence[Mapping],
    basic: set[str],
    notes: str,
    variant: str,
    contexts: dict[str, list[ContextNote]] | None = None,
) -> str:
    by_req = {v.requirement_id: v for v in verdicts}
    parts = [f"공고: {item.title}"]
    if variant:
        parts.append(f"선택 가치(가치를 고르는 문항에 쓴다): {variant}")
    if notes.strip():
        parts.append(f"사용자 지시: {notes.strip()}")
    parts.append("\n문항")
    parts += [f"[문항 {i}] {q}" for i, q in enumerate(questions, 1)]
    parts.append("\n요구와 근거")
    for i, m in enumerate(mappings, 1):
        parts.append(_render_mapping("요구", m, by_req.get(m.requirement.id), basic=m.requirement.id in basic, number=i, context=(contexts or {}).get(m.requirement.id, ())))
    if extra:
        parts.append("\n추가 재료(요구에 대한 근거가 아니다. 가치 문항을 위한 근거, 사용자가 지정한 근거, 사용자가 직접 말한 사실이 들어 있다)")
        parts += [_render_mapping("추가 재료", m, by_req.get(m.requirement.id), basic=False, limit=None) for m in extra]
    if brief is not None:
        parts.append(f"\n회사 사실({brief.subject}) — 사용자가 확인한 조사 결과")
        parts += [f"[주장 {c.id}] ({c.topic}) {c.text}" for c in brief.claims]
    return "\n".join(parts)


class StrandsPlanner:
    """core.stages.Planner를 Strands 에이전트로 구현한다."""

    def __init__(self, *, agent_factory: AgentFactory | None = None) -> None:
        self._agent_factory = agent_factory or (lambda tools: build_agent(SYSTEM_PROMPT, tools, PLANNER_MAX_TOKENS))

    def plan(
        self,
        item: Item,
        questions: list[str],
        mappings: list[Mapping],
        verdicts: list[Verdict],
        *,
        brief: Brief | None = None,
        extra: Sequence[Mapping] = (),
        basic_requirement_ids: Sequence[str] = (),
        notes: str = "",
        variant: str = "",
        contexts: dict[str, list[ContextNote]] | None = None,
    ) -> Plan:
        basic = set(basic_requirement_ids)
        agent = self._agent_factory([])
        result = agent(_render_input(item, questions, mappings, verdicts, brief, extra, basic, notes, variant, contexts), structured_output_model=PlanOut)
        out: PlanOut = result.structured_output

        usable = [m for m in mappings if m.status is not MappingStatus.TODO and m.evidence]
        known_reqs = {m.requirement.id for m in usable} - basic
        known_refs = {e.ref for m in list(mappings) + list(extra) for e in m.evidence}
        known_claims = {c.id for c in brief.claims} if brief is not None else set()

        warnings: list[str] = []
        by_number = {q.question_number: q for q in out.questions}
        plans: list[QuestionPlan] = []
        for number, question in enumerate(questions, 1):
            raw = by_number.get(number)
            if raw is None:
                warnings.append(f"문항 {number}의 배정을 모델이 내지 않았다.")
                plans.append(QuestionPlan(number=number, question=question, reading="", angle=""))
                continue
            experiences: list[PlannedExperience] = []
            for exp in raw.experiences:
                refs = [r for r in dict.fromkeys(exp.refs) if r in known_refs]
                req_ids = [i for i in dict.fromkeys(exp.requirement_ids) if i in known_reqs]
                dropped_refs = len(set(exp.refs)) - len(refs)
                if dropped_refs:
                    warnings.append(f"문항 {number} '{exp.name}': 입력에 없는 근거 위치 {dropped_refs}개를 뺐다.")
                if len(set(exp.requirement_ids)) - len(req_ids):
                    warnings.append(f"문항 {number} '{exp.name}': 기본 요건이거나 근거 없는 요구를 가리키는 id를 뺐다.")
                if not refs:
                    warnings.append(f"문항 {number} '{exp.name}': 쓸 수 있는 근거가 하나도 남지 않아 중심 경험에서 뺐다.")
                    continue
                experiences.append(PlannedExperience(name=exp.name.strip(), why=exp.why.strip(), refs=refs, requirement_ids=req_ids))
            normalized = list(dict.fromkeys(_claim_id(c) for c in raw.claim_ids))
            claim_ids = [c for c in normalized if c in known_claims]
            unknown_claims = [c for c in normalized if c not in known_claims]
            if unknown_claims:
                warnings.append(f"문항 {number}: 입력에 없는 회사 사실 id {unknown_claims}를 뺐다.")
            if not experiences:
                warnings.append(f"문항 {number}에 배정된 중심 경험이 없다.")
            plans.append(
                QuestionPlan(number=number, question=question, reading=raw.reading.strip(), angle=raw.angle.strip(), experiences=experiences, claim_ids=claim_ids, notes=raw.notes.strip())
            )

        # 같은 근거 페이지가 두 문항 이상의 중심 경험이면 경고한다.
        used_in: dict[str, set[int]] = defaultdict(set)
        for q in plans:
            for exp in q.experiences:
                for ref in exp.refs:
                    used_in[_path(ref)].add(q.number)
        for path, numbers in sorted(used_in.items()):
            if len(numbers) > 1:
                warnings.append(f"문항 {sorted(numbers)}이 같은 근거 페이지 '{path}'를 중심 경험으로 쓴다. 서로 다른 일화인지 확인한다.")

        # 다른 문항의 중심 경험은 이 문항에서 쓰지 않는다(이름으로 알린다).
        final = [
            QuestionPlan(
                number=q.number,
                question=q.question,
                reading=q.reading,
                angle=q.angle,
                experiences=q.experiences,
                claim_ids=q.claim_ids,
                avoid=[e.name for other in plans if other.number != q.number for e in other.experiences],
                notes=q.notes,
            )
            for q in plans
        ]
        return Plan(item_id=item.id, variant=variant, questions=final, basic_requirement_ids=sorted(basic), warnings=warnings)
