"""초안을 받아 보고 선택·피드백을 주면서 작성 지침을 고쳐 나가는 터미널 루프.

사용: AWS_PROFILE=<이름> uv run --extra strands python scripts/feedback_loop.py samples/db_inc_sw_engineer_ax.txt \
          --question "직무 관심 계기·노력·강점 (800자)"
조사 보고서(`scripts/research_brief.py`)를 **승인했으면** 그 주장들을 회사 사실로 쓴다. 승인 전이거나 없으면 쓰지 않고 그렇게 알린다.
먼저 `scripts/run_writer_demo.py`로 같은 공고의 상태(`logs/03_map/<공고>.json`)를 만들어 두어야 한다(분석·매핑·검증은 시간·비용이 들어
여기서는 되풀이하지 않는다). 이 루프는 작성(Bedrock 호출)만 한다. qmd 서버는 필요 없다.

명령 (초안·요구 번호는 1부터)
  p N [이유]   N번 초안이 가장 좋다(이유는 비워도 되지만 쓰면 지침 후보가 정확해진다)
  f N 내용     N번 초안에 피드백
  m            더 뽑기: 이미 낸 초안과 다른 각도로 3개
  n            새로 쓰기: 지침을 반영해 3개(이전 각도와 겹쳐도 됨. 지침 효과를 볼 때)
  r 1,3,5      요구 1·3·5만 재료로 3개 다시 쓰기
  l            지금까지 낸 초안 다시 보기
  g            지금까지의 피드백에서 작성 지침 후보를 뽑는다 → 번호로 승인한 것만 지침이 된다
  h            도움말, q 끝내기
지침: "일반"(문체·구조 선호)은 `logs/guidance.md`에 저장돼 다음 실행에도 쓰이고, "특정"(이 공고·문항 내용)은 이번 실행에서만 쓴다.
피드백 기록은 `logs/05_write/feedback/<공고>.jsonl`. 둘 다 자소서 내용이 들어 있어 git에 올리지 않는다(logs/).
이 방식은 강화학습이 아니라 사람 피드백으로 프롬프트(지침)를 고치는 간단한 self-improving이다.
"""
import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from common import FEEDBACK_DIR, GUIDANCE_PATH, MAP_DIR, load_approved_brief, print_draft
from modular_agentic.core.feedback import FeedbackSession, FeedbackStore
from modular_agentic.core.serialize import load_state

HELP = __doc__.split("명령 (초안·요구 번호는 1부터)")[1].split("지침:")[0].rstrip()


def _numbers(text: str) -> list[int]:
    return [int(x) for x in text.replace(" ", "").split(",") if x]


def run_loop(session: FeedbackSession, input_fn: Callable[[str], str] = input, out: Callable[[str], None] = print) -> None:
    """명령을 읽어 세션에 전달한다. 입력 처리만 하고 로직은 FeedbackSession에 있다."""
    number_of = {m.requirement.id: i for i, m in enumerate(session.mappings, 1)}

    def show(new, start: int) -> None:
        for offset, d in enumerate(new):
            print_draft(start + offset, d, number_of)
        if new and new[0].todos:
            out(f"\n직접 채울 것(근거 없는 요구): {new[0].todos}")

    def generate(**kwargs) -> None:
        start = len(session.drafts) + 1
        show(session.generate(**kwargs), start)

    out("초안을 쓰는 중…")
    generate()
    out("\n명령: p 선택 / f 피드백 / m 더 뽑기 / n 새로 쓰기 / r 요구 선택 / l 목록 / g 지침 / h 도움말 / q 끝")
    while True:
        try:
            line = input_fn("\n> ").strip()
        except EOFError:
            return
        if not line:
            continue
        cmd, _, rest = line.partition(" ")
        rest = rest.strip()
        try:
            if cmd == "q":
                return
            elif cmd == "h":
                out(HELP)
            elif cmd == "l":
                show(session.drafts, 1)
            elif cmd == "m":
                out("더 뽑는 중…")
                generate(avoid_previous=True)
            elif cmd == "n":
                out("새로 쓰는 중…")
                generate()
            elif cmd == "r":
                ids = session.requirement_ids(_numbers(rest))
                out(f"요구 {_numbers(rest)}만 재료로 쓰는 중…")
                generate(focus=ids)
            elif cmd == "p":
                number, _, reason = rest.partition(" ")
                session.pick(int(number), reason)
                out(f"초안 {number}을 가장 좋다고 기록했다.")
            elif cmd == "f":
                number, _, text = rest.partition(" ")
                session.comment(int(number), text)
                out(f"초안 {number}에 피드백을 기록했다.")
            elif cmd == "g":
                candidates = session.propose_guidance()
                if not candidates:
                    out("새로 뽑을 피드백이 없거나 후보가 없다. p 또는 f로 먼저 피드백을 남겨 주세요.")
                    continue
                for i, c in enumerate(candidates, 1):
                    out(f"  {i}. [{c.scope}] {c.text}\n     근거: {c.reason}")
                answer = input_fn("승인할 번호(쉼표로, all, 엔터=없음): ").strip()
                chosen = list(range(1, len(candidates) + 1)) if answer == "all" else _numbers(answer)
                approved = session.approve(chosen)
                if approved:
                    out(f"지침 {len(approved)}개를 적용한다(일반은 {GUIDANCE_PATH}에 저장). 다음 초안부터 반영된다. n 또는 m 으로 확인하세요.")
                else:
                    out("승인한 지침이 없다.")
            else:
                out("모르는 명령이다. h로 도움말을 본다.")
        except (ValueError, IndexError) as exc:
            out(f"입력 오류: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("posting", type=Path, help="공고 본문 텍스트 파일(상태 파일 이름을 찾는 데만 쓴다)")
    parser.add_argument("--question", required=True, help="자소서 문항(분량 제한은 '800자'처럼 적는다)")
    parser.add_argument("--n", type=int, default=3, help="한 번에 쓰는 초안 개수")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    state_path = MAP_DIR / f"{args.posting.stem}.json"
    if not state_path.exists():
        sys.exit(f"상태 파일이 없다: {state_path}\n먼저 scripts/run_writer_demo.py로 분석·매핑·검증을 돌려 상태를 만든다.")
    item, mappings, verdicts = load_state(json.loads(state_path.read_text(encoding="utf-8")))

    from modular_agentic.engines.strands.guidance import StrandsGuidanceProposer
    from modular_agentic.engines.strands.writer import StrandsWriter

    session = FeedbackSession(
        item=item,
        question=args.question,
        mappings=mappings,
        verdicts=verdicts,
        writer_factory=lambda guidance: StrandsWriter(guidance=guidance),
        proposer=StrandsGuidanceProposer(),
        store=FeedbackStore(FEEDBACK_DIR / f"{item.id}.jsonl", GUIDANCE_PATH),
        n=args.n,
        brief=load_approved_brief(item.id),
    )
    saved = session.store.guidance()
    print(f"저장된 일반 지침 {len(saved.splitlines()) if saved else 0}개를 쓴다({GUIDANCE_PATH})")
    run_loop(session)


if __name__ == "__main__":
    main()
