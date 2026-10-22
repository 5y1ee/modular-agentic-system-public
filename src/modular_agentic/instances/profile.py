"""업무 설정(Profile): 업무가 바뀌면 달라지는 것을 구현체 밖에 적어 두는 자리(업무 축, 2026-10-21).

구현체(engines/)는 업무를 모르고, 업무마다 다른 말·규칙을 여기서 받아 프롬프트를 만든다.
지금은 분석 단계에 필요한 것만 옮겼다: 대상 이름, 분석 역할, 뽑을 범위, 분류(kind) 규칙.
나머지 단계(조사·매핑·검증·소재 계획·작성)의 업무 차이는 아직 구현체 프롬프트 안에 있어 자소서 전용이다(`stages`로 표시).

공고 파일의 업무는 제목 다음 줄들 중 `업무: <이름 또는 라벨>` 줄로 정한다(없으면 자소서). 수집 단계가 이 줄을 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass

META_SCAN_LINES = 8  # 제목 다음 이만큼의 줄에서 `업무:`를 찾는다


@dataclass(frozen=True)
class Profile:
    name: str  # 코드에서 쓰는 이름(cover_letter, proposal)
    label: str  # 화면에 보이는 이름(자소서, 제안서)
    item_label: str  # 분석 대상 이름(채용공고, 제안요청서)
    analyzer_role: str  # "너는 {analyzer_role}이다."
    analyzer_scope: str  # 무엇을 뽑고 무엇을 빼는가(규칙 한 줄)
    analyzer_kind: str  # kind 칸 규칙(규칙 한 줄)
    stages: tuple[str, ...]  # 이 업무로 돌릴 수 있는 콘솔 단계
    collectors: tuple[str, ...] = ("paste",)  # 이 업무에 맞는 수집 구현(registry의 collector 이름). 크롤러·웹 에이전트는 채용공고 출처다


def _profiles() -> dict[str, Profile]:
    from .cover_letter.config import PROFILE as cover_letter
    from .proposal.config import PROFILE as proposal

    return {p.name: p for p in (cover_letter, proposal)}


def profile(name: str = "cover_letter") -> Profile:
    profiles = _profiles()
    for p in profiles.values():
        if name in (p.name, p.label):
            return p
    raise KeyError(f"모르는 업무: {name} (있는 것: {', '.join(profiles)})")


def all_profiles() -> list[Profile]:
    return list(_profiles().values())


def profile_of_text(text: str) -> Profile:
    """공고 파일 글에서 업무를 읽는다. `업무:` 줄이 없으면 자소서.

    값이 업무 이름이 아니면 그 줄은 공고 본문으로 보고 넘어간다(채용공고에 "업무: 서버 개발" 같은 줄이 흔하다).
    """
    for line in text.splitlines()[1 : 1 + META_SCAN_LINES]:
        if line.startswith("업무:"):
            try:
                return profile(line.split(":", 1)[1].strip())
            except KeyError:
                continue
    return profile()
