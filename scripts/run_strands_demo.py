"""공고 하나를 Strands 엔진으로 분석(Analyzer)하고 매핑(Mapper)해서 보여 준다.

사용: uv run --extra strands python scripts/run_strands_demo.py samples/db_inc_sw_engineer_ax.txt [--max 3]
      (윈도우에서는 PYTHONUTF8=1 필요. 프로필·리전·모델은 check_bedrock.py와 같은 환경변수)
검색 호출도 남기려면 `MAS_SEARCH_LOG=logs/search/<이름>.jsonl`을 함께 준다.
검색 방식: `MAS_QMD_URL`(예: http://localhost:8181)이 있고 그 qmd 상시 서버가 응답하면 서버로(호출당 약 0.07초, 집 PC 기준),
아니면 `qmd vsearch`를 호출마다 새 프로세스로(약 13~22초) 한다. 시작할 때 어느 쪽으로 가는지 출력한다.
윈도우 집 PC에서는 서버가 SSH 접속과 함께 종료되므로 scripts/with_qmd_server.ps1 뒤에 이 명령을 붙여 한 번에 돌린다.

--max는 매핑할 요구의 개수다(기본 3). 요구마다 모델 호출과 vault 검색이 여러 번 일어나서 시간과 비용이 든다.
--no-exclude를 주면 제외 목록(자소서 폴더 등)을 비우고 vault 전체를 검색한다. 기본은 제외 목록을 적용한다.
졸업작품·공공기관 공모전처럼 자소서에만 자세히 적힌 경험이 TODO로 나오는지 보려면 두 방식으로 한 번씩 돌려 비교한다.
(저장 파일 이름에 `-all`이 붙고, 검색 로그 태그는 `mapper-all:<요구 id>`가 된다.)
결과는 화면에 표로 보여 주고 logs/runs/<시각>.json에도 저장한다(logs/는 git 제외).
--only N은 N번째 요구 하나만(1부터) 처리한다. --max와 함께 쓰면 --only가 우선한다.
--verify를 주면 매핑 뒤에 검증(Verifier)을 한다: 근거가 말하는 경험을 vault 전체(제외 목록 없이)에서 다시 모아 모순·과장을 찾는다.
요구마다 모델 호출과 검색이 더 든다. 후보 경험은 eval 골든셋 v2의 경험 목록이다. 후보에 없어 모델이 직접 정한 경험은 결과에 남는다.
작성(Writer)은 아직 없다.
"""
import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from modular_agentic.core.types import Item
from modular_agentic.engines.strands.analyzer import StrandsAnalyzer
from modular_agentic.engines.strands.mapper import StrandsMapper
from common import load_known_experiences
from modular_agentic.engines.strands.verifier import StrandsVerifier
from modular_agentic.instances.cover_letter.config import VAULT_DIR
from modular_agentic.tools.vault_revision import stamp_environment
from modular_agentic.tools.vault_search import current_backend

RUNS_DIR = Path("logs") / "runs"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("posting", type=Path, help="공고 본문 텍스트 파일")
    parser.add_argument("--max", type=int, default=3, help="매핑할 요구 개수")
    parser.add_argument("--only", type=int, help="N번째 요구 하나만 처리한다(1부터)")
    parser.add_argument("--verify", action="store_true", help="매핑 뒤에 검증(Verifier)까지 한다")
    parser.add_argument("--no-exclude", action="store_true", help="제외 목록을 비우고 자소서 폴더 등 vault 전체를 검색한다")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    vault_rev = stamp_environment(VAULT_DIR)
    print(f"검색 방식: {current_backend()} / vault 상태: {vault_rev}")
    body = args.posting.read_text(encoding="utf-8")
    item = Item(id=args.posting.stem, source="file", title=body.splitlines()[0], body=body)

    start = time.monotonic()
    requirements = StrandsAnalyzer().analyze(item)
    print(f"분석: 요구 {len(requirements)}개 ({time.monotonic() - start:.1f}초)")
    for r in requirements:
        print(f"  - [{r.kind or '분류 없음'}] {r.text}")

    chosen = [requirements[args.only - 1]] if args.only else requirements[: args.max]
    scope = "제외 목록 비움(vault 전체)" if args.no_exclude else "제외 목록 적용"
    print(f"\n매핑: 앞 {len(chosen)}개만 진행 — {scope}")
    mapper = (
        StrandsMapper(exclude_prefixes=(), tag="mapper-all") if args.no_exclude else StrandsMapper(tag="mapper")
    )
    verifier = StrandsVerifier(load_known_experiences()) if args.verify else None
    results = []
    for requirement in chosen:
        start = time.monotonic()
        [mapping] = mapper.map([requirement])
        sec = time.monotonic() - start
        print(f"\n■ {requirement.text}  →  {mapping.status.value}  ({sec:.1f}초)")
        print(f"  판단: {mapping.note}")
        stats = mapper.stats[requirement.id]
        model_part = f"모델 응답 {stats.model_seconds:.1f}초" if stats.model_seconds is not None else "모델 응답 시간 알 수 없음"
        token_part = (
            f", 토큰 입력 {stats.input_tokens}/출력 {stats.output_tokens}" if stats.input_tokens is not None else ""
        )
        print(
            f"  시간: 검색 {stats.search_calls}번(합 {stats.search_sum_seconds:.1f}초, "
            f"겹침 반영 {stats.search_wall_seconds:.1f}초), {model_part}{token_part}, 루프 {stats.cycles}회"
        )
        for e in mapping.evidence:
            print(f"  근거 {e.ref}")
            print(f"    {e.excerpt[:140]}")
        verdict_info = None
        if verifier is not None:
            start = time.monotonic()
            [verdict] = verifier.verify([mapping])
            vsec = time.monotonic() - start
            print(f"  검증: {verdict.kind.value}  ({vsec:.1f}초)")
            print(f"    {verdict.reason}")
            vstats = verifier.stats.get(requirement.id)
            if vstats is not None:
                print(
                    f"    시간: 검색 {vstats.search_calls}번, 모델 응답 "
                    f"{vstats.model_seconds if vstats.model_seconds is None else round(vstats.model_seconds, 1)}초, "
                    f"토큰 입력 {vstats.input_tokens}/출력 {vstats.output_tokens}"
                )
            verdict_info = {
                "kind": verdict.kind.value,
                "reason": verdict.reason,
                "seconds": round(vsec, 1),
                "stats": asdict(vstats) if vstats is not None else None,
            }
        results.append(
            {
                "requirement": requirement.text,
                "kind": requirement.kind,
                "status": mapping.status.value,
                "note": mapping.note,
                "evidence": [{"ref": e.ref, "excerpt": e.excerpt} for e in mapping.evidence],
                "seconds": round(sec, 1),
                "stats": asdict(stats),
                "verdict": verdict_info,
            }
        )

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{item.id}{'-all' if args.no_exclude else ''}.json"
    payload = {"item": item.id, "scope": scope, "vault_rev": vault_rev, "results": results}
    if verifier is not None:
        payload["extracted_experiences"] = [asdict(x) for x in verifier.extracted]
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
