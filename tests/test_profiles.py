"""업무 설정(instances/profile.py): 자소서 프롬프트가 예전과 같은지, 제안서로 바꾸면 분석 구현 두 개가 같이 바뀌는지, 콘솔이 업무별 단계를 지키는지."""
from types import SimpleNamespace

import pytest

from modular_agentic.core.types import Item
from modular_agentic.engines.basic.bedrock_analyzer import BedrockAnalyzer
from modular_agentic.engines.basic.collectors import PasteCollector
from modular_agentic.instances.profile import profile, profile_of_text
from modular_agentic.tools.postings import posting_text

# 2026-10-21 이전 StrandsAnalyzer의 SYSTEM_PROMPT 그대로(기존 결과·실험 기록과 같은 프롬프트여야 한다)
OLD_COVER_LETTER_PROMPT = """너는 채용공고 본문에서 지원자에게 요구되는 항목을 하나씩 뽑는 역할이다.

규칙
- 본문에 실제로 있는 요구만 뽑는다. 없는 요구를 추가하지 않는다.
- 항목 하나에는 요구 하나만 담는다. 여러 요구가 한 줄에 묶여 있으면 의미를 바꾸지 않고 나눈다.
- 원문 표현을 최대한 유지한다. 요약하거나 말을 바꾸지 않는다.
- 지원자의 경험·역량·기술로 대답할 수 있는 항목만 뽑는다. 근무지, 급여, 복지, 전형 절차, 마감일은 제외한다.
- kind에는 공고가 붙인 분류(자격요건, 우대사항, 담당업무 등)를 쓴다. 분류가 없으면 빈 문자열이다.
- 요구가 하나도 없으면 빈 목록을 돌려준다."""


def test_profile_lookup_and_reading_task_line():
    assert profile().name == "cover_letter" and profile("제안서").name == profile("proposal").name == "proposal"
    with pytest.raises(KeyError, match="모르는 업무"):
        profile("포트폴리오")
    assert profile_of_text("제목\n업무: 제안서\n\n본문").name == "proposal"
    assert profile_of_text("제목\n회사: 가나\n\n업무: 제안서").name == "proposal"  # 앞 8줄 안이면 읽는다
    assert profile_of_text("백엔드 채용\n업무: 서버 개발\n자격: Java").name == "cover_letter"  # 업무 이름이 아니면 본문으로 본다
    assert profile_of_text("제목\n\n본문만").name == "cover_letter"
    assert profile_of_text("업무: 제안서\n두 번째 줄").name == "cover_letter"  # 첫 줄은 제목이라 보지 않는다


def test_strands_cover_letter_prompt_unchanged_and_proposal_prompt_differs():
    pytest.importorskip("strands")
    from modular_agentic.engines.strands.analyzer import SYSTEM_PROMPT, StrandsAnalyzer, system_prompt

    assert SYSTEM_PROMPT == system_prompt(profile()) == OLD_COVER_LETTER_PROMPT
    proposal = system_prompt(profile("proposal"))
    assert "제안요청서(RFP)" in proposal and "지원자" not in proposal

    seen = []

    def factory(tools):
        def agent(prompt, structured_output_model):
            seen.append(prompt)
            return SimpleNamespace(structured_output=structured_output_model(requirements=[]))
        return agent

    StrandsAnalyzer(profile=profile("proposal"), agent_factory=factory).analyze(Item(id="r", source="paste", title="RFP", body="본문"))
    StrandsAnalyzer(agent_factory=factory).analyze(Item(id="p", source="file", title="공고", body="본문"))
    assert seen[0].startswith("제안요청서 제목: RFP") and seen[1].startswith("채용공고 제목: 공고")


def test_bedrock_analyzer_uses_profile():
    calls = []
    client = SimpleNamespace(converse=lambda **kw: calls.append(kw) or {"output": {"message": {"content": [
        {"toolUse": {"name": "submit_requirements", "input": {"requirements": []}}}]}}})
    BedrockAnalyzer(profile=profile("proposal"), client=client).analyze(Item(id="r", source="", title="RFP", body="본문"))
    assert "제안요청서(RFP)" in calls[0]["system"][0]["text"]
    assert calls[0]["messages"][0]["content"][0]["text"].startswith("제안요청서 제목:")
    assert "지원자" in BedrockAnalyzer().system_prompt()


def test_paste_with_task_round_trips_through_posting_file():
    item = PasteCollector(title="발주기관 AI 사업 RFP", text="기능요구 6 AI Agent 권한", instance="proposal").collect()[0]
    text = posting_text(item)
    assert "업무: 제안서" in text and profile_of_text(text).name == "proposal"
    assert profile_of_text(posting_text(PasteCollector(title="가나 백엔드", text="Java").collect()[0])).name == "cover_letter"


def test_console_limits_stages_by_task(tmp_path, monkeypatch):
    from modular_agentic.services.console import actions

    monkeypatch.chdir(tmp_path)
    collect = tmp_path / "inputs" / "00_collect"
    collect.mkdir(parents=True)
    (collect / "rfp_demo.txt").write_text("발주기관 RFP\n업무: 제안서\n\n기능요구 6", encoding="utf-8")
    (collect / "job.txt").write_text("가나 백엔드\n\n자격요건: Java", encoding="utf-8")
    items = {i["id"]: i for i in actions.list_items()}
    assert items["rfp_demo"]["task"] == "제안서" and items["rfp_demo"]["stages"] == ["collect", "analyze"]
    assert items["job"]["task"] == "자소서"
    actions.validate("analyze.run", "rfp_demo", {"impl": {"analyzer": "bedrock"}})
    for key in ("map.run", "research.approve", "plan.approve"):
        with pytest.raises(actions.ActionError, match="제안서 업무는 아직"):
            actions.validate(key, "rfp_demo", {"variant": "x"} if key.startswith("plan") else {})
    actions.validate("map.run", "job", {})
    _, clean = actions.validate("collect.search", actions.COLLECT_ITEM, {"impl": {"collector": "paste"}, "title": "t", "text": "b", "instance": "제안서"})
    assert clean["instance"] == "proposal"
    with pytest.raises(actions.ActionError, match="모르는 업무"):
        actions.validate("collect.search", actions.COLLECT_ITEM, {"impl": {"collector": "paste"}, "title": "t", "text": "b", "instance": "x"})


def test_showcase_task_marker_and_collectors_per_task(tmp_path, monkeypatch):
    from modular_agentic.services.console import actions

    monkeypatch.chdir(tmp_path)
    (tmp_path / "inputs" / "00_collect").mkdir(parents=True)
    for name, marker in (("proposal_demo", "제안서\n"), ("old_demo", None)):
        folder = actions.SHOWCASE_DIR / name
        folder.mkdir(parents=True)
        if marker:
            (folder / actions.SHOWCASE_TASK_FILE).write_text(marker, encoding="utf-8")
    tasks = {i["id"]: i.get("task") for i in actions.list_items()}
    assert tasks["proposal_demo"] == "제안서" and tasks["old_demo"] == "자소서"
    assert [t["collectors"] for t in actions.task_choices()] == [["inthiswork", "web_agent", "paste"], ["paste"]]
    with pytest.raises(actions.ActionError, match="제안서 업무에는 이 수집 구현을 쓰지 않는다"):
        actions.validate("collect.search", actions.COLLECT_ITEM, {"impl": {"collector": "inthiswork"}, "instance": "proposal"})
    actions.validate("collect.search", actions.COLLECT_ITEM, {"impl": {"collector": "inthiswork"}, "instance": "cover_letter"})
