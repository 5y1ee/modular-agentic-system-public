"""같은 질의를 vault 전체와 제외 경로 적용 후로 나란히 보여 준다.

사용: uv run python scripts/compare_vault_search.py "질의1" "질의2" ...

qmd를 질의당 한 번만 부르고(약 5초), 같은 결과에서 "전체 상위 n건"과
"제외 후 상위 n건"을 둘 다 뽑는다. 읽기 전용이다.
"""
import sys

from modular_agentic.instances.cover_letter.config import VAULT_EXCLUDE_PREFIXES
from modular_agentic.tools.vault_search import VaultHit, search

N = 5


def show(label: str, hits: list[VaultHit]) -> None:
    print(f"  [{label}]")
    for i, h in enumerate(hits, 1):
        excluded = " (제외 대상)" if h.path.startswith(VAULT_EXCLUDE_PREFIXES) else ""
        print(f"    {i}. {h.score:.2f}  {h.path}:{h.line}{excluded}")


def main() -> None:
    queries = sys.argv[1:]
    if not queries:
        sys.exit("질의를 하나 이상 주세요.")
    for q in queries:
        print(f"\n질의: {q}")
        # 같은 qmd 결과를 두 번 쓰려고 제외 없이 넉넉히 받은 뒤 직접 나눈다.
        everything = search(q, n=40, tag="compare")
        show("전체", everything[:N])
        show("제외 적용", [h for h in everything if not h.path.startswith(VAULT_EXCLUDE_PREFIXES)][:N])


if __name__ == "__main__":
    main()
