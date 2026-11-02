import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import statusline


def test_render_full_payload():
    payload = {
        "model": {"display_name": "Opus 5.5"},
        "context_window": {"used_percentage": 42.4},
        "cost": {"total_cost_usd": 1.2345},
        "effort": {"level": "high"},
    }
    assert statusline.render(payload) == "Opus 5.5 | ctx 42% | $1.23 | effort high"


def test_render_missing_fields():
    assert statusline.render({}) == "? | ctx ?% | $0.00 | effort ?"


def test_main_decodes_utf8_stdin_under_cp1252_text_wrapper(monkeypatch, capsys):
    raw = json.dumps({"model": {"display_name": "Opus ”"}}, ensure_ascii=False).encode("utf-8")
    stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdin", stdin)
    statusline.main()
    assert "Opus ”" in capsys.readouterr().out


def test_render_tolerates_null_fields():
    assert statusline.render({"model": None, "context_window": None, "cost": None, "effort": None}) == "? | ctx ?% | $0.00 | effort ?"
