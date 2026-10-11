"""자소서 인스턴스 설정.

VAULT_EXCLUDE_PREFIXES: 근거(Evidence)를 찾을 때 검색 결과에서 뺄 vault 경로 접두사.

뺀 이유 (2026-10-03, "AI Agent 개발 경험"으로 검색한 한 번의 결과에서 나온 판단):
- 공고 요구 문구로 검색하면 그 문구를 인용한 공고 분석·자소서 페이지가 위로 올라온다.
  이런 곳은 내 경험의 근거가 아니라 요구 문구가 있는 곳이다.
- 과거 자소서 서술은 검증되지 않은 주장이라, 그대로 근거로 쓰면 사실과 다른 서술이 재사용될 수 있다.

미결정: `06_career/자소서/`를 근거에서 완전히 뺄지, "참고용·검증 필요"로 남길지.
사용자가 고민 중이다. 확정되면 이 목록에서 한 줄만 고치면 된다.
"""
import os
from pathlib import Path

# 근거를 찾는 지식 베이스(마크다운 vault)의 위치. 기기마다 다르므로 환경변수 MAS_VAULT_DIR로 바꾼다(없으면 이 기기의 기본 위치).
# 근거 읽기(tools/vault_read)와 평가(eval)가 페이지 본문을 직접 읽을 때 쓴다. 검색은 qmd가 색인한 위치를 쓰므로 이 값과 따로다.
VAULT_DIR = Path(os.environ.get("MAS_VAULT_DIR") or Path.home() / "knowledge-base")

VAULT_EXCLUDE_PREFIXES: tuple[str, ...] = (
    "06_career/자소서/",
    "06_career/관심 채용공고.md",
    "06_career/지원 전략 - CJ·DB.md",
    "00_raw/",
)

# 가공된 서술: 사실을 골라 다시 쓴 글(면접 답변, 포트폴리오). 같은 경험을 길게 서술해서 검색에서
# 원본 페이지보다 위에 올라오는 일이 있었다(docs/experiments/001). 위 제외 목록에 더한 실험용 목록이다.
# 아직 기본 목록에 합치지 않았다 — 효과를 재측정한 뒤 정한다.
DERIVED_NARRATIVE_PREFIXES: tuple[str, ...] = (
    "06_career/지원처 11",  # 면접 준비·최종본·모의면접 1·2회차·면접스터디
    "06_career/포트폴리오/",
)
VAULT_EXCLUDE_WITH_DERIVED: tuple[str, ...] = VAULT_EXCLUDE_PREFIXES + DERIVED_NARRATIVE_PREFIXES

# 조사(Researcher)가 회사 자료에서 뽑을 주제. 처음 정한 목록이고 써 보며 고친다(docs/design-research-stage.md 15절).
# 자료에 근거가 없는 주제는 주장을 만들지 않고 보고서의 "자료가 없어 못 다룬 주제"로 남는다.
RESEARCH_TOPICS: tuple[str, ...] = (
    "사업·주력 제품",
    "최근 동향",
    "산업 이슈",
    "조직·문화",
    "핵심 가치·인재상",
    "채용 직무의 맥락",
)

# 회사 자료를 검색으로 찾을 때 쓰는 질의 틀: (조사 주제, 질의 틀, 뉴스 검색 여부). {company}가 회사 이름으로 바뀐다.
# 처음 정한 틀이고 써 보며 고친다. 뉴스 검색은 최근 기사를 우선한다. 스크립트가 tools/source_search.QuerySpec으로 바꾼다
# (인스턴스 설정이 tools를 import하지 않게 단순한 튜플로 둔다).
RESEARCH_QUERIES: tuple[tuple[str, str, bool], ...] = (
    ("사업·주력 제품", "{company} 회사 소개 사업 영역 주요 제품", False),
    ("최근 동향", "{company} 최근 실적 사업 전략", True),
    ("산업 이슈", "{company} 산업 동향 시장 전망", True),
    ("조직·문화", "{company} 조직문화 인재상 복리후생", False),
)
