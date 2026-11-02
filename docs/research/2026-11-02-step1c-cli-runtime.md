# Step 1c research digest: CLI, runtime selection, native Postgres (2026-11-02)

Verified live on 2026-11-02 (PyPI JSON, GitHub API, Docker Hub API, vendor docs, plus local inspection of the pg0 win_amd64 wheel and a local Docker Desktop). "UNVERIFIED" marks anything not confirmed.

## 1. pg0-embedded
- **0.15.2, released 2026-09-14**, MIT, `requires-python >=3.8`. Still the latest, so the Round 3 digest holds. Source: https://pypi.org/pypi/pg0-embedded/json
- **Wheels (0.15.2):** `macosx_14_0_arm64`, `macosx_15_0_x86_64`, `manylinux_2_35_aarch64`, `manylinux_2_35_x86_64`, `win_amd64`, plus an sdist (needs Rust). The PyPI README table omits macOS x64, but the file list has it. Same source.
- **Bundle:** the wheel is `pg0/__init__.py` (a thin wrapper) plus `pg0/bin/pg0.exe` (a 60 MB Rust binary). I inspected the win_amd64 wheel: the exe embeds `postgresql-18.1.0-x86_64-pc-windows-msvc/` including **`lib/pg_trgm.dll` and `share/extension/pg_trgm--*.sql`**. So **`pg_trgm` is bundled on Windows** (verified by static inspection; I did not run `CREATE EXTENSION`, because executing the downloaded binary was blocked). pgvector is bundled (README: "PostgreSQL 18 with pgvector 0.8.5 bundled"). No `pg_textsearch`, as expected. Linux/macOS contents are UNVERIFIED but are likely the same build recipe. `pg0.list_extensions()` exists, so the spike should assert `vector` and `pg_trgm` appear. Sources: wheel inspection; https://github.com/vectorize-io/pg0
- **Python API** (from the wheel's `__init__.py` and the PyPI README):
  ```python
  from pg0 import Pg0
  pg = Pg0(name="researchly", port=5433, username="myuser", password="mypass",
           database="mydb", data_dir="/path/to/data", config={"shared_buffers": "512MB"})
  info = pg.start()   # InstanceInfo (uri, port, username, database, data_dir)
  pg.uri              # postgresql://myuser:mypass@127.0.0.1:5433/mydb
  pg.running; pg.info(); pg.execute("CREATE EXTENSION IF NOT EXISTS vector"); pg.logs(); pg.stop(); pg.drop()
  ```
  Context-manager use (`with Pg0() as pg:`) and module functions (`pg0.start/stop/info/list_instances/list_extensions`) also exist. `data_dir` is a constructor argument that maps to `--data-dir`. The default data dir is `~/.pg0/instances/<name>/data/`, and PG is extracted to `~/.pg0/installation/` on first start (README; the wheel's `_get_install_dir` uses `%LOCALAPPDATA%` on Windows). The README says it auto-picks a free port if the requested one is busy. Check `info.port` after start rather than assuming the port you passed.
- **Process model:** every Python call shells out to `pg0.exe` via `subprocess` (`_run_pg0`). The Postgres server is a separate OS process, not in-process. `start` returns once the server is up, which implies the server is detached from the CLI call (inferred, not documented). Consequence for supervision:
  - The CLI cannot hold a long-lived child handle.
  - It should call `pg0 start` / `pg0 stop`, and track the postmaster PID or poll `pg_isready`.
  - The Job Object (KILL_ON_JOB_CLOSE) will only cover a detached Postgres if the job assignment happens before pg0 spawns it, so that children inherit the job. UNVERIFIED: whether pg0's postmaster stays in our job. The spike must test the crash-the-CLI case.
  - Alternative: bypass the wrapper and run the extracted `postgres` binary directly. The binaries are under `<install dir>`. Run `initdb` and then `postgres -D <dir> -p <port>` as a real supervised child.
- **Start time:** no documented figure. The pg0 Docker examples use `sleep 3`. UNVERIFIED, so measure it in the spike (first start includes extraction plus initdb).
- **Windows quirk:** the wrapper routes stdio through real files because `subprocess.run` hangs on Windows when a detached child holds the pipes (comment in `_run_pg0`). This supports the detached-daemon reading. It also warns us not to capture pg0 stdio through pipes if we call the exe ourselves.

## 2. conda-forge fallback
- `postgresql` **18.6** and `pgvector` **0.8.6** are the latest on conda-forge. Builds exist for linux-64, linux-aarch64, osx-64, osx-arm64 and win-64 (also ppc64le). Source: https://api.anaconda.org/package/conda-forge/postgresql and https://api.anaconda.org/package/conda-forge/pgvector
- **pixi v0.83.0** (2026-10-22) is the latest release. It installs per-user (`~/.pixi`) with no admin. Source: https://github.com/prefix-dev/pixi/releases
- Command (not run here, UNVERIFIED end to end; package names are confirmed, the CLI flags are from my knowledge of pixi):
  ```
  pixi init <appdir>/pg-env -c conda-forge
  pixi add --manifest-path <appdir>/pg-env "postgresql=18" pgvector
  pixi run --manifest-path <appdir>/pg-env initdb -D <data> ; pixi run ... pg_ctl / postgres -D <data> -p <port>
  ```
  Whether conda-forge's PG includes the contrib `pg_trgm` is UNVERIFIED (contrib is normally in the main package). The spike must check.

## 3. CLI framework
- Versions: **Typer 0.27.3** (2026-10-06, MIT), **Click 8.5.0** (2026-08-26, BSD-3), **cyclopts 5.3.2** (2026-10-27, Apache-2.0). Source: PyPI JSON for each.
- Typer is built on Click, and tests use `typer.testing.CliRunner` (a Click `CliRunner` subclass). It has type-hint-driven commands, the largest ecosystem, and `uv tool` works with it (a console-script entry point). Source: https://typer.tiangolo.com
- cyclopts has its own parser and a smaller footprint. It is newer and its testing story is less standard. I did not verify its testing docs. The "less code" claims on its site are vendor claims, not benchmarks. Source: https://cyclopts.readthedocs.io/en/latest/vs_typer/README.html
- **Recommendation: Typer 0.27.3.** It is the mature choice, with a standard CliRunner test path, and the CLI's needs (four subcommands plus `config set`) are small. Pin the exact version. Plain Click is the fallback if Typer's dependency footprint (rich/shellingham) bothers us.

## 4. Container engine detection
Verified locally on this Windows box (Docker Desktop running: Docker 29.6.1, Compose v5.2.0). The cross-platform claims below are marked.
- **Ready vs stopped (all Docker-API engines):** `docker info --format '{{.ServerVersion}}'` (or `docker version`) returns exit **0** when the daemon answers and exit **1** with "error during connect" when unreachable (verified with a bogus `DOCKER_HOST`). Installed = `shutil.which("docker")` or a known path. Use a timeout of about 10 s, because a hung socket can block.
- **Context:** `docker context ls` here shows `default` (DOCKER_HOST-based) and `desktop-linux *` (npipe `//./pipe/dockerDesktopLinuxEngine`). OrbStack's context is named `orbstack` (https://docs.orbstack.dev/docker/). Respect the active context and `DOCKER_HOST`, because a stale `DOCKER_HOST` overrides the context (community reports).
- **Socket probes if the CLI is absent or the context is wrong** (macOS/Linux; Colima and Rancher paths are community-sourced, so treat as UNVERIFIED defaults):
  - Colima `~/.colima/default/docker.sock`
  - Rancher Desktop `~/.rd/docker.sock`
  - OrbStack `~/.orbstack/run/docker.sock` (UNVERIFIED; the docs only confirm a `/var/run/docker.sock` symlink with admin)
  - Docker Desktop `~/.docker/run/docker.sock`
  - Podman: take the socket from `podman machine inspect` (`ConnectionInfo.PodmanSocket.Path`, https://docs.podman.io/en/latest/markdown/podman-machine-inspect.1.html). `State` is `"running"` when up. On Linux use `$XDG_RUNTIME_DIR/podman/podman.sock`.
- **Engine start commands** (from spec §4.1; not re-verified): `podman machine start`, `colima start`, `rdctl start`, `orb start`. The OrbStack docs page I fetched mentions `orb config/restart/logs` but not `orb start`, so confirm `orb start` against the OrbStack CLI docs. Colima, Rancher, Podman and OrbStack start commands are UNVERIFIED here.
- **Docker Desktop on Windows:**
  - Per-user install (no admin): `%LOCALAPPDATA%\Programs\DockerDesktop`. All-users install (admin): `C:\Program Files\Docker\Docker` (confirmed present here: `...\Docker\Docker Desktop.exe`; the CLI is on PATH at `...\Docker\resources\bin`). Source: https://docs.docker.com/desktop/setup/install/windows-install/
  - The docs do not list registry keys. HKLM/HKCU `SOFTWARE\Docker Inc.\Docker Desktop` did not exist on this machine, so **do not rely on the registry**. Probe both paths, then `shutil.which("docker")` and walk up to find `Docker Desktop.exe`.
  - No documented CLI flag to start it. Launch the exe detached (`Popen` with `DETACHED_PROCESS`), then poll `docker info`.
- **Compose:** `docker compose` v2 plugin (v5.2.0 here) is the standard form. `podman compose` is only a wrapper over an external provider, `docker-compose` first and then `podman-compose` (override via `PODMAN_COMPOSE_PROVIDER`), and it prints a warning unless `PODMAN_COMPOSE_WARNING_LOGS=false`. So for Podman, check that a provider exists, else degrade to native. Source: https://docs.podman.io/en/latest/markdown/podman-compose.1.html
- **Readiness times:** no authoritative figures found. UNVERIFIED. Use a configurable timeout (suggest 120 s with progress output) and poll `docker info` every 2 s.

## 5. Windows process supervision
- **Job Object via `ctypes`:** `CreateJobObjectW`, `SetInformationJobObject(JobObjectExtendedLimitInformation)` with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000`, then `AssignProcessToJobObject`. Keep the job handle alive for the CLI lifetime. Reference implementations are in mozprocess and Chromium's luci-py (search results, e.g. https://searchfox.org/mozilla-central/source/testing/mozbase/mozprocess/mozprocess/winprocess.py). Roughly 40 lines, with no dependency. It works on Win 8+ (nested jobs).
- **pywin32 312** (2026-06-04, PSF) works but is a heavy dependency, and the user's machine already shows a broken pywin32 `.pth`. Prefer ctypes.
- **Maintained small library:** `win-safesubprocess 0.1.0` (2026-07-13, MIT) starts each child suspended and puts it in a kill-on-close job. It is brand new, version 0.1.0, and not patched for asyncio. I would not depend on it. Source: https://pypi.org/project/win-safesubprocess/
- **psutil 7.2.2** (2026-01-28, BSD-3). Useful for the process tree, `terminate`/`kill`/`wait_procs` and PID liveness, cross-platform. Source: PyPI JSON.
- **POSIX:** `subprocess.Popen(..., start_new_session=True)`, then `os.killpg(pgid, SIGTERM)`, wait about 10 s, then `SIGKILL` (stdlib).
- Caveat from §1: a detached Postgres may escape the supervision tree. Test it.

## 6. platformdirs
- **4.14.3** (2026-11-01, MIT). Source: PyPI JSON.
- API (from knowledge, stable for years): `user_data_dir("researchly")`, `user_log_dir(...)`, `user_state_dir(...)`, `user_config_dir(...)`, `user_cache_dir(...)`. Use `PlatformDirs("researchly", appauthor=False)` and its `.user_data_path`, `.user_log_path` and `.user_state_path` properties. `appauthor=False` avoids the extra `Researchly\Researchly` folder on Windows. Context7 was not queried.

## 7. GitHub Actions runner labels (public repos: standard runners free and unlimited)
- Linux x64 `ubuntu-24.04` (or `ubuntu-latest`)
- Linux arm64 `ubuntu-24.04-arm`
- Windows x64 `windows-2025` (or `windows-latest`; `windows-2022` also exists)
- macOS arm64 `macos-15` (also `macos-14`, deprecated, and `macos-26`/`macos-latest`)
- macOS x64 `macos-15-intel` (also `macos-26-intel`). `macos-13` is **absent** from both the runner-images README and the GitHub docs table, so treat it as retired. `macos-14-large` (x64) is marked deprecated.
- Sources: https://github.com/actions/runner-images and https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- UNVERIFIED: the end-of-life date for the Intel images. Apple's Intel support is ending, so expect `macos-*-intel` to be removed eventually. Keep that matrix leg `continue-on-error` or non-gating.
- Note: pg0's macOS x64 wheel needs macOS 15 and the arm64 wheel needs 14, so `macos-15` and `macos-15-intel` satisfy both.

## 8. Custom Postgres image
- `pgvector/pgvector:pg18` is current. Docker Hub tags updated 2026-10-31: `pg18`, `0.8.7-pg18`, `0.8.7-pg18-trixie`, `0.8.7-pg18-bookworm`. Pin `0.8.7-pg18-trixie` (or bookworm) for reproducibility. Note pgvector is 0.8.7 in the image, 0.8.6 on conda-forge and 0.8.5 in pg0, so schema/migrations must not depend on features newer than 0.8.5. Source: https://hub.docker.com/v2/repositories/pgvector/pgvector/tags?name=pg18
- Official `postgres:18` (18.6, updated 2026-10-31) is Debian-based, and the pgvector image builds on it. The Debian variants ship the contrib extensions (the docker-library README mentions "postgres-contrib" being absent only on Alpine, so Debian has them). `pg_trgm` is therefore available. Treat as high confidence but add a `CREATE EXTENSION pg_trgm` assertion to the image build test. Source: https://github.com/docker-library/docs/blob/master/postgres/README.md
- **pg_textsearch (Timescale):** latest **v1.5.1** (2026-10-02). Supports **PG17 and PG18** (PG19 best effort), generally available since 1.0.0, **PostgreSQL License** (permissive). Source: https://github.com/timescale/pg_textsearch
  - Prebuilt release assets: `pg-textsearch-v1.5.1-pg18-amd64.zip` and `-arm64.zip` (Linux only). Source tarball: `pg_textsearch-1.5.1.tar.gz`. Source: https://api.github.com/repos/timescale/pg_textsearch/releases/latest
  - Install: build from source in a multi-stage Dockerfile (`make && make install` against PG18 dev headers), or unpack the prebuilt zip (the layout of the zip is UNVERIFIED). It needs `shared_preload_libraries = 'pg_textsearch'`, a restart, then `CREATE EXTENSION pg_textsearch;`.
  - Windows needs an MSVC source build, with no prebuilt binaries, which matches the native-mode fallback to tsvector.
  - No distro package was described.

## 9. SearXNG and GROBID images
- **SearXNG:** `searxng/searxng` on Docker Hub (tags `latest`, and date-hash tags such as `2026.11.1-10db664f1`, last updated 2026-11-01). There is no semver, so pin a date-hash tag. Source: https://hub.docker.com/v2/repositories/searxng/searxng/tags
  - JSON format (settings.yml):
    ```yaml
    search:
      formats: [html, json]
    ```
    Source: https://docs.searxng.org/admin/settings/settings_search.html
  - Limiter off, local use:
    ```yaml
    server:
      limiter: false
      public_instance: false
      secret_key: "<random per install>"
      bind_address: "127.0.0.1"
    ```
    Env equivalents: `SEARXNG_LIMITER`, `SEARXNG_SECRET`, `SEARXNG_BIND_ADDRESS`, `SEARXNG_PORT`, `SEARXNG_PUBLIC_INSTANCE`. The limiter needs Valkey only when enabled. Source: https://docs.searxng.org/admin/settings/settings_server.html
- **GROBID:** `grobid/grobid` on Docker Hub. Latest tags `0.9.1-full` and `0.9.1-crf`, both updated 2026-08-04 (a `0.9.0` plain tag also exists). `-crf` is the lightweight CPU image, so use **`grobid/grobid:0.9.1-crf`** for the optional service. `-full` is the deep-learning image and is heavier. The meaning of each variant is from my knowledge of GROBID's docs, not re-fetched (UNVERIFIED). Source: https://hub.docker.com/v2/repositories/grobid/grobid/tags

## Recommendations for the plan
1. **CLI:** Typer 0.27.3 with `typer.testing.CliRunner`, installed via `uv tool`. Use `platformdirs` 4.14.3 with `appauthor=False`, and psutil 7.2.2 for process-tree work.
2. **Native Postgres spike:** use `pg0-embedded` 0.15.2 first. `pg_trgm` is confirmed bundled in the Windows build. The spike's acceptance checks:
   - `list_extensions()` lists `vector` and `pg_trgm`, and `CREATE EXTENSION` succeeds for both on all five targets;
   - the data dir and port are set explicitly (read `info.port` back);
   - measure first and warm start times;
   - confirm what happens to Postgres when the CLI is killed.
3. **Supervision:** the Job Object via `ctypes` (about 40 lines, no dependency) plus POSIX `start_new_session`/`killpg`. Treat pg0's detached postmaster as the weak point. If it escapes, switch to `initdb` + `postgres` run directly from pg0's extracted install dir, or keep `pg0 stop` in the shutdown path plus a PID file for crash recovery.
4. **Engine detection:** `docker info` exit code with a timeout is the readiness probe for Docker, Colima, Rancher and OrbStack. Use `podman machine inspect` for Podman. Probe `Docker Desktop.exe` at the two known Windows paths rather than the registry. For Podman require a compose provider, otherwise fall back to native.
5. **Images:** custom Postgres from `pgvector/pgvector:0.8.7-pg18-trixie`, add pg_textsearch v1.5.1 (source build in a multi-stage Dockerfile), `shared_preload_libraries=pg_textsearch`, and a build test asserting `vector`, `pg_trgm` and `pg_textsearch`. Compose images: `searxng/searxng:<date-hash tag>` and `grobid/grobid:0.9.1-crf`, with the SearXNG settings above mounted.
6. **CI matrix:** `ubuntu-24.04`, `ubuntu-24.04-arm`, `windows-2025`, `macos-15` and `macos-15-intel` (non-gating, because Intel images will be retired).
7. **Fallback:** conda-forge via pixi v0.83.0 (postgresql 18.6, pgvector 0.8.6) is available for all five targets, so keep it as the documented fallback. Don't build it in 1c unless the spike fails.

## Open risks (could not verify)
- No `CREATE EXTENSION` run of `pg_trgm` or `vector` on pg0 (the exe was not executed). The Linux and macOS bundles were not inspected.
- pg0 start time, and whether its postmaster stays inside our Job Object or process group.
- pg0's README says macOS arm64 only; the macOS x64 wheel exists on PyPI but its contents were not checked.
- Whether conda-forge's PG 18 includes `pg_trgm`, and the exact pixi commands (not run).
- Stopped-engine detection and start commands for Colima, Rancher, OrbStack and Podman (including whether `orb start` exists) were not tested. Typical readiness times for any engine have no source.
- Socket paths for Colima, Rancher and OrbStack come from community posts or the docs only partially.
- Intel macOS runner end-of-life date.
- Meaning of GROBID `-crf` vs `-full`, and the pg_textsearch prebuilt zip layout.
- The platformdirs API list and the pixi command flags come from prior knowledge, not a fetched page this session.

## Local verification by the controller (2026-11-02, Windows 10 x64, Docker Desktop 29.6.1)
These close several of the open risks above, on Windows only. The other four targets are covered by the plan 1c CI matrix.
- **pg0 0.15.2 works on Windows:** `CREATE EXTENSION vector` (0.8.5) and `pg_trgm` (1.6) succeed. `data_dir` relocation works (`SHOW data_directory` returns the requested path).
- **pg0 timings:** cold start including initdb (binaries already extracted) 6.6 s; warm start 0.5 s; stop 0.2 s.
- **pg0 lifecycle:**
  - The postmaster is a detached daemon and survives the Python process that started it. `Pg0.running` / `info()` see it from a fresh handle.
  - `start()` on a running instance raises `Pg0AlreadyRunningError`, so check `running` first. `stop()` is idempotent.
  - **`drop()` deletes the data directory**, so production code must never call it.
- **Job Object via ctypes:** a parent in a KILL_ON_JOB_CLOSE job that is hard-killed (`psutil.kill`) takes its child with it.
- **Docker image:** `pgvector/pgvector:0.8.7-pg18-trixie` plus a multi-stage source build of pg_textsearch 1.5.1 builds cleanly. Source is the release asset `pg_textsearch-1.5.1.tar.gz`, sha256 `5b90a160c4aa9ab78c72ab41eb761146a601485bff5e271f05a44d3da7493d5b` (GitHub asset digest). With `shared_preload_libraries=pg_textsearch`, `vector` 0.8.7, `pg_trgm` 1.6 and `pg_textsearch` 1.5.1 all install.
- **PG18 on a Windows bind mount** (Docker Desktop): initdb works with `-v <host dir>:/var/lib/postgresql`, and data lands in `<host dir>/18/docker`.
- **Image tags exist:** `grobid/grobid:0.9.1-crf`, `python:3.12-slim-trixie`, `ghcr.io/astral-sh/uv:0.12.10`. SearXNG is pinned to `2026.10.25-54ca1d9cf`, about a week old, in the spirit of the §19.7 cooldown.
