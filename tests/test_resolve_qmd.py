"""resolve_qmd_argv가 윈도우의 qmd.cmd를 cmd.exe 없이 node 직접 호출로 바꾸는지 확인한다."""
import pytest

from modular_agentic.tools.vault_search import VaultSearchError, resolve_qmd_argv


def test_non_windows_uses_exe_as_is():
    argv = resolve_qmd_argv(is_windows=False, which=lambda name: "/usr/bin/qmd")
    assert argv == ["/usr/bin/qmd"]


def test_windows_cmd_shim_resolves_to_node_entry(tmp_path):
    (tmp_path / "qmd.cmd").touch()
    entry = tmp_path / "node_modules" / "@tobilu" / "qmd" / "bin" / "qmd"
    entry.parent.mkdir(parents=True)
    entry.touch()
    paths = {"qmd": str(tmp_path / "qmd.CMD"), "node": "C:/node.exe"}

    argv = resolve_qmd_argv(is_windows=True, which=paths.get)

    assert argv == ["C:/node.exe", str(entry)]  # cmd.exe를 거치지 않는다


def test_windows_cmd_shim_without_entry_raises(tmp_path):
    paths = {"qmd": str(tmp_path / "qmd.cmd"), "node": "C:/node.exe"}
    with pytest.raises(VaultSearchError):
        resolve_qmd_argv(is_windows=True, which=paths.get)


def test_missing_qmd_raises():
    with pytest.raises(VaultSearchError):
        resolve_qmd_argv(is_windows=False, which=lambda name: None)
