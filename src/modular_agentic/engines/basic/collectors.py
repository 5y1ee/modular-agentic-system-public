"""모델을 부르지 않는 수집 구현체(core.stages.Collector): 크롤러 결과 읽기, 붙여넣기.

InthisworkCollector
  inthiswork-crawler(별도 레포)가 매일 모은 `data/daily/<날짜>.json`을 읽는다. 공고마다 id·company·role·title·link·content·images가 있다.
  본문이 이미지뿐인 공고는 body가 비고 meta["images"]에 이미지 주소가 남는다(내용을 추측하지 않는다는 core 규칙. 전사는 뒤에서 따로).
  자소설(jasoseol) 쪽 결과는 본문이 없어 읽지 않는다.
PasteCollector
  사람이 붙여 넣은 공고 글 한 건(채용공고든 RFP든). 업무(instance)를 고르면 공고 파일에 `업무:` 줄로 남는다. id는 글 내용의 해시라 같은 글을 두 번 넣어도 한 건이다.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from datetime import date
from pathlib import Path

from modular_agentic.core.types import Item
from modular_agentic.instances.profile import profile

CRAWLER_DIR_ENV = "MAS_CRAWLER_DIR"
DEFAULT_CRAWLER_DIR = Path.home() / "01_user" / "inthiswork-crawler" / "data" / "daily"


def _images(value) -> list[str]:
    """크롤러는 이미지 목록을 파이썬 리스트 모양의 글("['https://…']")로 저장한다. 목록이면 그대로 쓴다."""
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = ast.literal_eval(value or "[]")
    except (ValueError, SyntaxError):
        return []
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


class InthisworkCollector:
    def __init__(self, *, day: str = "", crawler_dir: Path | None = None) -> None:
        self.day = day or date.today().isoformat()
        self.crawler_dir = crawler_dir or Path(os.environ.get(CRAWLER_DIR_ENV) or DEFAULT_CRAWLER_DIR)

    def collect(self) -> list[Item]:
        path = self.crawler_dir / f"{self.day}.json"
        if not path.is_file():
            raise FileNotFoundError(f"크롤러 결과가 없다: {path} (크롤러가 그날 돌았는지, 날짜 형식 YYYY-MM-DD인지 확인)")
        items = []
        for post in json.loads(path.read_text(encoding="utf-8")):
            company, role = str(post.get("company", "")).strip(), str(post.get("role", "")).strip()
            images = _images(post.get("images"))
            meta = {"company": company, "role": role, "link": str(post.get("link", "")), "found_by": f"inthiswork {self.day}"}
            if images:
                meta["images"] = " ".join(images)
            items.append(Item(id=f"inthiswork_{post['id']}", source="inthiswork",
                              title=str(post.get("title") or f"{company} {role}").strip(),
                              body=str(post.get("content", "")).strip(), meta=meta))
        return items


class PasteCollector:
    def __init__(self, *, title: str, text: str, link: str = "", instance: str = "") -> None:
        self.title, self.text, self.link = title.strip(), text.strip(), link.strip()
        self.instance = profile(instance).label if instance else ""  # 업무(자소서·제안서). 없으면 공고 파일에 적지 않는다(=자소서)

    def collect(self) -> list[Item]:
        if not self.title or not self.text:
            raise ValueError("붙여넣기에는 제목과 본문이 모두 필요하다")
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:10]
        meta = {"found_by": "붙여넣기", **({"link": self.link} if self.link else {}), **({"instance": self.instance} if self.instance else {})}
        return [Item(id=f"paste_{digest}", source="paste", title=self.title, body=self.text, meta=meta)]
