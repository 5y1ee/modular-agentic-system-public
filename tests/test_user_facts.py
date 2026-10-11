"""사용자가 직접 말한 사실(tools/user_facts.py)과 그것을 소재 계획·작성에 잇는 부분. Bedrock은 부르지 않는다."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("common", SCRIPTS / "common.py")
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)

from modular_agentic.core.plan_review import PlanRecord, approve, plan_path, save_record  # noqa: E402
from modular_agentic.core.types import Draft, MappingStatus, Plan, PlannedExperience, QuestionPlan  # noqa: E402
from modular_agentic.tools.user_facts import is_user_ref, load_user_facts, user_facts_mapping  # noqa: E402

FACTS = """# 설명 글(읽지 않는다)

## 프로젝트A — 기획자 협업
- Jira로 의견을 주고받는다.
- 사용자의 인상: 신뢰받는다.
- 말하지 않은 것: 기간, 횟수.

## 회사C
- 1개월 인턴.
"""


@pytest.fixture
def facts_file(tmp_path):
    path = tmp_path / "facts.md"
    path.write_text(FACTS, encoding="utf-8")
    return path


def test_each_second_level_heading_is_one_evidence_with_a_user_ref(facts_file):
    found = load_user_facts(facts_file)
    assert [e.ref for e in found] == ["사용자:프로젝트A — 기획자 협업", "사용자:회사C"]
    assert "Jira로 의견을 주고받는다. - 사용자의 인상" in found[0].excerpt and "말하지 않은 것: 기간, 횟수." in found[0].excerpt
    assert "설명 글" not in " ".join(e.excerpt for e in found)  # 첫 소제목 앞의 글은 읽지 않는다
    assert all(is_user_ref(e.ref) for e in found) and not is_user_ref("06_career/회사B.md:3")


def test_bad_fact_files_raise_instead_of_adding_nothing_silently(tmp_path):
    with pytest.raises(ValueError):
        load_user_facts(tmp_path / "없는파일.md")
    empty = tmp_path / "empty.md"
    empty.write_text("# 제목만 있다\n\n## 내용 없는 소제목\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_user_facts(empty)
    dup = tmp_path / "dup.md"
    dup.write_text("## 같은 이름\n- a\n\n## 같은 이름\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_user_facts(dup)


def test_mapping_is_confirmed_extra_material_marked_as_unverified(facts_file):
    [m] = user_facts_mapping("job", load_user_facts(facts_file))
    assert m.status is MappingStatus.CONFIRMED and m.requirement.id == "job-user-facts"
    assert "vault와 대조하지 않았다" in m.note and len(m.evidence) == 2
    assert user_facts_mapping("job", []) == []


def test_draft_markdown_flags_unverified_user_facts():
    def draft(refs):
        return Draft(item_id="job", sections={"본문": "글"}, angle="각도", rationale="이유", used_refs=refs)

    assert "검증 안 된 사용자 진술 포함" in common.drafts_to_md("문항", [draft(["사용자:회사C", "06_career/회사B.md:3"])])
    assert "검증 안 된 사용자 진술 포함" not in common.drafts_to_md("문항", [draft(["06_career/회사B.md:3"])])


@pytest.fixture
def approved_plan_using_user_fact(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "PLAN_DIR", tmp_path / "plans")
    monkeypatch.setattr(common, "MAP_DIR", tmp_path / "state")  # 상태 파일이 없어도 배정된 근거를 읽을 수 있어야 한다
    (tmp_path / "plans").mkdir()
    exp = PlannedExperience(name="기획자 협업", why="협업", refs=["사용자:회사C"])
    plan = Plan(item_id="job", variant="이름", questions=[QuestionPlan(number=2, question="문항", reading="", angle="", experiences=[exp])])
    save_record(plan_path(tmp_path / "plans", "job", "이름"), approve(PlanRecord(plan=plan)))


def test_writer_gets_user_fact_evidence_from_the_facts_file(approved_plan_using_user_fact, facts_file):
    question_plan, extra = common.load_plan_question("job", "이름", 2, facts_file)
    assert question_plan.number == 2
    [m] = extra
    assert [e.ref for e in m.evidence] == ["사용자:회사C"] and "1개월 인턴" in m.evidence[0].excerpt  # 배정된 소제목만 넘긴다


def test_writer_stops_when_the_plan_needs_user_facts_but_none_given_or_heading_missing(approved_plan_using_user_fact, tmp_path):
    with pytest.raises(SystemExit):
        common.load_plan_question("job", "이름", 2)
    other = tmp_path / "other.md"
    other.write_text("## 다른 소제목\n- 내용\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        common.load_plan_question("job", "이름", 2, other)


def test_an_empty_heading_among_others_is_an_error_not_silently_dropped(tmp_path):
    path = tmp_path / "facts.md"
    path.write_text("## 내용 있음\n- 사실\n\n## 비어 있음\n\n## 또 있음\n- 사실2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="비어 있음"):
        load_user_facts(path)
