"""수집 단계: 고른 수집 구현체로 공고 후보를 모으고(search), 사람이 고른 후보를 공고 파일로 가져온다(import).

사용 (레포 루트에서)
  scripts/collect_postings.py search --impl inthiswork [--day 2026-10-20]          크롤러 결과 읽기(모델 호출 없음)
  AWS_PROFILE=user scripts/collect_postings.py search --impl web_agent --goal "금융권 백엔드 신입 공고" [--max 5]
                                                                                   웹 검색 에이전트(Bedrock 여러 번 + Tavily)
  scripts/collect_postings.py search --impl paste --title "회사 직무" --text "공고 본문" [--link URL]
                                                                                   붙여넣기: 후보 없이 바로 공고 파일로 저장
  scripts/collect_postings.py import logs/00_collect/<시각>-<구현>.json --pick 1,3  고른 후보를 inputs/00_collect/<id>.txt로
후보는 logs/00_collect/<시각>-<구현>.json(원본)과 .md(읽는 용: 번호·회사·링크·이미 모았는지·본문 앞부분·버린 후보)로 남는다.
구현체는 registry.py의 collector 목록에서 고른다(--impl, 없으면 MAS_IMPL_COLLECTOR, 없으면 첫 구현).
이미 같은 id의 공고 파일이 있으면 덮어쓰지 않는다. 본문이 비어 있는(이미지뿐인) 공고는 가져와도 분석이 빈 결과를 낸다 —
이미지 주소가 공고 파일에 남으므로 transcribe_file.py로 전사해 본문을 채운다.
"""
import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from modular_agentic.core.types import Item
from modular_agentic.registry import choose, make
from modular_agentic.tools.postings import META_LABELS, collected_titles, save_posting

CANDIDATE_DIR = Path("logs") / "00_collect"
PREVIEW_CHARS = 300


def candidates_md(items: list[Item], impl_label: str, settings: dict, rejected: list, seconds: float) -> str:
    known = collected_titles()
    lines = [f"# 수집 후보 — {impl_label}", "",
             f"설정: {json.dumps(settings, ensure_ascii=False)} · 후보 {len(items)}건 · {seconds:.0f}초", "",
             "가져오기: 콘솔의 [후보 가져오기]에 번호를 적거나 `collect_postings.py import <이 파일의 .json> --pick 1,3`.", ""]
    for n, item in enumerate(items, 1):
        state = "이미 모음" if item.id in known else ("본문 없음(이미지뿐)" if not item.body.strip() else "새 공고")
        lines += [f"## {n}. {item.title}", "",
                  f"- id: `{item.id}` · 상태: **{state}**",
                  *(f"- {label}: {item.meta[k]}" for k, label in {**META_LABELS, "reason": "고른 이유"}.items() if k != "found_by" and item.meta.get(k)),
                  "", "> " + " ".join(item.body[:PREVIEW_CHARS].split()) + ("…" if len(item.body) > PREVIEW_CHARS else ""), ""]
    if rejected:
        lines += ["## 버린 후보(코드 대조에서 탈락)", "", *(f"- {url} — {reason}" for url, reason in rejected), ""]
    return "\n".join(lines)


def cmd_search(args: argparse.Namespace) -> None:
    impl = choose("collector", args.impl)
    settings = {"inthiswork": {"day": args.day}, "web_agent": {"goal": args.goal, "max_candidates": args.max},
                "paste": {"title": args.title, "text": args.text, "link": args.link, "instance": args.instance}}[impl.name]
    collector = make("collector", impl.name, **settings)
    start = time.monotonic()
    items = collector.collect()
    seconds = time.monotonic() - start
    if impl.name == "paste":
        saved = save_posting(items[0])
        print(f"{'저장' if saved.created else '이미 있음(그대로 둠)'}: {saved.path}")
        return
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    stem = CANDIDATE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{impl.name}"
    shown = {k: v for k, v in settings.items() if k != "text"}
    stem.with_suffix(".json").write_text(json.dumps({"impl": impl.name, "settings": shown, "items": [asdict(i) for i in items]},
                                                    ensure_ascii=False, indent=2), encoding="utf-8")
    stem.with_suffix(".md").write_text(candidates_md(items, impl.label, shown, getattr(collector, "rejected", []), seconds), encoding="utf-8")
    print(f"후보 {len(items)}건 ({seconds:.0f}초): {stem.with_suffix('.md')}")


def cmd_import(args: argparse.Namespace) -> None:
    data = json.loads(args.candidates.read_text(encoding="utf-8"))
    items = [Item(**i) for i in data["items"]]
    picks = [int(p) for p in args.pick.replace(" ", "").split(",") if p]
    for n in picks:
        if not 1 <= n <= len(items):
            sys.exit(f"없는 번호: {n} (1~{len(items)})")
    for n in picks:
        saved = save_posting(items[n - 1])
        print(f"{n}. {'저장' if saved.created else '이미 있음(그대로 둠)'}: {saved.path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="후보를 모은다")
    search.add_argument("--impl", help="수집 구현체(inthiswork | web_agent | paste)")
    search.add_argument("--day", default="", help="inthiswork: 크롤러 결과 날짜(YYYY-MM-DD, 기본 오늘)")
    search.add_argument("--goal", default="", help="web_agent: 찾을 공고(예: 금융권 백엔드 신입)")
    search.add_argument("--max", type=int, default=5, help="web_agent: 최대 후보 수")
    search.add_argument("--title", default="", help="paste: 제목(회사 직무)")
    search.add_argument("--text", default="", help="paste: 공고 본문")
    search.add_argument("--link", default="", help="paste: 원문 주소(선택)")
    search.add_argument("--instance", default="", help="paste: 업무(cover_letter | proposal, 기본 자소서)")
    search.set_defaults(func=cmd_search)
    imp = sub.add_parser("import", help="고른 후보를 공고 파일로 저장한다")
    imp.add_argument("candidates", type=Path)
    imp.add_argument("--pick", required=True, help="가져올 후보 번호(쉼표로)")
    imp.set_defaults(func=cmd_import)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    args.func(args)


if __name__ == "__main__":
    main()
