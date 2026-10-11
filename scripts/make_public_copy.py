"""공개용 복사본 만들기: 이 레포의 파일을 규칙에 따라 정리해 새 폴더(새 git 저장소)로 내보낸다.

왜: 비공개 레포의 git 이력에는 지운 파일과 이름이 남는다. 공개 레포를 같은 이력으로 만들면 그대로 보인다.
그래서 현재 파일만 복사해 정리하고 이력은 새로 시작한다(커밋 1개). 규칙을 파일로 두어 다시 만들 때도 같은 결과가 나오고, 정리한 뒤 금지 문구가 남았는지 코드가 검사한다.

사용: uv run python scripts/make_public_copy.py --rules inputs/public_export_rules.json --out ../modular-agentic-system-public [--commit "초기 공개 버전"]
  - 규칙 파일(JSON)은 git에 올리지 않는다(이름 목록이 곧 비공개 정보). 이 스크립트에는 이름이 없다.
  - 대상 파일은 작업 트리의 추적 파일과 아직 추적하지 않은 파일(.gitignore 제외)이다. 커밋 전 변경도 들어간다.
  - 출력 폴더가 이미 있으면 거부한다(덮어쓰지 않는다). 지우고 다시 만들려면 사용자가 직접 지운다.
  - 금지 문구가 하나라도 남으면 종료 코드 1로 끝나고 어디에 남았는지 보고한다. 새 저장소 만들기·push는 하지 않는다.

규칙 파일 모양
  {"exclude": ["CLAUDE.md", "samples/*"],            # 복사하지 않을 경로(fnmatch, `*`은 `/`도 포함)
   "replace": [{"names": ["이름1", "이름2"], "to": "회사A"},          # 이름들을 한 말로(긴 이름 먼저)
               {"names": ["이름3", ["이름4", "이름4의 다른 표기"]], "series": "지원처 {n}"},  # 이름(또는 같은 대상의 표기 묶음)마다 번호를 붙인다(이름 순서로, 실제로 나온 것만)
               {"regex": "\\(규모 수치)]*\\)", "to": "(규모 수치)"}],   # 정규식
   "forbidden": ["이름1", "100\\.\\d+\\.\\d+\\.\\d+"]}               # 정리 뒤 남으면 안 되는 문구(정규식 아니면 이름으로 본다)
이름은 단어 경계를 지켜 찾는다: 영문·숫자 이름은 앞뒤에 영문·숫자가 없을 때만, 한글이 든 이름은 앞에 한글·영문·숫자가 없을 때만 맞는다(예: "지원처 14"은 "갱신한"에 걸리지 않는다).
"""
import argparse
import json
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path


def name_regex(name: str) -> str:
    """이름을 단어 경계를 지키는 정규식으로 만든다. 영문·숫자만이면 앞뒤 경계, 한글이 있으면 앞 경계만(뒤에는 조사가 붙는다)."""
    body = re.escape(name)
    if re.fullmatch(r"[A-Za-z0-9 ._&-]+", name):
        return rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])"
    return rf"(?<![가-힣A-Za-z0-9]){body}"


def names_regex(names: list[str]) -> re.Pattern:
    return re.compile("|".join(name_regex(n) for n in sorted(names, key=len, reverse=True)))


def build_replacements(rules: dict, texts: list[str]) -> list[tuple[re.Pattern, str]]:
    """규칙의 replace를 (패턴, 바꿀 말)의 순서 있는 목록으로 만든다. 번호 매기기(series)는 실제로 나온 이름만 이름 순서로 번호를 준다."""
    joined = "\n".join(texts)
    result: list[tuple[re.Pattern, str]] = []
    for entry in rules.get("replace", []):
        if "regex" in entry:
            result.append((re.compile(entry["regex"]), entry["to"]))
        elif "series" in entry:
            # names의 원소는 이름 하나이거나, 같은 대상의 여러 표기를 묶은 목록이다(묶음은 같은 번호를 받는다).
            groups = [[n] if isinstance(n, str) else list(n) for n in entry["names"]]
            present = sorted((g for g in groups if any(re.search(name_regex(n), joined) for n in g)), key=lambda g: min(g))
            for number, group in enumerate(present, 1):
                result.append((names_regex(group), entry["series"].format(n=number)))
        else:
            result.append((names_regex(entry["names"]), entry["to"]))
    return result


def apply_replacements(text: str, replacements: list[tuple[re.Pattern, str]]) -> str:
    for pattern, to in replacements:
        text = pattern.sub(to, text)
    return text


def forbidden_patterns(rules: dict) -> list[re.Pattern]:
    patterns = []
    for item in rules.get("forbidden", []):
        looks_like_regex = any(ch in item for ch in "\\[](){}|*+?^$")
        patterns.append(re.compile(item if looks_like_regex else name_regex(item)))
    return patterns


def find_forbidden(text: str, patterns: list[re.Pattern]) -> list[str]:
    return sorted({m.group(0) for p in patterns for m in p.finditer(text)})


def is_excluded(path: str, patterns: list[str]) -> bool:
    return any(fnmatch(path, p) for p in patterns)


def list_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root, capture_output=True, text=True, check=True).stdout
    return sorted({f for f in out.split("\n") if f and (root / f).is_file()})


def export(root: Path, out: Path, rules: dict) -> tuple[list[str], dict[str, list[str]]]:
    """복사해 정리하고 (복사한 파일들, 금지 문구가 남은 파일 → 문구들)을 돌려준다. 금지 문구는 경로에도 적용한다."""
    if out.exists():
        sys.exit(f"출력 폴더가 이미 있다: {out}\n덮어쓰지 않는다. 필요하면 직접 지우고 다시 실행한다.")
    files = [f for f in list_files(root) if not is_excluded(f, rules.get("exclude", []))]
    texts: dict[str, str | None] = {}
    for f in files:
        try:
            texts[f] = (root / f).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            texts[f] = None  # 바이너리는 그대로 복사한다
    replacements = build_replacements(rules, [t for t in texts.values() if t is not None] + files)
    forbidden = forbidden_patterns(rules)
    leftovers: dict[str, list[str]] = {}
    written = []
    for f in files:
        new_path = apply_replacements(f, replacements)
        target = out / new_path
        target.parent.mkdir(parents=True, exist_ok=True)
        text = texts[f]
        if text is None:
            target.write_bytes((root / f).read_bytes())
            bad = find_forbidden(new_path, forbidden)
        else:
            cleaned = apply_replacements(text, replacements)
            target.write_text(cleaned, encoding="utf-8")
            bad = find_forbidden(cleaned + "\n" + new_path, forbidden)
        if bad:
            leftovers[new_path] = bad
        written.append(new_path)
    return written, leftovers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rules", type=Path, required=True, help="규칙 JSON(git에 올리지 않는다)")
    parser.add_argument("--out", type=Path, required=True, help="공개용 복사본을 만들 새 폴더(이미 있으면 거부)")
    parser.add_argument("--commit", metavar="메시지", help="복사 뒤 새 git 저장소를 만들고 이 메시지로 커밋 1개를 만든다(금지 문구가 남았으면 하지 않는다). push는 하지 않는다")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    root = Path.cwd()
    written, leftovers = export(root, args.out, rules)
    print(f"복사한 파일 {len(written)}개 → {args.out}")
    if leftovers:
        print(f"\n⚠ 금지 문구가 남은 파일 {len(leftovers)}개:")
        for path, bad in sorted(leftovers.items()):
            print(f"  {path}: {', '.join(bad)}")
        print("\n규칙(replace/exclude)을 고쳐 출력 폴더를 지우고 다시 실행한다.")
        sys.exit(1)
    print("금지 문구가 남은 파일 없음")
    if args.commit:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=args.out, check=True)
        subprocess.run(["git", "add", "-A"], cwd=args.out, check=True)
        subprocess.run(["git", "commit", "-q", "-m", args.commit], cwd=args.out, check=True)
        print(f"새 저장소를 만들고 커밋 1개를 만들었다: {args.out} (push는 하지 않았다)")


if __name__ == "__main__":
    main()
