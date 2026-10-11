"""요구마다 관련 있는 조사 주장을 붙인다(회사 맥락, 설계 문서 25절). 모델이 연결을 고르고, 코드가 입력에 있던 번호·id만 인정한다.

규칙: 요구는 공고에서 뽑은 것이라 바꾸거나 새로 만들지 않는다. 사용자가 승인한 조사 주장만 쓴다. 관련 없는 요구는 연결이 없는 것이 정상이다.
지원자의 경험·강점은 이 단계가 다루지 않는다(회사 쪽 이해만).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from modular_agentic.core.types import Brief, ContextNote, Requirement
from modular_agentic.engines.strands.config import build_agent
from modular_agentic.engines.strands.schemas import LinksOut

LINKER_MAX_TOKENS = 8192

SYSTEM_PROMPT = """너는 채용공고의 요구 항목이 이 회사에서 실제로 어떤 일인지 이해하도록, 요구마다 관련 있는 조사 주장을 연결하는 역할이다. 입력으로 요구 목록(번호)과 사용자가 확인한 조사 주장 목록(id)이 주어진다.

규칙
- 요구의 글자를 바꾸거나 새 요구를 만들지 않는다. 연결만 한다.
- 주장이 그 요구가 가리키는 업무·제품·방향을 이해하는 데 직접 관련 있을 때만 연결한다. 억지로 연결하지 않는다. 관련 있는 주장이 없는 요구는 연결하지 않는다(그것이 정상이다).
- reason에는 그 주장이 이 요구와 어떻게 관련 있는지 한 문장을 쓴다. 주장에 적힌 내용만 쓰고, 주장에 없는 사실을 보태지 않는다. 지원자의 경험이나 강점은 다루지 않는다.
- 주장이 그 요구가 가리키는 제품·서비스·조직과 **다른 대상**(다른 제품, 다른 부서, 모회사 등)에 관한 것이면, 같은 대상의 기능·실적이라고 쓰지 않는다. 연결하더라도 reason에 어느 대상의 이야기인지 밝히고 "관련 사례"로만 쓴다. 억지스러우면 연결하지 않는다.
- 요구번호와 주장 id는 입력에 나온 것을 글자 그대로 쓴다. 한 요구에 여러 주장을 연결해도 되고, 한 주장을 여러 요구에 연결해도 된다."""

AgentFactory = Callable[[Sequence[Any]], Any]


def _render_input(requirements: Sequence[Requirement], brief: Brief) -> str:
    parts = ["요구(공고에서 뽑은 것)"]
    parts += [f"[요구 {i}] " + (f"({r.kind}) " if r.kind else "") + r.text for i, r in enumerate(requirements, 1)]
    parts.append(f"\n조사 주장({brief.subject}, 사용자가 확인한 것)")
    parts += [f"[{c.id}] ({c.topic}) {c.text}" if c.topic else f"[{c.id}] {c.text}" for c in brief.claims]
    return "\n".join(parts)


class StrandsContextLinker:
    """core.stages.ContextLinker를 Strands 에이전트로 구현한다."""

    def __init__(self, *, agent_factory: AgentFactory | None = None) -> None:
        self._agent_factory = agent_factory or (lambda tools: build_agent(SYSTEM_PROMPT, tools, LINKER_MAX_TOKENS))

    def link(self, requirements: Sequence[Requirement], brief: Brief) -> tuple[dict[str, list[ContextNote]], list[str]]:
        if not requirements or not brief.claims:
            return {}, []
        agent = self._agent_factory([])
        out = agent(_render_input(requirements, brief), structured_output_model=LinksOut).structured_output
        known_claims = {c.id for c in brief.claims}
        context: dict[str, list[ContextNote]] = {}
        bad_number = bad_claim = blank = 0
        for link in out.links:
            if not 1 <= link.requirement_number <= len(requirements):
                bad_number += 1
            elif link.claim_id not in known_claims:
                bad_claim += 1
            elif not link.reason.strip():
                blank += 1
            else:
                notes = context.setdefault(requirements[link.requirement_number - 1].id, [])
                if all(n.claim_id != link.claim_id for n in notes):  # 같은 요구에 같은 주장은 한 번만
                    notes.append(ContextNote(link.claim_id, link.reason.strip()))
        warnings = []
        if bad_number:
            warnings.append(f"모델이 입력에 없는 요구 번호로 낸 연결 {bad_number}개를 버렸다.")
        if bad_claim:
            warnings.append(f"모델이 입력에 없는 주장 id로 낸 연결 {bad_claim}개를 버렸다.")
        if blank:
            warnings.append(f"이유가 비어 있는 연결 {blank}개를 버렸다.")
        return context, warnings
