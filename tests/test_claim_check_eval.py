"""문장 검증기 평가 도구(eval/claim_check.py)의 순수한 부분: 문장 찾기, 라벨 검사, 점수. 모델은 부르지 않는다."""
import pytest

from modular_agentic.core.types import SentenceCheck, Support
from modular_agentic.eval.claim_check import Label, Row, label_problems, locate, render_eval_md, score

CHECKS = [
    SentenceCheck(1, "[헤드라인]", Support.NO_CLAIM),
    SentenceCheck(2, "브리지로 외부 접근 문제를 풀었습니다.", Support.UNSUPPORTED, note="근거 없음"),
    SentenceCheck(3, "도메인  지식을 학습하겠습니다.", Support.SUPPORTED, "사용자:포부", "모르는 도메인 지식을 학습한다"),
    SentenceCheck(4, "도메인 지식을 학습하겠습니다 라고 한 번 더 씁니다.", Support.PARTIAL),
]


def test_locate_finds_exactly_one_sentence_ignoring_whitespace_differences():
    assert locate(CHECKS, "외부 접근 문제를 풀었").index == 2
    assert locate(CHECKS, "[헤드라인]").index == 1
    with pytest.raises(ValueError, match="없다"):
        locate(CHECKS, "이 구절은 어디에도 없다")
    with pytest.raises(ValueError, match="2개"):
        locate(CHECKS, "도메인 지식을 학습하겠습니다")  # 3·4번 문장에 다 들어 있어 어느 것인지 모호하다


def test_label_problems_catch_labels_that_disagree_with_the_evidence():
    source = "브리지 구조는 결정까지 했다.\n모르는 도메인 지식을 학습한다."
    assert label_problems(Label("x", "pass", must_contain="도메인 지식을 학습한다"), source) == []
    assert label_problems(Label("x", "flag", must_not_contain="12.6초"), source) == []
    assert "must_contain이 근거 원문에 없다" in label_problems(Label("x", "pass", must_contain="12.6초"), source)[0]
    assert "must_not_contain이 근거 원문에 있다" in label_problems(Label("x", "flag", must_not_contain="브리지 구조는 결정"), source)[0]
    assert "must_contain(그 문장의 핵심 구절)이 필요" in label_problems(Label("x", "pass"), source)[0]  # pass 라벨은 근거를 대야 한다
    assert "expect는" in label_problems(Label("x", "maybe"), source)[0]
    assert label_problems(Label("x", "flag"), source) == []  # flag 라벨은 검사 조건이 없어도 된다


def test_score_counts_caught_flags_and_false_positives_separately():
    rows = [
        Row("c", Label("a", "flag"), CHECKS[1]),  # 표시함: 찾아냄
        Row("c", Label("b", "flag"), CHECKS[2]),  # 뒷받침됨으로 놓침
        Row("c", Label("c", "pass"), CHECKS[2]),  # 통과: 맞음
        Row("c", Label("d", "pass"), CHECKS[3]),  # 일부만으로 표시함: 오탐
        Row("c", Label("e", "pass"), CHECKS[0]),  # 사실 주장 없음도 표시가 아니다
    ]
    s = score(rows)
    assert (s.flag_total, s.flag_caught, s.pass_total, s.pass_flagged) == (2, 1, 3, 1)
    assert s.recall == 0.5 and s.false_positive_rate == pytest.approx(1 / 3)
    assert [r.correct for r in rows] == [True, False, True, False, True]
    assert score([]).recall is None and score([]).false_positive_rate is None


def test_report_shows_metrics_wrong_rows_and_dropped_labels():
    rows = [Row("c1", Label("a", "flag", why="구현은 미착수"), CHECKS[1]), Row("c1", Label("b", "flag"), CHECKS[2])]
    md = render_eval_md("시험", rows, ["[c1] 'x': must_contain이 근거 원문에 없다"], meta=["모델: m"])
    assert "찾아낸 비율**(flag 라벨 중 검증기가 표시한 것): 1/2 = 50%" in md and "오탐 비율**(pass 라벨 중 검증기가 표시한 것): 0/0 = 해당 없음" in md
    planted_md = render_eval_md("시험", [Row("c", Label("a", "flag", why="심은 문장: 근거 없음"), CHECKS[1]), Row("c", Label("b", "flag", why="실제"), CHECKS[2])], [])
    assert "실제 초안에서 찾은 틀린 문장: 0/1 = 0%" in planted_md and "일부러 심은 문장(대조군): 1/1 = 100%" in planted_md
    md2 = render_eval_md("시험", rows, [], unlabeled_flagged=[("c1", CHECKS[3])])
    assert "| 라벨 이유 | 검증기 메모 |" in md2 and "구현은 미착수" in md2 and "근거 없음" in md2  # 라벨 이유와 검증기 메모를 둘 다 보인다
    assert "라벨 없는 문장 중 검증기가 표시한 것 (1개)" in md2 and "[c1] **[4] 일부만**" in md2
    assert "평가에서 뺀 라벨 (1개)" in md and "**틀림**" in md and "구현은 미착수" in md and "- 모델: m" in md
