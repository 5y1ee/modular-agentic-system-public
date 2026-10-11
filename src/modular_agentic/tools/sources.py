"""조사에 쓸 자료(SourceDoc)를 모은다. 판단은 하지 않고 가져오기만 한다(읽고 판단하는 일은 Researcher의 일).

세 갈래
  공고      Item.body를 자료로 만든다(posting_doc).
  업로드    입력 폴더(기본 inputs/<item_id>/)의 파일을 읽는다(load_upload_dir).
            .txt·.md는 글자 그대로. PDF·이미지는 아래 두 방식.
  웹        URL의 본문을 가져온다(fetch_url). 못 읽으면 이유와 함께 "못 읽음" 자료로 남긴다.
            사용자가 준 URL과, 검색으로 찾은 URL(tools/source_search.py)을 같이 읽는다. 찾은 것에는 found_by가 붙는다.

PDF·이미지를 읽는 두 방식 (docs/design-research-stage.md 7.1절)
  A 추출    PDF의 텍스트 층을 라이브러리(pypdf, `sources` extra)로 뽑는다. 글자가 정확하고 비용이 없다.
  B 전사    이미지·PDF를 모델에 보여 글자를 텍스트로 옮겨 적게 한다(Transcriber). 캡처·스캔본도 읽지만 비용이 들고 읽기 오류가 섞일 수 있다.
  PDF는 A를 먼저 시도하고 글자가 거의 없으면(스캔본으로 보면) B로 넘어간다. 이미지는 바로 B다.
  파일별로 방식을 지정할 수 있다(force). 어느 방식으로 얻은 텍스트인지는 SourceDoc.text_origin에 남는다.
  세로로 긴 이미지(대기업 공채 공고는 8000px짜리 스크린샷이 흔하다)는 한 번에 보여 주면 작은 글씨를 흐릿하게 보고 비슷한 단어로
  채워 넣는다(2026-10-10 실측: 분야 이름·직무명이 여러 곳 틀렸다). 그래서 빈 줄에서 조각으로 잘라 조각마다 전사한다(split_tall_image, Pillow 필요).

못 읽은 자료를 버리지 않는 이유: 로그인 벽·봇 차단·JS 렌더링 때문에 자동으로 못 읽는 일이 흔하다. 조용히 넘기면
사용자가 자료가 빠진 줄 모른다. 못 읽음 + 이유로 남겨 보고서에서 "이 자료를 직접 주세요"라고 보여 준다.

보안: URL은 외부 입력이다. http·https만 받고, 내부망·루프백 주소는 거절하며, 리다이렉트로 내부망에 가는 것도 막는다.
(이름 해석 직후 실제 접속 사이에 주소가 바뀌는 공격까지는 막지 못한다. 개인 프로젝트 수준의 방어다.)
외부 프로그램을 부르지 않고 shell을 거치지 않는다.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol

from modular_agentic.core.types import Item, ReadStatus, SourceDoc, SourceKind, TextOrigin
from modular_agentic.tools.source_search import SearchHit, normalize_url

TEXT_SUFFIXES = {".txt", ".md"}
PDF_SUFFIX = ".pdf"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}  # 이미지로 다룰 확장자. 실제 형식은 내용으로 정한다(detect_image_format)

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_WEB_BYTES = 2 * 1024 * 1024
WEB_TIMEOUT_SECONDS = 15

# 아래 두 값은 근거 없는 임의의 시작값이다. 실제 PDF·웹 페이지로 보고 조정한다(설계 문서 15절).
MIN_PDF_CHARS_PER_PAGE = 30  # PDF 한 쪽당 이보다 적게 뽑히면 스캔본으로 보고 전사로 넘긴다
MIN_WEB_TEXT_CHARS = 200  # 웹 본문이 이보다 짧으면 JS로 그려지는 페이지이거나 차단된 것으로 본다

# 근거 없는 임의의 시작값이다. 1400px 안팎 조각은 작은 글씨도 또렷이 읽혔고(2026-10-10), 8000px 한 장은 틀렸다. 값 자체는 더 시험해 조정한다.
MAX_TILE_HEIGHT = 1400
CUT_SEARCH_WINDOW = 400  # 조각 끝에서 이만큼 위까지 글이 없는 빈 줄을 찾는다
BLANK_ROW_SPREAD = 8  # 한 줄의 밝기(0~255) 최댓값과 최솟값 차이가 이 이하면 빈 줄로 본다

EXTRACT = "extract"
TRANSCRIBE = "transcribe"


class Transcriber(Protocol):
    """이미지·PDF 바이트를 보고 글자를 텍스트로 옮겨 적는다(B 방식). 구현은 engines/에 있다."""

    def transcribe(self, data: bytes, *, kind: str, fmt: str) -> str:
        """kind는 "image" 또는 "document", fmt는 "png"·"jpeg"·"pdf" 등."""
        ...


class ExtractorUnavailable(RuntimeError):
    """PDF 추출 라이브러리(pypdf)나 이미지 도구(Pillow)가 설치돼 있지 않다."""


def _doc(kind: SourceKind, locator: str, *, text: str = "", origin: TextOrigin = TextOrigin.ORIGINAL, reason: str = "") -> SourceDoc:
    """id는 비워 둔다. number_docs()가 한꺼번에 붙인다."""
    status = ReadStatus.UNREAD if reason else ReadStatus.READ
    return SourceDoc(id="", kind=kind, locator=locator, text=text, text_origin=origin, status=status, reason=reason)


def number_docs(docs: Iterable[SourceDoc]) -> list[SourceDoc]:
    """자료에 d1, d2… 번호를 붙인다. Citation이 이 id로 자료를 가리킨다."""
    return [replace(d, id=f"d{i}") for i, d in enumerate(docs, 1)]


# ── 공고 ────────────────────────────────────────────────────────────────────


def posting_doc(item: Item) -> SourceDoc:
    if not item.body.strip():
        return _doc(SourceKind.POSTING, item.title, reason="공고 본문이 비어 있다")
    return _doc(SourceKind.POSTING, item.title, text=item.body)


def detect_image_format(data: bytes) -> str | None:
    """파일 내용의 첫 바이트로 이미지 형식을 알아낸다(Bedrock이 받는 이름: png·jpeg·gif·webp). 모르면 None.

    확장자를 믿지 않는 이유: 이미지 CDN이 `.webp` 주소로 PNG를 내려주는 일이 실제로 있었다(2026-10-10, 공고 이미지).
    형식 이름이 실제 내용과 다르면 Bedrock이 요청을 거절할 수 있다.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def split_tall_image(data: bytes, *, max_height: int = MAX_TILE_HEIGHT) -> list[bytes]:
    """세로로 긴 이미지를 PNG 조각들로 자른다. 높이가 max_height 이하면 원본 바이트 하나를 그대로 돌려준다.

    자르는 위치: 조각 끝에서 CUT_SEARCH_WINDOW 안의 가장 아래쪽 빈 줄(밝기가 거의 일정한 줄). 문단 사이 여백에서 잘려 글자가 반으로
    갈라지지 않는다. 빈 줄이 없으면(사진 등) 그냥 max_height에서 자른다 — 그 경계에 걸친 글은 읽기 오류가 날 수 있다.
    Pillow가 없으면 ExtractorUnavailable.
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise ExtractorUnavailable("Pillow가 설치돼 있지 않다(`--extra sources`)") from exc
    import io

    image = Image.open(io.BytesIO(data))
    width, height = image.size
    if height <= max_height:
        return [data]
    gray = image.convert("L")
    tiles: list[bytes] = []
    start = 0
    while start < height:
        end = min(start + max_height, height)
        if end < height:
            floor = max(start + max_height - CUT_SEARCH_WINDOW, start + 1)
            for y in range(end, floor, -1):
                low, high = gray.crop((0, y, width, y + 1)).getextrema()
                if high - low <= BLANK_ROW_SPREAD:
                    end = y
                    break
        buffer = io.BytesIO()
        image.crop((0, start, width, end)).save(buffer, format="PNG")
        tiles.append(buffer.getvalue())
        start = end
    return tiles


# ── PDF 추출(A) ─────────────────────────────────────────────────────────────


def extract_pdf_text(data: bytes) -> tuple[str, int]:
    """PDF의 텍스트 층을 뽑는다. (텍스트, 쪽수). pypdf가 없으면 ExtractorUnavailable."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractorUnavailable("pypdf가 설치돼 있지 않다(`--extra sources`)") from exc
    import io

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(pages), len(pages)


def _enough_pdf_text(text: str, pages: int) -> bool:
    return pages > 0 and len(text.strip()) >= MIN_PDF_CHARS_PER_PAGE * pages


# ── 업로드 폴더 ─────────────────────────────────────────────────────────────


def _transcribe(path: Path, data: bytes, *, kind: str, fmt: str, transcriber: Transcriber | None) -> SourceDoc:
    if transcriber is None:
        return _doc(SourceKind.UPLOAD, str(path), reason="이미지·스캔본을 읽을 전사 도구가 연결되지 않았다")
    try:
        text = transcriber.transcribe(data, kind=kind, fmt=fmt)
    except Exception as exc:  # 모델 호출 실패는 종류가 다양하다. 한 파일이 실패해도 나머지는 계속 읽는다.
        return _doc(SourceKind.UPLOAD, str(path), reason=f"전사 실패: {type(exc).__name__}: {str(exc)[:200]}")
    if not text.strip():
        return _doc(SourceKind.UPLOAD, str(path), reason="전사 결과가 비어 있다")
    return _doc(SourceKind.UPLOAD, str(path), text=text, origin=TextOrigin.TRANSCRIBED)


def _transcribe_image(path: Path, data: bytes, *, fmt: str, transcriber: Transcriber | None) -> SourceDoc:
    """이미지를 전사한다. 긴 이미지는 조각으로 잘라 조각마다 전사해 이어 붙인다. 한 조각이라도 실패하면 일부만 쓰지 않고 못 읽음으로 둔다."""
    if transcriber is None:
        return _transcribe(path, data, kind="image", fmt=fmt, transcriber=None)
    try:
        tiles = split_tall_image(data)
    except ExtractorUnavailable as exc:
        return _doc(SourceKind.UPLOAD, str(path), reason=f"이미지 크기를 확인할 도구가 없어 읽지 않았다(긴 이미지를 한 번에 읽으면 틀린 글이 섞인다): {exc}")
    except Exception as exc:  # 깨진 이미지 등
        return _doc(SourceKind.UPLOAD, str(path), reason=f"이미지를 열 수 없다: {type(exc).__name__}")
    if len(tiles) == 1:
        return _transcribe(path, data, kind="image", fmt=fmt, transcriber=transcriber)
    texts: list[str] = []
    for number, tile in enumerate(tiles, 1):
        piece = _transcribe(path, tile, kind="image", fmt="png", transcriber=transcriber)
        if piece.status is ReadStatus.UNREAD:
            return replace(piece, reason=f"조각 {number}/{len(tiles)}: {piece.reason}")
        texts.append(piece.text.strip())
    return _doc(SourceKind.UPLOAD, str(path), text="\n\n".join(texts), origin=TextOrigin.TRANSCRIBED)


def _read_pdf(path: Path, data: bytes, mode: str | None, transcriber: Transcriber | None) -> SourceDoc:
    extract_note = ""
    if mode != TRANSCRIBE:
        try:
            text, pages = extract_pdf_text(data)
        except ExtractorUnavailable as exc:
            extract_note = str(exc)
        except Exception as exc:  # 깨진 PDF 등. pypdf가 던지는 예외 종류가 많다.
            extract_note = f"추출 실패: {type(exc).__name__}"
        else:
            if _enough_pdf_text(text, pages):
                return _doc(SourceKind.UPLOAD, str(path), text=text, origin=TextOrigin.EXTRACTED)
            extract_note = f"텍스트 층에서 글자가 거의 안 뽑혔다({len(text.strip())}자/{pages}쪽, 스캔본으로 보임)"
        if mode == EXTRACT:
            return _doc(SourceKind.UPLOAD, str(path), reason=extract_note)
    doc = _transcribe(path, data, kind="document", fmt="pdf", transcriber=transcriber)
    if doc.status is ReadStatus.UNREAD and extract_note:
        return replace(doc, reason=f"{extract_note}; {doc.reason}")
    return doc


def read_upload_file(path: Path, *, transcriber: Transcriber | None = None, mode: str | None = None) -> SourceDoc:
    """파일 하나를 자료로 읽는다. 못 읽으면 이유와 함께 못 읽음 자료를 돌려준다(예외로 끝내지 않는다).

    mode: "extract"(PDF는 A만) 또는 "transcribe"(PDF는 B만). 없으면 자동 규칙.
    """
    suffix = path.suffix.lower()
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return _doc(SourceKind.UPLOAD, str(path), reason=f"파일이 너무 크다({size // (1024 * 1024)}MB)")
    if suffix in TEXT_SUFFIXES:
        try:
            return _doc(SourceKind.UPLOAD, str(path), text=path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            return _doc(SourceKind.UPLOAD, str(path), reason="UTF-8 텍스트가 아니다")
    if suffix == PDF_SUFFIX:
        return _read_pdf(path, path.read_bytes(), mode, transcriber)
    if suffix in IMAGE_SUFFIXES:
        data = path.read_bytes()
        fmt = detect_image_format(data)
        if fmt is None:
            return _doc(SourceKind.UPLOAD, str(path), reason=f"이미지 형식을 알 수 없다(확장자 {suffix}, 내용이 PNG·JPEG·GIF·WEBP가 아님)")
        return _transcribe_image(path, data, fmt=fmt, transcriber=transcriber)
    return _doc(SourceKind.UPLOAD, str(path), reason=f"지원하지 않는 형식이다({suffix or '확장자 없음'})")


def load_upload_dir(
    directory: Path,
    *,
    transcriber: Transcriber | None = None,
    force: Mapping[str, str] | None = None,
) -> list[SourceDoc]:
    """폴더의 파일들을 자료로 읽는다(하위 폴더는 보지 않는다). 폴더가 없으면 빈 목록.

    force: 파일 이름 → "extract"(A만) 또는 "transcribe"(B만). 안 주면 위의 자동 규칙.
    파일 하나가 못 읽혀도 나머지는 읽는다. 못 읽은 파일은 이유와 함께 못 읽음 자료로 남는다.
    """
    if not directory.is_dir():
        return []
    force = force or {}
    return [
        read_upload_file(path, transcriber=transcriber, mode=force.get(path.name))
        for path in sorted(p for p in directory.iterdir() if p.is_file() and not p.name.startswith("."))
    ]


# ── 웹 ──────────────────────────────────────────────────────────────────────


class _TextExtractor(HTMLParser):
    """HTML에서 사람이 읽는 글자만 뽑는다. script·style 등은 버리고 블록 태그에서 줄을 바꾼다."""

    _SKIP = {"script", "style", "noscript", "svg", "template"}
    _BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._parts).splitlines())
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def check_public_url(url: str) -> None:
    """http·https이고 공개 주소를 가리키는 URL만 통과시킨다. 아니면 ValueError."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"http·https 주소만 읽는다({parts.scheme or '스킴 없음'})")
    host = parts.hostname
    if not host:
        raise ValueError("주소에 호스트가 없다")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"주소를 찾을 수 없다: {host}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError(f"내부망·루프백 주소는 읽지 않는다: {host}")


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트 대상도 공개 주소인지 확인한다(공개 주소가 내부망으로 돌려보내는 경우를 막는다)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        check_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


HttpGet = Callable[[str], tuple[int, str, bytes]]  # url → (상태 코드, content-type, 본문 바이트)


def http_get(url: str) -> tuple[int, str, bytes]:
    """실제 GET. 테스트는 이 함수를 가짜로 바꿔 끼운다. 오류 상태 코드는 예외가 아니라 값으로 돌려준다."""
    check_public_url(url)
    opener = urllib.request.build_opener(_CheckedRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": "modular-agentic-system/0.1 (research)"})
    try:
        with opener.open(request, timeout=WEB_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_WEB_BYTES + 1)
            return response.status, response.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", b""


def _decode(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    for token in content_type.split(";")[1:]:
        key, _, value = token.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value.strip("\"'")
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def fetch_url(url: str, *, get: HttpGet = http_get) -> SourceDoc:
    """URL의 본문을 자료로 가져온다. 못 읽으면 이유와 함께 못 읽음 자료를 돌려준다(예외로 끝내지 않는다)."""
    try:
        status, content_type, body = get(url)
    except ValueError as exc:
        return _doc(SourceKind.WEB, url, reason=str(exc))
    except (urllib.error.URLError, OSError) as exc:
        return _doc(SourceKind.WEB, url, reason=f"접속 실패: {type(exc).__name__}")
    if status in (401, 403):
        return _doc(SourceKind.WEB, url, reason=f"HTTP {status}: 로그인이 필요하거나 접근이 차단됨. 페이지를 저장·캡처해서 입력 폴더에 넣어 준다")
    if status >= 400:
        return _doc(SourceKind.WEB, url, reason=f"HTTP {status}")
    if len(body) > MAX_WEB_BYTES:
        return _doc(SourceKind.WEB, url, reason="본문이 너무 크다")
    kind = content_type.split(";")[0].strip().lower()
    if kind == "application/pdf":
        return _doc(SourceKind.WEB, url, reason="PDF다. 내려받아 입력 폴더에 넣어 준다")
    text = _decode(body, content_type)
    if kind in ("text/html", "application/xhtml+xml", ""):
        text = html_to_text(text)
    elif not kind.startswith("text/"):
        return _doc(SourceKind.WEB, url, reason=f"읽지 않는 형식이다({kind})")
    if len(text.strip()) < MIN_WEB_TEXT_CHARS:
        return _doc(SourceKind.WEB, url, reason=f"본문이 거의 없다({len(text.strip())}자). JS로 그려지는 페이지이거나 차단됐을 수 있다. 저장·캡처해서 입력 폴더에 넣어 준다")
    return _doc(SourceKind.WEB, url, text=text)


# ── 한꺼번에 ────────────────────────────────────────────────────────────────


def collect_docs(
    item: Item,
    *,
    upload_dir: Path | None = None,
    urls: Iterable[str] = (),
    found: Iterable[SearchHit] = (),
    transcriber: Transcriber | None = None,
    force: Mapping[str, str] | None = None,
    get: HttpGet = http_get,
) -> list[SourceDoc]:
    """공고, 업로드 폴더, 사용자가 준 URL, 검색으로 찾은 URL을 모아 번호(d1…)를 붙여 돌려준다.

    순서는 사용자가 준 것(공고, 업로드, URL)이 먼저이고 검색으로 찾은 것이 뒤다. found는 이미 사용자 URL과 겹치는 것이 빠진
    상태로 오는 것이 원칙이지만(source_search.find_sources의 exclude_urls), 여기서도 한 번 더 걸러 같은 페이지를 두 번 읽지 않는다.
    검색으로 찾은 자료에는 found_by("검색: <질의>")가 붙어 보고서에서 구분된다.
    """
    user_urls = list(urls)
    docs = [posting_doc(item)]
    if upload_dir is not None:
        docs += load_upload_dir(upload_dir, transcriber=transcriber, force=force)
    docs += [fetch_url(u, get=get) for u in user_urls]
    seen = {normalize_url(u) for u in user_urls}
    for hit in found:
        key = normalize_url(hit.url)
        if key in seen:
            continue
        seen.add(key)
        docs.append(replace(fetch_url(hit.url, get=get), found_by=f"검색: {hit.query}" if hit.query else "검색"))
    return number_docs(docs)
