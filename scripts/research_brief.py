"""조사 보고서(Brief) 만들기와 확인 — 사용자 확인 지점.

공고와 사용자가 올린 자료·URL, 그리고 **시스템이 검색으로 찾은 URL**을 함께 읽어 출처가 붙은 보고서를 만든다.
사용자가 준 것이 항상 먼저이고, 검색으로 찾은 자료에는 보고서에 "검색: <질의>" 표시가 붙는다. 검색 키가 없으면 검색만 건너뛴다. 보고서는 자소서의 방향을 정하므로
**사용자가 읽고 승인한 것만** 작성 단계(feedback_loop.py, run_writer_demo.py)가 쓴다.

사용 순서
  0. (공고가 이미지뿐이면) scripts/transcribe_file.py로 공고 이미지를 텍스트로 옮기고 원본과 맞춰 본 뒤 그 텍스트 파일을 공고로 쓴다.
  1. 자료를 inputs/<공고 파일 이름>/ 폴더에 넣는다(.txt .md .pdf .png .jpg .webp 등). 로그인이 필요한 페이지는 캡처나 PDF로 저장해서 넣는다.
  2. run    자료를 모아 조사하고 logs/01_research/<id>.json(원본)과 .md(읽는 용)로 저장한다. 승인 전 상태다.
  3. show   보고서를 읽는다. 틀린 주장, 못 읽은 자료, 자료가 없어 못 다룬 주제를 본다.
  4. drop   틀린 주장을 id로 지운다(예: drop <id> c3 c5). 자료를 더 넣었으면 2번을 다시 돌린다. 지우거나 다시 돌리면 승인이 풀린다.
  (보조) reresearch 저장해 둔 자료로 조사 모델만 다시 돌린다. 웹 검색·페이지 가져오기는 하지 않는다(Bedrock 1회).
         --add 파일이나 폴더: 자료를 더해서 돌린다(예: 뒤늦게 받은 공식 문서). 새로 검색하지 않아 기존 자료가 그대로 유지된다.
         기본은 원본을 덮어쓰지 않고 새 이름으로 저장한다. --replace는 원본을 <이름>_prev로 백업하고 같은 이름으로 저장한다(작성 스크립트가 공고와 같은 이름의 보고서를 읽는다).
  5. approve 괜찮으면 승인한다. 이후 작성 스크립트가 이 보고서를 회사 사실로 쓴다.

비용: run은 Bedrock을 부른다(조사 1회, 이미지·스캔본 PDF는 파일마다 전사 1회). 검색은 Tavily 호출이 질의 개수만큼 든다(한도·요금은 확인 전).
검색 키: ~/.config/mas/tavily_key 파일(권한 600, 직접 만든다) 또는 환경변수 TAVILY_API_KEY. 이 코드는 키를 출력하지 않는다.
URL은 http·https 공개 주소만 읽는다. 못 읽은 곳은 이유와 함께 보고서에 남는다.
PDF의 글자 추출은 `--extra sources`(pypdf)가 필요하다. 없으면 PDF는 모델 전사로 넘어간다.

예: AWS_PROFILE=user uv run --extra strands --extra sources python scripts/research_brief.py run posting.txt --company "지원처 3." --url https://example.com/about
"""
import argparse
import sys
from pathlib import Path

from common import COLLECT_DIR, RESEARCH_DIR, brief_path
from modular_agentic.core.brief_review import approve, drop_claims, load_record, render_brief_md, save_record, BriefRecord
from modular_agentic.core.types import Item
from modular_agentic.instances.cover_letter.config import RESEARCH_QUERIES, RESEARCH_TOPICS
from modular_agentic.tools.source_search import DEFAULT_MAX_TOTAL, QuerySpec, TavilySearch, find_sources
from modular_agentic.tools.sources import EXTRACT, TRANSCRIBE, collect_docs



def md_path(item_id: str) -> Path:
    return RESEARCH_DIR / f"{item_id}.md"


def save(record: BriefRecord) -> None:
    item_id = record.brief.item_id
    save_record(brief_path(item_id), record)
    md_path(item_id).write_text(render_brief_md(record), encoding="utf-8")


def load(item_id: str) -> BriefRecord:
    path = brief_path(item_id)
    if not path.exists():
        sys.exit(f"조사 보고서가 없다: {path}\n먼저 run으로 만든다.")
    return load_record(path)


def cmd_run(args: argparse.Namespace) -> None:
    from modular_agentic.engines.strands.researcher import StrandsResearcher
    from modular_agentic.engines.strands.transcriber import StrandsTranscriber

    body = args.posting.read_text(encoding="utf-8")
    meta = {"company": args.company} if args.company else {}
    item = Item(id=args.posting.stem, source="file", title=body.splitlines()[0], body=body, meta=meta)
    upload_dir = args.inputs or COLLECT_DIR / item.id
    force = {name: TRANSCRIBE for name in args.transcribe} | {name: EXTRACT for name in args.extract}

    found = []
    if args.no_search:
        print("검색: 끔(--no-search)")
    else:
        result = find_sources(
            args.company or item.title,
            [QuerySpec(*t) for t in RESEARCH_QUERIES],
            TavilySearch(),
            extra_queries=args.query,
            exclude_urls=args.url,
            max_total=args.max_search_urls,
        )
        if result.skipped_reason:
            print(f"검색: 건너뜀 — {result.skipped_reason}\n  (사용자가 준 URL·파일만으로 계속한다)")
        else:
            print(f"검색: 질의 {len(result.queries)}개 → 후보 URL {len(result.hits)}개")
            for err in result.errors:
                print(f"  ⚠ {err}")
        found = result.hits

    print(f"자료 모으는 중: 공고 + 폴더 {upload_dir}({'있음' if upload_dir.is_dir() else '없음'}) + 내가 준 URL {len(args.url)}개 + 검색으로 찾은 URL {len(found)}개")
    docs = collect_docs(item, upload_dir=upload_dir, urls=args.url, found=found, transcriber=StrandsTranscriber(), force=force)
    for d in docs:
        state = "읽음" if d.status.value == "읽음" else f"못 읽음 — {d.reason}"
        by = f" ({d.found_by})" if d.found_by else ""
        print(f"  {d.id} {d.kind.value} {d.locator} [{d.text_origin.value}]{by} {state}")

    brief = StrandsResearcher(topics=RESEARCH_TOPICS).research(item, docs)
    save(BriefRecord(brief=brief, docs=docs, approved=False))
    print(f"\n조사 보고서를 저장했다(승인 전): {md_path(item.id)}")
    print(f"주장 {len(brief.claims)}개, 못 읽은 자료 {len(brief.unread)}건, 못 다룬 주제 {len(brief.gaps)}개")
    print(f"읽고 나서: show / drop / approve {item.id}")


def build_researcher():
    """조사 단계 구현. 테스트는 이 함수를 가짜로 바꿔 끼운다."""
    from modular_agentic.engines.strands.researcher import StrandsResearcher

    return StrandsResearcher(topics=RESEARCH_TOPICS)


def cmd_reresearch(args: argparse.Namespace) -> None:
    """저장해 둔 자료(SourceDoc)로 조사 단계만 다시 돌린다. 검색·가져오기는 하지 않으므로 같은 입력으로 모델 단계를 비교·점검할 수 있다.

    --add로 파일·폴더를 더하면 저장된 자료 뒤에 d<다음 번호>로 붙는다(이미 있는 파일은 건너뛴다).
    """
    from dataclasses import replace

    from modular_agentic.core.types import ReadStatus
    from modular_agentic.tools.sources import load_upload_dir, read_upload_file

    old = load(args.item_id)
    posting = next((d for d in old.docs if d.kind.value == "공고"), None)
    if posting is None:
        sys.exit("저장된 자료에 공고가 없어 다시 돌릴 수 없다.")
    item = Item(id=args.item_id, source="file", title=posting.locator, body=posting.text, meta={"company": old.brief.subject})
    if args.replace and args.save_as:
        sys.exit("--replace와 --as는 같이 쓰지 않는다.")
    new_id = args.item_id if args.replace else (args.save_as or f"{args.item_id}_re")
    if new_id == args.item_id and not args.replace:
        sys.exit("--as가 원래 이름과 같다. 원본을 덮어쓰지 않으려고 다른 이름을 쓴다(덮어쓰려면 --replace).")

    docs = list(old.docs)
    if args.add:
        from modular_agentic.engines.strands.transcriber import StrandsTranscriber

        transcriber = StrandsTranscriber()
        known = {d.locator for d in docs}
        next_number = max((int(d.id[1:]) for d in docs if d.id[1:].isdigit()), default=0) + 1
        for path in args.add:
            added = load_upload_dir(path, transcriber=transcriber) if path.is_dir() else [read_upload_file(path, transcriber=transcriber)]
            for doc in added:
                if doc.locator in known:
                    print(f"  이미 있는 자료라 건너뜀: {doc.locator}")
                    continue
                known.add(doc.locator)
                doc = replace(doc, id=f"d{next_number}")
                next_number += 1
                docs.append(doc)
                state = "읽음" if doc.status is ReadStatus.READ else f"못 읽음 — {doc.reason}"
                print(f"  추가 {doc.id} {doc.kind.value} {doc.locator} [{doc.text_origin.value}] {state}")

    print(f"자료 {len(docs)}건으로 조사 모델만 다시 돌린다(웹 접근 없음)")
    brief = build_researcher().research(item, docs)
    if args.replace:
        save(BriefRecord(brief=replace(old.brief, item_id=f"{args.item_id}_prev"), docs=old.docs, approved=False))
        print(f"원본을 백업했다: {md_path(args.item_id + '_prev')}")
    save(BriefRecord(brief=replace(brief, item_id=new_id), docs=docs, approved=False))
    print(f"저장(승인 전): {md_path(new_id)}")
    print(f"주장 {len(brief.claims)}개 (이전 {len(old.brief.claims)}개), 못 읽은 자료 {len(brief.unread)}건")
    print(f"\n경고 {len(brief.warnings)}개:")
    for w in brief.warnings:
        print(f"  - {w}")
    if not any(w.startswith("버린 주장") for w in brief.warnings):
        print("  (버린 주장 없음)")


def cmd_show(args: argparse.Namespace) -> None:
    print(render_brief_md(load(args.item_id)))


def cmd_drop(args: argparse.Namespace) -> None:
    try:
        record = drop_claims(load(args.item_id), args.claim_ids)
    except ValueError as exc:
        sys.exit(str(exc))
    save(record)
    print(f"{args.claim_ids}를 지웠다. 남은 주장 {len(record.brief.claims)}개. 승인이 풀렸으니 다시 확인하고 approve 한다.")


def cmd_approve(args: argparse.Namespace) -> None:
    record = load(args.item_id)
    save(approve(record))
    print(f"승인했다. 작성 스크립트가 주장 {len(record.brief.claims)}개를 회사 사실로 쓴다.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="자료를 모아 조사하고 보고서를 저장한다(Bedrock 호출)")
    run.add_argument("posting", type=Path, help="공고 본문 텍스트 파일(파일 이름이 공고 id가 된다)")
    run.add_argument("--company", help="회사 이름(없으면 공고 첫 줄을 조사 대상 이름으로 쓴다)")
    run.add_argument("--inputs", type=Path, help="자료 폴더(기본: inputs/<공고 id>)")
    run.add_argument("--url", action="append", default=[], help="내가 직접 주는 웹 주소(여러 번 쓸 수 있다). 검색으로 찾은 것보다 먼저 읽는다")
    run.add_argument("--query", action="append", default=[], help="내가 직접 주는 검색어(여러 번 쓸 수 있다). 기본 질의에 더해 검색한다")
    run.add_argument("--no-search", action="store_true", help="검색으로 URL을 찾지 않는다(내가 준 것만 쓴다)")
    run.add_argument("--max-search-urls", type=int, default=DEFAULT_MAX_TOTAL, help="검색으로 찾아 읽을 URL의 최대 개수")
    run.add_argument("--transcribe", action="append", default=[], metavar="파일이름", help="이 PDF는 추출을 건너뛰고 모델 전사로 읽는다")
    run.add_argument("--extract", action="append", default=[], metavar="파일이름", help="이 PDF는 글자 추출만 하고 전사하지 않는다")
    run.set_defaults(func=cmd_run)

    rr = sub.add_parser("reresearch", help="저장해 둔 자료로 조사 모델만 다시 돌린다(Bedrock 1회, 웹 접근 없음)")
    rr.add_argument("item_id")
    rr.add_argument("--as", dest="save_as", help="새 보고서 이름(기본: <이름>_re). 원본은 덮어쓰지 않는다")
    rr.add_argument("--add", action="append", type=Path, default=[], metavar="파일·폴더", help="자료를 더해서 돌린다(여러 번 쓸 수 있다). 새로 검색하지 않는다")
    rr.add_argument("--replace", action="store_true", help="같은 이름으로 저장한다(원본은 <이름>_prev로 백업). 작성 스크립트가 읽는 이름을 유지할 때 쓴다")
    rr.set_defaults(func=cmd_reresearch)

    show = sub.add_parser("show", help="보고서를 읽는다")
    show.add_argument("item_id")
    show.set_defaults(func=cmd_show)

    drop = sub.add_parser("drop", help="틀린 주장을 지운다(승인이 풀린다)")
    drop.add_argument("item_id")
    drop.add_argument("claim_ids", nargs="+", metavar="주장id")
    drop.set_defaults(func=cmd_drop)

    ok = sub.add_parser("approve", help="보고서를 승인한다(이후 작성이 쓴다)")
    ok.add_argument("item_id")
    ok.set_defaults(func=cmd_approve)

    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    args.func(args)


if __name__ == "__main__":
    main()
