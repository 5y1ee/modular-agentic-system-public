"""분석 단계의 구현체(엔진)를 같은 공고로 돌려 결과를 나란히 비교한다(엔진 축의 교체 증거).

사용 (레포 루트에서)
  AWS_PROFILE=user uv run --extra strands python scripts/compare_analyzers.py inputs/00_collect/<공고>.txt [--impls strands,bedrock]
결과는 logs/02_analyze/<공고>__compare.md(읽는 용)와 .json에 쓴다. 파이프라인이 쓰는 분석 결과(logs/02_analyze/<공고>.json)는
건드리지 않는다(기본 요건 표시·회사 맥락을 지우지 않으려고). 비용: 구현마다 Bedrock 1회.

비교는 개수·시간·문구가 원문에 그대로 있는 비율(원문 유지 규칙을 지켰는지, core/quote_match로 확인)과, 한쪽에만 있는 요구 목록이다.
같은 요구인지는 문구가 같을 때만 같다고 본다(표기 차이 보정은 quote_match의 정규화만). 의미가 같은데 문구가 다른 것은 사람이 본다.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from modular_agentic.core.quote_match import normalize, quote_in_text
from modular_agentic.core.types import Item
from modular_agentic.instances.profile import profile_of_text
from modular_agentic.registry import choose, make
from modular_agentic.engines.basic.bedrock_analyzer import DEFAULT_MODEL_ID, MODEL_ID_ENV

OUT_DIR = Path("logs") / "02_analyze"


def run_one(name: str, item: Item) -> dict:
    impl = choose("analyzer", name)
    start = time.monotonic()
    requirements = make("analyzer", name, profile=profile_of_text(item.body)).analyze(item)
    seconds = time.monotonic() - start
    verbatim = sum(quote_in_text(r.text, item.body) for r in requirements)
    return {"impl": name, "label": impl.label, "seconds": round(seconds, 1), "verbatim": verbatim,
            "requirements": [{"text": r.text, "kind": r.kind} for r in requirements]}


def render(item: Item, results: list[dict], model: str) -> str:
    lines = [f"# 분석 구현체 비교 — {item.title}", "",
             f"같은 공고(`{item.id}`, 업무: {profile_of_text(item.body).label})를 분석 구현체마다 한 번씩 돌렸다. 모델: `{model}`(부품은 같게, 엔진만 다르게). 실행마다 결과가 달라질 수 있다(1회 비교).", "",
             "- **원문 그대로**: 뽑은 요구 문장이 본문에 글자 그대로 있는 개수(공백·따옴표 같은 표기 차이만 보정). 분석 규칙 \"원문 표현 유지\"를 지켰는지 본다.",
             "- **이 구현에만(●)**: 그 문장이 다른 구현의 결과에 같은 문장으로는 없다는 표시. 글자로 비교하므로 같은 요구를 표기만 다르게 써도 ●가 붙는다(실제 차이보다 크게 나온다). 같은 뜻인지는 사람이 읽어 본다.", "",
             "| 구현 | 요구 수 | 원문 그대로 | 시간 |", "|---|---|---|---|"]
    for r in results:
        n = len(r["requirements"])
        lines.append(f"| {r['label']} (`{r['impl']}`) | {n} | {r['verbatim']}/{n} | {r['seconds']}초 |")
    keys = [{normalize(q["text"]) for q in r["requirements"]} for r in results]
    for i, r in enumerate(results):
        others = set().union(*(k for j, k in enumerate(keys) if j != i)) if len(results) > 1 else set()
        only = [q for q in r["requirements"] if normalize(q["text"]) not in others]
        lines += ["", f"## {r['label']} (`{r['impl']}`) — 요구 {len(r['requirements'])}개, 이 구현에만 있는 것 {len(only)}개", "",
                  "| # | 분류 | 요구 | 이 구현에만 |", "|---|---|---|---|"]
        for n, q in enumerate(r["requirements"], 1):
            mark = "●" if q in only else ""
            text = q["text"].replace("|", "\\|")
            lines.append(f"| {n} | {q['kind']} | {text} | {mark} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("posting", type=Path)
    parser.add_argument("--impls", default="strands,bedrock", help="비교할 구현 이름(쉼표로)")
    parser.add_argument("--render", action="store_true", help="저장된 결과(.json)로 문서만 다시 만든다(모델 호출 없음)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    body = args.posting.read_text(encoding="utf-8")
    item = Item(id=args.posting.stem, source="file", title=body.splitlines()[0], body=body)
    out = OUT_DIR / f"{item.id}__compare"
    if args.render:
        saved = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
        out.with_suffix(".md").write_text(render(item, saved["results"], saved["model"]), encoding="utf-8")
        print(f"다시 만듦: {out.with_suffix('.md')}")
        return
    results = [run_one(name.strip(), item) for name in args.impls.split(",") if name.strip()]
    for r in results:
        print(f"{r['label']}: 요구 {len(r['requirements'])}개, 원문 그대로 {r['verbatim']}, {r['seconds']}초")
    model = os.environ.get(MODEL_ID_ENV, DEFAULT_MODEL_ID)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps({"item": item.id, "model": model, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render(item, results, model), encoding="utf-8")
    print(f"비교: {out.with_suffix('.md')}")


if __name__ == "__main__":
    main()
