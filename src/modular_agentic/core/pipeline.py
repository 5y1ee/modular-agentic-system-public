"""단계들을 순서대로 호출하는 함수.

main.py(실행 진입점)가 아니다. 이 파일은 `run()` 함수 하나를 제공하고,
실행하는 쪽(CLI, 평가 코드, AgentCore 배포 entrypoint)이 같은 함수를 부른다.
진입점과 분리해 두는 이유는 같은 파이프라인을 여러 곳에서 재사용하기 위해서다.

흐름: analyze → (research) → map → verify → write (초안은 여러 개. 사용자 피드백으로 작성만 다시 부르는 루프는 호출하는 쪽이 만든다)

조사(research)는 선택이다. researcher가 있을 때만 돈다. 조사 보고서(Brief)는 자소서의 방향을 정하므로 사용자가 읽고
승인한 뒤에 작성에 넘기는 것이 원칙이다(사용자 확인 지점, docs/design-research-stage.md 9절). 그 "멈춤"은 run() 안이 아니라
호출하는 쪽의 일이다: 먼저 researcher.research()만 불러 Brief를 저장·확인받고, 승인된 Brief를 run(brief=...)로 넘긴다.
brief를 직접 주면 run()은 조사를 다시 하지 않는다.
Collector는 여기 없다. Item을 모으는 일은 한 번에 여러 건을 다루므로 호출하는 쪽에서
collect()로 Item을 얻은 뒤 Item마다 run()을 부른다.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .stages import Analyzer, Mapper, Researcher, Verifier, Writer
from .types import Brief, Draft, Item, Mapping, SourceDoc, Verdict


@dataclass(frozen=True)
class Stages:
    """파이프라인에 끼울 구현들의 묶음. engines/와 instances/가 이걸 조립해서 넘긴다."""

    analyzer: Analyzer
    mapper: Mapper
    verifier: Verifier
    writer: Writer
    researcher: Researcher | None = None  # 없으면 조사를 건너뛴다


@dataclass(frozen=True)
class RunResult:
    """최종 Draft들과 중간 산출물.

    중간 산출물을 같이 돌려주는 이유: eval/이 매핑·검증 단계를 따로 채점해야 하고,
    사용자도 초안이 왜 그렇게 나왔는지 확인할 수 있어야 한다.
    """

    drafts: list[Draft]
    mappings: list[Mapping]
    verdicts: list[Verdict]
    brief: Brief | None = None


def run(
    item: Item,
    question: str,
    stages: Stages,
    n_drafts: int = 1,
    *,
    docs: Sequence[SourceDoc] = (),
    brief: Brief | None = None,
) -> RunResult:
    """Item 하나를 분석 → (조사) → 매핑 → 검증 → 작성 순으로 처리한다. 초안은 n_drafts개 나온다.

    brief를 주면 그것을 쓰고, 안 주었는데 stages.researcher가 있으면 docs로 조사한다.
    brief가 None이면 writer에 brief 인자를 넘기지 않는다(brief를 모르는 기존 Writer 구현도 그대로 돈다).
    """
    requirements = stages.analyzer.analyze(item)
    if brief is None and stages.researcher is not None:
        brief = stages.researcher.research(item, list(docs))
    mappings = stages.mapper.map(requirements)
    verdicts = stages.verifier.verify(mappings)
    extra = {} if brief is None else {"brief": brief}
    drafts = stages.writer.write(item, question, mappings, verdicts, n=n_drafts, **extra)
    return RunResult(drafts=drafts, mappings=mappings, verdicts=verdicts, brief=brief)
