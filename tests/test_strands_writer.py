"""Strands Writer 테스트. 가짜 에이전트로 AWS 없이 검증한다.

핵심: (1) 플래그된 매핑도 발췌와 함께 재료로 주되 검증 이유를 "한계"로 붙인다 (2) TODO는 코드가 목록으로 만든다
(3) 모델이 적은 요구 id·근거 ref는 입력에 있던 것만 인정한다 (4) 분량 초과와 한계가 있는 요구의 사용을 경고한다
(5) focus와 previous가 입력에 반영된다 (6) 쓸 수 있는 근거가 없으면 모델을 부르지 않는다.
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("strands")  # strands extra가 설치된 환경에서만 돈다

from modular_agentic.core.types import (  # noqa: E402
    Draft,
    Evidence,
    Item,
    Mapping,
    MappingStatus,
    Requirement,
    Verdict,
    VerdictKind,
)
from modular_agentic.engines.strands.schemas import DraftOut, DraftsOut  # noqa: E402
from modular_agentic.engines.strands.writer import StrandsWriter, build_system_prompt, char_limit, min_chars  # noqa: E402

ITEM = Item(id="i1", source="t", title="공고 제목", body="본문")
QUESTION = "직무 관심 계기·노력·강점 (800자)"


def req(n: int, text: str) -> Requirement:
    return Requirement(id=f"i1-r{n}", item_id="i1", text=text)


M_OK = Mapping(req(1, "에이전트 개발"), [Evidence("a.md:1", "FINT에서 에이전트를 만듦")], MappingStatus.CONFIRMED, "충분")
M_PARTIAL = Mapping(req(2, "LLM 서비스"), [Evidence("b.md:2", "연동만 함")], MappingStatus.PARTIAL, "연동 수준")
M_FLAGGED = Mapping(req(3, "프레임워크"), [Evidence("c.md:3", "LangGraph 사용")], MappingStatus.CONFIRMED, "확인")
M_TODO = Mapping(req(4, "자격증"), [], MappingStatus.TODO, "없음")
ALL = [M_OK, M_PARTIAL, M_FLAGGED, M_TODO]
VERDICTS = [
    Verdict("i1-r1", VerdictKind.APPROVED),
    Verdict("i1-r2", VerdictKind.APPROVED),
    Verdict("i1-r3", VerdictKind.FLAGGED, "연동만 했다"),
    Verdict("i1-r4", VerdictKind.APPROVED),
]


def fake_factory(outputs: list[DraftOut], prompts: list[str]):
    class FakeAgent:
        def __init__(self, tools):
            assert tools == []  # Writer는 도구를 쓰지 않는다

        def __call__(self, prompt, structured_output_model=None):
            prompts.append(prompt)
            return SimpleNamespace(structured_output=DraftsOut(drafts=outputs))

    return FakeAgent


def draft_out(text="[헤드라인]\n\n본문", ids=("i1-r1",), refs=("a.md:1",), angle="각도") -> DraftOut:
    return DraftOut(angle=angle, text=text, covered_requirement_ids=list(ids), used_refs=list(refs), rationale="이유 r1")


def test_input_gives_flagged_mapping_as_material_with_limitation_and_separates_todo():
    prompts: list[str] = []
    writer = StrandsWriter(agent_factory=fake_factory([draft_out()], prompts))

    [draft] = writer.write(ITEM, QUESTION, ALL, VERDICTS)

    prompt = prompts[0]
    assert "[요구 i1-r1]" in prompt and "[a.md:1]" in prompt and "[요구 i1-r2]" in prompt
    assert "[요구 i1-r3]" in prompt and "[c.md:3] LangGraph 사용" in prompt  # 플래그된 매핑도 발췌와 함께 재료다
    assert "한계(검증이 지적함. 이 범위를 넘겨 쓰지 않는다): 연동만 했다" in prompt  # 검증 이유가 한계로 붙는다
    assert prompt.count("한계(검증이 지적함") == 1  # 승인된 요구에는 한계가 붙지 않는다
    assert "근거가 없는 요구" in prompt and "자격증" in prompt
    assert draft.todos == ["자격증"]  # TODO 목록은 코드가 만든다
    assert draft.sections == {QUESTION: "[헤드라인]\n\n본문"}
    assert draft.covered_requirement_ids == ["i1-r1"] and draft.used_refs == ["a.md:1"]


def test_unknown_ids_and_refs_from_model_are_dropped_with_warning():
    out = draft_out(ids=("i1-r1", "지어낸-id"), refs=("a.md:1", "지어낸.md:9"))
    writer = StrandsWriter(agent_factory=fake_factory([out], []))

    [draft] = writer.write(ITEM, QUESTION, ALL, VERDICTS)

    assert draft.covered_requirement_ids == ["i1-r1"] and draft.used_refs == ["a.md:1"]
    assert sum("입력에 없는" in w for w in draft.warnings) == 2


def test_warns_when_limited_requirement_used_or_text_too_long():
    out = draft_out(text="가" * 801, ids=("i1-r1", "i1-r3"), refs=("a.md:1", "c.md:3"))
    writer = StrandsWriter(agent_factory=fake_factory([out], []))

    [draft] = writer.write(ITEM, QUESTION, ALL, VERDICTS)

    assert any("한계를 지적한 요구 ['i1-r3']" in w for w in draft.warnings)  # 사람이 한계를 지켰는지 보게 한다
    assert any("801자" in w and "800자" in w for w in draft.warnings)
    assert draft.covered_requirement_ids == ["i1-r1", "i1-r3"]  # 플래그된 요구도 이제 다룰 수 있다
    assert draft.used_refs == ["a.md:1", "c.md:3"]


def test_input_states_the_85_to_100_percent_length_target():
    prompts: list[str] = []
    StrandsWriter(agent_factory=fake_factory([draft_out()], prompts)).write(ITEM, QUESTION, ALL, VERDICTS)  # 800자 문항
    limit = char_limit(QUESTION)
    assert f"분량 목표: {min_chars(limit)}~{limit}자" in prompts[0]
    prompts.clear()
    StrandsWriter(agent_factory=fake_factory([draft_out()], prompts)).write(ITEM, "지원동기", ALL, VERDICTS)  # 제한이 없으면 목표도 없다
    assert "분량 목표" not in prompts[0]


def test_warns_when_text_is_under_85_percent_of_the_limit_but_not_at_the_edge():
    def warnings_for(length: int) -> list[str]:
        [draft] = StrandsWriter(agent_factory=fake_factory([draft_out(text="가" * length)], [])).write(ITEM, QUESTION, ALL, VERDICTS)
        return [w for w in draft.warnings if "못 미친다" in w]

    limit = char_limit(QUESTION)
    lowest_ok = min_chars(limit)  # 경고 없이 통과하는 가장 짧은 글자 수
    assert warnings_for(lowest_ok - 1)  # 한 글자 모자라면 경고가 뜬다
    assert not warnings_for(lowest_ok) and not warnings_for(limit)
    half = lowest_ok // 2
    [draft] = StrandsWriter(agent_factory=fake_factory([draft_out(text="가" * half + "\n\n" + "가" * (lowest_ok - half))], [])).write(ITEM, QUESTION, ALL, VERDICTS)
    assert not any("못 미친다" in w for w in draft.warnings)  # 줄바꿈은 글자 수에서 뺀다(읽는 용 문서의 글자 수와 같다)


def test_focus_limits_materials_and_previous_drafts_are_shown():
    prompts: list[str] = []
    writer = StrandsWriter(agent_factory=fake_factory([draft_out()], prompts))
    earlier = Draft(item_id="i1", sections={QUESTION: "이전 본문"}, angle="FINT 중심")

    writer.write(ITEM, QUESTION, ALL, VERDICTS, n=1, focus=["i1-r2"], previous=[earlier])

    prompt = prompts[0]
    assert "[요구 i1-r2]" in prompt and "[요구 i1-r1]" not in prompt  # 체크한 요구만 재료
    assert "근거가 없는 요구" not in prompt  # TODO도 focus 밖이면 안 나온다
    assert "각도: FINT 중심" in prompt and "이전 본문" in prompt


def test_returns_at_most_n_drafts():
    outs = [draft_out(angle=f"각도{i}") for i in range(5)]
    writer = StrandsWriter(agent_factory=fake_factory(outs, []))

    assert [d.angle for d in writer.write(ITEM, QUESTION, ALL, VERDICTS, n=3)] == ["각도0", "각도1", "각도2"]


def test_no_usable_evidence_does_not_call_the_model():
    def factory(tools):
        raise AssertionError("쓸 수 있는 근거가 없으면 모델을 부르면 안 된다")

    [draft] = StrandsWriter(agent_factory=factory).write(ITEM, QUESTION, [M_TODO], VERDICTS)

    assert draft.sections == {QUESTION: ""} and draft.todos == ["자격증"] and draft.warnings


def test_char_limit_and_guidance_in_prompt():
    assert char_limit("직무 관심 계기·노력·강점 (800자)") == 800
    assert char_limit("지원동기") is None
    assert "작성 지침" not in build_system_prompt("")
    assert "결론 먼저" in build_system_prompt("  결론 먼저 쓴다  ")


# ── 조사 보고서(Brief) 연결 ──

from modular_agentic.core.types import Brief, Citation, Claim  # noqa: E402

BRIEF = Brief(
    item_id="i1",
    subject="지원처 3.",
    claims=[Claim("c1", "금융 IT 서비스를 제공한다", "사업", [Citation("d1", "지원처 3.는 금융 IT 서비스를 제공한다.")])],
)


def test_brief_claims_are_given_as_company_facts_and_used_claims_are_recorded():
    prompts: list[str] = []
    out = draft_out().model_copy(update={"used_claim_ids": ["c1", "c9"]})  # c9는 입력에 없는 id
    writer = StrandsWriter(agent_factory=fake_factory([out], prompts))

    [draft] = writer.write(ITEM, QUESTION, ALL, VERDICTS, brief=BRIEF)

    assert "회사 사실(지원처 3.)" in prompts[0] and "[주장 c1] (사업) 금융 IT 서비스를 제공한다" in prompts[0]
    assert '출처 발췌: "지원처 3.는 금융 IT 서비스를 제공한다."' in prompts[0]
    assert draft.used_claims == ["c1"]
    assert any("입력에 없는 주장 id" in w for w in draft.warnings)


def test_empty_brief_tells_the_model_not_to_use_company_facts_and_warns():
    prompts: list[str] = []
    writer = StrandsWriter(agent_factory=fake_factory([draft_out()], prompts))

    [draft] = writer.write(ITEM, QUESTION, ALL, VERDICTS, brief=Brief(item_id="i1", subject="지원처 3."))

    assert "회사 사실(지원처 3.): 없음" in prompts[0]
    assert any("쓸 수 있는 주장이 없어" in w for w in draft.warnings) and draft.used_claims == []


def test_without_a_brief_nothing_about_company_facts_is_added():
    prompts: list[str] = []
    writer = StrandsWriter(agent_factory=fake_factory([draft_out()], prompts))

    [draft] = writer.write(ITEM, QUESTION, ALL, VERDICTS)

    assert "회사 사실" not in prompts[0] and draft.used_claims == []
    assert not any("주장" in w for w in draft.warnings)


# ── 소재 계획(plan) 모드 ──

from modular_agentic.core.types import PlannedExperience, QuestionPlan  # noqa: E402


def test_char_limit_reads_numbers_with_thousands_separators():
    assert char_limit("... 작성해 주세요. (1,000자)") == 1000
    assert char_limit("... 구체적으로 작성해주세요. (1,500자)") == 1500
    assert char_limit("직무 관심 계기·노력·강점 (800자)") == 800
    assert char_limit("분량 제한 없음") is None


EXTRA_MAPPING = Mapping(req(9, "가치 근거"), [Evidence("p/회사B.md:18", "APK를 디컴파일해 약 2만 개 레벨 데이터를 추출했다")], MappingStatus.CONFIRMED, "")
PLAN_Q = QuestionPlan(
    number=3,
    question="Talent Value 중 하나를 선택해 실천 경험을 작성해 주세요. (1,000자)",
    reading="가치 하나를 실천한 경험을 묻는다",
    angle="기존 방식의 한계를 인식하고 다른 방법으로 도전",
    experiences=[
        PlannedExperience(name="회사B APK 레벨 추출", why="가치 정의에 맞는다", refs=["p/회사B.md:18", "없는/근거.md:1"], requirement_ids=["i1-r3"]),
        PlannedExperience(name="근거를 못 찾는 경험", why="x", refs=["없는/것.md:2"], requirement_ids=[]),
    ],
    claim_ids=["c1"],
    avoid=["프로젝트A 운영", "FINT"],
    notes="발췌 밖의 세부는 쓰지 않는다",
)
BRIEF2 = Brief(
    item_id="i1",
    subject="지원처 3.",
    claims=[
        Claim("c1", "Make Bold Moves는 과감한 도전이다", "가치", [Citation("d1", "원문 인용 문장입니다 열 글자 넘게")]),
        Claim("c2", "회사는 금융 IT를 한다", "사업", [Citation("d1", "또 다른 원문 인용 문장입니다")]),
    ],
)


def test_plan_mode_writes_only_the_assigned_experience_with_its_instructions_and_assigned_claims():
    prompts: list[str] = []
    out = draft_out(ids=("exp3-1", "exp3-9"), refs=("p/회사B.md:18", "p/프로젝트A.md:10")).model_copy(update={"used_claim_ids": ["c1", "c2"]})
    writer = StrandsWriter(agent_factory=fake_factory([out], prompts))

    [draft] = writer.write(ITEM, PLAN_Q.question, ALL, VERDICTS, brief=BRIEF2, plan=PLAN_Q, extra=[EXTRA_MAPPING])

    prompt = prompts[0]
    assert "소재 배정(사용자가 승인했다" in prompt and "문항이 묻는 것: 가치 하나를 실천한 경험을 묻는다" in prompt
    assert "이 문항에서 쓰지 않을 소재(다른 문항에서 쓴다): 프로젝트A 운영, FINT" in prompt and "조심할 점: 발췌 밖의 세부는 쓰지 않는다" in prompt
    assert "[중심 경험 exp3-1] 회사B APK 레벨 추출" in prompt and "[p/회사B.md:18] APK를 디컴파일해 약 2만 개" in prompt
    assert "근거를 못 찾는 경험" not in prompt and "[요구 i1-r1]" not in prompt  # 근거 없는 경험과 일반 요구 목록은 들어가지 않는다
    assert "[주장 c1]" in prompt and "[주장 c2]" not in prompt  # 배정된 회사 사실만
    assert draft.used_refs == ["p/회사B.md:18"] and draft.used_claims == ["c1"]  # 입력에 없던 ref·주장 id는 걸러진다
    assert draft.covered_requirement_ids == ["i1-r3"]  # 합성 id가 아니라 실제 요구 id로 돌려준다
    assert not any("넘는다" in w for w in draft.warnings)  # 분량 초과 경고만 본다(짧은 가짜 본문은 미달 경고가 정상이다)


def test_plan_mode_without_assigned_claims_hides_company_facts_and_does_not_warn_about_an_empty_brief():
    prompts: list[str] = []
    plan = QuestionPlan(number=2, question="강점 역량 (1,500자)", reading="r", angle="a", experiences=[PlannedExperience("회사B", "w", ["p/회사B.md:18"], [])], claim_ids=[])
    writer = StrandsWriter(agent_factory=fake_factory([draft_out(ids=(), refs=("p/회사B.md:18",))], prompts))

    [draft] = writer.write(ITEM, plan.question, ALL, VERDICTS, brief=BRIEF2, plan=plan, extra=[EXTRA_MAPPING])

    assert "회사 사실" not in prompts[0].split("소재 배정")[1].split("이 문항의 중심 경험")[0] or "회사 사실이 배정되지 않았다" in prompts[0]
    assert "[주장 c1]" not in prompts[0] and "회사에 대한 서술을 하지 않는다" in prompts[0]
    assert not any("조사 보고서" in w for w in draft.warnings)


def test_plan_mode_with_no_findable_evidence_does_not_call_the_model():
    plan = QuestionPlan(number=1, question="q (1,000자)", reading="", angle="", experiences=[PlannedExperience("x", "w", ["없는/것.md:1"], [])])

    class Boom:
        def __init__(self, tools):
            raise AssertionError("모델을 부르면 안 된다")

    [draft] = StrandsWriter(agent_factory=Boom).write(ITEM, plan.question, ALL, VERDICTS, plan=plan)
    assert draft.sections[plan.question] == "" and "배정된 중심 경험의 근거를 찾지 못함" in draft.warnings[0]


def test_plan_experience_inherits_the_limitation_of_the_requirements_it_supports():
    prompts: list[str] = []
    plan = QuestionPlan(number=1, question="q (1,000자)", reading="", angle="", experiences=[PlannedExperience("프레임워크 경험", "w", ["c.md:3"], ["i1-r3"])])
    writer = StrandsWriter(agent_factory=fake_factory([draft_out(ids=("exp1-1",), refs=("c.md:3",))], prompts))
    [draft] = writer.write(ITEM, plan.question, ALL, VERDICTS, plan=plan)  # VERDICTS가 i1-r3를 "연동만 했다"로 플래그했다
    assert "한계(검증이 지적함. 이 범위를 넘겨 쓰지 않는다): 연동만 했다" in prompts[0]
    assert any("한계" in w for w in draft.warnings)


def test_system_prompt_forbids_invented_inner_states_product_mixing_and_mixed_register():
    from modular_agentic.engines.strands.writer import SYSTEM_PROMPT

    assert "체감했다" in SYSTEM_PROMPT and "지어낸 생각의 인용" in SYSTEM_PROMPT  # 근거 없는 내면
    assert "다른 제품의 기능을 섞지 않고" in SYSTEM_PROMPT  # 회사 속성 혼입
    assert "~습니다/합니다" in SYSTEM_PROMPT  # 문체 통일


def test_one_off_guidance_is_appended_to_the_system_prompt():
    prompt = build_system_prompt("Tips는 연구 과제의 이름이다.")
    assert prompt.endswith("Tips는 연구 과제의 이름이다.") and "작성 지침" in prompt


def test_min_chars_rounds_up_to_whole_characters():
    assert min_chars(800) == 680 and min_chars(1000) == 850 and min_chars(1500) == 1275
    assert min_chars(750) == 638  # 750자의 85%는 637.5라서 637자는 미달이다
