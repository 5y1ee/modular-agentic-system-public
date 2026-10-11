"""초안, 또는 사용자가 고친 글의 문장마다 쓴 근거가 뒷받침하는지 검사한다(문장 검증, 설계 문서 24절).

왜: 초안에는 근거에 없는 문장이 섞여 나온다. 지금까지는 사람이 근거 원문과 한 문장씩 대조해 찾았다. 이 스크립트가 모델로 그 대조를 하고,
모델이 댄 인용이 근거에 실제로 있는지는 코드가 확인한다. 판정은 모델이 한 것이라 틀릴 수 있다. ⚠ 표시한 문장은 사람이 근거 원문과 본다.

사용:
  초안 검사      check_draft.py 공고.txt --draft logs/05_write/<시각>-<공고>.json --number 1
  고친 글 검사   check_draft.py 공고.txt --draft logs/05_write/<시각>-<공고>.json --number 1 --text 고친글.txt
  (둘 다 --user-facts 파일을 줄 수 있다. 초안이 `사용자:` 근거를 썼으면 필요하다.)
검사하는 근거는 그 초안(--draft의 --number번째)이 쓴 것(used_refs, used_claims)만이다. 고친 글에서 새 사실을 더했으면 그 근거를
`--refs "경로:줄,경로:줄"`·`--claims c1,c2`로 더해 준다. 근거 밖의 문장은 그만큼 "근거 없음"으로 나온다. 이게 정상이다(근거를 인용하지 않았다는 뜻).
--text 파일에는 글(헤드라인 포함)만 둔다. "(헤드라인 포함 …자)"·"*글자 수 …*" 같은 분량 메모 줄은 알아서 뺀다.

결과는 logs/05_write/<시각>-<공고>.check.md(읽는 용, git 제외)와 화면 요약이다. 모델을 한 번 부른다(Bedrock 비용, AWS_PROFILE 필요).
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from common import WRITE_DIR, collect_check_sources, load_approved_brief
from modular_agentic.core.claim_check import flagged, render_check_md, summary
from modular_agentic.engines.strands.claim_checker import StrandsClaimChecker


def split_list(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("posting", type=Path, help="공고 본문 텍스트 파일(파일 이름이 공고 id. 승인된 조사 보고서를 찾는 데 쓴다)")
    parser.add_argument("--draft", type=Path, required=True, help="초안 JSON(logs/05_write/<시각>-<공고>.json)")
    parser.add_argument("--number", type=int, required=True, help="그 파일의 몇 번째 초안인가(1부터)")
    parser.add_argument("--text", type=Path, help="초안 대신 검사할 글(사용자가 고친 판). 근거는 그 초안이 쓴 것을 쓴다")
    parser.add_argument("--refs", default="", help="근거를 더한다: `경로:줄`(쉼표로 여러 개, `사용자:소제목`도 가능)")
    parser.add_argument("--claims", default="", help="회사 사실 id를 더한다(쉼표로)")
    parser.add_argument("--user-facts", type=Path, help="사용자 진술 파일(`사용자:` 근거를 읽을 때)")
    parser.add_argument("--out", type=Path, help="보고서 경로(기본: logs/05_write/<시각>-<공고>.check.md)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    data = json.loads(args.draft.read_text(encoding="utf-8"))
    drafts = data["drafts"]
    if not 1 <= args.number <= len(drafts):
        sys.exit(f"--number는 1~{len(drafts)}이어야 한다(초안 {len(drafts)}개).")
    draft = drafts[args.number - 1]
    text = args.text.read_text(encoding="utf-8") if args.text else next(iter(draft["sections"].values()), "")
    if not text.strip():
        sys.exit("검사할 글이 비어 있다.")

    item_id = args.posting.stem
    refs = [*draft.get("used_refs", []), *split_list(args.refs)]
    claim_ids = [*draft.get("used_claims", []), *split_list(args.claims)]
    evidence, claims = collect_check_sources(refs, claim_ids, load_approved_brief(item_id) if claim_ids else None, args.user_facts)
    print(f"검사할 근거: 내 근거 {len(evidence)}곳, 회사 사실 {len(claims)}개")

    checks = StrandsClaimChecker().check(text, evidence, claims)
    what = f"사용자가 고친 글({args.text.name})" if args.text else "초안"
    title = f"{item_id} — {args.draft.name}의 {args.number}번째 {what}"
    notes = [f"검사한 근거: 내 근거 {len(evidence)}곳({', '.join(e.ref for e in evidence) or '없음'}), 회사 사실 {len(claims)}개({', '.join(c.id for c in claims) or '없음'})"]
    out = args.out or WRITE_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{item_id}.check.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_check_md(title, checks, notes), encoding="utf-8")

    print(summary(checks))
    for c in flagged(checks):
        print(f"  ⚠ [{c.index}] {c.support.value}: {c.sentence[:80]}{'…' if len(c.sentence) > 80 else ''}")
    print(f"읽는 용: {out}")


if __name__ == "__main__":
    main()
