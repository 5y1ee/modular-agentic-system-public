"""StrandsGuidanceProposer: 가짜 에이전트로 AWS 없이 검증한다. 피드백이 모델에 전달되고 결과가 후보로 바뀌는지만 본다."""
from types import SimpleNamespace

import pytest

pytest.importorskip("strands")

from modular_agentic.core.feedback import FeedbackEvent  # noqa: E402
from modular_agentic.engines.strands.guidance import StrandsGuidanceProposer  # noqa: E402
from modular_agentic.engines.strands.schemas import GuidanceCandidateOut, GuidanceCandidatesOut  # noqa: E402


def event(kind="comment", note="너무 길다", angle="각도1", text="본문 내용") -> FeedbackEvent:
    return FeedbackEvent(ts="t", item_id="i1", question="q", kind=kind, note=note, draft_angle=angle, draft_text=text)


def test_no_events_does_not_call_model():
    def factory(tools):
        raise AssertionError("피드백이 없으면 모델을 부르면 안 된다")

    assert StrandsGuidanceProposer(agent_factory=factory).propose([], "") == []


def test_feedback_and_current_guidance_reach_the_model_and_candidates_are_converted():
    prompts = []

    class FakeAgent:
        def __init__(self, tools):
            assert tools == []

        def __call__(self, prompt, structured_output_model=None):
            prompts.append(prompt)
            out = GuidanceCandidatesOut(
                candidates=[
                    GuidanceCandidateOut(text="  짧게 쓴다  ", scope="일반", reason="피드백 1"),
                    GuidanceCandidateOut(text="   ", scope="특정", reason="빈 후보는 버린다"),
                ]
            )
            return SimpleNamespace(structured_output=out)

    proposer = StrandsGuidanceProposer(agent_factory=FakeAgent)

    [cand] = proposer.propose([event(), event("pick", "", "각도2", "다른 본문")], "- 기존 지침")

    assert (cand.text, cand.scope, cand.reason) == ("짧게 쓴다", "일반", "피드백 1")
    prompt = prompts[0]
    assert "- 기존 지침" in prompt and "너무 길다" in prompt and "각도1" in prompt and "본문 내용" in prompt
    assert "[가장 좋다고 선택]" in prompt and "(이유 없음)" in prompt
