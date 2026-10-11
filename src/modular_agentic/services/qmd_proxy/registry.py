"""프록시가 노출하는 도구 목록과 실행. 웹 프레임워크를 모른다(server.py가 이걸 MCP로 감싼다).

도구 (역할 reader가 쓸 수 있는 것 / admin 전용)
  읽기   search_vault  우리 에이전트가 쓰는 단순형(질의 문자열 하나). 항상 벡터 검색 + 리랭킹 끔.
         query         qmd의 전체 검색(lex·vec·hyde 하위 질의). 리랭킹은 서버 설정이 허용할 때만.
         get, multi_get, status   (qmd MCP 그대로)
         ls, collection_list, collection_show, context_list   (qmd CLI, MCP에는 없는 읽기 명령)
  관리   update, embed, cleanup, collection_add(허용 루트 폴더 아래만), collection_remove, collection_rename,
         collection_include, collection_exclude, context_add, context_remove
         — 색인을 바꾸거나 CPU를 오래 쓴다. admin 키 + 서버 전역 스위치(config.allow_admin_tools)가 모두 있어야 보이고 실행된다.

일부러 노출하지 않는 것
  `qmd collection update-cmd`  `qmd update` 때 실행되는 **bash 명령**을 설정한다 → 열면 서버에서 임의 명령 실행이 가능하다.
  `init`, `trust`, `bench`, `skills`, `--index`, `update --pull`  — 범위 밖이거나 같은 위험이 있다.

방어 (qmd가 놓치는 것을 프록시가 막는다)
  - 모르는 인자는 오류로 알려 준다. qmd는 조용히 무시해서 `collection`(단수)을 주면 검색 범위가 풀린 채로 돈다(README 경고).
  - 리랭킹은 서버 설정이 허용하지 않으면 항상 끈다. qmd `query`의 rerank 기본값은 true인데 이 서버에서는 쿼리당 9~21분이다.
  - 질의 문자열만 오면 `vec` 하위 질의로 바꾼다(우리가 쓰는 빠른 경로. 문자열을 그대로 넘기면 qmd가 질의 확장 모델을 돌린다).
  - 명령줄에 들어가는 값은 `-`로 시작하지 못한다(옵션 주입 방지). 이름·대상·경로는 형식을 검사한다.
  - `collection_add`의 경로는 실제 경로(심볼릭 링크를 푼 뒤)가 허용 루트 안일 때만 받는다.
"""
from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backend import BackendError, QmdCli, QmdMcpBackend
from .config import ROLE_ADMIN, ProxyConfig

MAX_OUTPUT_CHARS = 20000


class ToolError(Exception):
    """도구 호출 자체가 성립하지 않는 오류."""


class UnknownTool(ToolError):
    """없거나 이 역할에게 보이지 않는 도구. 있는지 여부를 알려 주지 않으려고 같은 오류를 쓴다."""


class InvalidArguments(ToolError):
    pass


def text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    admin_only: bool
    run: Callable[[dict[str, Any]], dict[str, Any]]

    def listing(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


# ---- 인자 검사 -------------------------------------------------------------------------------------------------

_TYPE_NAMES = {"string": str, "boolean": bool, "array": list, "object": dict}


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """schema(JSON 스키마의 일부)로 인자를 검사한다. 모르는 인자·빠진 필수 인자·잘못된 형식은 InvalidArguments."""
    if not isinstance(args, dict):
        raise InvalidArguments("arguments는 객체여야 한다")
    props = schema.get("properties", {})
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise InvalidArguments(f"알 수 없는 인자: {', '.join(unknown)} (쓸 수 있는 인자: {', '.join(props) or '없음'})")
    for name in schema.get("required", []):
        if name not in args:
            raise InvalidArguments(f"필수 인자가 없다: {name}")
    for name, value in args.items():
        spec = props[name]
        kind = spec.get("type")
        if kind in ("integer", "number"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind == "integer" and int(value) != value):
                raise InvalidArguments(f"{name}은 {'정수' if kind == 'integer' else '숫자'}여야 한다")
            if "minimum" in spec and value < spec["minimum"]:
                raise InvalidArguments(f"{name}은 {spec['minimum']} 이상이어야 한다")
            if "maximum" in spec and value > spec["maximum"]:
                raise InvalidArguments(f"{name}은 {spec['maximum']} 이하여야 한다")
        elif kind in _TYPE_NAMES and not isinstance(value, _TYPE_NAMES[kind]):
            raise InvalidArguments(f"{name}은 {kind} 형식이어야 한다")
        if kind == "string":
            if "enum" in spec and value not in spec["enum"]:
                raise InvalidArguments(f"{name}은 {spec['enum']} 중 하나여야 한다")
            if spec.get("minLength", 0) > len(value):
                raise InvalidArguments(f"{name}이 비어 있다")
        if kind == "array":
            item = spec.get("items", {})
            if "minItems" in spec and len(value) < spec["minItems"]:
                raise InvalidArguments(f"{name}은 {spec['minItems']}개 이상이어야 한다")
            if "maxItems" in spec and len(value) > spec["maxItems"]:
                raise InvalidArguments(f"{name}은 {spec['maxItems']}개 이하여야 한다")
            for element in value:
                if item.get("type") == "string" and not isinstance(element, str):
                    raise InvalidArguments(f"{name}의 원소는 문자열이어야 한다")
                if item.get("type") == "object":
                    validate_args(item, element)
    return args


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-]{0,63}$")  # `-`로 시작하면 명령줄에서 옵션으로 읽힌다(옵션 주입)
_CONTEXT_TARGET = re.compile(r"^(/|qmd://[A-Za-z0-9_\-]{1,64}(/[^\x00-\x1f]*)?)$")


def safe_token(value: str, what: str, *, max_len: int = 300) -> str:
    """명령줄에 그대로 들어가는 값. 비어 있거나 제어문자가 있거나 `-`로 시작하면(옵션으로 읽힘) 거부한다."""
    if not value or len(value) > max_len or _CONTROL.search(value):
        raise InvalidArguments(f"{what} 값이 올바르지 않다")
    if value.startswith("-"):
        raise InvalidArguments(f"{what}은 '-'로 시작할 수 없다")
    return value


def safe_name(value: str, what: str) -> str:
    if not _NAME.match(value):
        raise InvalidArguments(f"{what}은 영문·숫자·_·-로 된 64자 이하이고 '-'로 시작할 수 없다")
    return value


def safe_relative(value: str, what: str) -> str:
    """컬렉션 안 경로·glob. 절대경로와 `..`는 거부한다."""
    safe_token(value, what)
    if value.startswith("/") or ".." in value.replace("\\", "/").split("/"):
        raise InvalidArguments(f"{what}에는 절대 경로나 '..'를 쓸 수 없다")
    return value


def check_collection_root(path: str, roots: tuple[str, ...]) -> str:
    """collection_add의 경로 검사. 실제 경로(심볼릭 링크 해석 후)가 허용 루트 안의 폴더일 때만 받는다."""
    safe_token(path, "path")
    if not roots:
        raise InvalidArguments("collection_add가 허용된 루트 폴더가 설정돼 있지 않다")
    real = Path(os.path.realpath(os.path.expanduser(path)))
    if not real.is_dir():
        raise InvalidArguments("path가 존재하는 폴더가 아니다")
    for root in roots:
        real_root = Path(os.path.realpath(os.path.expanduser(root)))
        if real == real_root or real_root in real.parents:
            return str(real)
    raise InvalidArguments("path가 허용된 루트 폴더 밖이다")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---- 레지스트리 ------------------------------------------------------------------------------------------------

_SEARCH_ITEM = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["lex", "vec", "hyde"]},
        "query": {"type": "string", "minLength": 1},
    },
    "required": ["query"],
}


def build_registry(config: ProxyConfig, backend: QmdMcpBackend, cli: QmdCli) -> ToolRegistry:
    admin_lock = threading.Lock()

    def mcp_tool(name: str, shape: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def run(args: dict[str, Any]) -> dict[str, Any]:
            try:
                return backend.call_tool(name, shape(args))
            except BackendError as exc:
                return text_result(str(exc), is_error=True)

        return run

    def query_args(args: dict[str, Any]) -> dict[str, Any]:
        default = config.default_search_type
        searches = args.get("searches")
        if searches is None:
            text = args.get("query")
            if not text:
                raise InvalidArguments("query(문자열) 또는 searches(하위 질의 배열) 중 하나가 필요하다")
            searches = [{"type": default, "query": text}]
        else:
            searches = [{"type": s.get("type", default), "query": s["query"]} for s in searches]
        out: dict[str, Any] = {
            "searches": searches,
            "limit": int(_clamp(args.get("limit", min(10, config.max_limit)), 1, config.max_limit)),
            "rerank": bool(args.get("rerank", False)) and config.allow_rerank,
        }
        if "collections" in args:
            out["collections"] = args["collections"]
        if "intent" in args:
            out["intent"] = args["intent"]
        if "minScore" in args:
            out["minScore"] = _clamp(args["minScore"], 0, 1)
        if "candidateLimit" in args:
            out["candidateLimit"] = int(_clamp(args["candidateLimit"], 1, 100))
        return out

    def get_args(args: dict[str, Any]) -> dict[str, Any]:
        out = dict(args)
        if "maxLines" in out:
            out["maxLines"] = int(_clamp(out["maxLines"], 1, 2000))
        return out

    def multi_get_args(args: dict[str, Any]) -> dict[str, Any]:
        out = dict(args)
        if "maxLines" in out:
            out["maxLines"] = int(_clamp(out["maxLines"], 1, 2000))
        if "maxBytes" in out:
            out["maxBytes"] = int(_clamp(out["maxBytes"], 1, 200_000))
        return out

    def cli_tool(build: Callable[[dict[str, Any]], list[str]], *, admin: bool = False) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def run(args: dict[str, Any]) -> dict[str, Any]:
            argv = build(args)  # 값 검사는 여기서 한다(InvalidArguments는 호출한 쪽으로 올라간다)
            timeout = config.admin_timeout_sec if admin else config.cli_timeout_sec
            if admin and not admin_lock.acquire(blocking=False):
                return text_result("다른 관리 작업이 실행 중이다. 끝난 뒤 다시 시도한다.", is_error=True)
            try:
                result = cli.run(argv, timeout)
            except BackendError as exc:
                return text_result(str(exc), is_error=True)
            finally:
                if admin:
                    admin_lock.release()
            output = (result.stdout or "").strip()
            if result.returncode != 0:
                output = f"종료 코드 {result.returncode}\n{(result.stderr or '').strip()}\n{output}".strip()
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + f"\n… (출력이 {MAX_OUTPUT_CHARS}자를 넘어 잘랐다)"
            return text_result(output or "(출력 없음)", is_error=result.returncode != 0)

        return run

    schema = lambda props, required=(): {"type": "object", "properties": props, "required": list(required)}  # noqa: E731
    name_prop = {"type": "string", "minLength": 1, "description": "컬렉션 이름"}
    limit_prop = {"type": "integer", "minimum": 1, "maximum": config.max_limit, "description": "결과 개수"}

    tools = [
        ToolDef(
            "search_vault",
            "개인 지식 베이스(vault)에서 질문과 의미가 가까운 글 조각을 찾는다. 단어 하나보다 '어떤 경험이나 사실을 찾는지'를 설명하는 "
            "한국어 문장이 잘 맞는다. 점수는 관련 여부를 보장하지 않으니 발췌를 직접 읽고 판단한다. 항상 벡터 검색이고 리랭킹은 끈다.",
            schema(
                {
                    "query": {"type": "string", "minLength": 1, "description": "찾으려는 경험·사실을 설명하는 문장"},
                    "limit": limit_prop,
                    "collections": {"type": "array", "items": {"type": "string"}, "description": "검색할 컬렉션 이름들(생략하면 기본 컬렉션)"},
                },
                ["query"],
            ),
            False,
            mcp_tool("query", lambda a: query_args({k: a[k] for k in ("query", "limit", "collections") if k in a})),
        ),
        ToolDef(
            "query",
            "qmd 전체 검색. searches에 하위 질의(type: lex=BM25 키워드, vec=벡터, hyde=가상 답변 문장)를 1~10개 준다. "
            "query(문자열)만 주면 vec 하위 질의로 바꾼다. 컬렉션 범위는 collections(복수형, 배열)로 준다.",
            schema(
                {
                    "query": {"type": "string", "description": "질의 문자열(searches가 없을 때만 쓴다)"},
                    "searches": {"type": "array", "items": _SEARCH_ITEM, "minItems": 1, "maxItems": 10},
                    "collections": {"type": "array", "items": {"type": "string"}},
                    "intent": {"type": "string", "description": "모호한 질의의 맥락(이것만으로 검색하지 않는다)"},
                    "limit": limit_prop,
                    "minScore": {"type": "number", "minimum": 0, "maximum": 1},
                    "candidateLimit": {"type": "integer", "minimum": 1},
                    "rerank": {"type": "boolean", "description": "리랭킹. 서버 설정이 허용하지 않으면 무시되고 항상 꺼진다"},
                }
            ),
            False,
            mcp_tool("query", query_args),
        ),
        ToolDef(
            "get",
            "문서 하나를 경로나 docid(#abc123)로 가져온다. `경로:시작줄:줄수` 형식도 된다.",
            schema(
                {
                    "file": {"type": "string", "minLength": 1},
                    "fromLine": {"type": "integer", "minimum": 1},
                    "maxLines": {"type": "integer", "minimum": 1},
                    "lineNumbers": {"type": "boolean"},
                },
                ["file"],
            ),
            False,
            mcp_tool("get", get_args),
        ),
        ToolDef(
            "multi_get",
            "여러 문서를 glob 패턴이나 쉼표로 구분한 목록으로 가져온다.",
            schema(
                {
                    "pattern": {"type": "string", "minLength": 1},
                    "maxLines": {"type": "integer", "minimum": 1},
                    "maxBytes": {"type": "integer", "minimum": 1},
                    "lineNumbers": {"type": "boolean"},
                },
                ["pattern"],
            ),
            False,
            mcp_tool("multi_get", multi_get_args),
        ),
        ToolDef("status", "색인 상태와 컬렉션 정보.", schema({}), False, mcp_tool("status", lambda a: {})),
        ToolDef(
            "ls",
            "컬렉션 안의 파일 목록. path를 생략하면 컬렉션 목록, `컬렉션` 또는 `컬렉션/하위폴더`를 주면 그 안의 파일.",
            schema({"path": {"type": "string"}}),
            False,
            cli_tool(lambda a: ["ls", safe_relative(a["path"], "path")] if a.get("path") else ["ls"]),
        ),
        ToolDef("collection_list", "컬렉션 목록.", schema({}), False, cli_tool(lambda a: ["collection", "list"])),
        ToolDef(
            "collection_show",
            "컬렉션 상세(경로, glob, 기본 검색 포함 여부, 컨텍스트 수).",
            schema({"name": name_prop}, ["name"]),
            False,
            cli_tool(lambda a: ["collection", "show", safe_name(a["name"], "name")]),
        ),
        ToolDef("context_list", "등록된 컨텍스트(경로별 설명) 목록.", schema({}), False, cli_tool(lambda a: ["context", "list"])),
        # ---- 관리 도구 ----
        ToolDef("update", "[관리] 컬렉션을 다시 스캔해 색인을 갱신한다(컬렉션에 설정된 갱신 명령이 있으면 먼저 실행된다).", schema({}), True, cli_tool(lambda a: ["update"], admin=True)),
        ToolDef(
            "embed",
            "[관리] 벡터 임베딩을 만든다. CPU를 오래 쓴다. force=true면 전부 다시 만든다.",
            schema({"force": {"type": "boolean"}}),
            True,
            cli_tool(lambda a: ["embed", "-f"] if a.get("force") else ["embed"], admin=True),
        ),
        ToolDef("cleanup", "[관리] 쓰이지 않는 임베딩 조각을 정리한다.", schema({}), True, cli_tool(lambda a: ["cleanup"], admin=True)),
        ToolDef(
            "collection_add",
            "[관리] 폴더를 컬렉션으로 추가한다. path는 서버 설정이 허용한 루트 폴더 아래여야 한다.",
            schema(
                {
                    "path": {"type": "string", "minLength": 1},
                    "name": name_prop,
                    "mask": {"type": "string", "description": "glob(기본 **/*.md). 쉼표로 여러 개"},
                },
                ["path", "name"],
            ),
            True,
            cli_tool(
                lambda a: [
                    "collection",
                    "add",
                    check_collection_root(a["path"], config.collection_roots),
                    "--name",
                    safe_name(a["name"], "name"),
                    *(["--mask", safe_relative(a["mask"], "mask")] if a.get("mask") else []),
                ],
                admin=True,
            ),
        ),
        ToolDef(
            "collection_remove",
            "[관리] 컬렉션을 색인에서 뺀다(원본 파일은 지우지 않는다).",
            schema({"name": name_prop}, ["name"]),
            True,
            cli_tool(lambda a: ["collection", "remove", safe_name(a["name"], "name")], admin=True),
        ),
        ToolDef(
            "collection_rename",
            "[관리] 컬렉션 이름을 바꾼다.",
            schema({"old": name_prop, "new": name_prop}, ["old", "new"]),
            True,
            cli_tool(lambda a: ["collection", "rename", safe_name(a["old"], "old"), safe_name(a["new"], "new")], admin=True),
        ),
        ToolDef(
            "collection_include",
            "[관리] 컬렉션을 기본(범위 없는) 검색에 포함한다.",
            schema({"name": name_prop}, ["name"]),
            True,
            cli_tool(lambda a: ["collection", "include", safe_name(a["name"], "name")], admin=True),
        ),
        ToolDef(
            "collection_exclude",
            "[관리] 컬렉션을 기본 검색에서 뺀다.",
            schema({"name": name_prop}, ["name"]),
            True,
            cli_tool(lambda a: ["collection", "exclude", safe_name(a["name"], "name")], admin=True),
        ),
        ToolDef(
            "context_add",
            "[관리] 컨텍스트(검색 결과와 함께 돌려줄 설명)를 추가한다. target은 `/`(전체) 또는 `qmd://컬렉션[/경로]`.",
            schema({"target": {"type": "string", "minLength": 1}, "text": {"type": "string", "minLength": 1}}, ["target", "text"]),
            True,
            cli_tool(
                lambda a: ["context", "add", _context_target(a["target"]), safe_token(a["text"], "text", max_len=2000)],
                admin=True,
            ),
        ),
        ToolDef(
            "context_remove",
            "[관리] 컨텍스트를 지운다.",
            schema({"target": {"type": "string", "minLength": 1}}, ["target"]),
            True,
            cli_tool(lambda a: ["context", "rm", _context_target(a["target"])], admin=True),
        ),
    ]
    return ToolRegistry(tools, config)


def _context_target(value: str) -> str:
    if not _CONTEXT_TARGET.match(value):
        raise InvalidArguments("target은 `/` 또는 `qmd://컬렉션[/경로]` 형식이어야 한다")
    return value


class ToolRegistry:
    def __init__(self, tools: list[ToolDef], config: ProxyConfig) -> None:
        self._tools = {t.name: t for t in tools}
        self._config = config

    def visible(self, role: str) -> list[ToolDef]:
        """이 역할에게 보이는 도구. 관리 도구는 admin 역할이고 서버 전역 스위치가 켜져 있을 때만."""
        admin_ok = role == ROLE_ADMIN and self._config.allow_admin_tools
        return [t for t in self._tools.values() if admin_ok or not t.admin_only]

    def call(self, name: str, args: dict[str, Any] | None, role: str) -> dict[str, Any]:
        tool = next((t for t in self.visible(role) if t.name == name), None)
        if tool is None:
            raise UnknownTool(name)
        checked = validate_args(tool.input_schema, args or {})
        return tool.run(checked)
