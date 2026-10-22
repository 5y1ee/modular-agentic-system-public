"""StrandsAnalyzer — 공고 본문을 Requirement 목록으로 쪼갠다.

본문이 비어 있으면(예: 본문이 이미지뿐인 공고) 모델을 부르지 않고 빈 목록을 돌려준다.
내용을 추측해서 지어내는 것을 막으려는 core 규칙이다(core/stages.py의 Analyzer).

업무(자소서·제안서)마다 다른 말·규칙은 업무 설정(instances/profile.py)에서 받는다. 기본은 자소서이고,
그때 프롬프트는 2026-10-21 이전과 글자까지 같다.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from modular_agentic.core.types import Item, Requirement
from modular_agentic.engines.strands.config import build_agent
from modular_agentic.instances.profile import Profile, profile as get_profile
from modular_agentic.engines.strands.schemas import RequirementsOut

PROMPT_TEMPLATE = """너는 {role}이다.

규칙
- 본문에 실제로 있는 요구만 뽑는다. 없는 요구를 추가하지 않는다.
- 항목 하나에는 요구 하나만 담는다. 여러 요구가 한 줄에 묶여 있으면 의미를 바꾸지 않고 나눈다.
- 원문 표현을 최대한 유지한다. 요약하거나 말을 바꾸지 않는다.
- {scope}
- {kind}
- 요구가 하나도 없으면 빈 목록을 돌려준다."""

AgentFactory = Callable[[Sequence[Any]], Any]
# 요구를 원문 그대로 옮겨 적어서 출력이 길다. 기본 4096이면 RFP 발췌(요구사항 문장이 길고 많음)에서 출력이 잘려 실패했다(2026-10-21).
ANALYZER_MAX_TOKENS = 16000


def system_prompt(profile: Profile) -> str:
    return PROMPT_TEMPLATE.format(role=profile.analyzer_role, scope=profile.analyzer_scope, kind=profile.analyzer_kind)


SYSTEM_PROMPT = system_prompt(get_profile())  # 자소서(기본) 프롬프트. 예전 이름을 쓰는 곳을 위해 남긴다


class StrandsAnalyzer:
    """core.stages.Analyzer를 Strands 에이전트로 구현한다."""

    def __init__(self, *, profile: Profile | None = None, agent_factory: AgentFactory | None = None) -> None:
        self.profile = profile or get_profile()
        self._agent_factory = agent_factory or (lambda tools: build_agent(system_prompt(self.profile), tools, ANALYZER_MAX_TOKENS))

    def analyze(self, item: Item) -> list[Requirement]:
        if not item.body.strip():
            return []
        agent = self._agent_factory([])
        result = agent(f"{self.profile.item_label} 제목: {item.title}\n\n본문:\n{item.body}", structured_output_model=RequirementsOut)
        return [
            Requirement(id=f"{item.id}-r{i}", item_id=item.id, text=r.text.strip(), kind=r.kind.strip())
            for i, r in enumerate(result.structured_output.requirements, 1)
            if r.text.strip()
        ]
