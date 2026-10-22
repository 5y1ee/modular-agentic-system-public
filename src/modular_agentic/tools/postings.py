"""수집한 공고(Item)를 뒤 단계가 읽는 공고 파일(`inputs/00_collect/<id>.txt`)로 저장하고, 이미 모은 공고를 읽는다.

공고 파일 형식은 수집 단계가 생기기 전부터 쓰던 것 그대로다: 첫 줄이 제목이고(스크립트가 첫 줄을 제목으로 읽는다) 그 뒤가 본문.
수집 출처·링크·마감일 같은 부가 정보는 제목 다음 줄들에 `출처: …`처럼 적는다(분석 모델이 읽어도 요구로 뽑지 않는 정보다).
이미 같은 id의 파일이 있으면 덮어쓰지 않는다(사람이 고친 전사본을 지우지 않으려고).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from modular_agentic.core.types import Item

COLLECT_DIR = Path("inputs") / "00_collect"
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
META_LABELS = {"instance": "업무", "company": "회사", "role": "직무", "deadline": "마감", "link": "출처", "found_by": "찾은 방법"}  # 업무는 instances/profile.py가 읽는다


@dataclass(frozen=True)
class Saved:
    path: Path
    created: bool  # False면 이미 있어서 그대로 뒀다


def posting_text(item: Item) -> str:
    meta = [f"{label}: {item.meta[key]}" for key, label in META_LABELS.items() if item.meta.get(key)]
    return "\n".join([item.title.strip(), *meta, "", item.body.strip(), ""])


def save_posting(item: Item, collect_dir: Path = COLLECT_DIR) -> Saved:
    if not ID_RE.match(item.id):
        raise ValueError(f"공고 id로 쓸 수 없다: {item.id}")
    path = collect_dir / f"{item.id}.txt"
    if path.exists():
        return Saved(path, False)
    collect_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(posting_text(item), encoding="utf-8")
    return Saved(path, True)


def collected_titles(collect_dir: Path = COLLECT_DIR) -> dict[str, str]:
    """이미 모은 공고 {id: 제목}. 중복 확인용."""
    if not collect_dir.is_dir():
        return {}
    titles = {}
    for path in sorted(collect_dir.glob("*.txt")):
        if path.name.endswith(".questions.txt"):
            continue
        with path.open(encoding="utf-8") as f:
            titles[path.stem] = f.readline().strip()
    return titles
