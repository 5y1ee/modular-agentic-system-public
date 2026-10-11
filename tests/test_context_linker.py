"""회사 맥락 연결(engines/strands/context_linker.py). 모델이 낸 연결 중 입력에 없는 번호·id는 코드가 버린다. 가짜 모델로 검증한다."""
from types import SimpleNamespace

from modular_agentic.core.types import Brief, Citation, Claim, Requirement
from modular_agentic.engines.strands.context_linker import StrandsContextLinker
from modular_agentic.engines.strands.schemas import LinkOut, LinksOut

REQS = [Requirement("job-r1", "job", "Java/Spring 백엔드", "필수"), Requirement("job-r2", "job", "AI 기능 연계(코드 생성·리뷰)")]
BRIEF = Brief("job", "회사", claims=[Claim("c3", "H Chat은 코드 작성·리뷰에도 쓰인다.", "제품", [Citation("d", "인용 구절입니다 충분히")]), Claim("c9", "SDV 전환을 추진한다.", "", [Citation("d", "또 다른 인용 구절입니다")])])


def fake_factory(out: LinksOut, prompts: list[str]):
    class FakeAgent:
        def __init__(self, tools):
            assert tools == []  # 도구를 쓰지 않는다

        def __call__(self, prompt, structured_output_model=None):
            prompts.append(prompt)
            return SimpleNamespace(structured_output=out)

    return FakeAgent


def test_input_numbers_requirements_and_lists_approved_claims():
    prompts: list[str] = []
    StrandsContextLinker(agent_factory=fake_factory(LinksOut(links=[]), prompts)).link(REQS, BRIEF)
    assert "[요구 1] (필수) Java/Spring 백엔드" in prompts[0] and "[요구 2] AI 기능 연계" in prompts[0]
    assert "[c3] (제품) H Chat은" in prompts[0] and "[c9] SDV 전환을 추진한다." in prompts[0]


def test_keeps_valid_links_and_drops_unknown_numbers_unknown_ids_blank_reasons_and_duplicates_with_warnings():
    out = LinksOut(links=[
        LinkOut(requirement_number=2, claim_id="c3", reason=" 코드 리뷰에 쓰이는 AI 도구다 "),
        LinkOut(requirement_number=2, claim_id="c3", reason="같은 연결을 또"),  # 중복
        LinkOut(requirement_number=2, claim_id="c9", reason="방향이 같다"),
        LinkOut(requirement_number=7, claim_id="c3", reason="없는 요구 번호"),
        LinkOut(requirement_number=1, claim_id="c99", reason="없는 주장 id"),
        LinkOut(requirement_number=1, claim_id="c9", reason="  "),  # 이유 없음
    ])
    context, warnings = StrandsContextLinker(agent_factory=fake_factory(out, [])).link(REQS, BRIEF)
    assert list(context) == ["job-r2"]  # 연결이 없는 요구(job-r1)는 키가 없다: 정상
    assert [(n.claim_id, n.reason) for n in context["job-r2"]] == [("c3", "코드 리뷰에 쓰이는 AI 도구다"), ("c9", "방향이 같다")]
    assert any("요구 번호로 낸 연결 1개" in w for w in warnings) and any("주장 id로 낸 연결 1개" in w for w in warnings) and any("이유가 비어 있는 연결 1개" in w for w in warnings)


def test_no_model_call_without_requirements_or_claims():
    calls: list[str] = []
    linker = StrandsContextLinker(agent_factory=fake_factory(LinksOut(links=[]), calls))
    assert linker.link([], BRIEF) == ({}, []) and linker.link(REQS, Brief("job", "회사")) == ({}, []) and calls == []


def test_prompt_says_not_to_pass_off_another_subjects_facts_as_the_requirements_subject():
    from modular_agentic.engines.strands.context_linker import SYSTEM_PROMPT

    assert "다른 대상" in SYSTEM_PROMPT and "관련 사례" in SYSTEM_PROMPT and "억지로 연결하지 않는다" in SYSTEM_PROMPT
