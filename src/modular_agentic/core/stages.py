"""각 단계가 가져야 할 함수의 모양(인터페이스).

Protocol은 "이런 함수를 가지고 있으면 이 역할로 인정한다"는 규칙이다.
함수의 입력과 출력 모양만 정의하고 내용은 없다. 이렇게 해 두면 Strands로 만든 구현이든
Quick으로 만든 구현이든, 같은 모양이면 갈아 끼울 수 있다. 구현은 engines/에 둔다.

단계는 7개이고, 교차 관심사로 문장 검증(ClaimChecker)이 따로 있다.

    Collector  수집   — Item 목록을 가져온다
    Analyzer   분석   — Item 하나를 Requirement 목록으로 쪼갠다
    Researcher 조사   — 모인 자료(SourceDoc)를 읽고 출처가 붙은 조사 보고서(Brief)를 만든다(선택 단계)
    Planner    배정   — 문항마다 쓸 소재를 먼저 정한다(소재 계획, 선택 단계)
    Mapper     매핑   — Requirement마다 Evidence를 찾아 붙여 Mapping을 만든다
    Verifier   검증   — Mapping의 근거가 지금도 사실인지, 과장은 아닌지 판정한다
    Writer     작성   — 매핑과 검증 결과로 Draft를 쓴다
    ClaimChecker 문장 검증 — 글의 문장마다 쓴 근거가 뒷받침하는지 판정한다(작성 직후나 사용자가 고친 뒤에 부른다)

Verifier는 4단계 앞뒤에 끼는 교차 관심사다. 매핑 직후, 작성 직후 양쪽에서
부를 수 있어서 별도 인터페이스로 둔다. 지금 파이프라인은 매핑 직후에만 호출한다(pipeline.py).

검증이 필요한 이유: 근거처럼 보이지만 사실과 다른 서술이 반복해서 나왔다.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .types import Brief, Claim, ContextNote, Draft, Evidence, Item, Mapping, Plan, QuestionPlan, Requirement, SentenceCheck, SourceDoc, Verdict


class Collector(Protocol):
    def collect(self) -> list[Item]:
        """처리할 Item들을 가져온다. 인스턴스마다 출처가 다르다."""
        ...


class Analyzer(Protocol):
    def analyze(self, item: Item) -> list[Requirement]:
        """Item을 Requirement 목록으로 쪼갠다.

        item.body가 비어 있으면 내용을 지어내지 말고 빈 목록을 돌려준다.
        """
        ...


class Researcher(Protocol):
    def research(self, item: Item, docs: list[SourceDoc]) -> Brief:
        """docs에 실제로 있는 내용만으로 조사 보고서(Brief)를 만든다. docs에 없는 사실은 쓰지 않는다.

        읽을 수 있는 docs가 없으면 모델을 부르지 않고 빈 Brief를 돌려준다(Analyzer가 본문이 비었을 때 하는 것과 같다).
        못 읽은 docs는 Brief.unread에, 자료가 없어 못 다룬 주제는 Brief.gaps에 남긴다.
        자료를 모으는 일(웹 가져오기, 업로드 읽기)은 단계가 아니라 tools/sources.py의 일이다.
        """
        ...


class Mapper(Protocol):
    def map(self, requirements: list[Requirement]) -> list[Mapping]:
        """각 Requirement에 맞는 Evidence를 찾아 Mapping으로 만든다.

        어디를 검색하는지는 인스턴스가 정한다 (자소서: knowledge-base, 제안서: 표준 제안서).
        """
        ...


class Verifier(Protocol):
    def verify(self, mappings: list[Mapping]) -> list[Verdict]:
        """Mapping의 근거가 현재도 사실인지, 표현이 과장은 아닌지 판정한다.

        Mapping 하나당 Verdict 하나를 돌려준다. 무엇과 대조하는지는 인스턴스가 정한다
        (자소서: 구직 이력 같은 상태 페이지, 제안서: 실제 시스템 조회).
        """
        ...


class Planner(Protocol):
    def plan(
        self,
        item: Item,
        questions: list[str],
        mappings: list[Mapping],
        verdicts: list[Verdict],
        *,
        brief: Brief | None = None,
        extra: Sequence[Mapping] = (),
        basic_requirement_ids: Sequence[str] = (),
        notes: str = "",
        variant: str = "",
        contexts: dict[str, list[ContextNote]] | None = None,
    ) -> Plan:
        """문항 여러 개에 쓸 소재를 먼저 배정한다(소재 계획).

        문항마다 중심 경험 1~2개와 근거, 각도, 쓸 회사 사실을 정하고, 한 소재가 두 문항의 중심이 되지 않게 한다.
        extra: 문항 하나만을 위한 추가 재료 매핑(예: 고른 가치의 근거). basic_requirement_ids는 어필하지 않는 요구.
        contexts: 요구 id → 회사 맥락(분석 단계가 붙인 조사 주장 메모). 참고일 뿐 지원자의 근거가 아니다.
        모델이 적은 id·근거 위치는 입력에 있던 것만 인정한다.
        """
        ...


class Writer(Protocol):
    def write(
        self,
        item: Item,
        question: str,
        mappings: list[Mapping],
        verdicts: list[Verdict],
        *,
        n: int = 1,
        focus: Sequence[str] | None = None,
        previous: Sequence[Draft] = (),
        brief: Brief | None = None,
        plan: QuestionPlan | None = None,
        extra: Sequence[Mapping] = (),
    ) -> list[Draft]:
        """매핑과 검증 결과로 question에 대한 Draft를 n개 쓴다. 서로 다른 각도로 쓴다.

        FLAGGED 판정을 받은 Mapping의 근거는 사실처럼 쓰면 안 된다.
        focus: 재료로 쓸 요구의 id들. 주면 그 요구들만 재료로 쓰고, 없으면(None) 쓸 수 있는 요구 전부를 쓴다.
        previous: 이미 낸 초안들. 주면 그것들과 다른 각도로 쓴다("더 뽑기").
        brief: 사용자가 승인한 조사 보고서. 주면 회사에 대한 서술은 이 보고서의 주장에서만 가져온다.
               None이면 이전과 똑같이 동작한다(호출하는 쪽은 None일 때 이 인자를 아예 넘기지 않아도 된다).
        plan: 사용자가 승인한 소재 계획에서 이 문항의 배정. 주면 그 중심 경험만 재료로 쓰고, 각도·쓰지 않을 소재·조심할 점을 따르며,
              회사 사실은 배정된 것만 쓴다(focus는 무시한다). 초안이 여러 개여도 소재는 같고 구조·강조만 다르다.
        extra: 배정된 근거 중 매핑 결과에 없는 것(가치 매핑, 사용자가 지정한 근거)을 담은 추가 매핑. plan의 근거 발췌를 찾는 데만 쓴다.
        """
        ...


class ClaimChecker(Protocol):
    def check(self, text: str, evidence: Sequence[Evidence], claims: Sequence[Claim]) -> list[SentenceCheck]:
        """글의 문장마다 evidence(내 근거)와 claims(회사 사실)가 뒷받침하는지 판정한다.

        문장 하나당 SentenceCheck 하나를 돌려준다(빠뜨린 문장은 근거 없음으로 채운다). 입력에 없는 근거는 쓰지 않는다.
        """
        ...


class ContextLinker(Protocol):
    def link(self, requirements: Sequence[Requirement], brief: Brief) -> tuple[dict[str, list[ContextNote]], list[str]]:
        """요구마다 관련 있는 조사 주장(회사 맥락)을 붙인다. (요구 id → 메모들, 경고들)을 돌려준다.

        요구의 글자를 바꾸거나 새 요구를 만들지 않는다. 입력에 없는 요구 번호·주장 id는 인정하지 않는다.
        """
        ...

