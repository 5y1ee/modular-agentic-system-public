"""모델이 채워서 돌려주는 답의 형식(Pydantic).

Strands의 `agent(프롬프트, structured_output_model=이 클래스)`로 부르면 모델의 답이 이 형식으로 검증돼
`result.structured_output`에 담긴다. 형식에 안 맞으면 모델에게 다시 시킨다.

필드 설명(Field description)은 모델에게 그대로 전달되므로, 모델이 읽는 지시문이기도 하다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RequirementOut(BaseModel):
    text: str = Field(description="공고가 요구하는 항목 하나. 원문 표현을 최대한 유지한다.")
    kind: str = Field(default="", description="공고의 분류(자격요건, 우대사항, 담당업무 등). 모르면 빈 문자열.")


class RequirementsOut(BaseModel):
    requirements: list[RequirementOut] = Field(description="공고에서 뽑은 요구 항목들. 요구가 없으면 빈 목록.")


class EvidenceOut(BaseModel):
    ref: str = Field(description="검색 결과에 나온 '경로:줄'을 글자 그대로. 검색하지 않은 글은 쓰지 않는다.")


class JudgementOut(BaseModel):
    status: Literal["확인", "부분확인", "TODO"] = Field(
        description="확인=발췌가 요구를 충분히 뒷받침, 부분확인=일부만 뒷받침, TODO=뒷받침하는 발췌 없음"
    )
    evidence: list[EvidenceOut] = Field(description="판단의 근거가 된 검색 결과들. TODO이면 빈 목록.")
    note: str = Field(description="판단 이유 한두 문장. 부분확인이면 무엇이 모자란지 쓴다.")


class VerdictOut(BaseModel):
    experience: str = Field(description="이 근거가 서술하는 경험의 이름. 후보 경험이 주어지면 그 이름을 그대로 쓴다.")
    keywords: list[str] = Field(
        description="그 경험을 알아보는 단어들(프로젝트·회사·기술 이름). 후보 경험이 주어지면 그 키워드를 그대로 쓴다."
    )
    kind: Literal["승인", "플래그"] = Field(
        description="승인=검색한 글들과 모순이 없고 매핑 판단이 경험이 실제로 한 것을 넘지 않음, 플래그=어긋나는 서술·정정이 있거나 판단이 과장됨"
    )
    reason: str = Field(description="판정 이유 한두 문장. 플래그면 실제 사실이 무엇인지, 승인이면 무엇을 확인했는지 쓴다.")
    conflicting: list[EvidenceOut] = Field(
        description="플래그의 근거가 된 검색 결과들(정정·어긋나는 서술). 승인이면 빈 목록."
    )


class DraftOut(BaseModel):
    angle: str = Field(description="이 초안이 무엇을 강조하는지 한 줄. 다른 초안과 겹치지 않게 정한다.")
    text: str = Field(description="문항에 대한 답변 초안 본문. 첫 줄은 대괄호 헤드라인, 한 줄 띄우고 본문.")
    covered_requirement_ids: list[str] = Field(
        description="이 초안이 다룬 요구의 id들. 입력에 나온 `[요구 <id>]`의 id를 글자 그대로 쓴다."
    )
    used_refs: list[str] = Field(description="본문이 근거로 삼은 `경로:줄`들. 입력에 나온 것을 글자 그대로 쓴다.")
    used_claim_ids: list[str] = Field(
        default_factory=list,
        description="본문이 쓴 회사 사실의 주장 id들. 입력에 나온 `[주장 <id>]`의 id를 글자 그대로 쓴다. 회사 사실을 안 썼으면 빈 목록.",
    )
    rationale: str = Field(description="왜 이렇게 썼는지 한두 문장. 어느 요구에 적합한지 요구 id와 함께 쓴다.")


class DraftsOut(BaseModel):
    drafts: list[DraftOut] = Field(description="서로 다른 각도의 초안들")


class GuidanceCandidateOut(BaseModel):
    text: str = Field(description="다음 초안에 적용할 작성 지침 한 줄. 명령형으로 짧게.")
    scope: Literal["일반", "특정"] = Field(
        description="일반=문체·구조·분량·표현 선호(다른 공고·문항에도 맞음), 특정=이 공고·문항의 내용(어떤 경험을 강조할지 등)"
    )
    reason: str = Field(description="어떤 피드백에서 나왔는지 한 문장. 추정이면 추정이라고 쓴다.")


class GuidanceCandidatesOut(BaseModel):
    candidates: list[GuidanceCandidateOut] = Field(description="지침 후보들. 근거가 부족하면 빈 목록.")


class CitationOut(BaseModel):
    doc_id: str = Field(description="출처 자료의 id. 입력에 나온 `[자료 <id> ...]`의 id를 글자 그대로 쓴다.")
    quote: str = Field(description="그 자료 본문에서 글자 그대로 옮긴 발췌(한두 문장). 요약·말바꿈·이어붙이기 금지.")


class ClaimOut(BaseModel):
    text: str = Field(description="자료가 말하는 사실 하나를 한 문장으로. 자료에 없는 내용은 쓰지 않는다.")
    topic: str = Field(description="주장이 속한 주제. 입력에 나온 조사 주제 중 하나를 그대로 쓴다.")
    citations: list[CitationOut] = Field(description="이 주장을 뒷받침하는 발췌들. 하나 이상.")


class BriefOut(BaseModel):
    claims: list[ClaimOut] = Field(description="자료에서 뽑은 주장들. 자료에 근거가 없는 주제는 만들지 않는다.")
    gaps: list[str] = Field(description="자료가 없거나 부족해 다루지 못한 주제와 이유. 없으면 빈 목록.")


class PlannedExperienceOut(BaseModel):
    name: str = Field(description="경험의 이름. 프로젝트·사건을 알아볼 수 있게(예: '프로젝트A PoolTimeout 진단').")
    why: str = Field(description="이 문항의 중심 경험으로 고른 이유 한두 문장. 문항이 묻는 것과 어떻게 맞는지.")
    refs: list[str] = Field(description="쓸 근거의 `경로:줄`들. 입력에 나온 것을 글자 그대로 쓴다. 입력에 없는 근거는 쓰지 않는다.")
    requirement_ids: list[str] = Field(description="이 경험이 뒷받침하는 요구의 id들. 입력의 `[요구 <id>]`를 글자 그대로. 없으면 빈 목록.")


class QuestionPlanOut(BaseModel):
    question_number: int = Field(description="문항 번호. 입력의 `[문항 N]`의 N.")
    reading: str = Field(description="이 문항이 실제로 묻는 것을 한두 문장으로 해독. 소재부터 고르지 않고 질문의 의도부터 쓴다.")
    angle: str = Field(description="이 문항의 각도 한 줄. 다른 문항과 겹치지 않게.")
    experiences: list[PlannedExperienceOut] = Field(description="중심 경험 1~2개. 보통 2개를 깊게 쓰고, 문항이 하나만 요구하면 1개.")
    claim_ids: list[str] = Field(description="이 문항에서 쓸 회사 사실의 주장 id들. 입력의 `[주장 <id>]`를 글자 그대로. 쓰지 않으면 빈 목록.")
    notes: str = Field(default="", description="이 문항을 쓸 때 조심할 점이나 근거의 한계. 없으면 빈 문자열.")


class PlanOut(BaseModel):
    questions: list[QuestionPlanOut] = Field(description="문항마다 하나씩, 문항 번호 순서대로")


class SentenceOut(BaseModel):
    index: int = Field(description="검증할 글에서 이 문장의 번호. 입력의 `[번호]`를 그대로 쓴다.")
    support: Literal["뒷받침됨", "일부만", "근거 없음", "근거와 다름", "사실 주장 없음"] = Field(
        description=(
            "뒷받침됨=문장의 사실 주장(수치·역할·행동·인과·동기·시점·범위)이 모두 입력 근거에 있다, "
            "일부만=일부만 근거에 있고 나머지가 없다, 근거 없음=근거에 없다(네가 아는 사실이어도 입력 근거에 없으면 여기다), "
            "근거와 다름=근거와 반대이거나 다른 사실이다, 사실 주장 없음=다짐·의견·연결처럼 확인할 사실이 없는 문장"
        )
    )
    source: str = Field(
        default="",
        description="뒷받침됨·일부만일 때 근거가 있는 곳: 입력의 근거 위치(`경로:줄`, `사용자:소제목`) 또는 회사 사실 id(`c13`)를 글자 그대로. 아니면 빈 문자열.",
    )
    quote: str = Field(
        default="",
        description="뒷받침됨·일부만일 때 그 근거 원문에서 **글자 그대로 복사한** 구절(10자 이상). 요약·바꿔 쓰기·번역은 안 된다. 아니면 빈 문자열.",
    )
    note: str = Field(
        default="",
        description="일부만이면 근거에 없는 부분이 무엇인지, 근거 없음·근거와 다름이면 왜 그런지 한 문장. 뒷받침됨이면 빈 문자열.",
    )


class CheckOut(BaseModel):
    sentences: list[SentenceOut] = Field(description="입력의 모든 문장 번호에 대해 하나씩. 빠뜨리지 않는다.")


class LinkOut(BaseModel):
    requirement_number: int = Field(description="입력에서 이 요구의 번호(`[요구 3]`의 3)")
    claim_id: str = Field(description="입력에 나온 조사 주장의 id(`[c3]`의 c3)를 글자 그대로")
    reason: str = Field(description="이 주장이 이 요구와 어떻게 관련 있는지 한 문장. 주장에 적힌 내용만으로 쓴다.")


class LinksOut(BaseModel):
    links: list[LinkOut] = Field(description="요구와 관련 있는 조사 주장의 연결들. 관련 있는 것이 없으면 빈 목록.")



class PostingCandidateOut(BaseModel):
    url: str = Field(description="공고 원문 페이지 주소. fetch_page로 실제로 읽은 주소를 글자 그대로 쓴다.")
    company: str = Field(description="회사 이름")
    role: str = Field(description="직무 이름")
    deadline: str = Field(default="", description="페이지에 적힌 마감일. 없으면 빈 문자열(지어내지 않는다).")
    quote: str = Field(description="이 페이지가 채용공고 원문임을 보여 주는 문장 하나를 페이지에서 글자 그대로 복사한다(예: 자격요건 한 줄).")
    reason: str = Field(description="목표에 맞는 이유 한 줄")


class PostingCandidatesOut(BaseModel):
    candidates: list[PostingCandidateOut] = Field(description="찾은 공고 후보. 못 찾았으면 빈 목록.")
