"""질의 확장(H3)·"페이지 앞부분 통째 질의"·소요 시간 진단 (docs/experiments/001-vault-search-recall.md).

사용: uv run python scripts/probe_expansion.py   (윈도우에서는 PYTHONUTF8=1 필요)
결과를 파일로 남기려면 `MAS_SEARCH_LOG=logs/search/<이름>.jsonl`을 함께 준다.

정답 페이지 3곳마다 질의 두 종류를 만들어 각각 두 방법으로 검색한다.
  질의 종류
    sentence  본문에서 규칙으로 고른 한 줄 (known_item과 같은 규칙)
    head      페이지 앞부분 8줄을 한 줄로 이은 것. 임베딩이 만들어지는 조각에 가까운 질의다.
  방법
    A. `qmd vsearch <질의>`                      질의 확장을 거치는 기본 방식
    B. `qmd query "vec: <질의>" --no-rerank`     질의 확장·리랭킹 없이 벡터 검색만
  C. sentence의 vsearch를 질의 확장 캐시를 피하는 변형 문장으로 한 번 더 (시간만 본다)

head 질의로도 정답 페이지가 안 나오면 그 페이지의 벡터 자체를 의심하고, 나오면 한 줄 질의에서는
의미가 희석됐다고 볼 수 있다. 읽기 전용이다. CPU는 강제하지 않는다.
"""
import os
import re
import subprocess
import time

from modular_agentic.eval.known_item import pick
from modular_agentic.instances.cover_letter.config import VAULT_DIR
from modular_agentic.tools import vault_search as vs

# (vault 안 경로, pick()이 돌려준 문장 중 몇 번째)
CASES = [
    ("04_project/FINT.md", 0),  # 두 방식 모두 40위 밖이었던 페이지
    ("04_project/프로젝트A/프로젝트A.md", 0),  # vsearch에서 24위였던 페이지
    ("04_project/오토체스_RL_시뮬레이터.md", 1),  # vsearch에서 1위였던 대조군
]
FETCH_N = 40
HEAD_LINES = 8
HEAD_MAX_CHARS = 800


def head_text(text: str) -> str:
    """페이지 앞 HEAD_LINES줄을 공백으로 이어 한 줄로 만든다."""
    joined = " ".join(text.splitlines()[:HEAD_LINES])
    return re.sub(r"\s+", " ", joined).strip()[:HEAD_MAX_CHARS]


def run_qmd(args: list[str]) -> tuple[subprocess.CompletedProcess[str], float]:
    argv = vs.resolve_qmd_argv() + args
    start = time.monotonic()
    proc = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=dict(os.environ), timeout=600
    )
    return proc, time.monotonic() - start


def search_and_log(method: str, query: str, args: list[str], tag: str) -> tuple[list[vs.VaultHit] | None, float, str]:
    """qmd를 실행하고 로그를 남긴다. (결과 또는 None, 걸린 시간, 실패 메시지)를 돌려준다."""
    proc, sec = run_qmd(args)
    if proc.returncode != 0:
        return None, sec, f"실패(코드 {proc.returncode}): {proc.stderr[-120:].strip()}"
    hits = vs.parse_hits(proc.stdout)
    vs.append_log(query, hits, elapsed_sec=sec, method=method, tag=tag, fetch_n=FETCH_N)
    return hits, sec, ""


def describe(hits: list[vs.VaultHit] | None, error: str, path: str) -> str:
    if hits is None:
        return error
    paths = [h.path for h in hits]
    return f"{paths.index(path) + 1}위" if path in paths else f">{FETCH_N}위/없음"


def main() -> None:
    for path, idx in CASES:
        text = (VAULT_DIR / path).read_text(encoding="utf-8")
        sentences = pick(text)
        queries = {
            "sentence": sentences[min(idx, len(sentences) - 1)],
            "head": head_text(text),
        }
        print(f"\n{path}")
        for kind, query in queries.items():
            tag = f"probe:{kind}:{path}"
            print(f"  [{kind}] {query[:60]}{'…' if len(query) > 60 else ''} ({len(query)}자)")

            a, a_sec, a_err = search_and_log(
                "vsearch", query, ["vsearch", query, "--format", "json", "-n", str(FETCH_N)], tag
            )
            print(f"    A vsearch(질의 확장):        {describe(a, a_err, path)}  {a_sec:.1f}초")

            b, b_sec, b_err = search_and_log(
                "query-vec-only",
                query,
                ["query", f"vec: {query}", "--no-rerank", "--format", "json", "-n", str(FETCH_N)],
                tag,
            )
            print(f"    B vec 한 줄(확장·리랭킹 없음): {describe(b, b_err, path)}  {b_sec:.1f}초")
            for i, h in enumerate((b or [])[:5], 1):
                print(f"      B{i}. {h.path}{'  ← 정답 페이지' if h.path == path else ''}")

        # 같은 문장 끝에 고유한 숫자를 붙여 질의 확장 캐시를 피한다. 순위가 아니라 시간을 보려는 것이다.
        busted = f"{queries['sentence']} ({time.time():.0f})"
        _, c_sec, c_err = search_and_log(
            "vsearch-cachebust", busted, ["vsearch", busted, "--format", "json", "-n", str(FETCH_N)], f"probe:bust:{path}"
        )
        print(f"  C vsearch(캐시 회피용 변형 문장): {c_sec:.1f}초 {c_err}")


if __name__ == "__main__":
    main()
