"""구현체 목록: 단계마다 꽂을 수 있는 구현체와 고르는 스위치.

core/stages.py의 인터페이스가 콘센트라면 여기 적힌 것이 꽂을 수 있는 플러그다. 스크립트는 구현 클래스를 직접 만들지 않고
`make("analyzer")`처럼 여기서 꺼낸다. 어느 것을 꺼낼지는 인자 `name` → 환경변수 `MAS_IMPL_<단계>` → 목록의 첫 구현 순으로 정한다.
콘솔은 작업을 실행할 때 이 환경변수를 넘겨 화면에서 고른 구현을 쓰게 한다.

갈아 끼우는 축은 셋이다(2026-10-21 정리).
- 목적: 같은 단계를 다른 출처·방식으로 한다(수집: 크롤러 결과 / 웹 검색 에이전트 / 붙여넣기).
- 엔진: 같은 일을 다른 프레임워크로 만든다(분석: Strands / Bedrock 직접 호출).
- 부품: 같은 구현체 안의 교체 가능한 부분(모델 `MAS_MODEL_ID`, 검색 방식 `MAS_QMD_URL`·`MAS_MCP_URL`). 부품은 이 목록이 아니라 환경변수로 바꾼다.
구현이 하나뿐인 단계도 목록에 올려 둔다(화면에 "구현 1개"로 보인다). 구현 클래스는 고를 때만 불러온다(Strands가 없는 환경에서도 목록은 읽힌다).
"""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Impl:
    stage: str
    name: str
    label: str
    axis: str  # 첫 구현과 무엇이 다른가: "기본" | "목적" | "엔진"
    target: str  # "모듈:클래스"
    description: str = ""


def _strands(stage: str, module: str, cls: str, label: str = "Strands 에이전트", **kw) -> Impl:
    return Impl(stage, "strands", label, "기본", f"modular_agentic.engines.strands.{module}:{cls}", **kw)


IMPLS: dict[str, list[Impl]] = {
    "collector": [
        Impl("collector", "inthiswork", "인디스워크 크롤러 결과 읽기", "기본", "modular_agentic.engines.basic.collectors:InthisworkCollector",
             "inthiswork-crawler가 매일 모은 JSON(data/daily/<날짜>.json)에서 공고를 읽는다. 모델 호출 없음"),
        Impl("collector", "web_agent", "웹 검색 에이전트", "목적", "modular_agentic.engines.strands.web_collector:StrandsWebCollector",
             "에이전트가 검색·페이지 읽기·중복 확인 도구를 골라 가며 공고 원문을 찾는다. 코드가 후보가 실제로 읽은 페이지에 있는지 대조한다"),
        Impl("collector", "paste", "붙여넣기", "목적", "modular_agentic.engines.basic.collectors:PasteCollector",
             "사람이 공고 글을 그대로 붙여 넣는다. 모델 호출 없음"),
    ],
    "researcher": [_strands("researcher", "researcher", "StrandsResearcher")],
    "analyzer": [
        _strands("analyzer", "analyzer", "StrandsAnalyzer"),
        Impl("analyzer", "bedrock", "Bedrock 직접 호출(Strands 없음)", "엔진", "modular_agentic.engines.basic.bedrock_analyzer:BedrockAnalyzer",
             "boto3로 Bedrock Converse API를 직접 부른다. 같은 일을 다른 엔진으로 한 비교용 구현"),
    ],
    "context_linker": [_strands("context_linker", "context_linker", "StrandsContextLinker")],
    "mapper": [_strands("mapper", "mapper", "StrandsMapper", "Strands 에이전트(vault 검색 도구)")],
    "verifier": [_strands("verifier", "verifier", "StrandsVerifier")],
    "planner": [_strands("planner", "planner", "StrandsPlanner")],
    "writer": [_strands("writer", "writer", "StrandsWriter")],
    "claim_checker": [_strands("claim_checker", "claim_checker", "StrandsClaimChecker")],
    "transcriber": [_strands("transcriber", "transcriber", "StrandsTranscriber", "Bedrock 비전 모델(Strands)")],
}


def env_name(stage: str) -> str:
    return f"MAS_IMPL_{stage.upper()}"


def choose(stage: str, name: str | None = None) -> Impl:
    """이름 → 환경변수 → 첫 구현 순으로 고른다. 모르는 이름이면 있는 이름을 알려 주며 실패한다."""
    impls = IMPLS.get(stage)
    if not impls:
        raise KeyError(f"모르는 단계: {stage} (있는 것: {', '.join(IMPLS)})")
    wanted = name or os.environ.get(env_name(stage), "").strip() or impls[0].name
    for impl in impls:
        if impl.name == wanted:
            return impl
    raise KeyError(f"{stage}에 '{wanted}' 구현이 없다 (있는 것: {', '.join(i.name for i in impls)})")


def make(stage: str, name: str | None = None, **kwargs: Any) -> Any:
    """고른 구현체를 만든다. kwargs는 생성자 인자다. 같은 단계의 구현체는 같은 인자를 받아야 한다(예: writer의 guidance,
    verifier의 experiences, researcher의 topics). 새 구현체를 넣을 때 이 약속을 지킨다.
    예외는 수집(collector)이다. 목적이 다른 구현체라 받는 설정이 다르다(크롤러: day, 웹 에이전트: goal, 붙여넣기: title·text)."""
    impl = choose(stage, name)
    module, cls = impl.target.split(":")
    return getattr(importlib.import_module(module), cls)(**kwargs)


def describe() -> dict[str, list[dict]]:
    """화면·채팅에 보일 목록(클래스를 불러오지 않는다)."""
    return {stage: [{"name": i.name, "label": i.label, "axis": i.axis, "description": i.description} for i in impls]
            for stage, impls in IMPLS.items()}
