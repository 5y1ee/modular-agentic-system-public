"""문장 검증기를 사람이 라벨을 붙인 문장으로 평가한다(읽기 전용 실험 스크립트, 결과는 docs/experiments/에 이어서 적는다).

사용: AWS_PROFILE=<이름> uv run --extra strands --extra sources python scripts/eval_claim_checker.py inputs/eval/claim_check_golden.json [--out 보고서.md]

정답 표본 파일(JSON, 자소서 본문이 들어 있어 git에 올리지 않는다)의 모양:
  {"title": "...", "cases": [{"id": "...", "item_id": "<공고 id>", "text": "검사할 글", "refs": ["경로:줄", "사용자:소제목"], "claims": ["c13"],
     "user_facts": "inputs/user_facts.md", "labels": [{"marker": "문장 안의 구절", "expect": "flag|pass", "why": "...", "must_contain": "...", "must_not_contain": "..."}]}]}
라벨이 검증기가 본 근거와 어긋나면(eval/claim_check.py 설명) 평가에서 빼고 이유를 보고서에 적는다. 사례마다 모델을 한 번 부른다(Bedrock 비용).
검증기가 본 것(근거 발췌)은 check_draft.py와 같은 방식으로 읽는다: 지금 vault의 줄을 그대로 읽으므로 vault가 바뀌면 결과가 달라질 수 있다(vault_rev를 같이 적는다).
"""
import argparse
import json
import sys
from pathlib import Path

from common import collect_check_sources, load_approved_brief
from modular_agentic.core.claim_check import flagged
from modular_agentic.engines.strands.claim_checker import PROMPT_VERSION, StrandsClaimChecker
from modular_agentic.engines.strands.config import model_id
from modular_agentic.eval.claim_check import Label, Row, label_problems, locate, render_eval_md
from modular_agentic.instances.cover_letter.config import VAULT_DIR
from modular_agentic.tools.vault_revision import stamp_environment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("golden", type=Path, help="정답 표본 JSON")
    parser.add_argument("--out", type=Path, help="보고서 경로(기본: logs/reports/claim-check-eval.md)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    checker = StrandsClaimChecker()
    rows: list[Row] = []
    invalid: list[str] = []
    unlabeled: list[str] = []
    unlabeled_flagged: list[tuple[str, object]] = []
    for case in golden["cases"]:
        claims_ids = case.get("claims", [])
        facts = Path(case["user_facts"]) if case.get("user_facts") else None
        brief = load_approved_brief(case["item_id"]) if claims_ids else None
        evidence, claims = collect_check_sources(case.get("refs", []), claims_ids, brief, facts)
        source_text = "\n".join([*(e.excerpt for e in evidence), *(c.text for c in claims)])
        labels = [Label(**raw) for raw in case["labels"]]
        usable = []
        for label in labels:
            problems = label_problems(label, source_text)
            if problems:
                invalid.append(f"[{case['id']}] {label.marker[:40]!r}: " + "; ".join(problems))
            else:
                usable.append(label)

        print(f"[{case['id']}] 검사 중(근거 {len(evidence)}곳, 회사 사실 {len(claims)}개, 라벨 {len(usable)}개)")
        checks = checker.check(case["text"], evidence, claims)
        for label in usable:
            try:
                rows.append(Row(case["id"], label, locate(checks, label.marker)))
            except ValueError as exc:
                invalid.append(f"[{case['id']}] {exc}")
        labeled_markers = {r.check.index for r in rows if r.case_id == case["id"]}
        rest = [c for c in checks if c.index not in labeled_markers]
        unlabeled_flagged += [(case["id"], c) for c in flagged(rest)]
        unlabeled.append(f"[{case['id']}] 라벨 없는 문장 {len(rest)}개 중 표시한 것 {len(flagged(rest))}개: " + ", ".join(f"[{c.index}]{c.support.value}" for c in flagged(rest)))

    meta = [f"모델: {model_id()}", f"검증기 프롬프트 해시: {PROMPT_VERSION}", f"vault 상태: {stamp_environment(VAULT_DIR)}", f"사례 {len(golden['cases'])}개, 평가에 쓴 라벨 {len(rows)}개", *unlabeled]
    out = args.out or Path("logs") / "reports" / "claim-check-eval.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_eval_md(golden.get("title", args.golden.stem), rows, invalid, meta, unlabeled_flagged), encoding="utf-8")
    flag_rows = [r for r in rows if r.label.expect == "flag"]
    pass_rows = [r for r in rows if r.label.expect == "pass"]
    print(f"찾아낸 비율 {sum(r.flagged for r in flag_rows)}/{len(flag_rows)}, 오탐 {sum(r.flagged for r in pass_rows)}/{len(pass_rows)}, 뺀 라벨 {len(invalid)}개")
    print(f"보고서: {out}")


if __name__ == "__main__":
    main()
