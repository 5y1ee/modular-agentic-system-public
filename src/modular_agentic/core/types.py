"""단계 사이를 오가는 데이터의 이름과 모양.

한 건의 데이터가 파이프라인을 지나며 모양이 바뀐다. 그 모양마다 이름을 붙인 것이다.
로직은 없고 정의만 있다.

흐름 (자소서 인스턴스 예시):

    Item            채용공고 1건
      ↓ 분석
    Requirement     공고가 요구하는 항목 하나 ("AI Agent 개발 Framework 경험")
      ↓ 매핑
    Mapping         요구 하나 + 그에 맞는 내 근거(Evidence)들 + 상태(확인/부분확인/TODO)
      ↓ 검증
    Verdict         그 근거가 지금도 사실인지, 표현이 과장은 아닌지에 대한 판정
      ↓ 작성
    Draft           문항별 답변 초안(여러 개)

조사(Researcher)는 이 흐름과 따로 한 줄 더 있다. 작성 단계에 "상대(회사·고객사)에 대한 사실"을 대 준다.

    SourceDoc       조사에 쓰는 자료 한 건(웹 페이지, 사용자가 올린 파일, 공고 본문)
      ↓ 조사
    Brief           출처가 붙은 주장(Claim)들 + 못 읽은 자료 + 자료가 없어 못 다룬 주제
                    (사용자가 읽고 승인한 것만 작성에 넘긴다. docs/design-research-stage.md)

소재 계획(Planner)는 작성 앞에 선다. 문항이 여러 개일 때 문항마다 쓸 소재를 먼저 정한다.

    Mapping들 + Brief ─ 배정 → Plan   문항마다 중심 경험·근거·각도·회사 사실, 다른 문항 몫인 소재는 "안 쓴다"로 표시
                                    (사용자가 읽고 승인한 것만 작성이 쓴다)

이름은 일부러 도메인 중립으로 지었다. 제안서 인스턴스라면 Item은 RFP 문서,
Requirement는 RFP 요구사항 한 줄이 되고, Brief는 고객사 분석이 된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class Item:
    """처리할 대상 한 건.

    자소서: 채용공고 1건 / 제안서: RFP 문서 1건.
    body가 비어 있는 경우(예: 본문이 이미지뿐인 공고)는 정상적으로 일어난다.
    그 경우 분석 단계는 내용을 추측하지 말고 "알 수 없음"으로 다뤄야 한다.
    """

    id: str
    source: str  # 어디서 왔는지 (예: "inthiswork")
    title: str
    body: str
    meta: dict[str, str] = field(default_factory=dict)  # 회사명·마감일·링크 등 인스턴스별 부가 정보


@dataclass(frozen=True)
class Requirement:
    """Item이 요구하는 항목 하나. 분석 단계의 출력."""

    id: str
    item_id: str
    text: str
    kind: str = ""  # 인스턴스가 정하는 분류 (예: 자소서는 "자격요건" / "우대사항")


@dataclass(frozen=True)
class Evidence:
    """요구를 뒷받침하는 내 쪽 근거 하나.

    ref는 근거가 나온 곳(자소서 인스턴스에서는 knowledge-base 페이지 경로),
    excerpt는 그 곳에서 가져온 발췌. 검증 단계가 ref를 다시 열어 확인할 수 있어야 한다.
    """

    ref: str
    excerpt: str


class MappingStatus(Enum):
    """요구 하나에 대해 근거를 얼마나 찾았는가."""

    CONFIRMED = "확인"  # 근거가 요구를 충분히 뒷받침
    PARTIAL = "부분확인"  # 일부만 뒷받침
    TODO = "TODO"  # 근거 없음 — 사용자가 직접 채워야 할 경험


@dataclass(frozen=True)
class Mapping:
    """Requirement 하나와 그에 맞는 Evidence들, 그리고 상태. 매핑 단계의 출력."""

    requirement: Requirement
    evidence: list[Evidence]
    status: MappingStatus
    note: str = ""  # 왜 그 상태로 판단했는지 한두 줄


class VerdictKind(Enum):
    APPROVED = "승인"
    FLAGGED = "과장·불일치 플래그"


@dataclass(frozen=True)
class Verdict:
    """검증 결과 한 건. Mapping 하나(requirement_id로 가리킴)에 대한 판정.

    예: "격리 버그를 검증"이라고 썼지만 실제로는 "설계 단계부터 예방"이었던 경우
    → FLAGGED + reason에 실제 사실을 적는다.
    """

    requirement_id: str
    kind: VerdictKind
    reason: str = ""


@dataclass(frozen=True)
class Draft:
    """답변 초안 하나. 작성 단계의 출력. 같은 문항에 여러 개가 나온다(사용자가 고르거나 피드백을 준다).

    sections: 문항(또는 섹션) 이름 → 초안 본문
    todos: 근거가 없어 사용자가 직접 채워야 하는 항목 (MappingStatus.TODO에서 온다)
    angle: 이 초안이 무엇을 강조했는지 한 줄 (초안끼리 다르게 쓰려고 정하는 각도)
    rationale: 왜 이렇게 썼는지 (어느 요구에 적합한지 포함)
    covered_requirement_ids: 이 초안이 다룬 요구들(Requirement.id)
    used_refs: 본문이 근거로 삼은 Evidence의 ref들. 사용자가 사실을 대조할 수 있게 남긴다.
    used_claims: 본문이 쓴 회사 사실(Claim.id)들. used_refs가 "내 경험"의 출처라면 이쪽은 "회사 사실"의 출처다.
    warnings: 사람이 봐야 할 점(코드가 찾은 것). 예: 분량 초과, 플래그된 근거를 사용
    """

    item_id: str
    sections: dict[str, str]
    todos: list[str] = field(default_factory=list)
    angle: str = ""
    rationale: str = ""
    covered_requirement_ids: list[str] = field(default_factory=list)
    used_refs: list[str] = field(default_factory=list)
    used_claims: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SourceKind(Enum):
    """조사 자료가 어디서 왔는가."""

    WEB = "웹"
    UPLOAD = "업로드"
    POSTING = "공고"


class TextOrigin(Enum):
    """SourceDoc.text를 어떻게 얻었는가. 믿을 수 있는 정도가 다르다."""

    ORIGINAL = "원문"  # 글자 그대로(텍스트 파일, 웹 본문, 공고)
    EXTRACTED = "추출"  # 라이브러리가 PDF의 텍스트 층에서 뽑음
    TRANSCRIBED = "전사"  # 모델이 이미지·PDF를 보고 옮겨 적음 — 읽기 오류가 섞일 수 있다


class ReadStatus(Enum):
    READ = "읽음"
    UNREAD = "못 읽음"  # 로그인 벽·봇 차단·JS 렌더링·지원하지 않는 형식 등. reason에 이유를 적는다.


@dataclass(frozen=True)
class SourceDoc:
    """조사에 쓰는 자료 한 건. 모으는 쪽(tools/sources.py)이 만들고 Researcher가 읽는다.

    id: 한 번의 조사 안에서 겹치지 않는 이름(d1, d2…). Citation이 이걸로 자료를 가리킨다.
    locator: 어디서 왔는지(URL 또는 파일 경로). 사용자가 원본을 열어 볼 수 있어야 한다.
    found_by: 사용자가 준 자료(공고·업로드 파일·직접 준 URL)는 빈 문자열, 시스템이 검색으로 찾은 자료는 "검색: <질의>".
    보고서에서 사용자가 "내가 준 것"과 "시스템이 찾은 것"을 가르고, 찾은 것을 더 의심해서 볼 수 있게 남긴다.
    status가 UNREAD이면 text는 비어 있고 reason에 이유가 있다. 못 읽은 자료를 조용히 버리지 않고
    Brief.unread로 사용자에게 보여 주려고 자료로 남긴다.
    """

    id: str
    kind: SourceKind
    locator: str
    text: str = ""
    text_origin: TextOrigin = TextOrigin.ORIGINAL
    status: ReadStatus = ReadStatus.READ
    reason: str = ""
    found_by: str = ""


@dataclass(frozen=True)
class Citation:
    """주장의 출처: 어느 자료의 어느 원문 발췌인가. 코드가 quote가 자료에 실제로 있는지 대조한다."""

    doc_id: str
    quote: str


@dataclass(frozen=True)
class Claim:
    """보고서의 주장 하나. 출처(Citation)가 하나 이상 있어야 한다.

    id: Brief 안에서 겹치지 않는 이름(c1, c2…). 사용자가 틀린 주장을 지울 때와 초안이 쓴 주장을 남길 때 쓴다.
    topic: 주장이 속한 주제(사업·제품·최근 동향 등). 목록은 인스턴스가 정한다.
    """

    id: str
    text: str
    topic: str
    citations: list[Citation]


@dataclass(frozen=True)
class Brief:
    """조사 보고서. 조사 단계의 출력.

    claims: 자료에서 출처와 함께 뽑은 주장들
    unread: 못 읽은 자료(status가 UNREAD인 SourceDoc). 사용자가 자료로 보완하라고 보여 준다.
    gaps: 자료가 없어 다루지 못한 주제
    warnings: 코드가 찾은 점. 예: 인용이 자료와 맞지 않아 버린 주장 수, 길이 때문에 잘린 자료
    """

    item_id: str
    subject: str  # 조사 대상 이름(회사명 등)
    claims: list[Claim] = field(default_factory=list)
    unread: list[SourceDoc] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlannedExperience:
    """문항의 중심 경험 하나. 근거(refs)는 매핑이 찾아 둔 Evidence의 ref여야 한다."""

    name: str  # 알아볼 수 있는 이름(프로젝트·사건)
    why: str  # 이 문항의 중심 경험으로 고른 이유
    refs: list[str] = field(default_factory=list)
    requirement_ids: list[str] = field(default_factory=list)  # 이 경험이 뒷받침하는 요구들


@dataclass(frozen=True)
class QuestionPlan:
    """문항 하나의 소재 배정.

    reading: 문항이 묻는 것의 해독
    angle: 이 문항의 각도(다른 문항과 겹치지 않게)
    experiences: 중심 경험 1~2개
    claim_ids: 쓸 회사 사실(Brief의 Claim.id)
    avoid: 다른 문항이 중심 경험으로 쓰는 소재의 이름. 이 문항에서는 쓰지 않는다(코드가 채운다)
    """

    number: int
    question: str
    reading: str
    angle: str
    experiences: list[PlannedExperience] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class Plan:
    """소재 계획. 소재 배정 단계의 출력. variant는 같은 문항을 다르게 배정해 비교할 때의 이름(예: 고른 가치)."""

    item_id: str
    variant: str
    questions: list[QuestionPlan]
    basic_requirement_ids: list[str] = field(default_factory=list)  # 어필·검증하지 않기로 한 "기본" 요구
    warnings: list[str] = field(default_factory=list)


class Support(Enum):
    """초안의 문장 하나를 쓴 근거가 얼마나 뒷받침하는가(문장 검증의 판정)."""

    SUPPORTED = "뒷받침됨"  # 문장의 사실 주장이 모두 근거에 있다
    PARTIAL = "일부만"  # 일부만 근거에 있고 나머지(수치·인과·동기·범위)는 없다
    UNSUPPORTED = "근거 없음"  # 근거에 없다(모델이 아는 사실이어도 근거에 없으면 여기다)
    CONTRADICTED = "근거와 다름"  # 근거와 반대이거나 다른 사실이다
    NO_CLAIM = "사실 주장 없음"  # 다짐·의견·연결처럼 확인할 사실이 없는 문장


@dataclass(frozen=True)
class SentenceCheck:
    """초안 문장 하나의 검증 결과.

    index: 글 안의 문장 번호(1부터). quote·source: 뒷받침한다고 댄 근거 구절과 그 출처(근거 ref 또는 회사 사실 id).
    코드가 quote가 source 원문에 실제로 있는지 대조해서, 없으면 판정을 내린다(core/claim_check.py).
    note: 일부만·근거 없음·다름일 때 무엇이 빠졌는지, 또는 코드가 판정을 바꾼 이유.
    """

    index: int
    sentence: str
    support: Support
    source: str = ""
    quote: str = ""
    note: str = ""


@dataclass(frozen=True)
class ContextNote:
    """요구 하나에 붙는 회사 맥락 메모 하나: 관련 있는 조사 주장의 id와, 어떻게 관련 있는지 한 문장.

    요구의 글자를 바꾸지 않는 참고이고, 지원자의 근거(Evidence)가 아니다. 주장은 사용자가 승인한 조사 보고서의 것만 쓴다.
    """

    claim_id: str
    reason: str


@dataclass(frozen=True)
class Analysis:
    """분석 단계의 저장 결과(logs/02_analyze). 요구 목록과 사용자가 표시한 기본 요건, 요구별 회사 맥락.

    basic_requirement_ids: 사용자가 "기본 요건이라 어필·검증하지 않는다"고 표시한 요구의 id
    context: 요구 id → 회사 맥락 메모들. 맥락이 없는 요구는 키가 없다(관련 있는 주장이 없는 것이 정상이다)
    """

    item_id: str
    requirements: list[Requirement]
    basic_requirement_ids: list[str] = field(default_factory=list)
    context: dict[str, list[ContextNote]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

