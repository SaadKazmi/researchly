import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DENY = {"write_outside", "read_decoy", "network", "spawn"}


def test_probe_reports_shape() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/sandbox_probe.py"], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert {"os", "arch", "mechanism", "checks", "deny_tests", "notes"} <= out.keys()
    assert out["os"] == platform.system()
    assert set(out["deny_tests"]) == DENY
    assert set(out["deny_tests"].values()) <= {"denied", "allowed", "skipped"}
