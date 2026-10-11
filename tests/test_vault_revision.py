"""vault 상태 기록(tools/vault_revision). 임시 git 저장소로 검증한다.

핵심: (1) 깨끗하면 커밋 해시만 (2) 미커밋 변경이 있으면 개수와 내용 지문이 붙는다 — 같은 해시라도 내용이 다르면 지문이 다르다
(3) 같은 내용이면 같은 지문 (4) 추적 안 하는 새 파일도 반영, .gitignore 파일은 무시 (5) git이 아니면 unknown
(6) 환경변수 스탬프는 이미 있으면 덮어쓰지 않고 검색 로그에 vault_rev로 남는다
"""
import json
import subprocess

import pytest

from modular_agentic.tools import vault_search as vs
from modular_agentic.tools.vault_revision import ENV_NAME, stamp_environment, vault_revision


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    git(root, "init", "-q")
    (root / ".gitignore").write_text("_scratch/\n", encoding="utf-8")
    (root / "a.md").write_text("처음", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "first")
    return root


def test_clean_repo_label_is_just_the_commit(repo):
    rev = vault_revision(repo)

    assert rev.changed_files == 0 and rev.fingerprint == ""
    assert rev.label == rev.commit and len(rev.commit) >= 7


def test_uncommitted_changes_add_count_and_content_fingerprint(repo):
    clean = vault_revision(repo)
    (repo / "a.md").write_text("바뀜 1", encoding="utf-8")
    first = vault_revision(repo)
    (repo / "a.md").write_text("바뀜 2", encoding="utf-8")
    second = vault_revision(repo)

    assert first.commit == second.commit == clean.commit  # 해시는 같지만
    assert first.changed_files == 1 and first.label.startswith(f"{clean.commit}+") and first.label.endswith("(1)")
    assert first.fingerprint != second.fingerprint  # 내용이 다르면 지문이 다르다
    (repo / "a.md").write_text("바뀜 1", encoding="utf-8")
    assert vault_revision(repo).fingerprint == first.fingerprint  # 같은 내용이면 같은 지문(되돌려도 같다)


def test_untracked_files_count_and_ignored_files_do_not(repo):
    base = vault_revision(repo)
    (repo / "_scratch").mkdir()
    (repo / "_scratch" / "tmp.pdf").write_bytes(b"x")
    assert vault_revision(repo) == base  # .gitignore 파일은 무시

    (repo / "new.md").write_text("새 글", encoding="utf-8")
    added = vault_revision(repo)
    (repo / "new.md").write_text("새 글 수정", encoding="utf-8")
    edited = vault_revision(repo)

    assert added.changed_files == 1 and added.fingerprint != edited.fingerprint  # 새 파일의 내용도 반영한다


def test_not_a_git_repo_is_unknown(tmp_path):
    assert vault_revision(tmp_path).label == "unknown"
    assert vault_revision(tmp_path / "없는폴더").label == "unknown"


def test_stamp_environment_keeps_existing_value_and_search_log_records_it(repo, tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    label = stamp_environment(repo)
    assert label == vault_revision(repo).label
    (repo / "a.md").write_text("딴 내용", encoding="utf-8")
    assert stamp_environment(repo) == label  # 이미 정해졌으면 프로세스 안에서 바뀌지 않는다

    log = tmp_path / "s.jsonl"
    monkeypatch.setenv(vs.LOG_ENV, str(log))
    vs.append_log("질의", [], elapsed_sec=0.1)
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[0])["vault_rev"] == label

    monkeypatch.delenv(ENV_NAME)
    vs.append_log("질의2", [], elapsed_sec=0.1)
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[1])["vault_rev"] == ""  # 모르면 빈 문자열
