import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(HOOKS.parents[1] / "scripts"))
