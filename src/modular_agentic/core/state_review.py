"""앞 단계(요구 매핑·검증) 결과를 사람이 읽는 마크다운으로 바꾼다.

저장 파일은 JSON이 원본(logs/03_map/<공고>.json)이고 마크다운(.md)은 읽는 용이다. 분석·조사·소재 계획·초안은 JSON과 함께 읽는 용
문서를 만드는데 이 단계만 JSON뿐이라 사용자가 근거를 읽으려면 터미널 요약에 기대야 했다.
이 함수는 모델을 부르지 않고 저장된 결과만 읽으므로 `mark`로 기본 요건 표시가 바뀐 뒤에 다시 만들어도 비용이 없다.
"""
from __future__ import annotations

from collections.abc import Collection, Sequence

from .types import Item, Mapping, Verdict

REQUIREMENT_PREVIEW_CHARS = 60  # 요약 표에서 요구 본문을 이만큼만 보인다(전체는 아래 항목별 절에 있다)
EXCERPT_CHARS = 220


def _cell(text: str) -> str:
    """마크다운 표 칸에 넣을 글. 칸을 깨는 `|`와 줄바꿈을 정리한다."""
    return " ".join(text.split()).replace("|", "\\|")


def render_state_md(
    item: Item,
    mappings: Sequence[Mapping],
    verdicts: Sequence[Verdict],
    basic_requirement_ids: Collection[str] = (),
    vault_rev: str = "",
) -> str:
    """요구마다 상태·판단·근거(위치와 발췌)·검증 플래그를 보인다. 기본 요건은 뒤쪽에 참고용으로 모은다.

    근거의 있고 없음·수준만 보여 주고 글감으로 좋은지는 보여 주지 않는다.
    """
    basic = set(basic_requirement_ids)
    by_req = {v.requirement_id: v for v in verdicts}
    rows = []
    for number, m in enumerate(mappings, 1):
        verdict = by_req.get(m.requirement.id)
        rows.append((number, m, verdict, verdict is not None and verdict.kind.value != "승인"))

    flagged = sum(1 for *_, f in rows if f)
    lines = [
        f"# 요구 매핑·검증 결과 — {item.id}",
        "",
        f"vault 상태: `{vault_rev or '기록 없음'}`. 요구 {len(rows)}개 중 기본 요건 {len(basic & {m.requirement.id for m in mappings})}개, 검증 플래그 {flagged}개.",
        "근거의 있고 없음·수준이지 글감의 좋고 나쁨이 아니다. 검증 플래그는 경험이 거짓이라는 뜻이 아니라 서술이 실제보다 크게 읽힐 수 있다는 표시다.",
        "**기본 요건**은 어필·검증하지 않기로 사용자가 `analyze_posting.py mark`로 표시한 요구다. 표시를 바꾸면 이 문서는 `run_writer_demo.py`를 다시 실행할 때 갱신된다(모델 호출 없음).",
        "",
        "| # | 구분 | 요구 | 상태 | 검증 |",
        "|---|---|---|---|---|",
    ]
    for number, m, _, is_flagged in rows:
        text = m.requirement.text
        preview = text[:REQUIREMENT_PREVIEW_CHARS] + ("…" if len(text) > REQUIREMENT_PREVIEW_CHARS else "")
        kind = "기본" if m.requirement.id in basic else "어필"
        lines.append(f"| {number} | {kind} | {_cell(preview)} | {m.status.value} | {'⚑ 플래그' if is_flagged else '승인'} |")
    lines.append("")

    for label, want_basic in (("어필 대상 요구", False), ("기본 요건(참고)", True)):
        section = [r for r in rows if (r[1].requirement.id in basic) == want_basic]
        if not section:
            continue
        lines += [f"## {label}", ""]
        for number, m, verdict, is_flagged in section:
            lines += [f"### {number}. {m.requirement.text} — {m.status.value}{' ⚑' if is_flagged else ''}", ""]
            lines += [f"**판단**: {m.note}", ""]
            if is_flagged and verdict is not None:
                lines += [f"**플래그 이유**: {verdict.reason}", ""]
            if m.evidence:
                lines.append("**근거**")
                lines += [f"- `{e.ref}` — {' '.join(e.excerpt.split())[:EXCERPT_CHARS]}" for e in m.evidence]
            else:
                lines.append("**근거**: 없음")
            lines.append("")
    return "\n".join(lines)
