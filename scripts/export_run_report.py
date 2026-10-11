"""한 번의 실행(상태 + 초안 + 검색 로그)을 읽기 좋은 마크다운 한 장으로 합친다. 읽기 전용(원본은 건드리지 않음).

사용: uv run python scripts/export_run_report.py logs/03_map/<공고>.json --search-log logs/search/<이름>.jsonl [--drafts logs/05_write/<파일>.json]
결과: logs/reports/<시각>-<공고>.md (logs/는 git 제외). VS Code에서 열어 Ctrl+Shift+V로 미리보기를 본다.

내용: 요구별 매핑 판단·근거·검증 판정·검색(매핑/검증 때 쓴 검색어와 상위 결과), 그리고 초안들(이유·다룬 요구·쓴 근거·경고).
--drafts를 안 주면 logs/05_write/에서 같은 공고의 가장 최근 파일을 쓴다. --search-log를 안 주면 검색 부분은 뺀다.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from common import WRITE_DIR
from modular_agentic.core.serialize import load_state

TOP_HITS = 5
SNIPPET = 110  # 검색 결과 발췌를 이만큼만 보여 준다
OUT_DIR = Path("logs") / "reports"


def one_line(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


def load_searches(path: Path | None) -> dict[str, list[dict]]:
    """검색 로그를 태그(예: 'mapper:<요구 id>')별로 모은다."""
    by_tag: dict[str, list[dict]] = {}
    if path is None:
        return by_tag
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            by_tag.setdefault(record["tag"], []).append(record)
    return by_tag


def render_searches(records: list[dict]) -> list[str]:
    lines = []
    for r in records:
        lines.append(f"- 검색어: `{r['query']}` ({r['method']}, {r['elapsed_sec']:.2f}초)")
        for h in r["hits"][:TOP_HITS]:
            lines.append(f"  - {h['rank']}. `{h['path']}:{h['line']}` — {one_line(h['snippet'], SNIPPET)}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("state", type=Path, help="logs/03_map/<공고>.json")
    parser.add_argument("--drafts", type=Path, help="logs/05_write/<파일>.json (없으면 같은 공고의 가장 최근 것)")
    parser.add_argument("--search-log", type=Path, help="logs/search/<이름>.jsonl")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    state_data = json.loads(args.state.read_text(encoding="utf-8"))
    item, mappings, verdicts = load_state(state_data)
    by_req = {v.requirement_id: v for v in verdicts}
    number_of = {m.requirement.id: i for i, m in enumerate(mappings, 1)}
    searches = load_searches(args.search_log)

    drafts_path = args.drafts
    if drafts_path is None:
        found = sorted(WRITE_DIR.glob(f"*-{item.id}.json"))
        drafts_path = found[-1] if found else None
    drafts_data = json.loads(drafts_path.read_text(encoding="utf-8")) if drafts_path else None

    out = [f"# 실행 보고서 — {item.title}", ""]
    out.append(f"- 상태: `{args.state}`")
    out.append(f"- 초안: `{drafts_path}`" if drafts_path else "- 초안: 없음")
    out.append(f"- 검색 로그: `{args.search_log}`" if args.search_log else "- 검색 로그: 없음")
    out.append(f"- vault 상태: 상태 파일 {state_data.get('vault_rev') or '기록 없음'}"
               + (f" / 초안 {drafts_data.get('vault_rev') or '기록 없음'}" if drafts_data else ""))
    if drafts_data:
        out.append(f"- 문항: {drafts_data['question']}")

    out += ["", "## 요약", "", "| # | 요구 | 매핑 | 검증 |", "|---|---|---|---|"]
    for i, m in enumerate(mappings, 1):
        v = by_req.get(m.requirement.id)
        out.append(f"| {i} | {m.requirement.text} | {m.status.value} | {v.kind.value if v else '-'} |")

    out += ["", "## 요구별 상세"]
    for i, m in enumerate(mappings, 1):
        v = by_req.get(m.requirement.id)
        out += ["", f"### {i}. {m.requirement.text}", "", f"분류: {m.requirement.kind or '없음'} · 매핑: **{m.status.value}**"]
        out += ["", f"**매핑 판단**: {m.note}"]
        if m.evidence:
            out += ["", "**근거**"]
            out += [f"- `{e.ref}` — {one_line(e.excerpt, 300)}" for e in m.evidence]
        if v:
            out += ["", f"**검증: {v.kind.value}** — {v.reason}"]
        for tag, title in (("mapper", "매핑 때 검색"), ("verifier", "검증 때 검색")):
            records = searches.get(f"{tag}:{m.requirement.id}", [])
            if records:
                out += ["", f"<details><summary>{title} {len(records)}번</summary>", ""]
                out += render_searches(records)
                out += ["", "</details>"]

    if drafts_data:
        out += ["", "## 초안"]
        for i, d in enumerate(drafts_data["drafts"], 1):
            text = next(iter(d["sections"].values()), "")
            out += ["", f"### 초안 {i} — {d['angle']}", "", f"> 분량 {len(text)}자", ""]
            out += [line for line in text.splitlines()]
            out += ["", f"**이유**: {d['rationale']}", ""]
            out.append(f"**다룬 요구**: {[number_of.get(r, r) for r in d['covered_requirement_ids']]}")
            out.append("")
            out.append("**쓴 근거**: " + ", ".join(f"`{r}`" for r in d["used_refs"]))
            for w in d["warnings"]:
                out += ["", f"⚠ {w}"]
        todos = drafts_data["drafts"][0]["todos"] if drafts_data["drafts"] else []
        out += ["", "## 직접 채울 것(근거 없는 요구)", ""] + [f"- {t}" for t in todos]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{item.id}.md"
    out_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"저장: {out_path} ({len(out)}줄)")


if __name__ == "__main__":
    main()
