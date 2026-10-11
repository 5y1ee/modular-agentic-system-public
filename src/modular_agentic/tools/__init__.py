"""tools — 에이전트가 호출하는 도구.

    vault_search.py   knowledge-base를 검색한다. `qmd vsearch`(벡터 검색, 약 5초)를 감싼다.
                      (미구현)

`qmd query`(리랭킹 포함)는 이 서버에서 쿼리당 9~21분이 걸려 쓰지 않는다.
실시간 에이전트 응답에 맞지 않기 때문이다.
"""
