# ADR 0006: Code sandboxes — Pyodide in Deno now, per-OS kernel sandbox later

- Status: accepted
- Date: 2026-11-02
- Spec sections: §4 (code cells row), §13 (notebook), §19.4, §26 risk 3

## Context
Spec §19.4 runs light notebook cells in Pyodide inside Deno, with Deno's permissions as the boundary. It runs the full CPython kernel in an OS sandbox. On Windows, that sandbox was specified as a restricted token plus ACLs and a Job Object. Research (`docs/research/2026-11-02-step1e-sandbox.md`) found three problems with the kernel side:
- A restricted token does not block network.
- An AppContainer with no capabilities can be created from ctypes without admin.
- Ubuntu 24.04 restricts unprivileged user namespaces, so `bwrap` fails without root.

Pyodide-level escapes (ctypes, `emscripten_run_script`) reach Deno's JS, where only Deno's flags stop them. This class of escape was reported at DEF CON 34 (Cyera).

## Decision
**Light cells (built in step 1e):** `researchly.sandbox.light.run_cell` runs Pyodide 314.0.7 under Deno 2.9.8, from the `deno` PyPI wheel (MIT, pinned).
- **Deno flags:** `--no-prompt --cached-only`, with read access to the Pyodide package dir, the package cache and the workdir, and write access to the workdir only. There is no net, run, FFI, env or sys access.
- **Environment:** the Deno env is `DENO_DIR`, `DENO_NO_UPDATE_CHECK` and `NO_COLOR`, plus `SYSTEMROOT` on Windows. Nothing else is inherited.
- **Paths:** on Windows they are passed resolved and with forward slashes, because Deno denies 8.3 short names.
- **Links:** `run_cell` refuses a workdir that contains a symlink, junction or hard link. Deno checks a link's own path, not its target. On windows-2025, a planted symlink let a cell read a file outside the workdir, and a junction does the same on a normal non-admin Windows account. A cell cannot create a symlink, because Deno stats the target first and gets NotCapable. It can hard-link two of its own files (`Deno.linkSync` needs write access to both paths), which only locks it out of its own workdir.
- **Config:** Deno runs with `--no-config` and `DENO_NO_PACKAGE_JSON=1`, so a `deno.json` or `package.json` above the runner cannot remap its imports. A cell's output is untrusted: a malformed result line is reported as a failed cell.
- **Commas:** Deno's permission lists are comma-separated, so a path that resolves to one containing a comma is rejected.
- **Timeout:** the default is 60 s per cell, and the Deno process is killed when it expires. With no run permission, there are no grandchildren.
- **Escape tests:** every escape test has a positive control and runs on all five OS in the `platform` CI job:
  - fetch;
  - ctypes `system()`;
  - `Deno.Command`;
  - host-JS read and write outside the workdir, `Deno.env` and `Deno.dlopen`;
  - a cell-made symlink and a planted link;
  - a runaway loop.

**Kernel sandbox (built at step 7/10):** one mechanism per OS, chosen from the probes below.
- **Linux:** Landlock is the first-class path. Use `bwrap` plus seccomp only where unprivileged user namespaces work, which Ubuntu 24.04+ blocks by default. Landlock controls only TCP before ABI 10, so a seccomp filter denying `socket(AF_INET*)` closes UDP and raw sockets.
- **macOS:** Seatbelt via `sandbox-exec`, with a `(deny default)` profile, the inverse of the probe's `(allow default)`.
- **Windows:** an AppContainer with no capabilities (spec §19.4 is changed from restricted token + ACLs), plus a Job Object with `ActiveProcessLimit` to block child processes, and memory and time limits. The container SID gets full access to the workdir. ALL APPLICATION PACKAGES gets RX on the Python runtime when that runtime is not already readable.
- `sandbox_level` in the capabilities row records the mechanism and what it enforces, for example the Landlock ABI and whether UDP is covered.

## Consequences
- Light cells run on every OS with no admin, container or extra install. Deno and Pyodide come from PyPI and npm with pinned versions, and the packages download once to the cache.
- **Cell latency:** each cell starts a fresh Deno and Pyodide. A first numpy+pandas+matplotlib cell takes 6–22 s on CI runners. Step 7/10 should keep one warm Pyodide per notebook session; the cells of one notebook share a trust level.
- **Upgrades:** Deno and Pyodide upgrades re-run the escape suite on all five OS before merging, because escape behaviour changes between versions.
- **Spawn:** a plain AppContainer and the probe's Landlock ruleset still allow starting a readable executable. The kernel step must add the Job Object limit on Windows and a no-exec rule (or seccomp) on Linux.
- **Windows network denial:** AppContainer loopback denial shows up as a connect timeout, not a refusal. Tests must allow for the wait.
- **CI is admin on Windows:** windows-2025 runs as admin, so a Windows check that depends on being unprivileged must also run on a non-admin account. The dev box is one.
- **The probe's ACL grant:** when the probe has to grant RX on the Python install (it did on the windows-2025 runner's tool cache), it removes the grant when it finishes. The kernel step should grant on its own runtime copy only.

## Evidence
**Light cells (CI run 41532022092, platform job; timings in seconds):**

| Runner | Warm-up (Deno + Pyodide + packages) | First numpy+pandas+matplotlib cell |
|---|---|---|
| ubuntu-24.04 | 3.92 | 9.16 |
| ubuntu-24.04-arm | 4.69 | 9.63 |
| windows-2025 | 7.48 | 14.71 |
| macos-15 (arm64) | 2.87 | 5.96 |
| macos-15-intel | 10.91 | 21.89 |

What the escape attempts returned:
- **`system()`:** on Windows it dies with `NotCapable: Requires env access to "COMSPEC"`. On POSIX, wasm `system()` returns non-zero.
- **`Deno.Command`:** with an absolute path, `NotCapable: Requires run access`.
- **The rest:** fetch, host reads and writes, env and FFI all return `NotCapable`.

**Kernel probes (`scripts/sandbox_probe.py`, CI run 41532022092; dev box Windows 10 19045 non-admin):**

| Target | Mechanism | Facts | write outside | read decoy | network | spawn |
|---|---|---|---|---|---|---|
| ubuntu-24.04 | Landlock ABI 8 | kernel 7.0.0-1017-azure; `apparmor_restrict_unprivileged_userns=1`; bwrap 0.9.0 fails (`loopback: Failed RTM_NEWADDR`) | denied | denied | denied | allowed |
| ubuntu-24.04-arm | Landlock ABI 8 | same kernel and bwrap result | denied | denied | denied | allowed |
| macos-15 (15.8.2) | Seatbelt | `/usr/bin/sandbox-exec` | denied | denied | denied | denied |
| macos-15-intel (15.7.9) | Seatbelt | same | denied | denied | denied | denied |
| windows-2025 | AppContainer | admin; icacls RX grant on the tool-cache Python | denied | denied | denied | allowed |
| Windows 10 dev box | AppContainer | non-admin; Python in Program Files readable without a grant | denied | denied | denied | allowed |

How to read the table:
- Each deny result has an unsandboxed positive control that succeeded.
- A sandboxed write inside the workdir succeeded (`work_writable`), so these denials are specific, not blanket.
- The Linux and macOS denials are `PermissionError`.
- The Windows denials are an `OSError` in the child. The network denial is a timeout.
