"""StrandsWriter — 매핑·검증 결과를 재료로 문항에 대한 답변 초안을 여러 개 쓴다.

역할 분담 (모델이 지어내지 못하게 하려는 것)
  코드가 하는 일   쓸 수 있는 근거(확인·부분확인)와 TODO를 가르고 플래그된 매핑에 한계를 붙인다,
                  모델이 표시한 요구 id·근거 ref가 입력에 있던 것인지 대조한다, 분량 초과와 한계가 있는 요구의 사용을 경고한다,
                  TODO 목록을 만든다.
  모델이 하는 일   도구 없이 한 번 호출로 초안 n개를 서로 다른 각도로 쓴다. 검색은 하지 않는다.

재료
  usable   확인·부분확인 매핑. 초안은 이 근거만 사실로 쓸 수 있다. 검증이 승인했거나 결과가 없으면 그대로 쓴다.
           검증이 플래그한 매핑도 재료다(2026-10-04 변경): 플래그는 "경험이 거짓"이 아니라 "서술이 실제보다 크게 읽힌다"는 뜻이라
           통째로 빼면 사실인 소재까지 사라진다(실험 002 9절: 가장 강한 소재가 초안 3개에서 모두 빠졌다).
           대신 플래그 이유를 **한계**로 붙여 "이 범위를 넘겨 쓰지 않는다"는 제약으로 모델에게 준다.
  todo     근거가 없는 매핑. 초안에 쓰지 않는다. Draft.todos로 사용자에게 돌려준다.

"더 뽑기"와 "요구 선택"
  previous에 이미 낸 초안을 주면 그것들과 다른 각도로 쓴다. focus에 요구 id들을 주면 그 요구만 재료로 쓴다.

brief (조사 보고서, docs/design-research-stage.md 10절)
  사용자가 승인한 Brief를 주면 그 주장들을 "회사 사실"로, vault 근거를 "내 경험"으로 나눠 보여 준다. 회사에 대한 서술은 주장에서만 가져온다.
  모델이 적은 주장 id는 입력에 있던 것만 인정하고 Draft.used_claims에 남긴다. brief가 None이면 이전과 똑같이 동작한다.
  Brief는 있는데 쓸 수 있는 주장이 하나도 없으면 "회사 사실을 쓰지 않는다"를 프롬프트에 적고 경고를 남긴다.
  (문항이 지원동기인지 코드로 알아내지 않는다. 어투·문항 종류를 코드로 감지하는 것은 하드코딩이라 쓰지 않기로 했다.)

guidance
  사용자 피드백에서 정리한 작성 지침(문체·구조 선호). 시스템 프롬프트 끝에 붙는다. 비어 있으면 붙지 않는다.

한계
  초안 문장 속 세부 사실이 근거 발췌와 맞는지, 모델이 한계를 실제로 지켰는지는 코드가 확인하지 못한다. 사람이 대조해야 한다.
  한계가 있는 요구를 초안이 다루면 그 사실을 경고로 남겨 사람이 보게 한다.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from modular_agentic.core.types import (
    Brief,
    Draft,
    Evidence,
    Item,
    Mapping,
    MappingStatus,
    QuestionPlan,
    Requirement,
    Verdict,
    VerdictKind,
)
from modular_agentic.engines.strands.config import build_agent
from modular_agentic.engines.strands.schemas import DraftsOut

SYSTEM_PROMPT = """너는 지원자의 자기소개서 답변 초안을 쓰는 역할이다. 입력으로 문항, 공고의 요구들과 요구별로 찾아 둔 근거가 주어진다.

규칙
- 근거에 적힌 사실만 쓴다. 근거에 없는 경험·수치·기술을 지어내지 않는다.
- 판단이 "부분확인"인 근거는 판단 이유에 적힌 한계를 넘겨 쓰지 않는다(예: 연동만 한 것을 직접 개발했다고 쓰지 않는다).
- 요구에 "한계"가 적혀 있으면 그것은 검증이 지적한 사실이다. 근거는 쓰되 한계를 넘겨 쓰지 않는다(예: 한계가 "인턴 경험이다"이면 인턴으로, "개발 중이다"이면 개발 중으로 쓴다). 한계가 가리키는 부풀림이 들어가면 안 된다.
- 근거에 적히지 않은 동기·결심·인과·시점·역할을 만들지 않는다. "졸업 후", "이 경험을 계기로", "그래서 ~하게 됐다" 같은 연결은 근거에 있을 때만 쓴다. 근거에 없는 서사는 쓰지 않고 사용자가 채울 부분으로 남긴다. 직책·기간·고용 형태(인턴, 팀장, 개인 프로젝트 등)는 근거에 적힌 그대로 쓴다.
- 입력에 "회사 사실"이 있으면 회사에 대한 서술(사업·제품·동향·문화 등)은 거기 적힌 주장(`[주장 <id>]`)에서만 가져온다. 주장에 없는 회사 이야기는 지어내지 않는다. 문항이 회사에 대해 묻지 않으면 쓰지 않아도 된다. 쓴 주장의 id는 used_claim_ids에 적는다. "회사 사실: 없음"이면 회사에 대한 사실을 쓰지 않는다.
- 근거에 적히지 않은 내면(확신이 생겼다, 체감했다, 부담이 있었다, 깨달았다 같은 감정·인식의 변화)과 지어낸 생각의 인용을 쓰지 않는다. 배운 점은 근거의 사실에서 곧바로 도출되는 한 문장으로만 쓴다.
- 근거 위치가 `사용자:`로 시작하는 것은 사용자가 직접 말한 사실이다. 발췌에 적힌 범위만 쓰고, "말하지 않은 것"으로 적힌 항목과 그 범위를 넘는 기간·횟수·수치·구체 사건·상대의 발언을 만들지 않는다. 사용자의 인상으로 적힌 것은 인상의 강도를 넘겨 단정하지 않는다.
- 회사 제품·서비스의 기능은 "회사 사실"에 적힌 범위로만 쓴다. 다른 제품의 기능을 섞지 않고, 사용자·대상이 누구인지 근거에 없으면 단정하지 않는다.
- 문체는 모든 문항에서 "~습니다/합니다"체로 통일한다("~다" 평서체를 쓰지 않는다).
- 요구 문구를 그대로 옮기지 말고, 요구가 묻는 역량을 지원자의 실제 경험으로 보여 준다. 근거가 없는 요구는 다루지 않는다.
- 문항이 묻는 것에 답한다. 재료를 다 넣지 않는다. 문항의 논지를 강화하는 근거를 골라 2~3개를 깊게 쓴다.
- 초안마다 다른 각도로 쓴다(강조할 요구 조합이나 이야기의 중심 경험이 달라야 한다). 각도가 겹치면 안 된다.
- 형식: 첫 줄은 대괄호 헤드라인(`[...]`), 한 줄 띄우고 본문. 헤드라인은 "~잘 아는/잘하는 개발자" 같은 자기평가형이 아니라 "다루는/만드는/지키는" 같은 행동·역할 중심으로 끝낸다. 문장은 결론 먼저, 짧게. 구어체와 의미가 불분명한 추상 표현은 쓰지 않는다.
- 문항에 분량 제한이 있으면 지킨다. 제한을 넘지 않되 입력의 "분량 목표" 구간(제한의 85~100%)을 채운다. 짧게 끝내지 말고 근거에 있는 사실을 더 깊게(과정·판단·결과) 풀어서 채운다. 분량을 채우려고 근거에 없는 사실·수치·감정을 지어내지 않는다.
- 입력에 "소재 배정"이 있으면 사용자가 승인한 배정이다. 위 규칙 중 "초안마다 다른 각도"보다 우선한다. 그 문항의 중심 경험으로 주어진 것만 쓰고, 주어진 각도를 따르고, "쓰지 않을 소재"는 언급하지 않으며, 조심할 점을 지킨다. 초안이 여러 개면 같은 중심 경험으로 서술 구조·강조점·헤드라인만 달리한다. 회사 사실이 주어지지 않았으면 회사에 대한 서술을 하지 않는다.
- 각 초안에 covered_requirement_ids(다룬 요구 id), used_refs(쓴 근거의 경로:줄), rationale(왜 이렇게 썼는지, 어느 요구에 적합한지)을 입력에 나온 id와 ref를 글자 그대로 써서 채운다."""

AgentFactory = Callable[[Sequence[Any]], Any]
# 초안 여러 개와 이유·근거를 한 번에 내므로 다른 단계(4096)보다 크게 둔다. 4096에서 MaxTokensReachedException이 났다(2026-10-04).
WRITER_MAX_TOKENS = 8192
_CHAR_LIMIT = re.compile(r"(\d[\d,]*)\s*자")  # "800자"·"1,000자". 쉼표를 허용하지 않으면 1,000자를 0자로 읽는다(2026-10-10 발견)


def build_system_prompt(guidance: str = "") -> str:
    guidance = guidance.strip()
    if not guidance:
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\n\n사용자가 이전 초안에 준 피드백에서 정리한 작성 지침(위 규칙과 함께 따른다)\n{guidance}"


MIN_FILL_PERCENT = 85  # 분량 제한의 이 퍼센트 이상을 채우는 것이 목표다(사용자 요청 2026-10-11: 초안이 대부분 80% 이하로 나왔다)


def min_chars(limit: int) -> int:
    """분량 제한의 85%를 올림한 글자 수. 이 글자 수부터 목표를 채운 것이다(750자의 85%는 637.5라 638자)."""
    return (limit * MIN_FILL_PERCENT + 99) // 100  # 정수 올림(부동소수점을 쓰지 않는다)


def text_length(text: str) -> int:
    """초안 글자 수(줄바꿈 제외, 공백 포함). 읽는 용 문서(scripts/common.py drafts_to_md)가 보이는 값과 같다."""
    return len("".join(text.split("\n")))


def char_limit(question: str) -> int | None:
    """문항에서 '800자' 같은 분량 제한을 읽는다. 없으면 None."""
    found = _CHAR_LIMIT.search(question)
    return int(found.group(1).replace(",", "")) if found else None


def _limitation(verdict: Verdict | None) -> str:
    """검증이 플래그했으면 그 이유(= 한계), 아니면 빈 문자열."""
    return verdict.reason if verdict is not None and verdict.kind is VerdictKind.FLAGGED else ""


def _split(
    mappings: Sequence[Mapping], verdicts: Sequence[Verdict], focus: Sequence[str] | None
) -> tuple[list[tuple[Mapping, Verdict | None]], list[Mapping]]:
    """매핑을 (쓸 수 있는 것, TODO)로 가른다. 플래그된 매핑도 쓸 수 있는 것에 들어가고 한계는 verdict로 따라간다.

    focus를 주면 그 요구들만 남긴다.
    """
    by_req = {v.requirement_id: v for v in verdicts}
    wanted = set(focus) if focus is not None else None
    usable: list[tuple[Mapping, Verdict | None]] = []
    todo: list[Mapping] = []
    for m in mappings:
        verdict = by_req.get(m.requirement.id)
        if wanted is not None and m.requirement.id not in wanted:
            continue
        if m.status is MappingStatus.TODO or not m.evidence:
            todo.append(m)
        else:
            usable.append((m, verdict))
    return usable, todo


def _plan_usable(
    plan: QuestionPlan, item_id: str, mappings: Sequence[Mapping], verdicts: Sequence[Verdict], extra: Sequence[Mapping]
) -> tuple[list[tuple[Mapping, Verdict | None]], dict[str, list[str]]]:
    """배정된 중심 경험마다 근거를 모아 (재료, 합성 요구 id → 실제 요구 id들)를 만든다.

    근거 발췌는 매핑 결과에서 ref로 찾고(없으면 extra에서), 둘 다 없으면 그 근거는 쓸 수 없다. 중심 경험이 가리키는 요구에 검증 플래그가 있으면
    그 이유를 한계로 붙인다. 경험은 "중심 경험" 종류의 합성 요구 하나로 나타내서 기존 입력 모양을 그대로 쓴다.
    """
    index: dict[str, Evidence] = {}
    for m in [*mappings, *extra]:
        for e in m.evidence:
            index.setdefault(e.ref, e)
    flagged = {v.requirement_id: v.reason for v in verdicts if v.kind is VerdictKind.FLAGGED}
    usable: list[tuple[Mapping, Verdict | None]] = []
    real_ids: dict[str, list[str]] = {}
    for i, exp in enumerate(plan.experiences, 1):
        evidence = [index[r] for r in exp.refs if r in index]
        if not evidence:
            continue
        sid = f"exp{plan.number}-{i}"
        requirement = Requirement(id=sid, item_id=item_id, text=exp.name, kind="중심 경험")
        reasons = [flagged[r] for r in exp.requirement_ids if r in flagged]
        usable.append((Mapping(requirement, evidence, MappingStatus.CONFIRMED, exp.why), Verdict(sid, VerdictKind.FLAGGED, "; ".join(reasons)) if reasons else None))
        real_ids[sid] = list(exp.requirement_ids)
    return usable, real_ids


def _render_plan(plan: QuestionPlan) -> str:
    lines = [
        "\n소재 배정(사용자가 승인했다. 이 배정을 따른다)",
        f"- 문항이 묻는 것: {plan.reading}",
        f"- 이 문항의 각도: {plan.angle}",
        "- 중심 경험: 아래 \"이 문항의 중심 경험과 근거\"에 있는 것만 쓴다. 문항의 논지를 강화하도록 깊게 쓴다.",
    ]
    if plan.avoid:
        lines.append("- 이 문항에서 쓰지 않을 소재(다른 문항에서 쓴다): " + ", ".join(plan.avoid))
    if plan.notes:
        lines.append(f"- 조심할 점: {plan.notes}")
    lines.append("- 초안이 여러 개면 같은 중심 경험으로 서술 구조·강조점·헤드라인만 달리한다. 소재를 바꾸지 않는다.")
    if not plan.claim_ids:
        lines.append("- 이 문항에는 회사 사실이 배정되지 않았다. 회사에 대한 서술을 하지 않는다.")
    return "\n".join(lines)


def _render_brief(brief: Brief) -> str:
    """조사 보고서의 주장을 "회사 사실"로 보여 준다. 출처 발췌를 같이 줘서 모델이 주장의 범위를 넘기지 않게 한다."""
    if not brief.claims:
        return f"\n회사 사실({brief.subject}): 없음 — 쓸 수 있는 조사 결과가 없다. 회사에 대한 사실을 쓰지 않는다."
    parts = [f"\n회사 사실({brief.subject}) — 사용자가 확인한 조사 결과"]
    for c in brief.claims:
        topic = f" ({c.topic})" if c.topic else ""
        parts.append(f"[주장 {c.id}]{topic} {c.text}" + "".join(f'\n  - 출처 발췌: "{x.quote}"' for x in c.citations))
    return "\n".join(parts)


def _render_input(
    item: Item,
    question: str,
    usable: Sequence[tuple[Mapping, Verdict | None]],
    todo: Sequence[Mapping],
    n: int,
    previous: Sequence[Draft],
    brief: Brief | None = None,
    plan: QuestionPlan | None = None,
) -> str:
    parts = [f"공고: {item.title}", f"문항: {question}", f"초안 개수: {n}"]
    limit = char_limit(question)
    if limit is not None:
        parts.append(f"분량 목표: {min_chars(limit)}~{limit}자(줄바꿈 제외, 공백 포함, 헤드라인 포함). {limit}자를 넘지 않는다.")
    if plan is not None:
        parts.append(_render_plan(plan))
    if brief is not None:
        parts.append(_render_brief(brief))
    tag = "중심 경험" if plan is not None else "요구"
    parts.append("\n이 문항의 중심 경험과 근거" if plan is not None else "\n쓸 수 있는 요구와 근거")
    for m, verdict in usable:
        kind = f" ({m.requirement.kind})" if m.requirement.kind and plan is None else ""
        lines = [f"[{tag} {m.requirement.id}]{kind} {m.requirement.text} — {m.status.value}", f"  판단: {m.note}"]
        limitation = _limitation(verdict)
        if limitation:
            lines.append(f"  한계(검증이 지적함. 이 범위를 넘겨 쓰지 않는다): {limitation}")
        lines += [f"  - [{e.ref}] {e.excerpt}" for e in m.evidence]
        parts.append("\n".join(lines))
    if todo:
        parts.append("\n근거가 없는 요구(다루지 않는다)")
        parts += [f"[요구 {m.requirement.id}] {m.requirement.text}" for m in todo]
    if previous:
        parts.append("\n이미 낸 초안(이것들과 각도가 달라야 한다)")
        parts += [f"- 각도: {d.angle}\n  본문: {' '.join(next(iter(d.sections.values()), '').split())}" for d in previous]
    return "\n".join(parts)


class StrandsWriter:
    """core.stages.Writer를 Strands 에이전트로 구현한다."""

    def __init__(self, *, guidance: str = "", agent_factory: AgentFactory | None = None) -> None:
        self._guidance = guidance
        self._agent_factory = agent_factory or (lambda tools: build_agent(build_system_prompt(guidance), tools, WRITER_MAX_TOKENS))

    def write(
        self,
        item: Item,
        question: str,
        mappings: list[Mapping],
        verdicts: list[Verdict],
        *,
        n: int = 1,
        focus: Sequence[str] | None = None,
        previous: Sequence[Draft] = (),
        brief: Brief | None = None,
        plan: QuestionPlan | None = None,
        extra: Sequence[Mapping] = (),
    ) -> list[Draft]:
        real_ids: dict[str, list[str]] = {}
        if plan is not None:
            usable, real_ids = _plan_usable(plan, item.id, mappings, verdicts, extra)
            todo = []
            wanted = set(plan.claim_ids)
            brief = Brief(item_id=brief.item_id, subject=brief.subject, claims=[c for c in brief.claims if c.id in wanted]) if brief is not None and wanted else None
        else:
            usable, todo = _split(mappings, verdicts, focus)
        todo_texts = [m.requirement.text for m in todo]
        if not usable:
            return [
                Draft(
                    item_id=item.id,
                    sections={question: ""},
                    todos=todo_texts,
                    warnings=["쓸 수 있는 근거가 없어 초안을 쓰지 않았다(" + ("배정된 중심 경험의 근거를 찾지 못함" if plan is not None else "확인·부분확인이면서 승인된 매핑이 없음") + ")."],
                )
            ]

        agent = self._agent_factory([])
        result = agent(_render_input(item, question, usable, todo, n, previous, brief, plan), structured_output_model=DraftsOut)
        usable_ids = {m.requirement.id for m, _ in usable}
        usable_refs = {e.ref for m, _ in usable for e in m.evidence}
        limited_ids = {m.requirement.id for m, verdict in usable if _limitation(verdict)}
        limit = char_limit(question)
        claim_ids = {c.id for c in brief.claims} if brief is not None else set()

        drafts: list[Draft] = []
        for out in result.structured_output.drafts[:n]:
            warnings: list[str] = []
            ids = [i for i in dict.fromkeys(out.covered_requirement_ids) if i in usable_ids]
            if len(ids) != len(set(out.covered_requirement_ids)):
                warnings.append("모델이 입력에 없는 요구 id를 적어 제외했다.")
            refs = [r for r in dict.fromkeys(out.used_refs) if r in usable_refs]
            if len(refs) != len(set(out.used_refs)):
                warnings.append("모델이 입력에 없는 근거 ref를 적어 제외했다.")
            claims = [c for c in dict.fromkeys(out.used_claim_ids) if c in claim_ids]
            if len(claims) != len(set(out.used_claim_ids)):
                warnings.append("모델이 입력에 없는 주장 id를 적어 제외했다.")
            if brief is not None and not brief.claims:
                warnings.append("조사 보고서에 쓸 수 있는 주장이 없어 회사 이야기는 쓰지 않았다.")
            limited = [i for i in ids if i in limited_ids]
            if limited:
                warnings.append(f"검증이 한계를 지적한 요구 {limited}를 다뤘다. 한계를 넘겨 쓰지 않았는지 확인이 필요하다.")
            if limit is not None and len(out.text) > limit:
                warnings.append(f"분량 {len(out.text)}자가 제한 {limit}자를 넘는다(공백 포함 기준).")
            if limit is not None and text_length(out.text) < min_chars(limit):
                warnings.append(f"분량 {text_length(out.text)}자가 제한 {limit}자의 {MIN_FILL_PERCENT}%({min_chars(limit)}자)에 못 미친다.")
            drafts.append(
                Draft(
                    item_id=item.id,
                    sections={question: out.text},
                    todos=todo_texts,
                    angle=out.angle,
                    rationale=out.rationale,
                    covered_requirement_ids=list(dict.fromkeys(r for i in ids for r in real_ids[i])) if plan is not None else ids,
                    used_refs=refs,
                    used_claims=claims,
                    warnings=warnings,
                )
            )
        return drafts
