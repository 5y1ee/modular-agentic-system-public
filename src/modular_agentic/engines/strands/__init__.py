"""Strands Agents SDK + Amazon Bedrock로 구현한 엔진.

core/stages.py의 인터페이스를 구현한다. 지금 구현된 것:

    analyzer.py     StrandsAnalyzer   공고 본문 → Requirement 목록 (모델이 정해진 형식으로 답함)
    mapper.py       StrandsMapper     Requirement마다 vault를 검색해 확인/부분확인/TODO를 판단
    verifier.py     StrandsVerifier   매핑 근거가 말하는 경험을 vault 전체에서 다시 모아 모순·과장을 찾아 승인/플래그
    writer.py       StrandsWriter     매핑·검증 결과로 문항에 대한 초안 여러 개를 서로 다른 각도로 씀(피드백 지침 주입 자리 있음)
    guidance.py     StrandsGuidanceProposer  사용자 피드백 기록에서 작성 지침 후보를 뽑음(승인은 사용자, core/feedback.py)
    search_tool.py  search_vault      매핑·검증 에이전트가 부르는 검색 도구 (tools/vault_search를 감쌈)
    schemas.py      모델이 채워서 돌려주는 답의 형식(Pydantic)
    config.py       모델 설정과 에이전트 생성


설계 포인트
- 에이전트를 만드는 함수(`agent_factory`)를 주입할 수 있다. 테스트에서는 AWS를 부르지 않는 가짜 에이전트를 넣는다.
- 매핑은 모델이 근거를 지어내지 못하게 한다. 모델이 인용한 근거는 **실제 검색 결과에 있던 것만** 인정하고,
  인용문은 모델이 쓴 문장이 아니라 검색 결과의 실제 발췌로 바꾼다. (mapper.build_mapping)
- 요구 하나마다 에이전트를 새로 만든다. 요구끼리 대화 기록이 섞이지 않게 하려는 것이다.

이 하위 패키지 이름이 `strands`여도 안에서 `from strands import ...`는 외부 패키지를 가리킨다(절대 import).
"""
