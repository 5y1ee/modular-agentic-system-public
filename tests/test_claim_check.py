"""문장 단위 근거 검증(core/claim_check.py, engines/strands/claim_checker.py). 가짜 모델로 AWS 없이 검증한다.

보는 것: (1) 글을 문장으로 나누는 규칙 (2) 모델이 댄 인용이 근거에 없으면 코드가 판정을 내린다 (3) 모델이 빠뜨린 문장은 근거 없음으로 채운다
(4) 입력에 없는 번호는 버린다 (5) 입력에 근거·문장이 번호와 함께 들어간다 (6) 보고서는 확인할 문장을 먼저 보인다.
"""



from types import SimpleNamespace  # noqa: E402

from modular_agentic.core.claim_check import FLAGGED, apply_quote_check, flagged, render_check_md, split_sentences, summary  # noqa: E402
from modular_agentic.core.types import Citation, Claim, Evidence, SentenceCheck, Support  # noqa: E402
from modular_agentic.engines.strands.claim_checker import StrandsClaimChecker  # noqa: E402
from modular_agentic.engines.strands.schemas import CheckOut, SentenceOut  # noqa: E402

BRIDGE = "브리지 구조는 결정까지 했고 구현은 아직 시작하지 않았다. 시험에서는 Runtime 안에서 Tailscale로 서버 응답을 받았다."
EVIDENCE = [Evidence("02_concept/AgentCore.md:37", BRIDGE), Evidence("사용자:입사 후 포부", "제조, 자동차 등 모르는 도메인 지식을 학습한다.")]
CLAIMS = [Claim("c13", "현대오토에버는 개발 중심의 기업 문화를 이식하려는 방향을 추진하고 있다.", "문화", [Citation("d1", "…")])]


def check(index, sentence, support, source="", quote="", note=""):
    return SentenceCheck(index, sentence, support, source, quote, note)


# ── 문장 나누기 ──


def test_split_sentences_keeps_headline_decimals_and_drops_length_memo():
    text = "[행동으로 끝나는 헤드라인]\n\n검색이 0.05~0.10초로 줄었습니다. 버전은 v3.0.0입니다! 맞나요? 네.\n둘째 줄입니다.\n\n(헤드라인 포함 756자(줄바꿈 제외), 1,000자 중 244자 여유)\n*글자 수(줄바꿈 제외, 공백 포함): 984자*"
    assert split_sentences(text) == ["[행동으로 끝나는 헤드라인]", "검색이 0.05~0.10초로 줄었습니다.", "버전은 v3.0.0입니다!", "맞나요?", "네.", "둘째 줄입니다."]
    assert split_sentences("  \n\n") == []


# ── 코드의 인용 대조 ──


def test_supported_stays_only_when_the_quote_is_really_in_the_cited_source():
    sources = {"A": "브리지 구조는 결정까지 했고 구현은 아직 시작하지 않았다.", "B": "다른 내용이다 정말로."}
    good = check(1, "s", Support.SUPPORTED, "A", "구현은 아직 시작하지 않았다")
    fake_quote = check(2, "s", Support.SUPPORTED, "A", "브리지 구조를 구현해 서버에 연결했다")  # 지어낸 인용
    wrong_source = check(3, "s", Support.SUPPORTED, "B", "구현은 아직 시작하지 않았다")  # 인용은 A에 있는데 출처를 B라고 함
    unknown_source = check(4, "s", Support.SUPPORTED, "Z", "구현은 아직 시작하지 않았다")
    short_quote = check(5, "s", Support.SUPPORTED, "A", "구현은")  # 10자 미만은 어디에나 맞을 수 있어 인정하지 않는다
    no_quote = check(6, "s", Support.SUPPORTED)

    out = apply_quote_check([good, fake_quote, wrong_source, unknown_source, short_quote, no_quote], sources)

    assert out[0].support is Support.SUPPORTED
    assert all(c.support is Support.UNSUPPORTED for c in out[1:])
    assert "원문에 없다" in out[1].note and "코드가 '근거 없음'으로 내렸다" in out[1].note
    assert "입력 근거에 없다" in out[3].note and "짧다" in out[4].note


def test_quote_check_tolerates_whitespace_and_markdown_emphasis_and_leaves_other_verdicts_alone():
    sources = {"A": "호출마다 드는 **고정 비용**이라\n\n모델을 불러오는 시간이다."}
    ok = check(1, "s", Support.SUPPORTED, "A", "호출마다 드는 고정 비용이라 모델을 불러오는 시간이다")
    partial_bad = check(2, "s", Support.PARTIAL, "A", "이 구절은 원문에 없다 없다 없다", "수치가 없다")
    others = [check(3, "s", Support.UNSUPPORTED, note="근거 없음"), check(4, "s", Support.NO_CLAIM), check(5, "s", Support.CONTRADICTED, note="반대")]

    out = apply_quote_check([ok, partial_bad, *others], sources)

    assert out[0].support is Support.SUPPORTED  # 줄바꿈·** 차이는 같은 구절로 본다
    assert out[1].support is Support.PARTIAL and out[1].note.startswith("수치가 없다") and "인용은" in out[1].note  # 판정은 두고 메모만 붙인다
    assert [c.support for c in out[2:]] == [Support.UNSUPPORTED, Support.NO_CLAIM, Support.CONTRADICTED]


def test_flagged_set_and_summary():
    checks = [check(1, "a", Support.SUPPORTED), check(2, "b", Support.PARTIAL), check(3, "c", Support.NO_CLAIM), check(4, "d", Support.UNSUPPORTED)]
    assert FLAGGED == {Support.PARTIAL, Support.UNSUPPORTED, Support.CONTRADICTED}
    assert [c.index for c in flagged(checks)] == [2, 4]
    assert summary(checks) == "문장 4개 — 뒷받침됨 1 / 일부만 1 / 근거 없음 1 / 사실 주장 없음 1"


# ── 검증기(Strands) ──


def fake_factory(out: CheckOut, prompts: list[str]):
    class FakeAgent:
        def __init__(self, tools):
            assert tools == []  # 검증기는 도구를 쓰지 않는다

        def __call__(self, prompt, structured_output_model=None):
            prompts.append(prompt)
            return SimpleNamespace(structured_output=out)

    return FakeAgent


def sentence_out(index, support, source="", quote="", note=""):
    return SentenceOut(index=index, support=support, source=source, quote=quote, note=note)


TEXT = "[헤드라인]\n\n브리지는 아직 구현하지 않았습니다. 브리지로 외부 접근 문제를 풀었습니다. 도메인을 학습하겠습니다."


def test_checker_numbers_sentences_gives_evidence_and_applies_the_quote_check():
    prompts: list[str] = []
    out = CheckOut(sentences=[
        sentence_out(1, "사실 주장 없음"),
        sentence_out(2, "뒷받침됨", "02_concept/AgentCore.md:37", "구현은 아직 시작하지 않았다"),
        sentence_out(3, "뒷받침됨", "02_concept/AgentCore.md:37", "브리지로 외부 접근 문제를 풀었다고 적혀 있다"),  # 지어낸 인용
        sentence_out(4, "뒷받침됨", "사용자:입사 후 포부", "모르는 도메인 지식을 학습한다"),
    ])

    checks = StrandsClaimChecker(agent_factory=fake_factory(out, prompts)).check(TEXT, EVIDENCE, CLAIMS)

    prompt = prompts[0]
    assert "[1] [헤드라인]" in prompt and "[3] 브리지로 외부 접근 문제를 풀었습니다." in prompt
    assert "[02_concept/AgentCore.md:37] 브리지 구조는 결정까지 했고" in prompt and "[사용자:입사 후 포부]" in prompt and "[c13] 현대오토에버는" in prompt
    assert [c.support for c in checks] == [Support.NO_CLAIM, Support.SUPPORTED, Support.UNSUPPORTED, Support.SUPPORTED]
    assert "원문에 없다" in checks[2].note  # 모델이 지어낸 인용은 코드가 내렸다


def test_checker_fills_missing_sentences_ignores_unknown_numbers_and_keeps_first_duplicate():
    out = CheckOut(sentences=[
        sentence_out(1, "사실 주장 없음"),
        sentence_out(2, "근거 없음", note="근거 없음"),
        sentence_out(2, "뒷받침됨", "02_concept/AgentCore.md:37", "구현은 아직 시작하지 않았다"),  # 같은 번호 두 번째는 무시
        sentence_out(9, "근거와 다름"),  # 없는 번호
    ])  # 3·4번 문장은 판정이 없다

    checks = StrandsClaimChecker(agent_factory=fake_factory(out, [])).check(TEXT, EVIDENCE, [])

    assert [c.index for c in checks] == [1, 2, 3, 4]
    assert checks[1].support is Support.UNSUPPORTED and checks[1].note == "근거 없음"
    assert [c.support for c in checks[2:]] == [Support.UNSUPPORTED, Support.UNSUPPORTED] and "판정하지 않았다" in checks[2].note


def test_checker_does_not_call_the_model_for_empty_text():
    calls: list[str] = []
    assert StrandsClaimChecker(agent_factory=fake_factory(CheckOut(sentences=[]), calls)).check("  \n", EVIDENCE, CLAIMS) == [] and calls == []


# ── 보고서 ──


def test_report_shows_flagged_sentences_first_with_quote_and_note():
    checks = [
        check(1, "괜찮은 문장입니다.", Support.SUPPORTED, "A", "근거에서 옮긴 구절입니다"),
        check(2, "브리지로 풀었습니다.", Support.PARTIAL, "A", "구현은 아직 시작하지 않았다", "구현이 아니라 결정이다"),
        check(3, "다짐입니다.", Support.NO_CLAIM),
    ]
    md = render_check_md("문항 1 초안 1", checks, notes=["근거 2곳을 확인했다"])
    assert md.startswith("# 문장 검증 — 문항 1 초안 1") and "문장 3개 — 뒷받침됨 1 / 일부만 1 / 사실 주장 없음 1" in md
    assert md.index("## 확인할 문장 (1개)") < md.index("⚠ **[2] 일부만**") < md.index("## 나머지 (2개)") < md.index("**[1] 뒷받침됨**")
    assert "메모: 구현이 아니라 결정이다" in md and "- 근거 2곳을 확인했다" in md
    assert "## 확인할 문장 없음" in render_check_md("x", [check(1, "a", Support.SUPPORTED, "A", "충분히 긴 인용 구절입니다")])
