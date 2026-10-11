"""Strands Verifier 테스트. AWS를 부르지 않고 가짜 에이전트와 가짜 검색으로 검증한다.

핵심: (1) 후보 경험을 코드가 찾는다 (2) 플래그의 근거 ref는 실제 검색 결과에 있던 것만 인정한다
(3) TODO는 모델을 부르지 않는다 (4) 근거를 못 댄 플래그를 승인으로 바꾸지 않는다.
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("strands")  # strands extra가 설치된 환경에서만 돈다

from modular_agentic.core.types import (  # noqa: E402
    Evidence,
    Mapping,
    MappingStatus,
    Requirement,
    VerdictKind,
)
from modular_agentic.engines.strands.schemas import EvidenceOut, VerdictOut  # noqa: E402
from modular_agentic.engines.strands.search_tool import SearchRegistry  # noqa: E402
from modular_agentic.engines.strands.verifier import (  # noqa: E402
    KnownExperience,
    StrandsVerifier,
    build_verdict,
    match_experiences,
)
from modular_agentic.tools.vault_search import VaultHit  # noqa: E402

REQ = Requirement(id="i1-r3", item_id="i1", text="AI Agent 개발 Framework 활용")
ADOTBIZ = KnownExperience(
    id="adotbiz_llm",
    name="프로젝트A의 LLM 대화 기능",
    keywords=("tool_call", "LLM 에이전트 대화"),
    origin_pages=("04_project/프로젝트A/프로젝트A.md",),
)
FINT = KnownExperience(id="fint", name="FINT", keywords=("FINT",), origin_pages=("04_project/FINT.md",))


def hit(path: str, line: int = 5, snippet: str = "실제 발췌") -> VaultHit:
    return VaultHit(path=path, title="", score=0.5, line=line, snippet=snippet, docid="#x")


def mapping(*evidence: Evidence, status=MappingStatus.PARTIAL) -> Mapping:
    return Mapping(requirement=REQ, evidence=list(evidence), status=status, note="판단")


def registry_with(*hits: VaultHit) -> SearchRegistry:
    registry = SearchRegistry()
    for h in hits:
        registry.add(h)
    return registry


def out(kind="승인", conflicting=(), reason="이유") -> VerdictOut:
    return VerdictOut(
        experience="프로젝트A",
        keywords=["프로젝트A"],
        kind=kind,
        reason=reason,
        conflicting=[EvidenceOut(ref=r) for r in conflicting],
    )


def test_match_experiences_by_origin_page_or_keyword():
    by_origin = mapping(Evidence(ref="04_project/프로젝트A/프로젝트A.md:11", excerpt="아무 말"))
    by_keyword = mapping(Evidence(ref="06_career/자소서/x.md:3", excerpt="FINT를 만들었다"))
    neither = mapping(Evidence(ref="02_concept/a.md:1", excerpt="무관"))

    assert match_experiences(by_origin, [ADOTBIZ, FINT]) == [ADOTBIZ]
    assert match_experiences(by_keyword, [ADOTBIZ, FINT]) == [FINT]
    assert match_experiences(neither, [ADOTBIZ, FINT]) == []


def test_flag_keeps_only_real_citations_and_uses_real_excerpt():
    registry = registry_with(hit("04_project/프로젝트A/프로젝트A.md", line=24, snippet="연동만 했다"))

    verdict = build_verdict(
        mapping(Evidence(ref="a.md:1", excerpt="x")),
        out("플래그", ["04_project/프로젝트A/프로젝트A.md:24", "지어낸.md:1"], "직접 개발이 아니다"),
        registry,
    )

    assert verdict.kind is VerdictKind.FLAGGED
    assert "[04_project/프로젝트A/프로젝트A.md:24] 연동만 했다" in verdict.reason  # 모델 문장이 아니라 실제 발췌
    assert "인용 1건" in verdict.reason  # 걸러낸 사실이 남는다
    assert "지어낸.md" not in verdict.reason


def test_flag_without_any_valid_citation_stays_flagged_and_asks_human():
    verdict = build_verdict(
        mapping(Evidence(ref="a.md:1", excerpt="x")), out("플래그", ["지어낸.md:1"]), SearchRegistry()
    )

    assert verdict.kind is VerdictKind.FLAGGED  # 승인으로 바꾸지 않는다
    assert "사람이 대조" in verdict.reason


def test_approval_passes_through():
    verdict = build_verdict(mapping(Evidence(ref="a.md:1", excerpt="x")), out("승인", reason="모순 없음"), SearchRegistry())

    assert (verdict.kind, verdict.reason, verdict.requirement_id) == (VerdictKind.APPROVED, "모순 없음", REQ.id)


def test_todo_mapping_is_approved_without_calling_the_model():
    def factory(tools):
        raise AssertionError("TODO는 모델을 부르면 안 된다")

    todo = Mapping(requirement=REQ, evidence=[], status=MappingStatus.TODO, note="없음")

    [verdict] = StrandsVerifier(agent_factory=factory).verify([todo])

    assert verdict.kind is VerdictKind.APPROVED
    assert "TODO" in verdict.reason


def test_verifier_with_fake_agent_searches_without_exclusion_and_records_extracted_experience():
    calls = []

    def fake_search(query, n, exclude_prefixes, tag):
        calls.append({"exclude": tuple(exclude_prefixes), "tag": tag})
        return [hit("04_project/프로젝트A/프로젝트A.md", line=24, snippet="LangGraph는 연동만 했다")]

    prompts = []

    class FakeAgent:
        def __init__(self, tools):
            self.tools = tools

        def __call__(self, prompt, structured_output_model=None):
            prompts.append(prompt)
            self.tools[0]("프로젝트A LangGraph 정정")
            return SimpleNamespace(structured_output=out("플래그", ["04_project/프로젝트A/프로젝트A.md:24"], "연동만"))

    m = mapping(Evidence(ref="02_concept/무관.md:1", excerpt="후보 키워드 없는 발췌"))
    verifier = StrandsVerifier([ADOTBIZ], agent_factory=FakeAgent, search_fn=fake_search)

    [verdict] = verifier.verify([m])

    assert verdict.kind is VerdictKind.FLAGGED
    assert calls[0]["exclude"] == ()  # 자소서·면접의 반복 서술과 정정을 모두 본다
    assert "verifier:i1-r3" in calls[0]["tag"]
    assert "(없음" in prompts[0]  # 후보가 없다고 모델에게 알린다
    assert [(x.requirement_id, x.name) for x in verifier.extracted] == [("i1-r3", "프로젝트A")]
    assert verifier.stats[REQ.id].search_calls == 1
