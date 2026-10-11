"""매핑 에이전트가 부르는 검색 도구.

`tools/vault_search`를 Strands 도구(`@tool`)로 감싼다. 도구는 검색 결과를 에이전트에게 글로 돌려주면서,
돌려준 결과를 SearchRegistry에도 기록한다. 매핑이 끝난 뒤 모델이 인용한 근거가 **정말 검색 결과에 있었는지**
대조하는 데 쓰려는 것이다(mapper.build_mapping).

도구 설명(docstring)은 모델이 읽는 지시문이다.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from strands import tool

from modular_agentic.tools import vault_search
from modular_agentic.tools.vault_search import VaultHit, VaultSearchError

SNIPPET_CHARS_FOR_MODEL = 400  # 모델에게 보여 주는 발췌 길이


def ref_of(hit: VaultHit) -> str:
    return f"{hit.path}:{hit.line}"


def union_seconds(intervals: Sequence[tuple[float, float]]) -> float:
    """구간들이 겹치면 겹친 부분을 한 번만 세어 전체 길이를 구한다."""
    total, current_end = 0.0, float("-inf")
    for start, end in sorted(intervals):
        if end <= current_end:
            continue
        total += end - max(start, current_end)
        current_end = end
    return total


@dataclass
class SearchRegistry:
    """에이전트가 검색으로 실제로 받은 결과들(키는 '경로:줄')과 검색 호출의 시간 기록."""

    hits: dict[str, VaultHit] = field(default_factory=dict)
    intervals: list[tuple[float, float]] = field(default_factory=list)  # 검색 호출마다 (시작, 끝) 단조 시계 값

    def add(self, hit: VaultHit) -> None:
        self.hits.setdefault(ref_of(hit), hit)

    @property
    def search_calls(self) -> int:
        return len(self.intervals)

    @property
    def search_sum_seconds(self) -> float:
        """검색 호출 시간을 단순히 더한 값. 호출이 겹쳤다면 실제 걸린 시간보다 크다."""
        return sum(end - start for start, end in self.intervals)

    @property
    def search_wall_seconds(self) -> float:
        """검색을 하느라 실제로 흐른 시간. 겹친 구간은 한 번만 센다."""
        return union_seconds(self.intervals)

    def resolve(self, ref: str) -> tuple[str, VaultHit] | None:
        """모델이 인용한 ref를 실제 검색 결과와 대조한다.

        '경로:줄'이 그대로 있으면 그것을, 줄 번호가 없거나 달라도 같은 경로의 결과가 있으면 그 중 첫 번째를 돌려준다.
        검색에 나온 적 없는 경로면 None이다.
        """
        ref = ref.strip()
        if ref in self.hits:
            return ref, self.hits[ref]
        path = ref.rsplit(":", 1)[0] if ref.rsplit(":", 1)[-1].isdigit() else ref
        for known_ref, hit in self.hits.items():
            if hit.path == path:
                return known_ref, hit
        return None


def make_search_tool(
    registry: SearchRegistry,
    *,
    exclude_prefixes: Sequence[str],
    n_results: int = 8,
    search_fn: Callable[..., list[VaultHit]] = vault_search.search,
    tag: str = "mapper",
):
    """registry에 결과를 기록하는 `search_vault` 도구를 만든다."""

    @tool
    def search_vault(query: str) -> str:
        """개인 지식 베이스에서 query와 의미가 가까운 글 조각을 찾는다.

        단어 하나보다 "어떤 경험이나 사실을 찾는지"를 설명하는 한국어 문장이 잘 맞는다.
        결과의 점수는 관련 여부를 보장하지 않으므로 발췌를 직접 읽고 판단한다.

        Args:
            query: 찾으려는 경험·사실을 설명하는 문장

        Returns:
            검색 결과 목록. 각 항목은 `[경로:줄]`로 시작하고 그 뒤에 해당 조각의 발췌가 온다.
        """
        start = time.monotonic()
        try:
            hits = search_fn(query, n=n_results, exclude_prefixes=exclude_prefixes, tag=tag)
        except VaultSearchError as exc:
            return f"검색 실패: {exc}"
        finally:
            registry.intervals.append((start, time.monotonic()))
        for hit in hits:
            registry.add(hit)
        if not hits:
            return "검색 결과 없음"
        return "\n\n".join(f"[{ref_of(h)}] {' '.join(h.snippet.split())[:SNIPPET_CHARS_FOR_MODEL]}" for h in hits)

    return search_vault
