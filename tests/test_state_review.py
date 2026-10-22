"""core/state_review.py: 앞 단계(요구 매핑·검증) 결과를 읽는 용 마크다운으로 바꾸는 부분. 모델을 부르지 않는다."""
from modular_agentic.core.state_review import EXCERPT_CHARS, REQUIREMENT_PREVIEW_CHARS, render_state_md
from modular_agentic.core.types import Evidence, Item, Mapping, MappingStatus, Requirement, Verdict, VerdictKind

ITEM = Item(id="job1", source="file", title="공고", body="본문")


def _req(i: int, text: str) -> Requirement:
    return Requirement(id=f"job1-r{i}", item_id="job1", text=text, kind="필요 역량")


def _state():
    reqs = [_req(1, "학위 취득자"), _req(2, "Python | SQL 활용"), _req(3, "협업 커뮤니케이션 역량 " * 8)]
    mappings = [
        Mapping(reqs[0], [Evidence("a.md:1", "졸업")], MappingStatus.CONFIRMED, "졸업 기록 있음"),
        Mapping(reqs[1], [Evidence("b.md:2", "파이썬\n프로젝트 " * 40)], MappingStatus.CONFIRMED, "충분히 뒷받침"),
        Mapping(reqs[2], [], MappingStatus.TODO, "근거를 못 찾음"),
    ]
    verdicts = [
        Verdict(reqs[0].id, VerdictKind.APPROVED),
        Verdict(reqs[1].id, VerdictKind.FLAGGED, "부풀려 읽힐 수 있음"),
        Verdict(reqs[2].id, VerdictKind.APPROVED),
    ]
    return reqs, mappings, verdicts


def test_summary_table_shows_kind_status_and_flag_for_every_requirement():
    reqs, mappings, verdicts = _state()
    md = render_state_md(ITEM, mappings, verdicts, basic_requirement_ids=[reqs[0].id], vault_rev="abc+1")

    assert md.startswith("# 요구 매핑·검증 결과 — job1") and "`abc+1`" in md
    assert "| 1 | 기본 | 학위 취득자 | 확인 | 승인 |" in md
    assert "| 3 | 어필 |" in md and "| TODO | 승인 |" in md
    assert "요구 3개 중 기본 요건 1개, 검증 플래그 1개" in md


def test_table_cells_escape_pipes_and_cut_long_requirements():
    reqs, mappings, verdicts = _state()
    md = render_state_md(ITEM, mappings, verdicts)

    assert "Python \\| SQL 활용" in md  # 표 칸이 깨지지 않는다
    long_row = next(line for line in md.splitlines() if line.startswith("| 3 |"))
    assert "…" in long_row and len(reqs[2].text) > REQUIREMENT_PREVIEW_CHARS


def test_details_put_appeal_requirements_first_and_basic_ones_last_with_flag_reason_and_evidence():
    reqs, mappings, verdicts = _state()
    md = render_state_md(ITEM, mappings, verdicts, basic_requirement_ids=[reqs[0].id])

    assert md.index("## 어필 대상 요구") < md.index("## 기본 요건(참고)")
    appeal = md.split("## 기본 요건(참고)")[0]
    assert "### 2. Python | SQL 활용 — 확인 ⚑" in appeal and "**플래그 이유**: 부풀려 읽힐 수 있음" in appeal
    assert "**근거**: 없음" in appeal and "**판단**: 근거를 못 찾음" in appeal  # 3번은 근거가 없다
    assert "`b.md:2`" in appeal
    excerpt_line = next(line for line in appeal.splitlines() if line.startswith("- `b.md:2`"))
    assert "\n" not in excerpt_line and len(excerpt_line.split(" — ", 1)[1]) <= EXCERPT_CHARS  # 발췌는 한 줄로 줄인다
    assert "### 1. 학위 취득자" in md.split("## 기본 요건(참고)")[1]


def test_sections_without_requirements_are_left_out_and_missing_vault_rev_is_said_so():
    reqs, mappings, verdicts = _state()
    md = render_state_md(ITEM, mappings, verdicts)  # 기본 요건 표시가 없다

    assert "## 기본 요건(참고)" not in md and "## 어필 대상 요구" in md
    assert "`기록 없음`" in md
