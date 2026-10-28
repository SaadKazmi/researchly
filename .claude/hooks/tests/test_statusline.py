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
