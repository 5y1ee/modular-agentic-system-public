"""사용자 피드백으로 작성 지침을 고치는 루프. 터미널이든 frontend든 이 모듈을 부른다(입출력은 여기 없다).

흐름
  1. 초안을 낸다(`generate`). 더 뽑거나(`more`) 요구를 골라 다시 쓸 수 있다(`generate(focus=…)`).
  2. 사용자가 가장 좋은 초안을 고르거나(`pick`) 초안에 피드백을 준다(`comment`). 둘 다 기록으로 남는다.
  3. 기록에서 작성 지침 후보를 뽑는다(`propose_guidance`, 뽑는 일은 GuidanceProposer가 한다).
  4. 사용자가 승인한 후보만 지침이 된다(`approve`). 승인 전에는 아무것도 프롬프트에 들어가지 않는다.

지침 두 종류
  일반  문체·구조·분량·표현 선호. 다른 공고·문항에도 맞는다 → 지침 파일에 저장돼 다음 세션에도 쓰인다.
  특정  이 공고·문항의 내용(어떤 경험을 강조할지 등). 이 세션에서만 쓰고 저장하지 않는다.
  (한 문항에서 받은 피드백이 다른 문항으로 새는 것을 막으려는 것이다.)

자동으로 지침을 쌓지 않는 이유: 모델이 알아서 규칙을 쌓으면 서로 충돌하거나 낡은 규칙이 남아 프롬프트가 이상해질 수 있다.
저장하는 것: 피드백 기록(`events`)과 지침 파일. 둘 다 자소서 내용이 들어 있어 git에 올리지 않는 logs/ 아래에 둔다.

이 방식은 강화학습이 아니다. 사람 피드백으로 프롬프트(지침)를 고치는 간단한 self-improving이다.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from .stages import Writer
from .types import Brief, Draft, Item, Mapping, Verdict

EventKind = Literal["pick", "comment", "guidance"]
GuidanceScope = Literal["일반", "특정"]


@dataclass(frozen=True)
class FeedbackEvent:
    """사용자가 한 일 하나. kind가 pick이면 가장 좋은 초안 선택(note는 이유, 비어도 됨), comment면 초안에 준 피드백,
    guidance면 승인한 지침(note가 지침 문장)이다. 초안에 대한 것이면 그 초안의 각도와 본문이 같이 남는다."""

    ts: str
    item_id: str
    question: str
    kind: EventKind
    note: str = ""
    draft_angle: str = ""
    draft_text: str = ""
    scope: str = ""  # guidance일 때만: "일반" 또는 "특정"


@dataclass(frozen=True)
class GuidanceCandidate:
    """모델이 피드백에서 뽑은 지침 후보. 사용자가 승인하기 전에는 지침이 아니다."""

    text: str  # 한 줄 지침
    scope: GuidanceScope
    reason: str  # 어떤 피드백에서 나왔는지


class GuidanceProposer(Protocol):
    def propose(self, events: Sequence[FeedbackEvent], current_guidance: str) -> list[GuidanceCandidate]:
        """피드백 기록에서 작성 지침 후보를 뽑는다. 사용자가 실제로 한 말에서만 뽑고, 이미 있는 지침과 겹치면 뺀다."""
        ...


WriterFactory = Callable[[str], Writer]  # 지침 문장 → 그 지침을 쓰는 Writer


class FeedbackStore:
    """피드백 기록(JSONL, 줄마다 이벤트 하나)과 일반 지침 파일(줄마다 `- 지침`)을 읽고 쓴다."""

    def __init__(self, events_path: Path, guidance_path: Path) -> None:
        self.events_path = events_path
        self.guidance_path = guidance_path

    def append(self, event: FeedbackEvent) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def events(self) -> list[FeedbackEvent]:
        if not self.events_path.exists():
            return []
        return [
            FeedbackEvent(**json.loads(line))
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def guidance(self) -> str:
        return self.guidance_path.read_text(encoding="utf-8").strip() if self.guidance_path.exists() else ""

    def add_guidance(self, text: str) -> None:
        self.guidance_path.parent.mkdir(parents=True, exist_ok=True)
        with self.guidance_path.open("a", encoding="utf-8") as f:
            f.write(f"- {text.strip()}\n")


@dataclass
class FeedbackSession:
    """한 공고·문항에 대한 초안 작성과 피드백 한 판. 초안 번호는 1부터, `more`로 늘어도 이어서 센다."""

    item: Item
    question: str
    mappings: list[Mapping]
    verdicts: list[Verdict]
    writer_factory: WriterFactory
    proposer: GuidanceProposer
    store: FeedbackStore
    n: int = 3
    brief: Brief | None = None  # 사용자가 승인한 조사 보고서. 있으면 작성이 회사 사실로 쓴다.
    clock: Callable[[], str] = field(default=lambda: datetime.now().isoformat(timespec="seconds"))
    drafts: list[Draft] = field(default_factory=list)
    session_guidance: list[str] = field(default_factory=list)  # "특정" 지침. 저장하지 않는다.
    _events: list[FeedbackEvent] = field(default_factory=list)
    _proposed: int = 0  # 지침 후보를 이미 뽑은 이벤트 수(같은 피드백으로 후보를 두 번 만들지 않으려고)
    _pending: list[GuidanceCandidate] = field(default_factory=list)

    @property
    def active_guidance(self) -> str:
        """지금 쓰는 지침 문장: 저장된 일반 지침 + 이 세션의 특정 지침."""
        parts = [self.store.guidance()]
        if self.session_guidance:
            parts.append("이번 공고·문항에만 적용:\n" + "\n".join(f"- {g}" for g in self.session_guidance))
        return "\n".join(p for p in parts if p)

    def generate(self, *, n: int | None = None, focus: Sequence[str] | None = None, avoid_previous: bool = False) -> list[Draft]:
        """초안을 n개(기본 self.n) 쓴다. focus는 재료로 쓸 요구 id들, avoid_previous면 이미 낸 초안과 다른 각도로 쓴다."""
        writer = self.writer_factory(self.active_guidance)
        extra = {} if self.brief is None else {"brief": self.brief}  # brief를 모르는 Writer 구현도 그대로 돈다
        new = writer.write(
            self.item,
            self.question,
            self.mappings,
            self.verdicts,
            n=n or self.n,
            focus=focus,
            previous=self.drafts if avoid_previous else (),
            **extra,
        )
        self.drafts.extend(new)
        return new

    def more(self, n: int | None = None) -> list[Draft]:
        """더 뽑기: 이미 낸 초안과 다른 각도로 쓴다."""
        return self.generate(n=n, avoid_previous=True)

    def requirement_ids(self, numbers: Sequence[int]) -> list[str]:
        """요구 번호(매핑 순서, 1부터)를 요구 id로 바꾼다. 범위를 벗어나면 ValueError."""
        ids = []
        for num in numbers:
            if not 1 <= num <= len(self.mappings):
                raise ValueError(f"요구 번호 {num}은 1~{len(self.mappings)} 범위를 벗어난다")
            ids.append(self.mappings[num - 1].requirement.id)
        return ids

    def _draft(self, number: int) -> Draft:
        if not 1 <= number <= len(self.drafts):
            raise ValueError(f"초안 번호 {number}은 1~{len(self.drafts)} 범위를 벗어난다")
        return self.drafts[number - 1]

    def _record(self, kind: EventKind, note: str, draft: Draft | None = None, scope: str = "") -> FeedbackEvent:
        event = FeedbackEvent(
            ts=self.clock(),
            item_id=self.item.id,
            question=self.question,
            kind=kind,
            note=note.strip(),
            draft_angle=draft.angle if draft else "",
            draft_text=next(iter(draft.sections.values()), "") if draft else "",
            scope=scope,
        )
        self.store.append(event)
        self._events.append(event)
        return event

    def pick(self, number: int, reason: str = "") -> FeedbackEvent:
        """가장 좋은 초안을 고른다. 이유는 비어도 되지만 있으면 지침 후보가 훨씬 정확해진다."""
        return self._record("pick", reason, self._draft(number))

    def comment(self, number: int, text: str) -> FeedbackEvent:
        if not text.strip():
            raise ValueError("피드백 내용이 비어 있다")
        return self._record("comment", text, self._draft(number))

    def propose_guidance(self) -> list[GuidanceCandidate]:
        """아직 후보를 뽑지 않은 피드백으로 지침 후보를 뽑는다. 새 피드백이 없으면 빈 목록(모델을 부르지 않는다)."""
        fresh = [e for e in self._events[self._proposed :] if e.kind in ("pick", "comment")]
        self._proposed = len(self._events)
        if not fresh:
            self._pending = []
            return []
        self._pending = self.proposer.propose(fresh, self.active_guidance)
        return list(self._pending)

    def approve(self, numbers: Sequence[int]) -> list[GuidanceCandidate]:
        """대기 중인 후보 중 번호(1부터)로 고른 것만 지침으로 만든다. 일반은 파일에 저장, 특정은 이 세션에서만 쓴다."""
        chosen = []
        for num in numbers:
            if not 1 <= num <= len(self._pending):
                raise ValueError(f"지침 후보 번호 {num}은 1~{len(self._pending)} 범위를 벗어난다")
            chosen.append(self._pending[num - 1])
        for cand in chosen:
            if cand.scope == "일반":
                self.store.add_guidance(cand.text)
            else:
                self.session_guidance.append(cand.text)
            self._record("guidance", cand.text, scope=cand.scope)
        self._pending = [c for c in self._pending if c not in chosen]
        return chosen
