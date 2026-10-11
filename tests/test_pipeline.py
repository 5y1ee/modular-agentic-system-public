"""core/pipeline.run()이 단계를 순서대로 부르고 결과를 돌려주는지 확인한다.

LLM이나 외부 서비스 없이, 정해진 값을 돌려주는 가짜 단계(fake)로 검증한다.
이 테스트가 core의 첫 검증이다. 엔진·인스턴스는 아직 없다.
"""
from modular_agentic.core.pipeline import Stages, run
from modular_agentic.core.types import (
    Draft,
    Evidence,
    Item,
    Mapping,
    MappingStatus,
    Requirement,
    Verdict,
    VerdictKind,
)

calls: list[str] = []  # 단계가 불린 순서를 기록한다


class FakeAnalyzer:
    def analyze(self, item: Item) -> list[Requirement]:
        calls.append("analyze")
        return [
            Requirement(id="r1", item_id=item.id, text="AI Agent 개발 경험"),
            Requirement(id="r2", item_id=item.id, text="Kubernetes 운영 경험"),
        ]


class FakeMapper:
    def map(self, requirements: list[Requirement]) -> list[Mapping]:
        calls.append("map")
        return [
            Mapping(
                requirement=requirements[0],
                evidence=[Evidence(ref="FINT", excerpt="Strands로 에이전트를 만들었다")],
                status=MappingStatus.CONFIRMED,
            ),
            Mapping(requirement=requirements[1], evidence=[], status=MappingStatus.TODO),
        ]


class FakeVerifier:
    def verify(self, mappings: list[Mapping]) -> list[Verdict]:
        calls.append("verify")
        return [Verdict(requirement_id=m.requirement.id, kind=VerdictKind.APPROVED) for m in mappings]


class FakeWriter:
    def write(
        self,
        item: Item,
        question: str,
        mappings: list[Mapping],
        verdicts: list[Verdict],
        *,
        n: int = 1,
        focus=None,
        previous=(),
    ) -> list[Draft]:
        calls.append("write")
        todos = [m.requirement.text for m in mappings if m.status is MappingStatus.TODO]
        return [Draft(item_id=item.id, sections={question: f"초안{i}"}, todos=todos) for i in range(1, n + 1)]


def make_stages() -> Stages:
    return Stages(
        analyzer=FakeAnalyzer(),
        mapper=FakeMapper(),
        verifier=FakeVerifier(),
        writer=FakeWriter(),
    )


def test_run_calls_stages_in_order():
    calls.clear()
    item = Item(id="i1", source="test", title="공고", body="본문")

    run(item, "지원동기", make_stages())

    assert calls == ["analyze", "map", "verify", "write"]


def test_run_returns_drafts_and_intermediates():
    calls.clear()
    item = Item(id="i1", source="test", title="공고", body="본문")

    result = run(item, "지원동기", make_stages(), n_drafts=3)

    assert [d.sections["지원동기"] for d in result.drafts] == ["초안1", "초안2", "초안3"]  # 초안 개수는 호출하는 쪽이 정한다
    assert result.drafts[0].item_id == "i1"
    assert result.drafts[0].todos == ["Kubernetes 운영 경험"]  # TODO 상태가 Draft로 전달됨
    assert len(result.mappings) == 2
    assert len(result.verdicts) == 2


# ── 조사(선택 단계) ──

from modular_agentic.core.types import Brief, Citation, Claim, SourceDoc, SourceKind  # noqa: E402


class FakeResearcher:
    def __init__(self):
        self.seen_docs = None

    def research(self, item, docs):
        calls.append("research")
        self.seen_docs = list(docs)
        return Brief(item_id=item.id, subject="회사", claims=[Claim("c1", "사실", "사업", [Citation("d1", "원문 발췌 문장입니다")])])


class BriefAwareWriter(FakeWriter):
    def write(self, item, question, mappings, verdicts, *, n=1, focus=None, previous=(), brief=None):
        self.brief = brief
        return super().write(item, question, mappings, verdicts, n=n, focus=focus, previous=previous)


def test_run_without_researcher_skips_research_and_does_not_pass_brief():
    calls.clear()
    item = Item(id="i1", source="test", title="공고", body="본문")
    result = run(item, "지원동기", make_stages())  # brief를 모르는 기존 FakeWriter도 그대로 돈다
    assert "research" not in calls and result.brief is None


def test_run_with_researcher_researches_after_analysis_and_hands_the_brief_to_the_writer():
    calls.clear()
    item = Item(id="i1", source="test", title="공고", body="본문")
    researcher, writer = FakeResearcher(), BriefAwareWriter()
    stages = Stages(analyzer=FakeAnalyzer(), mapper=FakeMapper(), verifier=FakeVerifier(), writer=writer, researcher=researcher)
    docs = [SourceDoc(id="d1", kind=SourceKind.POSTING, locator="공고", text="본문")]

    result = run(item, "지원동기", stages, docs=docs)

    assert calls == ["analyze", "research", "map", "verify", "write"]
    assert researcher.seen_docs == docs and writer.brief is result.brief and result.brief.claims[0].id == "c1"


def test_a_given_brief_is_used_as_is_without_researching_again():
    calls.clear()
    item = Item(id="i1", source="test", title="공고", body="본문")
    approved = Brief(item_id="i1", subject="회사")
    writer = BriefAwareWriter()
    stages = Stages(analyzer=FakeAnalyzer(), mapper=FakeMapper(), verifier=FakeVerifier(), writer=writer, researcher=FakeResearcher())

    result = run(item, "지원동기", stages, brief=approved)

    assert "research" not in calls and writer.brief is approved and result.brief is approved
