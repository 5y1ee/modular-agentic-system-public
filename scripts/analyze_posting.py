"""분석 단계: 공고에서 요구 목록을 뽑아 저장하고, 사용자가 읽고 기본 요건을 표시하게 하고, 조사 주장을 요구마다 붙인다(설계 문서 25절).

왜: 분석이 매핑·검증과 한 번에 돌며 `logs/03_map` 안에만 있어서, 사용자가 매핑 전에 요구 목록을 읽고 "이건 기본 요건이라 어필하지 않는다"고 표시할 자리가 없었다.
결과는 `logs/02_analyze/<공고>.json`(원본)과 `.md`(읽는 용, git 제외)다. 요구는 공고에서만 뽑고, 조사 보고서는 요구마다 붙는 참고 메모(회사 맥락)일 뿐이다.

명령
  run 공고.txt          공고에서 요구 목록을 뽑아 저장한다(Bedrock 1회). 이미 있으면 --refresh가 있어야 다시 만든다(기본 요건 표시가 사라진다)
  from-state 공고id     이미 만든 `logs/03_map/<공고>.json` 상태에서 분석 결과를 만든다(모델 없음, 비용 없음). 기본 요건 표시는 처음엔 비어 있다
  context 공고id        승인된 조사 보고서의 주장을 요구마다 붙인다(Bedrock 1회). 조사 보고서가 승인돼 있어야 한다. 이전 맥락은 대체된다
  mark 공고id --basic 1,2,9,10   기본 요건(어필·검증하지 않을 요구)을 요구 번호(1부터)로 표시한다. 이전 표시는 이 목록으로 바뀐다
  show 공고id           읽는 용 문서를 출력한다
승인된 조사 보고서가 있으면 `run`이 끝난 뒤 이어서 `context`를 부르면 된다(자동으로 이어 부르지 않는다: 비용이 따로 든다).
예: AWS_PROFILE=user uv run --extra strands --extra sources python scripts/analyze_posting.py context hyundai_autoever_2026q4_backend
"""
import argparse
import json
import sys
from pathlib import Path

from common import ANALYZE_DIR, MAP_DIR, brief_path, load_saved_analysis
from modular_agentic.core.analysis_review import analysis_path, mark_basic, save_analysis, with_context
from modular_agentic.core.brief_review import approved_brief, load_record, without_posting_only_claims
from modular_agentic.core.serialize import load_state
from modular_agentic.core.types import Analysis, Item


def parse_numbers(text: str) -> list[int]:
    return [int(x) for x in text.replace(" ", "").split(",") if x]


def analysis_from_state(item_id: str) -> Analysis:
    """03_map 상태 파일의 요구 목록으로 분석 결과를 만든다(모델 없음)."""
    state = MAP_DIR / f"{item_id}.json"
    if not state.exists():
        sys.exit(f"상태 파일이 없다: {state}")
    _, mappings, _ = load_state(json.loads(state.read_text(encoding="utf-8")))
    return Analysis(item_id=item_id, requirements=[m.requirement for m in mappings])


def require_analysis(item_id: str) -> Analysis:
    analysis = load_saved_analysis(item_id)
    if analysis is None:
        sys.exit(f"분석 결과가 없다: {analysis_path(ANALYZE_DIR, item_id)}\n먼저 `run 공고.txt` 또는 `from-state {item_id}`로 만든다.")
    return analysis


def cmd_run(args: argparse.Namespace) -> None:
    from modular_agentic.engines.strands.analyzer import StrandsAnalyzer

    item_id = args.posting.stem
    path = analysis_path(ANALYZE_DIR, item_id)
    if path.exists() and not args.refresh:
        sys.exit(f"이미 있다: {path}\n다시 만들려면 --refresh(기본 요건 표시와 회사 맥락이 사라진다).")
    body = args.posting.read_text(encoding="utf-8")
    item = Item(id=item_id, source="file", title=body.splitlines()[0], body=body)
    requirements = StrandsAnalyzer().analyze(item)
    if not requirements:
        sys.exit("요구를 뽑지 못했다(본문이 비었거나 요구가 없다).")
    save_analysis(path, Analysis(item_id=item_id, requirements=requirements), approved_brief_or_none(item_id))
    print(f"요구 {len(requirements)}개를 저장했다: {path.with_suffix('.md')}")
    print("다음: 읽고 `mark`로 기본 요건을 표시한다. 승인된 조사 보고서가 있으면 `context`로 회사 맥락을 붙일 수 있다.")


def cmd_from_state(args: argparse.Namespace) -> None:
    path = analysis_path(ANALYZE_DIR, args.item_id)
    if path.exists() and not args.refresh:
        sys.exit(f"이미 있다: {path}\n다시 만들려면 --refresh(기본 요건 표시와 회사 맥락이 사라진다).")
    analysis = analysis_from_state(args.item_id)
    save_analysis(path, analysis, approved_brief_or_none(args.item_id))
    print(f"상태 파일의 요구 {len(analysis.requirements)}개로 분석 결과를 만들었다(모델 호출 없음): {path.with_suffix('.md')}")


def approved_brief_or_none(item_id: str):
    """승인된 조사 보고서(없으면 None). 읽는 용 문서에 맥락의 주장 본문을 보이는 데만 쓴다. 안내 메시지는 내지 않는다."""
    brief, _ = approved_brief(brief_path(item_id))
    return brief


def cmd_context(args: argparse.Namespace) -> None:
    from modular_agentic.engines.strands.context_linker import StrandsContextLinker

    analysis = require_analysis(args.item_id)
    path = brief_path(args.item_id)
    if not path.exists() or not load_record(path).approved:
        sys.exit("승인된 조사 보고서가 없다. 먼저 research_brief.py로 만들고 승인한다.")
    record = load_record(path)
    brief, only_posting = without_posting_only_claims(record)
    notice = [f"공고에서만 나온 주장 {len(only_posting)}개({', '.join(only_posting)})는 연결에서 뺐다: 요구가 이미 공고에서 나온 것이라 연결하면 되풀이가 된다."] if only_posting else []
    if not brief.claims:
        sys.exit("공고 밖에서 나온 조사 주장이 없다(웹 자료나 직접 올린 자료의 주장이 필요하다).")
    print(f"조사 주장 {len(record.brief.claims)}개 중 {len(brief.claims)}개로 연결한다." + (f" {notice[0]}" if notice else ""))
    context, warnings = StrandsContextLinker().link(analysis.requirements, brief)
    warnings = [*notice, *warnings]
    updated = with_context(analysis, context, warnings)
    save_analysis(analysis_path(ANALYZE_DIR, args.item_id), updated, brief)
    linked = sum(len(v) for v in context.values())
    print(f"요구 {len(analysis.requirements)}개 중 {len(context)}개에 회사 맥락 {linked}개를 붙였다. 읽고 억지 연결은 무시한다: {analysis_path(ANALYZE_DIR, args.item_id).with_suffix('.md')}")
    for w in warnings:
        print(f"  ⚠ {w}")


def cmd_mark(args: argparse.Namespace) -> None:
    analysis = require_analysis(args.item_id)
    try:
        updated = mark_basic(analysis, parse_numbers(args.basic))
    except ValueError as exc:
        sys.exit(str(exc))
    save_analysis(analysis_path(ANALYZE_DIR, args.item_id), updated, approved_brief_or_none(args.item_id))
    print(f"기본 요건으로 표시했다: {args.basic or '(없음)'} → 소재 계획이 --basic을 안 받으면 이 표시를 쓴다.")


def cmd_show(args: argparse.Namespace) -> None:
    require_analysis(args.item_id)
    print(analysis_path(ANALYZE_DIR, args.item_id).with_suffix(".md").read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="공고에서 요구 목록을 뽑아 저장(Bedrock 1회)")
    run.add_argument("posting", type=Path, help="공고 본문 텍스트 파일(파일 이름이 공고 id)")
    run.add_argument("--refresh", action="store_true", help="이미 있어도 다시 만든다(기본 요건 표시·회사 맥락이 사라진다)")
    run.set_defaults(func=cmd_run)
    from_state = sub.add_parser("from-state", help="03_map 상태에서 분석 결과를 만든다(모델 없음)")
    from_state.add_argument("item_id")
    from_state.add_argument("--refresh", action="store_true")
    from_state.set_defaults(func=cmd_from_state)
    context = sub.add_parser("context", help="승인된 조사 주장을 요구마다 붙인다(Bedrock 1회)")
    context.add_argument("item_id")
    context.set_defaults(func=cmd_context)
    mark = sub.add_parser("mark", help="기본 요건을 요구 번호로 표시")
    mark.add_argument("item_id")
    mark.add_argument("--basic", required=True, help="기본 요건의 요구 번호들(쉼표로, 1부터). 비우려면 \"\"")
    mark.set_defaults(func=cmd_mark)
    show = sub.add_parser("show", help="읽는 용 문서 출력")
    show.add_argument("item_id")
    show.set_defaults(func=cmd_show)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    args.func(args)


if __name__ == "__main__":
    main()
