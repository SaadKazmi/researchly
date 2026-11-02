# Research 1e: SearXNG native (no-container) on Windows/macOS/Linux

Date: 2026-11-02. Tested locally: Windows 10 Pro 19045, uv, CPython 3.12.0 (uv --python 3.12).
Scratch dir: `...\scratchpad\spike-searxng` (src tarball, venv, settings, mock, logs).

## Recommendation (short)
SearXNG **runs on native Windows** with granian (WSGI) and an app-private venv, with **one trivial shim**: stub module `pwd`
(upstream `searx/valkeydb.py` has a top-level `import pwd`, imported unconditionally by `searx/webapp.py`). Everything else (lxml, curl_cffi,
msgspec, granian, setproctitle wheels) installs from binary wheels and a real live scholarly search returned JSON. Upstream says Windows is **unsupported**
(maintainer return42 on #5411, 2025-11-04, and closed PR #5412 that fixed `pwd`) so we own the shim and must re-verify on every pin bump.
Do NOT `pip install searxng` from PyPI (that name is an unrelated third-party MCP server). Install from a pinned codeload tarball (SHA + sha256).
Use granian first (works); waitress is only a fallback that is not needed. Keep spec fallback ("web search off, doctor says why") for failure.

## 1. How SearXNG is installed and versioned today
- **No PyPI releases, no GitHub releases/tags.** `gh api repos/searxng/searxng/releases/latest` -> 404; PyPI project `searxng` is `searXNG 0.1.2` by D. Danchev, "MCP server providing SearXNG-based web search" (https://pypi.org/pypi/searxng/json), not SearXNG. Versioning is rolling master; version string is derived from git (`searx/version.py`: `git show -s --format=%ct` -> e.g. `2025.11.4+e727ac614`). In a git-less tarball it logs `Error while getting the version: fatal: not a git repository` and falls back to `1.0.0` (non-fatal). Fix: `python -m searx.version freeze` (writes `searx/version_frozen.py`; in my run it also printed a CalledProcessError traceback from `git show` but still created the file with `1.0.0`). For Researchly: write our own `searx/version_frozen.py` with the commit date + SHA at install time.
- Source install is the official route (https://docs.searxng.org/admin/installation.html; `container/entrypoint.sh:126` runs `granian searx.webapp:app`). `setup.py` exists (no pyproject.toml), `python_requires=">=3.10"`, classifiers 3.10-3.13. Deps are `requirements.txt` + `requirements-server.txt`; the package is run from the source tree (`PYTHONPATH=<src>`), no install of the `searx` package needed (I did not install it).
- master at test time: **`d138f443bdd535d9935b6089c92912c5c9a8588d`** (2026-11-02T13:24:53Z, "[feat] engines: add ReaderFunded (general, web) - #6908"). Rolling master changes daily; a pin is essential. Tarball: `https://codeload.github.com/searxng/searxng/tar.gz/<SHA>` -> 6,629,205 bytes, **sha256 `8546d545416c7f6eb2f874d566130ce5ca9c4205861fa45afe1ba2697a47e858`** (GitHub codeload tarballs are generally stable per SHA but not contractually guaranteed byte-identical; if worried, verify the SHA via `gh api` / the tarball's top-level dir name `searxng-<SHA>` and a hash over extracted files instead).
- requirements at that SHA (all exact-pinned, good): certifi 2026.10.23, babel 2.18.0, flask-babel 4.0.0, flask 3.1.3, jinja2 3.1.6, lxml 6.1.3, pygments 2.21.0, python-dateutil 3.0.0, pyyaml 6.0.3, **curl_cffi 0.17.0** (new HTTP client; httpx is gone), valkey 6.1.1, markdown-it-py 4.2.0, msgspec 0.22.0, typer 0.27.3, isodate 0.7.2, whitenoise 6.12.0, typing-extensions 4.16.0; server: granian 2.8.5, granian[pname] 2.8.5. No uvloop, no setproctitle in base (setproctitle only via granian[pname]).
- Integrity pinning plan: constants `SEARXNG_SHA`, `SEARXNG_TARBALL_SHA256`; download with the PoliteClient (or urllib in the CLI), verify sha256 before extraction; install deps with `uv pip install --require-hashes` from a Researchly-generated lock (`uv pip compile --generate-hashes requirements.txt requirements-server.txt`) committed per pin. Windows tarball gotcha: `tar` fails on `utils/templates/etc/apache2 -> httpd` symlink ("Cannot create symlink"); extract with Python `tarfile` skipping links/`utils/` (we only need `searx/`, requirements*.txt, LICENSE). Extracted `searx/` = 24 MB; tarball 6.6 MB.

## 2. Blockers for native Windows (source-verified at d138f44, plus run)
Grep of `searx/**/*.py` for pwd/grp/fcntl/resource/termios/uvloop/setproctitle/fork/getuid/signal.SIG*: only hit is
- **`searx/valkeydb.py:22 import pwd`** (used only at line 63 `pwd.getpwuid(os.getuid())` in a Valkey-connection-failure log path). `searx/webapp.py:117` imports it at module load -> on Windows:
  `File ...\searx\valkeydb.py, line 22, in <module> import pwd` / `ModuleNotFoundError: No module named 'pwd'` (reproduced verbatim). **Only hard blocker.**
  Workaround without patching upstream: drop a `pwd.py` shim into the venv site-packages (namedtuple + `getpass.getuser()`); I did this and `import searx.webapp` succeeded. Alternative: patch the line (`try: import pwd`) at install time (breaks tarball-hash purity but we hash the download, not the result).
- Upstream stance: issue #5411 "ModuleNotFoundError pwd when running on Windows" (2025-11-04, closed): return42 "Sorry, there is no official support for SearXNG on MS-Win." PR #5412 (fix) closed unmerged, "challenges for MS Windows support are considerably greater". Open #6859 (2026-10-17) "Add WSL container documentation" - upstream pointer is WSL/containers. https://github.com/searxng/searxng/issues/5411
- Third-party precedent: `simplexng` (jlevy; PyPI 0.1.3, 2025-08-04; vendors SearXNG) claims macOS/Linux/Windows via uv; but it is old (Aug 2025) and unmaintained since, so not a dependency candidate. https://pypi.org/project/simplexng/
- Non-blockers verified: no `fcntl`/`uvloop`; lxml 6.1.3, msgspec 0.22.0, curl_cffi 0.17.0, cffi, granian 2.8.5, setproctitle 1.3.8 all resolved as Windows wheels (uv, no compiler). valkey client is pure Python and only connects if limiter/link_token enabled; with `server.limiter: false` no Valkey needed (`valkey_initialize` logs only if configured).
- Cosmetic warnings seen: `CurlCffiWarning: Proactor event loop does not implement add_reader family...` (curl_cffi spins a selector thread; harmless; could be silenced with `-W ignore::curl_cffi...` or ignored); `ERROR:searx.engines: ahmia / torch: can't register engine (loading engine failed)` (onion/Tor engines; avoid via `keep_only`); `ERROR:searx.botdetection: X-Forwarded-For nor X-Real-IP header is set!` on each request (logged since no proxy; harmless with limiter off; can be silenced by sending `X-Forwarded-For: 127.0.0.1` header from Researchly); `WARNING missing config file limiter.toml`.
- File paths: defaults reference `/etc/searxng` (only used if `SEARXNG_SETTINGS_PATH` unset), `/tmp/sxng_cache_{name}.db` in `searx/cache.py` default (falls back to Windows temp via sqlite path? Not exercised as an error in my runs; set `SEARXNG_SETTINGS_PATH` to a file and verify). `SEARXNG_SETTINGS_PATH` pointing to a **file** worked on Windows with forward-slash absolute path. Set `PYTHONUTF8=1` (settings/yaml/data are UTF-8; cp1252 default bites on Windows).
- Engine-specific: `json_engine`/other engines hitting plain `http://` need per-engine `enable_http: true` (else curl_cffi `InvalidSchema`), relevant for mocks only.

## 3. granian on Windows
- Works: `granian.exe --interface wsgi --host 127.0.0.1 --port 8888 --workers 1 searx.webapp:app` -> `[INFO] Websockets are not supported on WSGI, ignoring`, `Listening at http://127.0.0.1:8888`, `Spawning worker-1`. `/healthz` 200, JSON search 200. Startup to ready ~12-15 s (engine/data load), first live query a few seconds. (granian README: BSD-3, `--interface [asgi|asginl|rsgi|wsgi]`, `winloop` extra exists; no stated Windows limitations; https://github.com/emmett-framework/granian). Warning "Configuration allows spawning up to 512 Python threads" - pass `--blocking-threads 4` / `--runtime-threads 1`.
- Process model: granian main (PID A) + 1 worker child (PID B). `taskkill /F /T /PID A` killed both. Relevant to the Job Object supervisor: venv `granian.exe` is a launcher that may add one more level (pid chain seen: shell -> granian main -> worker); kill-on-close Job Object covers all descendants. Always bind 127.0.0.1; use `--workers 1`; do not use `--reload` (needs extra, unneeded).
- Fallbacks (not needed): waitress (pure Python, WSGI): `waitress-serve --listen=127.0.0.1:8888 searx.webapp:app` (not tested). `python -m searx.webapp` runs Flask dev server (`app.run`) - acceptable for localhost single user but has `debug`-related warnings and is single-threaded-ish; not tested.

## 4. Minimal settings (tested)
```yaml
use_default_settings: true          # or {engines: {keep_only: [...]}} to drop Tor/onion engines
general: {instance_name: researchly-searxng}
server:
  secret_key: "<random 32+ chars, generated at install, stored in app dir>"   # default "ultrasecretkey" is rejected
  bind_address: "127.0.0.1"   # (granian --host is what actually binds)
  port: 8888
  limiter: false              # default is already false at this SHA
  public_instance: false
  image_proxy: false
search:
  formats: [html, json]       # default is html only -> json 403 otherwise (csv/rss 403 confirmed)
```
- Botdetection/limiter off when `server.limiter: false` (no Valkey needed). `SEARXNG_SETTINGS_PATH=<abs file>`.
- Query: `GET http://127.0.0.1:8888/search?q=<q>&format=json&engines=arxiv,openalex,crossref,semantic%20scholar` (also `categories=science`, `pageno=`, `language=`, `time_range=`). Response keys: `query, results[{url,title,content,engine,engines,score,publishedDate,category,...}], answers, corrections, infoboxes, suggestions, unresponsive_engines`.
- Live keyless scholarly engines tested 2026-11-02 (`engines=<name>`, q="transformer attention"): arxiv 10 results; crossref 6 (disabled by default in settings.yml, works when named explicitly); openalex 10 (disabled by default, works named); semantic scholar 10; google scholar 5; **pubmed 0, `[['pubmed','timeout']]`** (flaky/timeout once). Also `wikipedia` 1 result. Others available in tree: base (commented out by default), core, springer (key), pubmed, google_scholar, duckduckgo, brave, mojeek. Default `science` category in settings.yml: lines ~289; engine sets change upstream often, so Researchly should specify `engines=` explicitly and treat `unresponsive_engines` as signal. General web engines (duckduckgo etc.) captcha frequently (issue #6779, 2026-09-24).

## 5. License
SearXNG is AGPL-3.0-or-later (`gh api repos/searxng/searxng/license` -> AGPL-3.0). Running it **unmodified** as a separate process, talking over HTTP/JSON on localhost, is generally "separate programs": FSF GPL FAQ (https://www.gnu.org/licenses/gpl-faq.html) "pipes, sockets and command-line arguments are communication mechanisms normally used between two separate programs. So when they are used for communication, the modules normally are separate programs" (unless intimate exchange of complex internal data structures); also the aggregate/mere-aggregation FAQ. So Researchly (MIT/Apache) is not a derivative work if we: do not import `searx` in our process, do not vendor/ship SearXNG source inside our repo, download it from upstream at install time, and keep HTTP/JSON as the only interface. Obligations: AGPL s13 (network interaction) only bites if we **modify** SearXNG and offer it to remote users; local-only 127.0.0.1 single-user use does not. **The `pwd` shim in site-packages is a separate file we author (not a modification of AGPL files); if we instead patch `valkeydb.py`, we modify SearXNG: keep the patch tiny, state it in `licenses/NOTICE`, and since use is local-only no source-offer is triggered, but publish the patch in our repo anyway (it is a 3-line diff).** Ship LICENSE text in the app venv dir and credit in docs. Not legal advice; ADR should record this.

## 6. Spike design
Install (any OS; `$APP` = app data dir; Windows uses `Scripts\` instead of `bin/`):
```
SHA=d138f443bdd535d9935b6089c92912c5c9a8588d
curl -fL https://codeload.github.com/searxng/searxng/tar.gz/$SHA -o sx.tgz
echo "8546d545416c7f6eb2f874d566130ce5ca9c4205861fa45afe1ba2697a47e858  sx.tgz" | sha256sum -c      # macOS: shasum -a 256 -c ; Windows: Python hashlib
python -c "import tarfile;t=tarfile.open('sx.tgz');t.extractall('src',members=[m for m in t if not (m.issym() or m.islnk()) and ('/searx/' in m.name or m.name.count('/')==1 and m.name.endswith(('requirements.txt','requirements-server.txt')))])"   # cross-platform extract skipping symlinks
uv venv --python 3.12 $APP/searxng/venv
uv pip install --python $APP/searxng/venv/<bin|Scripts>/python -r src/searxng-$SHA/requirements.txt -r src/searxng-$SHA/requirements-server.txt
# Windows only (harmless elsewhere): write $APP/searxng/venv/Lib/site-packages/pwd.py shim (see section 2)
# version file: write src/searxng-$SHA/searx/version_frozen.py with VERSION_STRING etc. (avoid git lookups)
```
Start: `SEARXNG_SETTINGS_PATH=$APP/searxng/settings.yml PYTHONPATH=src/searxng-$SHA PYTHONUTF8=1 <venv>/granian --interface wsgi --host 127.0.0.1 --port <free> --workers 1 --blocking-threads 4 searx.webapp:app`; readiness = poll `/healthz` (200) up to ~60 s.
Measured here (Windows 10): `uv venv` + `uv pip install` of all deps = **~8 s warm cache** (uv cache already had wheels? curl_cffi etc. were fetched fresh; cold-cache estimate 15-40 s on CI); venv **84 MB** + `searx/` source 24 MB + tarball 6.6 MB; startup to healthz ~12-15 s.
Smoke check, offline-safe (CI must not depend on live engines): settings-ci.yml with `use_default_settings: {engines: {keep_only: []}}` plus one `json_engine` pointed at a local mock HTTP server:
```yaml
engines:
  - name: mock
    engine: json_engine
    shortcut: mk
    categories: general
    search_url: http://127.0.0.1:9099/?q={query}
    results_query: items
    url_query: link
    title_query: name
    content_query: abstract
    disabled: false
    enable_http: true       # REQUIRED for plain http, else curl_cffi InvalidSchema
```
Mock = 8-line `http.server` returning `{"items":[{"link":"https://example.org/p1","name":"Mock Paper One","abstract":"offline fixture"}]}`.
Result on Windows (this box): `GET /search?q=hello&format=json&engines=mock` -> `https://example.org/p1 | Mock Paper One | unresponsive_engines=[] | 0.23 s`; `format=rss` -> HTTP 403 (formats gate works). `taskkill /F /T` on the granian main PID cleanly terminated worker + main.
CI matrix (windows-2025, ubuntu-24.04, ubuntu-24.04-arm, macos-15, macos-15-intel): same Python script (`spike_searxng.py`) for all five; python 3.12 via `uv`; wheels for lxml/msgspec/curl_cffi/granian exist for win-amd64, manylinux x86_64/aarch64, macOS arm64/x86_64 (curl_cffi and granian publish all of these; I only verified win-amd64 locally, so the matrix job is the check for the other four; linux-arm/macos-intel are the likeliest to need a source build if a wheel is missing: fail the job loudly). Separate non-gating nightly "live" job: query `engines=arxiv,openalex` and only warn on failure. Add a test that greps upstream master for new POSIX-only imports (`pwd|fcntl|grp|resource|termios`) to catch breakage early when bumping the pin.

## Local experiment log (commands + outcomes)
1. `gh api repos/searxng/searxng/commits/master` -> d138f44 (2026-11-02). Releases/tags -> 404.
2. `curl codeload ... -o sx.tgz; sha256sum` -> 8546d5...e858, 6,629,205 B. `tar xzf` -> `tar: ...utils/templates/etc/apache2: Cannot create symlink to 'httpd': No such file or directory` (exit with failure; rest extracted OK).
3. `uv venv --python 3.12 venv` (CPython 3.12.0); `uv pip install -r requirements.txt -r requirements-server.txt` -> 31 packages in ~8 s (incl. curl-cffi 0.17.0, lxml 6.1.3, msgspec 0.22.0, granian 2.8.5, setproctitle 1.3.8, valkey 6.1.1).
4. `python -c "import searx.webapp"` -> `ModuleNotFoundError: No module named 'pwd'` (valkeydb.py line 22). Also two `Error while getting the version: fatal: not a git repository` log lines.
5. Added venv `Lib/site-packages/pwd.py` shim, ran `python -m searx.version freeze` (printed git CalledProcessError traceback, created version_frozen.py = 1.0.0); `import searx.webapp` -> ok (warnings above).
6. granian WSGI start (live settings.yml, limiter false, formats html+json): `/healthz` 200; `engines=wikipedia` -> 1 result JSON. Per-engine scholarly checks as in section 4.
7. Offline mock: first attempt `unresponsive_engines: [["mock","unexpected crash"]]` with `curl_cffi...InvalidSchema ... '127.0.0.1:9099'`; fixed by `enable_http: true` on the engine; then OK in 0.23 s.
8. Process tree kill via `taskkill //F //T //PID <main>` -> worker + main terminated; port freed. (An earlier blanket `taskkill /IM granian.exe` was blocked by the permission classifier and not run; only my own PIDs were killed.)
Nothing installed globally; repo untouched.

## Sources
- https://github.com/searxng/searxng (commit d138f443..., files `searx/valkeydb.py`, `searx/webapp.py`, `searx/version.py`, `searx/settings_loader.py`, `requirements*.txt`, `setup.py`, `container/entrypoint.sh`)
- https://github.com/searxng/searxng/issues/5411 and PR #5412 (2025-11-04); issue #6859 (2026-10-17); issue #6779 (2026-09-24)
- https://pypi.org/pypi/searxng/json (third-party MCP package, not SearXNG); https://pypi.org/project/simplexng/
- https://github.com/emmett-framework/granian (BSD-3; WSGI interface flag)
- https://www.gnu.org/licenses/gpl-faq.html (pipes/sockets/command-line arguments; aggregate/containers)
- https://docs.searxng.org/admin/installation.html
