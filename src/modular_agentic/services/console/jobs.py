"""작업(job) 실행기: 단계 스크립트를 백그라운드로 돌리고 상태·로그를 파일로 남긴다.

흐름: 만들기(pending, 확인 대기) → 사람이 확인(running) → 끝(done/failed) 또는 취소(cancelled).
버튼이든 채팅이든 작업은 먼저 pending으로 만들고, 실행은 사람이 화면에서 확인해야 시작한다(비용·승인 변경을 사람이 쥔다).
한 번에 하나만 돌린다. 단계들이 같은 상태 파일과 qmd 서버(포트 하나)를 쓰므로 동시에 돌리면 서로 덮어쓴다.

상태는 `logs/console/jobs/<id>.json`, 출력은 같은 이름의 `.log`다(자소서 내용이 섞일 수 있어 git에 올리지 않는다).
서버를 다시 켜면 실행 중이던 작업은 결과를 알 수 없으므로 failed로 표시한다.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .actions import ActionError, build_argv, job_env, validate

DEFAULT_JOB_DIR = Path("logs") / "console" / "jobs"
LOG_TAIL_CHARS = 6000


class JobRunner:
    def __init__(self, job_dir: Path = DEFAULT_JOB_DIR, cwd: Path | None = None, argv_builder=build_argv):
        self.job_dir = job_dir
        self.cwd = cwd
        self._build = argv_builder  # 테스트에서 가짜 명령으로 바꾼다
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        job_dir.mkdir(parents=True, exist_ok=True)
        for job in self.list(limit=1000):
            if job["status"] == "running":
                self._save({**job, "status": "failed", "error": "서버가 다시 켜져 결과를 모른다", "ended": time.time()})

    # ---- 조회 ----
    def _path(self, job_id: str) -> Path:
        if not job_id.isalnum():
            raise ActionError(f"잘못된 작업 id: {job_id}")
        return self.job_dir / f"{job_id}.json"

    def get(self, job_id: str) -> dict:
        path = self._path(job_id)
        if not path.is_file():
            raise ActionError(f"없는 작업: {job_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, limit: int = 20) -> list[dict]:
        jobs = [json.loads(p.read_text(encoding="utf-8")) for p in self.job_dir.glob("*.json")]
        return sorted(jobs, key=lambda j: j["created"], reverse=True)[:limit]

    def log_tail(self, job_id: str, chars: int = LOG_TAIL_CHARS) -> str:
        self.get(job_id)
        log = self.job_dir / f"{job_id}.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        return text[-chars:]

    def _save(self, job: dict) -> dict:
        self._path(job["id"]).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        return job

    # ---- 바꾸기 ----
    def create(self, action_key: str, item_id: str, params: dict, source: str = "button") -> dict:
        """검사하고 확인 대기(pending) 작업을 만든다. 아직 실행하지 않는다."""
        action, clean = validate(action_key, item_id, params)
        job = {
            "id": uuid.uuid4().hex[:12], "action": action.key, "label": action.label, "item": item_id,
            "params": clean, "cost": action.cost, "source": source, "status": "pending",
            "created": time.time(), "started": None, "ended": None, "returncode": None, "error": "",
        }
        return self._save(job)

    def confirm(self, job_id: str) -> dict:
        """사람이 확인한 작업을 시작한다. 다른 작업이 돌고 있으면 거부한다."""
        with self._lock:
            job = self.get(job_id)
            if job["status"] != "pending":
                raise ActionError(f"확인 대기 중인 작업이 아니다({job['status']})")
            running = [j for j in self.list(limit=1000) if j["status"] == "running"]
            if running:
                raise ActionError(f"다른 작업이 실행 중이다: {running[0]['label']} ({running[0]['id']})")
            action, clean = validate(job["action"], job["item"], job["params"])  # 그사이 파일이 바뀌었을 수 있어 다시 검사
            argv = self._build(action, job["item"], clean)
            log = open(self.job_dir / f"{job_id}.log", "w", encoding="utf-8")
            env = {**os.environ, **job_env(action, clean), "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}  # 고른 구현·모델
            # 새 세션(프로세스 그룹)으로 띄워 취소할 때 qmd 서버 같은 자식까지 함께 끈다
            proc = subprocess.Popen(argv, cwd=self.cwd, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            self._procs[job_id] = proc
            job = self._save({**job, "status": "running", "started": time.time(), "argv": argv, "env": job_env(action, clean)})
        threading.Thread(target=self._wait, args=(job_id, proc, log), daemon=True).start()
        return job

    def _wait(self, job_id: str, proc: subprocess.Popen, log) -> None:
        code = proc.wait()
        log.close()
        with self._lock:
            job = self.get(job_id)
            if job["status"] == "running":
                job.update(status="done" if code == 0 else "failed", returncode=code, ended=time.time())
                self._save(job)
            self._procs.pop(job_id, None)

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self.get(job_id)
            if job["status"] == "pending":
                return self._save({**job, "status": "cancelled", "ended": time.time()})
            if job["status"] == "running" and job_id in self._procs:
                os.killpg(self._procs[job_id].pid, signal.SIGTERM)
                return self._save({**job, "status": "cancelled", "ended": time.time()})
            raise ActionError(f"취소할 수 없는 상태다({job['status']})")
