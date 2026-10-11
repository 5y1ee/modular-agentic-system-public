"""vault 페이지의 지정한 줄을 근거(Evidence)로 읽는다. 검색 없이 `경로:줄`로 바로 읽는다.

쓰는 곳: 사용자가 소재로 직접 지정한 근거(scripts/make_plan.py --extra-ref), 소재 계획의 근거를 작성 단계가 읽을 때(매핑 결과에 없는 근거).
읽기 전용이다. vault 밖의 경로는 읽지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from modular_agentic.core.types import Evidence

READ_LINES = 6  # 지정한 줄부터 읽는 줄 수
EXCERPT_CHARS = 400  # 발췌의 최대 글자 수(매핑이 쓰는 EXCERPT_CHARS와 같다)
TRUNCATED_MARK = " …(잘림)"  # 발췌가 EXCERPT_CHARS에서 잘렸다는 표시. 소재 계획·작성이 보는 발췌 끝에 붙고 make_plan이 경고로 올린다


def evidence_from_refs(refs: list[str], vault_dir: Path) -> list[Evidence]:
    """`경로:줄`을 읽어 Evidence로 만든다. 모양이 틀렸거나 vault 밖이거나 파일·줄이 없으면 ValueError(엉뚱한 근거가 들어가지 않게)."""
    found = []
    root = vault_dir.resolve()
    for ref in refs:
        path_text, _, line_text = ref.rpartition(":")
        if not path_text or not line_text.isdigit():
            raise ValueError(f"'경로:줄' 모양이어야 한다: {ref}")
        path = (vault_dir / path_text).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"vault 안에 없는 파일이다: {path_text}")
        lines = path.read_text(encoding="utf-8").splitlines()
        start = int(line_text)
        if not 1 <= start <= len(lines):
            raise ValueError(f"{path_text}는 {len(lines)}줄이다. 줄 번호가 범위를 벗어난다: {start}")
        text = " ".join(" ".join(lines[start - 1 : start - 1 + READ_LINES]).split())
        excerpt = text[:EXCERPT_CHARS] + TRUNCATED_MARK if len(text) > EXCERPT_CHARS else text
        found.append(Evidence(ref=ref, excerpt=excerpt))
    return found
