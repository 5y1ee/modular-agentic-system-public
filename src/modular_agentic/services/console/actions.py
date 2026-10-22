"""콘솔이 실행할 수 있는 작업(허용 목록)과 결과 문서 읽기. 웹 프레임워크를 쓰지 않는다.

작업 하나 = 기존 단계 스크립트의 명령 하나(`docs/runbook-cover-letter.md`의 순서 표). 여기 없는 명령은 실행하지 않는다.
인자는 이름·형식을 검사한 뒤 argv 리스트로 만든다(shell을 거치지 않는다. 문항·지침 같은 자유 글도 인자 하나로만 들어간다).
결과 문서는 단계 폴더(`logs/00_collect`~`05_write`)와 공개용 전시 폴더(`logs/console/showcase`) 안의 .md·.html만 읽는다.

구현 선택과 모델(갈아 끼우기, registry.py)
  작업마다 쓰는 단계 구현을 `impl_stages`로 적어 두고, 화면에서 고른 구현은 params["impl"]({단계: 이름})로,
  모델은 params["model"]로 받는다. 실행할 때 환경변수 `MAS_IMPL_<단계>`·`MAS_MODEL_ID`로 넘긴다(job_env). 스크립트는 고치지 않는다.

공고(item)는 세 종류다.
- 수집(`__collect__`): 공고 후보를 찾고 가져오는 자리. 특정 공고에 묶이지 않는다.
- 실행 가능: `inputs/00_collect/<id>.txt`가 있는 공고. 단계 버튼을 누를 수 있다. 공고 파일의 `업무:` 줄로 업무(자소서·제안서)가 정해지고,
  업무 설정(instances/profile.py)의 stages에 없는 단계는 실행하지 않는다(제안서는 수집·분석까지).
- 보기 전용(전시): `logs/console/showcase/<이름>/`의 문서 묶음. 제안서처럼 세션에서 손으로 단계를 따라간 결과를
  마스킹해 둔 것이고 파이프라인 코드로 만든 것이 아니라서 실행 버튼이 없다.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from modular_agentic import registry
from modular_agentic.instances.profile import all_profiles, profile, profile_of_text

COLLECT_DIR = Path("inputs") / "00_collect"
STAGE_DIRS = {  # 화면에 보이는 단계 순서. 순서 번호(0~5)가 폴더 번호·전시 파일 앞 두 자리와 같다. scripts/common.py의 폴더와 같다
    "collect": Path("logs") / "00_collect",
    "research": Path("logs") / "01_research",
    "analyze": Path("logs") / "02_analyze",
    "map": Path("logs") / "03_map",
    "plan": Path("logs") / "04_plan",
    "write": Path("logs") / "05_write",
}
SHOWCASE_DIR = Path("logs") / "console" / "showcase"
READABLE_SUFFIXES = {".md", ".html"}
QMD_WRAPPER = "scripts/with_qmd_server.sh"  # vault 검색이 필요한 단계는 qmd 서버를 켜고 실행한 뒤 끈다
COLLECT_ITEM = "__collect__"
SHOWCASE_TASK_FILE = "업무.txt"

ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
NUMBERS_RE = re.compile(r"^[0-9][0-9,\s]*$|^$")  # 요구 번호 목록 "1,3,5" (비우기 허용)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9.:_-]{3,120}$")
TEXT_MAX = 2000
LONGTEXT_MAX = 30000  # 붙여넣는 공고 본문


class ActionError(ValueError):
    """허용되지 않은 작업이나 잘못된 인자. 메시지는 화면·채팅에 그대로 보인다."""


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # "text" | "longtext" | "int" | "bool" | "numbers" | "ids" | "date" | "draft" | "candidates" | "choice"(업무 이름)
    help: str
    required: bool = False
    impl: str = ""  # 수집처럼 구현마다 받는 설정이 다를 때: 이 구현을 골랐을 때만 쓰는 인자


@dataclass(frozen=True)
class Action:
    key: str  # "<단계>.<동작>"
    stage: str
    label: str
    cost: str  # 사람이 확인할 때 보는 비용·시간 설명
    params: tuple[Param, ...] = ()
    needs_qmd: bool = False
    bedrock: bool = True  # Bedrock(비용)을 부르는가. 부르지 않는 작업도 사람 확인은 거친다(승인·표시를 바꾸므로)
    description: str = ""
    impl_stages: tuple[str, ...] = ()  # 이 작업이 쓰는 단계 구현(registry의 단계 이름). 화면에서 고를 수 있다


ACTIONS: dict[str, Action] = {a.key: a for a in (
    Action("collect.search", "collect", "공고 후보 찾기", "크롤러·붙여넣기는 모델 호출 없음 / 웹 검색 에이전트는 Bedrock 여러 번 + Tavily 최대 4회, 1~3분",
           (Param("day", "date", "크롤러 결과 날짜(YYYY-MM-DD, 비우면 오늘)", impl="inthiswork"),
            Param("goal", "text", "찾을 공고(예: 금융권 백엔드 신입, 서울)", impl="web_agent"),
            Param("max", "int", "최대 후보 수(기본 5)", impl="web_agent"),
            Param("title", "text", "제목(회사 직무)", impl="paste"),
            Param("text", "longtext", "공고 본문", impl="paste"),
            Param("link", "text", "원문 주소(선택)", impl="paste"),
            Param("instance", "choice", "업무(자소서·제안서). 분석 규칙이 업무에 따라 바뀐다")),
           impl_stages=("collector",),
           description="고른 수집 구현으로 공고 후보를 찾는다. 붙여넣기는 후보 없이 바로 공고로 저장한다"),
    Action("collect.import", "collect", "후보 가져오기", "모델 호출 없음",
           (Param("candidates", "candidates", "후보 파일(logs/00_collect/…json)", required=True),
            Param("pick", "numbers", "가져올 후보 번호(쉼표로)", required=True)), bedrock=False,
           description="고른 후보를 공고 파일(inputs/00_collect/<id>.txt)로 저장한다. 왼쪽 목록에 새 공고로 나타난다"),
    Action("research.run", "research", "회사 조사", "Bedrock 1회 + Tavily 약 4~6 크레딧, 1~3분",
           (Param("company", "text", "회사 이름"), Param("url", "text", "직접 줄 웹 주소(줄마다 하나)"),
            Param("query", "text", "직접 줄 검색어(줄마다 하나)")), impl_stages=("researcher", "transcriber"),
           description="공고·자료·웹 검색으로 조사 보고서를 만든다(미승인 상태로 저장)"),
    Action("research.drop", "research", "조사 주장 빼기", "모델 호출 없음",
           (Param("claim_ids", "ids", "뺄 주장 id(쉼표로, 예: c3,c7)", required=True),), bedrock=False,
           description="틀린 주장을 지운다. 승인이 풀린다"),
    Action("research.approve", "research", "조사 보고서 승인", "모델 호출 없음", bedrock=False,
           description="승인한 보고서만 뒤 단계가 쓴다"),
    Action("analyze.run", "analyze", "요구 목록 뽑기", "Bedrock 1회",
           (Param("refresh", "bool", "이미 있어도 다시 만든다(기본 요건 표시·회사 맥락이 사라진다)"),), impl_stages=("analyzer",),
           description="공고에서 요구 목록을 뽑아 저장한다"),
    Action("analyze.compare", "analyze", "분석 엔진 비교", "Bedrock 2회(구현마다 1회)", impl_stages=(),
           description="같은 공고를 Strands 구현과 Bedrock 직접 호출 구현으로 각각 분석해 한 문서(…__compare.md)에 나란히 놓는다. 파이프라인이 쓰는 분석 결과는 건드리지 않는다"),
    Action("analyze.context", "analyze", "회사 맥락 붙이기", "Bedrock 1회", impl_stages=("context_linker",),
           description="승인된 조사 주장을 요구마다 참고 메모로 붙인다"),
    Action("analyze.mark", "analyze", "기본 요건 표시", "모델 호출 없음",
           (Param("basic", "numbers", "기본 요건의 요구 번호(쉼표로, 1부터). 비우면 해제", required=True),), bedrock=False,
           description="어필·검증하지 않을 기본 요건을 표시한다"),
    Action("map.run", "map", "근거 매핑·검증", "약 4분, Bedrock 요구 수 × 여러 회",
           (Param("refresh", "bool", "이미 있어도 매핑·검증을 새로 돌린다"),), needs_qmd=True, impl_stages=("mapper", "verifier"),
           description="요구마다 vault에서 근거를 찾고(매핑) 경험 단위로 정정·모순을 대조한다(검증)"),
    Action("plan.make", "plan", "소재 계획 만들기", "Bedrock 1회",
           (Param("variant", "text", "계획 이름(예: 고른 가치)", required=True), Param("note", "text", "배정에 줄 추가 지시"),
            Param("with_context", "bool", "회사 맥락을 입력에 넣는다")), impl_stages=("planner",),
           description="문항 파일(inputs/00_collect/<공고>.questions.txt)의 문항마다 중심 경험·근거를 배정한다"),
    Action("plan.approve", "plan", "소재 계획 승인", "모델 호출 없음",
           (Param("variant", "text", "계획 이름", required=True),), bedrock=False,
           description="승인한 계획만 작성이 쓴다"),
    Action("write.run", "write", "초안 쓰기", "문항마다 Bedrock 1~수 회",
           (Param("variant", "text", "승인된 소재 계획 이름(이것 또는 문항)"), Param("number", "int", "계획의 문항 번호"),
            Param("question", "text", "계획 없이 쓸 문항(분량 제한은 '800자'처럼)"), Param("focus", "numbers", "재료로 쓸 요구 번호"),
            Param("n", "int", "초안 개수(기본 3)"), Param("guidance", "text", "이번 작성에만 적용할 지침")),
           needs_qmd=True, impl_stages=("writer",), description="초안 여러 개를 근거·이유와 함께 쓴다"),
    Action("check.run", "write", "문장 검증", "Bedrock 1회",
           (Param("draft", "draft", "초안 JSON(logs/05_write/…json)", required=True),
            Param("number", "int", "그 파일의 몇 번째 초안(1부터)", required=True)), impl_stages=("claim_checker",),
           description="초안 문장마다 쓴 근거가 뒷받침하는지 판정한다(⚠는 사람이 원문과 대조)"),
)}


def _check(param: Param, value) -> object:
    """값 하나의 형식을 검사해 정리된 값을 돌려준다."""
    if param.kind == "bool":
        if not isinstance(value, bool):
            raise ActionError(f"{param.name}: 참/거짓이어야 한다")
        return value
    if param.kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip().isdigit():
            raise ActionError(f"{param.name}: 양의 정수여야 한다")
        number = int(value)
        if not 1 <= number <= 50:
            raise ActionError(f"{param.name}: 1~50이어야 한다")
        return number
    if not isinstance(value, str):
        raise ActionError(f"{param.name}: 글이어야 한다")
    text = value.strip()
    if len(text) > (LONGTEXT_MAX if param.kind == "longtext" else TEXT_MAX):
        raise ActionError(f"{param.name}: 너무 길다")
    if param.kind == "numbers" and not NUMBERS_RE.match(text):
        raise ActionError(f"{param.name}: 번호를 쉼표로 적는다(예: 1,3,5)")
    if param.kind == "choice":
        try:
            return profile(text).name
        except KeyError as error:
            raise ActionError(str(error.args[0])) from None
    if param.kind == "date" and not DATE_RE.match(text):
        raise ActionError(f"{param.name}: YYYY-MM-DD 형식이어야 한다")
    if param.kind == "ids":
        ids = [p for p in re.split(r"[,\s]+", text) if p]
        if not ids or not all(ID_RE.match(i) for i in ids):
            raise ActionError(f"{param.name}: 영문·숫자 id를 쉼표로 적는다")
        return ids
    if param.kind in ("draft", "candidates"):
        folder = STAGE_DIRS["write" if param.kind == "draft" else "collect"]
        path = Path(text)
        if path.suffix != ".json" or path.resolve().parent != folder.resolve() or not path.is_file():
            raise ActionError(f"{param.name}: {folder} 안의 JSON이어야 한다")
        return str(path)
    if text.startswith("-"):
        raise ActionError(f"{param.name}: '-'로 시작할 수 없다(명령 옵션으로 읽힌다)")
    return text


def _check_choices(action: Action, impl, model) -> dict:
    """구현 선택({단계: 이름})과 모델을 검사한다. 이 작업이 쓰지 않는 단계는 거부한다."""
    clean: dict = {}
    if impl:
        if not isinstance(impl, dict):
            raise ActionError("impl은 {단계: 구현 이름} 모양이어야 한다")
        for stage, name in impl.items():
            if stage not in action.impl_stages:
                raise ActionError(f"{action.label}은(는) {stage} 구현을 쓰지 않는다(쓰는 것: {', '.join(action.impl_stages) or '없음'})")
            try:
                registry.choose(stage, str(name))
            except KeyError as error:
                raise ActionError(str(error.args[0])) from None
        clean["impl"] = {s: str(n) for s, n in impl.items()}
    if model:
        if not isinstance(model, str) or not MODEL_RE.match(model.strip()):
            raise ActionError("model: 모델 ID 형식이 아니다(예: us.anthropic.claude-sonnet-4-6)")
        clean["model"] = model.strip()
    return clean


def validate(action_key: str, item_id: str, params: dict) -> tuple[Action, dict]:
    """작업 이름·공고·인자를 검사한다. 모르는 인자는 거부한다."""
    action = ACTIONS.get(action_key)
    if action is None:
        raise ActionError(f"모르는 작업: {action_key} (있는 것: {', '.join(ACTIONS)})")
    if action.stage == "collect":
        if item_id != COLLECT_ITEM:
            raise ActionError(f"수집 작업은 '새 공고 수집'({COLLECT_ITEM})에서 한다")
    elif not ID_RE.match(item_id or "") or item_id == COLLECT_ITEM or not posting_path(item_id).is_file():
        raise ActionError(f"실행할 수 있는 공고가 아니다: {item_id}")
    else:
        task = item_profile(item_id)
        if action.stage not in task.stages:
            raise ActionError(f"{task.label} 업무는 아직 이 단계를 파이프라인으로 돌리지 않는다(되는 단계: {', '.join(task.stages)})")
    params = dict(params)
    choices = _check_choices(action, params.pop("impl", None), params.pop("model", None))
    collector = registry.choose("collector", choices.get("impl", {}).get("collector")).name if "collector" in action.impl_stages else ""
    known = {p.name: p for p in action.params}
    unknown = set(params) - set(known)
    if unknown:
        raise ActionError(f"모르는 인자: {', '.join(sorted(unknown))}")
    clean = {}
    for param in action.params:
        if param.impl and param.impl != collector:
            continue  # 고른 구현이 쓰지 않는 설정은 버린다
        value = params.get(param.name)
        if value is None or value == "" and param.kind != "numbers":
            if param.required:
                raise ActionError(f"{param.name}이(가) 필요하다: {param.help}")
            continue
        clean[param.name] = _check(param, value)
    if action.key == "write.run" and not (clean.get("variant") and clean.get("number")) and not clean.get("question"):
        raise ActionError("초안 쓰기에는 소재 계획 이름+문항 번호, 또는 문항이 필요하다")
    if collector == "web_agent" and not clean.get("goal"):
        raise ActionError("웹 검색 에이전트에는 찾을 공고(goal)가 필요하다")
    if collector == "paste" and not (clean.get("title") and clean.get("text")):
        raise ActionError("붙여넣기에는 제목과 본문이 필요하다")
    if collector and clean.get("instance") and collector not in profile(clean["instance"]).collectors:
        task = profile(clean["instance"])
        raise ActionError(f"{task.label} 업무에는 이 수집 구현을 쓰지 않는다(쓰는 것: {', '.join(task.collectors)})")
    return action, {**clean, **choices}


def job_env(action: Action, params: dict) -> dict[str, str]:
    """고른 구현·모델을 스크립트에 넘길 환경변수. 고르지 않은 것은 넘기지 않는다(스크립트 기본값)."""
    env = {registry.env_name(stage): name for stage, name in params.get("impl", {}).items()}
    if params.get("model"):
        env["MAS_MODEL_ID"] = params["model"]
    return env


def posting_path(item_id: str) -> Path:
    return COLLECT_DIR / f"{item_id}.txt"


def item_profile(item_id: str):
    """공고 파일 앞부분의 `업무:` 줄로 업무 설정을 정한다(없으면 자소서)."""
    with posting_path(item_id).open(encoding="utf-8") as f:
        head = "".join(f.readline() for _ in range(10))
    return profile_of_text(head)


def build_argv(action: Action, item_id: str, params: dict, python: str = sys.executable) -> list[str]:
    """검사를 마친 인자로 실행할 argv를 만든다. 레포 루트에서 실행한다고 가정한다."""
    posting = str(posting_path(item_id))
    p = params
    key = action.key
    if key == "collect.search":
        impl = registry.choose("collector", p.get("impl", {}).get("collector")).name
        argv = ["scripts/collect_postings.py", "search", "--impl", impl]
        for name in ("day", "goal", "max", "title", "text", "link", "instance"):
            if p.get(name):
                argv += [f"--{name}", str(p[name])]
    elif key == "collect.import":
        argv = ["scripts/collect_postings.py", "import", p["candidates"], "--pick", p["pick"]]
    elif key == "research.run":
        argv = ["scripts/research_brief.py", "run", posting]
        if p.get("company"):
            argv += ["--company", p["company"]]
        for flag, name in (("--url", "url"), ("--query", "query")):
            for line in (p.get(name) or "").splitlines():
                if line.strip():
                    argv += [flag, line.strip()]
    elif key == "research.drop":
        argv = ["scripts/research_brief.py", "drop", item_id, *p["claim_ids"]]
    elif key == "research.approve":
        argv = ["scripts/research_brief.py", "approve", item_id]
    elif key == "analyze.run":
        argv = ["scripts/analyze_posting.py", "run", posting] + (["--refresh"] if p.get("refresh") else [])
    elif key == "analyze.compare":
        argv = ["scripts/compare_analyzers.py", posting, "--impls", ",".join(i.name for i in registry.IMPLS["analyzer"])]
    elif key == "analyze.context":
        argv = ["scripts/analyze_posting.py", "context", item_id]
    elif key == "analyze.mark":
        argv = ["scripts/analyze_posting.py", "mark", item_id, "--basic", p.get("basic", "")]
    elif key == "map.run":
        argv = ["scripts/run_writer_demo.py", posting, "--state-only"] + (["--refresh"] if p.get("refresh") else [])
    elif key == "plan.make":
        argv = ["scripts/make_plan.py", "make", posting, "--questions", str(COLLECT_DIR / f"{item_id}.questions.txt"),
                "--variant", p["variant"]]
        if p.get("note"):
            argv += ["--note", p["note"]]
        if p.get("with_context"):
            argv.append("--with-context")
    elif key == "plan.approve":
        argv = ["scripts/make_plan.py", "approve", item_id, "--variant", p["variant"]]
    elif key == "write.run":
        argv = ["scripts/run_writer_demo.py", posting]
        if p.get("variant") and p.get("number"):
            argv += ["--plan", p["variant"], "--number", str(p["number"])]
        else:
            argv += ["--question", p["question"]]
            if p.get("focus"):
                argv += ["--focus", p["focus"]]
        if p.get("n"):
            argv += ["--n", str(p["n"])]
        if p.get("guidance"):
            argv += ["--guidance", p["guidance"]]
    elif key == "check.run":
        argv = ["scripts/check_draft.py", posting, "--draft", p["draft"], "--number", str(p["number"])]
    else:  # ACTIONS에 넣고 여기를 빠뜨린 경우
        raise ActionError(f"argv를 만들 수 없는 작업: {key}")
    argv = [python, *argv]
    return [QMD_WRAPPER, *argv] if action.needs_qmd else argv


# ---- 공고와 결과 문서 ----

def task_choices() -> list[dict]:
    return [{"name": p.name, "label": p.label, "item_label": p.item_label, "stages": list(p.stages), "collectors": list(p.collectors)}
            for p in all_profiles()]


def list_items() -> list[dict]:
    """수집 자리, 실행 가능한 공고(inputs/00_collect/*.txt), 보기 전용 전시 묶음."""
    items = [{"id": COLLECT_ITEM, "kind": "collect", "has_questions": False}]
    for path in sorted(COLLECT_DIR.glob("*.txt")):
        if path.name.endswith(".questions.txt"):
            continue
        task = item_profile(path.stem)
        items.append({"id": path.stem, "kind": "run", "has_questions": (COLLECT_DIR / f"{path.stem}.questions.txt").is_file(),
                      "task": task.label, "stages": list(task.stages)})
    if SHOWCASE_DIR.is_dir():
        for d in sorted(SHOWCASE_DIR.iterdir()):
            if d.is_dir():  # 업무는 묶음 폴더의 업무.txt(mask_showcase.py가 씀), 없으면 자소서
                marker = d / SHOWCASE_TASK_FILE
                task = profile(marker.read_text(encoding="utf-8").strip()) if marker.is_file() else profile()
                items.append({"id": d.name, "kind": "showcase", "has_questions": False, "task": task.label, "stages": []})
    return items


def _newest(paths) -> list[dict]:
    return [_entry(p) for p in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)]


def list_results(item_id: str) -> dict[str, list[dict]]:
    """단계별 결과 문서 목록(새 것부터). 전시 묶음이면 파일 이름의 앞 두 자리(00~05)로 단계를 정한다."""
    if not ID_RE.match(item_id or ""):
        raise ActionError(f"잘못된 공고 id: {item_id}")
    results: dict[str, list[dict]] = {stage: [] for stage in STAGE_DIRS}
    if item_id == COLLECT_ITEM:
        folder = STAGE_DIRS["collect"]
        results["collect"] = _newest(folder.glob("*.md")) if folder.is_dir() else []
        return results
    showcase = SHOWCASE_DIR / item_id
    if showcase.is_dir():
        order = list(STAGE_DIRS)
        for path in sorted(showcase.iterdir()):
            if path.suffix in READABLE_SUFFIXES and path.name[:2].isdigit() and int(path.name[:2]) < len(order):
                results[order[int(path.name[:2])]].append(_entry(path))
        return results
    for stage, folder in STAGE_DIRS.items():
        if stage != "collect" and folder.is_dir():
            results[stage] = _newest(p for p in folder.glob("*") if p.suffix in READABLE_SUFFIXES and item_id in p.name and not p.name.endswith("_prev.md"))
    return results


def list_drafts(item_id: str) -> list[str]:
    """문장 검증에 줄 수 있는 초안 JSON(새 것부터)."""
    folder = STAGE_DIRS["write"]
    paths = folder.glob(f"*-{item_id}.json") if ID_RE.match(item_id or "") and folder.is_dir() else []
    return [str(p) for p in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)]


def list_candidates() -> list[str]:
    """가져오기에 줄 수 있는 수집 후보 JSON(새 것부터)."""
    folder = STAGE_DIRS["collect"]
    return [e["path"] for e in _newest(folder.glob("*.json"))] if folder.is_dir() else []


def _entry(path: Path) -> dict:
    return {"path": str(path), "name": path.name, "mtime": path.stat().st_mtime}


def read_result(path_text: str) -> str:
    """결과 문서 하나를 읽는다. 단계 폴더·전시 폴더 밖이거나 .md·.html이 아니면 거부한다(경로 바꿔치기 방지)."""
    path = Path(path_text).resolve()
    roots = [d.resolve() for d in (*STAGE_DIRS.values(), SHOWCASE_DIR)]
    if path.suffix not in READABLE_SUFFIXES or not any(path.is_relative_to(root) for root in roots) or not path.is_file():
        raise ActionError(f"읽을 수 없는 경로: {path_text}")
    return path.read_text(encoding="utf-8")
