"""콘솔 웹 서버(Starlette). 화면(static/index.html)과 JSON API를 낸다.

API
  GET  /api/actions                    작업 목록(이름·단계·비용·인자·고를 수 있는 구현 단계)
  GET  /api/impls                      단계별 구현체 목록(registry.py)
  GET  /api/tasks                      업무 목록(instances/profile.py)과 업무마다 돌릴 수 있는 단계
  GET  /api/items                      공고(실행 가능)와 전시 묶음(보기 전용)
  GET  /api/items/{id}/results         단계별 결과 문서 목록 + 문장 검증에 줄 초안 JSON + 가져오기에 줄 수집 후보 JSON
  GET  /api/file?path=…                결과 문서 하나(단계·전시 폴더 안의 .md·.html만)
  GET  /api/jobs · POST /api/jobs      작업 목록 · 확인 대기 작업 만들기 {action, item, params}
  GET  /api/jobs/{id}                  상태 + 출력 끝부분
  POST /api/jobs/{id}/confirm|cancel   사람이 확인해 시작 · 취소
  POST /api/chat                       {item, message} → 에이전트 답(채팅이 켜져 있을 때)

인증이 없으므로 127.0.0.1에만 묶어 쓴다(scripts/run_console.py). 밖에서는 SSH 포트 포워딩으로 본다.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from modular_agentic import registry

from .actions import ACTIONS, ActionError, list_candidates, list_drafts, list_items, list_results, read_result, task_choices
from .jobs import JobRunner

STATIC_DIR = Path(__file__).parent / "static"


def _actions_json() -> list[dict]:
    return [{"key": a.key, "stage": a.stage, "label": a.label, "cost": a.cost, "bedrock": a.bedrock,
             "description": a.description, "impl_stages": list(a.impl_stages),
             "params": [{"name": p.name, "kind": p.kind, "help": p.help, "required": p.required, "impl": p.impl} for p in a.params]}
            for a in ACTIONS.values()]


def create_app(runner: JobRunner, chat: Callable[[str, str], str] | None = None) -> Starlette:
    """chat(item_id, message) -> 답. None이면 채팅 API는 꺼짐을 알린다."""

    def guarded(handler):
        async def wrapped(request: Request):
            try:
                return await handler(request)
            except ActionError as error:
                return JSONResponse({"error": str(error)}, status_code=400)
        return wrapped

    async def index(_: Request):
        return FileResponse(STATIC_DIR / "index.html")

    async def actions(_: Request):
        return JSONResponse(_actions_json())

    async def impls(_: Request):
        return JSONResponse(registry.describe())

    async def tasks(_: Request):
        return JSONResponse(task_choices())

    async def items(_: Request):
        return JSONResponse(list_items())

    async def results(request: Request):
        item_id = request.path_params["item_id"]
        return JSONResponse({"results": list_results(item_id), "drafts": list_drafts(item_id), "candidates": list_candidates()})

    async def file(request: Request):
        return PlainTextResponse(read_result(request.query_params.get("path", "")))

    async def jobs(request: Request):
        if request.method == "POST":
            body = await request.json()
            job = runner.create(body.get("action", ""), body.get("item", ""), body.get("params") or {}, source="button")
            return JSONResponse(job)
        return JSONResponse(runner.list())

    async def job(request: Request):
        job_id = request.path_params["job_id"]
        return JSONResponse({**runner.get(job_id), "log": runner.log_tail(job_id)})

    async def confirm(request: Request):
        return JSONResponse(runner.confirm(request.path_params["job_id"]))

    async def cancel(request: Request):
        return JSONResponse(runner.cancel(request.path_params["job_id"]))

    async def chat_route(request: Request):
        if chat is None:
            return JSONResponse({"error": "채팅이 꺼져 있다(서버를 --chat으로 켠다)"}, status_code=400)
        body = await request.json()
        from starlette.concurrency import run_in_threadpool  # 모델 호출이 수십 초 걸려 이벤트 루프를 막지 않게

        reply = await run_in_threadpool(chat, body.get("item", ""), str(body.get("message", ""))[:4000])
        return JSONResponse({"reply": reply})

    routes = [
        Route("/", index),
        Route("/api/actions", guarded(actions)),
        Route("/api/impls", guarded(impls)),
        Route("/api/tasks", guarded(tasks)),
        Route("/api/items", guarded(items)),
        Route("/api/items/{item_id}/results", guarded(results)),
        Route("/api/file", guarded(file)),
        Route("/api/jobs", guarded(jobs), methods=["GET", "POST"]),
        Route("/api/jobs/{job_id}", guarded(job)),
        Route("/api/jobs/{job_id}/confirm", guarded(confirm), methods=["POST"]),
        Route("/api/jobs/{job_id}/cancel", guarded(cancel), methods=["POST"]),
        Route("/api/chat", guarded(chat_route), methods=["POST"]),
    ]
    return Starlette(routes=routes)
