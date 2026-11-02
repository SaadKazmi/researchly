# ADR 0004: Native-mode SearXNG from a pinned upstream commit

- Status: accepted
- Date: 2026-11-02
- Spec sections: §4 (install table), §8 (search fan-out), §26 risk 2

## Context
Web search is keyless through SearXNG. Docker mode runs the `searxng/searxng` image. Native mode had no web search, and `doctor` listed it as degraded. Research is in `docs/research/2026-11-02-step1e-searxng.md` and found:
- SearXNG has no PyPI or GitHub releases. The PyPI name `searxng` is an unrelated package.
- It is installed from source and served with granian (WSGI).
- On Windows the only blocker is `searx/valkeydb.py`, which imports `pwd` at module load. Upstream declines Windows support (#5411; PR #5412 was closed).
- Every dependency has a binary wheel on all five targets.

## Decision
- **Pin:** `researchly start` in native mode installs SearXNG commit `54ca1d9cfb08674f66261d7f351b063558dfcc62`, the commit of the Docker image `2026.10.25-54ca1d9cf`, so both modes run the same SearXNG.
- **Download:** the commit tarball comes from `codeload.github.com` and is verified against sha256 `a40695381839d492eb6e65ec3af53de4fbefe7ad709ef39e945153fe14fc7ea9` **before** extraction. It is the CLI installer, not a data connector, so it uses `urllib`, not PoliteClient.
- **Extraction:** Python `tarfile` with `filter="data"`. Only `searx/**`, `requirements*.txt` and `LICENSE` are extracted, and links are skipped.
- **Install:** an app-private venv at `<data_dir>/searxng/<sha12>/venv`, made by `uv`, with dependencies from the committed hash-locked `cli/src/researchly_cli/searxng-requirements.txt` (`--require-hashes`). `searx/version_frozen.py` is written from the commit's date and SHA.
- **Windows:** a `pwd.py` shim that we author goes into the venv's site-packages. The AGPL files stay unmodified.
- **Running:** `granian --interface wsgi --host 127.0.0.1 --workers 1 --blocking-threads 4 searx.webapp:app` runs under the existing supervisor, as a second tree beside the app. It uses port 8766, or any free port when 8766 is taken. The secret reaches it only as `SEARXNG_SECRET` in the env.
- **Settings:** `settings_text()` holds the native settings, the same as `infra/searxng/settings.yml`; a test keeps the two equal.
- **Failure:** web search is off, `start` warns with the reason, and `doctor` prints `web search: off: <reason>`. The app still starts.

## Consequences
- Native mode gets the same keyless web search as Docker mode on every OS, with no container and no admin. The first native start installs about 7 MB plus the Python dependencies. A cold first `researchly start` took 25 s on the dev box, including the install.
- **Bumping the pin is a deliberate change.** The `searxng` CLI test installs the pin for real and queries a mock engine on all five OS. A bump takes these steps:
  1. Pick the commit of a published `searxng/searxng` image tag, and move `infra/compose.yaml` to that tag.
  2. Update `COMMIT` and `TARBALL_SHA256`.
  3. Regenerate `searxng-requirements.txt` with the command in its header comment.
  4. Re-grep `searx/**/*.py` for POSIX-only imports: `pwd`, `grp`, `fcntl`, `resource`, `termios`, `uvloop`, `os.fork`, `os.getuid`.
  5. Re-check that `searx/version.py` still reads the five frozen names.
  6. Re-check that `infra/searxng/settings.yml` and `settings_text()` still load.
- **Licence:** SearXNG is AGPL-3.0. We download it from upstream at install time, never vendor it, never `import searx`, and talk to it over HTTP on loopback only. Under the FSF FAQ these are separate programs, and since we do not modify it, the AGPL §13 source offer is not triggered. Not legal advice.
- **Windows support is ours:** upstream does not support Windows, so the shim and the run are re-verified on every pin bump.
- **Inherited environment:** granian gets the CLI's environment, as the app does. The CLI adds no keys to it.
- **Deferred to step 9:** two concurrent first starts can race on the shared tarball path. The worst case is a failed install with a warning, never a false `ready`.
- The backend wires the native SearXNG URL into search at step 2/3.

## Evidence
- **Windows 10 dev box, isolated data dir:**
  - `start` printed `Installing web search (SearXNG)...`, and `doctor` printed `web search: ready (SearXNG 54ca1d9cf)`.
  - `/healthz` returned 200, and a live arXiv JSON search returned results.
  - The port was bound on 127.0.0.1 only.
  - After `stop`, no granian process was left. A second start and stop did not reinstall.
- **Earlier research run:** SearXNG became healthy 12–15 s after spawn. The research run (commit d138f44) got live results from arXiv, Crossref, OpenAlex, Semantic Scholar and Google Scholar; PubMed timed out once.
- **Wheels:** `uv pip compile --only-binary :all:` resolved the 33 hashed requirements for Windows x64, Linux x64/arm64 and macOS arm64/x64.
- **CI:** the `searxng`-marked test runs in the `cli` job on all five OS (install, then a JSON query to a mock `json_engine`). Install plus query, cold uv cache, run 41535527204:

| Runner | Install + query (s) |
|---|---|
| ubuntu-24.04 | 4.44 |
| ubuntu-24.04-arm | 4.20 |
| windows-2025 | 9.20 |
| macos-15 (arm64) | 38.51 |
| macos-15-intel | 43.24 |
