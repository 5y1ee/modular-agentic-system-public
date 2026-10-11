"""eval/known_item의 문장 고르기 규칙을 확인한다."""
from modular_agentic.eval.known_item import candidate_lines, pick

LONG = "이것은 질의로 쓰기에 충분히 긴 평범한 문장입니다. 길이 조건을 넘기려고 덧붙입니다."


def test_skips_headings_tables_links_and_code():
    text = "\n".join(
        [
            "# " + LONG,
            "| " + LONG + " |",
            LONG + " https://example.com",
            "```",
            LONG,
            "```",
            LONG,
        ]
    )
    assert candidate_lines(text) == [LONG]  # 코드 블록 밖의 평범한 줄만 남음


def test_strips_list_markers_and_skips_short_and_frontmatter():
    text = "---\ntitle: x\n---\n- " + LONG + "\n짧은 줄\n"
    assert candidate_lines(text) == [LONG]


def test_pick_first_and_middle():
    lines = [f"{i}번째 문장입니다. " + "가" * 40 for i in range(5)]
    picked = pick("\n".join(lines))
    assert picked == [lines[0], lines[2]]
    assert pick("짧음") == []
