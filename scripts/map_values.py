"""회사의 가치(Talent Value 등)마다 내 경험 근거를 vault에서 찾아, 어느 가치에 근거가 탄탄한지 후보를 보여 준다.

왜 필요한가: "가치 중 하나를 골라 그것을 실천한 경험을 쓰라"는 문항은 가치를 먼저 고르는 일이 글의 절반이다.
공고의 요구를 매핑하는 기능(StrandsMapper·StrandsVerifier)을 가치에도 그대로 쓴다. 가치마다 "이 가치를 실천한 경험"을 요구로 삼아
vault를 검색하고, 확인·부분확인·근거 없음과 검증 결과(과장 플래그)를 보여 준다.

입력은 사용자가 공식 페이지에서 복사한 가치 파일이다. 줄마다 `이름 — 정의` 모양이면 읽는다(모델을 쓰지 않는다).
결과는 **근거의 있고 없음과 수준**이지 글감의 좋고 나쁨이 아니다. 어느 가치를 쓸지는 사용자가 고른다.

비용: 가치 하나에 Bedrock 호출 여러 번(매핑 + 검증). 가치 6개에 몇 분. qmd 서버가 필요하다(with_qmd_server.sh).
예: AWS_PROFILE=user scripts/with_qmd_server.sh uv run --extra strands --extra sources python scripts/map_values.py \\
      inputs/hyundai_autoever_2026q4_backend/talent_value.md --id hyundai_autoever_2026q4_backend
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

_VALUE_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z' ]*[A-Za-z])\s+—\s+(.+?)\s*$")


def parse_values(text: str) -> list[tuple[str, str]]:
    """`Name — 정의` 줄을 (이름, 정의)로 읽는다. 제목·빈 줄·다른 줄은 건너뛴다."""
    found = []
    for line in text.splitlines():
        match = _VALUE_LINE.match(line)
        if match:
            found.append((match.group(1).strip(), match.group(2).strip()))
    return found


def values_to_requirements(item_id: str, values: list[tuple[str, str]]) -> list[Requirement]:
    """가치마다 "이 가치를 실천한 경험"을 요구로 만든다. 정의를 같이 넣어 검색이 의미를 알 수 있게 한다."""
    return [
        Requirement(id=f"{item_id}-v{i}", item_id=item_id, text=f"Talent Value '{name}'을 실천한 경험 — {definition}", kind="핵심 가치")
        for i, (name, definition) in enumerate(values, 1)
    ]


_NAME_IN_REQUIREMENT = re.compile(r"Talent Value '(.+?)'")


def value_name(requirement: Requirement) -> str:
    """values_to_requirements가 만든 요구 문구에서 가치 이름을 되읽는다. 못 읽으면 요구 문구 앞부분."""
    found = _NAME_IN_REQUIREMENT.search(requirement.text)
    return found.group(1) if found else requirement.text[:30]


def value_definition(requirement: Requirement) -> str:
    """요구 문구("Talent Value 'X'을 실천한 경험 — 정의")에서 공식 정의 문구를 꺼낸다. 없으면 빈 문자열."""
    _, _, definition = requirement.text.partition(" — ")
    return definition.strip()


def render_values_md(mappings: list[Mapping], verdicts: list[Verdict], title: str = "회사 가치별 근거 후보") -> str:
    """가치마다 상태·판단·근거(위치와 발췌)·검증 플래그를 사람이 읽는 마크다운으로 만든다.

    근거의 있고 없음·수준만 보여 준다. 글감으로 좋은지는 보여 주지 않는다. 어느 가치를 쓸지는 사용자가 고른다.
    """
    by_req = {v.requirement_id: v for v in verdicts}
    order = {"확인": 0, "부분확인": 1, "TODO": 2}
    lines = [f"# {title}", "", "근거의 있고 없음과 수준이지 글감의 좋고 나쁨이 아니다. 어느 가치를 쓸지는 사용자가 고른다.", "", "| 가치 | 상태 | 검증 |", "|---|---|---|"]
    for m in sorted(mappings, key=lambda m: order.get(m.status.value, 3)):
        v = by_req.get(m.requirement.id)
        lines.append(f"| {value_name(m.requirement)} | {m.status.value} | {'⚑ 플래그' if v is not None and v.kind.value != '승인' else '승인'} |")
    lines.append("")
    for m in mappings:
        v = by_req.get(m.requirement.id)
        flagged = v is not None and v.kind.value != "승인"
        lines += [f"## {value_name(m.requirement)} — {m.status.value}{' ⚑ 검증 플래그' if flagged else ''}", ""]
        if value_definition(m.requirement):
            lines += [f"**가치의 뜻(공식 문구)**: {value_definition(m.requirement)}", ""]
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
    parser.add_argument("values_file", type=Path, nargs="?", help="`이름 — 정의` 줄이 있는 가치 파일(--render면 필요 없다)")
    parser.add_argument("--id", required=True, help="공고 id(결과 파일 이름에 쓴다)")
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
    requirements = values_to_requirements(item_id, values)
    print(f"가치 {len(values)}개: " + ", ".join(name for name, _ in values))

    from modular_agentic.engines.strands.mapper import StrandsMapper
    from modular_agentic.engines.strands.verifier import StrandsVerifier

    start = time.monotonic()
    mappings = StrandsMapper().map(requirements)
    print(f"매핑: {time.monotonic() - start:.1f}초")
    start = time.monotonic()
    verdicts = StrandsVerifier(load_known_experiences()).verify(mappings)
    print(f"검증: {time.monotonic() - start:.1f}초")

    by_req = {v.requirement_id: v for v in verdicts}
    print("\n가치별 근거 후보 (근거의 있고 없음·수준이지 글감의 좋고 나쁨이 아니다)")
    for (name, _), m in zip(values, mappings):
        v = by_req.get(m.requirement.id)
        flag = "  ⚑ 검증 플래그" if v is not None and v.kind.value != "승인" else ""
        print(f"\n■ {name} — {m.status.value}{flag}")
        print(f"  판단: {m.note}")
        for e in m.evidence[:4]:
            print(f"  - [{e.ref}] {' '.join(e.excerpt.split())[:150]}")
        if flag:
            print(f"  플래그 이유: {v.reason}")

    state = dump_state(Item(id=item_id, source="values", title=f"{args.id} Talent Value", body=""), mappings, verdicts)
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    out = MAP_DIR / f"{item_id}.json"
    out.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {out}")
    md_out = MAP_DIR / f"{item_id}.md"
    md_out.write_text(render_values_md(mappings, verdicts), encoding="utf-8")
    print(f"읽는 용: {md_out}")


if __name__ == "__main__":
    main()
