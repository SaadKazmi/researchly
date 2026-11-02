# ADR 0002: Native-mode PostgreSQL via pg0-embedded

- Status: accepted
- Date: 2026-11-02
- Spec sections: §4.2, §16.1, §19.2

## Context
Native mode is the fallback when no container engine is available (spec §4.2). It must run PostgreSQL 18 with `vector` and `pg_trgm` and no admin rights, on Windows, Linux (x64, arm64) and macOS (arm64, Intel). Spec §16.1 names `pg0-embedded` as the first choice and conda-forge PG 18 + pgvector as the fallback, with a step-1 spike on all five targets checking extensions, start time and data-dir relocation. Research: `docs/research/2026-11-02-step1c-cli-runtime.md` §1 and "Local verification".

## Decision
Native mode uses `pg0-embedded==0.15.2` (PG 18.1, pgvector 0.8.5, pg_trgm bundled). The conda-forge fallback is not needed.
- Data lives at `<data_dir>/pg/18/native`. Each data dir gets its own pg0 instance, named `researchly-<sha256(pgdata)[:8]>`. The port is `free_port(54329)`, read back from `info()`.
- The Postgres daemon runs outside the app supervisor. `researchly start` starts it, and `stop` stops it. A crashed native Postgres needs `researchly start`; a health-based restart is deferred to step 9.
- The CLI provisions the roles, the database and the untrusted extensions as superuser, then runs migrations as `researchly_app`. The app connects as `researchly_app` only.
- Schema and migrations must not rely on pgvector features newer than 0.8.5, the lowest version across modes (Docker image 0.8.7).

## Consequences
- Native mode needs no container engine, admin rights or system packages. Everything installs from PyPI wheels.
- Keyword search uses `tsvector` in native mode. BM25 (`pg_textsearch`) is Docker-only, and `doctor` lists it as degraded.
- **Known limitation (superuser password at rest):** pg0 keeps the superuser password in `~/.pg0/instances/<name>/instance.json`, and passes it as an argument while `pg0 start` runs. Spec §19.2 wants every secret in the keychain only, so this is an accepted exception. Mitigations:
  - Postgres listens on loopback only.
  - The app never uses the superuser role.
  - On POSIX, the CLI makes the instance directory `0700` after start.
  - A test pins that no other Researchly secret is ever written under `~/.pg0`.

  Revisit if pg0 gains a password-file or trust-auth option.
- pg0 state lives outside `<data_dir>`, so the `~/.pg0` instance record isn't covered by backup or uninstall (step 9 must handle it). pg0's `drop()` deletes the data directory; only test teardown may call it.
- A Docker-mode run and a native run use separate databases (`pg/18/docker` and `pg/18/native`). In `auto` mode, the CLI warns when the selected mode differs from the last one. Moving data between modes uses `pg_dump` (step 9).

## Evidence
- **Local, Windows 10 x64:**
  - Cold start, including initdb, provisioning, migration and app health: 7.4–7.9 s. Warm start: 2.2 s.
  - pg0 alone: cold 6.6 s, warm 0.5 s, stop 0.2 s.
  - `CREATE EXTENSION vector` (0.8.5) and `pg_trgm` (1.6) succeed. Data-dir relocation verified.
- **Docker-mode end-to-end** (for comparison): 561 s cold, including image builds.
- **CI** (`pg0 native start` line from each job's step summary, PR #3):

| Runner | Cold (s) | Warm (s) |
|---|---|---|
| ubuntu-24.04 (x64) | 2.0 | 1.4 |
| ubuntu-24.04-arm | 2.3 | 1.3 |
| windows-2025 | 3.8 | 1.6 |
| macos-15 (arm64) | 1.8 | 1.3 |
| macos-15-intel | 4.2 | 2.8 |

Cold = initdb + provision + migrate + app healthy; warm = restart on an existing data dir. All five targets passed `CREATE EXTENSION vector, pg_trgm` and data-dir relocation (the native round-trip test). The Docker-mode end-to-end job passed on ubuntu-24.04 in 1 min 57 s. CI run: https://github.com/SaadKazmi/researchly/actions/runs/41434531162
