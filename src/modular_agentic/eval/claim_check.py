"""문장 검증기의 평가: 사람이 라벨을 붙인 문장을 검증기가 제대로 가려내는가.

라벨 두 종류: `flag`는 사람이 근거와 대조해 틀리거나 근거 없다고 확인한 문장(검증기가 표시해야 한다), `pass`는 근거에 있다고 확인한 문장
(검증기가 표시하면 오탐이다). 지표는 두 가지다: 찾아낸 비율(flag 라벨 중 표시한 것)과 오탐 비율(pass 라벨 중 표시한 것).

라벨이 근거와 어긋나면 평가를 오염시키므로 먼저 검사한다: pass 라벨은 `must_contain`(그 문장의 핵심 구절)이 검증기가 본 근거 원문에 실제로 있어야 하고,
flag 라벨 중 "근거에 없어서" 틀린 것은 `must_not_contain`(문장이 단정한 구절)이 근거 원문에 없어야 한다. 어긋난 라벨은 평가에서 빼고 이유를 보고한다.
정답 표본에는 자소서 본문이 들어 있어 git에 올리지 않는다(inputs/eval/). 이 모듈에는 코드만 있다.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from modular_agentic.core.claim_check import FLAGGED
from modular_agentic.core.quote_match import normalize
from modular_agentic.core.types import SentenceCheck

EXPECT = ("flag", "pass")
IN_SAMPLE_NOTICE = ("이 점수는 **개발 표본 위의 값**이다. 검증기 프롬프트의 규칙은 이 표본의 오류를 본 뒤에 정했다(표본의 구절·예시를 프롬프트에 넣지는 않도록 "
                    "고쳤지만, 어떤 종류의 오류를 잡을지 정한 것 자체가 표본에서 나왔다). 새 공고의 새 초안에서도 같은 값이 나온다는 뜻이 아니다.")
PLANTED_PREFIX = "심은 문장"  # flag 라벨의 why가 이 말로 시작하면 사람이 일부러 끼워 넣은 대조군이다(실제 초안에서 찾은 것보다 쉽다)


@dataclass(frozen=True)
class Label:
    marker: str  # 문장 안에 들어 있는 구절(문장 하나를 가리킬 만큼 구체적이어야 한다)
    expect: str  # "flag" | "pass"
    why: str = ""
    must_contain: str = ""
    must_not_contain: str = ""


@dataclass(frozen=True)
class Row:
    case_id: str
    label: Label
    check: SentenceCheck

    @property
    def flagged(self) -> bool:
        return self.check.support in FLAGGED

    @property
    def correct(self) -> bool:
        return self.flagged == (self.label.expect == "flag")


@dataclass(frozen=True)
class Score:
    flag_total: int
    flag_caught: int
    pass_total: int
    pass_flagged: int

    @property
    def recall(self) -> float | None:
        return self.flag_caught / self.flag_total if self.flag_total else None

    @property
    def false_positive_rate(self) -> float | None:
        return self.pass_flagged / self.pass_total if self.pass_total else None


def locate(checks: Sequence[SentenceCheck], marker: str) -> SentenceCheck:
    """marker가 들어 있는 문장을 찾는다. 없거나 둘 이상이면 ValueError(라벨이 어느 문장인지 모호하면 평가할 수 없다)."""
    wanted = normalize(marker)
    found = [c for c in checks if wanted in normalize(c.sentence)]
    if not found:
        raise ValueError(f"그 구절이 들어 있는 문장이 없다: {marker!r}")
    if len(found) > 1:
        raise ValueError(f"그 구절이 들어 있는 문장이 {len(found)}개다(더 구체적으로): {marker!r}")
    return found[0]


def label_problems(label: Label, source_text: str) -> list[str]:
    """라벨이 검증기가 본 근거와 어긋나는 곳. 비어 있으면 평가에 쓸 수 있다."""
    problems = []
    if label.expect not in EXPECT:
        problems.append(f"expect는 {EXPECT} 중 하나여야 한다: {label.expect!r}")
    text = normalize(source_text)
    if label.expect == "pass" and not label.must_contain:
        problems.append("pass 라벨에는 must_contain(그 문장의 핵심 구절)이 필요하다")
    if label.must_contain and normalize(label.must_contain) not in text:
        problems.append(f"must_contain이 근거 원문에 없다: {label.must_contain!r}")
    if label.must_not_contain and normalize(label.must_not_contain) in text:
        problems.append(f"must_not_contain이 근거 원문에 있다: {label.must_not_contain!r}")
    return problems


def score(rows: Sequence[Row]) -> Score:
    flag_rows = [r for r in rows if r.label.expect == "flag"]
    pass_rows = [r for r in rows if r.label.expect == "pass"]
    return Score(len(flag_rows), sum(r.flagged for r in flag_rows), len(pass_rows), sum(r.flagged for r in pass_rows))


def _pct(value: float | None) -> str:
    return "해당 없음" if value is None else f"{value:.0%}"


def render_eval_md(title: str, rows: Sequence[Row], invalid: Sequence[str], meta: Sequence[str] = (), unlabeled_flagged: Sequence[tuple[str, SentenceCheck]] = ()) -> str:
    s = score(rows)
    real = score([r for r in rows if not r.label.why.startswith(PLANTED_PREFIX)])
    planted = score([r for r in rows if r.label.why.startswith(PLANTED_PREFIX)])
    lines = [f"# 문장 검증기 평가 — {title}", "", *[f"- {m}" for m in meta], "",
             f"- **찾아낸 비율**(flag 라벨 중 검증기가 표시한 것): {s.flag_caught}/{s.flag_total} = {_pct(s.recall)}",
             f"  - 실제 초안에서 찾은 틀린 문장: {real.flag_caught}/{real.flag_total} = {_pct(real.recall)}",
             f"  - 일부러 심은 문장(대조군): {planted.flag_caught}/{planted.flag_total} = {_pct(planted.recall)}",
             f"- **오탐 비율**(pass 라벨 중 검증기가 표시한 것): {s.pass_flagged}/{s.pass_total} = {_pct(s.false_positive_rate)}",
             "- 표본이 작아서 이 비율은 방향을 보는 용도다. 라벨은 한 건의 초안에서 사람이 근거와 대조해 붙였다.", f"- {IN_SAMPLE_NOTICE}", ""]
    if invalid:
        lines += [f"## 평가에서 뺀 라벨 ({len(invalid)}개)", ""] + [f"- {x}" for x in invalid] + [""]
    lines += ["## 라벨별 결과", "", "| 결과 | 라벨 | 검증기 판정 | 문장(앞 60자) | 라벨 이유 | 검증기 메모 |", "|---|---|---|---|---|---|"]
    for r in rows:
        mark = "맞음" if r.correct else "**틀림**"
        sentence = r.check.sentence.replace("|", "/")
        lines.append(f"| {mark} | {r.label.expect} | {r.check.support.value} | {sentence[:60]} | {r.label.why.replace('|', '/')[:70]} | {r.check.note.replace('|', '/')[:120]} |")
    if unlabeled_flagged:
        lines += ["", f"## 라벨 없는 문장 중 검증기가 표시한 것 ({len(unlabeled_flagged)}개) — 맞게 잡았는지 오탐인지는 사람이 근거 원문과 보고 판단한다", ""]
        for case_id, c in unlabeled_flagged:
            lines.append(f"- [{case_id}] **[{c.index}] {c.support.value}** — {c.sentence}")
            lines.append(f"  - 검증기 메모: {c.note or '(없음)'}" + (f" / 인용 `{c.source}`: \"{c.quote[:80]}\"" if c.quote else ""))
    return "\n".join(lines) + "\n"
