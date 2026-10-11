"""공고 하나로 분석 → (요구 전부) 매핑 → 검증 → 작성(초안 여러 개)까지 돌려 보여 준다.

사용:
  scripts/with_qmd_server.sh uv run --extra strands python scripts/run_writer_demo.py samples/db_inc_sw_engineer_ax.txt \
      --question "직무 관심 계기·노력·강점 (800자)"
  (프로필·리전은 `AWS_PROFILE=<이름>` 등 환경변수. 검색 기록은 `MAS_SEARCH_LOG=logs/search/<이름>.jsonl`)

분석 결과는 `logs/02_analyze/<공고>.json`에 따로 저장되고(analyze_posting.py), 있으면 앞 단계는 그 요구 목록으로 매핑·검증만 한다.
앞 단계(분석·매핑·검증) 결과는 `logs/03_map/<공고 파일 이름>.json`에 저장된다. 이 파일이 있으면 앞 단계를 건너뛰고
저장된 결과로 작성만 한다 — 작성은 피드백을 받아 여러 번 다시 쓰는 단계라 앞 단계(요구당 약 30초, Bedrock 호출)를 되풀이하지 않으려는 것이다.
`--refresh`를 주면 앞 단계를 새로 돌린다. 상태 파일에는 자소서 내용이 들어 있어 git에 올리지 않는다(logs/).

옵션
  --plan 이름 --number N   승인된 소재 계획(make_plan.py)의 N번 문항 배정으로 쓴다. 문항 글은 소재 계획에서 읽는다(--question 불필요).
                  그 문항의 중심 경험·각도·쓰지 않을 소재·조심할 점·배정된 회사 사실만 쓴다. 승인 전 소재 계획은 쓰지 않는다.
                  소재 계획이 `사용자:` 근거를 쓰면 --user-facts 파일(make_plan.py에 준 것과 같은 파일)이 필요하다. 이 근거는 vault와 대조하지 않았고 초안에 "검증 안 된 사용자 진술 포함"이 표시된다.
  --guidance "지시"  이번 작성에만 적용할 작성 지침(여러 줄 가능). 프롬프트 끝에 붙는다(예: 넣지 말 문장, 바로잡을 사실).
  --state-only    앞 단계(분석·매핑·검증)만 돌려 상태 파일을 만들고 끝낸다(작성하지 않는다). 문항은 필요 없다.
  --n N           초안 개수(기본 3)
  --focus 1,3,5   재료로 쓸 요구의 번호(공고에서 뽑힌 순서, 1부터). 없으면 쓸 수 있는 요구 전부.
초안은 `logs/05_write/<시각>-<공고>.json`에도 저장한다.
검색을 쓰는 매핑·검증이 돌 때만 qmd 서버가 필요하다. 저장된 상태로 작성만 할 때는 필요 없다.
"""
import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from common import ANALYZE_DIR, MAP_DIR, WRITE_DIR, drafts_to_md, load_approved_brief, load_known_experiences, load_plan_question, print_draft
from modular_agentic.core.analysis_review import analysis_path, load_analysis, save_analysis
from modular_agentic.core.serialize import dump_state, load_state
from modular_agentic.core.types import Analysis, Item
from modular_agentic.engines.strands.analyzer import StrandsAnalyzer
from modular_agentic.engines.strands.mapper import StrandsMapper
from modular_agentic.engines.strands.verifier import StrandsVerifier
from modular_agentic.engines.strands.writer import StrandsWriter
from modular_agentic.instances.cover_letter.config import VAULT_DIR
from modular_agentic.tools.vault_revision import ENV_NAME, stamp_environment



def build_state(item: Item, state_path: Path):
    """분석 → 요구 전부 매핑 → 검증을 돌려 저장한다."""
    start = time.monotonic()
    saved = load_analysis(analysis_path(ANALYZE_DIR, item.id))
    if saved is not None:
        requirements = saved.requirements
        print(f"분석 결과(02_analyze)를 읽었다: 요구 {len(requirements)}개 — 분석 호출은 건너뜀. 다시 분석하려면 analyze_posting.py run --refresh")
    else:
        requirements = StrandsAnalyzer().analyze(item)
        print(f"분석: 요구 {len(requirements)}개 ({time.monotonic() - start:.1f}초)")
        if requirements:
            save_analysis(analysis_path(ANALYZE_DIR, item.id), Analysis(item_id=item.id, requirements=requirements))
            print(f"  분석 결과를 저장했다: {analysis_path(ANALYZE_DIR, item.id).with_suffix('.md')}")
    for i, r in enumerate(requirements, 1):
        print(f"  {i}. [{r.kind or '분류 없음'}] {r.text}")

    start = time.monotonic()
    mappings = StrandsMapper().map(requirements)
    print(f"매핑: {time.monotonic() - start:.1f}초 — " + ", ".join(f"{m.status.value}" for m in mappings))

    start = time.monotonic()
    verdicts = StrandsVerifier(load_known_experiences()).verify(mappings)
    print(f"검증: {time.monotonic() - start:.1f}초 — " + ", ".join(v.kind.value for v in verdicts))

    MAP_DIR.mkdir(parents=True, exist_ok=True)
    state = dump_state(item, mappings, verdicts)
    state["vault_rev"] = os.environ.get(ENV_NAME, "")  # 이 상태가 어느 vault로 만들어졌는지
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"상태 저장: {state_path}")
    return item, mappings, verdicts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("posting", type=Path, help="공고 본문 텍스트 파일")
    parser.add_argument("--question", help="자소서 문항(분량 제한은 '800자'처럼 적는다). --state-only가 아니면 필요하다")
    parser.add_argument("--plan", help="승인된 소재 계획의 이름(make_plan.py의 --variant/--label)")
    parser.add_argument("--number", type=int, help="--plan과 함께: 쓸 문항 번호")
    parser.add_argument("--user-facts", type=Path, help="--plan과 함께: 사용자가 직접 말한 사실 파일(소재 계획이 `사용자:` 근거를 쓸 때)")
    parser.add_argument("--guidance", default="", help="이번 작성에만 적용할 작성 지침")
    parser.add_argument("--state-only", action="store_true", help="앞 단계(분석·매핑·검증)만 돌려 상태 파일을 만들고 끝낸다(작성 안 함)")
    parser.add_argument("--n", type=int, default=3, help="초안 개수")
    parser.add_argument("--focus", help="재료로 쓸 요구 번호들(쉼표로, 1부터)")
    parser.add_argument("--refresh", action="store_true", help="매핑·검증을 새로 돌린다(분석은 logs/02_analyze의 요구 목록을 읽는다. 다시 분석하려면 analyze_posting.py run --refresh)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.plan and not args.number:
        parser.error("--plan에는 --number(문항 번호)가 필요하다")
    if not args.state_only and not args.question and not args.plan:
        parser.error("--question이 필요하다(소재 계획으로 쓰려면 --plan과 --number, 앞 단계만 돌리려면 --state-only)")

    vault_rev = stamp_environment(VAULT_DIR)
    state_path = MAP_DIR / f"{args.posting.stem}.json"
    if state_path.exists() and not args.refresh:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
        item, mappings, verdicts = load_state(state_data)
        print(f"저장된 상태를 읽었다: {state_path} (요구 {len(mappings)}개) — 앞 단계는 건너뜀")
        made_with = state_data.get("vault_rev") or "기록 없음"
        note = "" if made_with == vault_rev else "  ← 지금 vault와 다르다(근거가 그때의 vault에서 나온 것)"
        print(f"vault 상태: 상태 파일을 만들 때 {made_with} / 지금 {vault_rev}{note}")
    else:
        body = args.posting.read_text(encoding="utf-8")
        item = Item(id=args.posting.stem, source="file", title=body.splitlines()[0], body=body)
        item, mappings, verdicts = build_state(item, state_path)

    if args.state_only:
        from collections import Counter

        counts = Counter(m.status.value for m in mappings)
        flagged = sum(1 for v in verdicts if v.kind.value != "승인")
        print(f"\n앞 단계 끝: 요구 {len(mappings)}개 — " + ", ".join(f"{k} {v}" for k, v in counts.items()) + f" / 검증 플래그 {flagged}개")
        print("작성은 문항을 정해 --question으로 다시 실행한다(저장된 상태를 읽는다).")
        return

    focus = None
    if args.focus:
        numbers = [int(x) for x in args.focus.split(",")]
        focus = [mappings[n - 1].requirement.id for n in numbers]
        print(f"재료로 쓸 요구: {numbers}")

    start = time.monotonic()
    brief = load_approved_brief(item.id)  # 승인된 조사 보고서만 쓴다(research_brief.py)
    extra = {} if brief is None else {"brief": brief}
    question = args.question
    if args.plan:
        question_plan, plan_extra = load_plan_question(item.id, args.plan, args.number, args.user_facts)
        question = question_plan.question
        extra |= {"plan": question_plan, "extra": plan_extra}
        print(f"소재 계획으로 쓴다: 문항 {args.number} — 중심 경험 {[e.name for e in question_plan.experiences]}")
    drafts = StrandsWriter(guidance=args.guidance).write(item, question, mappings, verdicts, n=args.n, focus=focus, **extra)
    print(f"\n작성: 초안 {len(drafts)}개 ({time.monotonic() - start:.1f}초)")
    number_of = {m.requirement.id: i for i, m in enumerate(mappings, 1)}
    for i, d in enumerate(drafts, 1):
        print_draft(i, d, number_of)
    if drafts:
        print(f"\n직접 채울 것(근거 없는 요구): {drafts[0].todos}")

    WRITE_DIR.mkdir(parents=True, exist_ok=True)
    out = WRITE_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{item.id}.json"
    out.write_text(json.dumps({"question": question, "vault_rev": vault_rev, "drafts": [asdict(d) for d in drafts]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {out}")
    md_out = out.with_suffix(".md")
    md_out.write_text(drafts_to_md(question, drafts, f"{item.id} — 문항 {args.number}" if args.plan else item.id), encoding="utf-8")
    print(f"읽는 용: {md_out}")


if __name__ == "__main__":
    main()
