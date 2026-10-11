"""StrandsMapper — Requirement마다 vault를 검색해 확인/부분확인/TODO를 판단한다.

흐름 (요구 하나당)
  1. 에이전트를 새로 만들어 `search_vault` 도구를 준다.
  2. 에이전트가 검색을 (대개 2~3번) 하고 발췌를 읽고 JudgementOut 형식으로 판단을 낸다.
  3. build_mapping이 판단을 core의 Mapping으로 바꾼다. 이때 모델의 인용을 **검증**한다.

build_mapping의 검증 규칙 (근거처럼 보이지만 사실과 다른 서술을 막으려는 것)
  - 모델이 인용한 ref가 이 요구의 검색 결과에 실제로 있었던 것만 인정한다.
  - 인정한 근거의 발췌는 모델이 쓴 문장이 아니라 **검색 결과의 실제 발췌**로 채운다.
  - 확인/부분확인인데 인정된 근거가 하나도 없으면 TODO로 내리고 note에 이유를 남긴다.
  - TODO의 근거는 비운다.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from modular_agentic.core.types import Evidence, Mapping, MappingStatus, Requirement
from modular_agentic.engines.strands.config import build_agent
from modular_agentic.engines.strands.schemas import JudgementOut
from modular_agentic.engines.strands.search_tool import SearchRegistry, make_search_tool
from modular_agentic.instances.cover_letter.config import VAULT_EXCLUDE_PREFIXES
from modular_agentic.tools import vault_search

EXCERPT_CHARS = 400  # Evidence에 담는 발췌 길이

SYSTEM_PROMPT = """너는 채용공고의 요구 하나에 대해, 지원자의 개인 지식 베이스에서 근거를 찾아 판단하는 역할이다.

절차
1. `search_vault`로 검색한다. 요구 문구를 그대로 넣지 말고 "어떤 경험이 있으면 이 요구를 충족하는가"를 문장으로 풀어 쓴다. 표현을 바꿔 2~3번까지 검색해도 된다.
2. 결과의 발췌를 읽고 그 글이 요구를 실제로 뒷받침하는지 판단한다. 단어가 겹친다고 근거가 아니다. 공고 문구를 인용한 글이나 요구와 무관한 글은 근거가 아니다.
3. 판단을 낸다.

판단 기준
- 확인: 발췌가 요구를 충분히 뒷받침하는 사실(한 일, 쓴 기술, 결과)을 적고 있다.
- 부분확인: 일부만 뒷받침한다. 수준이 낮거나, 관련은 있지만 요구와 다르거나, 학습·계획 단계이거나, 자소서·면접 답변의 서술일 뿐 사실을 적은 원본이 아닌 경우다. note에 무엇이 모자란지 쓴다.
- TODO: 뒷받침하는 발췌가 없다. 없는 경험을 있다고 쓰지 않는다. evidence는 비운다.

규칙
- evidence의 ref는 검색 결과에 나온 `경로:줄`을 글자 그대로 쓴다. 검색하지 않은 글은 인용하지 않는다.
- 요구의 수준과 근거의 수준이 다르면(예: 운영 경험을 요구하는데 어깨너머로 아는 정도) 부분확인이나 TODO로 한다.
- note는 한두 문장으로 판단 이유를 쓴다."""

_STATUS = {"확인": MappingStatus.CONFIRMED, "부분확인": MappingStatus.PARTIAL, "TODO": MappingStatus.TODO}

AgentFactory = Callable[[Sequence[Any]], Any]


def default_agent_factory(tools: Sequence[Any]) -> Any:
    return build_agent(SYSTEM_PROMPT, tools)


def build_mapping(requirement: Requirement, judgement: JudgementOut, registry: SearchRegistry) -> Mapping:
    """모델의 판단을 검증해서 Mapping으로 바꾼다. 검증 규칙은 모듈 docstring 참고."""
    evidence: list[Evidence] = []
    seen: set[str] = set()
    dropped = 0
    for cited in judgement.evidence:
        resolved = registry.resolve(cited.ref)
        if resolved is None:
            dropped += 1
            continue
        ref, hit = resolved
        if ref in seen:
            continue
        seen.add(ref)
        evidence.append(Evidence(ref=ref, excerpt=" ".join(hit.snippet.split())[:EXCERPT_CHARS]))

    status = _STATUS[judgement.status]
    note = judgement.note
    if dropped:
        note += f" [검증: 인용 {dropped}건이 검색 결과에 없어 제외]"
    if status is not MappingStatus.TODO and not evidence:
        status = MappingStatus.TODO
        note += " [검증: 인정된 근거가 없어 TODO로 내림]"
    if status is MappingStatus.TODO:
        evidence = []
    return Mapping(requirement=requirement, evidence=evidence, status=status, note=note)


@dataclass(frozen=True)
class MapStats:
    """요구 하나를 매핑하는 데 시간이 어디에 쓰였는지.

    total_seconds        이 요구를 처리한 전체 시간(벽시계)
    search_calls         vault 검색을 부른 횟수
    search_sum_seconds   검색 호출 시간의 단순 합(호출이 겹쳤으면 실제보다 큼)
    search_wall_seconds  검색을 하느라 실제로 흐른 시간(겹친 구간은 한 번만 셈)
    model_seconds        Bedrock가 보고한 모델 응답 시간의 합(Strands의 latencyMs). 알 수 없으면 None
    input/output_tokens  모델 호출의 토큰 사용량 합. 알 수 없으면 None
    cycles               에이전트 루프를 돈 횟수. 알 수 없으면 None
    """

    total_seconds: float
    search_calls: int
    search_sum_seconds: float
    search_wall_seconds: float
    model_seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cycles: int | None = None


def _model_stats(result: Any) -> tuple[float | None, int | None, int | None, int | None]:
    """Strands 결과의 metrics에서 모델 응답 시간·토큰·사이클을 꺼낸다. 없으면 None들."""
    metrics = getattr(result, "metrics", None)
    if metrics is None:
        return None, None, None, None
    latency_ms = metrics.accumulated_metrics.get("latencyMs")
    usage = metrics.accumulated_usage
    return (
        latency_ms / 1000 if latency_ms is not None else None,
        usage.get("inputTokens"),
        usage.get("outputTokens"),
        metrics.cycle_count,
    )


class StrandsMapper:
    """core.stages.Mapper를 Strands 에이전트로 구현한다.

    요구마다 걸린 시간의 구성은 `stats[requirement.id]`에 남는다(MapStats).
    """

    def __init__(
        self,
        *,
        exclude_prefixes: Sequence[str] = VAULT_EXCLUDE_PREFIXES,
        n_results: int = 8,
        agent_factory: AgentFactory = default_agent_factory,
        search_fn: Callable[..., list[vault_search.VaultHit]] = vault_search.search,
        tag: str = "mapper",
    ) -> None:
        self._exclude_prefixes = exclude_prefixes
        self._n_results = n_results
        self._agent_factory = agent_factory
        self._search_fn = search_fn
        self._tag = tag
        self.stats: dict[str, MapStats] = {}

    def map(self, requirements: list[Requirement]) -> list[Mapping]:
        return [self._map_one(r) for r in requirements]

    def _map_one(self, requirement: Requirement) -> Mapping:
        started = time.monotonic()
        registry = SearchRegistry()
        search_tool = make_search_tool(
            registry,
            exclude_prefixes=self._exclude_prefixes,
            n_results=self._n_results,
            search_fn=self._search_fn,
            tag=f"{self._tag}:{requirement.id}",
        )
        agent = self._agent_factory([search_tool])
        kind = f" (공고 분류: {requirement.kind})" if requirement.kind else ""
        result = agent(f"요구{kind}: {requirement.text}", structured_output_model=JudgementOut)
        mapping = build_mapping(requirement, result.structured_output, registry)
        model_seconds, input_tokens, output_tokens, cycles = _model_stats(result)
        self.stats[requirement.id] = MapStats(
            total_seconds=time.monotonic() - started,
            search_calls=registry.search_calls,
            search_sum_seconds=registry.search_sum_seconds,
            search_wall_seconds=registry.search_wall_seconds,
            model_seconds=model_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cycles=cycles,
        )
        return mapping
