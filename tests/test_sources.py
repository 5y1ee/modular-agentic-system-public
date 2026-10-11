"""tools/sources: 조사 자료 모으기. 네트워크·모델 없이 가짜 가져오기·가짜 전사로 검증한다.

핵심: (1) 못 읽은 자료는 버리지 않고 이유와 함께 남는다 (2) PDF는 추출(A)을 먼저 하고 글자가 거의 없으면 전사(B)로 넘어간다
(3) 이미지는 바로 전사, 어느 방식으로 얻은 텍스트인지가 text_origin에 남는다 (4) 파일별로 방식을 지정할 수 있다
(5) URL은 http·https 공개 주소만 읽는다 (6) 웹 실패(로그인·차단·JS 렌더링)는 이유가 사용자에게 보이는 말로 남는다.
"""
import io
import urllib.error

import pytest

from modular_agentic.core.types import Item, ReadStatus, SourceKind, TextOrigin
from modular_agentic.tools import sources
from modular_agentic.tools.sources import (
    EXTRACT,
    TRANSCRIBE,
    ExtractorUnavailable,
    check_public_url,
    detect_image_format,
    collect_docs,
    fetch_url,
    html_to_text,
    load_upload_dir,
    number_docs,
    posting_doc,
    split_tall_image,
)


class FakeTranscriber:
    def __init__(self, text="전사된 글", error=None):
        self.text = text
        self.error = error
        self.calls: list[tuple[str, str, bytes]] = []

    def transcribe(self, data, *, kind, fmt):
        self.calls.append((kind, fmt, data))
        if self.error:
            raise self.error
        return self.text


PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"data"  # 형식 판별 시험용 가짜 바이트(실제 이미지가 아니다)
JPEG_MAGIC = b"\xff\xd8\xff" + b"data"


def real_image(fmt: str = "PNG", size=(40, 30), bars: list[tuple[int, int]] | None = None, noise: bool = False) -> bytes:
    """Pillow로 실제 이미지를 만든다. bars는 (시작 y, 높이)마다 검은 띠를 그린다(나머지는 흰 배경 = 빈 줄)."""
    pytest.importorskip("PIL")
    import random

    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, "white")
    if noise:
        rng = random.Random(1)
        image.putdata([(rng.randrange(256),) * 3 for _ in range(size[0] * size[1])])
    draw = ImageDraw.Draw(image)
    for y, h in bars or []:
        draw.rectangle((0, y, size[0], y + h - 1), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def make_pdf(text: str | None) -> bytes:
    """pypdf로 PDF를 만든다. text가 None이면 글자가 없는 빈 쪽(스캔본처럼 텍스트 층이 없는 PDF)."""
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    if text is not None:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ── 공고·번호 ──


def test_posting_doc_uses_body_and_marks_empty_body_unread():
    ok = posting_doc(Item(id="i", source="t", title="제목", body="본문"))
    assert (ok.kind, ok.text, ok.status) == (SourceKind.POSTING, "본문", ReadStatus.READ)
    empty = posting_doc(Item(id="i", source="t", title="제목", body="  \n"))
    assert empty.status is ReadStatus.UNREAD and "비어" in empty.reason


def test_number_docs_gives_d1_d2():
    docs = number_docs([posting_doc(Item(id="i", source="t", title="a", body="x")), posting_doc(Item(id="i", source="t", title="b", body="y"))])
    assert [d.id for d in docs] == ["d1", "d2"]


# ── 업로드 폴더 ──


def test_missing_dir_gives_no_docs(tmp_path):
    assert load_upload_dir(tmp_path / "없음") == []


def test_text_files_are_read_as_original_and_hidden_files_skipped(tmp_path):
    (tmp_path / "기사.md").write_text("회사는 클라우드를 판다", encoding="utf-8")
    (tmp_path / ".DS_Store").write_text("x", encoding="utf-8")
    [doc] = load_upload_dir(tmp_path)
    assert doc.text == "회사는 클라우드를 판다" and doc.text_origin is TextOrigin.ORIGINAL and doc.kind is SourceKind.UPLOAD
    assert doc.locator.endswith("기사.md")


def test_non_utf8_and_unsupported_files_are_kept_as_unread_with_reason(tmp_path):
    (tmp_path / "a.txt").write_bytes("한글".encode("cp949"))
    (tmp_path / "b.docx").write_bytes(b"PK")
    docs = {d.locator.split("/")[-1]: d for d in load_upload_dir(tmp_path)}
    assert docs["a.txt"].status is ReadStatus.UNREAD and "UTF-8" in docs["a.txt"].reason
    assert docs["b.docx"].status is ReadStatus.UNREAD and ".docx" in docs["b.docx"].reason


def test_image_goes_straight_to_transcription_and_is_marked_transcribed(tmp_path):
    jpeg = real_image("JPEG")
    (tmp_path / "캡처.jpg").write_bytes(jpeg)
    t = FakeTranscriber("로그인 뒤 페이지의 글")
    [doc] = load_upload_dir(tmp_path, transcriber=t)
    assert doc.text == "로그인 뒤 페이지의 글" and doc.text_origin is TextOrigin.TRANSCRIBED
    assert t.calls == [("image", "jpeg", jpeg)]  # 짧은 이미지는 원본 바이트 그대로 한 번에  # jpg는 Bedrock이 받는 이름 jpeg로


def test_image_without_transcriber_is_unread(tmp_path):
    (tmp_path / "a.png").write_bytes(PNG_MAGIC)
    [doc] = load_upload_dir(tmp_path)
    assert doc.status is ReadStatus.UNREAD and "전사 도구" in doc.reason


def test_transcriber_failure_and_empty_result_do_not_stop_other_files(tmp_path):
    (tmp_path / "a.png").write_bytes(real_image())
    (tmp_path / "b.txt").write_text("멀쩡한 글", encoding="utf-8")
    [bad, good] = load_upload_dir(tmp_path, transcriber=FakeTranscriber(error=RuntimeError("모델 오류")))
    assert bad.status is ReadStatus.UNREAD and "전사 실패" in bad.reason and "모델 오류" in bad.reason
    assert good.text == "멀쩡한 글"
    (tmp_path / "b.txt").unlink()
    [blank] = load_upload_dir(tmp_path, transcriber=FakeTranscriber(text="  "))
    assert blank.status is ReadStatus.UNREAD and "비어" in blank.reason


def test_image_format_comes_from_the_bytes_not_the_extension(tmp_path):
    assert detect_image_format(PNG_MAGIC) == "png" and detect_image_format(JPEG_MAGIC) == "jpeg"
    assert detect_image_format(b"GIF89a....") == "gif" and detect_image_format(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert detect_image_format(b"not an image") is None
    (tmp_path / "공고.webp").write_bytes(real_image("PNG"))  # CDN이 .webp 주소로 PNG를 내려준 실제 사례
    t = FakeTranscriber()
    load_upload_dir(tmp_path, transcriber=t)
    assert t.calls[0][:2] == ("image", "png")


def test_image_with_unknown_content_is_unread_even_if_the_extension_looks_right(tmp_path):
    (tmp_path / "깨짐.png").write_bytes("html 에러 페이지".encode())
    t = FakeTranscriber()
    [doc] = load_upload_dir(tmp_path, transcriber=t)
    assert doc.status is ReadStatus.UNREAD and "형식을 알 수 없다" in doc.reason and t.calls == []


# ── PDF: 추출(A)과 전사(B) ──


def test_text_pdf_is_extracted_without_calling_the_transcriber(tmp_path):
    (tmp_path / "회사소개.pdf").write_bytes(make_pdf("Acme sells cloud software to banks and insurers worldwide"))
    t = FakeTranscriber()
    [doc] = load_upload_dir(tmp_path, transcriber=t)
    assert doc.text_origin is TextOrigin.EXTRACTED and "Acme sells cloud software" in doc.text
    assert t.calls == []


def test_scanned_pdf_falls_back_to_transcription_and_keeps_the_reason_when_that_fails(tmp_path):
    (tmp_path / "스캔.pdf").write_bytes(make_pdf(None))
    t = FakeTranscriber("스캔본에서 옮긴 글")
    [doc] = load_upload_dir(tmp_path, transcriber=t)
    assert doc.text_origin is TextOrigin.TRANSCRIBED and doc.text == "스캔본에서 옮긴 글"
    assert t.calls[0][:2] == ("document", "pdf")
    [unread] = load_upload_dir(tmp_path)  # 전사 도구가 없으면 둘 다 안 된 이유가 같이 남는다
    assert unread.status is ReadStatus.UNREAD and "스캔본" in unread.reason and "전사 도구" in unread.reason


def test_force_transcribe_skips_extraction_and_force_extract_never_transcribes(tmp_path):
    (tmp_path / "a.pdf").write_bytes(make_pdf("Acme sells cloud software to banks and insurers worldwide"))
    t = FakeTranscriber("모델이 읽은 글")
    [forced] = load_upload_dir(tmp_path, transcriber=t, force={"a.pdf": TRANSCRIBE})
    assert forced.text_origin is TextOrigin.TRANSCRIBED and len(t.calls) == 1

    (tmp_path / "a.pdf").write_bytes(make_pdf(None))
    t2 = FakeTranscriber()
    [only_extract] = load_upload_dir(tmp_path, transcriber=t2, force={"a.pdf": EXTRACT})
    assert only_extract.status is ReadStatus.UNREAD and t2.calls == []


def test_pdf_without_pypdf_goes_to_transcription(tmp_path, monkeypatch):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 fake")

    def unavailable(data):
        raise ExtractorUnavailable("pypdf가 설치돼 있지 않다")

    monkeypatch.setattr(sources, "extract_pdf_text", unavailable)
    t = FakeTranscriber("전사 결과")
    [doc] = load_upload_dir(tmp_path, transcriber=t)
    assert doc.text_origin is TextOrigin.TRANSCRIBED
    [unread] = load_upload_dir(tmp_path)
    assert "pypdf" in unread.reason and "전사 도구" in unread.reason


def test_broken_pdf_is_not_fatal(tmp_path):
    pytest.importorskip("pypdf")
    (tmp_path / "깨짐.pdf").write_bytes(b"not a pdf")
    [doc] = load_upload_dir(tmp_path)
    assert doc.status is ReadStatus.UNREAD


# ── 웹 ──


HTML = "<html><head><title>t</title><style>.a{}</style><script>var x=1;</script></head><body><h1>회사 소개</h1><p>" + "클라우드 사업을 한다. " * 30 + "</p></body></html>"


def fake_get(status=200, content_type="text/html; charset=utf-8", body=HTML.encode()):
    return lambda url: (status, content_type, body)


def test_html_to_text_drops_scripts_and_styles_and_splits_blocks():
    text = html_to_text(HTML)
    assert "var x" not in text and ".a{}" not in text
    assert "회사 소개" in text and "클라우드 사업을 한다." in text


def test_fetch_url_reads_html_body():
    doc = fetch_url("https://example.com/about", get=fake_get())
    assert doc.status is ReadStatus.READ and doc.kind is SourceKind.WEB and doc.locator == "https://example.com/about"
    assert "회사 소개" in doc.text and doc.text_origin is TextOrigin.ORIGINAL


@pytest.mark.parametrize("status", [401, 403])
def test_login_wall_or_block_is_reported_with_what_to_do(status):
    doc = fetch_url("https://example.com", get=fake_get(status=status, body=b""))
    assert doc.status is ReadStatus.UNREAD and str(status) in doc.reason and "입력 폴더" in doc.reason


def test_other_http_errors_and_js_rendered_pages_are_unread_with_reasons():
    assert "404" in fetch_url("https://example.com", get=fake_get(status=404, body=b"")).reason
    shell = fetch_url("https://example.com", get=fake_get(body=b"<html><body><div id=root></div><script>app()</script></body></html>"))
    assert shell.status is ReadStatus.UNREAD and "JS" in shell.reason


def test_pdf_and_binary_responses_are_unread_with_hint():
    assert "PDF" in fetch_url("https://example.com/a.pdf", get=fake_get(content_type="application/pdf", body=b"%PDF")).reason
    assert "읽지 않는" in fetch_url("https://example.com/a.zip", get=fake_get(content_type="application/zip", body=b"PK")).reason


def test_refused_and_unreachable_urls_are_unread_not_exceptions():
    def refused(url):
        raise ValueError("내부망·루프백 주소는 읽지 않는다: localhost")

    def down(url):
        raise urllib.error.URLError("연결 안 됨")

    assert "내부망" in fetch_url("http://localhost/", get=refused).reason
    assert "접속 실패" in fetch_url("https://example.com", get=down).reason


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com/a", "file:///etc/passwd", "http://127.0.0.1/", "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data", "http://[::1]/", "http:///nohost"],
)
def test_non_public_or_non_http_urls_are_refused(url):
    with pytest.raises(ValueError):
        check_public_url(url)


def test_public_literal_ip_is_allowed():
    check_public_url("https://8.8.8.8/")  # 이름 해석 없이 통과해야 한다(네트워크 필요 없음)


def test_collect_docs_numbers_posting_uploads_and_urls_in_order(tmp_path):
    (tmp_path / "a.txt").write_text("업로드 글", encoding="utf-8")
    item = Item(id="i", source="t", title="공고", body="공고 본문")
    docs = collect_docs(item, upload_dir=tmp_path, urls=["https://example.com/x"], get=fake_get())
    assert [d.id for d in docs] == ["d1", "d2", "d3"]
    assert [d.kind for d in docs] == [SourceKind.POSTING, SourceKind.UPLOAD, SourceKind.WEB]


# ── 세로로 긴 이미지: 조각으로 잘라 전사 ──


def _rows(png: bytes) -> list[int]:
    """조각의 각 줄이 검은 줄이면 1, 아니면 0."""
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("L")
    return [1 if image.getpixel((0, y)) < 128 else 0 for y in range(image.height)]


def test_short_image_is_not_split_or_reencoded():
    data = real_image(size=(60, 200))
    assert split_tall_image(data, max_height=900) == [data]


def test_tall_image_is_cut_at_blank_rows_so_no_band_is_split():
    bars = [(y, 30) for y in range(100, 3900, 200)]  # 200px마다 30px 띠, 사이는 빈 줄
    tiles = split_tall_image(real_image(size=(80, 4000), bars=bars), max_height=900)
    heights = [len(_rows(t)) for t in tiles]
    assert len(tiles) >= 5 and all(h <= 900 for h in heights) and sum(heights) == 4000  # 빠지거나 겹치는 줄이 없다
    all_rows = [r for t in tiles for r in _rows(t)]
    assert all_rows == [1 if any(y <= i < y + h for y, h in bars) else 0 for i in range(4000)]  # 이어 붙이면 원본과 같다
    boundary = 0
    for h in heights[:-1]:
        boundary += h
        assert not (all_rows[boundary - 1] and all_rows[boundary])  # 경계에서 띠가 반으로 갈라지지 않는다


def test_without_blank_rows_it_cuts_at_the_max_height():
    tiles = split_tall_image(real_image(size=(60, 2300), noise=True), max_height=900)
    assert [len(_rows(t)) for t in tiles] == [900, 900, 500]


def test_tall_image_is_transcribed_tile_by_tile_and_joined_in_order(tmp_path):
    bars = [(y, 30) for y in range(100, 3100, 200)]
    (tmp_path / "공고.png").write_bytes(real_image(size=(80, 3200), bars=bars))

    class Numbered(FakeTranscriber):
        def transcribe(self, data, *, kind, fmt):
            super().transcribe(data, kind=kind, fmt=fmt)
            return f"조각{len(self.calls)}"

    t = Numbered()
    [doc] = load_upload_dir(tmp_path, transcriber=t)
    n = len(t.calls)
    assert n >= 3 and all(c[:2] == ("image", "png") for c in t.calls)
    assert doc.text == "\n\n".join(f"조각{i}" for i in range(1, n + 1)) and doc.text_origin is TextOrigin.TRANSCRIBED


def test_one_failed_tile_makes_the_whole_image_unread_instead_of_a_partial_text(tmp_path):
    (tmp_path / "공고.png").write_bytes(real_image(size=(80, 3200), bars=[(y, 30) for y in range(100, 3100, 200)]))

    class FailsSecond(FakeTranscriber):
        def transcribe(self, data, *, kind, fmt):
            super().transcribe(data, kind=kind, fmt=fmt)
            if len(self.calls) == 2:
                raise RuntimeError("모델 오류")
            return "글"

    [doc] = load_upload_dir(tmp_path, transcriber=FailsSecond())
    assert doc.status is ReadStatus.UNREAD and doc.text == "" and "조각 2/" in doc.reason and "모델 오류" in doc.reason


def test_images_are_unread_when_pillow_is_missing_rather_than_read_in_one_wrong_shot(tmp_path, monkeypatch):
    (tmp_path / "공고.png").write_bytes(PNG_MAGIC)

    def no_pillow(data, **kwargs):
        raise ExtractorUnavailable("Pillow가 설치돼 있지 않다")

    monkeypatch.setattr(sources, "split_tall_image", no_pillow)
    t = FakeTranscriber()
    [doc] = load_upload_dir(tmp_path, transcriber=t)
    assert doc.status is ReadStatus.UNREAD and "Pillow" in doc.reason and t.calls == []


def test_broken_image_is_unread_with_a_reason(tmp_path):
    pytest.importorskip("PIL")
    (tmp_path / "깨짐.png").write_bytes(PNG_MAGIC)  # 첫 바이트만 PNG이고 내용은 이미지가 아니다
    [doc] = load_upload_dir(tmp_path, transcriber=FakeTranscriber())
    assert doc.status is ReadStatus.UNREAD and "열 수 없다" in doc.reason
