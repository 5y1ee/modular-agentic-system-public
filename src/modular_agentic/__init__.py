"""Modular Agentic System.

수집 → 분석 → 매핑 → 작성의 4단계에 검증을 끼운 에이전트 파이프라인 템플릿.
도메인(자소서·제안서 등)과 실행 엔진(Strands·Quick 등)을 코드에서 분리해서,
둘 중 하나만 바꿔 끼울 수 있게 하는 것이 목적이다.

패키지 구성 (자세한 설명은 각 하위 패키지 docstring과 README 참고):
    core/       프레임워크를 모르는 부분 — 데이터 타입, 단계 인터페이스, 파이프라인 러너
    engines/    단계를 실제로 실행하는 구현 (Strands 등). 교체 가능한 "무엇으로 돌리나"
    instances/  구체적인 업무 설정 (자소서 등). 교체 가능한 "무슨 업무를 하나"
    tools/      에이전트가 부르는 도구 (knowledge-base 검색 등)
    eval/       골든셋과 채점 — 평가·검증·self-improving의 기준선
"""
