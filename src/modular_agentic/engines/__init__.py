"""engines — 단계를 "무엇으로 실행하느냐"의 구현.

core/stages.py의 인터페이스(Analyzer, Mapper, Verifier, Writer)를 실제로 구현하는 코드가
여기 들어간다. 같은 인터페이스를 지키면 엔진을 갈아 끼울 수 있다.

    strands/   Strands Agents SDK + Amazon Bedrock으로 구현 (0단계 목표)
    quick/     Amazon Quick으로 한 단계만 교체하는 시연용 (선택, 미착수)

엔진은 "무슨 업무"는 모른다. 업무별 설정(검색 소스, 대조 소스, 문항 템플릿)은
instances/에서 받아서 쓴다.

이름 메모: 처음엔 backends/라고 지었다가, 서버 쪽 코드로 오해하기 쉬워서 engines/로 바꿨다.
"""
