"""실험·실행 기록에 남길 vault의 상태(git 커밋 + 미커밋 변경의 지문).

왜 필요한가 (docs/experiments/002 11절): vault는 계속 고쳐 가며 쓰는 자료라 같은 질의도 시점에 따라 재료가 달라진다.
어느 vault 상태로 돌린 결과인지 모르면 결과를 비교하거나 재현할 수 없다. vault는 git이라 커밋 해시가 스냅샷 id가 된다.

커밋 해시만으로는 부족하다: vault에 **미커밋 변경**이 늘 있어서 같은 해시라도 내용이 다를 수 있다. 그래서 미커밋 변경이 있으면
(추적 중인 파일의 변경 diff + 추적하지 않는 새 파일의 이름·내용)의 해시를 지문으로 같이 남긴다. 같은 내용이면 같은 지문이다.

표기(`label`)
  `abc1234`                  미커밋 변경 없음 — 이 커밋 그대로
  `abc1234+3f9a1c2e(8)`      미커밋 변경 8개, 변경 내용의 지문 3f9a1c2e (이 상태를 정확히 가리킨다)
  `unknown`                  git 저장소가 아니거나 git을 못 부름

한계: 지문은 "같은 상태인가"만 알려 준다. 그 상태를 되살리려면 미커밋 변경이 어딘가에 남아 있어야 한다(커밋하거나 stash).
`.gitignore`로 제외된 파일은 지문에 들어가지 않는다(vault의 `_scratch/`는 색인에서도 제외된다).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

ENV_NAME = "MAS_VAULT_REV"  # 설정돼 있으면 검색 로그의 `vault_rev`로 남는다


@dataclass(frozen=True)
class VaultRevision:
    commit: str | None
    changed_files: int = 0
    fingerprint: str = ""  # 미커밋 변경이 있을 때만

    @property
    def label(self) -> str:
        if self.commit is None:
            return "unknown"
        if self.changed_files == 0:
            return self.commit
        return f"{self.commit}+{self.fingerprint}({self.changed_files})"


def _git(vault_dir: Path, *args: str) -> bytes | None:
    try:
        done = subprocess.run(  # noqa: S603 (shell 없이 인자 목록)
            ["git", "-C", str(vault_dir), *args], capture_output=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def vault_revision(vault_dir: Path) -> VaultRevision:
    head = _git(vault_dir, "rev-parse", "--short", "HEAD")
    if head is None:
        return VaultRevision(commit=None)
    commit = head.decode("utf-8").strip()
    status = _git(vault_dir, "status", "--porcelain", "-z", "--untracked-files=all")
    if status is None:
        return VaultRevision(commit=commit)
    entries = [e for e in status.split(b"\0") if e]
    if not entries:
        return VaultRevision(commit=commit)

    digest = hashlib.sha1()  # noqa: S324 (보안용이 아니라 상태 식별용)
    digest.update(_git(vault_dir, "diff", "HEAD") or b"")  # 추적 중인 파일의 변경(삭제 포함)
    untracked = _git(vault_dir, "ls-files", "--others", "--exclude-standard", "-z") or b""
    for raw in sorted(p for p in untracked.split(b"\0") if p):
        digest.update(b"\0untracked\0" + raw + b"\0")
        try:
            digest.update((vault_dir / raw.decode("utf-8", errors="replace")).read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
    return VaultRevision(commit=commit, changed_files=len(entries), fingerprint=digest.hexdigest()[:8])


def stamp_environment(vault_dir: Path) -> str:
    """vault 상태를 계산해 `MAS_VAULT_REV` 환경변수에 넣고(이미 있으면 그대로) label을 돌려준다. 스크립트가 시작할 때 부른다."""
    if ENV_NAME not in os.environ:
        os.environ[ENV_NAME] = vault_revision(vault_dir).label
    return os.environ[ENV_NAME]
