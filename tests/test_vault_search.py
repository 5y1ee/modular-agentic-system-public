"""tools/vault_search.py 단위 테스트. qmd를 실제로 부르지 않고 가짜 runner를 넣는다."""
import json
import subprocess

import pytest

from modular_agentic.tools.vault_search import (
    LOG_ENV,
    QMD_URL_ENV,
    VaultSearchError,
    clean_http_snippet,
    current_backend,
    parse_http_hits,
    search,
)


@pytest.fixture(autouse=True)
def _no_http_server_by_default(monkeypatch):
    """테스트가 이 기기에 떠 있는 실제 qmd 서버(localhost:8181)에 영향받지 않게, 기본은 서버 없음으로 둔다."""
    monkeypatch.delenv("MAS_QMD_URL", raising=False)
    monkeypatch.setattr("modular_agentic.tools.vault_search.http_available", lambda url: False)


def raw(file: str, snippet: str = "@@ -1,2 @@ (0 before, 1 after)\n\n본문", score: float = 0.6) -> dict:
    return {
        "docid": "#abc",
        "score": score,
        "file": f"qmd://vault/{file}",
        "line": 7,
        "title": "제목",
        "snippet": snippet,
    }


def fake_runner(stdout: str, returncode: int = 0, stderr: str = ""):
    def runner(cmd, env):
        runner.cmd = cmd
        runner.env = env
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return runner


def test_parses_json_and_cleans_snippet():
    runner = fake_runner(json.dumps([raw("04_project/FINT.md")]))

    hits = search("q", runner=runner)

    assert hits[0].path == "04_project/FINT.md"  # qmd://vault/ 접두사가 떨어짐
    assert hits[0].snippet == "본문"  # @@ 머리말이 떨어짐
    assert hits[0].to_evidence().ref == "04_project/FINT.md:7"


def test_reads_json_after_progress_lines():
    noisy = "├─ q\n└─ vec: q\nSearching 1 vector queries...\n" + json.dumps([raw("a.md")], indent=2)

    hits = search("q", runner=fake_runner(noisy))

    assert [h.path for h in hits] == ["a.md"]


def test_excludes_prefixes_and_fills_up_to_n():
    items = [raw("06_career/자소서/x.md"), raw("00_raw/y.md"), raw("04_project/a.md"), raw("04_project/b.md")]

    hits = search("q", n=2, exclude_prefixes=("06_career/자소서/", "00_raw/"), runner=fake_runner(json.dumps(items)))

    assert [h.path for h in hits] == ["04_project/a.md", "04_project/b.md"]


def test_fetches_more_than_n(monkeypatch):
    monkeypatch.delenv("QMD_FORCE_CPU", raising=False)
    runner = fake_runner("[]")

    search("q", n=5, runner=runner)

    assert runner.cmd[:3] == ["qmd", "vsearch", "q"]
    assert int(runner.cmd[runner.cmd.index("-n") + 1]) >= 20  # 제외 후에도 채우려고 넉넉히 받는다


def test_does_not_force_cpu_by_default_but_can(monkeypatch):
    monkeypatch.delenv("QMD_FORCE_CPU", raising=False)
    runner = fake_runner("[]")

    search("q", runner=runner)
    assert "QMD_FORCE_CPU" not in runner.env  # GPU가 있는 기기에서 CPU를 강제하지 않는다

    search("q", force_cpu=True, runner=runner)
    assert runner.env["QMD_FORCE_CPU"] == "1"


def test_log_keeps_all_hits_before_exclusion(monkeypatch, tmp_path):
    import json as _json

    log_file = tmp_path / "logs" / "s.jsonl"
    monkeypatch.setenv(LOG_ENV, str(log_file))
    items = [raw("06_career/자소서/x.md"), raw("04_project/a.md")]

    hits = search("질의", n=1, exclude_prefixes=("06_career/자소서/",), tag="t1", runner=fake_runner(_json.dumps(items)))

    assert [h.path for h in hits] == ["04_project/a.md"]  # 반환값은 제외 적용
    record = _json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
    assert record["query"] == "질의" and record["tag"] == "t1"
    assert [h["path"] for h in record["hits"]] == ["06_career/자소서/x.md", "04_project/a.md"]  # 로그는 전체
    assert record["hits"][0]["snippet"] == "본문" and record["hits"][0]["rank"] == 1
    assert "elapsed_sec" in record and "machine" in record


def test_no_log_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv(LOG_ENV, raising=False)
    search("q", runner=fake_runner("[]"))
    assert not list(tmp_path.iterdir())


def test_nonzero_exit_raises():
    with pytest.raises(VaultSearchError):
        search("q", runner=fake_runner("", returncode=1, stderr="boom"))


def test_no_json_raises():
    with pytest.raises(VaultSearchError):
        search("q", runner=fake_runner("그냥 텍스트만 있음"))


# ---- HTTP(상시 서버) 방식 ----

MODULE = "modular_agentic.tools.vault_search"


def http_payload(*files: str) -> dict:
    return {
        "results": [
            {
                "docid": "#a",
                "file": f"qmd://vault/{f}",
                "title": "t",
                "score": 0.5,
                "line": 7,
                "snippet": "7: @@ -6,4 @@ (5 before, 70 after)\n8: \n9: 본문 한 줄",
            }
            for f in files
        ]
    }


def test_clean_http_snippet_strips_line_numbers_and_header():
    raw_snippet = "7: @@ -6,4 @@ (5 before, 70 after)\n8: \n9: 본문 한 줄\n10: 다음 줄"

    assert clean_http_snippet(raw_snippet) == "본문 한 줄\n다음 줄"


def test_parse_http_hits_decodes_percent_encoded_path():
    payload = http_payload("06_career/%EC%9E%90%EC%86%8C%EC%84%9C/a.md")

    [hit] = parse_http_hits(payload)

    assert hit.path == "06_career/자소서/a.md"
    assert hit.snippet == "본문 한 줄"
    assert (hit.line, hit.score) == (7, 0.5)


def test_search_http_sends_vec_query_filters_and_logs(monkeypatch, tmp_path):
    import json as _json

    log_file = tmp_path / "s.jsonl"
    monkeypatch.setenv(LOG_ENV, str(log_file))
    sent = {}

    def fake_post(url, payload):
        sent.update(url=url, payload=payload)
        return http_payload("06_career/자소서/x.md", "04_project/a.md")

    hits = search(
        "질의",
        n=1,
        exclude_prefixes=("06_career/자소서/",),
        backend="http",
        base_url="http://localhost:8181/",
        http_post=fake_post,
        tag="t1",
    )

    assert [h.path for h in hits] == ["04_project/a.md"]  # 반환값은 제외 적용
    assert sent["url"] == "http://localhost:8181/query"  # 끝의 / 정리
    assert sent["payload"]["searches"] == [{"type": "vec", "query": "질의"}]
    assert sent["payload"]["limit"] >= 20 and sent["payload"]["rerank"] is False
    record = _json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
    assert record["method"] == "http-vec" and record["tag"] == "t1"
    assert [h["path"] for h in record["hits"]] == ["06_career/자소서/x.md", "04_project/a.md"]  # 로그는 전체


def test_search_http_rerank_changes_payload_and_log_method(monkeypatch, tmp_path):
    import json as _json

    monkeypatch.setenv(LOG_ENV, str(tmp_path / "s.jsonl"))
    sent = {}
    search(
        "q", backend="http", base_url="http://localhost:8181", rerank=True,
        http_post=lambda url, payload: sent.update(payload=payload) or http_payload("a.md"),
    )
    assert sent["payload"]["rerank"] is True
    assert _json.loads((tmp_path / "s.jsonl").read_text(encoding="utf-8"))["method"] == "http-vec-rerank"


def test_auto_backend_uses_http_only_when_server_responds(monkeypatch):
    monkeypatch.setenv(QMD_URL_ENV, "http://localhost:8181")
    runner = fake_runner(json.dumps([raw("cli.md")]))
    post = lambda url, payload: http_payload("http.md")  # noqa: E731

    monkeypatch.setattr(f"{MODULE}.http_available", lambda url: True)
    assert [h.path for h in search("q", runner=runner, http_post=post)] == ["http.md"]

    monkeypatch.setattr(f"{MODULE}.http_available", lambda url: False)  # 서버가 죽어 있으면 CLI로 돌아간다
    assert [h.path for h in search("q", runner=runner, http_post=post)] == ["cli.md"]


def test_auto_backend_without_url_uses_cli():
    runner = fake_runner(json.dumps([raw("cli.md")]))

    assert [h.path for h in search("q", runner=runner)] == ["cli.md"]  # 서버가 없으면 CLI


def test_auto_backend_checks_default_url_when_env_missing(monkeypatch):
    checked = []
    monkeypatch.setattr(f"{MODULE}.http_available", lambda url: checked.append(url) or True)

    hits = search("q", http_post=lambda url, payload: http_payload("http.md"))

    assert checked == ["http://localhost:8181"]  # 환경변수가 없어도 기본 주소를 확인한다
    assert [h.path for h in hits] == ["http.md"]


def test_http_failures_become_vault_search_error():
    def failing_post(url, payload):
        raise OSError("연결 거부")

    with pytest.raises(VaultSearchError):
        search("q", backend="http", base_url="http://localhost:8181", http_post=failing_post)
    with pytest.raises(VaultSearchError):
        search("q", backend="http", base_url="http://localhost:8181", http_post=lambda u, p: (_ for _ in ()).throw(ValueError("깨진 JSON")))


def test_bad_backend_name_raises():
    with pytest.raises(ValueError):
        search("q", backend="grpc")


def test_current_backend_description(monkeypatch):
    assert current_backend() == "cli"  # 서버 없음(기본 fixture)
    monkeypatch.setattr(f"{MODULE}.http_available", lambda url: True)
    assert current_backend() == "http http://localhost:8181"  # 기본 주소
    monkeypatch.setenv(QMD_URL_ENV, "http://example.test:9000")
    assert current_backend() == "http http://example.test:9000"  # 환경변수가 우선
