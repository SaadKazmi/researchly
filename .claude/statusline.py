"""Claude Code status line: model | context % | session cost | effort."""

from __future__ import annotations

import json
import sys


def render(payload: dict) -> str:
    model = payload.get("model", {}).get("display_name", "?")
    used = payload.get("context_window", {}).get("used_percentage")
    ctx = f"{round(used)}%" if isinstance(used, (int, float)) else "?%"
    cost = payload.get("cost", {}).get("total_cost_usd") or 0.0
    effort = payload.get("effort", {}).get("level", "?")
    return f"{model} | ctx {ctx} | ${cost:.2f} | effort {effort}"


def main() -> None:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    print(render(json.loads(raw) if raw.strip() else {}))


if __name__ == "__main__":
    main()
