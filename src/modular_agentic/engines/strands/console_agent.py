"""콘솔 채팅 에이전트(Strands). 사용자와 대화하며 결과 문서를 읽고, 단계를 다시 돌리는 작업을 요청한다.

도구는 콘솔 버튼과 같은 작업 목록(`services/console/actions.py`)을 쓴다. 에이전트가 할 수 있는 것:
결과 문서 목록·읽기, 작업 요청(확인 대기로만 만든다), 작업 상태·출력 보기.
에이전트는 작업을 직접 시작하지 못한다. 사람이 화면에서 비용을 보고 [실행]을 눌러야 돈다(jobs.py).
"파이프라인을 고친다"는 요청은 실행 설정(어느 단계를 다시, 요구 선택, 지침, 기본 요건 표시, 주장 빼기 등)까지만 다룬다.
코드를 고치는 도구는 주지 않았다(발표 전 3일 안에 안전하게 검증할 수 없어서, 2026-10-20 합의).

대화 기록은 공고마다 에이전트 하나에 메모리로 둔다(서버를 끄면 사라진다).
"""
from __future__ import annotations

import json
import threading

from modular_agentic import registry
from modular_agentic.services.console.actions import ACTIONS, ActionError, list_candidates, list_drafts, list_items, list_results, read_result
from modular_agentic.services.console.jobs import JobRunner

from .config import build_agent

READ_LIMIT = 12000  # 문서 하나를 이만큼까지만 모델에 준다(넘으면 잘렸다고 알린다)

SYSTEM_PROMPT = """너는 자소서 작성 파이프라인의 운영을 돕는 에이전트다. 사용자는 지원자 본인이고, 지금 `{item}`을 보고 있다(`__collect__`이면 새 공고를 수집하는 자리다).
파이프라인 단계: 수집(collect) → 조사(research) → 분석(analyze) → 매핑·검증(map) → 소재 계획(plan) → 작성·문장 검증(write).
각 단계의 결과는 마크다운 문서로 저장돼 있고 list_results·read_result로 읽는다.

규칙
- 결과에 대해 말할 때는 먼저 문서를 읽고, 문서에 적힌 것과 네 추론을 구분한다. 문서에 없는 경험·수치를 지어내지 않는다.
- 단계를 (다시) 돌리려면 request_action을 부른다. 이것은 '확인 대기' 작업을 만들 뿐이고, 사용자가 화면 오른쪽 작업 목록에서 비용을 보고 [실행]을 눌러야 시작된다. 그렇게 안내한다.
- 사용자가 "파이프라인을 바꿔 달라"고 하면 실행 설정(어느 단계를 다시, 요구 선택 focus, 작성 지침 guidance, 기본 요건 mark, 조사 주장 drop, 소재 계획 note 등)으로 할 수 있는지 보고 작업으로 만든다. 코드 수정은 이 콘솔에서 하지 않는다고 말한다.
- 승인(approve)은 사용자가 결과를 읽고 명시적으로 원할 때만 요청한다.
- 단계마다 구현체를 바꿔 끼울 수 있다(아래 구현 목록). 사용자가 구현이나 모델을 바꾸라고 하면 params_json에 "impl": {{"단계": "구현 이름"}}, "model": "모델 ID"를 넣는다. 작업이 쓰지 않는 단계의 구현은 넣지 않는다.
- 수집 작업(collect.*)은 `__collect__`에서만 요청할 수 있다. 공고 후보를 찾은 뒤 가져오기는 사용자가 번호를 고르게 한다.
- 짧게 한국어로 답한다. 표가 도움이 되면 마크다운 표를 쓴다.

작업 목록(action 이름: 설명 / 인자 / 고를 수 있는 구현 단계):
{catalog}

구현 목록(단계: 이름=설명):
{impls}
"""


def _catalog() -> str:
    lines = []
    for a in ACTIONS.values():
        params = ", ".join(f"{p.name}{'*' if p.required else ''}({p.kind}: {p.help})" for p in a.params) or "인자 없음"
        impl = ", ".join(a.impl_stages) or "없음"
        lines.append(f"- {a.key}: {a.label} — {a.description}. 비용: {a.cost}. 인자: {params}. 구현 단계: {impl}")
    return "\n".join(lines)


def _impls() -> str:
    return "\n".join(f"- {stage}: " + "; ".join(f"{i['name']}={i['label']}" for i in impls) for stage, impls in registry.describe().items())


def make_tools(runner: JobRunner, item_id: str) -> list:
    """이 공고에 묶인 도구들. Strands의 @tool은 docstring을 도구 설명으로 쓴다."""
    from strands import tool

    @tool
    def list_documents() -> str:
        """이 공고의 단계별 결과 문서 경로 목록(새 것부터)과 문장 검증에 쓸 수 있는 초안 JSON 목록을 돌려준다."""
        results = {stage: [d["path"] for d in docs] for stage, docs in list_results(item_id).items()}
        return json.dumps({"results": results, "drafts": list_drafts(item_id), "collect_candidates": list_candidates()}, ensure_ascii=False)

    @tool
    def read_document(path: str) -> str:
        """결과 문서 하나를 읽는다. path는 list_documents가 준 경로를 그대로 쓴다."""
        try:
            text = read_result(path)
        except ActionError as error:
            return f"오류: {error}"
        if len(text) > READ_LIMIT:
            return text[:READ_LIMIT] + f"\n\n[잘림: 전체 {len(text)}자 중 앞 {READ_LIMIT}자]"
        return text

    @tool
    def request_action(action: str, params_json: str = "{}") -> str:
        """단계 작업을 '확인 대기'로 만든다(실행하지 않는다). action은 작업 이름(예: write.run),
        params_json은 인자 JSON 객체(예: {"variant": "가치A", "number": 2, "guidance": "…"})."""
        try:
            params = json.loads(params_json or "{}")
            if not isinstance(params, dict):
                return "오류: params_json은 JSON 객체여야 한다"
            job = runner.create(action, item_id, params, source="chat")
        except (ActionError, json.JSONDecodeError) as error:
            return f"오류: {error}"
        return f"확인 대기 작업을 만들었다: id={job['id']}, {job['label']}, 비용 {job['cost']}. 사용자가 화면에서 [실행]을 눌러야 시작된다."

    @tool
    def job_status(job_id: str = "") -> str:
        """작업 상태. job_id를 주면 그 작업의 상태와 출력 끝부분, 비우면 최근 작업 목록을 돌려준다."""
        try:
            if job_id:
                job = runner.get(job_id)
                return json.dumps({k: job[k] for k in ("id", "label", "status", "params", "returncode")}, ensure_ascii=False) + \
                    "\n출력 끝부분:\n" + runner.log_tail(job_id, 3000)
            return json.dumps([{k: j[k] for k in ("id", "label", "item", "status")} for j in runner.list(10)], ensure_ascii=False)
        except ActionError as error:
            return f"오류: {error}"

    return [list_documents, read_document, request_action, job_status]


class ConsoleChat:
    """공고마다 대화가 이어지는 채팅. 서버가 chat(item_id, message)로 부른다."""

    def __init__(self, runner: JobRunner, agent_factory=None):
        self.runner = runner
        self._factory = agent_factory or (lambda item_id: build_agent(
            SYSTEM_PROMPT.format(item=item_id, catalog=_catalog(), impls=_impls()), make_tools(runner, item_id)))
        self._agents: dict[str, object] = {}
        self._lock = threading.Lock()  # 같은 에이전트에 두 메시지가 겹치지 않게

    def __call__(self, item_id: str, message: str) -> str:
        if item_id not in {i["id"] for i in list_items() if i["kind"] in ("run", "collect")}:
            return "실행 가능한 공고를 먼저 고른다(보기 전용 묶음에서는 채팅을 쓰지 않는다)."
        with self._lock:
            agent = self._agents.get(item_id) or self._agents.setdefault(item_id, self._factory(item_id))
            return str(agent(message)).strip()

