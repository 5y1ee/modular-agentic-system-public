"""scripts/research_brief.py의 보기·삭제·승인 흐름과, 승인된 보고서만 작성에 쓰이는 것(common.load_approved_brief).

Bedrock을 부르는 run은 시험하지 않는다(사용자가 직접 실행). 저장 경로가 상대 경로(logs/01_research)라 임시 폴더로 옮겨 가서 돈다.
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("research_brief", SCRIPTS / "research_brief.py")
research_brief = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research_brief)

import common  # noqa: E402

from modular_agentic.core.brief_review import BriefRecord  # noqa: E402
from modular_agentic.core.types import Brief, Citation, Claim, SourceDoc, SourceKind  # noqa: E402

DOC = SourceDoc(id="d1", kind=SourceKind.WEB, locator="https://example.com", text="금융 IT 서비스를 제공한다.")
BRIEF = Brief(
    item_id="i1",
    subject="지원처 3.",
    claims=[
        Claim("c1", "금융 IT", "사업", [Citation("d1", "금융 IT 서비스를 제공한다.")]),
        Claim("c2", "틀린 주장", "사업", [Citation("d1", "금융 IT 서비스를 제공한다.")]),
    ],
)


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    research_brief.save(BriefRecord(brief=BRIEF, docs=[DOC]))


def test_unapproved_brief_is_not_used_for_writing(capsys):
    assert common.load_approved_brief("i1") is None
    assert "승인되지 않아" in capsys.readouterr().out
    assert common.load_approved_brief("없는공고") is None


def test_approve_then_writing_gets_the_brief(capsys):
    research_brief.cmd_approve(argparse.Namespace(item_id="i1"))
    brief = common.load_approved_brief("i1")
    assert brief is not None and [c.id for c in brief.claims] == ["c1", "c2"]


def test_drop_removes_the_claim_resets_approval_and_rewrites_the_markdown(capsys):
    research_brief.cmd_approve(argparse.Namespace(item_id="i1"))
    research_brief.cmd_drop(argparse.Namespace(item_id="i1", claim_ids=["c2"]))
    assert common.load_approved_brief("i1") is None  # 지운 뒤에는 다시 승인해야 한다
    md = research_brief.md_path("i1").read_text(encoding="utf-8")
    assert "[c1]" in md and "[c2]" not in md and "미승인" in md


def test_drop_with_unknown_id_exits_without_changing_anything():
    with pytest.raises(SystemExit):
        research_brief.cmd_drop(argparse.Namespace(item_id="i1", claim_ids=["c9"]))
    assert len(research_brief.load("i1").brief.claims) == 2


def test_show_and_missing_brief(capsys):
    research_brief.cmd_show(argparse.Namespace(item_id="i1"))
    assert "# 조사 보고서: 지원처 3." in capsys.readouterr().out
    with pytest.raises(SystemExit):
        research_brief.load("없는공고")


def test_reresearch_reuses_saved_docs_and_saves_under_a_new_name_without_touching_the_original(capsys, monkeypatch):
    from modular_agentic.core.types import ReadStatus

    posting = SourceDoc(id="d1", kind=SourceKind.POSTING, locator="공고 제목", text="공고 본문")
    web = SourceDoc(id="d2", kind=SourceKind.WEB, locator="https://example.com", text="금융 IT 서비스를 제공한다.", found_by="검색: 질의")
    research_brief.save(BriefRecord(brief=BRIEF, docs=[posting, web], approved=True))
    seen = {}

    class FakeResearcher:
        def research(self, item, docs):
            seen.update(item=item, docs=list(docs))
            return Brief(item_id=item.id, subject="지원처 3.", claims=BRIEF.claims[:1], warnings=['버린 주장: 이전과 다른 주장 — 인용: "원문과 다른 문장"'])

    monkeypatch.setattr(research_brief, "build_researcher", lambda: FakeResearcher())

    research_brief.cmd_reresearch(argparse.Namespace(item_id="i1", save_as=None, add=[], replace=False))

    assert seen["docs"] == [posting, web] and seen["item"].body == "공고 본문" and seen["item"].title == "공고 제목"
    assert seen["item"].meta == {"company": "지원처 3."}
    new = research_brief.load("i1_re")
    assert new.brief.item_id == "i1_re" and new.approved is False and new.docs == [posting, web]  # 자료는 그대로, 승인은 새로 받는다
    assert research_brief.load("i1").approved is True and len(research_brief.load("i1").brief.claims) == 2  # 원본은 그대로
    out = capsys.readouterr().out
    assert "웹 접근 없음" in out and '버린 주장: 이전과 다른 주장 — 인용: "원문과 다른 문장"' in out


def test_reresearch_refuses_to_overwrite_the_original_and_needs_a_saved_posting():
    with pytest.raises(SystemExit):
        research_brief.cmd_reresearch(argparse.Namespace(item_id="i1", save_as="i1", add=[], replace=False))
    with pytest.raises(SystemExit):  # 이 파일의 기본 보고서에는 공고 자료가 저장돼 있지 않다
        research_brief.cmd_reresearch(argparse.Namespace(item_id="i1", save_as=None, add=[], replace=False))


def _saved_with_posting():
    posting = SourceDoc(id="d1", kind=SourceKind.POSTING, locator="공고 제목", text="공고 본문")
    web = SourceDoc(id="d2", kind=SourceKind.WEB, locator="https://example.com", text="금융 IT 서비스를 제공한다.", found_by="검색: 질의")
    research_brief.save(BriefRecord(brief=BRIEF, docs=[posting, web], approved=True))
    return posting, web


def test_reresearch_add_appends_uploaded_files_after_the_saved_docs_without_searching(tmp_path, monkeypatch, capsys):
    posting, web = _saved_with_posting()
    extra = tmp_path / "talent_value.md"
    extra.write_text("Work With Grit — 집요하지만 빠르게 해결합니다.", encoding="utf-8")
    seen = {}

    class FakeResearcher:
        def research(self, item, docs):
            seen["docs"] = list(docs)
            return Brief(item_id=item.id, subject="지원처 3.", claims=BRIEF.claims[:1])

    monkeypatch.setattr(research_brief, "build_researcher", lambda: FakeResearcher())

    research_brief.cmd_reresearch(argparse.Namespace(item_id="i1", save_as=None, add=[extra, extra], replace=False))

    assert [d.id for d in seen["docs"]] == ["d1", "d2", "d3"] and seen["docs"][2].text.startswith("Work With Grit")
    assert seen["docs"][2].found_by == "" and seen["docs"][2].kind is SourceKind.UPLOAD  # 사용자가 준 자료는 표시가 없다
    assert "이미 있는 자료라 건너뜀" in capsys.readouterr().out  # 같은 파일을 두 번 줘도 한 번만 들어간다
    assert [d.id for d in research_brief.load("i1_re").docs] == ["d1", "d2", "d3"]


def test_reresearch_replace_keeps_the_name_backs_up_the_original_and_resets_approval(monkeypatch):
    _saved_with_posting()

    class FakeResearcher:
        def research(self, item, docs):
            return Brief(item_id=item.id, subject="지원처 3.", claims=BRIEF.claims[:1])

    monkeypatch.setattr(research_brief, "build_researcher", lambda: FakeResearcher())
    research_brief.cmd_reresearch(argparse.Namespace(item_id="i1", save_as=None, add=[], replace=True))

    assert len(research_brief.load("i1").brief.claims) == 1 and research_brief.load("i1").approved is False  # 새 보고서, 승인은 다시
    prev = research_brief.load("i1_prev")
    assert len(prev.brief.claims) == 2 and prev.brief.item_id == "i1_prev"  # 원본은 백업으로 남는다
    with pytest.raises(SystemExit):  # --replace와 --as를 같이 쓰면 거절
        research_brief.cmd_reresearch(argparse.Namespace(item_id="i1", save_as="x", add=[], replace=True))
