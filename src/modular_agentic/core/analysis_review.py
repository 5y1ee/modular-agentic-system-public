"""분석 결과를 저장하고, 사용자가 읽고 기본 요건을 표시하게 하는 부분(설계 문서 25절).

저장 파일은 JSON이 원본(logs/02_analyze/<공고>.json)이고 마크다운(.md)은 읽는 용이다. 이 단계에는 승인 절차가 없다.
사용자는 읽는 용 문서의 요구 번호를 보고 `mark`로 "기본 요건"(어필·검증하지 않을 요구)을 표시한다. 소재 계획이 그 표시를 기본값으로 쓴다.
회사 맥락(요구 옆에 붙은 조사 주장)은 참고일 뿐 지원자의 근거가 아니다. 억지 연결이 있으면 이 문서에서 눈으로 거른다.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from .serialize import analysis_from_dict, analysis_to_dict
from .types import Analysis, Brief, ContextNote

CLAIM_PREVIEW_CHARS = 70  # 읽는 용 문서에서 맥락의 주장 본문을 이만큼만 보인다(전체는 조사 보고서에 있다)


def analysis_path(directory: Path, item_id: str) -> Path:
    return directory / f"{item_id}.json"


def save_analysis(path: Path, analysis: Analysis, brief: Brief | None = None) -> None:
    """JSON 원본과 읽는 용 .md를 함께 쓴다. brief를 주면 맥락의 주장 본문도 문서에 보인다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analysis_to_dict(analysis), ensure_ascii=False, indent=2), encoding="utf-8")
    path.with_suffix(".md").write_text(render_analysis_md(analysis, brief), encoding="utf-8")


def load_analysis(path: Path) -> Analysis | None:
    """저장된 분석 결과. 파일이 없으면 None."""
    if not path.exists():
        return None
    return analysis_from_dict(json.loads(path.read_text(encoding="utf-8")))


def mark_basic(analysis: Analysis, numbers: Sequence[int]) -> Analysis:
    """요구 번호(1부터)를 기본 요건으로 표시한다. 이전 표시는 이 목록으로 바뀐다. 범위를 벗어난 번호는 ValueError."""
    total = len(analysis.requirements)
    bad = [n for n in numbers if not 1 <= n <= total]
    if bad:
        raise ValueError(f"요구 번호가 범위를 벗어난다: {bad} (요구는 1~{total})")
    return replace(analysis, basic_requirement_ids=[analysis.requirements[n - 1].id for n in dict.fromkeys(numbers)])


def with_context(analysis: Analysis, context: dict[str, list[ContextNote]], warnings: Sequence[str] = ()) -> Analysis:
    """회사 맥락을 새로 붙인다(이전 맥락은 대체). 경고는 이전 것에 더한다."""
    return replace(analysis, context=context, warnings=[*analysis.warnings, *warnings])


def render_analysis_md(analysis: Analysis, brief: Brief | None = None) -> str:
    claims = {c.id: c for c in brief.claims} if brief is not None else {}
    basic = set(analysis.basic_requirement_ids)
    lines = [f"# 분석 결과 — {analysis.item_id}", "",
             f"요구 {len(analysis.requirements)}개, 기본 요건 표시 {len(basic)}개, 회사 맥락이 붙은 요구 {len(analysis.context)}개.", "",
             "요구는 공고에서만 뽑은 것이다. **기본 요건**은 어필·검증하지 않을 요구이고 `analyze_posting.py mark <공고id> --basic 1,2,9`로 표시한다(소재 계획이 기본값으로 쓴다).",
             "**회사 맥락**은 사용자가 승인한 조사 보고서의 주장 중 그 요구와 관련 있다고 모델이 고른 것이다. 참고일 뿐 내 근거가 아니고, 억지 연결이면 무시한다.", ""]
    for w in analysis.warnings:
        lines.append(f"- ⚠ {w}")
    if analysis.warnings:
        lines.append("")
    lines.append("## 요구 목록")
    lines.append("")
    for i, r in enumerate(analysis.requirements, 1):
        kind = f"[{r.kind}] " if r.kind else ""
        lines.append(f"{i}. {kind}{r.text}" + (" — **기본(어필 안 함)**" if r.id in basic else ""))
        for n in analysis.context.get(r.id, []):
            claim = claims.get(n.claim_id)
            shown = f" — 주장: {claim.text[:CLAIM_PREVIEW_CHARS]}{'…' if len(claim.text) > CLAIM_PREVIEW_CHARS else ''}" if claim else ""
            lines.append(f"   - 회사 맥락 `{n.claim_id}`: {n.reason}{shown}")
    return "\n".join(lines) + "\n"
