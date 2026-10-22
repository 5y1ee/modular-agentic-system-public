"""항목(회사 가치, 문항 기조 등)마다 내 경험 근거를 vault에서 찾아, 어느 항목에 근거가 탄탄한지 후보를 보여 준다.

왜 필요한가: "가치 중 하나를 골라 그것을 실천한 경험을 쓰라"는 문항은 가치를 먼저 고르는 일이 글의 절반이다.
공고의 요구를 매핑하는 기능(StrandsMapper·StrandsVerifier)을 항목에도 그대로 쓴다. 항목마다 요구를 하나 만들어
vault를 검색하고, 확인·부분확인·근거 없음과 검증 결과(과장 플래그)를 보여 준다.

입력은 줄마다 `이름 — 정의` 모양인 파일이다(모델을 쓰지 않는다). 이름은 한글·영문·숫자 아무거나 되고 40자까지다.
`#`·`>`·`-`·`*`·`|`로 시작하는 줄과 40자를 넘는 이름은 설명 줄로 보고 건너뛴다. 설명 줄에 ` — `가 있으면 항목으로 읽힐 수 있으니 파일에 두지 않는다.
결과는 **근거의 있고 없음과 수준**이지 글감의 좋고 나쁨이 아니다. 어느 항목을 쓸지는 사용자가 고른다.

두 가지 종류가 있다(`--kind`).
- `value`(기본): 회사 가치. 사용자가 공식 페이지에서 복사한 가치 파일. 요구를 "Talent Value '이름'을 실천한 경험 — 정의"로 만든다.
- `topic`: 문항 기조·역량 같은 가치가 아닌 항목. 요구를 "'이름' — 정의"로 만든다(정의가 어떤 경험인지를 스스로 말해야 한다).
  결과 표기도 "가치"·"공식 문구" 대신 "항목"·"항목의 뜻"을 쓴다.

비용: 항목 하나에 Bedrock 호출 여러 번(매핑 + 검증). 항목 6개에 몇 분. qmd 서버가 필요하다(with_qmd_server.sh).
예: AWS_PROFILE=user scripts/with_qmd_server.sh uv run --extra strands --extra sources python scripts/map_values.py \\
      inputs/hyundai_autoever_2026q4_backend/talent_value.md --id hyundai_autoever_2026q4_backend
    (문항 기조 등 가치가 아닌 항목) ... map_values.py inputs/<공고>_intents.md --id <공고> --kind topic
결과는 logs/03_map/<id>_values.json(원본)과 <id>_values.md(읽는 용)에 저장한다(자소서 내용이 들어 있어 git에 올리지 않는다).
이미 저장된 결과를 다시 읽기 좋게 만들려면(Bedrock 호출 없음): scripts/map_values.py --render --id <공고>
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from common import MAP_DIR, load_known_experiences
from modular_agentic.core.serialize import dump_state, load_state
from modular_agentic.core.types import Item, Mapping, Requirement, Verdict
from modular_agentic.registry import make

VALUE_KIND = "핵심 가치"
TOPIC_KIND = "항목"
_MAX_NAME_LEN = 40
# 이름은 ` — `(공백 둘레의 긴 줄표) 앞의 글자 전부다. 목록·표·인용·제목 줄은 설명 줄이라 이름으로 읽지 않는다.
_VALUE_LINE = re.compile(r"^\s*([^\s#>*|\-—][^—]*?)\s+—\s+(.+?)\s*$")


def parse_values(text: str) -> list[tuple[str, str]]:
    """`이름 — 정의` 줄을 (이름, 정의)로 읽는다. 제목·빈 줄·긴 설명 줄은 건너뛴다."""
    found = []
    for line in text.splitlines():
        match = _VALUE_LINE.match(line)
        if match and len(match.group(1).strip()) <= _MAX_NAME_LEN:
            found.append((match.group(1).strip(), match.group(2).strip()))
    return found


def values_to_requirements(item_id: str, values: list[tuple[str, str]], kind: str = VALUE_KIND) -> list[Requirement]:
    """항목마다 요구를 하나 만든다. 정의를 같이 넣어 검색이 의미를 알 수 있게 한다.

    가치(`VALUE_KIND`)는 "이 가치를 실천한 경험"으로 만들고, 가치가 아닌 항목(`TOPIC_KIND`)은 이름과 정의만 쓴다
    (정의가 어떤 경험인지 스스로 말해야 한다).
    """
    def text(name: str, definition: str) -> str:
        return f"Talent Value '{name}'을 실천한 경험 — {definition}" if kind == VALUE_KIND else f"'{name}' — {definition}"

    return [Requirement(id=f"{item_id}-v{i}", item_id=item_id, text=text(name, definition), kind=kind) for i, (name, definition) in enumerate(values, 1)]


_NAME_IN_REQUIREMENT = re.compile(r"(?:Talent Value )?'(.+)'(?:을 실천한 경험)?")


def value_name(requirement: Requirement) -> str:
    """values_to_requirements가 만든 요구 문구에서 이름을 되읽는다(두 종류 모두). 못 읽으면 요구 문구 앞부분."""
    head, _, _ = requirement.text.partition(" — ")
    found = _NAME_IN_REQUIREMENT.fullmatch(head.strip())
    return found.group(1) if found else requirement.text[:30]


def value_definition(requirement: Requirement) -> str:
    """요구 문구("… — 정의")에서 정의 문구를 꺼낸다. 없으면 빈 문자열."""
    _, _, definition = requirement.text.partition(" — ")
    return definition.strip()


def render_values_md(mappings: list[Mapping], verdicts: list[Verdict], title: str | None = None) -> str:
    """항목마다 상태·판단·근거(위치와 발췌)·검증 플래그를 사람이 읽는 마크다운으로 만든다.

    근거의 있고 없음·수준만 보여 준다. 글감으로 좋은지는 보여 주지 않는다. 어느 항목을 쓸지는 사용자가 고른다.
    표기는 요구의 종류를 따른다: 회사 가치(`VALUE_KIND`)만 있으면 "가치"·"공식 문구", 그 밖이면 "항목"·"항목의 뜻".
    ("공식 문구"는 가치 파일을 공식 페이지에서 복사했다는 전제라서 가치가 아닌 항목에는 붙이지 않는다.)
    """
    is_value = all(m.requirement.kind == VALUE_KIND for m in mappings)
    noun, meaning = ("가치", "가치의 뜻(공식 문구)") if is_value else ("항목", "항목의 뜻")
    title = title or ("회사 가치별 근거 후보" if is_value else "항목별 근거 후보")
    by_req = {v.requirement_id: v for v in verdicts}
    order = {"확인": 0, "부분확인": 1, "TODO": 2}
    lines = [f"# {title}", "", f"근거의 있고 없음과 수준이지 글감의 좋고 나쁨이 아니다. 어느 {noun}을 쓸지는 사용자가 고른다.", "", f"| {noun} | 상태 | 검증 |", "|---|---|---|"]
    for m in sorted(mappings, key=lambda m: order.get(m.status.value, 3)):
        v = by_req.get(m.requirement.id)
        lines.append(f"| {value_name(m.requirement)} | {m.status.value} | {'⚑ 플래그' if v is not None and v.kind.value != '승인' else '승인'} |")
    lines.append("")
    for m in mappings:
        v = by_req.get(m.requirement.id)
        flagged = v is not None and v.kind.value != "승인"
        lines += [f"## {value_name(m.requirement)} — {m.status.value}{' ⚑ 검증 플래그' if flagged else ''}", ""]
        if value_definition(m.requirement):
            lines += [f"**{meaning}**: {value_definition(m.requirement)}", ""]
        lines += [f"**내 경험 근거 판단**: {m.note}", ""]
        if flagged:
            lines += [f"**플래그 이유**: {v.reason}", ""]
        if m.evidence:
            lines.append("**근거**")
            lines += [f"- `{e.ref}` — {' '.join(e.excerpt.split())[:220]}" for e in m.evidence]
        else:
            lines.append("**근거**: 없음")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("values_file", type=Path, nargs="?", help="`이름 — 정의` 줄이 있는 파일(--render면 필요 없다)")
    parser.add_argument("--id", required=True, help="공고 id(결과 파일 이름에 쓴다)")
    parser.add_argument("--kind", choices=["value", "topic"], default="value", help="value: 회사 가치(기본, '~을 실천한 경험'으로 요구를 만든다). topic: 문항 기조 같은 가치가 아닌 항목('이름 — 정의'를 그대로 쓴다)")
    parser.add_argument("--render", action="store_true", help="저장된 결과(logs/03_map/<id>_values.json)를 읽는 용 마크다운으로 다시 만든다(Bedrock 호출 없음)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.render:
        path = MAP_DIR / f"{args.id}_values.json"
        if not path.exists():
            sys.exit(f"저장된 결과가 없다: {path}")
        _, mappings, verdicts = load_state(json.loads(path.read_text(encoding="utf-8")))
        out = MAP_DIR / f"{args.id}_values.md"
        out.write_text(render_values_md(mappings, verdicts), encoding="utf-8")
        print(f"저장: {out}")
        return
    if args.values_file is None:
        parser.error("가치 파일이 필요하다(저장된 결과를 다시 만들려면 --render)")

    values = parse_values(args.values_file.read_text(encoding="utf-8"))
    if not values:
        sys.exit("`이름 — 정의` 모양의 줄을 찾지 못했다.")
    item_id = f"{args.id}_values"
    requirements = values_to_requirements(item_id, values, VALUE_KIND if args.kind == "value" else TOPIC_KIND)
    print(f"{'가치' if args.kind == 'value' else '항목'} {len(values)}개: " + ", ".join(name for name, _ in values))

    start = time.monotonic()
    mappings = make("mapper").map(requirements)
    print(f"매핑: {time.monotonic() - start:.1f}초")
    start = time.monotonic()
    verdicts = make("verifier", experiences=load_known_experiences()).verify(mappings)
    print(f"검증: {time.monotonic() - start:.1f}초")

    by_req = {v.requirement_id: v for v in verdicts}
    print(f"\n{'가치' if args.kind == 'value' else '항목'}별 근거 후보 (근거의 있고 없음·수준이지 글감의 좋고 나쁨이 아니다)")
    for (name, _), m in zip(values, mappings):
        v = by_req.get(m.requirement.id)
        flag = "  ⚑ 검증 플래그" if v is not None and v.kind.value != "승인" else ""
        print(f"\n■ {name} — {m.status.value}{flag}")
        print(f"  판단: {m.note}")
        for e in m.evidence[:4]:
            print(f"  - [{e.ref}] {' '.join(e.excerpt.split())[:150]}")
        if flag:
            print(f"  플래그 이유: {v.reason}")

    state = dump_state(Item(id=item_id, source="values", title=f"{args.id} {'Talent Value' if args.kind == 'value' else '항목'}", body=""), mappings, verdicts)
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    out = MAP_DIR / f"{item_id}.json"
    out.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {out}")
    md_out = MAP_DIR / f"{item_id}.md"
    md_out.write_text(render_values_md(mappings, verdicts), encoding="utf-8")
    print(f"읽는 용: {md_out}")


if __name__ == "__main__":
    main()
