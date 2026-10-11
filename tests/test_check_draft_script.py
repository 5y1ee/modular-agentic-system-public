"""scripts/common.py의 문장 검증용 근거 읽기(collect_check_sources). 못 읽은 근거는 조용히 빼지 않고 끝낸다. AWS는 부르지 않는다."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("common", SCRIPTS / "common.py")
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)

from modular_agentic.core.types import Brief, Citation, Claim  # noqa: E402

BRIEF = Brief("job", "회사", claims=[Claim("c1", "회사 사실 하나", "사업", [Citation("d", "인용 구절입니다 충분히")]), Claim("c2", "회사 사실 둘", "문화", [Citation("d", "또 다른 인용 구절입니다")])])


@pytest.fixture
def vault(tmp_path, monkeypatch):
    (tmp_path / "03_study").mkdir()
    (tmp_path / "03_study" / "a.md").write_text("첫 줄이다\n둘째 줄이다\n", encoding="utf-8")
    monkeypatch.setattr(common, "VAULT_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def facts(tmp_path):
    path = tmp_path / "facts.md"
    path.write_text("## 포부\n- 도메인을 학습한다.\n", encoding="utf-8")
    return path


def test_reads_vault_user_and_company_sources_in_the_order_given_without_duplicates(vault, facts):
    evidence, claims = common.collect_check_sources(["사용자:포부", "03_study/a.md:1", "사용자:포부"], ["c2", "c1", "c2"], BRIEF, facts)
    assert [e.ref for e in evidence] == ["사용자:포부", "03_study/a.md:1"]
    assert "도메인을 학습한다" in evidence[0].excerpt and "첫 줄이다" in evidence[1].excerpt
    assert [c.id for c in claims] == ["c2", "c1"]


def test_sources_that_cannot_be_read_end_the_run_instead_of_being_dropped(vault, facts):
    with pytest.raises(SystemExit, match="vault에서 읽지 못했다"):
        common.collect_check_sources(["03_study/없다.md:1"], [], None, None)
    with pytest.raises(SystemExit, match="--user-facts"):
        common.collect_check_sources(["사용자:포부"], [], None, None)
    with pytest.raises(SystemExit, match="없는 소제목"):
        common.collect_check_sources(["사용자:없는 소제목"], [], None, facts)
    with pytest.raises(SystemExit, match="승인된 조사 보고서"):
        common.collect_check_sources([], ["c1"], None, None)
    with pytest.raises(SystemExit, match="없는 주장 id"):
        common.collect_check_sources([], ["c9"], BRIEF, None)


def test_no_sources_is_allowed_and_means_nothing_to_check_against(vault):
    assert common.collect_check_sources([], [], None, None) == ([], [])


def test_vault_dir_can_be_changed_by_environment_variable(monkeypatch, tmp_path):
    """환경 비종속의 근거: vault 위치를 코드 수정 없이 바꿀 수 있다."""
    import importlib

    from modular_agentic.instances.cover_letter import config

    try:
        monkeypatch.setenv("MAS_VAULT_DIR", str(tmp_path))
        assert importlib.reload(config).VAULT_DIR == tmp_path
        monkeypatch.delenv("MAS_VAULT_DIR")
        assert importlib.reload(config).VAULT_DIR.name == "knowledge-base"  # 없으면 이 기기의 기본 위치
    finally:
        monkeypatch.delenv("MAS_VAULT_DIR", raising=False)
        importlib.reload(config)
