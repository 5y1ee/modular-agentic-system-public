"""전시 묶음 마스킹(scripts/mask_showcase.py): 규칙대로 바꿔 쓰고, 금지 문구가 남으면 아무것도 쓰지 않는다. 가짜 이름만 쓴다."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mask_showcase.py"


def run(tmp_path: Path, rules: dict) -> subprocess.CompletedProcess:
    (tmp_path / "rules.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), "--rules", "rules.json"], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")


def test_masks_into_showcase_and_keeps_original(tmp_path):
    (tmp_path / "src.md").write_text("가나공사의 RQ-007 요건, 제품 ZETA 사용", encoding="utf-8")
    rules = {"name": "demo", "task": "제안서", "files": {"src.md": "02_요구.md"},
             "replace": [{"names": ["가나공사"], "to": "발주기관"}, {"names": ["ZETA"], "to": "제품 Z"},
                         {"regex": "RQ-0*(\\d+)", "to": "요구 \\1"}],
             "forbidden": ["가나공사", "ZETA", "RQ-\\d"]}
    result = run(tmp_path, rules)
    assert result.returncode == 0, result.stdout + result.stderr
    out = tmp_path / "logs" / "console" / "showcase" / "demo" / "02_요구.md"
    assert out.read_text(encoding="utf-8") == "발주기관의 요구 7 요건, 제품 제품 Z 사용"
    assert "가나공사" in (tmp_path / "src.md").read_text(encoding="utf-8")
    assert (out.parent / "업무.txt").read_text(encoding="utf-8").strip() == "제안서"


def test_leftover_forbidden_writes_nothing(tmp_path):
    (tmp_path / "src.md").write_text("가나공사와 ZETA", encoding="utf-8")
    rules = {"name": "demo", "files": {"src.md": "02_요구.md"},
             "replace": [{"names": ["가나공사"], "to": "발주기관"}], "forbidden": ["ZETA"]}
    result = run(tmp_path, rules)
    assert result.returncode == 1 and "ZETA" in result.stdout
    assert not (tmp_path / "logs").exists()
