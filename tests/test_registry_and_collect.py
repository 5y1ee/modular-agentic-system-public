"""구현체 목록(registry)과 수집 구현체 3종, Strands 없는 분석 구현체. 가짜 클라이언트·가짜 에이전트만 쓴다(AWS·Tavily 호출 없음)."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from modular_agentic import registry
from modular_agentic.core.types import Item
from modular_agentic.engines.basic.bedrock_analyzer import TOOL_NAME, BedrockAnalyzer
from modular_agentic.engines.basic.collectors import InthisworkCollector, PasteCollector
from modular_agentic.tools.postings import collected_titles, save_posting

ROOT = Path(__file__).resolve().parent.parent


# ---- 구현체 목록 ----

def test_choose_order_name_then_env_then_first(monkeypatch):
    monkeypatch.delenv("MAS_IMPL_ANALYZER", raising=False)
    assert registry.choose("analyzer").name == "strands"
    monkeypatch.setenv("MAS_IMPL_ANALYZER", "bedrock")
    assert registry.choose("analyzer").name == "bedrock"
    assert registry.choose("analyzer", "strands").name == "strands"
    with pytest.raises(KeyError, match="있는 것: strands, bedrock"):
        registry.choose("analyzer", "quick")
    with pytest.raises(KeyError, match="모르는 단계"):
        registry.choose("collector2")


def test_every_impl_target_exists_and_describe_does_not_import():
    pytest.importorskip("strands")
    import importlib

    for impls in registry.IMPLS.values():
        for impl in impls:
            module, cls = impl.target.split(":")
            assert hasattr(importlib.import_module(module), cls), impl.target
    described = registry.describe()
    assert [i["name"] for i in described["collector"]] == ["inthiswork", "web_agent", "paste"]
    assert {i["axis"] for i in described["analyzer"]} == {"기본", "엔진"}


def test_make_passes_constructor_args():
    checker = registry.make("collector", "paste", title="가나 백엔드", text="자격요건: Java")
    assert isinstance(checker, PasteCollector)


# ---- 수집 ----

def write_crawler_day(folder: Path, day: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    posts = [{"id": "1", "company": "가나", "role": "백엔드", "title": "가나｜백엔드", "link": "https://example.com/1",
              "content": "자격요건: Java 3년", "images": "[]"},
             {"id": "2", "company": "다라", "role": "AI", "title": "", "link": "https://example.com/2",
              "content": "", "images": "['https://example.com/a.png']"}]
    (folder / f"{day}.json").write_text(json.dumps(posts, ensure_ascii=False), encoding="utf-8")


def test_inthiswork_collector_reads_day_and_keeps_image_only_body_empty(tmp_path):
    write_crawler_day(tmp_path, "2026-10-20")
    items = InthisworkCollector(day="2026-10-20", crawler_dir=tmp_path).collect()
    assert [i.id for i in items] == ["inthiswork_1", "inthiswork_2"]
    assert items[0].title == "가나｜백엔드" and items[0].meta["link"] == "https://example.com/1"
    assert items[1].body == "" and items[1].title == "다라 AI" and items[1].meta["images"] == "https://example.com/a.png"
    with pytest.raises(FileNotFoundError, match="크롤러 결과가 없다"):
        InthisworkCollector(day="2026-01-01", crawler_dir=tmp_path).collect()


def test_paste_collector_id_is_stable_and_needs_title_and_text():
    a = PasteCollector(title="가나 백엔드", text="본문").collect()[0]
    assert a.id == PasteCollector(title="다른 제목", text="본문").collect()[0].id and a.id.startswith("paste_")
    with pytest.raises(ValueError):
        PasteCollector(title="", text="본문").collect()


def test_save_posting_writes_title_first_and_never_overwrites(tmp_path):
    item = Item(id="inthiswork_1", source="inthiswork", title="가나 백엔드", body="자격요건: Java",
                meta={"company": "가나", "link": "https://example.com/1", "images": "x.png"})
    saved = save_posting(item, tmp_path)
    text = saved.path.read_text(encoding="utf-8")
    assert saved.created and text.splitlines()[0] == "가나 백엔드" and "출처: https://example.com/1" in text and "자격요건: Java" in text
    saved.path.write_text("사람이 고친 전사본\n", encoding="utf-8")
    assert not save_posting(item, tmp_path).created and saved.path.read_text(encoding="utf-8") == "사람이 고친 전사본\n"
    assert collected_titles(tmp_path) == {"inthiswork_1": "사람이 고친 전사본"}
    with pytest.raises(ValueError):
        save_posting(Item(id="../x", source="", title="t", body="b"), tmp_path)


# ---- Strands 없는 분석(엔진 축) ----

class FakeBedrock:
    def __init__(self, content):
        self.content, self.calls = content, []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"content": self.content}}, "stopReason": "tool_use"}


def test_bedrock_analyzer_reads_forced_tool_input():
    client = FakeBedrock([{"toolUse": {"name": TOOL_NAME, "input": {"requirements": [
        {"text": " Java 3년 ", "kind": "자격요건"}, {"text": "", "kind": ""}, {"text": "AWS 경험", "kind": "우대사항"}]}}}])
    reqs = BedrockAnalyzer(client=client).analyze(Item(id="p", source="file", title="t", body="자격요건: Java 3년"))
    assert [(r.id, r.text, r.kind) for r in reqs] == [("p-r1", "Java 3년", "자격요건"), ("p-r2", "AWS 경험", "우대사항")]
    call = client.calls[0]
    assert call["toolConfig"]["toolChoice"] == {"tool": {"name": TOOL_NAME}} and call["inferenceConfig"]["temperature"] == 0.0


def test_bedrock_analyzer_empty_body_and_missing_tool():
    client = FakeBedrock([{"text": "죄송합니다"}])
    assert BedrockAnalyzer(client=client).analyze(Item(id="p", source="", title="t", body=" ")) == [] and not client.calls
    with pytest.raises(RuntimeError, match="부르지 않았다"):
        BedrockAnalyzer(client=client).analyze(Item(id="p", source="", title="t", body="본문"))


# ---- 웹 검색 에이전트(목적 축) ----

def test_web_collector_keeps_only_read_pages_with_real_quotes(tmp_path, monkeypatch):
    pytest.importorskip("strands")
    from modular_agentic.engines.strands.schemas import PostingCandidateOut, PostingCandidatesOut
    from modular_agentic.engines.strands.web_collector import StrandsWebCollector

    monkeypatch.chdir(tmp_path)
    pages = {"https://jobs.example/1": "가나 채용 자격요건: Java 백엔드 개발 경험 3년 이상", "https://jobs.example/2": "다라 채용 우대: AWS"}
    search = SimpleNamespace(search=lambda q, max_results=6: [SimpleNamespace(title="가나", url="https://jobs.example/1", snippet="s", published="")])
    fetch = lambda url: SimpleNamespace(text=pages.get(url, ""), reason="" if url in pages else "HTTP 404")

    def factory(tools):
        t = {tool.tool_name: tool for tool in tools}

        def agent(prompt, structured_output_model):
            assert "가나" in t["web_search"]("가나 백엔드 채용")
            t["fetch_page"]("https://jobs.example/1")
            t["fetch_page"]("https://jobs.example/2")
            assert t["fetch_page"]("https://jobs.example/404").startswith("못 읽음")
            assert t["already_collected"]("가나") == "[]"
            out = PostingCandidatesOut(candidates=[
                PostingCandidateOut(url="https://jobs.example/1", company="가나", role="백엔드", quote="Java 백엔드 개발 경험 3년 이상", reason="r"),
                PostingCandidateOut(url="https://jobs.example/2", company="다라", role="AI", quote="Python 필수 우대", reason="r"),
                PostingCandidateOut(url="https://jobs.example/9", company="마바", role="FE", quote="지어낸 공고 문장입니다", reason="r")])
            return SimpleNamespace(structured_output=out)
        return agent

    collector = StrandsWebCollector(goal="백엔드 신입", search=search, fetch=fetch, agent_factory=factory)
    items = collector.collect()
    assert [i.meta["company"] for i in items] == ["가나"] and items[0].body == pages["https://jobs.example/1"]
    assert [reason[:10] for _, reason in collector.rejected] == ["인용이 페이지에 없다"[:10], "이번 실행에서 읽지 않은 주소다"[:10]]


# ---- 수집 스크립트 ----

def test_collect_script_search_then_import(tmp_path):
    crawler = tmp_path / "crawler"
    write_crawler_day(crawler, "2026-10-20")
    env = {**__import__("os").environ, "MAS_CRAWLER_DIR": str(crawler)}
    script = str(ROOT / "scripts" / "collect_postings.py")
    run = lambda *a: subprocess.run([sys.executable, script, *a], cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8")
    result = run("search", "--impl", "inthiswork", "--day", "2026-10-20")
    assert result.returncode == 0, result.stderr
    candidates = next((tmp_path / "logs" / "00_collect").glob("*-inthiswork.json"))
    md = candidates.with_suffix(".md").read_text(encoding="utf-8")
    assert "## 1. 가나｜백엔드" in md and "본문 없음(이미지뿐)" in md
    assert run("import", str(candidates), "--pick", "3").returncode != 0
    assert run("import", str(candidates), "--pick", "1").returncode == 0
    assert (tmp_path / "inputs" / "00_collect" / "inthiswork_1.txt").read_text(encoding="utf-8").startswith("가나｜백엔드\n")
    result = run("search", "--impl", "paste", "--title", "사바 백엔드", "--text", "자격요건: Go")
    assert result.returncode == 0 and "저장" in result.stdout
