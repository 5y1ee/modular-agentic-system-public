"""문장 단위 근거 검증의 순수한 부분: 글을 문장으로 나누기, 검증기가 댄 인용을 코드가 대조하기, 읽는 용 보고서.

왜: 초안에는 근거에 없는 문장이 섞여 나온다(2026-10-11 현대오토에버: 브리지를 "풀었다"고 쓴 것, 고객사 협업의 방향이 반대로 쓰인 것,
지시문에만 있던 수치가 인용 없이 나온 것). 지금까지는 사람이 근거 원문과 한 문장씩 대조해 찾았다. 이 모듈과 engines의 검증기가 그 일을 한다.

검증기(모델)도 틀린다. 그래서 "뒷받침됨"이라고 한 문장은 근거에서 그대로 옮긴 인용을 같이 내게 하고, 그 인용이 근거 원문에 실제로 있는지는
코드가 확인한다(조사 단계의 인용 대조와 같은 방식, core/quote_match.py). 인용이 없거나 맞지 않으면 "근거 없음"으로 내린다.
모델이 "일부만"이라고 하며 댄 인용이 틀린 경우는 판정은 두고 메모에 그 사실을 적는다.

한계: "근거가 이 문장을 정말 뒷받침하는가"의 판단 자체는 모델의 몫이라 코드가 확인하지 못한다. 사람이 최종 확인한다.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence

from .quote_match import MIN_QUOTE_CHARS, normalize, quote_in_text
from .types import SentenceCheck, Support

FLAGGED = frozenset({Support.PARTIAL, Support.UNSUPPORTED, Support.CONTRADICTED})  # 사람이 봐야 하는 판정
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")
_NOT_PROSE = re.compile(r"^\s*(\(헤드라인 포함|\*글자 수)")  # 자소서 파일에 붙는 분량 메모 줄은 글이 아니다


def split_sentences(text: str) -> list[str]:
    """글을 문장으로 나눈다. 줄바꿈과 마침표·물음표·느낌표 뒤 공백에서 끊는다. 대괄호 헤드라인 한 줄은 문장 하나다.
    `0.05~0.10초`·`v3.0.0`처럼 마침표 뒤에 공백이 없는 것은 끊지 않는다. 분량 메모 줄은 뺀다."""
    sentences: list[str] = []
    for line in text.splitlines():
        if not line.strip() or _NOT_PROSE.match(line):
            continue
        sentences += [s.strip() for s in _SENTENCE_END.split(line.strip()) if s.strip()]
    return sentences


def apply_quote_check(checks: Sequence[SentenceCheck], sources: MappingABC[str, str]) -> list[SentenceCheck]:
    """검증기가 댄 인용을 근거 원문과 대조하고, 맞지 않으면 판정을 바로잡는다.

    sources: 출처 이름(근거 ref 또는 회사 사실 id) → 그 원문. "뒷받침됨"인데 인용이 없거나(MIN_QUOTE_CHARS 미만 포함),
    출처가 입력에 없거나, 인용이 그 출처 원문에 없으면 "근거 없음"으로 내린다. "일부만"은 판정을 두고 메모만 붙인다.
    """
    normalized = {name: normalize(text) for name, text in sources.items()}
    result: list[SentenceCheck] = []
    for check in checks:
        if check.support not in (Support.SUPPORTED, Support.PARTIAL):
            result.append(check)
            continue
        quote = normalize(check.quote)
        problem = _quote_problem(quote, check.source, normalized)
        if problem is None:
            result.append(check)
        elif check.support is Support.SUPPORTED:
            result.append(SentenceCheck(check.index, check.sentence, Support.UNSUPPORTED, check.source, check.quote,
                                        f"검증기가 '뒷받침됨'이라 했지만 {problem}. 코드가 '근거 없음'으로 내렸다."))
        else:
            result.append(SentenceCheck(check.index, check.sentence, check.support, check.source, check.quote,
                                        (check.note + " " if check.note else "") + f"(검증기가 댄 인용은 {problem})"))
    return result


def _quote_problem(quote: str, source: str, normalized: MappingABC[str, str]) -> str | None:
    if len(quote) < MIN_QUOTE_CHARS:
        return f"인용이 없거나 {MIN_QUOTE_CHARS}자보다 짧다"
    if source not in normalized:
        return f"인용의 출처 '{source or '(없음)'}'가 입력 근거에 없다"
    if not quote_in_text(quote, normalized[source]):
        return f"인용이 출처 '{source}' 원문에 없다"
    return None


def flagged(checks: Sequence[SentenceCheck]) -> list[SentenceCheck]:
    return [c for c in checks if c.support in FLAGGED]


def summary(checks: Sequence[SentenceCheck]) -> str:
    counts = Counter(c.support for c in checks)
    return f"문장 {len(checks)}개 — " + " / ".join(f"{s.value} {counts[s]}" for s in Support if counts[s])


def render_check_md(title: str, checks: Sequence[SentenceCheck], notes: Sequence[str] = ()) -> str:
    """사람이 읽는 검증 보고서. 사람이 봐야 하는 문장(일부만·근거 없음·근거와 다름)을 먼저 보이고, 나머지는 뒤에 목록으로 둔다."""
    lines = [f"# 문장 검증 — {title}", "", summary(checks), "",
             "⚠는 사람이 근거 원문과 대조할 문장이다. 이 판정은 모델이 한 것이고, 인용이 근거에 실제로 있는지만 코드가 확인했다. 뒷받침됨도 틀릴 수 있다.", ""]
    lines += [f"- {n}" for n in notes] + ([""] if notes else [])
    bad = flagged(checks)
    lines.append(f"## 확인할 문장 ({len(bad)}개)" if bad else "## 확인할 문장 없음")
    lines.append("")
    for c in bad:
        lines += _entry(c, mark="⚠ ")
    rest = [c for c in checks if c.support not in FLAGGED]
    if rest:
        lines += ["", f"## 나머지 ({len(rest)}개)", ""]
        for c in rest:
            lines += _entry(c)
    return "\n".join(lines) + "\n"


def _entry(c: SentenceCheck, mark: str = "") -> list[str]:
    out = [f"- {mark}**[{c.index}] {c.support.value}** — {c.sentence}"]
    if c.quote:
        out.append(f"  - 근거 `{c.source or '출처 없음'}`: \"{c.quote}\"")
    if c.note:
        out.append(f"  - 메모: {c.note}")
    return out
