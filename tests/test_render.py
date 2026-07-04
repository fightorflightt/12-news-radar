from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "news_radar.py"
SPEC = importlib.util.spec_from_file_location("news_radar", MODULE_PATH)
news_radar = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["news_radar"] = news_radar
SPEC.loader.exec_module(news_radar)


def test_render_markdown_contains_sections() -> None:
    report = {
        "date": "2026-07-04",
        "task": "daily",
        "sections": {
            "AI 搞钱": [
                news_radar.Item(
                    title="Test",
                    source="YouTube",
                    url="https://example.com",
                    summary="Summary",
                    meta="Meta",
                )
            ]
        },
        "errors": [],
    }

    output = news_radar.render_markdown(report)

    assert "# 2026-07-04 资讯雷达" in output
    assert "## AI 搞钱" in output
    assert "https://example.com" in output
