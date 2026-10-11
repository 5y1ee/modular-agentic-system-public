"""공개용 복사본 만들기(scripts/make_public_copy.py): 이름 치환의 단어 경계, 번호 매기기, 금지 문구 검사, 내보내기. 가짜 이름만 쓴다."""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("make_public_copy", SCRIPTS / "make_public_copy.py")
mpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mpc)


def replace(text: str, rules: dict) -> str:
    return mpc.apply_replacements(text, mpc.build_replacements(rules, [text]))


def test_korean_names_do_not_match_inside_other_words_and_keep_following_particles():
    rules = {"replace": [{"names": ["가나은행"], "to": "회사A"}]}
    assert replace("가나은행에 지원했다. 이 가나은행은", rules) == "회사A에 지원했다. 이 회사A은"
    assert replace("갱가나은행", rules) == "갱가나은행"  # 앞에 한글이 붙은 다른 단어는 건드리지 않는다


def test_ascii_names_need_non_alphanumeric_neighbours():
    rules = {"replace": [{"names": ["ACME", "지원처 5"], "to": "X"}]}
    assert replace("ACME, 지원처 5 자소서 (지원처 5)", rules) == "X, X 자소서 (X)"
    assert replace("고객사와 KTX와 ACMES와 430KB", rules) == "고객사와 KTX와 ACMES와 430KB"  # 다른 단어 속·파일 크기는 그대로


def test_longer_names_win_over_their_prefixes_within_one_entry():
    rules = {"replace": [{"names": ["가나", "가나금융티아이"], "to": "회사"}]}
    assert replace("가나금융티아이와 가나", rules) == "회사와 회사"  # 긴 이름이 먼저 맞아 "회사금융티아이"가 되지 않는다


def test_series_numbers_only_names_that_occur_in_sorted_name_order_and_stay_consistent():
    rules = {"replace": [{"names": ["다회사", "가회사", "나회사"], "series": "지원처 {n}"}]}
    text = "다회사와 가회사. 다회사 또."  # 나회사는 안 나온다
    assert replace(text, rules) == "지원처 2와 지원처 1. 지원처 2 또."


def test_series_groups_give_the_same_number_to_every_spelling_of_one_target():
    rules = {"replace": [{"names": [["가은행", "NH가은행", "가정보시스템"], "나회사"], "series": "지원처 {n}"}]}
    assert replace("NH가은행과 나회사, 가정보시스템, 가은행.", rules) == "지원처 1과 지원처 2, 지원처 1, 지원처 1."


def test_regex_entries_and_order_of_entries():
    rules = {"replace": [{"regex": r"\(9,999건[^)]*\)", "to": "(규모 수치)"}, {"names": ["사고"], "to": "사례"}]}
    assert replace("사고 (9,999건, 18개) 끝", rules) == "사례 (규모 수치) 끝"


def test_forbidden_matches_names_with_boundaries_and_regexes():
    patterns = mpc.forbidden_patterns({"forbidden": ["가나은행", r"203\.0\.113\.\d+"]})
    assert mpc.find_forbidden("가나은행 서버 203.0.113.5", patterns) == ["203.0.113.5", "가나은행"]
    assert mpc.find_forbidden("갱가나은행 203", patterns) == []


def test_exclude_patterns_use_fnmatch_where_star_crosses_slashes():
    assert mpc.is_excluded("samples/a/b.txt", ["samples/*"]) and mpc.is_excluded("CLAUDE.md", ["CLAUDE.md"])
    assert not mpc.is_excluded("docs/samples.md", ["samples/*", "CLAUDE.md"])


def make_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    (root / "README.md").write_text("가나은행 지원 도구. 서버 203.0.113.5\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "가나은행-메모.md").write_text("내용\n", encoding="utf-8")  # 경로에도 이름이 있다
    (root / "samples").mkdir()
    (root / "samples" / "공고.txt").write_text("남의 공고\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00binary")
    (root / "ignored.log").write_text("가나은행\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md", "docs", ".gitignore"], cwd=root, check=True)  # 나머지는 추적하지 않은 상태


RULES = {"exclude": ["samples/*"], "replace": [{"names": ["가나은행"], "to": "회사A"}, {"regex": r"203\.0\.113\.5", "to": "10.0.0.1"}], "forbidden": ["가나은행", r"203\.0\.113\.\d+"]}


def test_export_copies_cleaned_files_including_untracked_but_not_ignored_or_excluded(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    make_repo(src)
    written, leftovers = mpc.export(src, out, RULES)
    assert leftovers == {}
    assert sorted(written) == [".gitignore", "README.md", "blob.bin", "docs/회사A-메모.md"]  # 경로의 이름도 치환, samples·ignored는 빠짐
    assert (out / "README.md").read_text(encoding="utf-8") == "회사A 지원 도구. 서버 10.0.0.1\n"
    assert (out / "blob.bin").read_bytes() == b"\xff\xfe\x00binary"  # 바이너리는 그대로


def test_export_reports_leftover_forbidden_text_and_refuses_an_existing_output_folder(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    make_repo(src)
    rules = {**RULES, "replace": [{"names": ["가나은행"], "to": "회사A"}], "forbidden": ["가나은행", r"203\.0\.113\.\d+"]}  # IP 치환 규칙을 빼먹은 규칙
    _, leftovers = mpc.export(src, out, rules)
    assert leftovers == {"README.md": ["203.0.113.5"]}  # 빼먹은 것을 검사가 잡는다
    with pytest.raises(SystemExit, match="이미 있다"):
        mpc.export(src, out, rules)
