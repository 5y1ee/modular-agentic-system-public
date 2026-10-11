"""StrandsAnalyzer — 공고 본문을 Requirement 목록으로 쪼갠다.

본문이 비어 있으면(예: 본문이 이미지뿐인 공고) 모델을 부르지 않고 빈 목록을 돌려준다.
내용을 추측해서 지어내는 것을 막으려는 core 규칙이다(core/stages.py의 Analyzer).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from modular_agentic.core.types import Item, Requirement
from modular_agentic.engines.strands.config import build_agent
from modular_agentic.engines.strands.schemas import RequirementsOut

SYSTEM_PROMPT = """너는 채용공고 본문에서 지원자에게 요구되는 항목을 하나씩 뽑는 역할이다.

규칙
- 본문에 실제로 있는 요구만 뽑는다. 없는 요구를 추가하지 않는다.
- 항목 하나에는 요구 하나만 담는다. 여러 요구가 한 줄에 묶여 있으면 의미를 바꾸지 않고 나눈다.
- 원문 표현을 최대한 유지한다. 요약하거나 말을 바꾸지 않는다.
- 지원자의 경험·역량·기술로 대답할 수 있는 항목만 뽑는다. 근무지, 급여, 복지, 전형 절차, 마감일은 제외한다.
- kind에는 공고가 붙인 분류(자격요건, 우대사항, 담당업무 등)를 쓴다. 분류가 없으면 빈 문자열이다.
- 요구가 하나도 없으면 빈 목록을 돌려준다."""

AgentFactory = Callable[[Sequence[Any]], Any]


def default_agent_factory(tools: Sequence[Any]) -> Any:
    return build_agent(SYSTEM_PROMPT, tools)


class StrandsAnalyzer:
    """core.stages.Analyzer를 Strands 에이전트로 구현한다."""

    def __init__(self, *, agent_factory: AgentFactory = default_agent_factory) -> None:
        self._agent_factory = agent_factory

    def analyze(self, item: Item) -> list[Requirement]:
        if not item.body.strip():
            return []
        agent = self._agent_factory([])
        result = agent(f"채용공고 제목: {item.title}\n\n본문:\n{item.body}", structured_output_model=RequirementsOut)
        return [
            Requirement(id=f"{item.id}-r{i}", item_id=item.id, text=r.text.strip(), kind=r.kind.strip())
            for i, r in enumerate(result.structured_output.requirements, 1)
            if r.text.strip()
        ]
