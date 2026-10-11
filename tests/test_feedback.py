"""core/feedback: 피드백 세션과 저장소. 모델 없이 가짜 Writer·가짜 Proposer로 검증한다.

핵심: (1) 승인한 지침만 Writer에 전달된다 (2) 일반 지침은 파일에 저장되고 특정 지침은 세션에서만 쓴다
(3) 같은 피드백으로 후보를 두 번 뽑지 않는다 (4) 더 뽑기는 이전 초안을, 요구 선택은 focus를 Writer에 넘긴다
(5) 피드백 기록이 파일에 남는다 (6) 잘못된 번호는 오류.
"""
from pathlib import Path

import pytest

from modular_agentic.core.feedback import (
    FeedbackSession,
    FeedbackStore,
    GuidanceCandidate,
)
from modular_agentic.core.types import Draft, Evidence, Item, Mapping, MappingStatus, Requirement

ITEM = Item(id="i1", source="t", title="공고", body="본문")
QUESTION = "직무 관심 계기·노력·강점 (800자)"
MAPPINGS = [
    Mapping(Requirement(id="i1-r1", item_id="i1", text="요구1"), [Evidence("a.md:1", "x")], MappingStatus.CONFIRMED),
    Mapping(Requirement(id="i1-r2", item_id="i1", text="요구2"), [Evidence("b.md:1", "y")], MappingStatus.CONFIRMED),
]


class FakeWriter:
    def __init__(self, guidance: str, log: list[dict]):
        self.guidance = guidance
        self.log = log

    def write(self, item, question, mappings, verdicts, *, n=1, focus=None, previous=()):
        self.log.append({"guidance": self.guidance, "n": n, "focus": focus, "previous": [d.angle for d in previous]})
        start = len(previous) + 1
        return [Draft(item_id=item.id, sections={question: f"본문{start + i}"}, angle=f"각도{start + i}") for i in range(n)]


class FakeProposer:
    def __init__(self, candidates):
        self.candidates = candidates
        self.seen: list[list] = []

    def propose(self, events, current_guidance):
        self.seen.append([(e.kind, e.note, e.draft_angle) for e in events])
        return list(self.candidates)


def make_session(tmp_path: Path, candidates=(), log=None, stamp="2026-10-04T10:00:00"):
    log = log if log is not None else []
    proposer = FakeProposer(candidates)
    session = FeedbackSession(
        item=ITEM,
        question=QUESTION,
        mappings=MAPPINGS,
        verdicts=[],
        writer_factory=lambda guidance: FakeWriter(guidance, log),
        proposer=proposer,
        store=FeedbackStore(tmp_path / "events.jsonl", tmp_path / "guidance.md"),
        clock=lambda: stamp,
    )
    return session, proposer, log


def test_generate_more_and_focus_pass_arguments_to_writer(tmp_path):
    session, _, log = make_session(tmp_path)

    session.generate()
    session.more()
    session.generate(focus=session.requirement_ids([2]))

    # 처음 3개(각도1~3), 더 뽑기는 이어서(각도4~6), 요구 선택은 이전 초안을 넘기지 않아 다시 1부터(가짜 Writer의 번호 규칙)
    assert [d.angle for d in session.drafts] == ["각도1", "각도2", "각도3", "각도4", "각도5", "각도6", "각도1", "각도2", "각도3"]
    assert log[0]["previous"] == [] and log[0]["n"] == 3
    assert log[1]["previous"] == ["각도1", "각도2", "각도3"]  # 더 뽑기는 이미 낸 초안을 넘긴다
    assert log[2]["focus"] == ["i1-r2"] and log[2]["previous"] == []  # 요구 선택은 focus를 넘긴다


def test_pick_and_comment_are_recorded_to_file_with_draft_content(tmp_path):
    session, _, _ = make_session(tmp_path)
    session.generate()

    session.pick(2, "결론이 먼저라서")
    session.comment(1, "너무 길다")

    events = session.store.events()
    assert [(e.kind, e.note, e.draft_angle, e.draft_text) for e in events] == [
        ("pick", "결론이 먼저라서", "각도2", "본문2"),
        ("comment", "너무 길다", "각도1", "본문1"),
    ]
    assert events[0].ts == "2026-10-04T10:00:00" and events[0].item_id == "i1" and events[0].question == QUESTION


def test_guidance_is_applied_only_after_approval_and_scope_decides_where_it_lives(tmp_path):
    cands = [
        GuidanceCandidate("결론을 먼저 쓴다", "일반", "초안 2 선택 이유"),
        GuidanceCandidate("공모전 경험을 강조한다", "특정", "피드백 1"),
    ]
    session, _, log = make_session(tmp_path, cands)
    session.generate()
    session.pick(1, "이유")
    assert session.propose_guidance() == cands
    session.generate()
    assert log[1]["guidance"] == ""  # 승인 전에는 프롬프트에 안 들어간다

    session.approve([1, 2])
    session.generate()

    assert "결론을 먼저 쓴다" in log[2]["guidance"] and "공모전 경험을 강조한다" in log[2]["guidance"]
    assert "이번 공고·문항에만 적용" in log[2]["guidance"]
    assert session.store.guidance() == "- 결론을 먼저 쓴다"  # 일반만 파일에 저장된다
    assert "공모전" not in session.store.guidance()
    # 새 세션은 저장된 일반 지침만 이어받는다
    session2, _, log2 = make_session(tmp_path, log=[])
    session2.generate()
    assert session2.active_guidance == "- 결론을 먼저 쓴다"


def test_unapproved_candidates_are_not_applied_and_approval_is_recorded(tmp_path):
    cands = [GuidanceCandidate("A", "일반", "r"), GuidanceCandidate("B", "일반", "r")]
    session, _, _ = make_session(tmp_path, cands)
    session.generate()
    session.comment(1, "피드백")
    session.propose_guidance()

    session.approve([2])

    assert session.store.guidance() == "- B"  # A는 승인하지 않았으므로 없다
    assert [(e.kind, e.note, e.scope) for e in session.store.events() if e.kind == "guidance"] == [("guidance", "B", "일반")]


def test_propose_uses_only_new_feedback_and_skips_model_when_none(tmp_path):
    session, proposer, _ = make_session(tmp_path, [GuidanceCandidate("A", "일반", "r")])
    session.generate()

    assert session.propose_guidance() == [] and proposer.seen == []  # 피드백이 없으면 모델을 안 부른다
    session.comment(1, "첫 피드백")
    session.propose_guidance()
    session.comment(2, "둘째 피드백")
    session.propose_guidance()
    assert session.propose_guidance() == [] and len(proposer.seen) == 2  # 이미 뽑은 피드백으로 다시 뽑지 않는다

    assert proposer.seen[0] == [("comment", "첫 피드백", "각도1")]
    assert proposer.seen[1] == [("comment", "둘째 피드백", "각도2")]


def test_invalid_numbers_raise(tmp_path):
    session, _, _ = make_session(tmp_path, [GuidanceCandidate("A", "일반", "r")])
    session.generate()
    session.comment(1, "x")
    session.propose_guidance()

    with pytest.raises(ValueError):
        session.pick(9)
    with pytest.raises(ValueError):
        session.comment(1, "   ")
    with pytest.raises(ValueError):
        session.requirement_ids([3])
    with pytest.raises(ValueError):
        session.approve([2])


def test_session_passes_the_approved_brief_to_the_writer_only_when_there_is_one(tmp_path):
    from modular_agentic.core.types import Brief

    seen: list[dict] = []

    class BriefAwareWriter:
        def write(self, item, question, mappings, verdicts, *, n=1, focus=None, previous=(), brief=None):
            seen.append({"brief": brief})
            return [Draft(item_id=item.id, sections={question: "본문"})]

    brief = Brief(item_id="i1", subject="회사")
    for given in (brief, None):
        session = FeedbackSession(
            item=ITEM,
            question=QUESTION,
            mappings=MAPPINGS,
            verdicts=[],
            writer_factory=lambda guidance: BriefAwareWriter(),
            proposer=FakeProposer([]),
            store=FeedbackStore(tmp_path / "e.jsonl", tmp_path / "g.md"),
            brief=given,
        )
        session.generate(n=1)
    assert [s["brief"] for s in seen] == [brief, None]
