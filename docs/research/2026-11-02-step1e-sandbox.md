# Step 1e sandbox research (as of 2026-11-02)

Legend: [V] = verified by me locally (Windows 10 19045, non-admin), [S] = from a fetched source, [U] = unverified or inferred.

## 0. Recommendation (short)
- Step 1 should spike **Deno + Pyodide** (the default for every mode) as a full, security-tested, 5-OS CI job, plus **cheap per-OS feasibility probes** for the native kernel sandbox. Probes only detect and run a tiny deny-test. Defer building the kernel sandbox to step 7/10.
- Install Deno with the **`deno` PyPI wheel** (`deno==2.9.8`, MIT). It is a normal pinned, hashable dependency and needs no admin, so no per-OS binary download code is needed. Fallback is the official GitHub zip plus its `.sha256sum`.
- For the later kernel work, reuse rather than build:
  - Linux: `bwrap` + seccomp, with Landlock as fallback (the Codex and sandbox-runtime pattern).
  - macOS: Seatbelt via `sandbox-exec`.
  - Windows: **AppContainer with no capabilities** is the unelevated path. It blocks network and writes and works from plain ctypes [V]. This is better than the spec's "restricted token + ACL" for unelevated mode. Treat Anthropic `srt-win` / Codex elevated mode as the optional UAC "stronger mode".
- Do not embed `sandbox-runtime`. It is Node/TypeScript, Windows is alpha and needs elevation, and Linux needs bwrap + socat + ripgrep. Mine its design, or shell out to the `srt` CLI as an optional backend if wanted.

## 1. Deno + Pyodide

### Versions
- Deno **2.9.8**, released 2026-10-21 [S: https://api.github.com/repos/denoland/deno/releases/latest]. V8 15.0, TypeScript 6.0.3 [V].
- Pyodide **314.0.7**, released 2026-09-14 [S: https://api.github.com/repos/pyodide/pyodide/releases/latest].
  - The "314" is the Python 3.14 ABI. Wheel tags are `cp314-...-pyemscripten_2026_0_wasm32`.
  - Packages loaded in my test [V]: numpy 2.4.6, pandas 3.0.2, matplotlib 3.10.8, plus Pillow, contourpy, cycler, fonttools, kiwisolver, packaging, pyparsing, python-dateutil, pytz and six.

### Installing Deno without admin
- **PyPI `deno`** is a "Python redistribution of Deno binaries". Version 2.9.8 was uploaded 2026-10-21, licence MIT (https://pypi.org/project/deno/, https://pypi.org/pypi/deno/2.9.8/json).
  - Wheels:
    - `win_amd64` (41.8 MB)
    - `macosx_10_12_x86_64` (42.8 MB)
    - `macosx_11_0_arm64` (38.8 MB)
    - `manylinux_2_27_x86_64` (42.0 MB)
    - `manylinux_2_27_aarch64` (40.1 MB)
  - These cover all five CI targets.
  - **There is no Windows arm64 wheel.** The CI matrix doesn't include windows-11-arm, but real users may be on it. For them, use the GitHub zip `deno-aarch64-pc-windows-msvc.zip`.
  - PyPI releases carry GitHub provenance attestations [S: PyPI search snippet].
- **Gotcha [V]:** `deno.find_deno_bin()` assumes the binary is in `sysconfig` scripts. It raised FileNotFoundError under `uv run --with deno`, where the ephemeral env's scripts dir wasn't the one it computed. Install the dep into the app's own venv and resolve the path as `Path(sys.executable).parent/"deno(.exe)"`, or `shutil.which` inside that venv. Do not rely on `find_deno_bin()` without a spike test.
- **Official binaries** (fallback): https://github.com/denoland/deno/releases/download/v2.9.8/deno-<triple>.zip, with a `.sha256sum` next to each. SHA-256 of the v2.9.8 zips [S: releases API digests]:

  | Target | SHA-256 |
  |---|---|
  | x86_64-pc-windows-msvc | cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf |
  | aarch64-pc-windows-msvc | b413b92b735b50c198e8fe3b85af1aa50e29d5f72e229aab701d132286e14cef |
  | x86_64-unknown-linux-gnu | e890975bc2549807bbcc0b584ffafda72cc7c93349dd99c80483dd784f6c4609 |
  | aarch64-unknown-linux-gnu | d49596c20b9788cf8f5d8ddf644103d4bde56ed239753f0bdb92ba9361d51905 |
  | x86_64-apple-darwin | 4b2f5edd2639b7470f034238b321d7002d8dfb4726d8558f52c17afac370eb2c |
  | aarch64-apple-darwin | 9f0bf660393da2eaff044cdc7444cb08c73f235849b61d224d825a194ce7c98e |

  Re-fetch these when bumping versions. Pin them in code only if the PyPI route is rejected.
- Size on disk: `deno.exe` is 98 MB uncompressed [V]. The wheel/zip is about 40 MB.
- Linux glibc: the wheel is manylinux_2_27. Alpine/musl is not supported by the wheel or the gnu zip.

### Running Pyodide in Deno [V]
```ts
import { loadPyodide } from "npm:pyodide@314.0.7";
const py = await loadPyodide({ packageCacheDir: "<app-cache>/pyodide-pkgs" });
await py.loadPackage(["numpy","pandas","matplotlib"]);
```
- `npm:` specifiers work and are cached under `DENO_DIR/npm/registry.npmjs.org/pyodide/314.0.7/`. Set `DENO_DIR` to an app-private dir.
- **`packageCacheDir` works under Deno.** On first load it fetched wheels from `cdn.jsdelivr.net/pyodide/v314.0.7/full/` and wrote them to the dir (17 MB for numpy+pandas+matplotlib and the 10 deps). The log message says "node_modules", but the files go to the given dir. A later run loaded from the cache with `--allow-net` absent.
- The core (`pyodide.asm.wasm`, `python_stdlib.zip`, `pyodide-lock.json`) is read from the npm package dir in `DENO_DIR`, so the sandbox needs read access there.
- Warm-up step, which runs once at install with network allowed: run a script that loads the packages so both the npm and wheel caches fill. Then run cells with `--cached-only` and no net.
- Alternative with no network at all: ship `pyodide-core` and the wheels as a pinned bundle and set `indexURL`/`lockFileURL` to the local dir. The release has `pyodide-core-314.0.7.tar.bz2` [S]. This is not needed unless the first-run download is unacceptable.
- Use `py.mountNodeFS("/work", "<cell-tmp-dir>")` to expose the cell temp dir. Without it, Pyodide's FS is in-memory MEMFS, and host paths don't exist [V: `open("C:/.../outside.txt")` gives FileNotFoundError because MEMFS has no such path, not because of Deno]. I did not test `mountNodeFS`. It needs `--allow-read`/`--allow-write` on that dir.

### Startup time [V]
Windows 10, Deno 2.9.8, warm caches, flags below:

| Phase | Time |
|---|---|
| `loadPyodide` | ~2.1–2.4 s |
| `loadPackage(numpy,pandas,matplotlib)` | ~1.2–1.8 s (first run with download: 6.3 s) |
| Total process, first cold run | ~19 s including downloads |
| Total warm | ~3.5–4 s |

CI runners will differ. Measure them and record them in the ADR. A warm worker pool is the obvious mitigation if cells feel slow, but don't build it in step 1.

### Permission flags for "no net except cached packages"
Tested [V], where `$S` is the app dir and `$work` is the per-cell temp dir:
```
deno run --no-prompt --cached-only \
  --allow-read=$S/pkgcache,$DENO_DIR/npm/registry.npmjs.org/pyodide,$work \
  --allow-write=$work  runner.ts
```
- No `--allow-net`, `--allow-run`, `--allow-ffi`, `--allow-env` or `--allow-sys`. Deno stays default-deny, and `--no-prompt` turns any prompt into a NotCapable error.
- Windows gotcha: pass **long, forward-slash absolute paths**. The 8.3 short form (`C:/Users/SAAD~1.KAZ/...`) caused a spurious NotCapable on the pyodide dir. Use `Path.resolve()` or `cygpath -m` equivalents.
- `--allow-env` is not needed for normal runs. Deno asked for `COMSPEC` only during a `system()` escape attempt (below).
- `--cached-only` forbids remote module fetches at run time. I did not exercise it in my run, so spike it. Also consider `--frozen` with a lockfile (Deno docs recommend both).
- Don't use `-A`, `--allow-run`, or `--allow-run=deno` (the last lets a spawned deno use `--allow-all`) [S: https://docs.deno.com/runtime/fundamentals/security/].

### Escape tests I ran inside Deno+Pyodide [V], minimal-permission flags above
| Attempt | Result |
|---|---|
| `js.fetch("https://example.com")` | NotCapable: Requires net access to example.com:443 |
| `ctypes.CDLL(None).system(b"echo pwned > file")` | Pyodide routes `system()` to Deno's `child_process.spawnSync`. Deno threw NotCapable on env `COMSPEC` and the Pyodide instance died. **No file was created.** |
| `ctypes... emscripten_run_script_string("Deno.readTextFileSync(~/.gitconfig)")` (JS eval in host) | NotCapable: Requires read access to the file |
| `open()` outside, `os.listdir(~/.ssh)` | Fail only because MEMFS has no host paths. Not a Deno test, and the real test is the two rows above |
| matplotlib agg render | works |
| `js.Deno.Command.new(...)` | failed. The Python call errored, and I didn't dig into the exact cause. Treat Deno `--allow-run` denial as the real control |

**Bottom line:** the Python-level escapes (ctypes, emscripten exports) do reach host JS. Deno permissions stop what they can do after that. This matches the DEF CON 34 work.

### Known Pyodide/Deno escapes and how permissions mitigate them
- Cyera, "Sandcastles, Not Sandboxes" (DEF CON 34, Aug 2026) [https://www.cyera.com/research/sandcastles-not-sandboxes-how-one-architectural-flaw-exposed-seven-products]:
  - Mechanism: Python-level denylists miss `ctypes`. `ctypes.CDLL(None)` exposes Emscripten exports, including `emscripten_run_script*` (eval JS in the host), `system`, `popen` and `execve`. Reflection via `__subclasses__` is a second route.
  - Deno permissions limit what escaped JS can do after it reaches Deno. They do not stop the transition out of Pyodide. Node has no equivalent.
  - CVEs: n8n CVE-2025-68668 (9.9), Grist CVE-2026-24002 (9.1, fixed by moving to Deno with restricted perms in v1.7.9), cohere-terrarium CVE-2026-61522, smolagents CVE-2026-10613 (WasmExecutor removed). langchain-sandbox was archived.
  - **Bypass caveat from the report:** in langchain-sandbox, `system()` went through Emscripten syscall emulation rather than Deno's Command API, so `--allow-run` did not govern it. My Windows run showed `system()` going through `spawnSync` and being blocked. **Make this a permanent CI test on every OS, since behaviour can change between Deno and Pyodide versions.**
  - smolagents' default `--allow-run` let escaped code spawn processes. Never grant it.
- Prefab UI CVE-2026-120338 (reported 2026-11-01; unpatched as far as I found) is a Pyodide-in-Deno case where the Deno subprocess held read/write/net permissions [S: search snippet via https://github.com/sattyamjjain/agent-airlock/pull/447; secondary source]. It reinforces the "minimal flags" rule.
- Deno's own caveats [https://docs.deno.com/runtime/fundamentals/security/]:
  - Modules in the initial static import graph, including npm, load without permission checks.
  - `--allow-write` plus an executable in that dir lets code replace the binary. Never put the cell dir in PATH.
  - Permissions don't cover symlinks or `/proc`. The docs say nothing on this, so [U]. Mitigation is to create the cell dir fresh and empty, and to never grant read to a parent dir. Test a symlink inside the cell dir pointing out. On Windows, creating a symlink needs privilege, which helps.
  - Deno docs recommend layered defence, with OS sandboxing underneath for untrusted code.
- Defence in depth that is cheap and worth adding in step 1: also remove `ctypes`/`_ctypes` and gate imports at the Python level, as a **non-boundary** hardening (the spec already says import blocking is not the boundary). Run the cell in a subprocess with a hard timeout and memory ceiling (Windows Job Object / Unix `RLIMIT_AS`). Deno has `--v8-flags=--max-old-space-size=N`, but WASM memory isn't covered by it, so rely on the OS limit [U].

## 2. Prior art

| Project | Licence | Fits | Verdict |
|---|---|---|---|
| **Anthropic sandbox-runtime** (`@anthropic-ai/sandbox-runtime` 0.0.81, 2026-10-20) | Apache-2.0 [README] | macOS Seatbelt; Linux bwrap + proxies over unix sockets; **Windows (alpha)** | Beta "research preview". Node/TS API plus `srt` CLI. No Python bindings. Needs `bwrap`, `socat`, `ripgrep` on Linux. Needs one UAC install on Windows. Not embeddable from Python; could be used as a subprocess backend. |
| **OpenAI Codex** (`openai/codex`, `codex-rs/linux-sandbox`, `windows-sandbox-rs`, `windows-sandbox-service`, `bwrap`, `mxc-sandbox`) | Apache-2.0 [GitHub API] | Rust crates | Reference code to read, and the pattern to copy. The crates are tied to the Codex repo. Shipping the Rust helper is possible but is a build/packaging burden (Rust toolchain, per-OS binaries). |
| **bubblewrap** 0.13.1 (2026-10-14) | LGPL-2.0+ [U: GitHub API returned NOASSERTION] | Linux | Unprivileged, but needs user namespaces (see §3). Call as a subprocess. Not auto-installable without a package manager. |
| **`landlockpy`** 0.2.0 (0BSD), **`py-landlock`** 0.1.1 (MIT), `landlock` 1.0.0.dev* | listed | Linux Landlock from Python (ctypes bindings) | Small and young. Our need is ~100 lines of ctypes, so prefer a vendored minimal implementation, or `landlockpy` if it supports the ABI we need. Not verified: network rules (ABI4) support. |
| **pyseccomp** | not found in my search [U] | | Skip. seccomp needs a BPF filter. Use bwrap's seccomp, or `libseccomp` via the OS. Landlock needs no seccomp for FS. |
| **nsjail** | Apache-2.0 [GitHub API] | Linux | Needs unprivileged userns, else root (`--disable_clone_newuser` needs root). Needs building from C++. Not suited. |
| **firejail** | GPL-2.0 [GitHub API] | Linux | Setuid-root binary and a system install. Not suitable. |

Can embed or call from Python on each OS without admin:
- **Linux:** call `bwrap` (if present and userns works) or call Landlock directly via ctypes (`landlock_create_ruleset` / `landlock_add_rule` / `landlock_restrict_self`, plus `prctl(PR_SET_NO_NEW_PRIVS)`). Both are unprivileged.
- **macOS:** call `/usr/bin/sandbox-exec` with a generated profile. Always present, no admin.
- **Windows:** ctypes calls to `CreateAppContainerProfile` / `CreateProcessW` / Job Objects. No admin [V]. Restricted token via `CreateRestrictedToken` is also unprivileged but doesn't block network.
- Codex now **requires bubblewrap for filesystem-restricted runs on Linux**, and keeps Landlock only as a legacy path. It uses `--unshare-user`/`--unshare-pid`, applies seccomp after bwrap (blocks network syscalls and `io_uring`), and shows a startup warning if bwrap can't create userns [https://raw.githubusercontent.com/openai/codex/main/codex-rs/linux-sandbox/README.md]. So the spec's Landlock fallback is our own addition. It is reasonable because the hosts we target may lack userns, but it is less proven.
- Codex Windows has three modes [https://learn.chatgpt.com/docs/windows/windows-sandbox]:
  - **MXC**: needs Windows 11 24H2 build 26100.9278 or newer, or 25H2 build 26200.9278 or newer. No admin, no extra accounts, no firewall rules.
  - **Elevated**: dedicated sandbox users, ACLs, firewall rules and local policy. Needs UAC. Gives stronger network isolation and denied-read paths.
  - **Unelevated**: restricted token from the current user plus ACLs. Network is blocked only via environment-level controls, which is weak. No denied-read paths.
  - Codex's unelevated mode does *not* truly block network. AppContainer (below) does.
- sandbox-runtime Windows (alpha) [README]: dedicated `srt-sandbox` local user, WFP filters, write-restricted token, job object. Needs a one-time elevated install. **Known gaps:** Schannel clients fail inside it. Reads are not isolated per session. The WFP fence misses traffic made on the process's behalf by a system service. Release v0.0.81 fixed a hole where a sandboxed process could start an AppContainer child that bypassed the network block. Our design must therefore also deny child-process creation or inherit the container.

## 3. Linux specifics

### Ubuntu 24.04 AppArmor userns restriction
- Ubuntu 23.10 added `kernel.apparmor_restrict_unprivileged_userns`. 24.04 enables it by default. Unprivileged `unshare`/`bwrap` get `bwrap: setting up uid map: Permission denied` unless an AppArmor profile grants `userns` to that binary. Ubuntu declined to ship a bwrap profile because bwrap can launch arbitrary binaries in a new userns [https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007, https://bugs.launchpad.net/bugs/2046477, https://etbe.coker.com.au/2024/04/24/ubuntu-24-04-bubblewrap/].
- sandbox-runtime documents the same: on Ubuntu 24.04+, disable the sysctl or add an AppArmor profile granting `userns` [README]. A Claude Code issue shows the failure mode: "sandboxed bash fails at apply seccomp on ubuntu 24.04" [https://claudeissues.com/issue/93130-sandboxed-bash-fails-at-apply-seccomp-on-ubuntu-24-04-apparmor-restrict-unprivil].
- **Fixing it needs root** (write a profile in `/etc/apparmor.d/`, or sysctl). So without admin, desktop Ubuntu 24.04+ users will land on the Landlock fallback. This makes Landlock a first-class path, not a rarity.
- **GitHub's ubuntu-24.04 runner** [U: I did not find official confirmation]. An unrelated project's CI needed to set the sysctl or load the bwrap profile, and runner-images issue #10443 reports `unshare` EPERM on 24.04 [https://github.com/actions/runner-images/issues/10443]. Assume restricted by default, and have the spike print `sysctl kernel.apparmor_restrict_unprivileged_userns` and try bwrap both ways. The runner has passwordless sudo, so CI can flip the sysctl to exercise both paths. Record it clearly as a CI-only change.

### Landlock
- ABIs [https://docs.kernel.org/userspace-api/landlock.html]:

  | ABI | Kernel | Adds |
  |---|---|---|
  | 1 | 5.13 | FS rules |
  | 2 | | REFER |
  | 3 | | TRUNCATE |
  | 4 | 6.7 | TCP bind/connect port rules |
  | 5 | | IOCTL_DEV |
  | 6 | 6.12 | abstract unix socket + signal scoping |
  | 7 | | audit flags |
  | 8 | | TSYNC (multithreaded enforcement) |
  | 9 | | pathname unix sockets |
  | 10 | | UDP rules |
  | 11 | | RESTRICT_SELF_NO_NEW_PRIVS |
  | 12 | | capability/namespace permissions |

  The doc page gives kernel versions only for ABI 1, 4 and 6. Exact kernel versions for the others weren't in the fetched text [U].
- Unprivileged, but the process must `prctl(PR_SET_NO_NEW_PRIVS, 1)` first (ABI ≤10), and Landlock must be in `CONFIG_LSM`/`lsm=`.
- **Landlock net rules cover TCP only until ABI 10 (UDP).** So with ABI 4–9, UDP and raw sockets are not controlled. For the "no network" requirement on such kernels, add a seccomp filter denying `socket(AF_INET/AF_INET6)`, or accept the limit and report `sandbox_level` lower. Also unix sockets: use scoping (ABI 6) and don't grant the dirs holding them.
- **Detection:**
  - `landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)` (syscall 444 on x86_64 and arm64). It returns the ABI number. ENOSYS means unsupported, and EOPNOTSUPP means disabled at boot.
  - From Python: `ctypes.CDLL(None).syscall(444, None, 0, 1)`.
  - Also read `/sys/kernel/security/lsm` for "landlock".
- **GitHub runner kernels:**
  - ubuntu-24.04 images used Azure kernel **6.17.0-1018/-1020/-1022** in Jun–Aug 2026 [S: runner-images release notes]. The current Ubuntu2404-Readme lists Ubuntu 24.04.5 with kernel **7.0.0-1017-azure** [S: https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md]. Either way it is above 6.12, so ABI 6+ is expected, and on 7.0 probably higher [U]. The spike must print the actual ABI. Whether the Azure kernel has Landlock in its LSM list is also to be confirmed by the spike.
  - ubuntu-24.04-arm: kernel not checked [U]. It is likely the same kernel family.
  - Ubuntu 24.04 GA kernel (6.8) gives ABI 4. Many users run older kernels, so TCP rules (6.7+) are not guaranteed. That's why `sandbox_level` must report the ABI.

## 4. macOS
- `sandbox-exec` has been deprecated (man page) for years but still ships and works. A March 2026 report found it fully functional on Sequoia 15.x [https://gastown.dev/docs/research/macos-sandbox-exec]. A May 2026 Claude Code issue on macOS 26.4.1 found the primitive intact (minimal profiles work), and the failure was in the generated profile [https://claudeissues.com/issue/55849-sandbox-fails-to-initialize-on-macos-26-tahoe-forces-dangerouslydisablesandbox-p]. The macOS 26 runner image is 26.6.2 [S: runner-images macos-26-arm64/20261026.0455]. I couldn't find an Apple statement about removal [U].
- Unprivileged: yes. It needs no root. Apple calls the SBPL interface internal, so profiles may break across OS versions. Hence the CI matrix on macos-15 and macos-15-intel, and possibly macos-26 later.
- The nested-sandbox limit: a process already inside a Seatbelt sandbox can't apply another one in some cases (`sandbox_apply: Operation not permitted`). If the app is launched from a sandboxed host, detection must catch it [U].
- Profile basics (SBPL):
  ```
  (version 1)
  (deny default)
  (allow process-exec) (allow process-fork)
  (allow file-read*)              ; or a tight subpath allowlist
  (deny file-read* (subpath "/Users/<u>/.ssh"))
  (allow file-write* (subpath "<workdir>"))
  (deny network*)                 ; or (allow network-outbound (remote ip "localhost:<proxy>"))
  ```
  - `(deny default)` is the safest start but needs many `allow` rules for dyld and system libs. sandbox-runtime does the reverse: it allows all reads and denies specific paths.
  - For our native kernel, `deny network*` is simpler than a proxy. The proxy is only needed if the kernel needs allow-listed network. The spec says "deny-all localhost proxy", so ask whether a proxy is needed at all.
- Good tests: `cat ~/.ssh/id_rsa`-style read, write outside, `curl`, and spawning `/bin/sh` if process-exec is not wanted.

## 5. Windows
- **AppContainer from plain ctypes, no admin [V]** (Windows 10 19045, non-admin user):
  1. `userenv.CreateAppContainerProfile(name, ...)` returned an AppContainer SID. It returned HRESULT 0x800700B7 (already exists) on the next call, and `DeriveAppContainerSidFromAppContainerName` fixes that.
  2. `SECURITY_CAPABILITIES{AppContainerSid, Capabilities=NULL, CapabilityCount=0}` is passed via `UpdateProcThreadAttribute(PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x20009)`.
  3. `CreateProcessW` with `EXTENDED_STARTUPINFO_PRESENT (0x80000)`.
  4. Cleanup: `DeleteAppContainerProfile` returned 0.
  - Results with `C:\Program Files\Python312\python.exe` as the child (exit code 1 = the attempted operation failed, which is what we want):

    | Test | Inside AppContainer | Control outside |
    |---|---|---|
    | sanity `sys.exit(0)` | exit 0 | |
    | `socket.connect_ex(("1.1.1.1",443))` | blocked (exit 1) | 0 (connected) |
    | write `C:\Users\<me>\ac_out.txt` | blocked, file not created | |
    | read `~\.gitconfig` | blocked | |

    Caveat: my exit-code harness doesn't capture the exception text, so each block is inferred from the exit code and from the control. The interpreter itself ran, so the failures weren't startup failures. The final spike should have the child write a result to a pipe or an allowed dir.
  - **Practical hurdles:**
    - The child exe and its libraries must be readable by `ALL APPLICATION PACKAGES` or by the container SID. `Program Files` is readable by default. Interpreters in a user profile or venv are not, so grant read ACLs on the runtime dir via `icacls /grant "*S-1-15-2-1:(RX)"` or `SetNamedSecurityInfo`. Changing ACLs on files you own needs no admin.
    - Grant the container SID write on the workdir only.
    - `CREATE_BREAKAWAY_FROM_JOB`, stdio handle inheritance (use pipes with `STARTF_USESTDHANDLES` and inheritable handles), and Python/Deno DLL loads need testing.
    - AppContainer blocks loopback by default too (a plus). Learn about `CheckNetIsolation LoopbackExempt` only if a proxy is needed. Not required for no-net.
- **Job Object limits** (unelevated): `JOB_OBJECT_LIMIT_ACTIVE_PROCESS` (cap/forbid children), `PROCESS_MEMORY`/`JOB_MEMORY`, `JOB_TIME`, `KILL_ON_JOB_CLOSE`, plus UI restrictions. All via ctypes. The AGENTS.md note about the venv launcher (`sys.executable` is a launcher, kill-on-close jobs chain) applies. A job limit of 1 active process would break the launcher.
- **Restricted token** (`CreateRestrictedToken` + low integrity): unprivileged. Strong on file writes (low integrity can't write to medium-integrity objects) but **doesn't block network**. This is why Codex's unelevated mode relies on weak env-level controls. Our `sandbox_level` for "restricted token only" must therefore say `network=not_enforced`.
- **Recommended Windows plan:** unelevated default = AppContainer (no capabilities) + Job Object. This beats the spec's "restricted token + ACLs" for network. Optional strong mode = sandbox user + WFP/firewall (admin, one UAC), if still wanted. Alternatively, defer to Codex MXC style on Windows 11 24H2+ [U for how to call it].
- GitHub's `windows-2025` runner (OS 10.0.26100, image 20261021) [S: Windows2025-Readme] runs as an **admin** user [U], so "unelevated" probes in CI must explicitly de-elevate or be run from an AppContainer or restricted token. Otherwise CI would pass tests a real user would fail.

## 6. Spike design for a GitHub Actions matrix

### Matrix
`windows-2025`, `ubuntu-24.04`, `ubuntu-24.04-arm`, `macos-15`, `macos-15-intel`. (Add `windows-11-arm` and `macos-26` as non-blocking extras if cheap. The macos-15 image README fetch was ambiguous about arch [U]. Print `uname -m` in the job.)

### Job A (blocking): Deno + Pyodide on every OS
Establish:
1. `uv add deno==2.9.8` or `uv run --with deno` in a venv, and resolve the binary without `find_deno_bin()` assumptions.
2. Warm-up with network on: caches `npm:pyodide@314.0.7` and the numpy, pandas and matplotlib wheels.
3. Run a runner script with the minimal flags in §1 and **no net**. Assert numpy, pandas and matplotlib work, and record `loadPyodide` ms, `loadPackage` ms, total ms and RSS.
4. Security tests that **must FAIL inside the cell**:
   - network: `js.fetch("https://example.com")` and `socket.connect` (NotCapable or error)
   - subprocess: `ctypes.CDLL(None).system(b"touch <outside>/pwn")`, `emscripten_run_script_string("Deno.Command...")`, and `os.system`. Assert the sentinel file does not exist after.
   - write outside: `emscripten_run_script_string` calling `Deno.writeTextFileSync(<outside>)`, and `open()` on a mounted dir to a symlink pointing outside
   - read secret: put a decoy file `~/.ssh/id_rsa_decoy` (or a temp "secret" file outside the allowed dir) and attempt to read it via both `emscripten_run_script` and `mountNodeFS` path traversal (`../`)
   - FFI: `Deno.dlopen` via the `js` bridge
   - env: `Deno.env.get("HOME")`/`GITHUB_TOKEN` (must be NotCapable)
   - a Deno-side permission query: `Deno.permissions.query` for net/run/ffi/env/sys must all be "denied"
   - timeout/memory: infinite loop and a 4 GB allocation get killed by the supervisor (Job Object / rlimit)
5. A **positive control** for each negative test (same operation on an allowed path succeeds), so a harness bug can't produce a false pass.

### Job B (non-blocking probes, per OS) writing `sandbox_level`
Each probe runs the same four tests inside the OS mechanism and prints a JSON blob. Tests must fail inside: write outside workdir, `connect()` to a local listener started by the harness (use a loopback listener so CI is hermetic), spawn `/bin/sh` or `cmd` and `curl`, read a decoy `~/.ssh` file. Each also has positive controls.

| OS | Probes |
|---|---|
| ubuntu-24.04, ubuntu-24.04-arm | (1) `uname -r`, `/sys/kernel/security/lsm`, `sysctl kernel.apparmor_restrict_unprivileged_userns`. (2) Landlock ABI via syscall 444. (3) `bwrap --unshare-all ... true` as is, then with sudo sysctl=0 (CI only), then with the AppArmor bwrap profile. (4) Landlock ruleset (FS + TCP if ABI ≥4) applied in a forked child, running the four tests. (5) With seccomp if feasible. |
| macOS (both) | `sandbox-exec -p '<profile>'` running the four tests. Also test nested use (inside another sandbox-exec). Print `sw_vers`. |
| windows-2025 | AppContainer spike (as in §5) running the four tests, with de-elevated or non-admin context noted. Job Object active-process limit test (and the launcher-chain issue). Also check `CreateRestrictedToken` network behaviour as a control (expect network not blocked). |

### `sandbox_level` reporting (suggest)
`doctor` and the kernel start path return a structure, not one string:
```
{ "mechanism": "bwrap+seccomp" | "landlock" | "seatbelt" | "appcontainer+job" | "none",
  "fs_write": "enforced" | "not_enforced", "fs_read_deny": ..., "network": "enforced" | "tcp_only" | "not_enforced",
  "process_spawn": ..., "landlock_abi": 6, "notes": [...] }
```
Level: `strong` = all enforced, `partial` = FS enforced but network is tcp_only or similar, and `none` = **kernel disabled** (fail closed). Pyodide-in-Deno is always available and reports `deno_permissions`. Establish the level by running a **self-test inside the real mechanism at start** (try a forbidden connect and a forbidden write and require both to fail). Don't infer it from "the API call succeeded".

### Step ordering
- **Step 1:** Job A as a full, blocking spike with the escape tests and numbers, plus Job B as probes only, producing the ADR with the per-OS table and `sandbox_level` schema. Install via the `deno` wheel. This is the default path for all users and is already proven locally on Windows.
- **Step 7/10:** build the kernel sandbox. Windows AppContainer, Linux bwrap/Landlock and macOS Seatbelt each need real engineering (ACLs, userns, fixes for toolchain quirks) that doesn't help the step-1 goal.
- Open questions the spike must answer: userns state on the GH runner; Landlock ABI on the Azure 7.0 kernel; Deno `--cached-only` plus `packageCacheDir` on every OS; whether `mountNodeFS` honours Deno read/write scoping; Windows non-admin de-elevation in CI.

## Sources
- Deno releases API: https://api.github.com/repos/denoland/deno/releases/latest (v2.9.8, 2026-10-21)
- PyPI deno: https://pypi.org/project/deno/ and https://pypi.org/pypi/deno/2.9.8/json
- Pyodide releases: https://api.github.com/repos/pyodide/pyodide/releases/latest (314.0.7, 2026-09-14); docs https://pyodide.org/en/stable/usage/index.html
- Deno security: https://docs.deno.com/runtime/fundamentals/security/
- Cyera: https://www.cyera.com/research/sandcastles-not-sandboxes-how-one-architectural-flaw-exposed-seven-products ; https://www.esecurityplanet.com/threats/def-con-34-one-pyodide-flaw-exposed-seven-products/
- sandbox-runtime: https://github.com/anthropic-experimental/sandbox-runtime ; releases API (v0.0.81, 2026-10-20) ; npm registry 0.0.81
- Codex: https://github.com/openai/codex (Apache-2.0 via API); linux-sandbox README https://raw.githubusercontent.com/openai/codex/main/codex-rs/linux-sandbox/README.md ; Windows sandbox https://learn.chatgpt.com/docs/windows/windows-sandbox
- Python Landlock: https://pypi.org/project/landlockpy/ ; https://pypi.org/project/py-landlock/ ; https://pypi.org/project/landlock
- Landlock docs: https://docs.kernel.org/userspace-api/landlock.html
- Ubuntu userns: https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007 ; https://bugs.launchpad.net/bugs/2046477 ; https://etbe.coker.com.au/2024/04/24/ubuntu-24-04-bubblewrap/ ; https://github.com/actions/runner-images/issues/10443
- Runner images: https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md (24.04.5, kernel 7.0.0-1017-azure) ; https://github.com/actions/runner-images/blob/main/images/windows/Windows2025-Readme.md ; https://github.com/actions/runner-images/releases/tag/ubuntu24%2F20260823.283
- macOS sandbox-exec: https://jmmv.dev/2019/11/macos-sandbox-exec.html ; https://gastown.dev/docs/research/macos-sandbox-exec
- AppContainer: https://learn.microsoft.com/en-us/windows/desktop/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute ; https://blahcat.github.io/2020-12-29-cheap-sandboxing-with-appcontainers
- Licences by GitHub API: nsjail Apache-2.0, firejail GPL-2.0, codex Apache-2.0.

## Local probe artifacts
Scratch dir: C:\Users\SAAD~1.KAZ\AppData\Local\Temp\claude\c--Users-saad-kazmi-Desktop-Researchly\cf262916-4f85-4150-ba91-25fd70803812\scratchpad\spike-sandbox (t1.ts, t2.ts, ac.py, pkgcache/, deno_dir/). Nothing was installed globally. The repo was not touched. The AppContainer profile was deleted afterwards.
