"""scripts/map_values.py의 순수한 부분: 가치 파일 읽기와 요구 만들기. Bedrock·qmd는 부르지 않는다."""
import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("map_values", SCRIPTS / "map_values.py")
map_values = importlib.util.module_from_spec(spec)
spec.loader.exec_module(map_values)

TEXT = """# 현대오토에버 Talent Value (제목 줄은 건너뛴다)

## MINDSET

Make It Yours — 일의 가치를 스스로 만들어 갑니다. 우리는 맡은 일의 의미를 만든다.

Work With Grit — 집요하지만 빠르게 해결합니다. 문제의 본질을 끝까지 파고든다.

설명만 있는 줄이거나 — 기호가 없는 줄은 건너뛴다
"""


def test_parse_values_reads_name_and_definition_lines_only():
    assert map_values.parse_values(TEXT) == [
        ("Make It Yours", "일의 가치를 스스로 만들어 갑니다. 우리는 맡은 일의 의미를 만든다."),
        ("Work With Grit", "집요하지만 빠르게 해결합니다. 문제의 본질을 끝까지 파고든다."),
    ]
    assert map_values.parse_values("") == []


def test_each_value_becomes_a_requirement_with_its_definition_for_search():
    reqs = map_values.values_to_requirements("job_values", map_values.parse_values(TEXT))
    assert [r.id for r in reqs] == ["job_values-v1", "job_values-v2"]
    assert reqs[1].text.startswith("Talent Value 'Work With Grit'을 실천한 경험")
    assert "문제의 본질을 끝까지" in reqs[1].text and reqs[0].kind == "핵심 가치"


def test_render_values_md_shows_status_judgement_evidence_and_flags_sorted_by_strength():
    from modular_agentic.core.types import Evidence, Mapping, MappingStatus, Verdict, VerdictKind

    reqs = map_values.values_to_requirements("job_values", [("Make Bold Moves", "과감히 도전한다 정의 문장입니다"), ("Work With Grit", "집요하게 해결한다 정의 문장입니다")])
    mappings = [
        Mapping(reqs[0], [], MappingStatus.TODO, "근거 없음 이유"),
        Mapping(reqs[1], [Evidence("04_project/x.md:3", "원인을 끝까지 추적했다 " * 3)], MappingStatus.CONFIRMED, "충분히 뒷받침"),
    ]
    verdicts = [Verdict(reqs[0].id, VerdictKind.APPROVED), Verdict(reqs[1].id, VerdictKind.FLAGGED, "과장 이유")]

    md = map_values.render_values_md(mappings, verdicts)

    table = md.split("## ")[0]
    assert table.index("Work With Grit") < table.index("Make Bold Moves")  # 근거가 강한 가치가 앞에 온다
    assert "| Work With Grit | 확인 | ⚑ 플래그 |" in md and "| Make Bold Moves | TODO | 승인 |" in md
    assert "**플래그 이유**: 과장 이유" in md and "`04_project/x.md:3`" in md and "**근거**: 없음" in md
    assert map_values.value_name(reqs[1]) == "Work With Grit"
    assert "**가치의 뜻(공식 문구)**: 집요하게 해결한다 정의 문장입니다" in md  # 고르는 데 필요한 가치의 뜻이 같이 나온다
    assert map_values.value_definition(reqs[0]) == "과감히 도전한다 정의 문장입니다" and "**내 경험 근거 판단**: 충분히 뒷받침" in md
