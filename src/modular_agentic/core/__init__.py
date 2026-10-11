"""core — 에이전트 프레임워크를 전혀 모르는 부분.

여기서는 Strands·Quick·LangGraph 등 어떤 프레임워크도 import하지 않는다.
core가 하는 일은 세 가지뿐이다.

    types.py     단계 사이를 오가는 데이터의 이름과 모양
    stages.py    각 단계가 "이런 함수를 가져야 한다"는 인터페이스(Protocol)
    pipeline.py  단계들을 순서대로 호출하는 함수

core가 프레임워크를 모르게 둔 이유: 코어가 특정 프레임워크에 묶이면
다른 엔진(예: Quick)을 붙일 수 없다. 프레임워크 고유 기능(예: Strands의 GraphBuilder)은
engines/ 안에서만 쓴다.
"""
