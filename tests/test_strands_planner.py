"""Strands Planner(소재 계획)와 plan_review. 가짜 에이전트로 AWS 없이 검증한다.

핵심: (1) 모델이 적은 요구 id·근거 위치·회사 사실 id는 입력에 있던 것만 인정한다 (2) 기본 요건은 중심 경험의 요구에서 빠진다
(3) 문항 번호를 맞추고 빠진 문항은 경고한다 (4) 같은 근거 페이지가 두 문항의 중심이면 경고한다 (5) avoid는 다른 문항의 중심 경험 이름이다
(6) 입력에는 근거·한계·기본 표시·추가 재료·회사 사실이 들어간다 (7) 대응표는 코드가 만들고 승인 전에는 쓰이지 않는다.
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("strands")

from modular_agentic.core.plan_review import (  # noqa: E402
    PlanRecord,
    approve,
    approved_plan,
    load_record,
    plan_path,
    render_plan_md,
    save_record,
    slug,
)
from modular_agentic.core.types import (  # noqa: E402
    Brief,
    Citation,
    Claim,
    Evidence,
    Item,
    Mapping,
    MappingStatus,
    Requirement,
    Verdict,
    VerdictKind,
)
from modular_agentic.engines.strands.planner import StrandsPlanner  # noqa: E402
from modular_agentic.engines.strands.schemas import PlannedExperienceOut, PlanOut, QuestionPlanOut  # noqa: E402

ITEM = Item(id="job", source="t", title="공고 제목", body="본문")
QUESTIONS = ["지원 이유와 커리어 계획 (1,000자)", "강점 역량과 노력 (1,500자)", "가치 하나를 골라 실천 경험 (1,000자)"]


def req(n, text):
    return Requirement(id=f"job-r{n}", item_id="job", text=text)


M1 = Mapping(req(1, "Java 기본"), [Evidence("a/기본.md:1", "자바를 쓴다")], MappingStatus.CONFIRMED, "기본 스킬")
M2 = Mapping(req(2, "장애 대응"), [Evidence("p/프로젝트A.md:10", "풀 고갈 원인을 추적했다")], MappingStatus.CONFIRMED, "확인")
M3 = Mapping(req(3, "AI 기능 연계"), [Evidence("p/DevCraft.md:5", "에이전트를 만들었다")], MappingStatus.PARTIAL, "일부")
M4 = Mapping(req(4, "문서화"), [], MappingStatus.TODO, "없음")
MAPPINGS = [M1, M2, M3, M4]
VERDICTS = [Verdict("job-r2", VerdictKind.APPROVED), Verdict("job-r3", VerdictKind.FLAGGED, "개발 중인 단계다")]
EXTRA = Mapping(req(9, "Talent Value 'Grit'을 실천한 경험 — 집요하게"), [Evidence("p/VOC.md:2", "원인 2건을 규명했다")], MappingStatus.CONFIRMED, "확인")
BRIEF = Brief(item_id="job", subject="회사", claims=[Claim("c1", "회사는 AI 플랫폼을 만든다", "사업", [Citation("d1", "원문 인용 문장입니다")])])


def exp(name, refs, req_ids=()):
    return PlannedExperienceOut(name=name, why="이유", refs=list(refs), requirement_ids=list(req_ids))


def qplan(n, experiences, claims=(), angle="각도"):
    return QuestionPlanOut(question_number=n, reading="해독", angle=angle, experiences=experiences, claim_ids=list(claims))


def fake_factory(out: PlanOut, prompts: list):
    class FakeAgent:
        def __init__(self, tools):
            assert tools == []  # Planner는 도구를 쓰지 않는다

        def __call__(self, prompt, structured_output_model=None):
            prompts.append(prompt)
            assert structured_output_model is PlanOut
            return SimpleNamespace(structured_output=out)

    return FakeAgent


def run(out, **kwargs):
    prompts: list[str] = []
    plan = StrandsPlanner(agent_factory=fake_factory(out, prompts)).plan(ITEM, QUESTIONS, MAPPINGS, VERDICTS, **kwargs)
    return plan, prompts[0]


def good_output():
    return PlanOut(
        questions=[
            qplan(1, [exp("회사 연결", ["p/DevCraft.md:5"], ["job-r3"])], claims=["c1"], angle="지원 이유"),
            qplan(2, [exp("프로젝트A 운영", ["p/프로젝트A.md:10"], ["job-r2"])], angle="역량"),
            qplan(3, [exp("VOC 규명", ["p/VOC.md:2"])], angle="가치"),
        ]
    )


def test_plan_keeps_valid_assignments_and_computes_avoid_from_the_other_questions():
    plan, _ = run(good_output(), brief=BRIEF, extra=[EXTRA], variant="Grit")
    assert [q.number for q in plan.questions] == [1, 2, 3] and plan.variant == "Grit" and plan.warnings == []
    assert plan.questions[0].claim_ids == ["c1"] and plan.questions[0].experiences[0].requirement_ids == ["job-r3"]
    assert plan.questions[0].avoid == ["프로젝트A 운영", "VOC 규명"]  # 다른 문항 몫은 이 문항에서 안 쓴다
    assert plan.questions[2].avoid == ["회사 연결", "프로젝트A 운영"]


def test_unknown_ids_refs_and_claims_are_dropped_and_an_experience_without_evidence_is_removed():
    out = PlanOut(
        questions=[
            qplan(1, [exp("지어낸 경험", ["없는/페이지.md:1"], ["job-r99"]), exp("실제 경험", ["p/DevCraft.md:5", "없는/것.md:2"], ["job-r3", "job-r99"])], claims=["c1", "c9"]),
            qplan(2, [exp("프로젝트A 운영", ["p/프로젝트A.md:10"], ["job-r2"])]),
            qplan(3, [exp("VOC 규명", ["p/VOC.md:2"])]),
        ]
    )
    plan, _ = run(out, brief=BRIEF, extra=[EXTRA])
    q1 = plan.questions[0]
    assert [e.name for e in q1.experiences] == ["실제 경험"] and q1.experiences[0].refs == ["p/DevCraft.md:5"] and q1.experiences[0].requirement_ids == ["job-r3"]
    assert q1.claim_ids == ["c1"]
    assert any("'지어낸 경험'" in w and "중심 경험에서 뺐다" in w for w in plan.warnings)
    assert any("회사 사실 id" in w for w in plan.warnings)


def test_claim_ids_are_read_leniently_and_dropped_ids_are_shown_in_the_warning():
    out = good_output()
    out.questions[0].claim_ids = ["[주장 c1]", "C1", "c77", "회사 소개"]
    plan, _ = run(out, brief=BRIEF, extra=[EXTRA])
    assert plan.questions[0].claim_ids == ["c1"]  # 표기가 달라도 같은 id는 하나로
    assert any("['c77', '회사 소개']" in w for w in plan.warnings)  # 무엇을 뺐는지 보인다


def test_basic_requirements_and_todo_requirements_are_removed_from_experience_requirement_ids():
    out = good_output()
    out.questions[1].experiences[0].requirement_ids = ["job-r1", "job-r2", "job-r4"]  # r1은 기본, r4는 근거 없음
    plan, _ = run(out, brief=BRIEF, extra=[EXTRA], basic_requirement_ids=["job-r1"])
    assert plan.questions[1].experiences[0].requirement_ids == ["job-r2"] and plan.basic_requirement_ids == ["job-r1"]
    assert any("기본 요건이거나 근거 없는 요구" in w for w in plan.warnings)


def test_missing_question_and_unordered_numbers_are_handled():
    out = PlanOut(questions=[qplan(3, [exp("VOC 규명", ["p/VOC.md:2"])]), qplan(1, [exp("회사 연결", ["p/DevCraft.md:5"])])])
    plan, _ = run(out, extra=[EXTRA])
    assert [q.number for q in plan.questions] == [1, 2, 3]
    assert plan.questions[1].experiences == [] and any("문항 2의 배정을 모델이 내지 않았다" in w for w in plan.warnings)
    assert [e.name for e in plan.questions[2].experiences] == ["VOC 규명"]


def test_same_evidence_page_in_two_questions_is_warned():
    out = good_output()
    out.questions[2].experiences[0].refs = ["p/프로젝트A.md:10"]  # 2번 문항과 같은 페이지
    plan, _ = run(out, brief=BRIEF, extra=[EXTRA])
    assert any("문항 [2, 3]" in w and "p/프로젝트A.md" in w for w in plan.warnings)


def test_prompt_carries_requirements_limits_basic_marks_extra_material_claims_and_user_note():
    _, prompt = run(good_output(), brief=BRIEF, extra=[EXTRA], basic_requirement_ids=["job-r1"], notes="2번은 운영 중심으로", variant="Grit")
    assert "[문항 1] 지원 이유와 커리어 계획" in prompt and "[문항 3] 가치 하나를 골라" in prompt
    assert "[요구 job-r1] (#1) Java 기본 — 확인 — 기본(어필 안 함)" in prompt
    assert "한계(검증이 지적함. 이 범위를 넘겨 쓰지 않는다): 개발 중인 단계다" in prompt and "[p/프로젝트A.md:10] 풀 고갈 원인을" in prompt
    assert "[추가 재료 job-r9]" in prompt and "[주장 c1] (사업) 회사는 AI 플랫폼을 만든다" in prompt
    assert "선택 가치(가치를 고르는 문항에 쓴다): Grit" in prompt and "사용자 지시: 2번은 운영 중심으로" in prompt


def test_prompt_shows_every_extra_evidence_but_caps_ordinary_requirement_evidence():
    many = lambda mapping_id: Mapping(req(mapping_id, "많은 근거"), [Evidence(f"p/{mapping_id}.md:{i}", f"발췌{i}") for i in range(1, 7)], MappingStatus.CONFIRMED, "확인")
    prompts: list[str] = []
    StrandsPlanner(agent_factory=fake_factory(good_output(), prompts)).plan(ITEM, QUESTIONS, [many(7)], [], extra=[many(8)])
    prompt = prompts[0]
    assert "[p/8.md:6]" in prompt  # 사용자가 지정한 추가 재료는 6개 전부 보인다(조용히 잘리면 지정한 소재가 배정에서 사라진다)
    assert "[p/7.md:4]" in prompt and "[p/7.md:5]" not in prompt  # 일반 요구의 근거는 앞 4개까지

# ── plan_review ──


def test_record_roundtrip_approval_and_only_approved_plans_are_used(tmp_path):
    plan, _ = run(good_output(), brief=BRIEF, extra=[EXTRA], variant="Work With Grit")
    path = plan_path(tmp_path, "job", "Work With Grit")
    assert path.name == "job__Work-With-Grit.json" and slug("Make Bold Moves") == "Make-Bold-Moves"

    assert approved_plan(path)[0] is None and "없다" in approved_plan(path)[1]
    save_record(path, PlanRecord(plan=plan))
    assert approved_plan(path)[0] is None and "승인되지 않아" in approved_plan(path)[1]
    save_record(path, approve(load_record(path)))
    approved, why = approved_plan(path)
    assert approved == plan and "승인된" in why


def test_rendered_table_is_made_by_code_from_the_plan():
    plan, _ = run(good_output(), brief=BRIEF, extra=[EXTRA], basic_requirement_ids=["job-r1"], variant="Grit")
    md = render_plan_md(PlanRecord(plan=plan), MAPPINGS, VERDICTS, BRIEF)

    assert "# 소재 계획: Grit — job" in md and "**미승인**" in md
    assert "| 1 | Java 기본 | 확인 | `a/기본.md:1` | 기본(어필 안 함) |" in md
    assert "| 2 | 장애 대응 | 확인 | `p/프로젝트A.md:10` | 문항 2 |" in md
    assert "| 3 | AI 기능 연계 | 부분확인 ⚑ | `p/DevCraft.md:5` | 문항 1 |" in md
    assert "| 4 | 문서화 | TODO | 없음 | 쓰지 않음 |" in md
    assert "**[c1]** 회사는 AI 플랫폼을 만든다" in md and "이 문항에서 쓰지 않을 소재(다른 문항 몫)**: 프로젝트A 운영, VOC 규명" in md
    assert "승인됨" in render_plan_md(approve(PlanRecord(plan=plan)), MAPPINGS, VERDICTS, BRIEF)


def test_planner_input_keeps_the_whole_excerpt_and_says_when_it_hides_evidence():
    """소재 계획이 근거를 200자로 자르던 것(작성은 400자까지 본다)과 요구당 상한을 표시 없이 자르던 것을 막는다."""
    from modular_agentic.engines.strands.planner import EVIDENCE_PER_REQUIREMENT

    long_excerpt = "앞부분 " + "가" * 300 + " 끝부분"
    many = Mapping(req(7, "많은 근거"), [Evidence(f"p/{i}.md:1", long_excerpt if i == 1 else f"발췌{i}") for i in range(1, EVIDENCE_PER_REQUIREMENT + 3)], MappingStatus.CONFIRMED, "확인")
    prompts: list[str] = []
    StrandsPlanner(agent_factory=fake_factory(good_output(), prompts)).plan(ITEM, QUESTIONS, [many], [])
    assert "끝부분" in prompts[0]  # 300자가 넘는 발췌도 끝까지 보인다
    assert f"(근거 {EVIDENCE_PER_REQUIREMENT + 2}개 중 {EVIDENCE_PER_REQUIREMENT}개만 보임)" in prompts[0]


def test_plan_table_cuts_long_requirement_text_at_a_word_boundary():
    from modular_agentic.core.plan_review import REQUIREMENT_TABLE_CHARS

    long_text = "AutoEver Code(사내 AI 코딩 플랫폼) 백엔드(Spring Boot/Java) 기능 개발·유지보수와 AI 기능 연계 및 운영 지원"
    assert len(long_text) > REQUIREMENT_TABLE_CHARS
    plan, _ = run(good_output(), brief=BRIEF, extra=[EXTRA])
    md = render_plan_md(PlanRecord(plan=plan), [Mapping(req(1, long_text), [Evidence("a.md:1", "근거")], MappingStatus.CONFIRMED, "확인")], [], BRIEF)
    row = next(line for line in md.splitlines() if line.startswith("| 1 |"))
    cell = row.split("|")[2].strip()
    assert cell.endswith(" …") and len(cell) <= REQUIREMENT_TABLE_CHARS + 2
    kept = cell[: -len(" …")]
    assert long_text.startswith(kept) and long_text[len(kept)] == " "  # 단어 중간(예: "Spring Boo")이 아니라 공백에서 끊는다


def test_planner_input_shows_company_context_under_the_requirement_as_reference_only():
    from modular_agentic.core.types import ContextNote

    prompts: list[str] = []
    contexts = {MAPPINGS[1].requirement.id: [ContextNote("c1", "회사가 AI 플랫폼을 만든다는 점과 이어진다")]}
    StrandsPlanner(agent_factory=fake_factory(good_output(), prompts)).plan(ITEM, QUESTIONS, MAPPINGS, VERDICTS, brief=BRIEF, contexts=contexts)
    prompt = prompts[0]
    assert "회사 맥락(참고. 내 근거가 아니다): [c1] 회사가 AI 플랫폼을 만든다는 점과 이어진다" in prompt
    assert prompt.count("회사 맥락(참고") == 1  # 맥락이 붙은 요구에만 나온다
    prompts.clear()
    StrandsPlanner(agent_factory=fake_factory(good_output(), prompts)).plan(ITEM, QUESTIONS, MAPPINGS, VERDICTS, brief=BRIEF)  # 맥락이 없어도 그대로 돈다
    assert "회사 맥락(참고" not in prompts[0]
