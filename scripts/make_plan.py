"""소재 계획 만들기와 확인 — 문항 여러 개에 쓸 소재를 먼저 배정하고 사용자가 읽고 승인한다.

왜: 문항을 하나씩 쓰면 문항마다 가장 강한 경험이 반복된다. 소재를 먼저 정하면 문항마다 초점이 달라진다(core/plan_review.py).
같은 문항을 다르게 배정해 비교할 수 있다(--variant: 예 고른 가치). 모델 호출은 한 번이다(도구 없음, Bedrock 비용).

사용:
  make       소재 계획을 만든다: make 공고.txt --questions 문항파일 --variant "Work With Grit" [--basic 1,2,9] [--note "지시"]
  show       읽는다: show <공고id> --variant "Work With Grit"
  approve    승인한다: approve <공고id> --variant "Work With Grit"
앞 단계(logs/03_map/<공고>.json)와 승인된 조사 보고서가 있어야 한다. 가치 문항이면 `map_values.py` 결과(logs/03_map/<공고>_values.json)에서
--variant와 같은 이름의 가치 근거를 추가 재료로 쓴다. 결과는 logs/04_plan/<공고>__<이름>.json·.md(git 제외).

--extra-ref는 사용자가 소재로 직접 지정하는 vault 근거(`경로:줄`, 여러 번 가능)다. 그 줄부터 몇 줄을 읽어 "추가 재료"로 넣는다.
매핑이 놓친 소재를 쓰고 싶을 때 쓴다(가치 매핑은 검색과 모델 판단이 실행마다 조금씩 달라 같은 근거가 빠질 수 있다). --label은 파일 이름·제목(기본: --variant).
--user-facts는 사용자가 직접 말한 사실을 적은 파일(예: inputs/user_facts.md, `## 소제목` 하나가 근거 하나)이다. vault와 대조하지 않고 "추가 재료"로 넣는다.
출처는 `사용자:<소제목>`으로 남아 소재 계획·초안에서 vault 근거와 구분된다(tools/user_facts.py). 작성 단계에도 같은 파일을 --user-facts로 준다.
--note는 배정 방향을 주는 지시다. **수치·사실은 쓰지 않는다**: 지시문의 사실은 근거 없이 초안에 들어간다(2026-10-11, 지시문에 쓴 수치가 인용 없이 초안에 나왔다). 사실은 --extra-ref·--user-facts로 준다.
--basic은 어필·검증하지 않을 "기본" 요구의 번호(안 주면 분석 결과 `logs/02_analyze`에 `analyze_posting.py mark`로 표시한 것을 쓴다. 분석 결과의 회사 맥락은 `--with-context`를 줄 때만 소재 계획 입력에 넣는다)(앞 단계의 요구 순서, 1부터)다. 문항 파일은 한 줄에 문항 하나(글자 수 제한은 문항 끝에 적힌 대로).
예: AWS_PROFILE=user uv run --extra strands --extra sources python scripts/make_plan.py make inputs/00_collect/hyundai_autoever_2026q4_backend.txt \\
      --questions inputs/00_collect/hyundai_autoever_2026q4_backend.questions.txt --variant "Work With Grit" --basic 1,2,9,10,11,13
"""
import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from common import MAP_DIR, PLAN_DIR, load_approved_brief, load_saved_analysis
from map_values import value_name
from modular_agentic.core.types import Analysis, ContextNote, Evidence, Mapping, MappingStatus, Requirement
from modular_agentic.instances.cover_letter.config import VAULT_DIR
from modular_agentic.tools.user_facts import load_user_facts, user_facts_mapping
from modular_agentic.tools.vault_read import EXCERPT_CHARS, TRUNCATED_MARK, evidence_from_refs as _read_evidence
from modular_agentic.core.plan_review import PlanRecord, approve, load_record, plan_path, render_plan_md, save_record
from modular_agentic.core.serialize import load_state



def read_questions(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_context(item_id: str):
    state = MAP_DIR / f"{item_id}.json"
    if not state.exists():
        sys.exit(f"앞 단계 결과가 없다: {state}\n먼저 run_writer_demo.py --state-only로 만든다.")
    return load_state(json.loads(state.read_text(encoding="utf-8")))


def find_value_mapping(item_id: str, variant: str):
    """--variant와 같은 이름의 가치 매핑을 찾는다. 없으면 빈 목록."""
    path = MAP_DIR / f"{item_id}_values.json"
    if not variant or not path.exists():
        return []
    _, mappings, _ = load_state(json.loads(path.read_text(encoding="utf-8")))
    return [m for m in mappings if value_name(m.requirement).lower() == variant.lower()]


def evidence_from_refs(refs: list[str], vault_dir: Path = VAULT_DIR) -> list[Evidence]:
    """tools.vault_read.evidence_from_refs를 부르고 실패하면 메시지를 내고 끝낸다."""
    try:
        return _read_evidence(refs, vault_dir)
    except ValueError as exc:
        sys.exit(f"--extra-ref 오류: {exc}")


def pinned_mapping(item_id: str, refs: list[str], vault_dir: Path = VAULT_DIR) -> list[Mapping]:
    """사용자가 지정한 근거를 추가 재료 매핑 하나로 묶는다. 사용자가 고른 소재라는 표시를 판단에 남긴다(검증은 거치지 않은 근거)."""
    if not refs:
        return []
    requirement = Requirement(id=f"{item_id}-pinned", item_id=item_id, text="사용자가 소재로 지정한 근거", kind="지정")
    return [Mapping(requirement, evidence_from_refs(refs, vault_dir), MappingStatus.CONFIRMED, "사용자가 소재로 직접 지정한 근거다. 검증 단계를 거치지 않았다.")]


def truncation_notice(extra: list[Mapping]) -> list[str]:
    """발췌가 EXCERPT_CHARS에서 잘린 근거가 있으면 소재 계획 경고에 올릴 문장(없으면 빈 목록)."""
    cut = [e.ref for m in extra for e in m.evidence if e.excerpt.endswith(TRUNCATED_MARK)]
    if not cut:
        return []
    return [f"근거 발췌가 {EXCERPT_CHARS}자에서 잘렸다({len(cut)}곳): {', '.join(cut)}. 뒤쪽 내용은 소재 계획과 작성이 보지 못했다."]


def resolve_basic(basic_arg: str, mappings: list[Mapping], analysis: Analysis | None) -> tuple[list[str], str]:
    """기본 요건 id와 그 출처 설명. --basic이 있으면 그것(요구 번호, 1부터), 없으면 분석 결과(02_analyze)의 표시, 둘 다 없으면 없음.
    분석 결과의 표시가 상태 파일의 요구에 없으면(두 파일의 요구 목록이 다르면) 조용히 넘기지 않고 ValueError."""
    if basic_arg:
        numbers = [int(x) for x in basic_arg.replace(" ", "").split(",") if x]
        bad = [n for n in numbers if not 1 <= n <= len(mappings)]
        if bad:
            raise ValueError(f"--basic 번호가 범위를 벗어난다: {bad} (요구는 1~{len(mappings)})")
        return [mappings[n - 1].requirement.id for n in numbers], "--basic"
    if analysis is not None and analysis.basic_requirement_ids:
        known = {m.requirement.id for m in mappings}
        missing = [i for i in analysis.basic_requirement_ids if i not in known]
        if missing:
            raise ValueError(f"분석 결과(02_analyze)의 기본 요건 {missing}이 상태 파일(03_map)의 요구에 없다. 두 파일의 요구 목록이 다르다. 분석을 다시 했다면 앞 단계도 --refresh로 다시 돌린다.")
        return list(analysis.basic_requirement_ids), "분석 결과(02_analyze)의 표시"
    return [], "없음"


def context_for(mappings: list[Mapping], analysis: Analysis | None) -> dict[str, list[ContextNote]] | None:
    """분석 결과의 회사 맥락 중 이 상태 파일의 요구에 해당하는 것. 없으면 None."""
    if analysis is None or not analysis.context:
        return None
    known = {m.requirement.id for m in mappings}
    found = {rid: notes for rid, notes in analysis.context.items() if rid in known}
    return found or None


def write_md(path: Path, record: PlanRecord, mappings, verdicts, brief) -> None:
    path.with_suffix(".md").write_text(render_plan_md(record, mappings, verdicts, brief), encoding="utf-8")


def cmd_make(args: argparse.Namespace) -> None:
    from modular_agentic.engines.strands.planner import StrandsPlanner

    item_id = args.posting.stem
    item, mappings, verdicts = load_context(item_id)
    questions = read_questions(args.questions)
    if not questions:
        sys.exit("문항 파일이 비어 있다.")
    analysis = load_saved_analysis(item_id)
    try:
        basic_ids, basic_source = resolve_basic(args.basic, mappings, analysis)
    except ValueError as exc:
        sys.exit(str(exc))
    contexts = context_for(mappings, analysis) if args.with_context else None  # 회사 맥락은 기본으로 쓰지 않는다(설계 문서 25절: 첫 시험에서 정보가 적고 억지 연결이 있었다)
    brief = load_approved_brief(item_id)
    extra = find_value_mapping(item_id, args.variant) + pinned_mapping(item_id, args.extra_ref)
    if args.user_facts:
        try:
            extra += user_facts_mapping(item_id, load_user_facts(args.user_facts))
        except ValueError as exc:
            sys.exit(f"--user-facts 오류: {exc}")
    if args.variant and not extra:
        print(f"'{args.variant}'에 해당하는 가치 매핑을 못 찾았다(가치 문항이 아니면 무시해도 된다).")
    print(f"문항 {len(questions)}개, 요구 {len(mappings)}개(기본 {len(basic_ids)}개, 출처: {basic_source}), 회사 맥락 요구 {len(contexts or {})}개(--with-context {'켬' if args.with_context else '끔'}), 추가 재료 {len(extra)}개, 이름: {args.variant or '(없음)'}")

    plan = StrandsPlanner().plan(item, questions, mappings, verdicts, brief=brief, extra=extra, basic_requirement_ids=basic_ids, notes=args.note, variant=args.variant, contexts=contexts)
    label = args.label or args.variant
    plan = replace(plan, variant=label, warnings=[*plan.warnings, *truncation_notice(extra)])
    path = plan_path(PLAN_DIR, item_id, label)
    record = PlanRecord(plan=plan, approved=False)
    save_record(path, record)
    write_md(path, record, mappings, verdicts, brief)
    print(f"\n소재 계획을 저장했다(승인 전): {path.with_suffix('.md')}")
    for q in plan.questions:
        print(f"  문항 {q.number}: {', '.join(e.name for e in q.experiences) or '(중심 경험 없음)'} / 각도: {q.angle[:60]}")
    for w in plan.warnings:
        print(f"  ⚠ {w}")


def cmd_show(args: argparse.Namespace) -> None:
    path = plan_path(PLAN_DIR, args.item_id, args.variant)
    if not path.exists():
        sys.exit(f"소재 계획이 없다: {path}")
    print(path.with_suffix(".md").read_text(encoding="utf-8"))


def cmd_approve(args: argparse.Namespace) -> None:
    path = plan_path(PLAN_DIR, args.item_id, args.variant)
    if not path.exists():
        sys.exit(f"소재 계획이 없다: {path}")
    item, mappings, verdicts = load_context(args.item_id)
    record = approve(load_record(path))
    save_record(path, record)
    write_md(path, record, mappings, verdicts, load_approved_brief(args.item_id))
    print("승인했다. 작성 단계가 이 소재 계획을 쓴다.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("make", help="소재 계획을 만든다(Bedrock 1회)")
    make.add_argument("posting", type=Path, help="공고 본문 텍스트 파일(파일 이름이 공고 id)")
    make.add_argument("--questions", type=Path, required=True, help="문항 파일(한 줄에 문항 하나)")
    make.add_argument("--variant", default="", help="이 소재 계획의 이름(예: 고른 가치)")
    make.add_argument("--basic", default="", help="어필하지 않을 기본 요구의 번호들(쉼표로, 1부터)")
    make.add_argument("--note", default="", help="배정에 줄 추가 지시")
    make.add_argument("--extra-ref", action="append", default=[], metavar="경로:줄", help="소재로 직접 지정할 vault 근거(여러 번 가능)")
    make.add_argument("--user-facts", type=Path, help="사용자가 직접 말한 사실 파일(vault와 대조하지 않는다)")
    make.add_argument("--with-context", action="store_true", help="분석 결과의 회사 맥락을 소재 계획 입력에 넣는다(기본은 안 넣는다)")
    make.add_argument("--label", default="", help="소재 계획 이름(파일·제목). 기본은 --variant")
    make.set_defaults(func=cmd_make)
    for name, func, text in (("show", cmd_show, "읽는다"), ("approve", cmd_approve, "승인한다")):
        p = sub.add_parser(name, help=text)
        p.add_argument("item_id")
        p.add_argument("--variant", default="")
        p.set_defaults(func=func)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    args.func(args)


if __name__ == "__main__":
    main()
