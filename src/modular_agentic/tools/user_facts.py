"""사용자가 직접 말한 사실을 근거(Evidence)로 읽는다. vault와 대조하지 않는다.

왜: 소재 근거가 vault에만 있으면 사용자만 아는 이야기(협업 관계 등)를 쓰려고 vault부터 고쳐야 한다. 이 파일은 그 우회로다.
쓰는 곳: 소재 배정(scripts/make_plan.py --user-facts)과 작성(scripts/run_writer_demo.py --user-facts).
검증하지 않는 대신 출처 표시를 `사용자:<소제목>`으로 남겨, 소재 계획·초안에서 vault 근거와 구분해 보인다.

파일 모양: `## 소제목` 하나가 근거 하나다. 소제목 아래 줄들이 발췌가 된다(첫 `##` 앞의 글은 설명이라 읽지 않는다).
읽기 전용이다.
"""
from __future__ import annotations

from pathlib import Path

from modular_agentic.core.types import Evidence, Mapping, MappingStatus, Requirement

REF_PREFIX = "사용자:"
EXCERPT_CHARS = 1500  # vault 근거(400자)보다 길게 둔다. 사용자가 한 줄씩 적은 진술이 잘리면 "말하지 않은 것" 표시까지 잃는다.


def is_user_ref(ref: str) -> bool:
    return ref.startswith(REF_PREFIX)


def load_user_facts(path: Path) -> list[Evidence]:
    """`## 소제목` 단위로 나눠 Evidence로 만든다. 파일이 없거나 소제목이 하나도 없으면 ValueError."""
    if not path.is_file():
        raise ValueError(f"사용자 진술 파일이 없다: {path}")
    sections: dict[str, list[str]] = {}
    title = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            if not title:
                raise ValueError(f"소제목이 비어 있다: {path}")
            if title in sections:
                raise ValueError(f"같은 소제목이 두 번 나온다(근거 출처가 겹친다): {title}")
            sections[title] = []
        elif title is not None:
            sections[title].append(line)
    found = [Evidence(ref=f"{REF_PREFIX}{t}", excerpt=" ".join(" ".join(body).split())[:EXCERPT_CHARS]) for t, body in sections.items()]
    empty = [e.ref[len(REF_PREFIX):] for e in found if not e.excerpt]
    if empty:
        raise ValueError(f"내용이 없는 소제목이 있다(근거로 쓸 수 없다): {empty} — {path}")
    if not found:
        raise ValueError(f"'## 소제목'과 그 아래 내용이 있어야 한다: {path}")
    return found


def user_facts_mapping(item_id: str, evidence: list[Evidence]) -> list[Mapping]:
    """사용자 진술을 추가 재료 매핑 하나로 묶는다. 검증하지 않았다는 표시를 판단에 남긴다."""
    if not evidence:
        return []
    requirement = Requirement(id=f"{item_id}-user-facts", item_id=item_id, text="사용자가 직접 말한 사실", kind="사용자 제공")
    note = "사용자가 직접 말한 사실이다. vault와 대조하지 않았다. 적힌 범위를 넘는 세부(기간·횟수·수치·사건)는 쓰지 않는다."
    return [Mapping(requirement, evidence, MappingStatus.CONFIRMED, note)]
