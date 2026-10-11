"""scripts/ 안의 실행 스크립트가 같이 쓰는 조립 코드. 엔진이 instances·eval을 모르게 여기서 이어 준다."""
import json
import sys
from pathlib import Path

from modular_agentic.core.analysis_review import analysis_path, load_analysis
from modular_agentic.core.brief_review import approved_brief
from modular_agentic.core.plan_review import approved_plan, plan_path
from modular_agentic.core.serialize import load_state
from modular_agentic.core.types import Analysis, Brief, Claim, Evidence, Mapping, MappingStatus, QuestionPlan, Requirement
from modular_agentic.instances.cover_letter.config import VAULT_DIR
from modular_agentic.tools.user_facts import is_user_ref, load_user_facts, user_facts_mapping
from modular_agentic.tools.vault_read import evidence_from_refs
from modular_agentic.engines.strands.verifier import KnownExperience
from modular_agentic.eval.experience_recall import GOLDEN_V2_PATH


# 단계별 폴더. 역할 순서대로 번호를 붙였다: 00 수집 → 01 조사 → 02 분석 → 03 매핑(+검증) → 04 소재 계획 → 05 작성.
# 02 분석 결과(요구 목록·기본 요건 표시·회사 맥락)는 02_analyze에 따로 저장한다(설계 문서 25절). 경로를 바꿀 때는 여기만 고친다.
COLLECT_DIR = Path("inputs") / "00_collect"  # 공고 텍스트(+전사 원본), 문항 파일, 공고별 회사 자료 폴더
RESEARCH_DIR = Path("logs") / "01_research"
ANALYZE_DIR = Path("logs") / "02_analyze"
MAP_DIR = Path("logs") / "03_map"
PLAN_DIR = Path("logs") / "04_plan"
WRITE_DIR = Path("logs") / "05_write"
FEEDBACK_DIR = WRITE_DIR / "feedback"
GUIDANCE_PATH = Path("logs") / "guidance.md"


def brief_path(item_id: str) -> Path:
    return RESEARCH_DIR / f"{item_id}.json"


def load_approved_brief(item_id: str) -> Brief | None:
    """사용자가 승인한 조사 보고서만 돌려준다(없거나 승인 전이면 None). 어떻게 됐는지 한 줄로 알린다."""
    brief, message = approved_brief(brief_path(item_id))
    print(message)
    return brief



def load_saved_analysis(item_id: str) -> Analysis | None:
    """02_analyze에 저장된 분석 결과. 없으면 None."""
    return load_analysis(analysis_path(ANALYZE_DIR, item_id))


def load_plan_question(item_id: str, label: str, number: int, user_facts: Path | None = None) -> tuple[QuestionPlan, list[Mapping]]:
    """승인된 소재 계획에서 문항 하나의 배정과, 작성이 쓸 근거를 담은 추가 매핑을 돌려준다.

    배정된 근거 중 앞 단계 매핑·가치 매핑 결과에 없는 것(사용자가 지정한 근거)은 vault에서 지정 줄을 읽어 채운다.
    `사용자:`로 시작하는 근거는 user_facts 파일(사용자가 직접 말한 사실)에서 채운다. 파일을 안 주었거나 소제목이 없으면 끝낸다.
    승인된 소재 계획이 없거나 문항 번호가 없으면 메시지를 내고 끝낸다(승인 전 소재 계획은 쓰지 않는다).
    """
    plan, message = approved_plan(plan_path(PLAN_DIR, item_id, label))
    print(message)
    if plan is None:
        sys.exit(f"승인된 소재 계획이 필요하다: make_plan.py approve {item_id} --variant \"{label}\"")
    question_plan = next((q for q in plan.questions if q.number == number), None)
    if question_plan is None:
        sys.exit(f"소재 계획에 문항 {number}이 없다(있는 문항: {[q.number for q in plan.questions]})")

    extra: list[Mapping] = []
    known: set[str] = set()
    for name in (f"{item_id}.json", f"{item_id}_values.json"):
        path = MAP_DIR / name
        if path.exists():
            _, mappings, _ = load_state(json.loads(path.read_text(encoding="utf-8")))
            extra += mappings if name.endswith("_values.json") else []
            known |= {e.ref for m in mappings for e in m.evidence}
    missing = [r for exp in question_plan.experiences for r in exp.refs if r not in known]
    wanted_user = [r for r in dict.fromkeys(missing) if is_user_ref(r)]
    missing = [r for r in missing if not is_user_ref(r)]
    if wanted_user:
        if user_facts is None:
            sys.exit(f"소재 계획이 사용자 진술 {wanted_user}을 쓴다. --user-facts로 진술 파일을 준다.")
        try:
            facts = {e.ref: e for e in load_user_facts(user_facts)}
        except ValueError as exc:
            sys.exit(f"--user-facts 오류: {exc}")
        absent = [r for r in wanted_user if r not in facts]
        if absent:
            sys.exit(f"진술 파일 {user_facts}에 배정된 소제목이 없다: {absent}")
        extra += user_facts_mapping(item_id, [facts[r] for r in wanted_user])
    if missing:
        try:
            pinned = evidence_from_refs(list(dict.fromkeys(missing)), VAULT_DIR)
        except ValueError as exc:
            sys.exit(f"배정된 근거를 vault에서 읽지 못했다: {exc}")
        extra.append(Mapping(Requirement(id=f"{item_id}-plan-extra", item_id=item_id, text="배정된 추가 근거", kind="지정"), pinned, MappingStatus.CONFIRMED, "사용자가 지정한 근거(검증 전)"))
    return question_plan, extra


def collect_check_sources(refs: list[str], claim_ids: list[str], brief: Brief | None, user_facts: Path | None) -> tuple[list[Evidence], list[Claim]]:
    """초안이 쓴 근거(refs, 회사 사실 id)를 문장 검증기에 줄 모양으로 읽는다. 못 읽은 것이 있으면 메시지를 내고 끝낸다(조용히 빼지 않는다).

    `사용자:`로 시작하는 근거는 user_facts 파일에서, 나머지는 vault의 `경로:줄`에서 읽는다. 회사 사실은 승인된 조사 보고서의 주장이다.
    refs는 순서를 지키고 중복을 뺀다.
    """
    refs = list(dict.fromkeys(refs))
    vault_refs = [r for r in refs if not is_user_ref(r)]
    user_refs = [r for r in refs if is_user_ref(r)]
    try:
        found = {e.ref: e for e in (evidence_from_refs(vault_refs, VAULT_DIR) if vault_refs else [])}
    except ValueError as exc:
        sys.exit(f"근거를 vault에서 읽지 못했다: {exc}")
    if user_refs:
        if user_facts is None:
            sys.exit(f"사용자 진술 근거 {user_refs}를 읽으려면 --user-facts 파일이 필요하다.")
        try:
            facts = {e.ref: e for e in load_user_facts(user_facts)}
        except ValueError as exc:
            sys.exit(f"--user-facts 오류: {exc}")
        missing = [r for r in user_refs if r not in facts]
        if missing:
            sys.exit(f"진술 파일 {user_facts}에 없는 소제목: {missing}")
        found |= {r: facts[r] for r in user_refs}
    claims: list[Claim] = []
    if claim_ids:
        if brief is None:
            sys.exit(f"회사 사실 {claim_ids}를 읽으려면 승인된 조사 보고서가 필요하다.")
        by_id = {c.id: c for c in brief.claims}
        unknown = [i for i in dict.fromkeys(claim_ids) if i not in by_id]
        if unknown:
            sys.exit(f"조사 보고서에 없는 주장 id: {unknown}")
        claims = [by_id[i] for i in dict.fromkeys(claim_ids)]
    return [found[r] for r in refs], claims


def load_known_experiences() -> list[KnownExperience]:
    """골든셋 v2의 경험 목록을 Verifier의 후보 경험으로 읽는다."""
    data = json.loads(GOLDEN_V2_PATH.read_text(encoding="utf-8"))
    return [
        KnownExperience(
            id=e["id"], name=e["name"], keywords=tuple(e["keywords"]), origin_pages=tuple(e["origin_pages"])
        )
        for e in data["experiences"]
    ]


def drafts_to_md(question: str, drafts: list, title: str = "") -> str:
    """초안들을 사용자가 읽고 고치기 좋은 마크다운으로 만든다. 초안마다 본문·글자 수·이유·쓴 근거·쓴 회사 사실·경고를 같이 보인다."""
    lines = [f"# {title}" if title else "# 초안", "", f"> {question}", ""]
    for number, d in enumerate(drafts, 1):
        body = next(iter(d.sections.values()), "")
        count = len("".join(body.split("\n")))  # 줄바꿈 제외 글자 수(공백 포함)
        lines += [f"## 초안 {number} — {d.angle}", "", body, "", f"*글자 수(줄바꿈 제외, 공백 포함): {count}자*", ""]
        lines.append(f"- **이유**: {d.rationale}")
        lines.append(f"- **쓴 근거**: {', '.join(f'`{r}`' for r in d.used_refs) or '없음'}")
        if any(is_user_ref(r) for r in d.used_refs):
            lines.append("- **검증 안 된 사용자 진술 포함**: `사용자:`로 시작하는 근거는 vault와 대조하지 않았다. 진술 범위를 넘는 세부가 없는지 사용자가 본다.")
        if d.used_claims:
            lines.append(f"- **쓴 회사 사실**: {', '.join(d.used_claims)}")
        lines += [f"- ⚠ {w}" for w in d.warnings]
        lines.append("")
    return "\n".join(lines)


def print_draft(number: int, draft, number_of: dict[str, int]) -> None:
    """초안 하나를 이유·다룬 요구·쓴 근거·경고와 함께 출력한다. number_of는 요구 id → 요구 번호(1부터)."""
    print(f"\n━━ 초안 {number} — {draft.angle}")
    print(next(iter(draft.sections.values()), ""))
    print(f"\n  이유: {draft.rationale}")
    print(f"  다룬 요구: {[number_of.get(r, r) for r in draft.covered_requirement_ids]}")
    print(f"  쓴 근거: {', '.join(draft.used_refs)}")
    if draft.used_claims:
        print(f"  쓴 회사 사실: {', '.join(draft.used_claims)}")
    for w in draft.warnings:
        print(f"  ⚠ {w}")
