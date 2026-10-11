"""모델이 적은 인용이 자료에 실제로 있는지 대조한다. 프레임워크를 모르는 순수 함수다.

쓰는 곳: 조사 단계(주장의 출처 인용을 자료와 대조), 문장 검증(검증기가 댄 근거 인용을 근거 원문과 대조).
모델은 그럴듯한 인용을 지어낼 수 있어서, "자료에 이 구절이 있다"는 말은 코드가 직접 확인한다.
"""
from __future__ import annotations

import re

MIN_QUOTE_CHARS = 10
MIN_FRAGMENT_CHARS = 4  # 생략 표시(…)로 이은 인용에서 조각 하나의 최소 글자 수. 근거 없는 임의의 시작값

# 인용 대조에서 표기 차이로 맞는 인용이 버려지는 것을 막는다(2026-10-10 실측, 실험 003 3절): 원문의 곡선 따옴표를 모델이 직선으로 적었고,
# 위키의 각주 번호([4])를 모델이 빼고 옮겼고, 마크다운 강조(**)를 빼고 옮겼다. 글자의 의미를 바꾸는 보정이 아니다.
_QUOTE_MARKS = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "“": '"', "”": '"', "„": '"', "″": '"'})
_FOOTNOTE_MARK = re.compile(r"\[\s*(?:주\s*)?\d{1,3}\s*\]")
_ELLIPSIS = re.compile(r"…|\.{3}")


def normalize(text: str) -> str:
    """인용과 자료를 같은 모양으로 맞춘다: 따옴표 모양 통일, 각주 번호·마크다운 강조(**) 제거, 공백을 하나로.
    줄바꿈·들여쓰기 차이로 맞는 인용이 틀렸다고 판정되는 것도 막는다."""
    text = _FOOTNOTE_MARK.sub("", text.translate(_QUOTE_MARKS)).replace("**", "")
    return " ".join(text.split())


def quote_in_text(quote: str, text: str) -> bool:
    """정규화된 인용이 정규화된 자료에 있는가. 생략 표시(…)로 이은 인용은 조각마다 자료에 순서대로 있으면 인정한다.

    조각이 4글자(MIN_FRAGMENT_CHARS) 미만이면 어디에나 맞을 수 있어 인정하지 않는다. 전체가 MIN_QUOTE_CHARS 미만인 인용도 인정하지 않는다.
    이어 붙인 조각들이 서로 다른 문맥이라도 통과할 수 있다(조각이 각각 원문에 있다는 것까지만 본다). 보고서에는 …가 그대로 보인다.
    """
    fragments = [f.strip() for f in _ELLIPSIS.split(quote) if f.strip()]
    if not fragments or sum(len(f) for f in fragments) < MIN_QUOTE_CHARS:
        return False
    if len(fragments) > 1 and any(len(f) < MIN_FRAGMENT_CHARS for f in fragments):
        return False
    position = 0
    for fragment in fragments:
        found = text.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return True
