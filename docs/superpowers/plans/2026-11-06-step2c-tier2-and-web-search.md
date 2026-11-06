# Step 2c: Tier-2 Sources and Local Web Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make keyless web search work end to end through the local SearXNG sidecar, and add the keyless Tier-2 sources: the Papers with Code archive table, bioRxiv/medRxiv, OpenReview decisions, DBLP venues over SPARQL and OpenAIRE.

**Architecture:**
- **SearXNG:** general web and code only (`brave`, `mojeek`, `yahoo`, `github`), JSON only, 5 s engine timeout. The CLI hands the app its origin in `RESEARCHLY_SEARXNG_URL` (native: the loopback port picked before the app starts; Docker: `http://searxng:8080`). `RESEARCHLY_WEB_SEARCH=off` turns it off.
- **Sidecar client:** `connectors/sidecar.py` is the only HTTP outside `PoliteClient`. It is pinned to the configured origin, takes relative paths only, follows no redirects, ignores env proxies, caps bodies and times out fast.
- **Web search:** `services/web_search.py` maps SearXNG JSON to `WebResult`s (with scholarly IDs read from URLs by `ids.from_url`) and reports unresponsive engines.
- **Capability:** an app lifespan task probes `/healthz` in the background for up to 60 s and upserts `capability('web_search', {state, reason})`.
- **PwC archive:** `services/paper_code.py` downloads one pinned, sha256-checked JSON.gz through `PoliteClient` and loads it into the `paper_code` table (migration 0004). It is a local lookup, not a connector.
- **Tier-2 connectors:** `biorxiv` (get-only), `openreview` (decision lookup by title), `dblp` (SPARQL, get-only by DOI), `openaire` (Deep-run search and PID lookup). Routing gains a Deep-run extra and an enrichment order (`services/search.py`).

**Tech Stack:**
- Python 3.12, httpx 0.28.1 (already pinned), SQLAlchemy 2, Alembic, pydantic-settings.
- SearXNG `searxng/searxng:2026.10.25-54ca1d9cf` / commit `54ca1d9cfb08674f66261d7f351b063558dfcc62` (unchanged pin).
- No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-28-researchly-design.md` (F2.1, F3.2, §4.1, §4.2, §8.1, §8.1.1, §8.2, §8.3, §8.4, §9.1, §19.3, §22).
- Research: `docs/research/2026-11-06-step2c-sidecars.md` (**R-side §N**) and `docs/research/2026-11-06-step2c-connectors.md` (**R-conn §N**).
- ADRs: 0004 (native SearXNG), 0005 (Docling), 0008 (scholarly HTTP), 0009 (full text).

## Global Constraints

- **HTTP:**
  - All outbound HTTP goes through `PoliteClient` (`backend/src/researchly/connectors/http.py`). The one exception is the sidecar client (`connectors/sidecar.py`), which reaches only the CLI-configured sidecar origin.
  - Only `connectors/http.py`, `connectors/sidecar.py` and `connectors/ssrf.py` import `httpx` or `httpcore` (tests excepted). Task 2 adds a test that enforces this.
  - Never bypass a bot wall or challenge: the dblp.org search API (Anubis), OpenReview `/notes` (403 `ChallengeRequiredError`) and www.biorxiv.org (Cloudflare) are never called. A non-200 answer is a `SourceUnavailable`, never a retry with other headers.
- **Keyless:** no key, account or email anywhere: not in code, tests, cassettes or settings. Never add a `mailto`.
- **Connectors** follow the `add-scholarly-connector` skill checklist (`.claude/skills/add-scholarly-connector/SKILL.md`):
  - cache keys start `"<name>:v1:"`;
  - `TTL_METADATA` for singletons, `TTL_COUNTS` for answers that change (decisions), `TTL_SEARCH` keyed by `sha256(json.dumps(params, sort_keys=True))` for searches;
  - IDs go through `connectors/ids.py`; a record carrying an arXiv ID also carries `ids.arxiv_doi(...)`;
  - error mapping: 404 or empty → `[]`/`None`; other non-2xx → `SourceUnavailable(name, "HTTP n")`; an unparseable or wrongly shaped 2xx body → `SourceUnavailable(name, "unreadable response")`.
- **Limits** (`connectors/limits.yaml`, copied verbatim from R-conn):
  - `api2.openreview.net: [3.5, 1]   # /notes/search 20 per 60 s (ratelimit-policy: 20;w=60)`
  - `api.biorxiv.org: [1.0, 1]   # no published limit; spec polite 1 rps`
  - `sparql.dblp.org: [2.0, 1]   # no published limit; QLever, keep it gentle`
  - `api.openaire.eu: [60.0, 1]   # anonymous 60 per hour (docs); header claims 7199`
- **Cassettes:**
  - Record live, keyless and with no email: `uv run --directory backend pytest tests/connectors/test_<name>.py --record-mode=once`.
  - Before committing, grep the new cassettes for `mailto`, `cookie`, `authorization` and email addresses (`grep -rniE "mailto|cookie|authorization|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}" backend/tests/connectors/cassettes/test_<name>`). The only allowed address is `redacted@example.org` (the conftest hook). Never commit a 429 or 5xx.
  - Replay is record mode `none`: a missing cassette fails the test.
- **Tests:**
  - DB tests are marked `pytest.mark.db` and use the `db_url` fixture (a fresh database per test). Async tests run under `anyio` (the `anyio_backend` fixture picks a SelectorEventLoop).
  - Live checks are marked `pytest.mark.nightly` and run in `.github/workflows/nightly.yml`, never in CI.
  - CLI markers: `searxng` (installs the pinned SearXNG), `native` (real pg0), `docker` (needs `RESEARCHLY_TEST_DOCKER=1`).
- **Database:** Alembic only. Never edit 0001–0003. This plan adds `0004`.
- **Dependencies:** none are added. If one ever is: exact pin, then `uv lock --directory backend` and `uv lock --directory cli`.
- **Licences:** the PwC table is CC BY-SA 4.0: shown with the attribution `Papers with Code archive (CC BY-SA 4.0)`, never exported, bundled or redistributed.
- **Never** edit `docs/features.json` (edits other than `passes` need user approval). No flips happen in this step.
- **Verify:**
  - Backend: `uv run --directory backend pytest -q`, `uv run --directory backend pytest -m db -q`, `uv run --directory backend ruff check`, `uv run --directory backend ruff format --check`, `uv run --directory backend pyright` (0 errors).
  - CLI: `uv run --directory cli pytest -q`, `uv run --directory cli ruff check`, `uv run --directory cli ruff format --check`, `uv run --directory cli pyright`.
  - Repo: `uv run --no-project --python 3.12 scripts/repo_checks.py`.
- **Formatting:** run `uv run --directory backend ruff check --fix` and then `uv run --directory backend ruff format` (or the `cli` equivalents) before every lint check step, so import order (I001) and long lines (E501) are fixed before `--check` runs; a few code lines in this plan exceed 120 characters and the formatter wraps them. Keep comments under 120 characters, since ruff cannot wrap them.
- **Commits:** Conventional Commits, each ending with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (shown in Task 1; every commit carries it).

## Rulings made while planning

1. **SearXNG serves general web and code only** (R-side §A2): `keep_only: [brave, mojeek, yahoo, github]`, `formats: [json]`, `outgoing.request_timeout: 5.0`, `mojeek` un-inactivated and `yahoo` enabled, in both `infra/searxng/settings.yml` and `settings_text()`. Its scholarly engines stay off: the connectors own those sources and their per-IP quotas.
2. **SearXNG stays the default despite scraping** (spec F2.1 conflict): `brave`, `mojeek` and `yahoo` scrape result pages the way `ddgs` does. SearXNG stays the default because it is local and keyless, ships no vendored scraper, suspends an engine on a CAPTCHA, and can be turned off with `RESEARCHLY_WEB_SEARCH=off`. `ddgs` stays opt-in and unbuilt. Recorded in ADR 0010.
3. **URL plumbing:** native `start` picks the SearXNG port before it spawns the app and sets `RESEARCHLY_SEARXNG_URL=http://127.0.0.1:<port>` only when SearXNG runs (any inherited value is dropped). Docker sets it on `app` (`${RESEARCHLY_SEARXNG_URL-http://searxng:8080}`; the CLI passes the URL, or an empty string when web search is off). The backend reads `Settings.searxng_url`; unset or empty means off.
4. **Docker no longer blocks on SearXNG:** `up -d searxng` (no `--wait`) runs before `up -d --wait app`; `searxng` gains a `wget` healthcheck, and `app` has no `depends_on: searxng`. Web search is optional, so it never fails a start by being slow.
5. **The sidecar client is origin-pinned, not loopback-only** (R-side §A4): in Docker, `searxng` resolves to a private compose address that the SSRF guard refuses. It uses `httpx.AsyncClient(base_url=<origin>, trust_env=False, follow_redirects=False)`, a 2 MB cap, `connect=2 s` and a 15 s total, and `HostLimit(1.0, 2)` pacing. A redirect, a transport error or a timeout is `SourceUnavailable`. It sends `X-Real-IP: 127.0.0.1` to silence SearXNG's bot-detection log noise.
6. **The httpx boundary had no enforcement:** no test or repo check enforced "only http.py and ssrf.py import httpx". Task 2 adds `test_only_the_http_modules_import_httpx`; ADR 0008 and AGENTS.md are updated in Task 11.
7. **Web results stay `WebResult`s:** R-side §A3 suggested emitting stub `PaperRecord`s. Instead `WebResult.ids` carries the IDs that `ids.from_url` (plus a `doi` field, plus the arXiv DOI) finds; the step 3 Scout turns those into identity lookups. `publishedDate` is kept as a string and never used as a year. No caching yet (deferred).
8. **`capability.web_search` comes from a background probe** (spec §4.1 conflict: "at startup"): the app becomes healthy before SearXNG does (native 12–15 s, Docker 5–7 s), so a startup check would always say off. The lifespan task polls `/healthz` 30 times, 2 s apart, and upserts `{"state": "ready"}`, `{"state": "off", "reason": "not configured"}` or `{"state": "off", "reason": "unreachable: <why>"}`. It writes through a **sync** engine in `asyncio.to_thread`, because uvicorn on Windows runs a Proactor loop and psycopg async cannot. `Settings.env == "test"` skips the probe (unit-test apps have no database). Showing it in `/api/v1/system` and `doctor` is step 9.
9. **PwC archive** (R-conn §7): file `jul-28-links-between-papers-and-code.json.gz` at commit `1af3a546770f9b98665523ccba4b22b4f3e18c1f`, 26,261,205 bytes, sha256 `e7c60f2170880af3bd5a5b9469a951341435e4ccfd21d3d18393c52cfec24694` (from `X-Linked-Size` and `X-Linked-ETag`, probed 2026-11-06). It is downloaded through `PoliteClient`, which follows the 302 to `us.aws.cdn.hf.co` with per-hop SSRF checks. There is no content-type gate: the sha256 is the check. The ~150 MB JSON array is decoded one item at a time. Rows without a repo, or with neither an arXiv ID nor a title, are dropped. A title lookup is an exact normalized match, and only for titles strong enough to fuzzy-match (`fuzzy_title_ok`).
10. **bioRxiv's published DOI goes in `extra`, not `ids`** (R-conn §2 suggested a second ID): OpenAlex and Crossref keep the preprint and the journal version as separate works, and merging on it would fuse them. `extra.published_doi` keeps the link.
11. **OpenReview is a decision lookup only** (R-conn §1): `OpenReviewConnector.by_title(title, year)` calls `/notes/search`; `search`, `get` and `citations` return nothing (all capabilities `False`), so it never joins the fan-out. Matching: exact normalized title, `fuzzy_title_ok`, and a venue year from one before to two after the caller's year (venues trail preprints). The decision (`oral`, `spotlight`, `poster`, `accepted`, `not_accepted`) and the venue string go in `extra`. `authorids` (raw emails) are never read; the conftest hook already redacts every email in cassette bodies. The Oral, Spotlight and "Submitted to" forms were not seen live: unit tests cover them, and a cassette is added if recording meets one.
12. **DBLP is DOI-only venue enrichment over SPARQL** (R-conn §5): live probes on 2026-11-06 showed a case-insensitive title scan takes 8.3 s, and `ql:contains-word` returned 0 rows, so there is **no title lookup**. The query (68 ms) finds the records sharing an author and the title with the DOI's record; Inproceedings beats Article beats Informal. Both the upper- and lower-case DOI IRIs are asked for (DBLP stores arXiv DOIs upper-case); a mixed-case stored DOI is missed. The chosen record's own DOI goes in `extra.dblp_doi`, not `ids` (same reason as ruling 10). `Informal` maps to `venue_type="repository"`, matching OpenAlex.
13. **OpenAIRE uses one instance** (R-conn §4): IDs, year, licence and OA link come from the `instances[]` entry holding the requested ID, else (search) the earliest-dated one. A deduplicated record (`dedup` in its id) with no such instance is dropped; its top-level date, publisher and venue are never used. DOIs containing a comma are skipped (it may be read as a list). Using OpenAIRE OA links as a full-text tier is step 3.
14. **Tier-2 data lives in `extra`:** `SOURCE_PRIORITY` appends `dblp`, `openreview`, `biorxiv`, `openaire` after the §8.3 list, so they only fill fields no Tier-1 source has. Venue priors read `extra` (`dblp_venue`, `openreview_decision`) at step 4/5.
15. **Routing** (§8.1.1): `default_sources(..., deep=True)` adds `openaire` (60 calls per hour). `enrichment_sources(record, domains)` returns `openalex`, `crossref`, then `biorxiv` for a `10.1101/` DOI, `dblp` for `ml`/`cs` papers with a DOI or arXiv ID whose venue type is unknown or `repository`, and `openreview` for `ml` papers (called through `by_title`). The PwC table is looked up by the enrichment step itself. Step 3's Scouts call these.
16. **Deferred by the controller:** GROBID (step 4, Docker-only `grobid/grobid:0.9.1-crf`, internal network with no egress, `consolidate*=0`); `paper_refs` and keeping the arXiv HTML and JATS bibliographies (step 4); CORE (an optional-key connector, later); ACL Anthology; arXiv OAI-PMH; the OpenCitations Meta dump; OpenReview reviews (403 challenge).
17. **Native GROBID is dropped** (R-side §B7): no Windows `pdfalto`, no runnable release, about 1 GB of disk and 4 GB of RAM. Native mode uses the IDs-first reference path. The `DEGRADED_NATIVE` line "GROBID reference parsing needs Docker mode" stays true.
18. **CORE leaves the keyless chain** (spec F3.2 and §8.1.1 conflict): keyless CORE gives no full text and 100 tokens a day (R-conn §3). It moves to the optional-key table.
19. **Nightly web search runs in the existing `connectors-live` job:** a step starts the pinned SearXNG image with `infra/searxng/settings.yml` via `docker run` and exports `RESEARCHLY_SEARXNG_URL`; the live test skips when the variable is unset (local runs).
20. **Web search off switch:** `RESEARCHLY_WEB_SEARCH=off` in the CLI's environment: native skips the SearXNG install and spawn; Docker skips `up searxng` and passes an empty URL. It needs no UI until step 9.

---

### Task 1: SearXNG settings, URL plumbing and the off switch

**Files:**
- Modify: `infra/searxng/settings.yml`, `infra/compose.yaml`
- Modify: `cli/src/researchly_cli/searxng.py` (`_SETTINGS`), `cli/src/researchly_cli/native.py` (`start`), `cli/src/researchly_cli/docker.py` (`compose_env`, `start`), `cli/src/researchly_cli/main.py` (`start`)
- Modify: `backend/src/researchly/config.py`
- Test: `cli/tests/test_searxng.py`, `cli/tests/test_docker.py`, `cli/tests/test_main.py`, `backend/tests/test_app.py`

**Interfaces:**
- `Settings.searxng_url: str | None = None` (env `RESEARCHLY_SEARXNG_URL`; unset or `""` means web search is off).
- `docker.SEARXNG_URL = "http://searxng:8080"`; `docker.compose_env(secrets, port, web_search: bool = True) -> dict[str, str]` adds `RESEARCHLY_SEARXNG_URL` (the URL, or `""` when off); `docker.start(secrets, compose, web_search: bool = True) -> state.State`.
- `native.start(secrets, searxng_home=None)` unchanged signature; the app env carries `RESEARCHLY_SEARXNG_URL` only when `searxng_home` is given.
- `main._web_search_on() -> bool`: `False` when `RESEARCHLY_WEB_SEARCH` is `off` (case-insensitive).

- [ ] **Step 1: Write the failing CLI tests**

In `cli/tests/test_searxng.py`, add `import urllib.error` to the imports, then add:

```python
def test_settings_keep_only_the_general_and_code_engines():
    settings = yaml.safe_load(searxng.settings_text())
    assert settings["use_default_settings"] == {"engines": {"keep_only": ["brave", "mojeek", "yahoo", "github"]}}
    assert settings["search"]["formats"] == ["json"]  # the HTML UI's search answers 403
    assert settings["outgoing"]["request_timeout"] == 5.0
    assert settings["engines"] == [{"name": "mojeek", "inactive": False}, {"name": "yahoo", "disabled": False}]
    assert settings["server"] == {"limiter": False, "public_instance": False, "image_proxy": False}
```

Change `test_native_start_supervises_searxng_and_records_it` to also check the app's env (append after the existing asserts):

```python
    _, _, _, app_env = spawns[0]
    assert app_env["RESEARCHLY_SEARXNG_URL"] == f"http://127.0.0.1:{run.searxng_port}"
```

Replace `test_native_start_without_searxng_spawns_none` with:

```python
def test_native_start_without_searxng_spawns_none(fakes, monkeypatch):
    monkeypatch.setenv("RESEARCHLY_SEARXNG_URL", "http://127.0.0.1:1")  # a stale value must not reach the app
    run = native.start(state.load_secrets(), searxng_home=None)
    spawns = [e for e in fakes if e[0] == "spawn"]
    assert len(spawns) == 1
    assert "RESEARCHLY_SEARXNG_URL" not in spawns[0][3]
    assert run.searxng_port is None and run.searxng_pid is None
```

After `test_start_still_works_when_the_install_fails`, add:

```python
def test_start_with_web_search_off_skips_searxng(web_search, monkeypatch):
    monkeypatch.setenv("RESEARCHLY_WEB_SEARCH", "OFF")

    def never(home):
        raise AssertionError("web search is off: nothing is installed")

    monkeypatch.setattr(searxng, "install", never)
    result = runner.invoke(main.app, ["start"])
    assert result.exit_code == 0, result.output
    assert web_search["home"] is None
    assert "Web search is off" in result.output
```

At the end of the file, add a real-install check of the shipped settings (it needs no engine traffic: `/config` and a refused HTML search only):

```python
@pytest.mark.searxng
def test_real_settings_enable_exactly_the_four_engines(tmp_path):
    home = tmp_path / "sx"
    searxng.install(home)
    settings_file = tmp_path / "settings.yml"
    settings_file.write_text(searxng.settings_text(), "utf-8")
    port = state.free_port(0)
    proc = subprocess.Popen(  # noqa: S603
        searxng.command(home, port), env=searxng.env(home, settings_file, "test-secret-0123456789abcdef")
    )
    try:
        assert state.wait_until(lambda: searxng.healthy(port), 120)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/config", timeout=30) as response:  # noqa: S310 - fixed loopback URL
            config = json.load(response)
        assert {e["name"]: e["enabled"] for e in config["engines"]} == dict.fromkeys(
            ("brave", "mojeek", "yahoo", "github"), True
        )
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/search?q=hello", timeout=30)  # noqa: S310 - fixed loopback URL
        assert refused.value.code == 403  # HTML format is not enabled
    finally:
        supervise.terminate_tree(proc.pid)
```

If `/config` at this pin names the flag differently than `enabled`, print one engine entry, adjust the comprehension to that key, and keep the assertion "exactly these four, all on".

In `cli/tests/test_docker.py`, add `import yaml` to the imports, then add:

```python
def test_start_runs_searxng_without_blocking_the_app(runs):
    docker.start(state.load_secrets(), ("docker", "compose"))
    ups = [cmd[cmd.index("up") :] for cmd, _ in runs if "up" in cmd]
    assert ups[-2:] == [["up", "-d", "searxng"], ["up", "-d", "--wait", "app"]]
    assert runs[-1][1]["env"]["RESEARCHLY_SEARXNG_URL"] == "http://searxng:8080"


def test_start_with_web_search_off_leaves_searxng_down(runs):
    docker.start(state.load_secrets(), ("docker", "compose"), web_search=False)
    assert not any("searxng" in cmd for cmd, _ in runs)
    assert runs[-1][1]["env"]["RESEARCHLY_SEARXNG_URL"] == ""


def test_compose_points_the_app_at_searxng_with_a_healthcheck():
    services = yaml.safe_load(docker.COMPOSE_FILE.read_text("utf-8"))["services"]
    assert services["app"]["environment"]["RESEARCHLY_SEARXNG_URL"] == "${RESEARCHLY_SEARXNG_URL-http://searxng:8080}"
    assert "searxng" not in services["app"].get("depends_on", {})  # web search is optional
    assert services["searxng"]["healthcheck"]["test"] == [
        "CMD",
        "wget",
        "-q",
        "--spider",
        "http://127.0.0.1:8080/healthz",
    ]
```

In `cli/tests/test_main.py`, the three fakes at lines 142, 208 and 273 take `(secrets, compose)`. Change all three lambdas to accept the new keyword. Lines 142 and 273:

```python
        docker, "start", lambda secrets, compose, web_search=True: state.State(mode="docker", port=9996, compose=tuple(compose))
```

Line 208 (`test_start_docker_uses_the_engine_compose_command`) keeps its `seen.append`:

```python
        lambda secrets, compose, web_search=True: seen.append(compose)
        or state.State(mode="docker", port=9998, compose=tuple(compose)),
```

In `backend/tests/test_app.py`, add:

```python
def test_settings_read_the_searxng_url(monkeypatch):
    monkeypatch.setenv("RESEARCHLY_SEARXNG_URL", "http://127.0.0.1:8766")
    assert Settings().searxng_url == "http://127.0.0.1:8766"
    monkeypatch.delenv("RESEARCHLY_SEARXNG_URL")
    assert Settings().searxng_url is None
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run --directory cli pytest -q tests/test_searxng.py tests/test_docker.py tests/test_main.py` and `uv run --directory backend pytest -q tests/test_app.py`
Expected: FAIL (the settings test, the env asserts, the docker `up` order, the compose asserts, the off switch and `Settings.searxng_url`).

- [ ] **Step 3: Rewrite the SearXNG settings**

`infra/searxng/settings.yml` becomes:

```yaml
# SearXNG for Researchly (spec F2.1, ADR 0010): a private instance for general web and code search only.
# Scholarly engines stay off: the connectors own those sources and their quotas. JSON API only; rate limiter off.
# The secret comes from SEARXNG_SECRET, injected by the CLI.
use_default_settings:
  engines:
    keep_only: [brave, mojeek, yahoo, github]
server:
  limiter: false
  public_instance: false
  image_proxy: false
search:
  formats: [json]
outgoing:
  request_timeout: 5.0
engines:
  - name: mojeek
    inactive: false
  - name: yahoo
    disabled: false
```

In `cli/src/researchly_cli/searxng.py`, replace `_SETTINGS` with the same YAML (no comments needed; the equality test compares parsed YAML):

```python
_SETTINGS = """\
use_default_settings:
  engines:
    keep_only: [brave, mojeek, yahoo, github]
server:
  limiter: false
  public_instance: false
  image_proxy: false
search:
  formats: [json]
outgoing:
  request_timeout: 5.0
engines:
  - name: mojeek
    inactive: false
  - name: yahoo
    disabled: false
"""
```

The existing mock-engine test overwrites `use_default_settings` and `engines`, so it keeps working (R-side §A2).

- [ ] **Step 4: Native: pick the port first and pass the URL**

In `cli/src/researchly_cli/native.py` `start`, replace the block from `env = {...}` down to the end of the SearXNG spawn with:

```python
        env = {**os.environ, "RESEARCHLY_DATABASE_URL": app_url, "RESEARCHLY_LOCAL_TOKEN": secrets.local_token}
        env.pop("RESEARCHLY_SEARXNG_URL", None)  # web search is off unless this start runs SearXNG
        sx_port = None
        if searxng_home is not None:  # web search is optional: the app runs without it
            sx_port = state.free_port(searxng.PORT)  # known before the app starts, so the app can be told
            env["RESEARCHLY_SEARXNG_URL"] = f"http://127.0.0.1:{sx_port}"
        logs = state.log_dir()
        supervisor = spawn(supervisor_cmd(logs / "app.log", uvicorn), logs / "supervisor.log", env)
        if searxng_home is not None and sx_port is not None:
            sx_env = searxng.env(searxng_home, searxng_home / "settings.yml", secrets.searxng)
            sx_cmd = supervisor_cmd(logs / "searxng.log", searxng.command(searxng_home, sx_port))
            sx_supervisor = spawn(sx_cmd, logs / "supervisor.log", sx_env)
```

The `run = state.State(...)` block that follows is unchanged.

- [ ] **Step 5: Docker: pass the URL and stop waiting on SearXNG**

In `cli/src/researchly_cli/docker.py`, add after `DOWN`:

```python
SEARXNG_URL = "http://searxng:8080"  # the compose service, on the internal network
```

Change `compose_env`:

```python
def compose_env(secrets: state.Secrets, port: int, web_search: bool = True) -> dict[str, str]:
    """Secrets reach compose only through the environment of the compose process (spec §16.1)."""
    return {
        **os.environ,
        "RESEARCHLY_DATA_DIR": str(state.data_dir().resolve()),
        "RESEARCHLY_PORT": str(port),
        "RESEARCHLY_PG_SUPERUSER_PASSWORD": secrets.pg_superuser,
        "RESEARCHLY_DB_APP_PASSWORD": secrets.db_app,
        "RESEARCHLY_DB_RO_PASSWORD": secrets.db_ro,
        "RESEARCHLY_LOCAL_TOKEN": secrets.local_token,
        "RESEARCHLY_SEARXNG_SECRET": secrets.searxng,
        "RESEARCHLY_SEARXNG_URL": SEARXNG_URL if web_search else "",  # empty: the app records web search off
    }
```

Change `start`:

```python
def start(secrets: state.Secrets, compose: Sequence[str], web_search: bool = True) -> state.State:
    port = state.free_port(APP_PORT)
    env = compose_env(secrets, port, web_search)
    # Created as the user: rootful Docker would create it root-owned, and native mode needs to write in pg/18/
    (state.data_dir() / "pg" / "18" / "docker").mkdir(parents=True, exist_ok=True)
    _compose(compose, ["build"], env)
    try:
        _compose(compose, ["up", "-d", "--wait", "postgres"], env)
        _compose(compose, ["run", "--rm", "migrate"], env)  # spec §16.1: a failed migration aborts the start
        if web_search:
            _compose(compose, ["up", "-d", "searxng"], env)  # optional: the app probes it, so never wait on it
        _compose(compose, ["up", "-d", "--wait", "app"], env)
    except BaseException:  # a failed or interrupted start leaves nothing running
        with contextlib.suppress(Exception):
            _compose(compose, DOWN, env)
        raise
    return state.State(mode="docker", port=port, compose=tuple(compose))
```

In `infra/compose.yaml`, add to the `app` service's `environment`:

```yaml
      RESEARCHLY_SEARXNG_URL: ${RESEARCHLY_SEARXNG_URL-http://searxng:8080}
```

and give `searxng` a healthcheck (the image has busybox `wget`, R-side §A4):

```yaml
  searxng:
    image: searxng/searxng:2026.10.25-54ca1d9cf
    environment:
      SEARXNG_SECRET: ${RESEARCHLY_SEARXNG_SECRET:?}
    volumes:
      - ./searxng/settings.yml:/etc/searxng/settings.yml:ro
    networks: [internal, default]
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://127.0.0.1:8080/healthz"]
      interval: 2s
      timeout: 5s
      retries: 30
    restart: unless-stopped
```

Do not add `depends_on: searxng` to `app`.

- [ ] **Step 6: The CLI off switch**

In `cli/src/researchly_cli/main.py`, add next to `_ensure_web_search`:

```python
def _web_search_on() -> bool:
    """`RESEARCHLY_WEB_SEARCH=off` turns web search off: its engines scrape result pages (ADR 0010)."""
    return os.environ.get("RESEARCHLY_WEB_SEARCH", "on").strip().lower() != "off"
```

In `start`, replace the lines from `search_home = None` through the `docker.start` call with:

```python
    web_search = _web_search_on()
    if not web_search:
        typer.echo("Web search is off (RESEARCHLY_WEB_SEARCH=off).")
    search_home = None
    if selection.mode == "native":
        _ensure_web_ui()  # the Docker image builds its own copy
        search_home = _ensure_web_search() if web_search else None  # so does the compose stack's searxng service
    try:
        if selection.mode == "docker" and selection.engine is not None:
            run = docker.start(secrets, selection.engine.compose, web_search=web_search)
```

The rest of `start` is unchanged.

- [ ] **Step 7: Backend setting**

In `backend/src/researchly/config.py`, add to `Settings` after `local_token`:

```python
    searxng_url: str | None = None  # set by the CLI (ADR 0010); unset or empty: web search is off
```

- [ ] **Step 8: Run the tests to confirm they pass**

Run: `uv run --directory cli pytest -q` and `uv run --directory backend pytest -q tests/test_app.py`
Expected: PASS. Then run the real SearXNG checks once (they install the pin, about 30 s each):
`uv run --directory cli pytest -m searxng -q -rA`
Expected: PASS (`test_installs_and_answers_json_from_a_mock_engine` and `test_real_settings_enable_exactly_the_four_engines`).

- [ ] **Step 9: Lint, type-check and commit**

```bash
uv run --directory cli ruff check && uv run --directory cli ruff format --check && uv run --directory cli pyright
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add infra cli/src cli/tests backend/src/researchly/config.py backend/tests/test_app.py
git commit -m "feat: SearXNG for general web and code, URL plumbing and an off switch" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Sidecar client and the httpx import guard

**Files:**
- Create: `backend/src/researchly/connectors/sidecar.py`
- Test: `backend/tests/connectors/test_sidecar.py`

**Interfaces:**
- `SidecarClient(base_url: str, *, source: str, timeout: float = 15.0, max_bytes: int = 2_000_000, limit: HostLimit | None = None, transport: httpx.AsyncBaseTransport | None = None)`. Raises `ValueError` unless `base_url` is a bare `http(s)://host[:port]` origin (no user info, path, query or fragment).
- `SidecarClient.source: str` (the error label, `"web"` for SearXNG).
- `async SidecarClient.get(path: str, params: Mapping[str, str | int] | None = None) -> Fetched` (`Fetched` from `connectors/http.py`). `path` must start with one `/`. Raises `ValueError` for any other path, `SourceUnavailable(source, "unexpected redirect (HTTP n)")` on a 3xx with `Location`, `SourceUnavailable(source, "unreachable: <ExceptionName>")` on a transport error or timeout, `ResponseTooLarge` over `max_bytes`. Any other status is returned for the caller to map.
- `async SidecarClient.aclose() -> None`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/connectors/test_sidecar.py`:

```python
import asyncio
import re
from pathlib import Path

import httpx
import pytest

from researchly.connectors.base import ResponseTooLarge, SourceUnavailable
from researchly.connectors.http import HostLimit
from researchly.connectors.sidecar import SidecarClient

pytestmark = pytest.mark.anyio

ORIGIN = "http://127.0.0.1:8766"


def client_for(handler, **kw) -> SidecarClient:
    kw.setdefault("limit", HostLimit(0.0, 2))
    return SidecarClient(ORIGIN, source="web", transport=httpx.MockTransport(handler), **kw)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8766/search",
        "http://user:pw@127.0.0.1:8766",
        "http://127.0.0.1:8766?x=1",
        "file:///etc/passwd",
        "searxng:8080",
        "http://",
    ],
)
def test_only_a_bare_origin_is_accepted(url):
    with pytest.raises(ValueError, match="origin"):
        SidecarClient(url, source="web")


def test_a_trailing_slash_and_a_service_name_are_fine():
    SidecarClient("http://searxng:8080/", source="web")
    SidecarClient("https://127.0.0.1:9", source="web")


@pytest.mark.parametrize("path", ["https://evil.example/x", "//evil.example/x", "search", ""])
async def test_paths_must_be_relative_to_the_origin(path):
    seen = []
    client = client_for(lambda r: seen.append(r) or httpx.Response(200))
    with pytest.raises(ValueError, match="relative"):
        await client.get(path)
    assert seen == []
    await client.aclose()


async def test_get_reaches_the_origin_with_params_and_the_real_ip_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1" and request.url.port == 8766
        assert request.url.path == "/search"
        assert request.url.params["q"] == "a b" and request.url.params["format"] == "json"
        assert request.headers["x-real-ip"] == "127.0.0.1"
        return httpx.Response(200, json={"results": []})

    client = client_for(handler)
    fetched = await client.get("/search", {"q": "a b", "format": "json"})
    assert fetched.status == 200 and fetched.json() == {"results": []}
    assert fetched.url.startswith(ORIGIN + "/search?")
    await client.aclose()


async def test_other_statuses_are_returned_for_the_caller():
    client = client_for(lambda r: httpx.Response(502, text="bad gateway"))
    assert (await client.get("/healthz")).status == 502
    await client.aclose()


async def test_a_redirect_is_never_followed():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest"})

    client = client_for(handler)
    with pytest.raises(SourceUnavailable, match="redirect"):
        await client.get("/search")
    assert len(seen) == 1
    await client.aclose()


async def test_an_oversized_body_is_refused():
    client = client_for(lambda r: httpx.Response(200, content=b"x" * 2048), max_bytes=1024)
    with pytest.raises(ResponseTooLarge):
        await client.get("/search")
    await client.aclose()


async def test_a_dead_sidecar_is_unavailable_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = client_for(handler)
    with pytest.raises(SourceUnavailable, match="unreachable: ConnectError"):
        await client.get("/healthz")
    await client.aclose()


async def test_a_slow_sidecar_times_out():
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    client = client_for(handler, timeout=0.05)
    with pytest.raises(SourceUnavailable, match="unreachable: TimeoutError"):
        await client.get("/search")
    await client.aclose()


async def test_requests_are_paced():
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    limit = HostLimit(1.0, 2, sleep=sleep, clock=lambda: 0.0)
    client = client_for(lambda r: httpx.Response(200), limit=limit)
    await client.get("/healthz")
    await client.get("/healthz")
    assert sleeps == [1.0]
    await client.aclose()


def test_only_the_http_modules_import_httpx():
    src = Path(__file__).resolve().parents[2] / "src" / "researchly"
    imports = re.compile(r"^\s*(?:import|from)\s+(?:httpx|httpcore)\b", re.M)
    found = {p.relative_to(src).as_posix() for p in src.rglob("*.py") if imports.search(p.read_text("utf-8"))}
    assert found == {"connectors/http.py", "connectors/sidecar.py", "connectors/ssrf.py"}
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run --directory backend pytest -q tests/connectors/test_sidecar.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'researchly.connectors.sidecar'`.

- [ ] **Step 3: Implement the client**

Create `backend/src/researchly/connectors/sidecar.py`:

```python
"""HTTP client for local sidecars (SearXNG now, GROBID at step 4): the only HTTP outside PoliteClient (ADR 0010).

Every request goes to the one origin the CLI configured (`RESEARCHLY_SEARXNG_URL`), and callers pass relative paths,
so no caller, page or model can pick the target. No redirects, no env proxy, a size cap and short timeouts. It is not
loopback-only: in Docker mode `searxng` resolves to a private compose-network address, which the SSRF guard refuses.
"""

import asyncio
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

from researchly.connectors.base import ResponseTooLarge, SourceUnavailable
from researchly.connectors.http import Fetched, HostLimit


class SidecarClient:
    def __init__(
        self,
        base_url: str,
        *,
        source: str,
        timeout: float = 15.0,
        max_bytes: int = 2_000_000,
        limit: HostLimit | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError(f"a sidecar URL must be a bare http(s) origin, got {base_url!r}")
        self.source = source
        self._timeout, self._max_bytes = timeout, max_bytes
        self._limit = limit or HostLimit(1.0, 2)  # one query a second, two in flight: engines ban fast clients
        self._http = httpx.AsyncClient(
            base_url=f"{parts.scheme}://{parts.netloc}",
            transport=transport,
            headers={"X-Real-IP": "127.0.0.1"},  # SearXNG's bot detection logs an error per request without it
            follow_redirects=False,
            trust_env=False,  # an env proxy would send local traffic elsewhere
            timeout=httpx.Timeout(timeout, connect=2.0),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Fetched:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError(f"sidecar paths are relative to the configured origin, got {path!r}")
        request = self._http.build_request("GET", path, params=params)
        base = self._http.base_url
        if (request.url.scheme, request.url.host, request.url.port) != (base.scheme, base.host, base.port):
            raise ValueError(f"{path!r} is not relative to the configured origin")
        body = bytearray()
        async with self._limit:
            try:
                async with asyncio.timeout(self._timeout):
                    response = await self._http.send(request, stream=True)
                    try:
                        if response.is_redirect:
                            raise SourceUnavailable(self.source, f"unexpected redirect (HTTP {response.status_code})")
                        async for chunk in response.aiter_bytes():
                            body += chunk
                            if len(body) > self._max_bytes:
                                raise ResponseTooLarge(self.source, f"over {self._max_bytes} bytes")
                    finally:
                        await response.aclose()
            except (httpx.HTTPError, TimeoutError) as exc:
                raise SourceUnavailable(self.source, f"unreachable: {type(exc).__name__}") from exc
        headers = {k.lower(): v for k, v in response.headers.items()}  # the same shape PoliteClient returns
        return Fetched(response.status_code, headers, bytes(body), str(request.url))
```

`httpx.TimeoutException` is an `httpx.HTTPError`, so an httpx timeout reports its own class name; `asyncio.timeout` raises `TimeoutError`. `SourceUnavailable` and `ResponseTooLarge` are not `HTTPError`s, so they pass through unchanged.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run --directory backend pytest -q tests/connectors/test_sidecar.py`
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/connectors/sidecar.py backend/tests/connectors/test_sidecar.py
git commit -m "feat: origin-pinned sidecar client and an httpx import guard" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: URL-to-ID mapping and the web search service

**Files:**
- Modify: `backend/src/researchly/connectors/ids.py` (add `from_url`)
- Create: `backend/src/researchly/services/web_search.py`
- Modify: `.github/workflows/nightly.yml`
- Test: `backend/tests/connectors/test_ids.py`, `backend/tests/services/test_web_search.py`, `backend/tests/services/test_web_search_live.py`

**Interfaces:**
- `ids.from_url(url: str) -> ExternalId | None`. Never raises. Recognizes `doi.org`, `dx.doi.org` and `oadoi.org/<doi>`; `arxiv.org` and `export.arxiv.org` `abs|pdf|html/<id>`; `pubmed.ncbi.nlm.nih.gov/<pmid>`; `www.ncbi.nlm.nih.gov/pubmed/<pmid>` and `/pmc/articles/<PMCID>`; `pmc.ncbi.nlm.nih.gov/articles/<PMCID>`; `europepmc.org/article|abstract/MED/<pmid>` and `/PMC/<PMCID>`.
- `services/web_search.py`:
  - `CATEGORIES = {"general": "general", "code": "it"}`.
  - `@dataclass(frozen=True) WebResult(url: str, title: str, snippet: str | None, engines: tuple[str, ...], published: str | None, ids: tuple[ExternalId, ...] = ())`.
  - `@dataclass WebSearchOutcome(results: list[WebResult], unresponsive: dict[str, str] = {})` (engine → reason).
  - `parse_result(row: Any) -> WebResult | None`.
  - `async web_search(client: SidecarClient, query: str, *, category: Literal["general", "code"] = "general", limit: int = 20) -> WebSearchOutcome`. Raises `SourceUnavailable(client.source, "HTTP n")` on a non-200 and `SourceUnavailable(client.source, "unreadable response")` on a bad body; transport failures arrive from the client as `SourceUnavailable` too.

- [ ] **Step 1: Write the failing ID tests**

Append to `backend/tests/connectors/test_ids.py`:

```python
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://doi.org/10.1038/nature14539", ExternalId("doi", "10.1038/nature14539")),
        ("http://dx.doi.org/10.1016/S0140-6736(97)11096-0", ExternalId("doi", "10.1016/s0140-6736(97)11096-0")),
        ("https://oadoi.org/10.48550/arXiv.1706.03762", ExternalId("doi", "10.48550/arxiv.1706.03762")),
        ("https://doi.org/10.1000%2Fabc", ExternalId("doi", "10.1000/abc")),
        ("https://arxiv.org/abs/1706.03762v7", ExternalId("arxiv", "1706.03762")),
        ("https://arxiv.org/pdf/1706.03762v7.pdf", ExternalId("arxiv", "1706.03762")),
        ("https://arxiv.org/html/2310.06825v1/#S2", ExternalId("arxiv", "2310.06825")),
        ("http://export.arxiv.org/abs/hep-th/9901001", ExternalId("arxiv", "hep-th/9901001")),
        ("https://pubmed.ncbi.nlm.nih.gov/26017442/", ExternalId("pmid", "26017442")),
        ("https://www.ncbi.nlm.nih.gov/pubmed/26017442", ExternalId("pmid", "26017442")),
        ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4481139/", ExternalId("pmcid", "PMC4481139")),
        ("https://pmc.ncbi.nlm.nih.gov/articles/PMC4481139", ExternalId("pmcid", "PMC4481139")),
        ("https://europepmc.org/article/MED/26017442", ExternalId("pmid", "26017442")),
        ("https://europepmc.org/abstract/MED/26017442", ExternalId("pmid", "26017442")),
        ("https://europepmc.org/article/PMC/PMC4481139", ExternalId("pmcid", "PMC4481139")),
        ("https://europepmc.org/article/PPR/PPR123456", None),  # preprint IDs have no scheme
        ("https://dl.acm.org/citation.cfm?id=3295222", None),  # no DOI in the URL: stays a plain web result
        ("https://arxiv.org/list/cs.CL/recent", None),
        ("https://doi.org/", None),
        ("ftp://doi.org/10.1038/nature14539", None),
        ("https://evil.example/doi.org/10.1038/nature14539", None),
        ("http://[::1", None),  # urlsplit raises: never escapes
        ("not a url", None),
    ],
)
def test_from_url(url, expected):
    assert ids.from_url(url) == expected
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest -q tests/connectors/test_ids.py`
Expected: FAIL with `AttributeError: module 'researchly.connectors.ids' has no attribute 'from_url'`.

- [ ] **Step 3: Implement `from_url`**

In `backend/src/researchly/connectors/ids.py`, add `from urllib.parse import unquote, urlsplit` to the imports, and append:

```python
def from_url(url: str) -> ExternalId | None:
    """The scholarly ID a landing-page URL names (web search results, OpenReview links), or None. Never raises."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").removeprefix("www.")
    except ValueError:  # e.g. an unbalanced IPv6 bracket
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    path = unquote(parts.path)
    segs = [s for s in path.split("/") if s]
    value: str | None
    if host in ("doi.org", "dx.doi.org", "oadoi.org"):
        return ExternalId("doi", value) if (value := doi(path.lstrip("/"))) else None
    if host in ("arxiv.org", "export.arxiv.org") and len(segs) >= 2 and segs[0] in ("abs", "pdf", "html"):
        return ExternalId("arxiv", value) if (value := arxiv("/".join(segs[1:]))) else None
    if host == "pubmed.ncbi.nlm.nih.gov" and len(segs) == 1:
        return ExternalId("pmid", value) if (value := pmid(segs[0])) else None
    if host == "ncbi.nlm.nih.gov" and len(segs) == 2 and segs[0] == "pubmed":
        return ExternalId("pmid", value) if (value := pmid(segs[1])) else None
    if host == "ncbi.nlm.nih.gov" and len(segs) >= 3 and segs[:2] == ["pmc", "articles"]:
        return ExternalId("pmcid", value) if (value := pmcid(segs[2])) else None
    if host == "pmc.ncbi.nlm.nih.gov" and len(segs) >= 2 and segs[0] == "articles":
        return ExternalId("pmcid", value) if (value := pmcid(segs[1])) else None
    if host == "europepmc.org" and len(segs) == 3 and segs[0] in ("article", "abstract"):
        if segs[1].upper() == "MED":
            return ExternalId("pmid", value) if (value := pmid(segs[2])) else None
        if segs[1].upper() == "PMC":
            return ExternalId("pmcid", value) if (value := pmcid(segs[2])) else None
    return None
```

Run: `uv run --directory backend pytest -q tests/connectors/test_ids.py`
Expected: PASS.

- [ ] **Step 4: Write the failing web search tests**

Create `backend/tests/services/test_web_search.py` (the payload follows the SearXNG JSON shape in R-side §A3):

```python
import httpx
import pytest

from researchly.connectors.base import ExternalId, SourceUnavailable
from researchly.connectors.http import HostLimit
from researchly.connectors.sidecar import SidecarClient
from researchly.services.web_search import WebResult, parse_result, web_search

pytestmark = pytest.mark.anyio

SAMPLE = {
    "query": "attention is all you need",
    "number_of_results": None,
    "results": [
        {
            "url": "https://arxiv.org/abs/1706.03762v7",
            "title": "[1706.03762] Attention Is All You Need",
            "content": "The dominant <b>sequence transduction</b> models &amp; more",
            "engine": "brave",
            "engines": ["brave", "mojeek"],
            "publishedDate": "2023-08-02T00:00:00",
            "score": 2.0,
            "category": "general",
        },
        {"url": "https://oadoi.org/10.1038/nature14539", "title": "Deep learning", "content": "", "engines": ["yahoo"]},
        {
            "url": "https://example.org/blog/transformers",
            "title": "A <i>blog</i> post",
            "content": "Plain text",
            "engines": ["mojeek"],
            "doi": "10.5555/Example.1",
            "publishedDate": None,
        },
        {"url": "javascript:alert(1)", "title": "bad", "engines": ["brave"]},
        {"title": "no url", "engines": ["brave"]},
    ],
    "answers": [],
    "corrections": [],
    "infoboxes": [],
    "suggestions": [],
    "unresponsive_engines": [["yahoo", "timeout"], ["brave", "Suspended: CAPTCHA"]],
}


def client_for(handler) -> SidecarClient:
    return SidecarClient(
        "http://127.0.0.1:8766", source="web", transport=httpx.MockTransport(handler), limit=HostLimit(0.0, 2)
    )


async def test_results_map_to_web_results_with_ids_and_engine_failures():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert dict(request.url.params) == {"q": "attention is all you need", "format": "json", "categories": "general"}
        return httpx.Response(200, json=SAMPLE)

    client = client_for(handler)
    outcome = await web_search(client, "  attention is all you need ")
    await client.aclose()
    assert [r.url for r in outcome.results] == [
        "https://arxiv.org/abs/1706.03762v7",
        "https://oadoi.org/10.1038/nature14539",
        "https://example.org/blog/transformers",
    ]
    first, second, third = outcome.results
    assert first == WebResult(
        url="https://arxiv.org/abs/1706.03762v7",
        title="[1706.03762] Attention Is All You Need",
        snippet="The dominant sequence transduction models & more",
        engines=("brave", "mojeek"),
        published="2023-08-02T00:00:00",  # a crawl date, never a paper's year
        ids=(ExternalId("arxiv", "1706.03762"), ExternalId("doi", "10.48550/arxiv.1706.03762")),
    )
    assert second.ids == (ExternalId("doi", "10.1038/nature14539"),) and second.snippet is None
    assert third.title == "A blog post" and third.ids == (ExternalId("doi", "10.5555/example.1"),)
    assert third.published is None
    assert outcome.unresponsive == {"yahoo": "timeout", "brave": "Suspended: CAPTCHA"}


async def test_code_search_asks_the_it_category_and_limit_truncates():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["categories"] == "it"
        return httpx.Response(200, json=SAMPLE)

    client = client_for(handler)
    outcome = await web_search(client, "transformer pytorch", category="code", limit=1)
    await client.aclose()
    assert len(outcome.results) == 1


async def test_an_empty_query_makes_no_request():
    seen = []
    client = client_for(lambda r: seen.append(r) or httpx.Response(200, json=SAMPLE))
    assert (await web_search(client, "   ")).results == []
    await client.aclose()
    assert seen == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(403, text="Forbidden"), "HTTP 403"),
        (httpx.Response(200, text="<html>not json</html>"), "unreadable response"),
        (httpx.Response(200, json=["not", "an", "object"]), "unreadable response"),
        (httpx.Response(200, json={"results": [], "unresponsive_engines": [["only-one"]]}), "unreadable response"),
    ],
)
async def test_bad_answers_are_unavailable(response, message):
    client = client_for(lambda r: response)
    with pytest.raises(SourceUnavailable, match=message):
        await web_search(client, "x")
    await client.aclose()


def test_parse_result_rejects_rows_without_a_web_url():
    assert parse_result({"url": "javascript:alert(1)"}) is None
    assert parse_result({"url": "http://[::1"}) is None
    assert parse_result("not a row") is None
    assert parse_result({"url": "https://example.org/"}) == WebResult(
        url="https://example.org/", title="https://example.org/", snippet=None, engines=(), published=None
    )
```

- [ ] **Step 5: Run them to confirm they fail**

Run: `uv run --directory backend pytest -q tests/services/test_web_search.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'researchly.services.web_search'`.

- [ ] **Step 6: Implement the service**

Create `backend/src/researchly/services/web_search.py`:

```python
"""General and code web search through the local SearXNG sidecar (spec F2.1, §8.1; ADR 0010).

Titles and snippets are untrusted page text (spec §19.1): shown and stored, never put into a prompt directly.
"""

import html
import re
from dataclasses import dataclass, field
from typing import Any, Literal, TypeGuard
from urllib.parse import urlsplit

from researchly.connectors import ids
from researchly.connectors.base import ExternalId, SourceUnavailable
from researchly.connectors.sidecar import SidecarClient

CATEGORIES = {"general": "general", "code": "it"}  # SearXNG categories: brave, mojeek, yahoo; then github
_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class WebResult:
    url: str
    title: str
    snippet: str | None
    engines: tuple[str, ...]
    published: str | None  # the engine's page date (Brave: the crawl date); never a paper's year
    ids: tuple[ExternalId, ...] = ()  # scholarly IDs named by the URL or a `doi` field, for identity resolution


@dataclass
class WebSearchOutcome:
    results: list[WebResult]
    unresponsive: dict[str, str] = field(default_factory=dict)  # engine -> reason ("timeout", "CAPTCHA", ...)


def _plain(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    return " ".join(html.unescape(_TAGS.sub(" ", raw)).split()) or None


def _web_url(url: Any) -> TypeGuard[str]:  # narrows `url` for pyright
    try:
        return isinstance(url, str) and urlsplit(url).scheme in ("http", "https")
    except ValueError:
        return False


def parse_result(row: Any) -> WebResult | None:
    if not isinstance(row, dict) or not _web_url(url := row.get("url")):
        return None
    found: list[ExternalId] = []
    if (eid := ids.from_url(url)) is not None:
        found.append(eid)
    if isinstance(row.get("doi"), str) and (doi := ids.doi(row["doi"])):
        found.append(ExternalId("doi", doi))
    if arxiv_id := next((e.value for e in found if e.scheme == "arxiv"), None):
        found.append(ExternalId("doi", ids.arxiv_doi(arxiv_id)))
    engines = row.get("engines")
    published = row.get("publishedDate")
    return WebResult(
        url=url,
        title=_plain(row.get("title")) or url,
        snippet=_plain(row.get("content")),
        engines=tuple(e for e in engines if isinstance(e, str)) if isinstance(engines, list) else (),
        published=published if isinstance(published, str) else None,
        ids=tuple(dict.fromkeys(found)),
    )


async def web_search(
    client: SidecarClient,
    query: str,
    *,
    category: Literal["general", "code"] = "general",
    limit: int = 20,
) -> WebSearchOutcome:
    text = query.strip()
    if not text:
        return WebSearchOutcome([])
    fetched = await client.get("/search", {"q": text, "format": "json", "categories": CATEGORIES[category]})
    if fetched.status != 200:
        raise SourceUnavailable(client.source, f"HTTP {fetched.status}")
    try:
        body = fetched.json()
        results = [r for row in body.get("results") or [] if (r := parse_result(row))]
        unresponsive = {str(engine): str(reason) for engine, reason in body.get("unresponsive_engines") or []}
    except (ValueError, AttributeError, TypeError) as exc:
        raise SourceUnavailable(client.source, "unreadable response") from exc
    return WebSearchOutcome(results[:limit], unresponsive)
```

Run: `uv run --directory backend pytest -q tests/services/test_web_search.py`
Expected: PASS.

- [ ] **Step 7: Add the nightly live check**

Create `backend/tests/services/test_web_search_live.py`:

```python
"""Nightly: the shipped SearXNG settings reach real engines. Run: RESEARCHLY_SEARXNG_URL=... pytest -m nightly."""

import os

import pytest

from researchly.connectors.sidecar import SidecarClient
from researchly.services.web_search import web_search

pytestmark = [pytest.mark.nightly, pytest.mark.anyio]


@pytest.fixture
async def client():
    url = os.environ.get("RESEARCHLY_SEARXNG_URL")
    if not url:
        pytest.skip("set RESEARCHLY_SEARXNG_URL to a SearXNG running infra/searxng/settings.yml")
    sidecar = SidecarClient(url, source="web")
    yield sidecar
    await sidecar.aclose()


async def test_general_search_reaches_the_general_engines(client):
    outcome = await web_search(client, "retrieval augmented generation survey")
    assert outcome.results, outcome.unresponsive
    engines = {e for r in outcome.results for e in r.engines}
    assert engines & {"brave", "mojeek", "yahoo"}, outcome.unresponsive
    assert not engines - {"brave", "mojeek", "yahoo", "github"}  # scholarly engines stay off


async def test_code_search_reaches_github(client):
    outcome = await web_search(client, "transformer implementation pytorch", category="code")
    assert any("github" in r.engines for r in outcome.results), outcome.unresponsive
```

In `.github/workflows/nightly.yml`, in the `connectors-live` job, insert before `- run: uv sync --locked`:

```yaml
      - name: Start the pinned SearXNG with the shipped settings
        working-directory: .
        run: |
          docker run -d --name searxng -p 127.0.0.1:8888:8080 \
            -e SEARXNG_SECRET="$(openssl rand -hex 32)" \
            -v "$PWD/infra/searxng/settings.yml:/etc/searxng/settings.yml:ro" \
            searxng/searxng:2026.10.25-54ca1d9cf
          for _ in $(seq 60); do curl -fsS http://127.0.0.1:8888/healthz && exit 0; sleep 2; done
          docker logs searxng
          exit 1
```

and give the test step the URL:

```yaml
      - run: uv run pytest -m nightly -q
        env:
          RESEARCHLY_SEARXNG_URL: http://127.0.0.1:8888
```

Keep the image tag equal to `infra/compose.yaml` (ADR 0004 bump checklist; Task 11 adds this file to it).

- [ ] **Step 8: Run the tests**

Run: `uv run --directory backend pytest -q tests/connectors/test_ids.py tests/services/test_web_search.py tests/services/test_web_search_live.py`
Expected: PASS, with the live tests deselected (the default `addopts` excludes `nightly`). Optionally, with Docker running, start the container as in the workflow and run `RESEARCHLY_SEARXNG_URL=http://127.0.0.1:8888 uv run --directory backend pytest -m nightly -q tests/services/test_web_search_live.py` once.

- [ ] **Step 9: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/connectors/ids.py backend/src/researchly/services/web_search.py backend/tests/connectors/test_ids.py backend/tests/services/test_web_search.py backend/tests/services/test_web_search_live.py .github/workflows/nightly.yml
git commit -m "feat: web search through SearXNG with scholarly IDs read from URLs" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `capability.web_search` from a background probe

**Files:**
- Create: `backend/src/researchly/services/capabilities.py`
- Modify: `backend/src/researchly/app.py` (lifespan)
- Test: `backend/tests/services/test_capabilities.py`, `cli/tests/test_native.py`, `cli/tests/test_docker.py`

**Interfaces:**
- `async probe_web_search(client: SidecarClient | None, *, attempts: int = 30, interval: float = 2.0, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> dict[str, str]`: `{"state": "ready"}`, `{"state": "off", "reason": "not configured"}` or `{"state": "off", "reason": "unreachable: <ConnectError | HTTP 502 | ...>"}`.
- `write_capability(database_url: str, key: str, value: Any) -> None`: a sync upsert into `capability` (run it in a thread).
- `async record_web_search(database_url: str, client: SidecarClient | None) -> None`: probes, then writes; a database failure is logged (exception type only), never raised.
- `create_app` gains a lifespan: `app.state.searxng: SidecarClient | None` (built when `settings.searxng_url` is non-empty, closed on shutdown) and `app.state.capability_probe: asyncio.Task[None] | None` (`None` when `settings.env == "test"`; cancelled on shutdown).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/services/test_capabilities.py`:

```python
import httpx
import pytest
from sqlalchemy import create_engine, text

from researchly.app import create_app
from researchly.config import Settings
from researchly.connectors.http import HostLimit
from researchly.connectors.sidecar import SidecarClient
from researchly.db import migrate
from researchly.services.capabilities import probe_web_search, record_web_search, write_capability

pytestmark = pytest.mark.anyio

TOKEN = "t" * 43


def sidecar(handler) -> SidecarClient:
    return SidecarClient(
        "http://127.0.0.1:8766", source="web", transport=httpx.MockTransport(handler), limit=HostLimit(0.0, 1)
    )


def read_capability(url: str, key: str):
    engine = create_engine(url)
    with engine.connect() as conn:
        value = conn.scalar(text("SELECT value FROM capability WHERE key = :k"), {"k": key})
    engine.dispose()
    return value


async def test_no_url_means_off_and_not_configured():
    assert await probe_web_search(None) == {"state": "off", "reason": "not configured"}


async def test_ready_once_healthz_answers():
    answers = iter([502, 502, 200])
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = sidecar(lambda r: httpx.Response(next(answers)))
    assert await probe_web_search(client, sleep=sleep) == {"state": "ready"}
    await client.aclose()
    assert sleeps == [2.0, 2.0]


@pytest.mark.parametrize(
    ("answer", "reason"),
    [(None, "unreachable: ConnectError"), (503, "unreachable: HTTP 503")],
)
async def test_gives_up_with_the_last_reason(answer, reason):
    def handler(request: httpx.Request) -> httpx.Response:
        if answer is None:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(answer)

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = sidecar(handler)
    assert await probe_web_search(client, attempts=3, sleep=sleep) == {"state": "off", "reason": reason}
    await client.aclose()
    assert len(sleeps) == 2  # no sleep after the last attempt


async def test_test_apps_build_the_client_and_skip_the_probe():
    app = create_app(Settings(env="test", searxng_url="http://127.0.0.1:9"), token=TOKEN)
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.searxng, SidecarClient)
        assert app.state.capability_probe is None


async def test_an_empty_url_builds_no_client():
    app = create_app(Settings(env="test", searxng_url=""), token=TOKEN)
    async with app.router.lifespan_context(app):
        assert app.state.searxng is None


async def test_a_database_failure_is_logged_not_raised(caplog):
    await record_web_search("postgresql+psycopg://nobody@127.0.0.1:1/none", None)
    assert "could not record the web search capability" in caplog.text


@pytest.mark.db
def test_write_capability_upserts(db_url):
    migrate.upgrade(db_url)
    write_capability(db_url, "web_search", {"state": "off", "reason": "unreachable: ConnectError"})
    write_capability(db_url, "web_search", {"state": "ready"})
    assert read_capability(db_url, "web_search") == {"state": "ready"}


@pytest.mark.db
async def test_record_web_search_writes_ready(db_url):
    migrate.upgrade(db_url)
    client = sidecar(lambda r: httpx.Response(200, text="OK"))
    await record_web_search(db_url, client)
    await client.aclose()
    assert read_capability(db_url, "web_search") == {"state": "ready"}


@pytest.mark.db
async def test_the_app_records_web_search_in_the_background(db_url):
    migrate.upgrade(db_url)
    app = create_app(Settings(env="production", database_url=db_url, searxng_url=None), token=TOKEN)
    async with app.router.lifespan_context(app):
        assert app.state.capability_probe is not None
        await app.state.capability_probe
    assert read_capability(db_url, "web_search") == {"state": "off", "reason": "not configured"}
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest -q tests/services/test_capabilities.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'researchly.services.capabilities'`.

- [ ] **Step 3: Implement the service**

Create `backend/src/researchly/services/capabilities.py`:

```python
"""Runtime capabilities (spec §4.1): what this install can do, kept in the `capability` table for services and the UI.

Web search is probed in the background: SearXNG becomes healthy seconds after the app (native 12-15 s, Docker
5-7 s), so a check at startup would always say off (ADR 0010).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert

from researchly.connectors.base import ConnectorError
from researchly.connectors.sidecar import SidecarClient
from researchly.db.models import Capability

logger = logging.getLogger(__name__)


async def probe_web_search(
    client: SidecarClient | None,
    *,
    attempts: int = 30,
    interval: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, str]:
    if client is None:
        return {"state": "off", "reason": "not configured"}
    last = "no answer"
    for attempt in range(attempts):
        try:
            fetched = await client.get("/healthz")
        except ConnectorError as exc:
            last = type(exc.__cause__ or exc).__name__
        else:
            if fetched.status == 200:
                return {"state": "ready"}
            last = f"HTTP {fetched.status}"
        if attempt < attempts - 1:
            await sleep(interval)
    return {"state": "off", "reason": f"unreachable: {last}"}


def write_capability(database_url: str, key: str, value: Any) -> None:
    """Sync on purpose: uvicorn on Windows runs a Proactor loop, where psycopg's async driver cannot."""
    engine = create_engine(database_url, connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as conn:
            stmt = insert(Capability).values(key=key, value=value)
            conn.execute(stmt.on_conflict_do_update(index_elements=[Capability.key], set_={"value": stmt.excluded.value}))
    finally:
        engine.dispose()


async def record_web_search(database_url: str, client: SidecarClient | None) -> None:
    value = await probe_web_search(client)
    try:
        await asyncio.to_thread(write_capability, database_url, "web_search", value)
    except Exception as exc:  # never take the app down over a status row
        # the type only: a driver message could carry connection details
        logger.warning("could not record the web search capability: %s", type(exc).__name__)
```

- [ ] **Step 4: Add the app lifespan**

In `backend/src/researchly/app.py`, merge these imports into the existing import blocks in sorted order (`ruff check --fix` sorts them):

```python
import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from researchly.connectors.sidecar import SidecarClient
from researchly.services.capabilities import record_web_search
```

Add above `create_app`:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    searxng = SidecarClient(settings.searxng_url, source="web") if settings.searxng_url else None
    app.state.searxng = searxng
    probe = None
    if settings.env != "test":  # unit-test apps have no database
        probe = asyncio.create_task(record_web_search(settings.database_url, searxng))
    app.state.capability_probe = probe
    try:
        yield
    finally:
        if probe is not None:
            probe.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await probe
        if searxng is not None:
            await searxng.aclose()
```

and pass it to `FastAPI(...)`: add `lifespan=_lifespan,` after `redoc_url=None,`.

- [ ] **Step 5: Run the backend tests**

Run: `uv run --directory backend pytest -q` and `uv run --directory backend pytest -m db -q tests/services/test_capabilities.py tests/db`
Expected: PASS. The existing app, guard and SPA tests use `Settings(env="test")`, so no probe runs there.

- [ ] **Step 6: Check it in both runtimes**

In `cli/tests/test_native.py` `test_native_start_serves_the_app_on_pg0_and_stops_cleanly`, replace the block from `with engine.connect() as conn:` through `engine.dispose()` with:

```python
        with engine.connect() as conn:
            exts = set(conn.scalars(text("SELECT extname FROM pg_extension")))
            bm25 = conn.scalar(text("SELECT value FROM capability WHERE key = 'bm25_engine'"))

        def web_search():
            with engine.connect() as conn:
                return conn.scalar(text("SELECT value FROM capability WHERE key = 'web_search'"))

        # native.start without a SearXNG home: the app records web search as off
        assert state.wait_until(lambda: web_search() == {"state": "off", "reason": "not configured"}, 30, 0.5)
        engine.dispose()
```

In `cli/tests/test_docker.py` `test_docker_stack_serves_health_with_bm25`, after the `bm25_engine` assertion, add:

```python
        ready = "SELECT value->>'state' FROM capability WHERE key = 'web_search'"
        assert state.wait_until(lambda: psql(ready) == "ready", 90, 2)  # SearXNG starts after the app
```

Run: `uv run --directory cli pytest -m native -q tests/test_native.py` (about 30 s). Run the Docker test when Docker is available: `RESEARCHLY_TEST_DOCKER=1 uv run --directory cli pytest -m docker -q tests/test_docker.py` (it rebuilds the app image; about 10 min cold).
Expected: PASS.

- [ ] **Step 7: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
uv run --directory cli ruff check && uv run --directory cli ruff format --check && uv run --directory cli pyright
git add backend/src/researchly/services/capabilities.py backend/src/researchly/app.py backend/tests/services/test_capabilities.py cli/tests/test_native.py cli/tests/test_docker.py
git commit -m "feat: probe web search in the background and record the capability" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Papers with Code archive table

**Files:**
- Create: `backend/src/researchly/db/migrations/versions/0004_paper_code.py`
- Modify: `backend/src/researchly/db/models.py` (add `PaperCode`)
- Create: `backend/src/researchly/services/paper_code.py`
- Test: `backend/tests/db/test_migrations.py`, `backend/tests/services/test_paper_code.py`, `backend/tests/services/test_paper_code_live.py`

**Interfaces:**
- Table `paper_code`:
  - columns `paper_url text`, `repo_url text`, `arxiv_id text NULL`, `title_norm text NULL`, `is_official bool`, `framework text NULL`, `mentioned_in_paper bool`;
  - primary key `(paper_url, repo_url)`; indexes `ix_paper_code_arxiv_id` and `ix_paper_code_title_norm`;
  - model `PaperCode`.
- `services/paper_code.py`:
  - constants `COMMIT`, `FILE`, `URL`, `SHA256`, `SIZE`, `SOURCE = "pwc"`, `ATTRIBUTION = "Papers with Code archive (CC BY-SA 4.0)"`, `BATCH = 5000`;
  - `@dataclass(frozen=True) CodeLink(repo_url: str, is_official: bool, framework: str | None, mentioned_in_paper: bool)`;
  - `async download(client: PoliteClient) -> bytes` (raises `SourceUnavailable(SOURCE, "HTTP n" | "sha256 mismatch")`);
  - `iter_links(gz: bytes, chunk: int = 1 << 20) -> Iterator[dict[str, Any]]` (raises `ValueError` on a truncated or malformed file);
  - `to_row(item: Any) -> dict[str, Any] | None`;
  - `async load(session: AsyncSession, gz: bytes) -> int` (rows read; idempotent);
  - `async ensure_loaded(session: AsyncSession, client: PoliteClient) -> int` (0 when the table already has rows);
  - `async lookup(session: AsyncSession, *, arxiv_id: str | None = None, title: str | None = None) -> list[CodeLink]` (arXiv ID first, then the title; official links first).
- Callers show `ATTRIBUTION` next to any code link. Step 3's enrichment calls `ensure_loaded` (one 26 MB download per install) and `lookup`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/db/test_migrations.py`, add `"paper_code"` to `TABLES`.

Create `backend/tests/services/test_paper_code.py`:

```python
import gzip
import hashlib
import json

import httpx
import pytest
from sqlalchemy import func, select

from researchly.connectors.base import SourceUnavailable
from researchly.connectors.http import PoliteClient
from researchly.db import migrate
from researchly.db.engine import make_engine, make_sessionmaker
from researchly.db.models import PaperCode
from researchly.services import paper_code as pc
from researchly.services.identity import normalize_title

pytestmark = pytest.mark.anyio

T2T = "https://github.com/tensorflow/tensor2tensor"
TORCH = "https://github.com/jadore801120/attention-is-all-you-need-pytorch"
GAT = "https://github.com/example/gat-mol"


def link(paper, title, arxiv_id, repo, official, framework="none", in_paper=False):
    return {
        "paper_url": f"https://paperswithcode.com/paper/{paper}",
        "paper_title": title,
        "paper_arxiv_id": arxiv_id,
        "paper_url_abs": None,
        "paper_url_pdf": None,
        "repo_url": repo,
        "is_official": official,
        "mentioned_in_paper": in_paper,
        "mentioned_in_github": False,
        "framework": framework,
    }


ITEMS = [
    link("attention-is-all-you-need", "Attention Is All You Need", "1706.03762", T2T, True, "tf", True),
    link("attention-is-all-you-need", "Attention Is All You Need", "1706.03762", TORCH, False, "pytorch"),
    link("gat-mol", "Graph Attention Networks for Molecules", None, GAT, True),
    link("no-repo", "A Paper Without Code", "1810.10559", None, False),
    link("weak-title", "Hi", None, "https://github.com/example/hi", True),  # no arXiv ID, title too weak
    link("bad-link", "A Paper With A Bad Link", "2001.00001", "javascript:alert(1)", True),
]


def gz(items=ITEMS) -> bytes:
    return gzip.compress(json.dumps(items, indent=1).encode())


def test_iter_links_reads_items_across_chunk_edges():
    assert list(pc.iter_links(gz(), chunk=7)) == ITEMS
    assert list(pc.iter_links(gzip.compress(b"[]"))) == []


@pytest.mark.parametrize(
    "data",
    [gzip.compress(json.dumps(ITEMS).encode()[:-40]), gz()[:-10], b"not gzip at all"],
)
def test_a_truncated_or_broken_file_fails(data):
    with pytest.raises(ValueError, match="links file"):
        list(pc.iter_links(data, chunk=64))


def test_to_row_keeps_linkable_rows_only():
    rows = [pc.to_row(item) for item in ITEMS]
    assert rows[0] == {
        "paper_url": "https://paperswithcode.com/paper/attention-is-all-you-need",
        "repo_url": T2T,
        "arxiv_id": "1706.03762",
        "title_norm": "attention is all you need",
        "is_official": True,
        "framework": "tf",
        "mentioned_in_paper": True,
    }
    assert rows[2] is not None and rows[2]["arxiv_id"] is None and rows[2]["framework"] is None
    assert rows[2]["title_norm"] == normalize_title("Graph Attention Networks for Molecules")
    assert rows[3:] == [None, None, None]
    assert pc.to_row("not a row") is None


async def test_download_follows_the_cdn_redirect_and_checks_sha256(monkeypatch):
    body = gz()
    monkeypatch.setattr(pc, "SHA256", hashlib.sha256(body).hexdigest())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "huggingface.co":
            assert request.url.path == f"/datasets/pwc-archive/files/resolve/{pc.COMMIT}/{pc.FILE}"
            return httpx.Response(302, headers={"Location": "https://us.aws.cdn.hf.co/xet-bridge-us/abc?sig=1"})
        assert request.url.host == "us.aws.cdn.hf.co"
        return httpx.Response(200, content=body, headers={"content-type": "application/gzip"})

    async with PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)}) as client:
        assert await pc.download(client) == body


@pytest.mark.parametrize(
    ("response", "message"), [(httpx.Response(200, content=b"x"), "sha256"), (httpx.Response(404), "HTTP 404")]
)
async def test_a_changed_or_missing_file_is_refused(response, message):
    transport = httpx.MockTransport(lambda r: response)
    async with PoliteClient(transport=transport, limits={"default": (0.0, 1)}) as client:
        with pytest.raises(SourceUnavailable, match=message):
            await pc.download(client)


@pytest.fixture
async def sm(db_url):
    migrate.upgrade(db_url)
    engine = make_engine(db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.mark.db
async def test_load_then_look_up_by_arxiv_id_or_title(sm):
    async with sm() as s:
        assert await pc.load(s, gz()) == 3
        assert await pc.load(s, gz()) == 3  # idempotent
        assert await s.scalar(select(func.count()).select_from(PaperCode)) == 3
        links = await pc.lookup(s, arxiv_id="arXiv:1706.03762v7")
        assert links == [pc.CodeLink(T2T, True, "tf", True), pc.CodeLink(TORCH, False, "pytorch", False)]
        assert [x.repo_url for x in await pc.lookup(s, title="Graph attention networks for molecules.")] == [GAT]
        fallback = await pc.lookup(s, arxiv_id="9999.99999", title="Graph Attention Networks for Molecules")
        assert [x.repo_url for x in fallback] == [GAT]  # no arXiv row: the title is tried next
        assert await pc.lookup(s, title="Hi") == []
        assert await pc.lookup(s) == []


@pytest.mark.db
async def test_ensure_loaded_downloads_once(sm, monkeypatch):
    calls = []

    async def fake_download(client):
        calls.append(client)
        return gz()

    monkeypatch.setattr(pc, "download", fake_download)
    async with sm() as s, PoliteClient() as client:
        assert await pc.ensure_loaded(s, client) == 3
        assert await pc.ensure_loaded(s, client) == 0
    assert len(calls) == 1
```

Create `backend/tests/services/test_paper_code_live.py`:

```python
"""Nightly: the pinned PwC archive file still downloads, matches its sha256 and parses."""

import pytest

from researchly.connectors.http import PoliteClient
from researchly.services import paper_code as pc

pytestmark = [pytest.mark.nightly, pytest.mark.anyio]


async def test_the_pinned_archive_downloads_and_parses():
    async with PoliteClient() as client:
        body = await pc.download(client)
    rows = [r for item in pc.iter_links(body) if (r := pc.to_row(item))]
    assert len(rows) > 250_000
    assert any(r["arxiv_id"] == "1706.03762" and r["is_official"] for r in rows)
```

Run it once locally: `uv run --directory backend pytest -m nightly -q tests/services/test_paper_code_live.py` (a 26 MB download). If the row count is below 250,000, set the floor to 90% of the measured count and say so in the commit message.

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest -q tests/services/test_paper_code.py`
Expected: FAIL with `ImportError: cannot import name 'PaperCode'`.

- [ ] **Step 3: Add the model and migration**

In `backend/src/researchly/db/models.py`, add before the `HttpCacheRow` comment:

```python
class PaperCode(Base):
    """Papers with Code archive links (CC BY-SA 4.0): a local lookup, never exported or redistributed (spec §8.4)."""

    __tablename__ = "paper_code"
    paper_url: Mapped[str] = mapped_column(Text, primary_key=True)
    repo_url: Mapped[str] = mapped_column(Text, primary_key=True)
    arxiv_id: Mapped[str | None] = mapped_column(Text, index=True)
    title_norm: Mapped[str | None] = mapped_column(Text, index=True)
    is_official: Mapped[bool] = mapped_column(Boolean)
    framework: Mapped[str | None] = mapped_column(Text)
    mentioned_in_paper: Mapped[bool] = mapped_column(Boolean)
```

Create `backend/src/researchly/db/migrations/versions/0004_paper_code.py`:

```python
"""paper_code: the Papers with Code archive's paper-to-repository links

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_code",
        sa.Column("paper_url", sa.Text(), primary_key=True),
        sa.Column("repo_url", sa.Text(), primary_key=True),
        sa.Column("arxiv_id", sa.Text()),
        sa.Column("title_norm", sa.Text()),
        sa.Column("is_official", sa.Boolean(), nullable=False),
        sa.Column("framework", sa.Text()),
        sa.Column("mentioned_in_paper", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_paper_code_arxiv_id", "paper_code", ["arxiv_id"])
    op.create_index("ix_paper_code_title_norm", "paper_code", ["title_norm"])


def downgrade() -> None:
    op.drop_table("paper_code")
```

Run: `uv run --directory backend pytest -m db -q tests/db`
Expected: PASS, including `test_models_match_migrations`.

- [ ] **Step 4: Implement the service**

Create `backend/src/researchly/services/paper_code.py`:

```python
"""Papers with Code archive (spec §8.1; R-conn §7): paper -> code links from the frozen July 2025 snapshot.

CC BY-SA 4.0: shown with ATTRIBUTION; never exported, bundled or redistributed (ShareAlike applies to adapted
tables we would share). One pinned, sha256-checked file, downloaded through PoliteClient on first use.
"""

import asyncio
import gzip
import hashlib
import io
import itertools
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from researchly.connectors import ids
from researchly.connectors.base import SourceUnavailable
from researchly.connectors.http import PoliteClient
from researchly.db.models import PaperCode
from researchly.services.identity import fuzzy_title_ok, normalize_title

COMMIT = "1af3a546770f9b98665523ccba4b22b4f3e18c1f"  # huggingface.co/datasets/pwc-archive/files, probed 2026-11-06
FILE = "jul-28-links-between-papers-and-code.json.gz"
URL = f"https://huggingface.co/datasets/pwc-archive/files/resolve/{COMMIT}/{FILE}"
SHA256 = "e7c60f2170880af3bd5a5b9469a951341435e4ccfd21d3d18393c52cfec24694"  # X-Linked-ETag
SIZE = 26_261_205  # X-Linked-Size
SOURCE = "pwc"
ATTRIBUTION = "Papers with Code archive (CC BY-SA 4.0)"
BATCH = 5000  # rows per INSERT: 7 columns stay under psycopg's 65,535 parameters
_BETWEEN = " \t\r\n,[]"  # what separates the array's items


@dataclass(frozen=True)
class CodeLink:
    repo_url: str
    is_official: bool
    framework: str | None
    mentioned_in_paper: bool


async def download(client: PoliteClient) -> bytes:
    # No content-type gate: the sha256 is the check. PoliteClient follows the 302 to the HF CDN with SSRF checks.
    fetched = await client.get(URL, source=SOURCE, max_bytes=SIZE + 1_000_000)
    if fetched.status != 200:
        raise SourceUnavailable(SOURCE, f"HTTP {fetched.status}")
    if hashlib.sha256(fetched.body).hexdigest() != SHA256:
        raise SourceUnavailable(SOURCE, "sha256 mismatch")
    return fetched.body


def iter_links(gz: bytes, chunk: int = 1 << 20) -> Iterator[dict[str, Any]]:
    """The array's objects one at a time: the file inflates to ~150 MB of JSON, so it is never loaded whole."""
    decoder = json.JSONDecoder()
    buf = ""
    try:
        with gzip.open(io.BytesIO(gz), "rt", encoding="utf-8") as text:
            eof = False
            while not eof:
                more = text.read(chunk)
                eof = not more
                buf += more
                pos = 0
                while True:
                    while pos < len(buf) and buf[pos] in _BETWEEN:
                        pos += 1
                    if pos == len(buf):
                        break
                    try:
                        item, pos = decoder.raw_decode(buf, pos)
                    except json.JSONDecodeError:
                        if eof:
                            raise ValueError("the links file is truncated or malformed") from None
                        break  # an item cut at the chunk edge: read more
                    if isinstance(item, dict):
                        yield item
                buf = buf[pos:]
    except (OSError, EOFError, UnicodeDecodeError) as exc:  # gzip.BadGzipFile is an OSError
        raise ValueError(f"the links file is unreadable: {type(exc).__name__}") from exc


def _web(url: Any) -> bool:
    return isinstance(url, str) and url.startswith(("https://", "http://"))  # shown as a link: never javascript:


def to_row(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not _web(item.get("paper_url")) or not _web(item.get("repo_url")):
        return None
    raw_arxiv, title = item.get("paper_arxiv_id"), item.get("paper_title")
    arxiv_id = ids.arxiv(raw_arxiv) if isinstance(raw_arxiv, str) else None
    norm = normalize_title(title) if isinstance(title, str) else ""
    title_norm = norm if fuzzy_title_ok(norm) else None  # weak titles never match (spec §8.3)
    if arxiv_id is None and title_norm is None:
        return None
    framework = item.get("framework")
    return {
        "paper_url": item["paper_url"],
        "repo_url": item["repo_url"],
        "arxiv_id": arxiv_id,
        "title_norm": title_norm,
        "is_official": item.get("is_official") is True,
        "framework": framework if isinstance(framework, str) and framework not in ("", "none") else None,
        "mentioned_in_paper": item.get("mentioned_in_paper") is True,
    }


async def load(session: AsyncSession, gz: bytes) -> int:
    rows = await asyncio.to_thread(lambda: [r for item in iter_links(gz) if (r := to_row(item))])
    for batch in itertools.batched(rows, BATCH):
        await session.execute(insert(PaperCode).values(list(batch)).on_conflict_do_nothing())
    await session.commit()
    return len(rows)


async def ensure_loaded(session: AsyncSession, client: PoliteClient) -> int:
    if await session.scalar(select(PaperCode.paper_url).limit(1)) is not None:
        return 0  # the snapshot is frozen: one load is enough
    return await load(session, await download(client))


async def lookup(session: AsyncSession, *, arxiv_id: str | None = None, title: str | None = None) -> list[CodeLink]:
    conditions = []
    if arxiv_id and (a := ids.arxiv(arxiv_id)):
        conditions.append(PaperCode.arxiv_id == a)
    if title and fuzzy_title_ok(norm := normalize_title(title)):
        conditions.append(PaperCode.title_norm == norm)  # exact normalized title: the archive has no years
    for condition in conditions:
        rows = await session.scalars(
            select(PaperCode).where(condition).order_by(PaperCode.is_official.desc(), PaperCode.repo_url)
        )
        links: dict[str, CodeLink] = {}
        for r in rows:
            links.setdefault(r.repo_url, CodeLink(r.repo_url, r.is_official, r.framework, r.mentioned_in_paper))
        if links:
            return list(links.values())
    return []
```

The lookup test relies on `is_official DESC`: T2T comes before TORCH because it is official, not because of URL order.

- [ ] **Step 5: Run the tests**

Run: `uv run --directory backend pytest -q tests/services/test_paper_code.py` and `uv run --directory backend pytest -m db -q tests/services/test_paper_code.py tests/db`
Expected: PASS.

- [ ] **Step 6: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/db backend/src/researchly/services/paper_code.py backend/tests/db/test_migrations.py backend/tests/services/test_paper_code.py backend/tests/services/test_paper_code_live.py
git commit -m "feat: Papers with Code archive table from a pinned, sha256-checked snapshot" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: bioRxiv/medRxiv connector

**Files:**
- Create: `backend/src/researchly/connectors/biorxiv.py`
- Modify: `backend/src/researchly/connectors/limits.yaml`, `backend/src/researchly/connectors/__init__.py`
- Test: `backend/tests/connectors/test_biorxiv.py` (+ cassettes under `backend/tests/connectors/cassettes/test_biorxiv/`), `backend/tests/connectors/test_registry.py`, `backend/tests/connectors/test_live.py`

**Interfaces:**
- `BiorxivConnector(client: PoliteClient)`, `name = "biorxiv"`, capabilities `search=False, get=True, citations_in=False, citations_out=False`.
- `get(ids)` accepts only DOIs starting `10.1101/`. It asks `biorxiv`, then `medrxiv`, and returns one record per DOI found.
- `parse_details(rows: list[Any], server: str) -> PaperRecord | None`.
- Record fields:
  - `ids=[doi]`; `venue` is `"bioRxiv"` or `"medRxiv"`; `venue_type="repository"`; `is_oa=True`;
  - `license`: `cc_by` becomes `cc-by`, and `cc_no` becomes `None`;
  - `year` is the first version's date, and the other fields come from the latest version;
  - `extra = {"server", "version", "category", "jatsxml", "published_doi"}` (ruling 10).
- Errors:
  - a status other than `"ok"` gives `[]` (the API answers 200 even for "no posts found");
  - 404 gives `[]`;
  - other non-200 statuses raise `SourceUnavailable("biorxiv", "HTTP n")`;
  - a bad body raises `SourceUnavailable("biorxiv", "unreadable response")`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/connectors/test_biorxiv.py`:

```python
import httpx
import pytest

from researchly.connectors import biorxiv as bx
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.biorxiv import BiorxivConnector
from researchly.connectors.http import PoliteClient

pytestmark = pytest.mark.anyio

DOI = "10.1101/2020.03.22.002386"


def row(version, date, **kw):
    base = {
        "doi": DOI,
        "title": "A SARS-CoV-2-Human Protein-Protein Interaction Map",
        "authors": "Gordon, D. E.; Jang, G. M.; Bouhaddou, M.",
        "author_corresponding": "Nevan Krogan",
        "date": date,
        "version": str(version),
        "type": "new results",
        "license": "cc_by",
        "category": "systems biology",
        "jatsxml": f"https://www.biorxiv.org/content/early/{date}/{DOI}.source.xml",
        "abstract": "An abstract.",
        "published": "10.1038/s41586-020-2286-9",
        "server": "bioRxiv",
    }
    return base | kw


ROWS = [row(1, "2020-03-22", title="Old title"), row(3, "2020-04-03"), row(2, "2020-03-27")]


def envelope(rows, status="ok"):
    return {"messages": [{"status": status}], "collection": rows}


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def test_parse_details_takes_the_latest_version_and_the_first_year():
    rec = bx.parse_details(ROWS, "biorxiv")
    assert rec is not None
    assert rec.source == "biorxiv" and rec.ids == [ExternalId("doi", DOI)]
    assert rec.title == "A SARS-CoV-2-Human Protein-Protein Interaction Map"  # v3, not v1's
    assert rec.year == 2020 and rec.abstract == "An abstract."
    assert [a.name for a in rec.authors] == ["Gordon, D. E.", "Jang, G. M.", "Bouhaddou, M."]
    assert (rec.venue, rec.venue_type, rec.is_oa, rec.license) == ("bioRxiv", "repository", True, "cc-by")
    assert rec.extra == {
        "server": "biorxiv",
        "version": 3,
        "category": "systems biology",
        "jatsxml": f"https://www.biorxiv.org/content/early/2020-04-03/{DOI}.source.xml",
        "published_doi": "10.1038/s41586-020-2286-9",  # extra only: the journal version is another work
    }


def test_parse_details_edge_cases():
    unpublished = bx.parse_details([row(1, "2021-01-05", published="NA", license="cc_no")], "medrxiv")
    assert unpublished is not None
    assert unpublished.extra["published_doi"] is None and unpublished.license is None
    assert unpublished.venue == "medRxiv"
    assert bx.parse_details([], "biorxiv") is None
    assert bx.parse_details([{"doi": "not a doi"}, "junk"], "biorxiv") is None


async def test_get_tries_biorxiv_then_medrxiv():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if "/biorxiv/" in request.url.path:
            return httpx.Response(200, json=envelope([], "no posts found"))
        return httpx.Response(200, json=envelope([row(1, "2021-01-05")]))

    async with client_for(handler) as client:
        [rec] = await BiorxivConnector(client).get([ExternalId("doi", f"https://doi.org/{DOI.upper()}")])
    assert seen == [f"/details/biorxiv/{DOI}", f"/details/medrxiv/{DOI}"]
    assert rec.venue == "medRxiv"


async def test_other_dois_and_other_schemes_make_no_request():
    seen = []
    async with client_for(lambda r: seen.append(r) or httpx.Response(500)) as client:
        conn = BiorxivConnector(client)
        assert await conn.get([ExternalId("doi", "10.1038/nature14539"), ExternalId("arxiv", "1706.03762")]) == []
        assert await conn.search(SearchQuery("anything")) == []
        assert await conn.citations(ExternalId("doi", DOI), "in") == []
    assert seen == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(403, text="Forbidden"), "HTTP 403"),
        (httpx.Response(200, text="<html>Cloudflare</html>"), "unreadable response"),
        (httpx.Response(200, json={"collection": []}), "unreadable response"),  # no messages
    ],
)
async def test_errors(response, message):
    async with client_for(lambda r: response) as client:
        with pytest.raises(SourceUnavailable, match=message):
            await BiorxivConnector(client).get([ExternalId("doi", DOI)])


async def test_a_404_is_not_found():
    async with client_for(lambda r: httpx.Response(404)) as client:
        assert await BiorxivConnector(client).get([ExternalId("doi", DOI)]) == []


@pytest.mark.vcr
async def test_get_a_published_preprint(polite):
    [rec] = await BiorxivConnector(polite).get([ExternalId("doi", DOI)])
    assert "SARS-CoV-2" in rec.title and rec.year == 2020 and rec.venue == "bioRxiv"
    assert rec.extra["published_doi"] == "10.1038/s41586-020-2286-9"
    assert rec.authors and rec.abstract and rec.license


@pytest.mark.vcr
async def test_not_found_on_either_server(polite):
    assert await BiorxivConnector(polite).get([ExternalId("doi", "10.1101/0000.00.00.000000")]) == []
```

In `backend/tests/connectors/test_registry.py`, add `"biorxiv"` to `NAMES` and rename `test_registry_holds_the_eight_sources` to `test_registry_holds_every_source`. In `backend/tests/connectors/test_live.py`, add `"biorxiv": ExternalId("doi", "10.1101/2020.03.22.002386"),` to `GET_PROBES`.

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest -q tests/connectors/test_biorxiv.py tests/connectors/test_registry.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'researchly.connectors.biorxiv'`.

- [ ] **Step 3: Implement the connector**

Create `backend/src/researchly/connectors/biorxiv.py`:

```python
"""bioRxiv/medRxiv connector (R-conn §2): get-only by 10.1101 DOI; the API has no search. Keyless.

The API answers HTTP 200 even for errors ({"messages": [{"status": "no posts found"}]}). Full text on
www.biorxiv.org is behind a Cloudflare challenge: `jatsxml` is kept as data, never fetched.
"""

from typing import Any, Literal
from urllib.parse import quote

from researchly.connectors import ids
from researchly.connectors.base import (
    Author,
    ConnectorCaps,
    ExternalId,
    PaperRecord,
    SearchQuery,
    SourceUnavailable,
)
from researchly.connectors.http import TTL_METADATA, PoliteClient

BASE = "https://api.biorxiv.org"
KEY = "biorxiv:v1"
SERVERS = ("biorxiv", "medrxiv")  # both use the 10.1101 prefix, so the server is found by asking
PREFIX = "10.1101/"
_VENUES = {"biorxiv": "bioRxiv", "medrxiv": "medRxiv"}


def _version(row: dict[str, Any]) -> int:
    try:
        return int(row.get("version") or 0)
    except (TypeError, ValueError):
        return 0


def _license(raw: Any) -> str | None:
    if not isinstance(raw, str) or raw in ("", "cc_no"):  # cc_no: no CC licence, all rights reserved
        return None
    return raw.replace("_", "-")  # cc_by_nc_nd -> cc-by-nc-nd, the form OpenAlex uses


def _str(raw: Any) -> str | None:
    return raw.strip() or None if isinstance(raw, str) else None


def parse_details(rows: list[Any], server: str) -> PaperRecord | None:
    versions = [r for r in rows if isinstance(r, dict) and ids.doi(str(r.get("doi") or ""))]
    if not versions:
        return None
    latest, first = max(versions, key=_version), min(versions, key=_version)
    doi = ids.doi(str(latest["doi"]))
    title = _str(latest.get("title"))
    if doi is None or title is None:
        return None
    year = str(first.get("date") or "")[:4]
    authors = str(latest.get("authors") or "").split(";")
    return PaperRecord(
        source="biorxiv",
        ids=[ExternalId("doi", doi)],
        title=title,
        abstract=_str(latest.get("abstract")),
        year=int(year) if year.isdigit() else None,
        authors=[Author(name=n.strip()) for n in authors if n.strip()],
        venue=_VENUES[server],
        venue_type="repository",
        is_oa=True,
        license=_license(latest.get("license")),
        extra={
            "server": server,
            "version": _version(latest),
            "category": _str(latest.get("category")),
            "jatsxml": _str(latest.get("jatsxml")),
            "published_doi": ids.doi(str(latest.get("published") or "")),  # "NA" until published
        },
    )


class BiorxivConnector:
    name = "biorxiv"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=False, get=True, citations_in=False, citations_out=False)

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        return []

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []

    async def _rows(self, server: str, doi: str) -> list[Any]:
        fetched = await self._client.get(
            f"{BASE}/details/{server}/{quote(doi, safe='/:')}",
            source=self.name,
            cache_key=f"{KEY}:{server}:{doi}",
            ttl=TTL_METADATA,
        )
        if fetched.status == 404:
            return []
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            body = fetched.json()
            status = body["messages"][0]["status"]
            rows = body.get("collection") or []
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc
        if status != "ok" or not isinstance(rows, list):
            return []
        return rows

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        dois = [n.value for e in ids_ if (n := ids.normalize(e)) and n.scheme == "doi" and n.value.startswith(PREFIX)]
        recs: list[PaperRecord] = []
        for doi in dict.fromkeys(dois):
            for server in SERVERS:
                if (rec := parse_details(await self._rows(server, doi), server)) is not None:
                    recs.append(rec)
                    break
        return recs
```

In `backend/src/researchly/connectors/limits.yaml`, add before `default:`:

```yaml
api.biorxiv.org: [1.0, 1]           # no published limit; spec polite 1 rps
```

In `backend/src/researchly/connectors/__init__.py`:
- change the docstring to `"""Connector registry (spec §8.1): the keyless Tier-1 and Tier-2 sources."""`;
- add `from researchly.connectors.biorxiv import BiorxivConnector`;
- append `BiorxivConnector(client),` to the list.

- [ ] **Step 4: Record the cassettes and run the tests**

Run: `uv run --directory backend pytest tests/connectors/test_biorxiv.py --record-mode=once -q`, then grep the new cassettes:
`grep -rniE "mailto|cookie|authorization|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}" backend/tests/connectors/cassettes/test_biorxiv`
Expected: no match except `redacted@example.org`, and no 429 or 5xx status in the cassettes. Then run in replay: `uv run --directory backend pytest -q tests/connectors`
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/connectors backend/tests/connectors
git commit -m "feat: bioRxiv/medRxiv connector for preprint metadata and published DOIs" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: OpenReview decision lookup

**Files:**
- Create: `backend/src/researchly/connectors/openreview.py`
- Modify: `backend/src/researchly/connectors/limits.yaml`, `backend/src/researchly/connectors/__init__.py`
- Test: `backend/tests/connectors/test_openreview.py` (+ cassettes), `backend/tests/connectors/test_registry.py`, `backend/tests/connectors/test_live.py`

**Interfaces:**
- `OpenReviewConnector(client: PoliteClient)`, `name = "openreview"`. Every capability is `False`, so `search`, `get` and `citations` return `[]` and it never joins a fan-out (ruling 11).
- `async OpenReviewConnector.by_title(title: str, year: int | None = None) -> PaperRecord | None`:
  - matches an exact normalized title only, with `fuzzy_title_ok`;
  - when both years are known, the venue year must be between `year - 1` and `year + 2`;
  - a non-200 answer (including the 403 challenge) raises `SourceUnavailable("openreview", "HTTP n")`.
- `decision(venue: str) -> Literal["oral", "spotlight", "poster", "accepted", "not_accepted"]`.
- `parse_note(raw: Any) -> PaperRecord | None`:
  - drops notes from the `DBLP.org` and `OpenReview.net/*` domains, and notes without a title or venue;
  - `venue=None`;
  - `extra = {"openreview_id", "openreview_venue", "openreview_venueid", "openreview_decision"}`;
  - `ids` are read from `content.html` (with the arXiv DOI added).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/connectors/test_openreview.py`:

```python
import httpx
import pytest

from researchly.connectors import openreview as orv
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.http import PoliteClient
from researchly.connectors.openreview import OpenReviewConnector

pytestmark = pytest.mark.anyio


def note(title="Dataflow Is All You Need", venue="MLSys 2026", domain="MLSys/2026/Conference", **content):
    return {
        "id": "tjNf0L8QjR",
        "forum": "tjNf0L8QjR",
        "domain": domain,
        "pdate": 1767225600000,  # 2026-01-01
        "license": "CC BY 4.0",
        "content": {
            "title": {"value": title},
            "venue": {"value": venue},
            "venueid": {"value": domain},
            "authors": {"value": ["Ada Lovelace", "Alan Turing"]},
            "authorids": {"value": ["ada@example.org", "~Alan_Turing1"]},
            "abstract": {"value": " An abstract. "},
        }
        | content,
    }


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


@pytest.mark.parametrize(
    ("venue", "expected"),
    [
        ("ICLR 2025 Oral", "oral"),
        ("NeurIPS 2024 spotlight", "spotlight"),
        ("ICLR 2025 Poster", "poster"),
        ("MLSys 2026", "accepted"),
        ("Submitted to ICLR 2025", "not_accepted"),
        ("ICLR 2024 Conference Withdrawn Submission", "not_accepted"),
        ("Rejected by TMLR", "not_accepted"),
        ("Postering 2025", "accepted"),  # whole words only
    ],
)
def test_decision(venue, expected):
    assert orv.decision(venue) == expected


def test_parse_note():
    rec = orv.parse_note(note(html={"value": "https://doi.org/10.48550/arXiv.2407.04708"}))
    assert rec is not None
    assert rec.source == "openreview" and rec.title == "Dataflow Is All You Need" and rec.year == 2026
    assert rec.ids == [ExternalId("doi", "10.48550/arxiv.2407.04708")]
    assert [a.name for a in rec.authors] == ["Ada Lovelace", "Alan Turing"]
    assert rec.abstract == "An abstract." and rec.venue is None and rec.license == "CC BY 4.0"
    assert rec.extra == {
        "openreview_id": "tjNf0L8QjR",
        "openreview_venue": "MLSys 2026",
        "openreview_venueid": "MLSys/2026/Conference",
        "openreview_decision": "accepted",
    }
    assert "ada@example.org" not in repr(rec)  # authorids are never read


def test_parse_note_arxiv_link_and_fallbacks():
    rec = orv.parse_note(note(venue="Some Workshop", html={"value": "https://arxiv.org/abs/2407.04708v2"}))
    assert rec is not None
    assert rec.ids == [ExternalId("arxiv", "2407.04708"), ExternalId("doi", "10.48550/arxiv.2407.04708")]
    assert rec.year == 2026  # no year in the venue: pdate
    assert orv.parse_note(note(domain="DBLP.org")) is None  # an import, not a venue decision
    assert orv.parse_note(note(domain="OpenReview.net/Archive")) is None
    assert orv.parse_note(note(venue="")) is None
    assert orv.parse_note("junk") is None


async def test_by_title_matches_exactly_and_checks_the_year():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/notes/search"
        assert dict(request.url.params) == {
            "term": "Dataflow is all you need",
            "content": "title",
            "source": "forum",
            "limit": "5",
        }
        notes = [note(title="Dataflow Is All You Need (Extended)"), note(domain="DBLP.org"), note()]
        return httpx.Response(200, json={"notes": notes, "count": 3})

    async with client_for(handler) as client:
        conn = OpenReviewConnector(client)
        rec = await conn.by_title("  Dataflow is all you need ", 2025)
        assert rec is not None and rec.extra["openreview_decision"] == "accepted"
        assert await conn.by_title("Dataflow is all you need", 2020) is None  # 2026 is too far from 2020


async def test_weak_titles_make_no_request_and_the_fan_out_gets_nothing():
    seen = []
    async with client_for(lambda r: seen.append(r) or httpx.Response(500)) as client:
        conn = OpenReviewConnector(client)
        assert await conn.by_title("Transformers") is None
        assert await conn.search(SearchQuery("attention")) == []
        assert await conn.get([ExternalId("arxiv", "1706.03762")]) == []
        assert not any(vars(conn.capabilities()).values())
    assert seen == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(403, json={"name": "ChallengeRequiredError", "status": 403}), "HTTP 403"),
        (httpx.Response(200, json={"count": 0}), "unreadable response"),
        (httpx.Response(200, text="<html>"), "unreadable response"),
    ],
)
async def test_errors(response, message):
    async with client_for(lambda r: response) as client:
        with pytest.raises(SourceUnavailable, match=message):
            await OpenReviewConnector(client).by_title("Dataflow Is All You Need")


@pytest.mark.vcr
async def test_by_title_finds_a_v2_venue_decision(polite):
    rec = await OpenReviewConnector(polite).by_title("Dataflow Is All You Need")
    assert rec is not None and rec.extra["openreview_venue"].startswith("MLSys")
    assert rec.extra["openreview_decision"] == "accepted" and rec.year == 2026


@pytest.mark.vcr
async def test_by_title_ignores_imports_and_near_titles(polite):
    assert await OpenReviewConnector(polite).by_title("Attention Is All You Need", 2017) is None
```

`ConnectorCaps` is a frozen dataclass, so `vars(...)` lists its four flags.

In `test_registry.py`, add `"openreview"` to `NAMES`. In `test_live.py`, add:

```python
async def test_openreview_decision_lookup(connectors):
    rec = await connectors["openreview"].by_title("Dataflow Is All You Need")
    assert rec is not None and isinstance(rec.extra["openreview_decision"], str)  # the cassette paper: a miss is breakage
```

Pyright sees `connectors["openreview"]` as a `Connector`, which has no `by_title`. Put `# pyright: ignore[reportAttributeAccessIssue]` on that line.

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest -q tests/connectors/test_openreview.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'researchly.connectors.openreview'`.

- [ ] **Step 3: Implement the connector**

Create `backend/src/researchly/connectors/openreview.py`:

```python
"""OpenReview connector (R-conn §1): venue decisions for ML papers through /notes/search. Keyless.

Only search answers anonymously; note and forum lookups (`/notes`) return a 403 challenge, so reviews are out
of reach and never attempted. `content.authorids` holds raw author emails: it is never read.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

from researchly.connectors import ids
from researchly.connectors.base import (
    Author,
    ConnectorCaps,
    ExternalId,
    PaperRecord,
    SearchQuery,
    SourceUnavailable,
)
from researchly.connectors.http import TTL_COUNTS, PoliteClient

BASE = "https://api2.openreview.net"
KEY = "openreview:v1"
Decision = Literal["oral", "spotlight", "poster", "accepted", "not_accepted"]
_TIER: dict[str, Decision] = {"oral": "oral", "spotlight": "spotlight", "poster": "poster"}
_TIER_WORD = re.compile(r"\b(oral|spotlight|poster)\b", re.I)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_MAX_MS = 4_102_444_800_000  # 2100-01-01: anything later is junk


def _hash_key(prefix: str, params: dict[str, str | int]) -> str:
    return f"{KEY}:{prefix}:" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def decision(venue: str) -> Decision:
    low = venue.lower()
    if low.startswith("submitted to") or "withdrawn" in low or "reject" in low:
        return "not_accepted"
    if match := _TIER_WORD.search(venue):
        return _TIER[match.group(1).lower()]
    return "accepted"


def _value(content: dict[str, Any], key: str) -> Any:
    entry = content.get(key)
    return entry.get("value") if isinstance(entry, dict) else None


def parse_note(raw: Any) -> PaperRecord | None:
    if not isinstance(raw, dict) or not isinstance(content := raw.get("content"), dict):
        return None
    domain = raw.get("domain")
    if not isinstance(domain, str) or domain == "DBLP.org" or domain.startswith("OpenReview.net/"):
        return None  # imports and archive copies carry no venue decision
    title, venue = _value(content, "title"), _value(content, "venue")
    if not isinstance(title, str) or not title.strip() or not isinstance(venue, str) or not venue.strip():
        return None
    year = None
    if match := _YEAR.search(venue):
        year = int(match.group(0))
    elif isinstance(pdate := raw.get("pdate"), int) and 0 < pdate < _MAX_MS:
        year = datetime.fromtimestamp(pdate / 1000, UTC).year
    found: list[ExternalId] = []
    if isinstance(link := _value(content, "html"), str) and (eid := ids.from_url(link)) is not None:
        found.append(eid)
        if eid.scheme == "arxiv":
            found.append(ExternalId("doi", ids.arxiv_doi(eid.value)))
    authors, abstract, venueid = _value(content, "authors"), _value(content, "abstract"), _value(content, "venueid")
    license_ = raw.get("license")
    return PaperRecord(
        source="openreview",
        ids=found,
        title=" ".join(title.split()),
        abstract=abstract.strip() or None if isinstance(abstract, str) else None,
        year=year,
        authors=[Author(name=a.strip()) for a in authors if isinstance(a, str) and a.strip()]
        if isinstance(authors, list)
        else [],
        license=license_ if isinstance(license_, str) else None,
        extra={
            "openreview_id": raw.get("forum") or raw.get("id"),
            "openreview_venue": venue.strip(),
            "openreview_venueid": venueid if isinstance(venueid, str) else None,
            "openreview_decision": decision(venue),
        },
    )


class OpenReviewConnector:
    name = "openreview"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=False, get=False, citations_in=False, citations_out=False)

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        return []  # term search is loose (R-conn §1): decisions come from by_title only

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        return []  # /notes?id= answers a 403 challenge

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []

    async def by_title(self, title: str, year: int | None = None) -> PaperRecord | None:
        from researchly.services.identity import fuzzy_title_ok, normalize_title  # lazy: identity imports connectors

        norm = normalize_title(title)
        if not fuzzy_title_ok(norm):
            return None
        params: dict[str, str | int] = {"term": " ".join(title.split()), "content": "title", "source": "forum", "limit": 5}
        fetched = await self._client.get(
            f"{BASE}/notes/search",
            source=self.name,
            params=params,
            cache_key=_hash_key("title", params),
            ttl=TTL_COUNTS,  # decisions land after submission: re-check weekly
        )
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            notes = fetched.json()["notes"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc
        if not isinstance(notes, list):
            raise SourceUnavailable(self.name, "unreadable response")
        for raw in notes:
            rec = parse_note(raw)
            if rec is None or normalize_title(rec.title) != norm:
                continue
            if year is not None and rec.year is not None and not year - 1 <= rec.year <= year + 2:
                continue  # a venue trails its preprint by up to two years
            return rec
        return None
```

Add to `limits.yaml` before `default:`:

```yaml
api2.openreview.net: [3.5, 1]       # /notes/search 20 per 60 s (ratelimit-policy: 20;w=60)
```

In `connectors/__init__.py`, import `OpenReviewConnector` and append `OpenReviewConnector(client),`.

- [ ] **Step 4: Record the cassettes and run the tests**

Run: `uv run --directory backend pytest tests/connectors/test_openreview.py --record-mode=once -q`. Then grep the cassettes as in Task 6. `authorids` emails must show only as `redacted@example.org`, because the conftest hook redacts every email in response bodies. Check that no `set-cookie` header and no 403 response were recorded.
- If OpenReview answers the search with a 403 challenge, stop. Do not retry with other headers, and do not commit a cassette. Report it.
- If the live answer no longer holds a "Dataflow Is All You Need" MLSys note, pick another exact v2 title from the recorded response, and update the test's title and venue asserts to match it.

Then run in replay: `uv run --directory backend pytest -q tests/connectors`
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/connectors backend/tests/connectors
git commit -m "feat: OpenReview venue decisions by title through the keyless search API" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: DBLP venue enrichment over SPARQL

**Files:**
- Create: `backend/src/researchly/connectors/dblp.py`
- Modify: `backend/src/researchly/connectors/limits.yaml`, `backend/src/researchly/connectors/__init__.py`
- Test: `backend/tests/connectors/test_dblp.py` (+ cassettes), `backend/tests/connectors/test_registry.py`, `backend/tests/connectors/test_live.py`

**Interfaces:**
- `DblpConnector(client: PoliteClient)`, `name = "dblp"`, capabilities `search=False, get=True, citations_in=False, citations_out=False`.
- `get(ids)` takes DOIs and arXiv IDs (as the arXiv DOI). It finds the DBLP records that share an author and the title with the DOI's record, and returns the best one: Inproceedings, then Article, then Informal.
- Record fields:
  - `ids=[the input DOI, ExternalId("dblp", "<key>")]`;
  - `title` without DBLP's trailing period; `year`;
  - `venue=dblp:publishedIn`; `venue_type` conference, journal or repository;
  - `extra = {"dblp_venue", "dblp_type", "dblp_doi"}` (ruling 12).
- `parse_bindings(bindings: list[Any], doi: str) -> PaperRecord | None`.
- Errors:
  - a non-200 answer (an Anubis 202 page included) raises `SourceUnavailable("dblp", "HTTP n")`;
  - a 200 that is not SPARQL JSON raises `UnwantedContent`, through the `accept` gate;
  - a bad body raises `SourceUnavailable("dblp", "unreadable response")`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/connectors/test_dblp.py`:

```python
import httpx
import pytest

from researchly.connectors import dblp
from researchly.connectors.base import ExternalId, SearchQuery, SourceUnavailable, UnwantedContent
from researchly.connectors.dblp import DblpConnector
from researchly.connectors.http import PoliteClient

pytestmark = pytest.mark.anyio

ARXIV_DOI = "10.48550/arxiv.1706.03762"
S = "https://dblp.org/rdf/schema#"


def binding(key, kind, venue, year="2017", doi=None, title="Attention is All you Need."):
    b = {
        "p": {"type": "uri", "value": f"https://dblp.org/rec/{key}"},
        "t": {"type": "literal", "value": title},
        "venue": {"type": "literal", "value": venue},
        "year": {"type": "literal", "value": year},
        "type": {"type": "uri", "value": S + kind},
    }
    if doi:
        b["doi"] = {"type": "uri", "value": f"https://doi.org/{doi}"}
    return b


BINDINGS = [
    binding("journals/corr/VaswaniSPUJGKP17", "Informal", "CoRR", doi="10.48550/ARXIV.1706.03762"),
    binding("conf/nips/VaswaniSPUJGKP17", "Publication", "NIPS"),  # the super-type: ignored
    binding("conf/nips/VaswaniSPUJGKP17", "Inproceedings", "NIPS"),
]


def sparql(bindings):
    return httpx.Response(
        200,
        json={"head": {"vars": ["p", "t", "venue", "year", "type", "doi"]}, "results": {"bindings": bindings}},
        headers={"content-type": "application/sparql-results+json"},
    )


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def test_parse_bindings_prefers_the_venue_record():
    rec = dblp.parse_bindings(BINDINGS, ARXIV_DOI)
    assert rec is not None
    assert rec.source == "dblp"
    assert rec.ids == [ExternalId("doi", ARXIV_DOI), ExternalId("dblp", "conf/nips/VaswaniSPUJGKP17")]
    assert (rec.title, rec.year, rec.venue, rec.venue_type) == ("Attention is All you Need", 2017, "NIPS", "conference")
    assert rec.extra == {"dblp_venue": "NIPS", "dblp_type": "Inproceedings", "dblp_doi": None}


def test_parse_bindings_falls_back_to_the_preprint_record():
    rec = dblp.parse_bindings(BINDINGS[:1], ARXIV_DOI)
    assert rec is not None and rec.venue_type == "repository" and rec.venue == "CoRR"
    assert rec.extra["dblp_doi"] == ARXIV_DOI  # lower-cased; extra only (ruling 12)
    assert dblp.parse_bindings([], ARXIV_DOI) is None
    assert dblp.parse_bindings([binding("x/y", "Publication", "V")], ARXIV_DOI) is None


async def test_get_asks_for_both_doi_cases_and_maps_arxiv_ids():
    queries = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "sparql.dblp.org" and request.url.path == "/sparql"
        queries.append(request.url.params["query"])
        return sparql(BINDINGS)

    async with client_for(handler) as client:
        [rec] = await DblpConnector(client).get([ExternalId("arxiv", "1706.03762v7")])
    assert rec.id("dblp") == "conf/nips/VaswaniSPUJGKP17"
    assert "<https://doi.org/10.48550/ARXIV.1706.03762>" in queries[0]
    assert "<https://doi.org/10.48550/arxiv.1706.03762>" in queries[0]


async def test_unsafe_dois_and_other_schemes_make_no_request():
    seen = []
    async with client_for(lambda r: seen.append(r) or sparql([])) as client:
        conn = DblpConnector(client)
        assert await conn.get([ExternalId("doi", "10.1000/a>b"), ExternalId("pmid", "26017442")]) == []
        assert await conn.search(SearchQuery("attention")) == []
    assert seen == []


async def test_not_found_is_empty():
    async with client_for(lambda r: sparql([])) as client:
        assert await DblpConnector(client).get([ExternalId("doi", "10.1234/none")]) == []


async def test_the_anubis_page_is_unavailable_never_parsed():
    page = httpx.Response(202, text="<html>Making sure you're not a bot!</html>", headers={"content-type": "text/html"})
    async with client_for(lambda r: page) as client:
        with pytest.raises(SourceUnavailable, match="HTTP 202"):
            await DblpConnector(client).get([ExternalId("doi", ARXIV_DOI)])


async def test_bad_answers():
    html = httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
    async with client_for(lambda r: html) as client:
        with pytest.raises(UnwantedContent):
            await DblpConnector(client).get([ExternalId("doi", ARXIV_DOI)])
    shapeless = httpx.Response(200, json={"results": []}, headers={"content-type": "application/sparql-results+json"})
    async with client_for(lambda r: shapeless) as client:
        with pytest.raises(SourceUnavailable, match="unreadable response"):
            await DblpConnector(client).get([ExternalId("doi", ARXIV_DOI)])


@pytest.mark.vcr
async def test_get_an_arxiv_paper_finds_its_conference(polite):
    [rec] = await DblpConnector(polite).get([ExternalId("arxiv", "1706.03762")])
    assert rec.id("dblp") == "conf/nips/VaswaniSPUJGKP17"
    assert (rec.venue, rec.year, rec.venue_type) == ("NIPS", 2017, "conference")


@pytest.mark.vcr
async def test_get_a_publisher_doi(polite):
    [rec] = await DblpConnector(polite).get([ExternalId("doi", "10.1109/CVPR.2016.90")])
    assert rec.venue == "CVPR" and rec.venue_type == "conference" and rec.year == 2016


@pytest.mark.vcr
async def test_get_not_found(polite):
    assert await DblpConnector(polite).get([ExternalId("doi", "10.1234/does-not-exist-xyz")]) == []
```

In `test_registry.py`, add `"dblp"` to `NAMES`. In `test_live.py`, add `"dblp": ExternalId("arxiv", "1706.03762"),` to `GET_PROBES`.

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest -q tests/connectors/test_dblp.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'researchly.connectors.dblp'`.

- [ ] **Step 3: Implement the connector**

Create `backend/src/researchly/connectors/dblp.py`:

```python
"""DBLP connector (R-conn §5): CS venue and year for a known DOI, over the QLever SPARQL endpoint. Keyless, CC0.

The dblp.org search API sits behind an Anubis bot challenge and is never called. There is no title lookup: a
case-insensitive title scan took 8.3 s (ruling 12). DBLP keeps an arXiv paper's CoRR record and its conference
record apart, so the query finds the records that share an author and the title with the DOI's record.
"""

import re
from typing import Any, Literal

from researchly.connectors import ids
from researchly.connectors.base import ConnectorCaps, ExternalId, PaperRecord, SearchQuery, SourceUnavailable
from researchly.connectors.http import TTL_METADATA, PoliteClient

BASE = "https://sparql.dblp.org/sparql"
KEY = "dblp:v1"
ACCEPT = frozenset({"application/sparql-results+json", "application/json"})
_SCHEMA = "https://dblp.org/rdf/schema#"
_REC = "https://dblp.org/rec/"
_RANK = {"Inproceedings": 0, "Article": 1, "Informal": 2}
_VENUE_TYPE = {"Inproceedings": "conference", "Article": "journal", "Informal": "repository"}
_UNSAFE_IRI = re.compile(r'[<>"{}|^`\\\s]')  # would break out of the IRI in the query
# DOIs are stored as upper-case IRIs (https://doi.org/10.48550/ARXIV.1706.03762); a mixed-case one is missed
_QUERY = """PREFIX dblp: <https://dblp.org/rdf/schema#>
SELECT DISTINCT ?p ?t ?venue ?year ?type ?doi WHERE {{
  VALUES ?d {{ <https://doi.org/{upper}> <https://doi.org/{lower}> }}
  ?corr dblp:doi ?d ; dblp:title ?ct ; dblp:authoredBy ?a .
  ?p dblp:authoredBy ?a ; dblp:title ?t ; dblp:publishedIn ?venue ; dblp:yearOfPublication ?year ; a ?type .
  OPTIONAL {{ ?p dblp:doi ?doi }}
  FILTER(LCASE(STR(?t)) = LCASE(STR(?ct)))
}}
ORDER BY ?p
LIMIT 50"""


def parse_bindings(bindings: list[Any], doi: str) -> PaperRecord | None:
    best: tuple[int, dict[str, str]] | None = None
    for b in bindings:
        if not isinstance(b, dict):
            continue
        row = {k: v["value"] for k, v in b.items() if isinstance(v, dict) and isinstance(v.get("value"), str)}
        kind = row.get("type", "").removeprefix(_SCHEMA)
        if kind not in _RANK or not row.get("p", "").startswith(_REC) or not row.get("t", "").strip():
            continue  # dblp:Publication is the super-type every record also has
        if best is None or _RANK[kind] < best[0]:
            best = (_RANK[kind], row)
    if best is None:
        return None
    row = best[1]
    kind = row["type"].removeprefix(_SCHEMA)
    year = row.get("year", "")[:4]
    return PaperRecord(
        source="dblp",
        ids=[ExternalId("doi", doi), ExternalId("dblp", row["p"].removeprefix(_REC))],
        title=" ".join(row["t"].split()).removesuffix("."),
        year=int(year) if year.isdigit() else None,
        venue=row.get("venue"),
        venue_type=_VENUE_TYPE[kind],
        extra={
            "dblp_venue": row.get("venue"),
            "dblp_type": kind,
            "dblp_doi": ids.doi(row.get("doi", "")),  # this record's own DOI: another work's, maybe
        },
    )


class DblpConnector:
    name = "dblp"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=False, get=True, citations_in=False, citations_out=False)

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        return []  # the search API is behind Anubis; never bypassed

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        dois: list[str] = []
        for e in ids_:
            n = ids.normalize(e)
            if n and n.scheme == "arxiv":
                dois.append(ids.arxiv_doi(n.value))
            elif n and n.scheme == "doi":
                dois.append(n.value)
        recs: list[PaperRecord] = []
        for doi in dict.fromkeys(dois):
            if _UNSAFE_IRI.search(doi):
                continue
            fetched = await self._client.get(
                BASE,
                source=self.name,
                params={"query": _QUERY.format(upper=doi.upper(), lower=doi)},
                cache_key=f"{KEY}:doi:{doi}",
                ttl=TTL_METADATA,
                accept=ACCEPT,
            )
            if fetched.status != 200:  # Anubis answers 202 with an HTML page
                raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
            try:
                bindings = fetched.json()["results"]["bindings"]
            except (ValueError, KeyError, TypeError) as exc:
                raise SourceUnavailable(self.name, "unreadable response") from exc
            if not isinstance(bindings, list):
                raise SourceUnavailable(self.name, "unreadable response")
            if (rec := parse_bindings(bindings, doi)) is not None:
                recs.append(rec)
        return recs
```

Add to `limits.yaml` before `default:`:

```yaml
sparql.dblp.org: [2.0, 1]           # no published limit; QLever, keep it gentle
```

Do not add `dblp.org`: its search API is Anubis-gated and never called. In `connectors/__init__.py`, import `DblpConnector` and append `DblpConnector(client),`.

- [ ] **Step 4: Record the cassettes and run the tests**

Run: `uv run --directory backend pytest tests/connectors/test_dblp.py --record-mode=once -q`, then grep the cassettes as in Task 6. If `10.1109/CVPR.2016.90` returns a different venue string, such as `CVPR` with a suffix, update only that assert to the recorded value. Then run `uv run --directory backend pytest -q tests/connectors`.
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/connectors backend/tests/connectors
git commit -m "feat: DBLP venue enrichment by DOI over the SPARQL endpoint" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: OpenAIRE connector

**Files:**
- Create: `backend/src/researchly/connectors/openaire.py`
- Modify: `backend/src/researchly/connectors/limits.yaml`, `backend/src/researchly/connectors/__init__.py`
- Test: `backend/tests/connectors/test_openaire.py` (+ cassettes), `backend/tests/connectors/test_registry.py`, `backend/tests/connectors/test_live.py`

**Interfaces:**
- `OpenAireConnector(client: PoliteClient)`, `name = "openaire"`, capabilities `search=True, get=True, citations_in=False, citations_out=False`.
- `search(query)`: `GET /researchProducts?search=&type=publication&pageSize=1..100[&fromPublicationDate=&toPublicationDate=]`, cached `TTL_SEARCH`. The year filter applies to OpenAIRE's merged top-level date (ruling 13).
- `get(ids)`: `GET /researchProducts?pid=<doi>&pageSize=5` per DOI or arXiv DOI, cached `TTL_METADATA`. A DOI containing a comma is skipped.
- `parse_result(raw: Any, want: set[ExternalId] | None = None) -> PaperRecord | None` (ruling 13):
  - the IDs, year, licence and OA come from the instance that holds a wanted ID; with no `want`, from the earliest-dated instance that has IDs;
  - a deduplicated record (`dedup` in its `id`) with no such instance is dropped;
  - the title, abstract, authors and `cited_by` come from the top level;
  - `venue` (from `container.name`) is used only when the record is not merged;
  - `extra = {"openaire_id"}`.
- Every answer's `x-ratelimit-used` header is logged at INFO, so the real keyless limit can be checked later.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/connectors/test_openaire.py`:

```python
import httpx
import pytest

from researchly.connectors import openaire as oa
from researchly.connectors.base import Author, ExternalId, SearchQuery, SourceUnavailable
from researchly.connectors.http import PoliteClient
from researchly.connectors.openaire import OpenAireConnector

pytestmark = pytest.mark.anyio

ARXIV_DOI = ExternalId("doi", "10.48550/arxiv.1706.03762")
ARXIV_LICENCE = "http://arxiv.org/licenses/nonexclusive-distrib/1.0/"

MERGED = {
    "id": "doi_dedup___::5e53125f15c47535bb231d51dc2a2cce",
    "mainTitle": "Attention Is All You Need",
    "descriptions": ["<jats:p>The dominant sequence transduction models</jats:p>"],
    "authors": [
        {"fullName": "Noam Shazeer", "rank": 2, "pid": None},
        {"fullName": "Ashish Vaswani", "rank": 1, "pid": {"id": {"scheme": "orcid", "value": "0000-0002-1825-009X"}}},
    ],
    "publicationDate": "2025-08-23",  # a spam repost's date: never used for a merged record
    "publisher": "Shenzhen Medical Academy of Research and Translation",
    "container": {"name": "Spam Journal"},
    "pids": [{"scheme": "doi", "value": "10.48550/arxiv.1706.03762"}, {"scheme": "doi", "value": "10.65215/spam.1"}],
    "indicators": {"citationImpact": {"citationCount": 16770}},
    "instances": [
        {
            "pids": [{"scheme": "doi", "value": "10.65215/spam.1"}],
            "publicationDate": "2025-08-23",
            "license": "CC BY",
            "accessRight": {"label": "OPEN"},
            "urls": ["https://spam.example/1"],
        },
        {
            "pids": [{"scheme": "doi", "value": "10.48550/arxiv.1706.03762"}],
            "alternateIdentifiers": [{"scheme": "arXiv", "value": "1706.03762"}],
            "publicationDate": "2017-01-01",
            "license": ARXIV_LICENCE,
            "accessRight": None,
            "urls": ["https://arxiv.org/abs/1706.03762"],
        },
    ],
}

PLAIN = {
    "id": "od______::abc",
    "mainTitle": "A Journal Paper",
    "publicationDate": "2021-05-01",
    "container": {"name": "Neurocomputing"},
    "instances": [
        {
            "pids": [{"scheme": "doi", "value": "10.1016/j.neucom.2021.01.001"}],
            "publicationDate": "2021-05-01",
            "accessRight": {"label": "OPEN"},
            "license": "CC BY",
            "urls": ["ftp://bad.example/x", "https://repo.example/paper.pdf"],
        }
    ],
}


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), limits={"default": (0.0, 1)})


def results(*rows):
    return httpx.Response(200, json={"header": {"numFound": len(rows)}, "results": list(rows)})


def test_a_merged_record_takes_everything_bibliographic_from_the_matching_instance():
    rec = oa.parse_result(MERGED, {ARXIV_DOI})
    assert rec is not None
    assert rec.ids == [ExternalId("arxiv", "1706.03762"), ARXIV_DOI]  # sorted; no spam DOI
    assert rec.year == 2017 and rec.license == ARXIV_LICENCE
    assert rec.is_oa is None and rec.oa_url is None  # the arXiv instance states no access right
    assert rec.venue is None  # a merged record's container may be a repost's
    assert rec.title == "Attention Is All You Need"
    assert rec.abstract == "The dominant sequence transduction models"
    assert rec.authors == [Author("Ashish Vaswani", orcid="0000-0002-1825-009X"), Author("Noam Shazeer")]
    assert rec.cited_by == 16770 and rec.extra == {"openaire_id": MERGED["id"]}


def test_a_merged_record_without_the_wanted_instance_is_dropped():
    assert oa.parse_result(MERGED, {ExternalId("doi", "10.1234/other")}) is None


def test_without_a_wanted_id_the_earliest_instance_wins():
    rec = oa.parse_result(MERGED)
    assert rec is not None and rec.year == 2017


def test_a_plain_record():
    rec = oa.parse_result(PLAIN)
    assert rec is not None
    assert rec.ids == [ExternalId("doi", "10.1016/j.neucom.2021.01.001")] and rec.year == 2021
    assert rec.venue == "Neurocomputing" and rec.is_oa is True and rec.oa_url == "https://repo.example/paper.pdf"
    assert oa.parse_result({"id": "x", "mainTitle": ""}) is None
    assert oa.parse_result("junk") is None


async def test_get_asks_by_pid_and_skips_comma_dois(caplog):
    caplog.set_level("INFO")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        response = results(MERGED)
        response.headers["x-ratelimit-used"] = "3"
        return response

    async with client_for(handler) as client:
        recs = await OpenAireConnector(client).get(
            [ExternalId("arxiv", "1706.03762"), ExternalId("doi", "10.1000/a,b")]
        )
    assert seen == [{"pid": "10.48550/arxiv.1706.03762", "pageSize": "5"}]
    assert [r.year for r in recs] == [2017]
    assert "x-ratelimit-used=3" in caplog.text


async def test_search_params():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/graph/v1/researchProducts"
        assert dict(request.url.params) == {
            "search": "attention",
            "type": "publication",
            "pageSize": "100",
            "fromPublicationDate": "2017",
            "toPublicationDate": "2020",
        }
        return results(PLAIN, {"mainTitle": "no instances, unmerged", "id": "x"})

    async with client_for(handler) as client:
        recs = await OpenAireConnector(client).search(SearchQuery("attention", limit=500, year_from=2017, year_to=2020))
    assert [r.title for r in recs] == ["A Journal Paper"]  # a record without any ID is dropped


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(400, json={"message": "Unknown parameter"}), "HTTP 400"),
        (httpx.Response(200, text="<html>"), "unreadable response"),
        (httpx.Response(200, json={"results": {"not": "a list"}}), "unreadable response"),
    ],
)
async def test_errors(response, message):
    async with client_for(lambda r: response) as client:
        with pytest.raises(SourceUnavailable, match=message):
            await OpenAireConnector(client).search(SearchQuery("x"))


@pytest.mark.vcr
async def test_get_resolves_the_merged_arxiv_record(polite):
    [rec] = await OpenAireConnector(polite).get([ExternalId("doi", "10.48550/arXiv.1706.03762")])
    assert "Attention" in rec.title and rec.year == 2017
    assert ARXIV_DOI in rec.ids and not any(i.value.startswith("10.65215/") for i in rec.ids)
    assert rec.license == ARXIV_LICENCE


@pytest.mark.vcr
async def test_search_finds_the_transformer_paper(polite):
    recs = await OpenAireConnector(polite).search(SearchQuery("attention is all you need", limit=3))
    assert recs and all(r.title and r.ids for r in recs)


@pytest.mark.vcr
async def test_get_not_found(polite):
    assert await OpenAireConnector(polite).get([ExternalId("doi", "10.1234/does-not-exist-xyz")]) == []
```

In `test_registry.py`, add `"openaire"` to both `NAMES` and `SEARCH`. In `test_live.py`, add `"openaire": ExternalId("doi", "10.48550/arxiv.1706.03762"),` to `GET_PROBES`.

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest -q tests/connectors/test_openaire.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'researchly.connectors.openaire'`.

- [ ] **Step 3: Implement the connector**

Create `backend/src/researchly/connectors/openaire.py`:

```python
"""OpenAIRE Graph v1 connector (R-conn §4): secondary search and PID lookup for EU repositories. Keyless.

60 calls an hour anonymously (docs; the header claims 7,199). Deduplicated records merge unrelated reposts, so
every bibliographic field comes from the one instance that holds the paper's ID (ruling 13).
"""

import hashlib
import html
import json
import logging
import re
from typing import Any, Literal

from researchly.connectors import ids
from researchly.connectors.base import (
    Author,
    ConnectorCaps,
    ExternalId,
    PaperRecord,
    SearchQuery,
    SourceUnavailable,
)
from researchly.connectors.http import TTL_METADATA, TTL_SEARCH, Fetched, PoliteClient

BASE = "https://api.openaire.eu/graph/v1"
KEY = "openaire:v1"
_TAGS = re.compile(r"<[^>]+>")
_ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")
logger = logging.getLogger(__name__)


def _hash_key(prefix: str, params: dict[str, str | int]) -> str:
    return f"{KEY}:{prefix}:" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def _text(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    return " ".join(html.unescape(_TAGS.sub(" ", raw)).split()) or None


def _pids(raw: Any) -> list[ExternalId]:
    out: list[ExternalId] = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict) or not isinstance(value := p.get("value"), str):
            continue
        scheme = str(p.get("scheme") or "").lower()
        if scheme == "doi" and (d := ids.doi(value)):
            out.append(ExternalId("doi", d))
        elif scheme == "arxiv" and (a := ids.arxiv(value)):
            out += [ExternalId("arxiv", a), ExternalId("doi", ids.arxiv_doi(a))]
        elif scheme == "pmid" and (m := ids.pmid(value)):
            out.append(ExternalId("pmid", m))
        elif scheme == "pmc" and (c := ids.pmcid(value)):
            out.append(ExternalId("pmcid", c))
    return out


def _instance_ids(instance: dict[str, Any]) -> list[ExternalId]:
    return list(dict.fromkeys(_pids(instance.get("pids")) + _pids(instance.get("alternateIdentifiers"))))


def _author(raw: Any) -> tuple[int, Author] | None:
    if not isinstance(raw, dict) or not (name := _text(raw.get("fullName"))):
        return None
    pid = raw.get("pid")
    ident = pid.get("id") if isinstance(pid, dict) else None
    orcid = None
    if isinstance(ident, dict) and ident.get("scheme") == "orcid" and isinstance(ident.get("value"), str):
        orcid = m.group(0) if (m := _ORCID.search(ident["value"])) else None  # orcid_pending is ignored
    rank = raw.get("rank")
    return (rank if isinstance(rank, int) else 1_000_000, Author(name=name, orcid=orcid))


def parse_result(raw: Any, want: set[ExternalId] | None = None) -> PaperRecord | None:
    if not isinstance(raw, dict) or not (title := _text(raw.get("mainTitle"))):
        return None
    merged = "dedup" in str(raw.get("id") or "")
    instances = [(i, found) for i in raw.get("instances") or [] if isinstance(i, dict) and (found := _instance_ids(i))]
    if want is not None:
        chosen = next(((i, found) for i, found in instances if want & set(found)), None)
    else:
        chosen = min(instances, key=lambda pair: str(pair[0].get("publicationDate") or "9999"), default=None)
    if chosen is None:
        return None  # no instance carries the paper's ID: nothing here can be trusted for it
    instance, found = chosen
    date = str(instance.get("publicationDate") or "")[:4]
    access = instance.get("accessRight")
    label = access.get("label") if isinstance(access, dict) else None
    is_oa = label == "OPEN" if isinstance(label, str) else None
    urls = [u for u in instance.get("urls") or [] if isinstance(u, str) and u.startswith(("https://", "http://"))]
    container = raw.get("container")
    descriptions = raw.get("descriptions")
    indicators = raw.get("indicators")
    impact = indicators.get("citationImpact") if isinstance(indicators, dict) else None
    cited = impact.get("citationCount") if isinstance(impact, dict) else None
    licence = instance.get("license")
    authors = sorted((a for a in map(_author, raw.get("authors") or []) if a), key=lambda pair: pair[0])
    return PaperRecord(
        source="openaire",
        ids=sorted(found),
        title=title,
        abstract=_text(descriptions[0]) if isinstance(descriptions, list) and descriptions else None,
        year=int(date) if date.isdigit() else None,
        authors=[a for _, a in authors],
        venue=None if merged or not isinstance(container, dict) else _text(container.get("name")),
        cited_by=cited if isinstance(cited, int) else None,
        oa_url=urls[0] if is_oa and urls else None,
        is_oa=is_oa,
        license=licence if isinstance(licence, str) and licence else None,
        extra={"openaire_id": raw.get("id")},
    )


class OpenAireConnector:
    name = "openaire"

    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def capabilities(self) -> ConnectorCaps:
        return ConnectorCaps(search=True, get=True, citations_in=False, citations_out=False)

    async def citations(self, id: ExternalId, direction: Literal["in", "out"]) -> list[ExternalId]:
        return []

    def _results(self, fetched: Fetched) -> list[Any]:
        if (used := fetched.headers.get("x-ratelimit-used")) is not None:
            logger.info("openaire x-ratelimit-used=%s", used)  # to check the real keyless limit later
        if fetched.status == 404:
            return []
        if fetched.status != 200:
            raise SourceUnavailable(self.name, f"HTTP {fetched.status}")
        try:
            rows = fetched.json()["results"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceUnavailable(self.name, "unreadable response") from exc
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise SourceUnavailable(self.name, "unreadable response")
        return rows

    async def search(self, query: SearchQuery) -> list[PaperRecord]:
        params: dict[str, str | int] = {
            "search": query.text,
            "type": "publication",
            "pageSize": max(1, min(query.limit, 100)),
        }
        if query.year_from:
            params["fromPublicationDate"] = str(query.year_from)
        if query.year_to:
            params["toPublicationDate"] = str(query.year_to)
        fetched = await self._client.get(
            f"{BASE}/researchProducts",
            source=self.name,
            params=params,
            cache_key=_hash_key("search", params),
            ttl=TTL_SEARCH,
        )
        return [r for raw in self._results(fetched) if (r := parse_result(raw))]

    async def get(self, ids_: list[ExternalId]) -> list[PaperRecord]:
        wanted: dict[str, set[ExternalId]] = {}
        for e in ids_:
            n = ids.normalize(e)
            if n and n.scheme == "arxiv":
                wanted.setdefault(ids.arxiv_doi(n.value), set()).update({n, ExternalId("doi", ids.arxiv_doi(n.value))})
            elif n and n.scheme == "doi" and "," not in n.value:  # a comma may be read as a list of PIDs
                wanted.setdefault(n.value, set()).add(n)
        recs: list[PaperRecord] = []
        for doi, want in wanted.items():
            fetched = await self._client.get(
                f"{BASE}/researchProducts",
                source=self.name,
                params={"pid": doi, "pageSize": 5},
                cache_key=f"{KEY}:pid:{doi}",
                ttl=TTL_METADATA,
            )
            if (rec := next((r for raw in self._results(fetched) if (r := parse_result(raw, want))), None)) is not None:
                recs.append(rec)
        return recs
```

Add to `limits.yaml` before `default:`:

```yaml
api.openaire.eu: [60.0, 1]          # anonymous 60 per hour (docs); header claims 7199
```

In `connectors/__init__.py`, import `OpenAireConnector` and append `OpenAireConnector(client),`.

- [ ] **Step 4: Record the cassettes and run the tests**

Each test gets a fresh `polite` client, so pacing does not carry across tests; if OpenAIRE answers 429 while recording, wait a minute and re-run the failing test only:
`uv run --directory backend pytest tests/connectors/test_openaire.py --record-mode=once -q`
Then grep the cassettes as in Task 6. If the merged record's arXiv instance has a different licence string, or the search returns rows without IDs, adjust only those asserts to the recorded values, and keep the "no `10.65215/` IDs" assert. Then run `uv run --directory backend pytest -q tests/connectors`.
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/connectors backend/tests/connectors
git commit -m "feat: OpenAIRE connector with instance-level fields for merged records" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Routing for the Tier-2 sources

**Files:**
- Modify: `backend/src/researchly/services/search.py`, `backend/src/researchly/services/identity.py`
- Test: `backend/tests/services/test_search.py`, `backend/tests/services/test_identity.py`

**Interfaces:**
- `DEEP_EXTRAS = ("openaire",)`; `default_sources(connectors, domains=frozenset(), *, deep: bool = False) -> list[Connector]` appends the extras when `deep`.
- `VENUE_DOMAINS = frozenset({"ml", "cs"})`; `enrichment_sources(record: PaperRecord, domains: frozenset[str] = frozenset()) -> list[str]` returns, in call order:
  - `openalex` and `crossref`;
  - then `biorxiv` for a `10.1101/` DOI;
  - then `dblp` for `ml`/`cs` papers that have a DOI or an arXiv ID and whose `venue_type` is `None` or `"repository"`;
  - then `openreview` for `ml` papers. The caller calls OpenReview through `by_title`, not `get`.
- `SOURCE_PRIORITY` gains the tail `"dblp", "openreview", "biorxiv", "openaire"` (ruling 14).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/services/test_search.py`. It already imports `pytest`, `Any`, `ExternalId` and `PaperRecord`; add `enrichment_sources` to its `researchly.services.search` import.

```python
def test_deep_runs_add_the_low_rate_extras():
    names: dict[str, Any] = {n: object() for n in ("arxiv", "crossref", "openalex", "openaire")}
    tier1 = [names["arxiv"], names["crossref"], names["openalex"]]
    assert default_sources(names) == tier1
    assert default_sources(names, deep=True) == [*tier1, names["openaire"]]


@pytest.mark.parametrize(
    ("record", "domains", "expected"),
    [
        (
            PaperRecord("crossref", [ExternalId("doi", "10.1101/2020.03.22.002386")], "T"),
            frozenset(),
            ["openalex", "crossref", "biorxiv"],
        ),
        (
            PaperRecord("arxiv", [ExternalId("arxiv", "1706.03762")], "T", venue_type="repository"),
            frozenset({"ml"}),
            ["openalex", "crossref", "dblp", "openreview"],
        ),
        (
            PaperRecord("openalex", [ExternalId("doi", "10.1109/cvpr.2016.90")], "T", venue_type="conference"),
            frozenset({"cs"}),
            ["openalex", "crossref"],  # the venue is already known
        ),
        (PaperRecord("openalex", [ExternalId("pmid", "1")], "T"), frozenset({"cs"}), ["openalex", "crossref"]),
    ],
)
def test_enrichment_sources(record, domains, expected):
    assert enrichment_sources(record, domains) == expected
```

Append to `backend/tests/services/test_identity.py`, and add `SOURCE_PRIORITY` and `source_rank` to its `researchly.services.identity` import:

```python
def test_tier2_sources_rank_after_tier1():
    assert SOURCE_PRIORITY[-4:] == ("dblp", "openreview", "biorxiv", "openaire")
    assert source_rank("opencitations") < source_rank("dblp") < source_rank("openaire") < source_rank("unknown")
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run --directory backend pytest -q tests/services/test_search.py tests/services/test_identity.py`
Expected: FAIL (`ImportError: cannot import name 'enrichment_sources'`).

- [ ] **Step 3: Implement**

`backend/src/researchly/services/search.py` already imports `PaperRecord`. Replace `default_sources` with:

```python
DEEP_EXTRAS = ("openaire",)  # 60 calls an hour keyless: Deep runs only (§8.1.1)
VENUE_DOMAINS = frozenset({"ml", "cs"})


def default_sources(
    connectors: Mapping[str, Connector], domains: frozenset[str] = frozenset(), *, deep: bool = False
) -> list[Connector]:
    names = list(ALWAYS) + [n for d in sorted(domains) for n in BY_DOMAIN.get(d, ())]
    names += list(DEEP_EXTRAS) if deep else []
    return [connectors[n] for n in names if n in connectors]


def enrichment_sources(record: PaperRecord, domains: frozenset[str] = frozenset()) -> list[str]:
    """Sources to ask about a found paper, in order (§8.1.1). OpenReview is asked through `by_title`."""
    names = ["openalex", "crossref"]
    doi = record.id("doi")
    if doi and doi.startswith("10.1101/"):
        names.append("biorxiv")
    if domains & VENUE_DOMAINS and (doi or record.id("arxiv")) and record.venue_type in (None, "repository"):
        names.append("dblp")  # a preprint's conference or journal record
    if "ml" in domains:
        names.append("openreview")
    return names
```

In `backend/src/researchly/services/identity.py`, replace the comment line above `SOURCE_PRIORITY` ("spec §8.1.1: OpenAlex enriches first ...") and the `SOURCE_PRIORITY` line with:

```python
# §8.3 order; the Tier-2 tail only fills fields no Tier-1 source has (their own data lives in `extra`)
SOURCE_PRIORITY = (
    "openalex",
    "crossref",
    "pubmed",
    "europepmc",
    "datacite",
    "arxiv",
    "hf",
    "opencitations",
    "dblp",
    "openreview",
    "biorxiv",
    "openaire",
)
```

- [ ] **Step 4: Run the tests**

Run: `uv run --directory backend pytest -q` and `uv run --directory backend pytest -m db -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run --directory backend ruff check && uv run --directory backend ruff format --check && uv run --directory backend pyright
git add backend/src/researchly/services/search.py backend/src/researchly/services/identity.py backend/tests/services/test_search.py backend/tests/services/test_identity.py
git commit -m "feat: route Tier-2 sources as a Deep-run extra and as enrichment" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: ADR 0010, spec updates and progress

**Files:**
- Create: `docs/adr/0010-searxng-engines-and-sidecar-client.md`
- Modify: `docs/adr/0004-native-searxng.md`, `docs/adr/0005-pdf-docling-torch-free.md`, `docs/adr/0008-scholarly-http.md`
- Modify: `docs/superpowers/specs/2026-10-28-researchly-design.md`
- Modify: `AGENTS.md`, `docs/progress.md`

In this task, each "replace A with B" gives A and B as code spans. Spans that contain backticks use double-backtick delimiters, and the text to write is what sits between them.

- [ ] **Step 1: Write ADR 0010**

Create `docs/adr/0010-searxng-engines-and-sidecar-client.md`:

```markdown
# ADR 0010: SearXNG engines, URL plumbing and the sidecar client

- Status: accepted
- Date: 2026-11-06
- Spec sections: F2.1, §4.1, §8.1, §19.3

## Context
Step 1e installs and supervises SearXNG (ADR 0004), but nothing used it, and it ran upstream's default engine set. That set includes scholarly engines (arXiv, Crossref, PubMed, Semantic Scholar) that would spend the same per-IP quotas as our connectors. The app also had no way to find SearXNG, and every HTTP call had to go through `PoliteClient`, whose SSRF guard refuses loopback and private addresses. Research is in `docs/research/2026-11-06-step2c-sidecars.md`.

## Decision
- **Engines:** `use_default_settings.engines.keep_only: [brave, mojeek, yahoo, github]`:
  - general web from Brave, Mojeek and Yahoo; code from GitHub (category `it`);
  - `mojeek` is un-inactivated and `yahoo` enabled;
  - `search.formats: [json]`, so the HTML UI's search answers 403;
  - `outgoing.request_timeout: 5.0`;
  - the scholarly engines stay off: the connectors own those sources and their quotas.
- **Brave, Mojeek and Yahoo scrape result pages**, as `ddgs` does. F2.1 rejected `ddgs` as a default for that reason. SearXNG stays the default anyway:
  - it runs locally and needs no key;
  - nothing scraper-shaped is vendored into Researchly;
  - it suspends an engine that answers with a CAPTCHA instead of retrying;
  - it reports `unresponsive_engines` per query;
  - `RESEARCHLY_WEB_SEARCH=off` turns it off.

  `ddgs` stays opt-in and unbuilt.
- **URL plumbing:** the CLI tells the app where SearXNG is through `RESEARCHLY_SEARXNG_URL`:
  - native: `http://127.0.0.1:<port>`, with the port picked before the app is spawned;
  - Docker: `http://searxng:8080`;
  - off: unset or empty.
- **Docker start:** `up -d searxng` runs without `--wait`, before `up -d --wait app`. `app` has no `depends_on: searxng`, so a slow or broken SearXNG never fails a start. The `searxng` service has a `wget /healthz` healthcheck.
- **Sidecar client (`connectors/sidecar.py`):** the only HTTP outside `PoliteClient`.
  - It is pinned to the configured origin, and callers pass relative paths only.
  - No redirects, no environment proxy, a 2 MB body cap, a 2 s connect timeout and a 15 s total.
  - Pacing: 1 request per second, 2 in flight.
  - It sends `X-Real-IP: 127.0.0.1` to quiet SearXNG's bot-detection log.
  - It is **not** loopback-only. In Docker, `searxng` resolves to a private compose address, which is why it cannot go through the SSRF-guarded `PoliteClient`. Pinning the origin gives the same protection: no caller, page or model can choose the target.
- **Import guard:** only `connectors/http.py`, `connectors/sidecar.py` and `connectors/ssrf.py` import httpx or httpcore. `test_only_the_http_modules_import_httpx` enforces it.
- **Capability:** an app lifespan task polls `/healthz` for up to 60 s and upserts `capability('web_search')` as `ready` or `off` with a reason. SearXNG becomes healthy after the app (native 12–15 s, Docker 5–7 s), so a check at startup would always say off.

## Consequences
- Web search works keyless in both runtimes, and only the connectors spend the scholarly quotas.
- If an upstream engine changes its pages, it shows up in `unresponsive_engines` and in the nightly check, which runs the pinned image with the shipped settings. It never fails a run.
- Some users may not want scraping engines. They can set `RESEARCHLY_WEB_SEARCH=off`. A Settings toggle and keyed engines (Brave API, Tavily, Exa) are step 9.
- GROBID (step 4) will reuse the sidecar client.
- A SearXNG pin bump must re-check the engine list (ADR 0004, checklist step 7).

## Evidence
- `docs/research/2026-11-06-step2c-sidecars.md` §A2 (settings and engine behaviour), §A3 (JSON shape, `unresponsive_engines`), §A4 (plumbing, healthcheck), §A5 (start latency).
- `cli/tests/test_searxng.py::test_real_settings_enable_exactly_the_four_engines` (the pinned SearXNG's `/config`).
```

- [ ] **Step 2: Amend ADRs 0004, 0005 and 0008**

**ADR 0004** (`docs/adr/0004-native-searxng.md`):
- In Decision, replace the **Settings** bullet with:
  ``- **Settings:** `settings_text()` holds the native settings, the same as `infra/searxng/settings.yml` (general web and code engines only, JSON only; ADR 0010); a test keeps the two equal.``
- In the pin-bump checklist, add item 7:
  ``7. Run the `searxng` CLI tests: `test_real_settings_enable_exactly_the_four_engines` fails if an engine name in `keep_only` changed. Keep the image tag in `.github/workflows/nightly.yml` equal to the compose tag.``
- In the **Licence** bullet, replace `over HTTP on loopback only` with `over HTTP only (loopback in native mode, the internal compose network in Docker mode)`.
- Replace the last bullet (`The backend wires the native SearXNG URL into search at step 2/3.`) with:
  ``- The app finds SearXNG through `RESEARCHLY_SEARXNG_URL`, set by the CLI (ADR 0010).``

**ADR 0005** (`docs/adr/0005-pdf-docling-torch-free.md`):
- In the **Tables** bullet:
  - Replace `GROBID's TEI tables are used where GROBID runs (Docker mode).` with `GROBID's TEI tables are not relied on: GROBID's own docs call its table extraction weak, and GROBID is an opt-in Docker-only service from step 4 (step 2c research §B8).`
  - Replace `step 2/4` with `step 4`.
- In Consequences, replace `Docker mode with GROBID gets TEI tables for scholarly PDFs.` with `Rich tables wait for the step 4 re-evaluation in both modes.`

**ADR 0008** (`docs/adr/0008-scholarly-http.md`):
- Replace ``Only `connectors/http.py` and its SSRF guard `connectors/ssrf.py` import httpx or httpcore.`` with:
  ``Only `connectors/http.py`, its SSRF guard `connectors/ssrf.py` and the sidecar client `connectors/sidecar.py` (ADR 0010) import httpx or httpcore; `test_only_the_http_modules_import_httpx` enforces it.``
- Replace the "Exceptions" bullet with:
  ``- **Exception to "all HTTP via `PoliteClient`":** local sidecars (SearXNG now, GROBID at step 4) go through the origin-pinned `SidecarClient` (ADR 0010).``

- [ ] **Step 3: Update the spec** (`docs/superpowers/specs/2026-10-28-researchly-design.md`)

**F2.1:** replace the whole line with:
``- **F2.1** Multi-source search across the keyless §8 connectors plus web search through **SearXNG** (local, free, the default in every mode where it can run; general web from Brave, Mojeek and Yahoo, code from GitHub; scholarly engines off, ADR 0010). Those engines scrape result pages, as `ddgs` does; SearXNG stays the default because it runs locally with no vendored scraper, suspends an engine on a CAPTCHA, and turns off with `RESEARCHLY_WEB_SEARCH=off`. Optional extras: Tavily, Exa or Brave with the user's own key. `ddgs` is an explicit opt-in only.``

**F3.2:** replace ``(OpenAlex `best_oa_location`, arXiv, Europe PMC, CORE)`` with ``(OpenAlex `best_oa_location`, arXiv, Europe PMC; CORE with the optional key)``.

**§4.1 table:**
- Web search row, Docker cell: replace `**SearXNG** container (always on)` with ``**SearXNG** container (started without blocking the app; off with `RESEARCHLY_WEB_SEARCH=off`)``.
- PDF parsing row: replace the whole row with
  ``| PDF parsing | Docling + **GROBID** container (opt-in profile from step 4: `grobid/grobid:0.9.1-crf`, internal network with no egress, `consolidate*=0`; header and references) | Docling (`docling-slim`: pypdfium2, Heron layout and RapidOCR on onnxruntime; no torch/CUDA; tables as flattened text, ADR 0005). No native GROBID (no Windows build, about 1 GB disk and 4 GB RAM): references come IDs-first from OpenAlex, Crossref and OpenCitations, then LLM reference-string parsing + Crossref/OpenAlex matching |``

**§4.1 capability detection:** replace ``at startup the app records a `capabilities` row`` with ``the app records `capability` rows (at startup, or from a background probe for sidecars that start after it: `web_search` polls SearXNG's `/healthz` for up to 60 s)``.

**§4.2 native supervision:** replace `It spawns Postgres, SearXNG, the optional GROBID and the app` with `It spawns Postgres, SearXNG and the app`.

**§8.1 table:** replace these rows (keep the column order the table already uses; adjust the cells if it differs):
- PwC:
  ``| 1 | **PwC archive** (HF `pwc-archive/files`, frozen July 2025) | Local paper→code table (`paper_code`), downloaded at runtime (not bundled): `jul-28-links-between-papers-and-code.json.gz` at commit `1af3a54`, sha256-checked | One 26 MB download | CC BY-SA 4.0 (verified): attribution shown; never exported or redistributed |``
- OpenReview:
  ``| 2 | OpenReview API v2 | ML venue decisions only (oral/spotlight/poster/accepted/not accepted), by exact title through `/notes/search`, for API-v2 venues. Reviews and `/notes` lookups answer a 403 challenge | 20 / min (search) | Per-note CC BY / CC BY-SA |``
- bioRxiv:
  ``| 2 | bioRxiv/medRxiv | Get-only by `10.1101` DOI: preprint metadata and the published DOI (no search API; errors arrive as HTTP 200) | Polite 1 req/s | Per-paper licenses |``
- CORE: delete the row.
- OpenAIRE:
  `| 2 | OpenAIRE Graph v1 | Secondary search in Deep runs, EU repositories, OA links. Merged (dedup) records take every bibliographic field from the instance holding the paper's ID | Budget **60 / hour** (docs; the header claims 7,199) | CC-BY: attribution shown |`
- DBLP:
  ``| 2 | DBLP (SPARQL) | CS venue and year for a known DOI through `sparql.dblp.org` (QLever); not a search source | No published limit; 1 per 2 s | CC0 |``
- ACL Anthology:
  ``| 2 | ACL Anthology | Deferred: daily `anthology+abstracts.bib.gz` (47 MB) into a local table, not the Python package | One daily download | Papers CC BY 4.0 (2016+) / CC BY-NC-SA 3.0 (earlier); metadata licence unstated |``

**§8.1 optional keys:**
- Replace the CORE row's effect with `Full-text fallback (keyless CORE gives no full text and 100 tokens a day): 1,000–5,000 tokens a day`.
- Replace the OpenAIRE row's effect with `7,200 / hour (a personal access token that expires hourly and is renewed with a refresh token)`.

**§8.1 Excluded:** add these bullets:
- `- DBLP search API: behind an Anubis bot challenge (SPARQL is used instead).`
- ``- OpenReview `/notes` (reviews, forums): 403 challenge for anonymous callers.``
- `- bioRxiv/medRxiv web pages and JATS (www.biorxiv.org): Cloudflare challenge.`

**§8.1.1:**
- In the fan-out, replace `OpenAIRE and DBLP as low-rate extras in Deep runs.` with `OpenAIRE as a low-rate extra in Deep runs.`
- Replace the Enrichment bullet with:
  ``- **Enrichment:** OpenAlex singleton ($0) first, then Crossref singleton (license, funders, retraction relations); then bioRxiv for `10.1101` DOIs, DBLP for ML/CS papers without a known venue, OpenReview decisions by title for ML papers, and the local PwC table for code links.``
- In the Full text bullet, replace `then Europe PMC OA XML, then CORE.` with `then Europe PMC OA XML. OpenAIRE OA instance URLs and CORE (optional key) are later tiers.`

**§8.2 limits table:**
- Replace `| OpenReview | 1 per 3 s | — |` with `| OpenReview | 1 per 3.5 s | 1 |`.
- Replace `| OpenAIRE | 1 per min | — |` with `| OpenAIRE | 1 per min | 1 |`.
- Delete the CORE row.
- Add `| bioRxiv | 1 rps | 1 |` and `| DBLP SPARQL | 1 per 2 s | 1 |`.

**§8.3 item 3:** replace `(OpenAlex, Crossref, PubMed, Europe PMC, DataCite, arXiv, HF, OpenCitations)` with `(OpenAlex, Crossref, PubMed, Europe PMC, DataCite, arXiv, HF, OpenCitations, then DBLP, OpenReview, bioRxiv, OpenAIRE)`.

**§8.4:** add the bullet `- Code links from the PwC archive show "Papers with Code archive (CC BY-SA 4.0)"; the table is never exported, bundled or shared.`

**§9.1:**
- Item 2: replace
  ``so tables are flattened text unless GROBID's TEI tables are available (ADR 0005; TableFormer-in-ONNX is re-evaluated at step 2/4). **GROBID** adds the header and references whenever it can run (Docker mode, or native Linux/macOS).``
  with
  ``so tables are flattened text (ADR 0005; GROBID's TEI tables are not relied on; TableFormer-in-ONNX is re-evaluated at step 4). **GROBID** adds the header and references in Docker mode when its opt-in profile runs (step 4).``
- Item 3: where it names GROBID as a reference parser, make it read `GROBID in Docker mode`.

**§19.3:** add the bullet ``- Local sidecars (SearXNG, GROBID) are reached only through the origin-pinned `SidecarClient`: relative paths, no redirects, no env proxy, size cap and timeouts (ADR 0010).``

**§22 footprint:** replace `optional JDK + GROBID ~0.6 GB` with `optional GROBID image ~0.5 GB download / ~1.1 GB on disk and 3–4 GB RAM, Docker mode only`.

- [ ] **Step 4: AGENTS.md and progress**

**AGENTS.md:**
- Replace the bullet
  ``Local sidecars (SearXNG, GROBID) will be the only HTTP outside `PoliteClient`, through a loopback-only client, from step 2b/2c.``
  with
  ``Local sidecars (SearXNG, later GROBID) are the only HTTP outside `PoliteClient`, through the origin-pinned `connectors/sidecar.py`; `test_only_the_http_modules_import_httpx` fails on any other httpx import (ADR 0010).``
- Amend the binding line ``All outbound HTTP goes through `PoliteClient` (`backend/src/researchly/connectors/http.py`); never call `httpx` directly elsewhere.`` to end ``...never call `httpx` directly elsewhere, except the origin-pinned sidecar client `connectors/sidecar.py` for SearXNG (no redirects, 2 MB cap; ADR 0010).``
- In `backend/AGENTS.md`, amend ``outbound HTTP only via `PoliteClient` in `connectors/http.py`, from step 2`` to ``outbound HTTP only via `PoliteClient` in `connectors/http.py` (sidecars: the origin-pinned `connectors/sidecar.py`), from step 2``.
- Add these Gotchas bullets:
  - ``- Web search: SearXNG runs only brave, mojeek, yahoo (general) and github (code), JSON only (ADR 0010). The CLI passes `RESEARCHLY_SEARXNG_URL`; `RESEARCHLY_WEB_SEARCH=off` turns it off. `capability.web_search` is written by a background probe, so it reads `ready` up to a minute after the app starts.``
  - ``- The `paper_code` table holds the PwC archive (CC BY-SA 4.0): show its attribution and never export it. DBLP is SPARQL only and OpenReview is `/notes/search` only: their other endpoints are bot-gated, so never add a workaround.``

**docs/progress.md:** append a step 2c entry in the format of the step 2b entry. It covers:
- what was done;
- test counts (backend, `-m db`, CLI, nightly);
- rulings (point to this plan);
- deferred items (point to this plan's Deferred list);
- "No features.json flips";
- "Next: step 3 (run manager)".

Keep `## Latest plan` pointing at this plan.

- [ ] **Step 5: Check and commit**

```bash
uv run --no-project --python 3.12 scripts/repo_checks.py
git add docs AGENTS.md backend/AGENTS.md
git commit -m "docs: step 2c ADR 0010, spec updates and progress" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Deferred (recorded, not built here)

- **GROBID** (header and references): step 4, as an opt-in Docker-only profile:
  - `grobid/grobid:0.9.1-crf`, pinned by digest at step 4;
  - an internal network with no egress, `consolidateHeader=0`, `consolidateCitations=0`;
  - reached through `SidecarClient`.
  - No native GROBID (ruling 17).
- **`paper_refs`** and keeping the arXiv HTML and Europe PMC JATS bibliographies as parsed references: step 4.
- **CORE:** an optional-key connector (full text, 1,000–5,000 tokens a day), later. Keyless CORE is not useful (R-conn §3).
- **ACL Anthology:** the daily `anthology+abstracts.bib.gz` into a local table, when NLP coverage matters.
- **arXiv OAI-PMH harvest** and the **OpenCitations Meta dump:** opt-in offline indexes, later.
- **OpenReview reviews and forum notes:** they need a logged-in account (403 challenge). At most, a future optional-credential feature.
- **Keyed web search** (Brave API, Tavily, Exa) and a web search toggle in Settings: step 9. `ddgs` stays unbuilt.
- **Stack Overflow and Wikipedia engines:** add them to `keep_only` if Scouts need Q&A or encyclopedia results.
- **Web result caching** and **query-level dedupe across Scouts:** step 3, with the run manager.
- **Calling `enrichment_sources` and `paper_code.ensure_loaded`/`lookup`:** step 3, in the run manager's enrichment.
- **Capability display** (`/api/v1/system`, and `researchly doctor` reading `capability.web_search`): step 9.
- **OpenAIRE OA instance URLs as a full-text tier:** step 3.
- **bioRxiv "no posts found" answers** are HTTP 200, so they are cached for 60 days like real records. Revisit if new preprints are looked up before they are posted.
- **OpenAIRE's real keyless limit:** log `x-ratelimit-used` in the field, then relax `limits.yaml` if 7,199 an hour holds.
- **OpenReview Oral, Spotlight and "Submitted to" cassettes:** add one when a recording meets these forms.
- **Mixed-case DBLP DOIs:** only the upper- and lower-case IRIs are asked for.
- **DBLP title lookup:** revisit if QLever text search (`ql:contains-word`) starts answering for titles.
- **PwC `paper_code` reload:** the snapshot is frozen, so there is no refresh path. A new snapshot needs a new pin and a `TRUNCATE`.
