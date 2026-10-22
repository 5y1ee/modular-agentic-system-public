"""BedrockAnalyzer — Strands 없이 Bedrock Converse API를 boto3로 직접 불러 공고를 Requirement 목록으로 쪼갠다.

StrandsAnalyzer와 같은 일(core.stages.Analyzer)을 다른 엔진으로 한 구현이다. 두 구현을 같은 공고로 돌려 결과를 비교하는 데 쓴다
(엔진 축의 교체 증거, registry.py). 규칙(프롬프트의 내용)은 StrandsAnalyzer와 같게 두었고, 엔진 사이에 코드를 공유하지 않으려고 따로 적었다.

업무(자소서·제안서)마다 다른 말·규칙은 업무 설정(instances/profile.py)에서 받는다(기본 자소서).
구조화된 출력은 Converse의 도구 강제 호출(toolChoice)로 받는다: 모델에게 `submit_requirements` 도구 하나만 주고 반드시 부르게 해서,
그 도구 입력(JSON)을 결과로 읽는다. 모델 ID는 Strands 엔진과 같은 환경변수 `MAS_MODEL_ID`를 읽는다(부품 축: 모델).
본문이 비어 있으면 모델을 부르지 않고 빈 목록을 돌려준다(core 규칙).
"""
from __future__ import annotations

import os
from typing import Any

from modular_agentic.core.types import Item, Requirement
from modular_agentic.instances.profile import Profile, profile as get_profile

MODEL_ID_ENV = "MAS_MODEL_ID"
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"  # engines/strands/config.py와 같은 기본값(엔진끼리 import하지 않으려고 따로 둔다)
MAX_TOKENS = 16000  # 요구를 원문 그대로 옮겨 적어 출력이 길다(StrandsAnalyzer와 같은 상한, 4096이면 RFP 발췌에서 잘렸다)
TOOL_NAME = "submit_requirements"

PROMPT_TEMPLATE = """너는 {role}이다. 결과는 submit_requirements 도구로 제출한다.

규칙
- 본문에 실제로 있는 요구만 뽑는다. 없는 요구를 추가하지 않는다.
- 항목 하나에는 요구 하나만 담는다. 여러 요구가 한 줄에 묶여 있으면 의미를 바꾸지 않고 나눈다.
- 원문 표현을 최대한 유지한다. 요약하거나 말을 바꾸지 않는다.
- {scope}
- {kind}
- 요구가 하나도 없으면 빈 목록을 제출한다."""

TOOL_SPEC = {
    "toolSpec": {
        "name": TOOL_NAME,
        "description": "공고에서 뽑은 요구 목록을 제출한다.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"requirements": {"type": "array", "items": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "요구 원문"},
                               "kind": {"type": "string", "description": "공고가 붙인 분류, 없으면 빈 문자열"}},
                "required": ["text", "kind"],
            }}},
            "required": ["requirements"],
        }},
    }
}


def default_client() -> Any:
    import boto3  # 리전·자격증명은 boto3 기본 규칙(프로필·AWS_REGION)을 따른다

    return boto3.client("bedrock-runtime")


class BedrockAnalyzer:
    """core.stages.Analyzer를 Bedrock Converse API 직접 호출로 구현한다."""

    def __init__(self, *, profile: Profile | None = None, client: Any = None) -> None:
        self.profile = profile or get_profile()
        self._client = client

    def system_prompt(self) -> str:
        p = self.profile
        return PROMPT_TEMPLATE.format(role=p.analyzer_role, scope=p.analyzer_scope, kind=p.analyzer_kind)

    def analyze(self, item: Item) -> list[Requirement]:
        if not item.body.strip():
            return []
        client = self._client or default_client()
        response = client.converse(
            modelId=os.environ.get(MODEL_ID_ENV, DEFAULT_MODEL_ID),
            system=[{"text": self.system_prompt()}],
            messages=[{"role": "user", "content": [{"text": f"{self.profile.item_label} 제목: {item.title}\n\n본문:\n{item.body}"}]}],
            inferenceConfig={"temperature": 0.0, "maxTokens": MAX_TOKENS},
            toolConfig={"tools": [TOOL_SPEC], "toolChoice": {"tool": {"name": TOOL_NAME}}},
        )
        submitted = next((block["toolUse"]["input"] for block in response["output"]["message"]["content"]
                          if "toolUse" in block and block["toolUse"].get("name") == TOOL_NAME), None)
        if submitted is None:
            raise RuntimeError(f"모델이 {TOOL_NAME}를 부르지 않았다(stopReason={response.get('stopReason')})")
        texts = [(str(r.get("text", "")).strip(), str(r.get("kind", "")).strip()) for r in submitted.get("requirements", [])]
        return [Requirement(id=f"{item.id}-r{i}", item_id=item.id, text=text, kind=kind)
                for i, (text, kind) in enumerate([t for t in texts if t[0]], 1)]
