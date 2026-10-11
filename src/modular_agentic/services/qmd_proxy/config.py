"""프록시 설정: API 키(해시로 저장), 역할, 허용 범위, 한도.

키는 평문으로 저장하지 않는다. 설정 파일에는 sha256 해시만 두고, 요청이 오면 받은 키의 해시를 비교한다(`hash_key`).
새 키는 `scripts/run_qmd_proxy.py make-key`로 만든다(평문은 그때 한 번만 보이고 저장되지 않는다).

역할
  reader  검색·조회 도구만(`search_vault`, `query`, `get`, `multi_get`, `status`, `ls`, `collection_list` …)
  admin   읽기 도구 + 관리 도구(`update`, `embed`, `collection_add` …).
          단 서버 전역 스위치 `allow_admin_tools`가 true일 때만 관리 도구가 열린다(기본 false).
          Why: 관리 도구는 색인을 바꾸고 CPU를 오래 쓴다. 키가 새어도 스위치가 꺼져 있으면 막힌다.

설정 파일(JSON) 예
  {
    "keys": [{"name": "gateway", "sha256": "<해시>", "role": "reader"}],
    "allow_admin_tools": false,
    "collection_roots": ["/home/ubuntu/01_user"],
    "allowed_origins": [],
    "max_limit": 50
  }
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROLE_READER = "reader"
ROLE_ADMIN = "admin"
ROLES = (ROLE_READER, ROLE_ADMIN)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_key() -> str:
    """새 API 키(평문). 보여 준 뒤 저장하지 않는다."""
    return "qmdp_" + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class KeyInfo:
    name: str
    role: str


@dataclass(frozen=True)
class ProxyConfig:
    keys: dict[str, KeyInfo] = field(default_factory=dict)  # sha256 해시 → 키 정보
    allow_admin_tools: bool = False
    collection_roots: tuple[str, ...] = ()  # collection_add가 허용하는 루트 폴더들. 비어 있으면 collection_add는 항상 거부
    allowed_origins: tuple[str, ...] = ()  # 브라우저 Origin 허용 목록. Origin 헤더가 없는 요청(MCP 클라이언트·curl)은 영향 없음
    max_limit: int = 50  # query·search_vault 결과 개수 상한. 검색 도구(vault_search)가 제외 목록을 거르려고 n×4건(최소 20, 매퍼 기본 n=8이면 32건)을 요청한다
    allow_rerank: bool = False  # false면 리랭킹을 항상 끈다(이 서버에서 리랭킹은 쿼리당 9~21분)
    default_search_type: str = "vec"  # 질의 문자열만 왔을 때 쓰는 검색 방식(lex|vec|hyde)
    cli_timeout_sec: int = 120
    admin_timeout_sec: int = 1800
    force_cpu: bool = False  # CLI 호출에 QMD_FORCE_CPU=1을 붙일지. 기본 끔(GPU 기기에서 느려진 적이 있다)

    def authenticate(self, key: str | None) -> KeyInfo | None:
        """받은 키가 등록돼 있으면 그 정보, 아니면 None. 모든 키와 비교해 걸린 시간으로 키를 짐작하지 못하게 한다."""
        if not key:
            return None
        digest = hash_key(key)
        found: KeyInfo | None = None
        for stored, info in self.keys.items():
            if hmac.compare_digest(stored, digest):
                found = info
        return found


def load_config(path: Path) -> ProxyConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    keys: dict[str, KeyInfo] = {}
    for entry in data.get("keys", []):
        role = entry["role"]
        if role not in ROLES:
            raise ValueError(f"알 수 없는 역할 {role!r} (허용: {ROLES})")
        sha = entry["sha256"].lower()
        if len(sha) != 64:
            raise ValueError(f"키 {entry.get('name')!r}의 sha256이 64자리가 아니다")
        keys[sha] = KeyInfo(name=entry["name"], role=role)
    return ProxyConfig(
        keys=keys,
        allow_admin_tools=bool(data.get("allow_admin_tools", False)),
        collection_roots=tuple(data.get("collection_roots", ())),
        allowed_origins=tuple(data.get("allowed_origins", ())),
        max_limit=int(data.get("max_limit", 50)),
        allow_rerank=bool(data.get("allow_rerank", False)),
        default_search_type=data.get("default_search_type", "vec"),
        cli_timeout_sec=int(data.get("cli_timeout_sec", 120)),
        admin_timeout_sec=int(data.get("admin_timeout_sec", 1800)),
        force_cpu=bool(data.get("force_cpu", False)),
    )
