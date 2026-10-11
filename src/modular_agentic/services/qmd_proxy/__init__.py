"""qmd MCP 프록시 — qmd 앞에 서서 인증·도구 범위·인자를 통제하는 얇은 MCP 서버.

왜 필요한가 (docs/agentcore-research.md, 2026-10-05)
  AgentCore Gateway에 qmd를 MCP 서버 대상으로 붙이려면 다음이 부족하다.
  - qmd의 HTTP 서버는 **인증이 없다**(README: "put your own auth in front"). → 프록시가 API 키를 받는다.
  - qmd `query`의 `rerank` 기본값이 true라 이 서버에서는 호출마다 9~21분이 걸린다. 모델이 인자를 채우므로 도구 정의만으로 못 막는다.
    → 프록시가 리랭킹을 강제로 끈다.
  - qmd는 모르는 인자를 조용히 무시한다(`collection` 단수형을 주면 범위가 풀림). → 프록시가 오류로 알려 준다.
  - 나중에 사용자별 범위(RBAC)를 거는 자리가 필요하다. → 키 → 역할 → 보이는 도구, 컬렉션 범위를 이 한 곳에서 통제한다.

구성 (웹을 아는 부분은 server.py 하나)
  config.py    키(해시)·역할·허용 범위·한도         backend.py   qmd를 부르는 두 길(MCP, CLI)
  registry.py  도구 목록·인자 검사·실행              protocol.py  MCP JSON-RPC 처리(순수 함수)
  server.py    HTTP·인증·Origin(Starlette, extra `proxy`)

실행: `uv run --extra proxy python scripts/run_qmd_proxy.py serve --config <설정 JSON>`. 키 만들기: `... make-key`.
노출하는 도구와 일부러 노출하지 않는 것(`collection update-cmd` 등)은 registry.py의 docstring.
"""
