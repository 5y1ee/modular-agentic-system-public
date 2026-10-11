"""StrandsGuidanceProposer — 사용자 피드백 기록에서 작성 지침 후보를 뽑는다.

후보만 뽑는다. 지침이 되는 것은 사용자가 승인한 것뿐이다(core/feedback.py의 FeedbackSession.approve).
도구 없이 한 번 호출하고, 사용자가 실제로 한 말에서만 뽑게 한다. 이 모듈이 검사하지 못하는 것: 모델이 피드백을
잘못 일반화했는지(예: 한 문장의 선호를 모든 문항의 규칙으로 올림). 그 판단은 승인 때 사용자가 한다.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from modular_agentic.core.feedback import FeedbackEvent, GuidanceCandidate
from modular_agentic.engines.strands.config import build_agent
from modular_agentic.engines.strands.schemas import GuidanceCandidatesOut

SYSTEM_PROMPT = """너는 자기소개서 초안에 대한 사용자의 선택과 피드백을 읽고, 다음 초안을 쓸 때 적용할 작성 지침 후보를 뽑는 역할이다.

규칙
- 사용자가 실제로 한 말(선택한 이유, 피드백 문장)에서만 뽑는다. 말하지 않은 선호를 만들지 않는다.
- 선택만 있고 이유가 없으면, 선택된 초안의 각도·구조에서 뽑을 수 있는 것도 "추정"이라고 reason에 밝히고 최대 하나만 뽑는다. 근거가 약하면 뽑지 않는다.
- 지침은 한 줄, 명령형, 짧게. 모호한 표현("좋게 써라")은 쓰지 않는다.
- scope: 문체·구조·분량·표현·헤드라인 같은 선호는 "일반". 이 공고나 문항의 내용(어떤 경험을 강조할지, 어떤 요구를 다룰지)에 대한 것은 "특정". 판단이 애매하면 "특정"으로 둔다(저장되지 않는 쪽이 안전하다).
- 이미 있는 지침과 같은 뜻이면 뽑지 않는다. 지침끼리 모순이 되면 새 지침의 reason에 어떤 지침과 충돌하는지 쓴다.
- 후보가 없으면 빈 목록을 돌려준다."""

AgentFactory = Callable[[Sequence[Any]], Any]


def _render(events: Sequence[FeedbackEvent], current_guidance: str) -> str:
    parts = ["현재 지침:\n" + (current_guidance or "(없음)"), "\n사용자 피드백 기록:"]
    for i, e in enumerate(events, 1):
        what = "가장 좋다고 선택" if e.kind == "pick" else "피드백"
        parts.append(
            f"{i}. [{what}] 초안 각도: {e.draft_angle}\n"
            f"   사용자 말: {e.note or '(이유 없음)'}\n"
            f"   초안 본문: {' '.join(e.draft_text.split())}"
        )
    return "\n".join(parts)


class StrandsGuidanceProposer:
    """core.feedback.GuidanceProposer를 Strands 에이전트로 구현한다."""

    def __init__(self, *, agent_factory: AgentFactory | None = None) -> None:
        self._agent_factory = agent_factory or (lambda tools: build_agent(SYSTEM_PROMPT, tools))

    def propose(self, events: Sequence[FeedbackEvent], current_guidance: str) -> list[GuidanceCandidate]:
        if not events:
            return []
        agent = self._agent_factory([])
        result = agent(_render(events, current_guidance), structured_output_model=GuidanceCandidatesOut)
        return [
            GuidanceCandidate(text=c.text.strip(), scope=c.scope, reason=c.reason.strip())
            for c in result.structured_output.candidates
            if c.text.strip()
        ]
