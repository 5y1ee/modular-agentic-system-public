"""파이프라인 콘솔: 허용 목록·인자 검사·경로 제한·작업 흐름·서버 API·채팅 도구. AWS·qmd를 부르지 않는다."""
import json
import sys
import time
from pathlib import Path

import pytest

from modular_agentic.services.console import actions
from modular_agentic.services.console.actions import ActionError, build_argv, list_results, read_result, validate
from modular_agentic.services.console.jobs import JobRunner

ITEM = "acme_2026_backend"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """레포 루트처럼 생긴 임시 폴더로 옮긴다(단계 폴더는 상대 경로라서)."""
    monkeypatch.chdir(tmp_path)
    collect = tmp_path / "inputs" / "00_collect"
    collect.mkdir(parents=True)
    (collect / f"{ITEM}.txt").write_text("ACME 백엔드 공고\n", encoding="utf-8")
    (collect / f"{ITEM}.questions.txt").write_text("문항 1\n", encoding="utf-8")
    for folder in actions.STAGE_DIRS.values():
        folder.mkdir(parents=True)
    return tmp_path


def wait_until(runner, job_id, statuses, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        job = runner.get(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"작업이 {statuses}가 되지 않음: {runner.get(job_id)}")


# ---- 인자 검사와 argv ----

def test_unknown_action_param_and_item_are_rejected(repo):
    with pytest.raises(ActionError, match="모르는 작업"):
        validate("shell.run", ITEM, {})
    with pytest.raises(ActionError, match="모르는 인자"):
        validate("map.run", ITEM, {"cmd": "rm -rf /"})
    with pytest.raises(ActionError, match="실행할 수 있는 공고"):
        validate("map.run", "../etc", {})
    with pytest.raises(ActionError, match="실행할 수 있는 공고"):
        validate("map.run", "no_such_posting", {})


def test_param_formats(repo):
    with pytest.raises(ActionError, match="양의 정수"):
        validate("write.run", ITEM, {"variant": "가치A", "number": "two"})
    with pytest.raises(ActionError, match="번호를 쉼표로"):
        validate("analyze.mark", ITEM, {"basic": "1;rm"})
    with pytest.raises(ActionError, match="'-'로 시작"):
        validate("plan.make", ITEM, {"variant": "--refresh"})
    with pytest.raises(ActionError, match="소재 계획 이름\\+문항 번호"):
        validate("write.run", ITEM, {"guidance": "짧게"})
    _, clean = validate("analyze.mark", ITEM, {"basic": ""})  # 비우기(표시 해제)는 허용
    assert clean == {"basic": ""}


def test_draft_must_be_json_in_write_folder(repo):
    with pytest.raises(ActionError, match="05_write 안의 JSON"):
        validate("check.run", ITEM, {"draft": "/etc/passwd", "number": 1})
    draft = actions.STAGE_DIRS["write"] / f"20261020-0000-{ITEM}.json"
    draft.write_text("{}", encoding="utf-8")
    _, clean = validate("check.run", ITEM, {"draft": str(draft), "number": 2})
    assert build_argv(actions.ACTIONS["check.run"], ITEM, clean, python="py")[-4:] == ["--draft", str(draft), "--number", "2"]


def test_build_argv_wraps_qmd_and_keeps_free_text_as_one_argument(repo):
    action, clean = validate("write.run", ITEM, {"variant": "가치 A", "number": "2", "guidance": "첫 문장을 바꿔라; echo hi"})
    argv = build_argv(action, ITEM, clean, python="py")
    assert argv[:3] == [actions.QMD_WRAPPER, "py", "scripts/run_writer_demo.py"]
    assert argv[argv.index("--plan") + 1] == "가치 A" and argv[argv.index("--guidance") + 1] == "첫 문장을 바꿔라; echo hi"
    action, clean = validate("research.run", ITEM, {"url": "https://a.example\n\nhttps://b.example", "query": "ACME 채용"})
    argv = build_argv(action, ITEM, clean, python="py")
    assert argv[0] == "py" and argv.count("--url") == 2 and argv[-2:] == ["--query", "ACME 채용"]


def test_every_action_builds_argv(repo):
    """ACTIONS에 넣고 build_argv에 분기를 빠뜨리지 않았는지."""
    draft = actions.STAGE_DIRS["write"] / f"x-{ITEM}.json"
    draft.write_text("{}", encoding="utf-8")
    candidates = actions.STAGE_DIRS["collect"] / "20261021-000000-inthiswork.json"
    candidates.write_text("{}", encoding="utf-8")
    samples = {"text": "a", "longtext": "본문", "int": 1, "bool": True, "numbers": "1,2", "ids": "c1", "date": "2026-10-20",
               "draft": str(draft), "candidates": str(candidates), "choice": "cover_letter"}
    for action in actions.ACTIONS.values():
        params = {p.name: samples[p.kind] for p in action.params if p.name != "question" and not p.impl}
        item = actions.COLLECT_ITEM if action.stage == "collect" else ITEM
        checked, clean = validate(action.key, item, params)
        assert build_argv(checked, item, clean, python="py")[0] in ("py", actions.QMD_WRAPPER)


# ---- 결과 문서 ----

def test_results_by_stage_and_path_guard(repo):
    (actions.STAGE_DIRS["map"] / f"{ITEM}.md").write_text("# 매핑", encoding="utf-8")
    (actions.STAGE_DIRS["map"] / f"{ITEM}.json").write_text("{}", encoding="utf-8")  # 원본 JSON은 목록에 안 보인다
    (actions.STAGE_DIRS["map"] / "other_item.md").write_text("다른 공고", encoding="utf-8")
    results = list_results(ITEM)
    assert [d["name"] for d in results["map"]] == [f"{ITEM}.md"] and results["research"] == []
    assert read_result(results["map"][0]["path"]) == "# 매핑"
    (repo / "secret.md").write_text("비밀", encoding="utf-8")
    for bad in ("secret.md", "logs/03_map/../../secret.md", str(actions.STAGE_DIRS["map"] / f"{ITEM}.json")):
        with pytest.raises(ActionError):
            read_result(bad)


def test_showcase_is_view_only_and_sorted_by_prefix(repo):
    showcase = actions.SHOWCASE_DIR / "proposal_demo"
    showcase.mkdir(parents=True)
    (showcase / "02_요구.md").write_text("요구", encoding="utf-8")
    (showcase / "00_수집.md").write_text("수집", encoding="utf-8")
    (showcase / "05_초안.html").write_text("<p>초안</p>", encoding="utf-8")
    (showcase / "메모.md").write_text("번호 없음", encoding="utf-8")
    kinds = {i["id"]: i["kind"] for i in actions.list_items()}
    assert kinds == {actions.COLLECT_ITEM: "collect", ITEM: "run", "proposal_demo": "showcase"}
    results = list_results("proposal_demo")
    assert [d["name"] for d in results["analyze"]] == ["02_요구.md"] and [d["name"] for d in results["write"]] == ["05_초안.html"]
    assert [d["name"] for d in results["collect"]] == ["00_수집.md"]
    with pytest.raises(ActionError, match="실행할 수 있는 공고"):
        validate("map.run", "proposal_demo", {})


# ---- 작업 흐름 ----

def fake_builder(code):
    return lambda action, item_id, params: [sys.executable, "-c", code]


def test_job_waits_for_confirmation_then_runs(repo):
    runner = JobRunner(repo / "jobs", argv_builder=fake_builder("print('단계 실행됨')"))
    job = runner.create("analyze.run", ITEM, {}, source="chat")
    assert job["status"] == "pending" and job["source"] == "chat" and not (repo / "jobs" / f"{job['id']}.log").exists()
    runner.confirm(job["id"])
    done = wait_until(runner, job["id"], {"done", "failed"})
    assert done["status"] == "done" and done["returncode"] == 0
    assert "단계 실행됨" in runner.log_tail(job["id"])
    with pytest.raises(ActionError, match="확인 대기 중인 작업이 아니다"):
        runner.confirm(job["id"])


def test_one_job_at_a_time_and_cancel(repo):
    runner = JobRunner(repo / "jobs", argv_builder=fake_builder("import time; time.sleep(30)"))
    first, second = (runner.create("analyze.run", ITEM, {}) for _ in range(2))
    runner.confirm(first["id"])
    with pytest.raises(ActionError, match="다른 작업이 실행 중"):
        runner.confirm(second["id"])
    assert runner.cancel(second["id"])["status"] == "cancelled"
    assert runner.cancel(first["id"])["status"] == "cancelled"
    time.sleep(0.3)
    assert runner.get(first["id"])["status"] == "cancelled"  # 프로세스가 끝나도 취소 표시는 그대로


def test_failed_job_and_restart_marks_running_as_failed(repo):
    runner = JobRunner(repo / "jobs", argv_builder=fake_builder("raise SystemExit(3)"))
    job = runner.create("analyze.run", ITEM, {})
    runner.confirm(job["id"])
    assert wait_until(runner, job["id"], {"done", "failed"})["returncode"] == 3
    path = repo / "jobs" / f"{job['id']}.json"
    path.write_text(json.dumps({**runner.get(job["id"]), "status": "running"}), encoding="utf-8")
    assert JobRunner(repo / "jobs").get(job["id"])["status"] == "failed"


# ---- 서버 API ----

def test_server_api(repo):
    pytest.importorskip("starlette")
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from modular_agentic.services.console.server import create_app

    (actions.STAGE_DIRS["analyze"] / f"{ITEM}.md").write_text("# 요구", encoding="utf-8")
    runner = JobRunner(repo / "jobs", argv_builder=fake_builder("print(1)"))
    client = TestClient(create_app(runner, chat=lambda item, message: f"{item}:{message}"))
    assert "<title>" in client.get("/").text
    assert {a["key"] for a in client.get("/api/actions").json()} == set(actions.ACTIONS)
    assert [i["id"] for i in client.get("/api/items").json()] == [actions.COLLECT_ITEM, ITEM]
    assert "bedrock" in [i["name"] for i in client.get("/api/impls").json()["analyzer"]]
    docs = client.get(f"/api/items/{ITEM}/results").json()["results"]["analyze"]
    assert client.get("/api/file", params={"path": docs[0]["path"]}).text == "# 요구"
    assert client.get("/api/file", params={"path": "/etc/passwd"}).status_code == 400
    job = client.post("/api/jobs", json={"action": "analyze.run", "item": ITEM, "params": {}}).json()
    assert job["status"] == "pending" and job["source"] == "button"
    assert client.post("/api/jobs", json={"action": "x", "item": ITEM}).json()["error"].startswith("모르는 작업")
    assert client.post(f"/api/jobs/{job['id']}/confirm").json()["status"] == "running"
    wait_until(runner, job["id"], {"done"})
    assert client.post("/api/chat", json={"item": ITEM, "message": "안녕"}).json() == {"reply": f"{ITEM}:안녕"}
    assert client.post("/api/chat", json={}).status_code == 200
    assert TestClient(create_app(runner)).post("/api/chat", json={}).status_code == 400  # 채팅이 꺼져 있으면


# ---- 채팅 ----

def test_chat_tools_only_create_pending_jobs(repo):
    pytest.importorskip("strands")
    from modular_agentic.engines.strands.console_agent import ConsoleChat, make_tools

    (actions.STAGE_DIRS["write"] / f"20261020-{ITEM}.md").write_text("# 초안\n" + "가" * 20000, encoding="utf-8")
    runner = JobRunner(repo / "jobs")
    tools = {t.tool_name: t for t in make_tools(runner, ITEM)}
    listing = json.loads(tools["list_documents"]())
    assert listing["results"]["write"] == [str(actions.STAGE_DIRS["write"] / f"20261020-{ITEM}.md")]
    assert "[잘림:" in tools["read_document"](listing["results"]["write"][0])
    assert tools["read_document"]("/etc/passwd").startswith("오류")
    reply = tools["request_action"]("write.run", json.dumps({"variant": "가치A", "number": 2}))
    assert "확인 대기" in reply
    jobs = runner.list()
    assert len(jobs) == 1 and jobs[0]["status"] == "pending" and jobs[0]["source"] == "chat"
    assert tools["request_action"]("shell.run", "{}").startswith("오류")
    assert tools["request_action"]("map.run", "[1]").startswith("오류")

    made = []
    chat = ConsoleChat(runner, agent_factory=lambda item_id: made.append(item_id) or (lambda message: f" 답:{message} "))
    assert chat(ITEM, "a") == "답:a" and chat(ITEM, "b") == "답:b" and made == [ITEM]  # 공고마다 에이전트 하나로 대화가 이어진다
    assert "실행 가능한 공고" in chat("proposal_demo", "a")


# ---- 구현 선택·모델(갈아 끼우기) ----

def test_impl_and_model_are_checked_and_passed_as_env(repo):
    action, clean = validate("analyze.run", ITEM, {"impl": {"analyzer": "bedrock"}, "model": "us.anthropic.claude-haiku-4-5"})
    assert actions.job_env(action, clean) == {"MAS_IMPL_ANALYZER": "bedrock", "MAS_MODEL_ID": "us.anthropic.claude-haiku-4-5"}
    assert "impl" not in " ".join(build_argv(action, ITEM, clean, python="py"))  # 스크립트 인자는 그대로, 고른 구현은 환경변수로
    with pytest.raises(ActionError, match="'quick' 구현이 없다"):
        validate("analyze.run", ITEM, {"impl": {"analyzer": "quick"}})
    with pytest.raises(ActionError, match="mapper 구현을 쓰지 않는다"):
        validate("analyze.run", ITEM, {"impl": {"mapper": "strands"}})
    with pytest.raises(ActionError, match="모델 ID"):
        validate("analyze.run", ITEM, {"model": "x; rm -rf /"})
    assert actions.job_env(*validate("analyze.run", ITEM, {})) == {}


def test_job_runner_passes_env_to_script(repo):
    runner = JobRunner(repo / "jobs", argv_builder=fake_builder("import os; print(os.environ.get('MAS_IMPL_ANALYZER'))"))
    job = runner.create("analyze.run", ITEM, {"impl": {"analyzer": "bedrock"}})
    runner.confirm(job["id"])
    wait_until(runner, job["id"], {"done"})
    assert "bedrock" in runner.log_tail(job["id"]) and runner.get(job["id"])["env"] == {"MAS_IMPL_ANALYZER": "bedrock"}


def test_collect_actions_use_collect_item_and_impl_specific_params(repo):
    with pytest.raises(ActionError, match="새 공고 수집"):
        validate("collect.search", ITEM, {})
    with pytest.raises(ActionError, match="실행할 수 있는 공고"):
        validate("analyze.run", actions.COLLECT_ITEM, {})
    action, clean = validate("collect.search", actions.COLLECT_ITEM,
                             {"impl": {"collector": "paste"}, "title": "가나 백엔드", "text": "자격요건: Java", "goal": "무시됨"})
    assert "goal" not in clean  # 고른 구현이 쓰지 않는 설정은 버린다
    argv = build_argv(action, actions.COLLECT_ITEM, clean, python="py")
    assert argv[1:5] == ["scripts/collect_postings.py", "search", "--impl", "paste"] and argv[argv.index("--text") + 1] == "자격요건: Java"
    with pytest.raises(ActionError, match="찾을 공고"):
        validate("collect.search", actions.COLLECT_ITEM, {"impl": {"collector": "web_agent"}})
    with pytest.raises(ActionError, match="YYYY-MM-DD"):
        validate("collect.search", actions.COLLECT_ITEM, {"impl": {"collector": "inthiswork"}, "day": "어제"})
    action, clean = validate("analyze.compare", ITEM, {})
    assert build_argv(action, ITEM, clean, python="py")[-2:] == ["--impls", "strands,bedrock"]
