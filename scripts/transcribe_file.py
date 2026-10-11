"""이미지·PDF 한 장을 모델이 읽어 텍스트로 옮긴다(전사). 공고 본문이 이미지뿐인 경우(대기업 공채 공고 대부분)에 쓴다.

왜 필요한가: 본문이 이미지뿐인 공고는 Item.body가 비어 분석 단계가 요구를 뽑지 못한다. 이미지를 전사해 텍스트 파일로 저장하면
그 파일을 공고 본문으로 쓸 수 있다(scripts/run_writer_demo.py, scripts/research_brief.py run).
전사는 모델이 읽은 것이라 읽기 오류가 섞일 수 있다. **저장된 텍스트를 원본 이미지와 대조해서 고친 뒤** 공고로 쓴다.

비용: Bedrock 호출 1회. 사용자가 직접 실행한다.
예: AWS_PROFILE=user uv run --extra strands --extra sources python scripts/transcribe_file.py inputs/공고_이미지.png -o samples/공고.txt
"""
import argparse
import sys
import time
from pathlib import Path

from modular_agentic.core.types import ReadStatus
from modular_agentic.engines.strands.transcriber import StrandsTranscriber
from modular_agentic.tools.sources import MAX_TILE_HEIGHT, TRANSCRIBE, read_upload_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path, help="이미지(png·jpg·gif·webp) 또는 PDF")
    parser.add_argument("-o", "--output", type=Path, required=True, help="전사 결과를 저장할 텍스트 파일")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    print(f"전사 중: {args.file} — Bedrock 호출(세로로 긴 이미지는 {MAX_TILE_HEIGHT}px 안팎 조각마다 1회)")
    start = time.monotonic()
    doc = read_upload_file(args.file, transcriber=StrandsTranscriber(), mode=TRANSCRIBE)
    if doc.status is ReadStatus.UNREAD:
        sys.exit(f"읽지 못했다: {doc.reason}")
    print(f"완료: {len(doc.text)}자 ({time.monotonic() - start:.1f}초)")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(doc.text + "\n", encoding="utf-8")
    print(f"저장: {args.output} — 원본과 대조해서 고친 뒤 공고로 쓴다([판독불가] 표시가 있는지도 본다)")


if __name__ == "__main__":
    main()
