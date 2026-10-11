"""scripts/feedback_loop.py의 입력 처리(run_loop). 가짜 입력과 가짜 세션으로 AWS 없이 검증한다."""
import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))  # 스크립트가 `from common import …`로 옆 파일을 부른다
spec = importlib.util.spec_from_file_location("feedback_loop", SCRIPTS / "feedback_loop.py")
feedback_loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feedback_loop)

from test_feedback import make_session  # noqa: E402

from modular_agentic.core.feedback import GuidanceCandidate  # noqa: E402


def run(tmp_path, inputs, candidates=()):
    session, _, log = make_session(tmp_path, candidates)
    queue = iter(inputs)
    outputs: list[str] = []

    def fake_input(prompt=""):
        try:
            return next(queue)
        except StopIteration:
            raise EOFError

    feedback_loop.run_loop(session, input_fn=fake_input, out=outputs.append)
    return session, log, "\n".join(outputs)


def test_pick_comment_more_new_and_restrict_commands(tmp_path):
    session, log, _ = run(tmp_path, ["p 2 결론이 먼저", "f 1 너무 길다", "m", "n", "r 2", "q"])

    assert [e.kind for e in session.store.events()] == ["pick", "comment"]
    assert [(c["n"], c["focus"], len(c["previous"])) for c in log] == [
        (3, None, 0),  # 처음 3개
        (3, None, 3),  # m: 이전 초안을 넘김
        (3, None, 0),  # n: 새로 쓰기
        (3, ["i1-r2"], 0),  # r 2: 요구 2만
    ]
    assert len(session.drafts) == 12


def test_guidance_flow_approves_by_number_and_all(tmp_path):
    cands = [GuidanceCandidate("A", "일반", "r"), GuidanceCandidate("B", "특정", "r")]
    session, log, out = run(tmp_path, ["g", "f 1 피드백", "g", "all", "n"], cands)

    assert "새로 뽑을 피드백이 없거나" in out  # 피드백 전에 g를 누르면 안내
    assert session.store.guidance() == "- A" and session.session_guidance == ["B"]
    assert "- A" in log[-1]["guidance"] and "- B" in log[-1]["guidance"]  # 승인 뒤 n 이 지침을 반영한다


def test_input_errors_do_not_stop_the_loop(tmp_path):
    session, _, out = run(tmp_path, ["p 9", "x", "f 1", "r 7", "p 1 정상", "q"])

    assert out.count("입력 오류") >= 3 and "모르는 명령" in out
    assert [e.kind for e in session.store.events()] == ["pick"]  # 오류 뒤에도 정상 명령은 동작
