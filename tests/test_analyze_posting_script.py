"""scripts/analyze_posting.py·make_plan.py의 분석 결과 연결: 상태에서 분석 만들기, 기본 요건 우선순위(--basic > 분석 결과), 맥락 걸러내기. 모델은 부르지 않는다."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyze_posting = load("analyze_posting")
make_plan = load("make_plan")

from modular_agentic.core.serialize import dump_state  # noqa: E402
from modular_agentic.core.types import Analysis, ContextNote, Evidence, Item, Mapping, MappingStatus, Requirement  # noqa: E402

REQS = [Requirement(f"job-r{i}", "job", f"요구 {i}") for i in range(1, 4)]
MAPPINGS = [Mapping(r, [Evidence("a.md:1", "근거")], MappingStatus.CONFIRMED, "확인") for r in REQS]


def test_analysis_is_made_from_the_saved_state_without_a_model(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze_posting, "MAP_DIR", tmp_path)
    (tmp_path / "job.json").write_text(json.dumps(dump_state(Item("job", "file", "제목", "본문"), MAPPINGS, [])), encoding="utf-8")
    analysis = analyze_posting.analysis_from_state("job")
    assert [r.id for r in analysis.requirements] == ["job-r1", "job-r2", "job-r3"] and analysis.basic_requirement_ids == []  # 표시는 처음엔 비어 있다
    with pytest.raises(SystemExit, match="상태 파일이 없다"):
        analyze_posting.analysis_from_state("없는공고")


def test_parse_numbers_and_missing_analysis_message(tmp_path, monkeypatch):
    assert analyze_posting.parse_numbers("1, 2,9") == [1, 2, 9] and analyze_posting.parse_numbers("") == []
    monkeypatch.setattr(analyze_posting, "load_saved_analysis", lambda item_id: None)
    with pytest.raises(SystemExit, match="분석 결과가 없다"):
        analyze_posting.require_analysis("job")


def test_basic_requirements_come_from_the_flag_first_then_the_analysis_marks_then_none():
    analysis = Analysis("job", REQS, basic_requirement_ids=["job-r3"])
    assert make_plan.resolve_basic("1,2", MAPPINGS, analysis) == (["job-r1", "job-r2"], "--basic")  # 명시한 --basic이 우선
    assert make_plan.resolve_basic("", MAPPINGS, analysis) == (["job-r3"], "분석 결과(02_analyze)의 표시")
    assert make_plan.resolve_basic("", MAPPINGS, Analysis("job", REQS)) == ([], "없음")  # 표시가 비어 있으면 없음
    assert make_plan.resolve_basic("", MAPPINGS, None) == ([], "없음")
    with pytest.raises(ValueError, match="범위를 벗어난다"):
        make_plan.resolve_basic("0,4", MAPPINGS, None)


def test_analysis_marks_that_do_not_match_the_state_requirements_are_an_error_not_ignored():
    stale = Analysis("job", [Requirement("job-r9", "job", "다른 요구")], basic_requirement_ids=["job-r9"])
    with pytest.raises(ValueError, match="요구 목록이 다르다"):
        make_plan.resolve_basic("", MAPPINGS, stale)


def test_context_for_keeps_only_requirements_present_in_the_state():
    analysis = Analysis("job", REQS, context={"job-r2": [ContextNote("c1", "이유")], "job-r9": [ContextNote("c2", "상태에 없는 요구")]})
    assert make_plan.context_for(MAPPINGS, analysis) == {"job-r2": [ContextNote("c1", "이유")]}
    assert make_plan.context_for(MAPPINGS, Analysis("job", REQS)) is None and make_plan.context_for(MAPPINGS, None) is None
