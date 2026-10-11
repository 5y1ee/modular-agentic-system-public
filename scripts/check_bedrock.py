"""Bedrock 모델을 한 번 불러 접근이 되는지 확인한다.

사용: uv run --extra strands python scripts/check_bedrock.py
      (프로필을 쓰면 `AWS_PROFILE=<이름>`, 리전은 `AWS_REGION=<리전>`, 모델은 `MAS_MODEL_ID=<모델 ID>`)

짧은 질문 하나를 보내고 답과 걸린 시간을 보여 준다. 비용은 아주 작지만 AWS 계정에 요청이 나가므로 직접 실행한다.
자주 만나는 오류를 알아보기 쉽게 안내한다. 자격증명은 boto3 기본 규칙으로 찾고 이 스크립트는 읽지 않는다.
"""
import sys
import time

from modular_agentic.engines.strands.config import build_agent, model_id

HINTS = {
    "AccessDenied": "이 계정에 그 모델의 접근이 없거나 IAM 권한(bedrock:InvokeModel)이 없다. 콘솔의 Bedrock 모델 접근 설정을 확인한다.",
    "ValidationException": "모델 ID가 이 리전에서 유효하지 않거나 요청 형식이 맞지 않는다. MAS_MODEL_ID와 AWS_REGION을 확인한다.",
    "ResourceNotFound": "모델 ID나 리전이 맞지 않는다. MAS_MODEL_ID와 AWS_REGION을 확인한다.",
    "NoCredentials": "자격증명을 찾지 못했다. AWS_PROFILE을 지정하거나 `aws configure`로 설정되어 있는지 확인한다.",
    "Throttling": "요청이 너무 많다. 잠시 뒤에 다시 시도한다.",
}


def main() -> None:
    print(f"모델: {model_id()}")
    agent = build_agent("한국어로 한 문장으로만 짧게 답한다.")
    start = time.monotonic()
    try:
        result = agent("연결 확인이야. '연결됨'이라고만 답해줘.")
    except Exception as exc:  # 어떤 오류든 원인을 알아보기 쉽게 보여 주려는 것
        text = f"{type(exc).__name__}: {exc}"
        print(f"\n실패: {text[:600]}")
        for key, hint in HINTS.items():
            if key.lower() in text.lower():
                print(f"안내: {hint}")
        sys.exit(1)
    print(f"\n답: {str(result).strip()}")
    print(f"걸린 시간: {time.monotonic() - start:.1f}초")


if __name__ == "__main__":
    main()
