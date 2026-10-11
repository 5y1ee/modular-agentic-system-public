"""Strands 엔진의 모델 설정과 에이전트 생성.

모델 ID는 환경변수 `MAS_MODEL_ID`로 바꾼다. 기본값은 DevCraft 워크숍이 쓴 ID(`us.anthropic.claude-sonnet-4-6`)다.
이 AWS 계정에서 그 모델의 접근은 켜져 있고 호출이 된다(2026-10-04). 접근이 없는 계정이면 첫 호출에서 오류가 난다.

리전은 직접 지정하지 않는다. BedrockModel이 boto3의 기본 규칙(프로필 설정, `AWS_REGION` 환경변수,
없으면 라이브러리 기본값)으로 정한다. 자격증명도 boto3 기본 규칙을 따르며 이 코드는 읽지 않는다.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
MODEL_ID_ENV = "MAS_MODEL_ID"
MAX_TOKENS = 4096


def model_id() -> str:
    return os.environ.get(MODEL_ID_ENV, DEFAULT_MODEL_ID)


def build_agent(system_prompt: str, tools: Sequence[Any] = (), max_tokens: int = MAX_TOKENS) -> Any:
    """Strands Agent를 만든다. 출력(스트리밍 표시)은 끄고, temperature 0으로 같은 입력에 같은 답을 기대한다.

    Strands는 import가 무거워서 이 함수가 불릴 때만 불러온다.
    """
    from strands import Agent
    from strands.models import BedrockModel

    model = BedrockModel(model_id=model_id(), temperature=0.0, max_tokens=max_tokens)
    return Agent(model=model, system_prompt=system_prompt, tools=list(tools), callback_handler=None)
