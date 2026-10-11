"""scripts/make_plan.py의 순수한 부분: 사용자가 지정한 vault 근거 읽기. Bedrock은 부르지 않는다."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("make_plan", SCRIPTS / "make_plan.py")
make_plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_plan)

from modular_agentic.core.types import MappingStatus  # noqa: E402


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "06_career").mkdir()
    lines = ["# 회사B", "", "### 레퍼런스 게임 APK 디코딩으로 레벨 데이터 추출", "- APK를 디컴파일해 바이너리를 분석", "- 약 2만 개의 레벨 데이터 추출", "- 자사 게임에 포팅", "- 수백 배 빠르다", "- 끝"]
    (tmp_path / "06_career" / "회사B.md").write_text("\n".join(lines), encoding="utf-8")
    return tmp_path


def test_evidence_is_read_from_the_given_line_for_exactly_six_lines(vault):
    [ev] = make_plan.evidence_from_refs(["06_career/회사B.md:1"], vault)  # 1~6줄
    assert ev.ref == "06_career/회사B.md:1"
    assert ev.excerpt.startswith("# 회사B ### 레퍼런스 게임 APK 디코딩으로 레벨 데이터 추출 - APK를 디컴파일해") and "약 2만 개의 레벨 데이터 추출 - 자사 게임에 포팅" in ev.excerpt
    assert "수백 배" not in ev.excerpt and "끝" not in ev.excerpt  # 7줄부터는 읽지 않는다
    [late] = make_plan.evidence_from_refs(["06_career/회사B.md:3"], vault)  # 3~8줄(파일 끝까지)
    assert late.excerpt.endswith("수백 배 빠르다 - 끝")


def test_bad_refs_exit_instead_of_adding_wrong_evidence(vault):
    for bad in ("06_career/회사B.md", "06_career/회사B.md:abc", "06_career/없는파일.md:1", "06_career/회사B.md:99", "../밖의파일.md:1"):
        with pytest.raises(SystemExit):
            make_plan.evidence_from_refs([bad], vault)


def test_pinned_mapping_is_one_confirmed_extra_material_marked_as_user_chosen(vault):
    [m] = make_plan.pinned_mapping("job", ["06_career/회사B.md:3", "06_career/회사B.md:5"], vault)
    assert m.status is MappingStatus.CONFIRMED and m.requirement.id == "job-pinned" and [e.ref for e in m.evidence] == ["06_career/회사B.md:3", "06_career/회사B.md:5"]
    assert "사용자가 소재로 직접 지정" in m.note and "검증 단계를 거치지 않았다" in m.note  # 검증을 거치지 않은 근거라는 표시가 남는다


def test_no_refs_means_no_pinned_mapping():
    assert make_plan.pinned_mapping("job", []) == []


def test_long_vault_excerpt_is_marked_as_cut_and_reported_in_the_plan_warnings(tmp_path):
    from modular_agentic.tools.vault_read import EXCERPT_CHARS, TRUNCATED_MARK

    (tmp_path / "03_study").mkdir()
    (tmp_path / "03_study" / "길다.md").write_text("가" * (EXCERPT_CHARS + 50) + "\n짧다\n", encoding="utf-8")
    (tmp_path / "03_study" / "짧다.md").write_text("한 줄이다\n", encoding="utf-8")
    long_ev, short_ev = make_plan.evidence_from_refs(["03_study/길다.md:1", "03_study/짧다.md:1"], tmp_path)
    assert long_ev.excerpt.endswith(TRUNCATED_MARK) and not short_ev.excerpt.endswith(TRUNCATED_MARK)  # 잘렸을 때만 표시한다
    [m] = make_plan.pinned_mapping("job", ["03_study/길다.md:1", "03_study/짧다.md:1"], tmp_path)
    [notice] = make_plan.truncation_notice([m])
    assert "03_study/길다.md:1" in notice and "03_study/짧다.md:1" not in notice and f"{EXCERPT_CHARS}자에서 잘렸다" in notice
    assert make_plan.truncation_notice([]) == []
