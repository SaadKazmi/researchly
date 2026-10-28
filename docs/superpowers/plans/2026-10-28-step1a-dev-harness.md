# Step 1a — Development Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the repository harness that every later step builds inside:
- repo hygiene;
- the AGENTS.md / CLAUDE.md instructions;
- the `features.json` / `progress.md` / ADR harness, with checks that enforce its invariants;
- Claude Code subagents, hooks, status line, plugins and MCP servers;
- a CI job that guards all of it.

**Architecture:** Plain files plus two small Python packages of checks:
- `scripts/repo_checks.py`: repo invariants, run in CI and imported by hooks.
- `.claude/hooks/*.py`: Claude Code hooks.

Everything Python runs through `uv run --no-project --python 3.12`, because the machine's default `python` is 3.8. Nothing here depends on application code; later plans add their own skills when the thing they describe exists.

**Tech Stack:** uv 0.12+, Python 3.12, pytest, Claude Code (updated to latest), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-28-researchly-design.md` (§5 layout, §23 dev process & tooling, §24 step 1)

## Global Constraints

- License: **Apache-2.0** (spec §5).
- `AGENTS.md` is the source of truth. `CLAUDE.md` imports it with `@AGENTS.md`. Each file stays **under 200 lines** (spec §23.2).
- `docs/features.json`: agents may only flip `passes`. Structure changes need user approval. `passes: true` requires at least one E2E spec path (spec §23.1).
- Hooks are Python, so they work on Windows. Invocation: `uv run --no-project --python 3.12 "<script>"` (spec §23.2).
- Subagent models: architect / spec-reviewer / debugger = **opus**; implementer / test-writer / user-simulator / ai-provider = **sonnet**; test-runner / Explore = **haiku**. No Fable anywhere (spec D12).
- GitHub Actions are pinned by full commit SHA, with minimal `permissions` (spec §19.7).
- Commit messages use Conventional Commits and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Line endings: LF in the repo (`.gitattributes`). Only `*.ps1` and `*.cmd` stay CRLF.

---

## File Structure

```
.gitattributes .gitignore .editorconfig LICENSE README.md
AGENTS.md CLAUDE.md backend/AGENTS.md frontend/AGENTS.md
docs/features.json docs/progress.md docs/adr/0000-template.md docs/adr/0001-adopt-design-spec.md
scripts/repo_checks.py                 # invariants: features.json, agent frontmatter, line budgets
scripts/tests/test_repo_checks.py
.claude/settings.json                  # hooks, permissions, env, status line
.claude/statusline.py
.claude/hooks/_common.py               # stdin/env helpers shared by hooks
.claude/hooks/protect.py               # PreToolUse: ask/deny rules
.claude/hooks/format.py                # PostToolUse: ruff / prettier
.claude/hooks/typecheck.py             # Stop: pyright / tsc on changed packages
.claude/hooks/session_context.py       # SessionStart(compact|resume): progress + plan + git status
.claude/hooks/tests/conftest.py
.claude/hooks/tests/test_protect.py
.claude/hooks/tests/test_format.py
.claude/hooks/tests/test_typecheck.py
.claude/hooks/tests/test_session_context.py
.claude/hooks/tests/test_statusline.py
.claude/agents/{architect,spec-reviewer,debugger,implementer,test-writer,test-runner,Explore,user-simulator,ai-provider}.md
.mcp.json                              # project MCP servers (LangChain docs)
.github/workflows/ci.yml               # repo-checks job
```

---

### Task 1: Repo hygiene and license

**Files:**
- Create: `.gitattributes`, `.gitignore`, `.editorconfig`, `LICENSE`, `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: LF normalization for every later file; ignore rules for `.venv/`, `node_modules/`, `e2e/.artifacts/`, `.env*`.

- [ ] **Step 1: Create `.gitattributes`**

```gitattributes
* text=auto eol=lf
*.ps1 text eol=crlf
*.cmd text eol=crlf
*.png binary
*.jpg binary
*.pdf binary
*.onnx binary
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
dist/
build/
*.egg-info/

# Node
node_modules/
frontend/dist/
.pnpm-store/

# Env & secrets
.env
.env.*
!.env.example

# Test artifacts
e2e/.artifacts/
e2e/test-results/
e2e/playwright-report/
coverage/
htmlcov/

# Claude Code local
CLAUDE.local.md
.claude/settings.local.json

# OS / editors
.DS_Store
Thumbs.db
.idea/
.vscode/*
!.vscode/extensions.json
```

- [ ] **Step 3: Create `.editorconfig`**

```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 2

[*.py]
indent_size = 4

[*.{ps1,cmd}]
end_of_line = crlf

[*.md]
trim_trailing_whitespace = false
```

- [ ] **Step 4: Fetch the Apache-2.0 license text**

Run (PowerShell):
```powershell
gh api /licenses/apache-2.0 --jq .body | Out-File -Encoding utf8NoBOM LICENSE
```
If `utf8NoBOM` is rejected (Windows PowerShell 5.1), run instead:
```powershell
uv run --no-project --python 3.12 python -c "import subprocess,pathlib; t=subprocess.run(['gh','api','/licenses/apache-2.0','--jq','.body'],capture_output=True,text=True,check=True).stdout; pathlib.Path('LICENSE').write_text(t,encoding='utf-8',newline='\n')"
```
Expected: `LICENSE` starts with `Apache License` and contains `Version 2.0, January 2004`.

- [ ] **Step 5: Create `README.md`**

````markdown
# Researchly

Open-source, local-first, multi-agent research assistant. Chat about a topic and Researchly's agents:

- search open scholarly sources;
- rank the best papers, explaining each score;
- build citation and concept graphs you can explore;
- explain and visualize concepts;
- write research notebooks and books with checked citations.

The only thing that costs money is your own LLM API key. Everything else is free, keyless and installed for you.

> **Status:** under active development. The install commands below work from the first tagged release.

## Install

macOS / Linux:
```bash
curl -fsSL https://raw.githubusercontent.com/SaadKazmi/researchly/main/install.sh | bash
```

Windows (PowerShell):
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/SaadKazmi/researchly/main/install.ps1 | iex"
```

Then run `researchly start`. Researchly uses Docker if you have a container engine (it starts the engine for you) and runs natively otherwise. No admin rights are needed.

## Documentation

- Design spec: [docs/superpowers/specs/2026-10-28-researchly-design.md](docs/superpowers/specs/2026-10-28-researchly-design.md)
- Contributor and agent guide: [AGENTS.md](AGENTS.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
````

- [ ] **Step 6: Renormalize line endings and verify**

Run:
```powershell
git add --renormalize .
git check-attr eol -- docs/superpowers/specs/2026-10-28-researchly-design.md
```
Expected: `docs/superpowers/specs/2026-10-28-researchly-design.md: eol: lf`

- [ ] **Step 7: Commit**

```powershell
git add .gitattributes .gitignore .editorconfig LICENSE README.md docs
git commit -m "chore: repo hygiene, Apache-2.0 license, README" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Repo invariant checks (`scripts/repo_checks.py`)

**Files:**
- Create: `scripts/repo_checks.py`
- Test: `scripts/tests/test_repo_checks.py`

**Interfaces:**
- Consumes: nothing.
- Produces (imported by `.claude/hooks/protect.py` and run in CI):
  - `FEATURE_KEYS: frozenset[str]`
  - `check_features(data: dict) -> list[str]`: error messages, empty if valid.
  - `parse_frontmatter(text: str) -> dict[str, str]`
  - `check_agents(agents_dir: Path) -> list[str]`
  - `check_line_budget(path: Path, max_lines: int = 200) -> list[str]`
  - `main(root: Path | None = None) -> int`: 0 if valid, 1 otherwise; prints errors.

- [ ] **Step 1: Write the failing tests**

`scripts/tests/test_repo_checks.py`:
```python
from pathlib import Path

import pytest

from repo_checks import (
    check_agents,
    check_features,
    check_line_budget,
    main,
    parse_frontmatter,
)


def feature(**overrides):
    base = {
        "id": "F1.1",
        "title": "Projects and threads",
        "build_step": 3,
        "spec_ref": "§3 F1.1",
        "acceptance": "Projects hold threads.",
        "e2e": [],
        "passes": False,
    }
    base.update(overrides)
    return base


def test_valid_features_have_no_errors():
    assert check_features({"features": [feature()]}) == []


def test_empty_features_rejected():
    assert check_features({"features": []}) == ["features must be a non-empty list"]


def test_missing_key_rejected():
    f = feature()
    del f["acceptance"]
    errors = check_features({"features": [f]})
    assert len(errors) == 1 and errors[0].startswith("F1.1: keys must be")


def test_duplicate_id_rejected():
    errors = check_features({"features": [feature(), feature()]})
    assert "F1.1: duplicate id" in errors


def test_bad_id_rejected():
    assert "F1: bad id" in check_features({"features": [feature(id="F1")]})


def test_passes_requires_e2e():
    errors = check_features({"features": [feature(passes=True)]})
    assert "F1.1: passes=true requires at least one e2e spec" in errors


def test_passes_with_e2e_ok():
    data = {"features": [feature(passes=True, e2e=["e2e/specs/F1.1.spec.ts"])]}
    assert check_features(data) == []


def test_passes_must_be_bool():
    assert "F1.1: passes must be a boolean" in check_features({"features": [feature(passes="yes")]})


def test_build_step_range():
    assert "F1.1: build_step must be 1..10" in check_features({"features": [feature(build_step=11)]})


def test_parse_frontmatter_crlf_and_lf():
    text = "---\r\nname: architect\r\nmodel: opus\r\n---\r\nbody\r\n"
    assert parse_frontmatter(text) == {"name": "architect", "model": "opus"}
    assert parse_frontmatter("no frontmatter") == {}


def test_check_agents(tmp_path: Path):
    (tmp_path / "good.md").write_text(
        "---\nname: good\ndescription: does things\nmodel: sonnet\n---\nbody\n", encoding="utf-8"
    )
    (tmp_path / "bad.md").write_text("---\nname: bad\n---\nbody\n", encoding="utf-8")
    (tmp_path / "fable.md").write_text(
        "---\nname: fable\ndescription: x\nmodel: fable\n---\n", encoding="utf-8"
    )
    errors = check_agents(tmp_path)
    assert "bad.md: missing description" in errors
    assert "bad.md: missing model" in errors
    assert "fable.md: model must be one of haiku, inherit, opus, sonnet" in errors
    assert not any(e.startswith("good.md") for e in errors)


def test_line_budget(tmp_path: Path):
    p = tmp_path / "AGENTS.md"
    p.write_text("x\n" * 201, encoding="utf-8")
    assert check_line_budget(p) == ["AGENTS.md: 201 lines exceeds budget of 200"]
    p.write_text("x\n" * 200, encoding="utf-8")
    assert check_line_budget(p) == []


def test_main_on_minimal_repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "features.json").write_text('{"features": []}', encoding="utf-8")
    assert main(tmp_path) == 1
    assert "features must be a non-empty list" in capsys.readouterr().out
```

Also create `scripts/tests/conftest.py`:
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-project --python 3.12 --with pytest pytest scripts/tests -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'repo_checks'`

- [ ] **Step 3: Write the implementation**

`scripts/repo_checks.py`:
```python
"""Repository invariants enforced in CI and by Claude Code hooks.

Run: uv run --no-project --python 3.12 scripts/repo_checks.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FEATURE_KEYS = frozenset({"id", "title", "build_step", "spec_ref", "acceptance", "e2e", "passes"})
ALLOWED_MODELS = ("haiku", "inherit", "opus", "sonnet")
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "backend/AGENTS.md", "frontend/AGENTS.md")
_ID_RE = re.compile(r"^F\d+\.\d+$")


def check_features(data: dict) -> list[str]:
    features = data.get("features")
    if not isinstance(features, list) or not features:
        return ["features must be a non-empty list"]
    errors: list[str] = []
    seen: set[str] = set()
    for f in features:
        fid = f.get("id", "?") if isinstance(f, dict) else "?"
        if not isinstance(f, dict) or set(f) != FEATURE_KEYS:
            errors.append(f"{fid}: keys must be {sorted(FEATURE_KEYS)}")
            continue
        if not _ID_RE.match(fid):
            errors.append(f"{fid}: bad id")
        if fid in seen:
            errors.append(f"{fid}: duplicate id")
        seen.add(fid)
        if not isinstance(f["passes"], bool):
            errors.append(f"{fid}: passes must be a boolean")
        elif f["passes"] and not f["e2e"]:
            errors.append(f"{fid}: passes=true requires at least one e2e spec")
        if not isinstance(f["build_step"], int) or not 1 <= f["build_step"] <= 10:
            errors.append(f"{fid}: build_step must be 1..10")
        if not isinstance(f["e2e"], list):
            errors.append(f"{fid}: e2e must be a list")
    return errors


def parse_frontmatter(text: str) -> dict[str, str]:
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def check_agents(agents_dir: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(agents_dir.glob("*.md")):
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        for key in ("name", "description", "model"):
            if not fm.get(key):
                errors.append(f"{path.name}: missing {key}")
        model = fm.get("model")
        if model and model not in ALLOWED_MODELS:
            errors.append(f"{path.name}: model must be one of {', '.join(ALLOWED_MODELS)}")
    return errors


def check_line_budget(path: Path, max_lines: int = 200) -> list[str]:
    count = len(path.read_text(encoding="utf-8").splitlines())
    if count > max_lines:
        return [f"{path.name}: {count} lines exceeds budget of {max_lines}"]
    return []


def main(root: Path | None = None) -> int:
    root = root or Path(__file__).resolve().parents[1]
    errors: list[str] = []
    features_path = root / "docs" / "features.json"
    if features_path.exists():
        errors += check_features(json.loads(features_path.read_text(encoding="utf-8")))
    else:
        errors.append("docs/features.json missing")
    agents_dir = root / ".claude" / "agents"
    if agents_dir.exists():
        errors += check_agents(agents_dir)
    for rel in INSTRUCTION_FILES:
        if (root / rel).exists():
            errors += check_line_budget(root / rel)
    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-project --python 3.12 --with pytest pytest scripts/tests -q`
Expected: `13 passed`

- [ ] **Step 5: Commit**

```powershell
git add scripts
git commit -m "feat(repo): add repo invariant checks for features, agents, line budgets" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Harness documents (`features.json`, `progress.md`, ADRs)

**Files:**
- Create: `docs/features.json`, `docs/progress.md`, `docs/adr/0000-template.md`, `docs/adr/0001-adopt-design-spec.md`

**Interfaces:**
- Consumes: `scripts/repo_checks.py` (`main`).
- Produces: the feature registry every later plan flips `passes` in, and the session log that hooks inject.

- [ ] **Step 1: Create `docs/features.json`**

One feature per line, so diffs stay readable. `e2e` stays empty until each feature's plan adds its spec paths.
```json
{
  "spec": "docs/superpowers/specs/2026-10-28-researchly-design.md",
  "features": [
    {"id": "F1.1", "title": "Projects and chat threads", "build_step": 3, "spec_ref": "§3 F1.1", "acceptance": "Projects hold multiple threads; history persists and is searchable.", "e2e": [], "passes": false},
    {"id": "F1.2", "title": "Clarifying questions", "build_step": 3, "spec_ref": "§3 F1.2", "acceptance": "Standard/Deep runs ask at most 3 skippable clarifying questions.", "e2e": [], "passes": false},
    {"id": "F1.3", "title": "Plan review", "build_step": 3, "spec_ref": "§3 F1.3", "acceptance": "Editable plan (sub-questions, sources, effort, cost estimate) approved/edited/cancelled before Standard/Deep runs.", "e2e": [], "passes": false},
    {"id": "F1.4", "title": "Effort levels", "build_step": 3, "spec_ref": "§3 F1.4, §6.4", "acceptance": "Quick/Standard/Deep limits enforced in code.", "e2e": [], "passes": false},
    {"id": "F1.5", "title": "Live run view", "build_step": 3, "spec_ref": "§3 F1.5", "acceptance": "Agent to tool tree with status, time, tokens and cost, streamed over AG-UI.", "e2e": [], "passes": false},
    {"id": "F1.6", "title": "Run control", "build_step": 3, "spec_ref": "§3 F1.6", "acceptance": "Pause, resume, cancel and branch-and-rerun from a checkpoint; runs survive restart.", "e2e": [], "passes": false},
    {"id": "F1.7", "title": "Budgets", "build_step": 3, "spec_ref": "§3 F1.7", "acceptance": "Pre-run estimate with confirm above threshold; wrap-up at 80%, graceful stop at 100% keeping partial results.", "e2e": [], "passes": false},
    {"id": "F1.8", "title": "Citation chips", "build_step": 3, "spec_ref": "§3 F1.8", "acceptance": "Inline [n] chips show the quote on hover and open the reader at the passage on click.", "e2e": [], "passes": false},
    {"id": "F2.1", "title": "Multi-source search", "build_step": 3, "spec_ref": "§3 F2.1, §8", "acceptance": "Keyless scholarly connectors plus SearXNG web search, fanned out and merged.", "e2e": [], "passes": false},
    {"id": "F2.2", "title": "Identity resolution and dedupe", "build_step": 3, "spec_ref": "§3 F2.2, §8.3", "acceptance": "Records merge across sources by IDs, then by fuzzy title+year+first author.", "e2e": [], "passes": false},
    {"id": "F2.3", "title": "Explainable ranking", "build_step": 3, "spec_ref": "§3 F2.3, §10", "acceptance": "Per-paper score breakdown with Balanced/Seminal/Recent presets.", "e2e": [], "passes": false},
    {"id": "F2.4", "title": "Coverage indicator", "build_step": 3, "spec_ref": "§3 F2.4", "acceptance": "Completeness estimate from the discovery-rate curve with a search-more action.", "e2e": [], "passes": false},
    {"id": "F2.5", "title": "Saved searches and alerts", "build_step": 9, "spec_ref": "§3 F2.5", "acceptance": "Scheduled re-runs surface new papers in an inbox.", "e2e": [], "passes": false},
    {"id": "F3.1", "title": "Collections, tags, notes", "build_step": 4, "spec_ref": "§3 F3.1", "acceptance": "Per-project collections, tags, notes and highlights.", "e2e": [], "passes": false},
    {"id": "F3.2", "title": "Legal OA full text", "build_step": 4, "spec_ref": "§3 F3.2", "acceptance": "Full text fetched only from legal OA locations; license stored per paper.", "e2e": [], "passes": false},
    {"id": "F3.3", "title": "Parsing pipeline", "build_step": 4, "spec_ref": "§3 F3.3, §9.1", "acceptance": "Papers parsed into sections, figures, tables, equations and references.", "e2e": [], "passes": false},
    {"id": "F3.4", "title": "PDF reader with passage jump", "build_step": 4, "spec_ref": "§3 F3.4", "acceptance": "pdf.js reader highlights the exact passage when a citation is opened.", "e2e": [], "passes": false},
    {"id": "F3.5", "title": "Import/export and Zotero", "build_step": 9, "spec_ref": "§3 F3.5", "acceptance": "Import BibTeX/RIS/CSL-JSON; Zotero local read and web-API write; export BibTeX/CSL-JSON.", "e2e": [], "passes": false},
    {"id": "F4.1", "title": "Multi-level summaries", "build_step": 4, "spec_ref": "§3 F4.1", "acceptance": "TL;DR to expert summaries with a level slider; every sentence grounding-checked.", "e2e": [], "passes": false},
    {"id": "F4.2", "title": "Equation/figure/table explanations", "build_step": 4, "spec_ref": "§3 F4.2", "acceptance": "Explanations use the figure, caption and referencing paragraphs.", "e2e": [], "passes": false},
    {"id": "F4.3", "title": "Grounded Q&A", "build_step": 4, "spec_ref": "§3 F4.3, §12.2", "acceptance": "Answers pass layered citation verification.", "e2e": [], "passes": false},
    {"id": "F4.4", "title": "Paper comparison", "build_step": 4, "spec_ref": "§3 F4.4", "acceptance": "Side-by-side method, data, results and limitations.", "e2e": [], "passes": false},
    {"id": "F4.5", "title": "Extraction tables", "build_step": 4, "spec_ref": "§3 F4.5", "acceptance": "User-defined columns; each cell has a value, quote and source; CSV export.", "e2e": [], "passes": false},
    {"id": "F4.6", "title": "Contradiction finder", "build_step": 4, "spec_ref": "§3 F4.6", "acceptance": "Contradicting claims across papers are surfaced with evidence.", "e2e": [], "passes": false},
    {"id": "F4.7", "title": "Gaps and trends", "build_step": 8, "spec_ref": "§3 F4.7, §12.5", "acceptance": "Trend charts, rare-link gaps and thin sub-questions.", "e2e": [], "passes": false},
    {"id": "F4.8", "title": "Research ideas with novelty check", "build_step": 8, "spec_ref": "§3 F4.8, §12.4", "acceptance": "Ideas labeled as untested hypotheses, each with a novelty score and nearest prior work.", "e2e": [], "passes": false},
    {"id": "F5.1", "title": "Citation graph", "build_step": 5, "spec_ref": "§3 F5.1, §11", "acceptance": "Prior and derivative works plus co-citation and coupling similarity.", "e2e": [], "passes": false},
    {"id": "F5.2", "title": "Concept graph", "build_step": 5, "spec_ref": "§3 F5.2, §11", "acceptance": "SciERC-style entities and relations linked across papers.", "e2e": [], "passes": false},
    {"id": "F5.3", "title": "Timeline", "build_step": 5, "spec_ref": "§3 F5.3", "acceptance": "Timeline view of papers and concepts.", "e2e": [], "passes": false},
    {"id": "F5.4", "title": "Graph traversal", "build_step": 5, "spec_ref": "§3 F5.4", "acceptance": "Expand on click, filters, Louvain clusters, in-graph search, pin to collection.", "e2e": [], "passes": false},
    {"id": "F5.5", "title": "Field view", "build_step": 5, "spec_ref": "§3 F5.5", "acceptance": "GPU-rendered overview of more than 100k nodes.", "e2e": [], "passes": false},
    {"id": "F5.6", "title": "Mind maps and small diagrams", "build_step": 5, "spec_ref": "§3 F5.6", "acceptance": "Mind maps reuse the graph engine; small editable diagrams use React Flow.", "e2e": [], "passes": false},
    {"id": "F5.7", "title": "In-answer visuals", "build_step": 5, "spec_ref": "§3 F5.7", "acceptance": "Schema-validated Vega-Lite, Mermaid, KaTeX and Excalidraw.", "e2e": [], "passes": false},
    {"id": "F6.1", "title": "Research notebook", "build_step": 7, "spec_ref": "§3 F6.1, §13", "acceptance": "Block document with sandboxed executable code cells.", "e2e": [], "passes": false},
    {"id": "F6.2", "title": "Research book generator", "build_step": 7, "spec_ref": "§3 F6.2, §12.1", "acceptance": "Evidence-first, outline, sectioned writing, verification and critique, user editable at each stage.", "e2e": [], "passes": false},
    {"id": "F6.3", "title": "Block editor with AI edits", "build_step": 7, "spec_ref": "§3 F6.3", "acceptance": "Plate editor with AI rewrite, expand, condense, cite-this and comments.", "e2e": [], "passes": false},
    {"id": "F6.4", "title": "Exports", "build_step": 7, "spec_ref": "§3 F6.4", "acceptance": "PDF, DOCX, LaTeX, ipynb, MyST site, Markdown and BibTeX in any CSL style.", "e2e": [], "passes": false},
    {"id": "F6.5", "title": "Redistribution guard", "build_step": 7, "spec_ref": "§3 F6.5", "acceptance": "Exports embed full text beyond quotes only for CC BY or CC0 papers.", "e2e": [], "passes": false},
    {"id": "F7.1", "title": "Audio overview", "build_step": 8, "spec_ref": "§3 F7.1, §14", "acceptance": "Two-voice grounded script with local Kokoro TTS; MP3 plus cited transcript.", "e2e": [], "passes": false},
    {"id": "F7.2", "title": "Study guide, FAQ, flashcards, slides", "build_step": 8, "spec_ref": "§3 F7.2", "acceptance": "Grounded outputs; Anki/CSV and Reveal.js/PPTX exports.", "e2e": [], "passes": false},
    {"id": "F7.3", "title": "Systematic review", "build_step": 8, "spec_ref": "§3 F7.3, §14", "acceptance": "Protocol, search log, screening, extraction table and a PRISMA 2020 diagram.", "e2e": [], "passes": false},
    {"id": "F8.1", "title": "Memory", "build_step": 6, "spec_ref": "§3 F8.1, §15", "acceptance": "User and project memory via the bake-off winner.", "e2e": [], "passes": false},
    {"id": "F8.2", "title": "Memory panel", "build_step": 6, "spec_ref": "§3 F8.2", "acceptance": "View, search, edit, pin, delete, forget and export, each with its source turn.", "e2e": [], "passes": false},
    {"id": "F8.3", "title": "Procedural preferences", "build_step": 6, "spec_ref": "§3 F8.3", "acceptance": "Citation style, venues and reading level always applied.", "e2e": [], "passes": false},
    {"id": "F8.4", "title": "Project playbook", "build_step": 6, "spec_ref": "§3 F8.4, §15.3", "acceptance": "Append-only ACE-style lessons after each run.", "e2e": [], "passes": false},
    {"id": "F8.5", "title": "Memory write notices", "build_step": 6, "spec_ref": "§3 F8.5", "acceptance": "User notified of writes; behaviour-changing memories need confirmation.", "e2e": [], "passes": false},
    {"id": "F9.1", "title": "LLM providers", "build_step": 9, "spec_ref": "§3 F9.1, §7", "acceptance": "Anthropic, OpenAI, Gemini, OpenRouter, Ollama and OpenAI-compatible; dev-only claude-code provider.", "e2e": [], "passes": false},
    {"id": "F9.2", "title": "Role model routing", "build_step": 9, "spec_ref": "§3 F9.2, §7.3", "acceptance": "Per-role overrides with provider defaults.", "e2e": [], "passes": false},
    {"id": "F9.3", "title": "Optional source keys", "build_step": 9, "spec_ref": "§3 F9.3", "acceptance": "Sources work unconfigured; optional keys and mailto show what they unlock.", "e2e": [], "passes": false},
    {"id": "F9.4", "title": "Budget settings", "build_step": 9, "spec_ref": "§3 F9.4", "acceptance": "Budgets and confirmation thresholds are configurable.", "e2e": [], "passes": false},
    {"id": "F9.5", "title": "System settings", "build_step": 9, "spec_ref": "§3 F9.5", "acceptance": "Data directory, backup schedule, theme and language.", "e2e": [], "passes": false},
    {"id": "F10.1", "title": "CLI", "build_step": 9, "spec_ref": "§3 F10.1, §22", "acceptance": "setup, onboard, start, stop, status, config, update, logs, backup, restore, migrate-runtime, db shell, bundle, doctor and uninstall all work.", "e2e": [], "passes": false},
    {"id": "F10.2", "title": "Usage dashboard", "build_step": 9, "spec_ref": "§3 F10.2, §20", "acceptance": "Tokens and cost by run, agent, model and day; cache-hit rate.", "e2e": [], "passes": false},
    {"id": "F10.3", "title": "Offline mode", "build_step": 9, "spec_ref": "§3 F10.3", "acceptance": "Ollama LLM and embeddings, with the cache serving known papers.", "e2e": [], "passes": false},
    {"id": "F10.4", "title": "MCP server", "build_step": 9, "spec_ref": "§3 F10.4, §17", "acceptance": "Read-only library tools over stdio and Streamable HTTP.", "e2e": [], "passes": false},
    {"id": "F10.5", "title": "MCP client", "build_step": 9, "spec_ref": "§3 F10.5", "acceptance": "External MCP tools attachable with per-tool approval.", "e2e": [], "passes": false},
    {"id": "F10.6", "title": "Accessibility", "build_step": 9, "spec_ref": "§3 F10.6", "acceptance": "Keyboard navigation, WCAG 2.2 AA, screen-reader labels, graph table view.", "e2e": [], "passes": false}
  ]
}
```

- [ ] **Step 2: Create `docs/progress.md`**

```markdown
# Progress log

Newest entries at the bottom. Each session appends: date, plan + task, what changed, test status, next step.

## 2026-10-28
- Design spec approved: docs/superpowers/specs/2026-10-28-researchly-design.md
- Research digest: docs/research/2026-10-28-research-digest.md
- Started plan: docs/superpowers/plans/2026-10-28-step1a-dev-harness.md
```

- [ ] **Step 3: Create `docs/adr/0000-template.md`**

```markdown
# ADR NNNN: <decision title>

- Status: proposed | accepted | superseded by ADR NNNN
- Date: YYYY-MM-DD
- Spec sections: §x.y

## Context
What forces are at play; link research and measurements.

## Decision
What we chose, stated so it can be checked.

## Consequences
What gets easier, what gets harder, what we will watch.

## Evidence
Benchmarks, spike results, links.
```

- [ ] **Step 4: Create `docs/adr/0001-adopt-design-spec.md`**

```markdown
# ADR 0001: Adopt the Researchly design spec

- Status: accepted
- Date: 2026-10-28
- Spec sections: all

## Context
Researchly's architecture was researched (docs/research/2026-10-28-research-digest.md) and approved by the owner on 2026-10-28.

## Decision
The decision log D1–D15 in docs/superpowers/specs/2026-10-28-researchly-design.md §2 is accepted as the project's baseline architecture. Any change to a D-item requires a new ADR that supersedes the relevant entry.

## Consequences
Implementation plans argue from the spec. Spikes in step 1 (Postgres native, SearXNG on Windows, native sandboxes, Docling ONNX, ONNX models) each produce their own ADR.

## Evidence
See the spec's decision log and the research digest.
```

- [ ] **Step 5: Run the repo checks**

Run: `uv run --no-project --python 3.12 scripts/repo_checks.py`
Expected: no output, exit code 0 (`$LASTEXITCODE` is `0`).

- [ ] **Step 6: Commit**

```powershell
git add docs
git commit -m "docs: add feature registry, progress log and ADR baseline" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Instruction files (AGENTS.md, CLAUDE.md, nested)

**Files:**
- Create: `AGENTS.md`, `CLAUDE.md`, `backend/AGENTS.md`, `frontend/AGENTS.md`

**Interfaces:**
- Consumes: `scripts/repo_checks.py` (line budgets).
- Produces: the conventions every implementer follows. The module boundaries named here (`llm/registry.py`, `agents/factory.py`, `PoliteClient`) are binding for plans 1b onward.

- [ ] **Step 1: Create `AGENTS.md`**

````markdown
# Researchly — agent & contributor guide

Researchly is an open-source, local-first, multi-agent research assistant: chat → agents search keyless scholarly sources → rank → read into verified evidence → graphs, notebooks and books. The only thing that costs the user money is their LLM API key.

- Spec (source of truth): `docs/superpowers/specs/2026-10-28-researchly-design.md`
- Plans: `docs/superpowers/plans/` · ADRs: `docs/adr/` · Research: `docs/research/`
- Feature registry: `docs/features.json` · Session log: `docs/progress.md`

## Principles (Karpathy guidelines)
1. **Think before coding.** Restate the goal, read the code you will touch, name your assumptions. Ask when the spec is ambiguous.
2. **Simplicity first.** The smallest change that meets the spec. No speculative abstractions, flags or config.
3. **Surgical changes.** Touch only what the task needs; no drive-by refactors or reformatting.
4. **Goal-driven execution.** Define "done" as a passing test or check before writing code; stop when it passes.

## Repo map
- `backend/` — Python 3.12 app (FastAPI, LangGraph/Deep Agents, LiteLLM, SQLAlchemy/Alembic). See `backend/AGENTS.md`.
- `frontend/` — React 19 + Vite + TanStack Router + shadcn/ui SPA. See `frontend/AGENTS.md`.
- `cli/` — `researchly` CLI (setup, runtime selection, supervision).
- `e2e/` — Playwright specs, journeys, personas, fixtures (LLM + HTTP replay).
- `evals/` — Inspect AI eval tasks.
- `infra/` — compose files, Dockerfiles, native runtime assets.
- `scripts/` — repo checks and maintenance scripts.

## Commands
| Purpose | Command |
|---|---|
| Repo checks | `uv run --no-project --python 3.12 scripts/repo_checks.py` |
| Hook/script tests | `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests scripts/tests -q` |
| Backend tests | `uv run --directory backend pytest -q` |
| Backend lint/format | `uv run --directory backend ruff check` · `uv run --directory backend ruff format` |
| Backend types | `uv run --directory backend pyright` |
| Frontend tests | `pnpm -C frontend test` |
| Frontend types/lint | `pnpm -C frontend exec tsc --noEmit` · `pnpm -C frontend lint` |
| E2E smoke | `pnpm -C e2e exec playwright test --grep @smoke` |

The default `python` on dev machines may be old; always go through `uv`.

## Binding conventions
- LLM calls go **only** through `backend/src/researchly/llm/` (`llm/registry.py` is the only module importing `litellm`).
- `deepagents` is imported **only** in `backend/src/researchly/agents/factory.py`.
- All outbound HTTP goes through `PoliteClient` (`backend/src/researchly/connectors/http.py`); never call `httpx` directly elsewhere.
- Untrusted text (web, PDFs) never reaches planner/writer prompts directly — use the quarantined extractor (spec §19.1).
- No API keys in state, checkpoints, logs, traces or fixtures. Keys come from the keychain at call time.
- Keyless by default: no feature may require a paid service or a data-source key (spec D14).
- Tests use replay: LLM via `RecordReplay` fixtures, HTTP via vcrpy cassettes. A missing fixture fails the test.
- Migrations: Alembic; never edit an applied revision — add a new one.
- Pin exact versions for `litellm`, `deepagents`, `langgraph`, `langchain*`; upgrades need an ADR (spec §6.6, §19.7).

## Workflow contract (every session)
1. Read `docs/progress.md`, `docs/features.json`, `git log --oneline -15`, and the current plan.
2. Run the smoke checks for what exists (repo checks; later `@smoke`).
3. Take **one** plan task. TDD: failing test → minimal code → passing test.
4. Verify (tests + relevant E2E), then commit with a Conventional Commit message.
5. Flip `passes` in `features.json` only after its E2E spec passes; append to `docs/progress.md`.

## Gotchas
- Windows dev box: PowerShell 5.1 has no `&&`; use `;` with `if ($?)`. Hooks are Python scripts launched via `uv run` (Claude Code runs hook commands through Git Bash on Windows).
- `docs/features.json` edits other than `passes` require user approval (hook asks).
- Docker may be installed but stopped; the CLI starts it — don't assume either runtime in tests; both modes are in CI.

## Compact instructions
When compacting, keep: current plan path and task number, failing test names and errors, files changed this session, decisions made and why. Drop: full file dumps, passing test output, exploration dead ends.
````

- [ ] **Step 2: Create `CLAUDE.md`**

```markdown
@AGENTS.md

# Claude Code specifics
- Process skills: use superpowers (brainstorming → writing-plans → subagent-driven-development, TDD, systematic-debugging, verification-before-completion). ponytail guards against over-engineering.
- Subagents live in `.claude/agents/`: architect/spec-reviewer/debugger (opus), implementer/test-writer/user-simulator/ai-provider (sonnet), test-runner/Explore (haiku). Route noisy test output through `test-runner`.
- Library docs: prefer the `docs-langchain` MCP for LangGraph/Deep Agents/LangChain and context7 for other libraries; verify APIs before using them.
- Browser checks: `playwright-cli` in the main thread; Playwright MCP only inside `user-simulator`.
- Use `/clear` between unrelated tasks; plans and progress live on disk, not in the conversation.
```

- [ ] **Step 3: Create `backend/AGENTS.md`**

```markdown
# Backend guide

- Layout: `src/researchly/{api,runs,agents,services,connectors,llm,memory,security,db}`; tests mirror it under `tests/`.
- Python 3.12, async-first (FastAPI, SQLAlchemy 2 async, httpx via `PoliteClient`).
- Types: pyright strict on `src/`; no `Any` in public signatures.
- Errors: raise domain exceptions from `researchly.errors`; API layer maps them to HTTP problems (RFC 9457 JSON).
- DB: SQLAlchemy 2 models in `db/models/`; Alembic in `db/migrations/`; every migration has an upgrade→downgrade→upgrade test.
- Tests: pytest + pytest-asyncio; unit tests never touch the network; integration tests use testcontainers Postgres and replay fixtures.
- LLM in tests: `GenericFakeChatModel` for unit tests; `RecordReplay` in replay mode for integration.
```

- [ ] **Step 4: Create `frontend/AGENTS.md`**

```markdown
# Frontend guide

- Vite + React 19 + TypeScript strict + TanStack Router/Query; shadcn/ui (Base UI) + Tailwind v4.
- API types come from the generated OpenAPI client (`src/api/schema.d.ts`); never hand-write API types.
- Chat uses assistant-ui with the AG-UI runtime; tool UIs live in `src/features/chat/tools/`.
- Rendering LLM output: `react-markdown` + `rehype-sanitize` only; Mermaid `securityLevel: "strict"`; Vega-Lite specs validated before render; no `dangerouslySetInnerHTML`.
- Graph test hooks: expose `window.__researchly.graph` (node count, camera) for Playwright; never assert on canvas pixels.
- Tests: Vitest + Testing Library for components; Playwright for flows (in `e2e/`).
```

- [ ] **Step 5: Run the repo checks**

Run: `uv run --no-project --python 3.12 scripts/repo_checks.py`
Expected: exit code 0 (all four files under 200 lines).

- [ ] **Step 6: Commit**

```powershell
git add AGENTS.md CLAUDE.md backend/AGENTS.md frontend/AGENTS.md
git commit -m "docs: add AGENTS.md source of truth, CLAUDE.md import, nested guides" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Hook helpers and the protect hook

**Files:**
- Create: `.claude/hooks/_common.py`, `.claude/hooks/protect.py`
- Test: `.claude/hooks/tests/conftest.py`, `.claude/hooks/tests/test_protect.py`

**Interfaces:**
- Consumes: `scripts/repo_checks.check_features`.
- Produces:
  - `_common.read_payload() -> dict`
  - `_common.project_root() -> Path`
  - `_common.rel_path(file_path: str, root: Path) -> str | None`: POSIX relative path, or `None` if the file is outside the repo.
  - `protect.decide(payload: dict, root: Path) -> tuple[str, str] | None`: `("ask" | "deny", reason)`, or `None` to allow.

- [ ] **Step 1: Write the failing tests**

`.claude/hooks/tests/conftest.py`:
```python
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(HOOKS.parents[1] / "scripts"))
```

`.claude/hooks/tests/test_protect.py`:
```python
import json
from pathlib import Path

import protect

MIGRATION_DIR = "backend/src/researchly/db/migrations/versions"


def features(passes=False, e2e=None, title="Projects"):
    return {
        "features": [
            {
                "id": "F1.1",
                "title": title,
                "build_step": 3,
                "spec_ref": "§3 F1.1",
                "acceptance": "a",
                "e2e": e2e if e2e is not None else [],
                "passes": passes,
            }
        ]
    }


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def payload(tool: str, path: Path, **tool_input) -> dict:
    return {"tool_name": tool, "tool_input": {"file_path": str(path), **tool_input}}


def test_unrelated_file_allowed(tmp_path):
    assert protect.decide(payload("Write", tmp_path / "README.md", content="x"), tmp_path) is None


def test_outside_repo_allowed(tmp_path):
    outside = tmp_path.parent / "elsewhere.txt"
    assert protect.decide(payload("Write", outside, content="x"), tmp_path) is None


def test_workflow_edit_asks(tmp_path):
    decision = protect.decide(payload("Write", tmp_path / ".github/workflows/ci.yml", content="x"), tmp_path)
    assert decision is not None and decision[0] == "ask"


def test_new_migration_allowed(tmp_path):
    p = tmp_path / MIGRATION_DIR / "0002_new.py"
    assert protect.decide(payload("Write", p, content="x"), tmp_path) is None


def test_existing_migration_asks(tmp_path):
    p = write(tmp_path, f"{MIGRATION_DIR}/0001_base.py", "x")
    decision = protect.decide(payload("Edit", p, old_string="x", new_string="y"), tmp_path)
    assert decision is not None and decision[0] == "ask"


def test_features_passes_flip_allowed(tmp_path):
    spec = ["e2e/specs/F1.1.spec.ts"]
    p = write(tmp_path, "docs/features.json", json.dumps(features(e2e=spec)))
    new = json.dumps(features(passes=True, e2e=spec))
    assert protect.decide(payload("Write", p, content=new), tmp_path) is None


def test_features_edit_tool_passes_flip_allowed(tmp_path):
    spec = ["e2e/specs/F1.1.spec.ts"]
    p = write(tmp_path, "docs/features.json", json.dumps(features(e2e=spec)))
    decision = protect.decide(
        payload("Edit", p, old_string='"passes": false', new_string='"passes": true'), tmp_path
    )
    assert decision is None


def test_features_structure_change_asks(tmp_path):
    p = write(tmp_path, "docs/features.json", json.dumps(features()))
    new = json.dumps(features(title="Renamed"))
    decision = protect.decide(payload("Write", p, content=new), tmp_path)
    assert decision is not None and decision[0] == "ask"


def test_features_pass_without_e2e_denied(tmp_path):
    p = write(tmp_path, "docs/features.json", json.dumps(features()))
    new = json.dumps(features(passes=True))
    decision = protect.decide(payload("Write", p, content=new), tmp_path)
    assert decision is not None and decision[0] == "deny"
    assert "requires at least one e2e spec" in decision[1]


def test_features_invalid_json_denied(tmp_path):
    p = write(tmp_path, "docs/features.json", json.dumps(features()))
    decision = protect.decide(payload("Write", p, content="{not json"), tmp_path)
    assert decision is not None and decision[0] == "deny"


def test_multiedit_applies_all_edits(tmp_path):
    p = write(tmp_path, "docs/features.json", json.dumps(features()))
    decision = protect.decide(
        {
            "tool_name": "MultiEdit",
            "tool_input": {
                "file_path": str(p),
                "edits": [{"old_string": '"title": "Projects"', "new_string": '"title": "X"'}],
            },
        },
        tmp_path,
    )
    assert decision is not None and decision[0] == "ask"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests/test_protect.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'protect'`

- [ ] **Step 3: Write `_common.py`**

`.claude/hooks/_common.py`:
```python
"""Helpers shared by Claude Code hook scripts."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def read_payload() -> dict:
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def project_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).resolve().parents[2])).resolve()


def rel_path(file_path: str, root: Path) -> str | None:
    if not file_path:
        return None
    try:
        return Path(file_path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
```

- [ ] **Step 4: Write `protect.py`**

`.claude/hooks/protect.py`:
```python
"""PreToolUse hook: ask/deny rules for sensitive files.

- .github/workflows/*: ask (CI changes need the user's eye).
- Existing Alembic revisions: ask (add a new revision instead).
- docs/features.json: deny invalid content; ask for anything beyond flipping `passes`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from _common import project_root, read_payload, rel_path  # noqa: E402
from repo_checks import check_features  # noqa: E402

_MIGRATION_RE = re.compile(r"^backend/src/researchly/db/migrations/versions/.+\.py$")


def proposed_content(tool_name: str, tool_input: dict, path: Path) -> str:
    if tool_name == "Write":
        return tool_input.get("content", "")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    for edit in tool_input.get("edits") or [tool_input]:
        old, new = edit.get("old_string", ""), edit.get("new_string", "")
        text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
    return text


def _skeleton(data: dict) -> dict:
    stripped = [{k: v for k, v in f.items() if k != "passes"} for f in data.get("features", [])]
    return {**{k: v for k, v in data.items() if k != "features"}, "features": stripped}


def _check_features_change(payload: dict, path: Path) -> tuple[str, str] | None:
    new_text = proposed_content(payload.get("tool_name", ""), payload.get("tool_input", {}), path)
    try:
        new = json.loads(new_text)
    except json.JSONDecodeError as exc:
        return ("deny", f"docs/features.json would become invalid JSON: {exc}")
    errors = check_features(new)
    if errors:
        return ("deny", "docs/features.json invalid: " + "; ".join(errors[:5]))
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if _skeleton(old) != _skeleton(new):
            return ("ask", "docs/features.json structure change: only `passes` may change without user approval.")
    return None


def decide(payload: dict, root: Path) -> tuple[str, str] | None:
    file_path = payload.get("tool_input", {}).get("file_path", "")
    rel = rel_path(file_path, root)
    if rel is None:
        return None
    if rel.startswith(".github/workflows/"):
        return ("ask", "CI workflow change: confirm with the user before editing.")
    if _MIGRATION_RE.match(rel) and (root / rel).exists():
        return ("ask", "Editing an existing Alembic revision; add a new revision unless the user confirms.")
    if rel == "docs/features.json":
        return _check_features_change(payload, root / rel)
    return None


def main() -> int:
    result = decide(read_payload(), project_root())
    if result:
        decision, reason = result
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": decision,
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests/test_protect.py -q`
Expected: `11 passed`

- [ ] **Step 6: Commit**

```powershell
git add .claude/hooks
git commit -m "feat(claude): add protect hook for workflows, migrations and features.json" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Format, typecheck and session-context hooks

**Files:**
- Create: `.claude/hooks/format.py`, `.claude/hooks/typecheck.py`, `.claude/hooks/session_context.py`
- Test: `.claude/hooks/tests/test_format.py`, `.claude/hooks/tests/test_typecheck.py`, `.claude/hooks/tests/test_session_context.py`

**Interfaces:**
- Consumes: `_common.read_payload`, `_common.project_root`, `_common.rel_path`.
- Produces:
  - `format.commands_for(rel: str, root: Path) -> list[list[str]]`
  - `typecheck.checks_for(changed: list[str], root: Path) -> list[list[str]]`
  - `typecheck.decide(payload: dict, root: Path, changed: list[str], run: Callable[[list[str]], CompletedLike]) -> dict | None`
  - `typecheck.parse_porcelain(text: str) -> list[str]`
  - `session_context.build_context(root: Path, git_status: str, limit: int = 10_000) -> str`

- [ ] **Step 1: Write the failing tests**

`.claude/hooks/tests/test_format.py`:
```python
from pathlib import Path

import format as fmt


def make(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")


def test_python_in_backend(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    cmds = fmt.commands_for("backend/src/researchly/app.py", tmp_path)
    assert cmds == [
        ["uv", "run", "--directory", "backend", "ruff", "format", "src/researchly/app.py"],
        ["uv", "run", "--directory", "backend", "ruff", "check", "--fix", "src/researchly/app.py"],
    ]


def test_python_without_backend_project_skipped(tmp_path):
    assert fmt.commands_for("backend/src/x.py", tmp_path) == []


def test_tsx_in_frontend(tmp_path):
    make(tmp_path, "frontend/package.json")
    assert fmt.commands_for("frontend/src/App.tsx", tmp_path) == [
        ["pnpm", "-C", "frontend", "exec", "prettier", "--write", "src/App.tsx"]
    ]


def test_other_files_skipped(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    assert fmt.commands_for("docs/progress.md", tmp_path) == []
```

`.claude/hooks/tests/test_typecheck.py`:
```python
from dataclasses import dataclass
from pathlib import Path

import typecheck


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def make(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")


def test_parse_porcelain_handles_renames_and_untracked():
    text = " M backend/a.py\n?? frontend/src/b.tsx\nR  old.py -> backend/new.py\n"
    assert typecheck.parse_porcelain(text) == ["backend/a.py", "frontend/src/b.tsx", "backend/new.py"]


def test_checks_for_backend_and_frontend(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    make(tmp_path, "frontend/package.json")
    cmds = typecheck.checks_for(["backend/src/x.py", "frontend/src/y.ts"], tmp_path)
    assert cmds == [
        ["uv", "run", "--directory", "backend", "pyright"],
        ["pnpm", "-C", "frontend", "exec", "tsc", "--noEmit"],
    ]


def test_checks_skip_missing_projects(tmp_path):
    assert typecheck.checks_for(["backend/src/x.py", "frontend/src/y.ts"], tmp_path) == []


def test_stop_hook_active_never_blocks(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    result = typecheck.decide(
        {"stop_hook_active": True}, tmp_path, ["backend/x.py"], run=lambda cmd: Result(1, "err")
    )
    assert result is None


def test_failure_blocks_with_truncated_reason(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    output = "\n".join(f"error {i}" for i in range(100))
    result = typecheck.decide({}, tmp_path, ["backend/x.py"], run=lambda cmd: Result(1, output))
    assert result is not None and result["decision"] == "block"
    assert "error 39" in result["reason"] and "error 40" not in result["reason"]


def test_success_does_not_block(tmp_path):
    make(tmp_path, "backend/pyproject.toml")
    assert typecheck.decide({}, tmp_path, ["backend/x.py"], run=lambda cmd: Result(0)) is None
```

`.claude/hooks/tests/test_session_context.py`:
```python
from pathlib import Path

import session_context


def test_context_includes_progress_plan_and_status(tmp_path: Path):
    (tmp_path / "docs/superpowers/plans").mkdir(parents=True)
    (tmp_path / "docs/progress.md").write_text("# Progress\n- did a thing\n", encoding="utf-8")
    older = tmp_path / "docs/superpowers/plans/2026-10-28-a.md"
    newer = tmp_path / "docs/superpowers/plans/2026-10-29-b.md"
    older.write_text("a", encoding="utf-8")
    newer.write_text("b", encoding="utf-8")
    text = session_context.build_context(tmp_path, " M AGENTS.md\n")
    assert "did a thing" in text
    assert "docs/superpowers/plans/2026-10-29-b.md" in text
    assert "M AGENTS.md" in text


def test_context_is_capped(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/progress.md").write_text("x" * 50_000, encoding="utf-8")
    assert len(session_context.build_context(tmp_path, "", limit=1000)) <= 1000


def test_context_without_files(tmp_path: Path):
    text = session_context.build_context(tmp_path, "")
    assert "no progress log" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests -q`
Expected: FAIL with `ModuleNotFoundError` for `format`, `typecheck` and `session_context`

- [ ] **Step 3: Write `format.py`**

`.claude/hooks/format.py`:
```python
"""PostToolUse hook: format the edited file (ruff for backend Python, prettier for frontend)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import project_root, read_payload, rel_path  # noqa: E402

_FRONTEND_EXT = (".ts", ".tsx", ".css", ".json")


def commands_for(rel: str, root: Path) -> list[list[str]]:
    if rel.startswith("backend/") and rel.endswith(".py") and (root / "backend/pyproject.toml").exists():
        return [
            ["uv", "run", "--directory", "backend", "ruff", "format", rel.removeprefix("backend/")],
            ["uv", "run", "--directory", "backend", "ruff", "check", "--fix", rel.removeprefix("backend/")],
        ]
    if rel.startswith("frontend/") and rel.endswith(_FRONTEND_EXT) and (root / "frontend/package.json").exists():
        return [["pnpm", "-C", "frontend", "exec", "prettier", "--write", rel.removeprefix("frontend/")]]
    return []


def main() -> int:
    root = project_root()
    rel = rel_path(read_payload().get("tool_input", {}).get("file_path", ""), root)
    for cmd in commands_for(rel, root) if rel else []:
        exe = shutil.which(cmd[0]) or cmd[0]
        subprocess.run([exe, *cmd[1:]], cwd=root, capture_output=True, text=True, check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Write `typecheck.py`**

`.claude/hooks/typecheck.py`:
```python
"""Stop hook: typecheck packages with changed files; block the stop on errors."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import project_root, read_payload  # noqa: E402

MAX_REASON_LINES = 40


class CompletedLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


def parse_porcelain(text: str) -> list[str]:
    paths = []
    for line in text.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return paths


def checks_for(changed: list[str], root: Path) -> list[list[str]]:
    cmds: list[list[str]] = []
    if any(p.startswith("backend/") and p.endswith(".py") for p in changed) and (
        root / "backend/pyproject.toml"
    ).exists():
        cmds.append(["uv", "run", "--directory", "backend", "pyright"])
    if any(p.startswith("frontend/") and p.endswith((".ts", ".tsx")) for p in changed) and (
        root / "frontend/package.json"
    ).exists():
        cmds.append(["pnpm", "-C", "frontend", "exec", "tsc", "--noEmit"])
    return cmds


def decide(
    payload: dict, root: Path, changed: list[str], run: Callable[[list[str]], CompletedLike]
) -> dict | None:
    if payload.get("stop_hook_active"):
        return None
    for cmd in checks_for(changed, root):
        result = run(cmd)
        if result.returncode != 0:
            lines = (result.stdout + result.stderr).splitlines()[:MAX_REASON_LINES]
            return {"decision": "block", "reason": f"`{' '.join(cmd)}` failed:\n" + "\n".join(lines)}
    return None


def _run(root: Path) -> Callable[[list[str]], CompletedLike]:
    def run(cmd: list[str]) -> CompletedLike:
        exe = shutil.which(cmd[0]) or cmd[0]
        return subprocess.run([exe, *cmd[1:]], cwd=root, capture_output=True, text=True, check=False)

    return run


def main() -> int:
    root = project_root()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"], cwd=root, capture_output=True, text=True, check=False
    ).stdout
    result = decide(read_payload(), root, parse_porcelain(status), _run(root))
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Write `session_context.py`**

`.claude/hooks/session_context.py`:
```python
"""SessionStart hook (compact|resume): re-inject progress tail, current plan and git status."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import project_root  # noqa: E402

PROGRESS_TAIL_LINES = 60


def build_context(root: Path, git_status: str, limit: int = 10_000) -> str:
    parts = []
    progress = root / "docs" / "progress.md"
    if progress.exists():
        tail = progress.read_text(encoding="utf-8").splitlines()[-PROGRESS_TAIL_LINES:]
        parts.append("## docs/progress.md (tail)\n" + "\n".join(tail))
    else:
        parts.append("## docs/progress.md\n(no progress log yet)")
    plans = sorted((root / "docs" / "superpowers" / "plans").glob("*.md")) if (
        root / "docs" / "superpowers" / "plans"
    ).exists() else []
    if plans:
        parts.append("## Latest plan\n" + plans[-1].relative_to(root).as_posix())
    parts.append("## git status --short\n" + (git_status.strip() or "(clean)"))
    return "\n\n".join(parts)[-limit:]


def main() -> int:
    root = project_root()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=root, capture_output=True, text=True, check=False
    ).stdout
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": build_context(root, status)}}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests -q`
Expected: `24 passed` (11 protect + 4 format + 6 typecheck + 3 session_context)

- [ ] **Step 7: Commit**

```powershell
git add .claude/hooks
git commit -m "feat(claude): add format, typecheck and session-context hooks" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Status line and `.claude/settings.json`

**Files:**
- Create: `.claude/statusline.py`, `.claude/settings.json`
- Test: `.claude/hooks/tests/test_statusline.py`

**Interfaces:**
- Consumes: the hook scripts from Tasks 5–6.
- Produces: `statusline.render(payload: dict) -> str`, and the project settings that wire everything up.

- [ ] **Step 1: Update Claude Code**

Run: `claude update`, then `claude --version`
Expected: a version newer than 2.1.77. Note the version in `docs/progress.md`.

- [ ] **Step 2: Write the failing test**

`.claude/hooks/tests/test_statusline.py`:
```python
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
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests/test_statusline.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'statusline'`

- [ ] **Step 4: Write `statusline.py`**

`.claude/statusline.py`:
```python
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


if __name__ == "__main__":
    raw = sys.stdin.read()
    print(render(json.loads(raw) if raw.strip() else {}))
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests/test_statusline.py -q`
Expected: `2 passed`

- [ ] **Step 6: Create `.claude/settings.json`**

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "env": {
    "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"
  },
  "permissions": {
    "deny": [
      "Edit(.env*)",
      "Write(.env*)",
      "Edit(**/uv.lock)",
      "Write(**/uv.lock)",
      "Edit(**/pnpm-lock.yaml)",
      "Write(**/pnpm-lock.yaml)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          { "type": "command", "command": "uv run --no-project --python 3.12 \"$CLAUDE_PROJECT_DIR/.claude/hooks/protect.py\"", "timeout": 30 }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          { "type": "command", "command": "uv run --no-project --python 3.12 \"$CLAUDE_PROJECT_DIR/.claude/hooks/format.py\"", "timeout": 60 }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "uv run --no-project --python 3.12 \"$CLAUDE_PROJECT_DIR/.claude/hooks/typecheck.py\"", "timeout": 300 }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "compact|resume",
        "hooks": [
          { "type": "command", "command": "uv run --no-project --python 3.12 \"$CLAUDE_PROJECT_DIR/.claude/hooks/session_context.py\"", "timeout": 30 }
        ]
      }
    ]
  },
  "statusLine": {
    "type": "command",
    "command": "uv run --no-project --python 3.12 \"$CLAUDE_PROJECT_DIR/.claude/statusline.py\""
  }
}
```

- [ ] **Step 7: Set the auto-compact window if this Claude Code version supports it**

Run `/config` in Claude Code and look for an auto-compact window setting. If `autoCompactWindow` is listed in the settings docs for the installed version (https://code.claude.com/docs/en/settings), add `"autoCompactWindow": 300000` as a top-level key in `.claude/settings.json`. If it is not listed, skip this step and note "autoCompactWindow unsupported in <version>" in `docs/progress.md`.

- [ ] **Step 8: Verify the hooks fire**

In a new Claude Code session in the repo:
1. Run `/hooks` and confirm all four events are listed.
2. Ask Claude to edit `docs/features.json` to rename `F1.1`'s title. Expected: an approval prompt citing "structure change".
3. Ask Claude to set `F1.1` `passes` to `true`. Expected: a denial citing "requires at least one e2e spec".
4. Confirm the status line shows `model | ctx N% | $x.xx | effort ...`.

If a hook does not fire on Windows, check `claude --debug` output. If the `$CLAUDE_PROJECT_DIR` expansion fails, replace the quoted path with the `"args"` form from the hooks docs (`"command": "uv", "args": ["run", "--no-project", "--python", "3.12", "${CLAUDE_PROJECT_DIR}/.claude/hooks/protect.py"]`) and re-test.

- [ ] **Step 9: Commit**

```powershell
git add .claude/statusline.py .claude/settings.json .claude/hooks/tests/test_statusline.py docs/progress.md
git commit -m "feat(claude): wire hooks, permissions, status line in project settings" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Subagents

**Files:**
- Create: `.claude/agents/architect.md`, `spec-reviewer.md`, `debugger.md`, `implementer.md`, `test-writer.md`, `test-runner.md`, `Explore.md`, `user-simulator.md`, `ai-provider.md`

**Interfaces:**
- Consumes: `scripts/repo_checks.check_agents` (validated in Step 10).
- Produces: subagent names used by later plans: `architect`, `spec-reviewer`, `debugger`, `implementer`, `test-writer`, `test-runner`, `Explore`, `user-simulator`, `ai-provider`.

- [ ] **Step 1: `architect.md`**

```markdown
---
name: architect
description: Read-only design authority. Use for ADRs, design decisions, decomposing work into plan tasks, and judging whether a change fits the spec's architecture.
model: opus
tools: Read, Grep, Glob, WebFetch, WebSearch
---

You are Researchly's architect. The spec at docs/superpowers/specs/2026-10-28-researchly-design.md and the ADRs in docs/adr/ are binding.

When asked for a decision:
1. Restate the question and the constraints from the spec (cite section numbers).
2. Research current facts (versions, licenses, benchmarks) when the answer depends on them; cite URLs.
3. Give one recommendation with trade-offs and what would make you change it.
4. If it changes a decision-log item (D1–D15), draft an ADR using docs/adr/0000-template.md.

Rules: zero cost beyond the user's LLM key; best available outcome for the user; prefer existing proven frameworks; simplest design that meets the spec. You never edit files — return text only.
```

- [ ] **Step 2: `spec-reviewer.md`**

```markdown
---
name: spec-reviewer
description: Reviews a completed task's diff against the spec and plan before it is accepted. Use after every implementer task.
model: opus
tools: Read, Grep, Glob, Bash
---

You review one task's changes. Inputs: the plan path + task number, and the commit range or `git diff` to review.

Check, in order:
1. Spec compliance: does the change do exactly what the plan task and cited spec sections require — nothing missing, nothing extra?
2. Binding conventions in AGENTS.md (module boundaries, PoliteClient, llm/ only, no secrets in state/logs, keyless by default).
3. Tests: do they test behaviour (not implementation), cover the failure paths named in the spec, and actually fail without the change?
4. Security: untrusted-input handling, SSRF, rendering, secrets (spec §19).

Only use Bash for read-only commands (git diff, git log, git show, test runs). Never modify files.
Output: APPROVED, or a numbered list of blocking issues (file:line, what is wrong, what the spec says), then optional non-blocking notes. Be skeptical: do not approve on intent; approve on evidence.
```

- [ ] **Step 3: `debugger.md`**

```markdown
---
name: debugger
description: Root-cause analysis for failing tests, flaky E2E runs and production-like bugs. Use when a fix attempt has failed or the cause is unclear.
model: opus
tools: Read, Grep, Glob, Bash, Edit, Write
---

Follow superpowers:systematic-debugging. Reproduce first, then form hypotheses, then test them one at a time with the smallest experiment. Grep every caller of a function before changing it; fix the shared root cause, not one call site. Add a regression test that fails before the fix. Report: root cause, evidence, fix, regression test name.
```

- [ ] **Step 4: `implementer.md`**

```markdown
---
name: implementer
description: Implements exactly one plan task with TDD in an isolated worktree, then commits. Use for each task in docs/superpowers/plans/.
model: sonnet
isolation: worktree
---

You implement one task from a plan. Read AGENTS.md, the task's text in the plan, and the spec sections it cites. Follow the task's steps literally: write the failing test, run it and see it fail, write the minimal implementation, run it and see it pass, commit with the given message.

Rules: do not touch files outside the task's Files list unless a step requires it; do not add features, flags or abstractions the task does not ask for; never weaken or skip a test to make it pass; if a step is impossible as written, stop and report why instead of improvising. Report: files changed, test command output (pass counts), commit SHA, and anything the reviewer should look at.
```

- [ ] **Step 5: `test-writer.md`**

```markdown
---
name: test-writer
description: Writes unit and integration tests for an existing module against its spec behaviour. Use to backfill coverage or add regression tests.
model: sonnet
---

Write tests that pin down behaviour described in the spec and plan, not implementation details. Unit tests: no network, fake chat models (GenericFakeChatModel). Integration tests: testcontainers Postgres, RecordReplay LLM fixtures in replay mode, vcrpy cassettes for HTTP. Each test name states the behaviour. Run the tests and report pass/fail counts; a new test that passes immediately against a deliberately broken implementation is not a test — prove it can fail.
```

- [ ] **Step 6: `test-runner.md`**

```markdown
---
name: test-runner
description: Runs test suites (pytest, Vitest, Playwright) and returns only failures with minimal context. Use to keep noisy test output out of the main conversation.
model: haiku
tools: Bash, Read
---

Run exactly the command(s) you are given. Return:
- PASS/FAIL and counts per command;
- for each failure: test id, assertion/error message (≤15 lines), and the file:line where it failed;
- paths to traces/screenshots for Playwright failures.
Never paste passing output, full logs, or stack frames from library code. Never edit files.
```

- [ ] **Step 7: `Explore.md`**

```markdown
---
name: Explore
description: Cheap read-only codebase search. Use to locate files, symbols and patterns; returns paths and short excerpts, not analysis.
model: haiku
tools: Read, Grep, Glob
---

Find what was asked using Glob and Grep first, then read only the relevant excerpts. Return a list of `path:line — one-line description` entries and, if asked, a 3–5 sentence summary. Do not review, judge or propose changes.
```

- [ ] **Step 8: `user-simulator.md`**

```markdown
---
name: user-simulator
description: Plays a realistic end user of the running Researchly app in a headless browser, following a persona and perturbations, and reports failures. Use for exploratory sessions and nightly persona campaigns.
model: sonnet
tools: Read, Write, Bash
mcpServers:
  playwright:
    command: npx
    args: ["@playwright/mcp@latest", "--headless", "--isolated", "--output-dir", "e2e/.artifacts"]
---

You are a user of Researchly, not a tester reading code. Inputs: a persona file from e2e/personas/ (who you are, your goal, your perturbations) and the app URL (from `researchly status`).

Behave like that person: vague when they would be vague, impatient when they are impatient, writing in their language, changing their mind mid-run, combining features, and trying the adversarial inputs listed. Do not be cooperative by default — follow the persona's policy.

After the session, run the hard checks: browser console errors, HTTP 5xx in network logs, final run state (completed/cancelled/partial — never failed), citations that open a real passage, no raw HTML rendered from model output.

Write a report to e2e/user-sim/reports/<YYYY-MM-DD>/<persona>-<n>.md with: persona, goal, steps taken, what happened, each failure with severity (blocker/major/minor), evidence (quote, screenshot path, trace path), and exact repro steps. Only write inside e2e/user-sim/reports/ and e2e/.artifacts/.
```

- [ ] **Step 9: `ai-provider.md`**

```markdown
---
name: ai-provider
description: Curates Researchly's recorded LLM fixtures produced through the dev-only claude-code provider (spec §7.7) — drives recording sessions, reviews responses for realism, and re-records stale fixtures after prompt changes.
model: sonnet
tools: Read, Write, Bash
---

Researchly's dev builds can route LLM calls to the local Claude Code login through `ClaudeCodeChatModel` (spec §7.7), standing in for a user's API key. You manage the fixtures that come out of it.

Tasks you handle:
1. Recording: start the stack with RESEARCHLY_ENV=development, RESEARCHLY_DEV_PROVIDER=claude-code and RecordReplay in `record` mode for the named suite; drive the scenario (API calls or a user-simulator session); stop and list new fixture files.
2. Realism review: read new fixtures in e2e/fixtures/llm/<suite>/ and flag responses a real provider model would not produce (wrong tool-call shape, ignored schema, refusals, truncated JSON).
3. Re-recording: when replay reports a missing fixture after a prompt change, re-record only the affected keys.
4. Scrubbing: confirm no API keys, tokens, emails or memory contents appear in fixtures (run the fixture secret scan).

Only write inside e2e/fixtures/llm/. Never commit fixtures that fail the secret scan.
```

- [ ] **Step 10: Validate agent frontmatter**

Run: `uv run --no-project --python 3.12 scripts/repo_checks.py`
Expected: exit code 0.

Then in Claude Code run `/agents` and confirm all nine agents are listed with the right models. If `isolation` or `mcpServers` are flagged as unknown fields in this version, check https://code.claude.com/docs/en/sub-agents for the current field names, fix them, and re-run the check.

- [ ] **Step 11: Commit**

```powershell
git add .claude/agents
git commit -m "feat(claude): add project subagents with model routing" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Plugins, LSP, skills and MCP servers

**Files:**
- Create: `.mcp.json`
- Modify: `.claude/settings.json` (plugin installs at project scope write `enabledPlugins`)

**Interfaces:**
- Consumes: Claude Code updated in Task 7.
- Produces: the project-scoped tool kit from spec §23.2.

- [ ] **Step 1: Install the language-server binaries**

Run: `npm i -g pyright typescript-language-server typescript`
Verify: `Get-Command pyright-langserver, typescript-language-server` resolves both.

- [ ] **Step 2: Install the official plugins at project scope**

Run each command, approving prompts:
```powershell
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install pyright-lsp@claude-plugins-official --scope project
claude plugin install typescript-lsp@claude-plugins-official --scope project
claude plugin install security-guidance@claude-plugins-official --scope project
claude plugin install pr-review-toolkit@claude-plugins-official --scope project
claude plugin install claude-md-management@claude-plugins-official --scope project
claude plugin install frontend-design@claude-plugins-official --scope project
```
If the marketplace is already present, the `marketplace add` command reports that; continue.
Verify: `claude plugin list` shows all six as enabled for this project.

- [ ] **Step 3: Disable the `data` plugin for this project**

Run `claude plugin list` and find the full id of the `data` plugin, `data@<marketplace>`. Then run:
```powershell
claude plugin disable data@<marketplace> --scope local
```
If the CLI lacks `disable`, use `/plugin` → Installed → data → Disable (local scope).
Verify: `claude mcp list` no longer shows amplitude, bigquery, hex or definite in this project.

- [ ] **Step 4: Install the selected LangChain skills**

Run:
```powershell
npx skills add langchain-ai/langchain-skills --agent claude-code --skill langgraph-fundamentals --skill langgraph-persistence --skill langgraph-human-in-the-loop --skill deep-agents-core --skill deep-agents-memory --skill deep-agents-orchestration --skill langchain-middleware
```
Verify: the seven skill folders exist under `.claude/skills/`. If the installer syntax differs, run `npx skills --help` and install the same seven skills one at a time.

- [ ] **Step 5: Add the LangChain docs MCP server at project scope**

Run: `claude mcp add --scope project --transport http docs-langchain https://docs.langchain.com/mcp`
Expected `.mcp.json`:
```json
{
  "mcpServers": {
    "docs-langchain": {
      "type": "http",
      "url": "https://docs.langchain.com/mcp"
    }
  }
}
```
Verify: `claude mcp list` shows `docs-langchain` as connected. context7 stays at user scope; confirm it is listed.

- [ ] **Step 6: Record the tool kit in the progress log**

Append to `docs/progress.md`:
```markdown
- Step 1a tool kit installed: plugins (pyright-lsp, typescript-lsp, security-guidance, pr-review-toolkit, claude-md-management, frontend-design), data plugin disabled locally, LangChain skills (7), MCP docs-langchain; Claude Code <version>.
```

- [ ] **Step 7: Commit**

```powershell
git add .mcp.json .claude/settings.json .claude/skills docs/progress.md
git commit -m "chore(claude): install project plugins, LangChain skills and docs MCP" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: CI repo-checks workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `scripts/repo_checks.py`, `.claude/hooks/tests`, `scripts/tests`.
- Produces: a `repo-checks` job that later plans extend with backend, frontend and E2E jobs.

- [ ] **Step 1: Resolve the action commit SHAs**

Run:
```powershell
gh api repos/actions/checkout/releases/latest --jq .tag_name
gh api repos/astral-sh/setup-uv/releases/latest --jq .tag_name
```
Then, for each tag (`<tag>`):
```powershell
gh api repos/actions/checkout/commits/<tag> --jq .sha
gh api repos/astral-sh/setup-uv/commits/<tag> --jq .sha
```
Record both 40-character SHAs and tags.

- [ ] **Step 2: Create `.github/workflows/ci.yml`**

Replace `<CHECKOUT_SHA>`/`<CHECKOUT_TAG>` and `<SETUP_UV_SHA>`/`<SETUP_UV_TAG>` with the values from Step 1:
```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  repo-checks:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@<CHECKOUT_SHA> # <CHECKOUT_TAG>
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@<SETUP_UV_SHA> # <SETUP_UV_TAG>
      - name: Repo invariants
        run: uv run --no-project --python 3.12 scripts/repo_checks.py
      - name: Hook and script tests
        run: uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests scripts/tests -q
```

- [ ] **Step 3: Run the same commands locally**

Run:
```powershell
uv run --no-project --python 3.12 scripts/repo_checks.py; if ($?) { uv run --no-project --python 3.12 --with pytest pytest .claude/hooks/tests scripts/tests -q }
```
Expected: exit 0, then `39 passed` (13 repo_checks + 24 hooks + 2 statusline).

- [ ] **Step 4: Commit and push**

The protect hook asks before editing workflows; approve it.
```powershell
git add .github/workflows/ci.yml
git commit -m "ci: add repo-checks job with SHA-pinned actions" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```

- [ ] **Step 5: Verify CI is green**

Run: `gh run watch --exit-status (gh run list --workflow ci --limit 1 --json databaseId --jq '.[0].databaseId')`
Expected: the `repo-checks` job succeeds. If it fails, fix the cause (not the test), commit, and push again.

- [ ] **Step 6: Log progress**

Append to `docs/progress.md`:
```markdown
- Step 1a complete: harness, hooks, agents, plugins, CI repo-checks green. Next: docs/superpowers/plans/2026-10-28-step1b-backend-core.md
```
Commit: `git commit -am "docs: log step 1a completion" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`, then `git push`.
