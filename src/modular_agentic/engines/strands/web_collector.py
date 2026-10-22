"""StrandsWebCollector — 웹 검색 에이전트로 채용공고 원문을 찾아 Item으로 모은다(core.stages.Collector, 목적 축의 구현체).

검색 한 번으로 끝나지 않고 에이전트가 도구를 골라 가며 찾는다.
  web_search(query)        Tavily 검색. 검색어는 에이전트가 목표를 보고 바꿔 가며 정한다
  fetch_page(url)          페이지를 읽어 공고 원문인지 확인한다(사설 주소·로그인 페이지는 tools/sources.fetch_url이 거절)
  already_collected(text)  이미 모은 공고(inputs/00_collect)의 제목과 겹치는지 본다
에이전트는 후보마다 원문 URL과 "공고 원문임을 보여 주는 문장(quote)"을 낸다. 코드가 확인하는 것:
  1. 그 URL을 이번 실행에서 fetch_page로 실제로 읽었는가(읽지 않은 주소는 버린다 — 지어낸 공고 차단)
  2. quote가 그 페이지 글에 글자 그대로 있는가(core/quote_match, 조사 단계의 인용 대조와 같은 함수)
통과한 후보의 본문은 모델이 쓴 글이 아니라 읽은 페이지 글 그대로다. 버린 후보는 이유와 함께 `rejected`에 남긴다.

한계: 검색 결과에 오래된 공고·채용 포털의 목록 페이지가 섞인다(실험 003에서 본 문제). 마감 여부는 페이지에 적힌 것만 옮기고 판단하지 않는다.
비용: Bedrock 여러 번(도구 호출마다) + Tavily 검색 횟수만큼 크레딧. 검색·페이지 읽기 횟수에 상한을 둔다.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any

from modular_agentic.core.quote_match import quote_in_text
from modular_agentic.core.types import Item
from modular_agentic.tools.postings import collected_titles
from modular_agentic.tools.sources import fetch_url

from .config import build_agent
from .schemas import PostingCandidatesOut

MAX_SEARCHES = 4
MAX_FETCHES = 8
PAGE_CHARS_FOR_MODEL = 6000  # 모델에게 보여 주는 페이지 글 길이. 저장하는 본문은 아래 상한까지
MAX_BODY_CHARS = 20000
MIN_QUOTE_CHARS = 8  # 너무 짧은 인용은 어느 페이지에나 맞아 대조가 무의미하다

SYSTEM_PROMPT = """너는 지원자를 위해 채용공고 원문을 찾아오는 수집 에이전트다.

할 일: 사용자가 준 목표에 맞는 채용공고를 찾아, 원문 페이지를 읽고 후보로 제출한다.
도구
- web_search: 검색한다. 검색어는 목표에 맞게 스스로 정하고, 결과가 나쁘면 바꿔서 다시 검색한다.
- fetch_page: 페이지를 읽는다. 후보로 내려면 반드시 이 도구로 그 페이지를 읽어야 한다.
- already_collected: 회사·직무 이름으로 이미 모은 공고와 겹치는지 확인한다. 겹치면 후보에서 뺀다.
규칙
- 채용공고 원문(회사 채용 페이지, 채용 플랫폼의 공고 상세)만 후보로 낸다. 뉴스 기사·목록 페이지·블로그 후기는 내지 않는다.
- quote에는 읽은 페이지에서 공고 원문임을 보여 주는 문장(자격요건·담당업무 한 줄 등)을 글자 그대로 복사한다. 바꾸거나 요약하지 않는다.
- 마감일은 페이지에 적혀 있을 때만 쓴다. 이미 지난 마감이 적혀 있으면 내지 않는다.
- 못 찾았으면 빈 목록을 낸다. 지어내지 않는다."""

AgentFactory = Callable[[Sequence[Any]], Any]


def default_agent_factory(tools: Sequence[Any]) -> Any:
    return build_agent(SYSTEM_PROMPT, tools)


def default_search() -> Any:
    from modular_agentic.tools.source_search import TavilySearch

    return TavilySearch()


class StrandsWebCollector:
    def __init__(self, *, goal: str, max_candidates: int = 5, search: Any = None, fetch: Callable = fetch_url,
                 agent_factory: AgentFactory = default_agent_factory) -> None:
        if not goal.strip():
            raise ValueError("웹 검색 수집에는 목표(어떤 공고를 찾을지)가 필요하다")
        self.goal, self.max_candidates = goal.strip(), max_candidates
        self._search, self._fetch, self._agent_factory = search, fetch, agent_factory
        self.pages: dict[str, str] = {}  # 이번 실행에서 실제로 읽은 페이지 {url: 글}
        self.searches: list[str] = []
        self.rejected: list[tuple[str, str]] = []  # (url, 버린 이유)

    def _tools(self) -> list:
        from strands import tool

        search = self._search or default_search()

        @tool
        def web_search(query: str) -> str:
            """웹을 검색해 후보 페이지(제목·주소·요약)를 돌려준다. 요약은 서비스가 만든 것이라 사실로 쓰지 말고 fetch_page로 확인한다."""
            if len(self.searches) >= MAX_SEARCHES:
                return f"검색 상한({MAX_SEARCHES}회)에 닿았다. 지금까지 찾은 것으로 마무리한다."
            self.searches.append(query)
            hits = search.search(query, max_results=6)
            return json.dumps([{"title": h.title, "url": h.url, "snippet": h.snippet[:200], "published": h.published} for h in hits],
                              ensure_ascii=False)

        @tool
        def fetch_page(url: str) -> str:
            """페이지를 읽어 글을 돌려준다. 후보로 낼 페이지는 반드시 이 도구로 읽는다."""
            if url not in self.pages and len(self.pages) >= MAX_FETCHES:
                return f"페이지 읽기 상한({MAX_FETCHES}회)에 닿았다."
            doc = self._fetch(url)
            if not doc.text:
                return f"못 읽음: {doc.reason}"
            self.pages[url] = doc.text[:MAX_BODY_CHARS]
            text = doc.text[:PAGE_CHARS_FOR_MODEL]
            return text + (f"\n[잘림: 전체 {len(doc.text)}자 중 앞 {PAGE_CHARS_FOR_MODEL}자]" if len(doc.text) > PAGE_CHARS_FOR_MODEL else "")

        @tool
        def already_collected(text: str) -> str:
            """회사·직무 이름 일부로 이미 모은 공고 제목을 찾는다. 겹치는 제목 목록을 돌려준다(없으면 빈 목록)."""
            needle = text.strip().lower()
            return json.dumps([t for t in collected_titles().values() if needle and needle in t.lower()], ensure_ascii=False)

        return [web_search, fetch_page, already_collected]

    def collect(self) -> list[Item]:
        agent = self._agent_factory(self._tools())
        result = agent(f"목표: {self.goal}\n후보는 최대 {self.max_candidates}개.", structured_output_model=PostingCandidatesOut)
        items: list[Item] = []
        for c in result.structured_output.candidates[: self.max_candidates]:
            page = self.pages.get(c.url)
            if page is None:
                self.rejected.append((c.url, "이번 실행에서 읽지 않은 주소다"))
            elif len(c.quote.strip()) < MIN_QUOTE_CHARS or not quote_in_text(c.quote, page):
                self.rejected.append((c.url, f"인용이 페이지에 없다: {c.quote[:60]}"))
            else:
                digest = hashlib.sha256(c.url.encode("utf-8")).hexdigest()[:10]
                meta = {"company": c.company, "role": c.role, "deadline": c.deadline, "link": c.url,
                        "found_by": f"웹 검색 에이전트: {self.goal}", "reason": c.reason}
                items.append(Item(id=f"web_{digest}", source="web_agent", title=f"{c.company} {c.role}".strip(), body=page, meta=meta))
        return items
