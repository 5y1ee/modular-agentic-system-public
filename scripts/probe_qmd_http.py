"""qmd 상시 서버(HTTP)의 `/query` 응답 시간과 결과를 잰다 (docs/experiments/002 3~5절).

사용(서버를 띄운 기기에서):
    qmd mcp --http --daemon          # localhost:8181 에 상시 서버를 띄운다 (끄기: qmd mcp stop)
    (윈도우 집 PC에서 SSH로 할 때는 접속이 끝나면 서버가 같이 종료되므로 시작·실행·종료를 한 번에 하는
     scripts/with_qmd_server.ps1 뒤에 이 명령을 붙여 쓴다)
    MAS_SEARCH_LOG=logs/search/qmd-http.jsonl uv run python scripts/probe_qmd_http.py
    uv run python -m modular_agentic.eval.experience_recall logs/search/qmd-http.jsonl            # 리랭킹 없음
    uv run python -m modular_agentic.eval.experience_recall logs/search/qmd-http.jsonl --tag recall-rerank

골든셋 v2의 질의마다 `/query`에 `vec` 하위 질의 하나를 보낸다(서버가 질의 확장을 하지 않는다).
  1) rerank=false 첫 호출   2) 같은 호출 한 번 더(따뜻한 상태)   3) rerank=true
응답 시간과 상위 결과를 보여 주고, MAS_SEARCH_LOG가 있으면 기존 검색 로그와 같은 형식으로 남긴다(tools/vault_search가 남김).
  rerank=false → 태그 `recall`, 방식 `http-vec`  /  rerank=true → 태그 `recall-rerank`, 방식 `http-vec-rerank`
그래서 `vsearch` 로그와 같은 채점기로 점수를 비교할 수 있다.

주의: 이 경로는 `vsearch`가 아니라 구조화 검색(RRF 결합, 조각 선택, 선택적 리랭킹)이다. 같은 질의여도 순위가 다를 수 있다.
서버는 인증이 없고 localhost에만 묶인다. 읽기 전용이다.
"""
import argparse
import json
import sys
import time

from modular_agentic.eval.experience_recall import GOLDEN_V2_PATH
from modular_agentic.tools import vault_search as vs

LIMIT = 60  # 제외 관점을 적용해도 상위 10건을 채울 만큼 넉넉히 받는다


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # 윈도우에서는 localhost가 IPv6(::1)로만 열려 127.0.0.1로는 닿지 않는다(2026-10-04 확인). localhost는 둘 다 시도한다.
    parser.add_argument("--url", default="http://localhost:8181")
    parser.add_argument(
        "--skip-rerank",
        action="store_true",
        help="rerank 켠 호출을 건너뛴다. GPU가 없는 서버(CPU)에서는 리랭킹이 20조각에 약 9분 걸려서 꼭 쓴다",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    print(f"서버 상태: {json.dumps(vs._http_get_json(args.url.rstrip('/') + '/health'))}")
    queries = [case["query"] for case in json.loads(GOLDEN_V2_PATH.read_text(encoding="utf-8"))["cases"]]
    cold, warm, reranked = [], [], []
    for query in queries:
        print(f"\n질의: {query}")
        for label, rerank, tag in (
            ("rerank 끔(첫 호출)", False, "recall"),
            ("rerank 끔(두 번째)", False, "probe-warm"),
            ("rerank 켬", True, "recall-rerank"),
        ):
            if rerank and args.skip_rerank:
                continue
            start = time.monotonic()
            hits = vs.search_http_raw(query, LIMIT, base_url=args.url, rerank=rerank, tag=tag)
            elapsed = time.monotonic() - start
            (cold if tag == "recall" else warm if tag == "probe-warm" else reranked).append(elapsed)
            top = ", ".join(f"{h.path.split('/')[-1][:16]}:{h.line}" for h in hits[:4])
            print(f"  {label}: {elapsed:5.2f}초  결과 {len(hits)}건  상위: {top}")

    def avg(values: list[float]) -> str:
        return f"{sum(values) / len(values):.2f}초 (최소 {min(values):.2f}, 최대 {max(values):.2f})"

    rerank_part = f" / rerank 켬 {avg(reranked)}" if reranked else " (rerank 켠 호출은 건너뜀)"
    print(f"\n평균 응답 시간 — rerank 끔 첫 호출 {avg(cold)} / 두 번째 {avg(warm)}{rerank_part}")


if __name__ == "__main__":
    main()
