"""문장 검증(ClaimChecker)의 Strands 구현: 모델이 글의 문장마다 쓴 근거가 뒷받침하는지 판정하고, 코드가 인용을 대조한다.

코드가 하는 일   글을 문장으로 나눠 번호를 붙인다(모델이 문장을 지어내거나 빠뜨리지 못하게), 판정이 없는 문장을 "근거 없음"으로 채운다,
                입력에 없는 번호는 버린다, 모델이 댄 인용이 근거 원문에 있는지 대조해 판정을 바로잡는다(core/claim_check.py).
모델이 하는 일   문장마다 판정과, 뒷받침한다면 근거에서 그대로 복사한 인용을 낸다. 도구는 쓰지 않는다(입력 근거 안에서만 판단한다).

비용: 글 하나당 모델 호출 한 번이다. 문장이 많으면 출력이 길어져 CHECKER_MAX_TOKENS를 넉넉히 둔다.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Any

from modular_agentic.core.claim_check import apply_quote_check, split_sentences
from modular_agentic.core.types import Claim, Evidence, SentenceCheck, Support
from modular_agentic.engines.strands.config import build_agent
from modular_agentic.engines.strands.schemas import CheckOut

CHECKER_MAX_TOKENS = 8192

SYSTEM_PROMPT = """너는 자기소개서 초안의 사실 검증 담당이다. 입력으로 검증할 글(문장마다 번호)과, 그 글을 쓸 때 쓴 근거(내 근거 발췌, 사용자 진술, 회사 사실)가 주어진다. 문장마다 그 근거가 실제로 뒷받침하는지 판정한다.

규칙
- 입력에 있는 근거만 쓴다. 네가 아는 사실, 일반 상식, 그럴듯한 추론은 근거가 아니다. 입력 근거에 없으면 "근거 없음"이다.
- 문장에 사실 주장(수치, 역할, 행동, 결과, 인과, 동기, 시점, 범위)이 여럿이면 모두 근거에 있어야 "뒷받침됨"이다. 하나라도 없으면 "일부만"이고 note에 무엇이 없는지 쓴다. 근거가 말한 것보다 범위를 넓히거나(예: "초안을 썼다"는 근거를 "출간했다"로, "일부 기능을 맡았다"는 근거를 "전체를 총괄했다"로 쓰는 것) 수치를 바꾼 것도 "일부만" 또는 "근거와 다름"이다.
- 인과와 동기("~ 때문에", "~하기 위해", "~ 덕분에", "~를 계기로")는 근거가 그 인과·동기를 직접 말할 때만 뒷받침된다. 근거에 없으면 "근거 없음"이다.
- 서로 다른 대상(프로젝트, 회사, 시기)의 사실을 한 문장에서 연결했으면, 근거가 그 연결을 직접 말할 때만 뒷받침된다.
- 근거와 반대이거나 다른 사실이면 "근거와 다름"이다.
- 다짐, 의견, 소개, 연결어처럼 확인할 사실이 없는 문장만 "사실 주장 없음"이다. "~해 왔다", "~했다"처럼 과거 사실을 담았으면 사실 주장이다. 헤드라인도 문장으로 판정한다(과장된 표현이 있으면 일부만·근거 없음).
- `사용자:`로 시작하는 근거는 사용자가 직접 말한 사실이다. 그 범위 안에서는 뒷받침됨으로 본다. 진술에 "말하지 않은 것"으로 적힌 항목은 근거 없음이다.
- 뒷받침됨·일부만이면 source에 근거 위치(입력의 `[...]` 안 글자 그대로, 회사 사실은 id)를, quote에 그 근거 원문에서 **글자 그대로 복사한** 10자 이상 구절을 쓴다. 요약하거나 바꿔 쓰지 않는다. 그 구절이 문장의 핵심 사실을 말해야 한다. 근거 없음·근거와 다름이면 source·quote는 비운다.
- 모든 문장 번호에 대해 하나씩 답한다. 빠뜨리지 않는다."""

# 평가 보고서에 남겨 어느 프롬프트로 쟀는지 알 수 있게 한다. 프롬프트를 고치면 달라진다.
PROMPT_VERSION = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:8]

AgentFactory = Callable[[Sequence[Any]], Any]


def _render_input(sentences: Sequence[str], evidence: Sequence[Evidence], claims: Sequence[Claim]) -> str:
    parts = ["검증할 글(문장마다 번호)"]
    parts += [f"[{i}] {s}" for i, s in enumerate(sentences, 1)]
    parts.append("\n내 근거(이 글을 쓸 때 쓴 것)")
    parts += [f"[{e.ref}] {' '.join(e.excerpt.split())}" for e in evidence] or ["(없음)"]
    parts.append("\n회사 사실(사용자가 확인한 조사 결과)")
    parts += [f"[{c.id}] {c.text}" for c in claims] or ["(없음)"]
    return "\n".join(parts)


class StrandsClaimChecker:
    """core.stages.ClaimChecker를 Strands 에이전트로 구현한다."""

    def __init__(self, *, agent_factory: AgentFactory | None = None) -> None:
        self._agent_factory = agent_factory or (lambda tools: build_agent(SYSTEM_PROMPT, tools, CHECKER_MAX_TOKENS))

    def check(self, text: str, evidence: Sequence[Evidence], claims: Sequence[Claim]) -> list[SentenceCheck]:
        sentences = split_sentences(text)
        if not sentences:
            return []
        agent = self._agent_factory([])
        result = agent(_render_input(sentences, evidence, claims), structured_output_model=CheckOut)
        out: CheckOut = result.structured_output

        judged = {}
        for item in out.sentences:
            if 1 <= item.index <= len(sentences):
                judged.setdefault(item.index, item)  # 같은 번호가 두 번 오면 처음 것을 쓴다
        checks: list[SentenceCheck] = []
        for i, sentence in enumerate(sentences, 1):
            item = judged.get(i)
            if item is None:
                checks.append(SentenceCheck(i, sentence, Support.UNSUPPORTED, note="검증기가 이 문장을 판정하지 않았다(빠뜨림). 근거 없음으로 둔다."))
            else:
                checks.append(SentenceCheck(i, sentence, Support(item.support), item.source.strip(), item.quote.strip(), item.note.strip()))
        sources = {e.ref: e.excerpt for e in evidence} | {c.id: c.text for c in claims}
        return apply_quote_check(checks, sources)
