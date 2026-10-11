"""StrandsVerifier — 매핑의 근거가 말하는 **경험**을 vault 전체에서 다시 모아 모순·과장을 찾는다.

왜 페이지가 아니라 경험 단위인가 (docs/experiments/001, 골든셋 v2와 같은 이유)
  같은 경험이 자소서·면접·원본 페이지에 여러 번 쓰이고, 틀린 서술은 반복되는데 정정은 한 곳에만 있는 일이 있었다
  ("격리를 검증", 프로젝트A의 LangGraph 범위). 인용된 페이지 하나만 열면 그 정정을 못 만난다.

흐름 (확인·부분확인 Mapping 하나당)
  1. 코드가 후보 경험을 찾는다: 근거의 경로가 경험의 원본 페이지이거나, 발췌에 경험의 키워드가 있으면 후보다(골든셋 v2의 규칙).
  2. 에이전트가 `search_vault`(제외 목록 없이)로 그 경험을 다룬 글을 키워드와 "정정·아님·과장" 같은 단어로 찾아 읽고
     승인/플래그를 낸다. 후보가 없으면 모델이 경험 이름과 키워드를 직접 정한다(`extracted`에 남겨 목록 승격 후보로 쓴다).
  3. 코드가 판정을 검증한다: 플래그의 근거로 댄 ref가 이 검색 결과에 실제로 있었는지 대조하고, 모델이 쓴 문장 대신
     검색 결과의 발췌 일부를 reason에 붙인다.

TODO Mapping은 근거가 없어 검증할 것이 없으므로 모델을 부르지 않고 승인으로 둔다.
Verdict는 Mapping과 같은 순서로 하나씩 돌려준다(core.stages.Verifier).

한계
  - 검색은 조각 단위라 경험을 다룬 글을 놓칠 수 있다. "정정을 못 찾음"은 정정이 없다는 증거가 아니다. reason에 그렇게 쓰게 한다.
  - 후보 경험 목록은 엔진이 모른다. 인스턴스가 만들어 넘긴다(core 규칙: engines와 instances는 서로 모른다).
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from modular_agentic.core.types import Mapping, MappingStatus, Verdict, VerdictKind
from modular_agentic.engines.strands.config import build_agent
from modular_agentic.engines.strands.mapper import MapStats, _model_stats
from modular_agentic.engines.strands.schemas import VerdictOut
from modular_agentic.engines.strands.search_tool import SearchRegistry, make_search_tool
from modular_agentic.tools import vault_search

CONFLICT_EXCERPT_CHARS = 200  # reason에 붙이는 정정·모순 발췌 길이

SYSTEM_PROMPT = """너는 지원자의 자소서 근거를 검증하는 역할이다. 이미 한 판단이 사실과 맞는지, 부풀려지지 않았는지 본다.

입력: 공고의 요구 하나, 그에 대한 판단(상태·이유), 판단이 인용한 근거(경로:줄과 발췌), 후보 경험(있다면).

절차
1. 근거가 서술하는 경험을 정한다. 후보 경험이 맞으면 그 이름과 키워드를 그대로 쓴다. 후보가 없거나 맞지 않으면 직접 이름과 키워드를 정한다.
2. `search_vault`로 그 경험을 다룬 글을 찾는다. 키워드로 검색하고, "정정", "아님", "연동만", "과장", "사실은" 같은 단어를 더해 **정정·부정 문구를 일부러 찾는 검색**도 한다. 2~4번이면 충분하다.
3. 읽은 글들을 서로 대조한다. 정정이 표시된 글은 그 이전 서술보다 우선한다. 같은 경험을 서술한 글끼리 사실이 다르면 어긋남이다.
4. 판정한다.
   - 플래그: (a) 검색한 글에 근거와 어긋나는 서술이나 정정이 있고 그것이 판단을 흔든다. 또는 (b) 판단이 그 경험이 실제로 한 것보다 강하게 읽힌다(예: 연동만 한 것을 직접 개발 경험으로 읽음, 학습·계획을 한 일로 읽음).
   - 승인: 어긋나는 글이 없고 판단이 경험이 실제로 한 것을 넘지 않는다. 판단이 이미 "부분확인"으로 한계를 적고 있고 정정이 그 한계와 같은 방향이면 승인이다.

규칙
- "정정이나 모순을 못 찾았다"는 정정이 없다는 증거가 아니다. 승인이어도 reason에 무엇을 검색했고 무엇을 확인했는지 쓴다.
- conflicting의 ref는 검색 결과에 나온 `경로:줄`을 글자 그대로 쓴다. 검색하지 않은 글은 인용하지 않는다.
- 없는 사실을 지어내지 않는다. reason은 한두 문장."""

AgentFactory = Callable[[Sequence[Any]], Any]


def default_agent_factory(tools: Sequence[Any]) -> Any:
    return build_agent(SYSTEM_PROMPT, tools)


@dataclass(frozen=True)
class KnownExperience:
    """후보 경험 하나. 인스턴스가 정한다(자소서: eval 골든셋 v2의 경험 목록)."""

    id: str
    name: str
    keywords: tuple[str, ...]
    origin_pages: tuple[str, ...] = ()


def match_experiences(mapping: Mapping, experiences: Sequence[KnownExperience]) -> list[KnownExperience]:
    """근거의 경로나 발췌로 후보 경험을 찾는다(골든셋 v2의 규칙: 원본 페이지이거나 키워드가 발췌에 있다)."""
    matched: list[KnownExperience] = []
    for exp in experiences:
        for e in mapping.evidence:
            path = e.ref.rsplit(":", 1)[0] if e.ref.rsplit(":", 1)[-1].isdigit() else e.ref
            text = e.excerpt.lower()
            if path in exp.origin_pages or any(k.lower() in text for k in exp.keywords):
                matched.append(exp)
                break
    return matched


def build_verdict(mapping: Mapping, out: VerdictOut, registry: SearchRegistry) -> Verdict:
    """모델의 판정을 검증해서 Verdict로 바꾼다. 인용한 정정·모순 ref가 검색 결과에 실제로 있었는지 대조한다."""
    reason = out.reason.strip()
    if out.kind == "승인":
        return Verdict(requirement_id=mapping.requirement.id, kind=VerdictKind.APPROVED, reason=reason)

    shown: list[str] = []
    dropped = 0
    seen: set[str] = set()
    for cited in out.conflicting:
        resolved = registry.resolve(cited.ref)
        if resolved is None:
            dropped += 1
            continue
        ref, hit = resolved
        if ref in seen:
            continue
        seen.add(ref)
        shown.append(f"[{ref}] {' '.join(hit.snippet.split())[:CONFLICT_EXCERPT_CHARS]}")
    if shown:
        reason += " 근거: " + " / ".join(shown)
    if dropped:
        reason += f" [검증: 인용 {dropped}건이 검색 결과에 없어 제외]"
    if not shown:
        # 플래그를 승인으로 바꾸지 않는다: 근거를 못 댄 플래그도 사람이 한 번은 봐야 한다.
        reason += " [검증: 플래그의 근거가 검색 결과에서 확인되지 않음 — 사람이 대조]"
    return Verdict(requirement_id=mapping.requirement.id, kind=VerdictKind.FLAGGED, reason=reason)


@dataclass
class ExtractedExperience:
    """모델이 직접 정한 경험(후보가 없던 경우). 골든셋 목록에 올릴지 사람이 판단한다."""

    requirement_id: str
    name: str
    keywords: list[str]


class StrandsVerifier:
    """core.stages.Verifier를 Strands 에이전트로 구현한다.

    요구마다 걸린 시간은 `stats[requirement_id]`, 모델이 직접 정한 경험은 `extracted`에 남는다.
    """

    def __init__(
        self,
        experiences: Sequence[KnownExperience] = (),
        *,
        exclude_prefixes: Sequence[str] = (),
        n_results: int = 8,
        agent_factory: AgentFactory = default_agent_factory,
        search_fn: Callable[..., list[vault_search.VaultHit]] = vault_search.search,
        tag: str = "verifier",
    ) -> None:
        self._experiences = experiences
        self._exclude_prefixes = exclude_prefixes  # 기본은 비어 있다: 자소서·면접의 반복 서술과 정정을 모두 봐야 해서
        self._n_results = n_results
        self._agent_factory = agent_factory
        self._search_fn = search_fn
        self._tag = tag
        self.stats: dict[str, MapStats] = {}
        self.extracted: list[ExtractedExperience] = []

    def verify(self, mappings: list[Mapping]) -> list[Verdict]:
        return [self._verify_one(m) for m in mappings]

    def _verify_one(self, mapping: Mapping) -> Verdict:
        rid = mapping.requirement.id
        if mapping.status is MappingStatus.TODO or not mapping.evidence:
            return Verdict(requirement_id=rid, kind=VerdictKind.APPROVED, reason="TODO라 검증할 근거가 없다.")

        started = time.monotonic()
        registry = SearchRegistry()
        search_tool = make_search_tool(
            registry,
            exclude_prefixes=self._exclude_prefixes,
            n_results=self._n_results,
            search_fn=self._search_fn,
            tag=f"{self._tag}:{rid}",
        )
        candidates = match_experiences(mapping, self._experiences)
        agent = self._agent_factory([search_tool])
        result = agent(_prompt(mapping, candidates), structured_output_model=VerdictOut)
        out: VerdictOut = result.structured_output
        if not candidates:
            self.extracted.append(ExtractedExperience(rid, out.experience, list(out.keywords)))
        verdict = build_verdict(mapping, out, registry)
        model_seconds, input_tokens, output_tokens, cycles = _model_stats(result)
        self.stats[rid] = MapStats(
            total_seconds=time.monotonic() - started,
            search_calls=registry.search_calls,
            search_sum_seconds=registry.search_sum_seconds,
            search_wall_seconds=registry.search_wall_seconds,
            model_seconds=model_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cycles=cycles,
        )
        return verdict


def _prompt(mapping: Mapping, candidates: Sequence[KnownExperience]) -> str:
    evidence = "\n".join(f"- [{e.ref}] {e.excerpt}" for e in mapping.evidence)
    if candidates:
        known = "\n".join(f"- {c.name} (키워드: {', '.join(c.keywords)})" for c in candidates)
    else:
        known = "(없음 — 경험 이름과 키워드를 직접 정한다)"
    return (
        f"요구: {mapping.requirement.text}\n"
        f"판단: {mapping.status.value} — {mapping.note}\n\n"
        f"인용된 근거:\n{evidence}\n\n"
        f"후보 경험:\n{known}"
    )
