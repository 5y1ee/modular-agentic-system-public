"""Strands 엔진 테스트. AWS를 부르지 않고 가짜 에이전트와 가짜 검색으로 검증한다.

핵심은 모델의 인용 검증이다: 모델이 지어낸 근거는 걸러지고, 인정된 근거의 인용문은 모델이 쓴 문장이 아니라
검색 결과의 실제 발췌여야 한다.
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("strands")  # strands extra가 설치된 환경에서만 돈다

from modular_agentic.core.types import Item, MappingStatus, Requirement  # noqa: E402
from modular_agentic.engines.strands.analyzer import StrandsAnalyzer  # noqa: E402
from modular_agentic.engines.strands.mapper import StrandsMapper, build_mapping  # noqa: E402
from modular_agentic.engines.strands.schemas import (  # noqa: E402
    EvidenceOut,
    JudgementOut,
    RequirementOut,
    RequirementsOut,
)
from modular_agentic.engines.strands.search_tool import SearchRegistry  # noqa: E402
from modular_agentic.tools.vault_search import VaultHit  # noqa: E402

REQ = Requirement(id="i1-r1", item_id="i1", text="AI Agent 개발 경험")


def hit(path: str, line: int = 7, snippet: str = "실제 발췌") -> VaultHit:
    return VaultHit(path=path, title="", score=0.5, line=line, snippet=snippet, docid="#x")


def registry_with(*hits: VaultHit) -> SearchRegistry:
    registry = SearchRegistry()
    for h in hits:
        registry.add(h)
    return registry


def test_build_mapping_keeps_only_real_citations_and_uses_real_excerpt():
    registry = registry_with(hit("04_project/FINT.md", snippet="FINT를 만들었다"))
    judgement = JudgementOut(
        status="확인",
        evidence=[EvidenceOut(ref="04_project/FINT.md:7"), EvidenceOut(ref="지어낸/페이지.md:3")],
        note="좋음",
    )

    mapping = build_mapping(REQ, judgement, registry)

    assert mapping.status is MappingStatus.CONFIRMED
    assert [(e.ref, e.excerpt) for e in mapping.evidence] == [("04_project/FINT.md:7", "FINT를 만들었다")]
    assert "인용 1건" in mapping.note  # 걸러낸 사실이 note에 남는다


def test_build_mapping_downgrades_to_todo_without_valid_evidence():
    judgement = JudgementOut(status="부분확인", evidence=[EvidenceOut(ref="없는.md:1")], note="")

    mapping = build_mapping(REQ, judgement, SearchRegistry())

    assert mapping.status is MappingStatus.TODO
    assert mapping.evidence == []
    assert "TODO로 내림" in mapping.note


def test_build_mapping_clears_evidence_for_todo():
    registry = registry_with(hit("04_project/FINT.md"))
    judgement = JudgementOut(status="TODO", evidence=[EvidenceOut(ref="04_project/FINT.md:7")], note="없음")

    assert build_mapping(REQ, judgement, registry).evidence == []


def test_registry_resolves_by_path_when_line_differs_or_missing():
    registry = registry_with(hit("04_project/FINT.md", line=7))

    assert registry.resolve("04_project/FINT.md:7")[0] == "04_project/FINT.md:7"
    assert registry.resolve("04_project/FINT.md:99")[0] == "04_project/FINT.md:7"  # 줄이 달라도 같은 경로
    assert registry.resolve("04_project/FINT.md")[0] == "04_project/FINT.md:7"  # 줄 번호 없음
    assert registry.resolve("다른/경로.md:7") is None


def test_mapper_with_fake_agent_and_search():
    calls = []

    def fake_search(query, n, exclude_prefixes, tag):
        calls.append({"query": query, "exclude": tuple(exclude_prefixes), "tag": tag})
        return [hit("04_project/FINT.md", snippet="FINT 서술")]

    class FakeAgent:
        def __init__(self, tools):
            self.tools = tools

        def __call__(self, prompt, structured_output_model=None):
            assert "AI Agent 개발 경험" in prompt
            shown = self.tools[0]("FINT 경험")  # 도구를 실제로 불러 레지스트리를 채운다
            assert "[04_project/FINT.md:7]" in shown
            judgement = JudgementOut(
                status="확인",
                evidence=[EvidenceOut(ref="04_project/FINT.md:7"), EvidenceOut(ref="가짜.md:1")],
                note="n",
            )
            return SimpleNamespace(structured_output=judgement)

    mapper = StrandsMapper(agent_factory=FakeAgent, search_fn=fake_search, exclude_prefixes=("06_career/자소서/",))

    [mapping] = mapper.map([REQ])

    assert [e.ref for e in mapping.evidence] == ["04_project/FINT.md:7"]  # 가짜 인용은 걸러짐
    assert calls[0]["exclude"] == ("06_career/자소서/",)
    assert "mapper:i1-r1" in calls[0]["tag"]  # 로그에서 어느 요구의 검색인지 알 수 있다


def test_union_seconds_counts_overlap_once():
    from modular_agentic.engines.strands.search_tool import union_seconds

    assert union_seconds([]) == 0
    assert union_seconds([(0, 10), (2, 5)]) == 10  # 안에 포함된 구간
    assert union_seconds([(0, 10), (5, 15)]) == 15  # 걸친 구간
    assert union_seconds([(0, 4), (10, 12)]) == 6  # 떨어진 구간은 각각


def test_mapper_records_stats_with_and_without_model_metrics():
    def fake_search(query, n, exclude_prefixes, tag):
        return [hit("04_project/FINT.md")]

    class Usage(dict):
        pass

    class FakeAgent:
        def __init__(self, tools):
            self.tools = tools

        def __call__(self, prompt, structured_output_model=None):
            self.tools[0]("질의 1")
            self.tools[0]("질의 2")
            judgement = JudgementOut(status="확인", evidence=[EvidenceOut(ref="04_project/FINT.md:7")], note="n")
            metrics = SimpleNamespace(
                accumulated_metrics={"latencyMs": 4500},
                accumulated_usage={"inputTokens": 1200, "outputTokens": 300},
                cycle_count=3,
            )
            return SimpleNamespace(structured_output=judgement, metrics=metrics)

    mapper = StrandsMapper(agent_factory=FakeAgent, search_fn=fake_search)
    mapper.map([REQ])

    stats = mapper.stats[REQ.id]
    assert stats.search_calls == 2
    assert stats.model_seconds == 4.5
    assert (stats.input_tokens, stats.output_tokens, stats.cycles) == (1200, 300, 3)
    assert stats.search_wall_seconds <= stats.search_sum_seconds + 1e-9  # 겹침 반영은 단순 합보다 클 수 없다

    class NoMetricsAgent(FakeAgent):
        def __call__(self, prompt, structured_output_model=None):
            judgement = JudgementOut(status="TODO", evidence=[], note="n")
            return SimpleNamespace(structured_output=judgement)  # metrics 없음

    mapper2 = StrandsMapper(agent_factory=NoMetricsAgent, search_fn=fake_search)
    mapper2.map([REQ])
    assert mapper2.stats[REQ.id].model_seconds is None  # 없으면 None으로 둔다


def test_analyzer_does_not_call_model_for_empty_body():
    def factory(tools):
        raise AssertionError("본문이 비어 있으면 모델을 부르면 안 된다")

    analyzer = StrandsAnalyzer(agent_factory=factory)

    assert analyzer.analyze(Item(id="i1", source="t", title="공고", body="   ")) == []


def test_analyzer_builds_requirements_and_skips_blank():
    class FakeAgent:
        def __call__(self, prompt, structured_output_model=None):
            out = RequirementsOut(
                requirements=[RequirementOut(text=" A 경험 ", kind="우대사항"), RequirementOut(text="  ")]
            )
            return SimpleNamespace(structured_output=out)

    analyzer = StrandsAnalyzer(agent_factory=lambda tools: FakeAgent())

    reqs = analyzer.analyze(Item(id="i1", source="t", title="공고", body="본문"))

    assert [(r.id, r.item_id, r.text, r.kind) for r in reqs] == [("i1-r1", "i1", "A 경험", "우대사항")]
