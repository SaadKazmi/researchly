"""Kernel-sandbox feasibility probe (spec 19.4 / 26 risk 3, ADR 0006). Stdlib only.

Prints one JSON object describing what this OS's unprivileged sandbox primitive can
deny: write outside the workdir, read a decoy file, loopback TCP, spawning a process.
Each deny test runs a child that tries one operation. The child exits 0 if it worked and
EXIT_OP_FAILED (7) if the operation raised. A positive control (same child, unsandboxed)
must exit 0, and the sandbox must still start the interpreter, or the result is "skipped".
This is a probe, not the sandbox: the real one is built at step 7/10.
"""

# ruff: noqa: S603, S607  (fixed argv built in-script, no user input)
import ctypes
import errno
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OP_FAILED = 7
SPAWN_CMD = [os.environ.get("COMSPEC", "cmd.exe"), "/c", "exit 0"] if os.name == "nt" else ["/bin/sh", "-c", "true"]


def child_code(op: str, outside: str, port: int, work: str) -> str:
    body = {
        "write_outside": f"open({os.path.join(outside, 'w.txt')!r}, 'w').write('x')",
        "read_decoy": f"open({os.path.join(outside, 'decoy.txt')!r}).read()",
        "network": f"socket.create_connection(('127.0.0.1', {port}), 3)",
        "spawn": f"subprocess.run({SPAWN_CMD!r}, check=True, capture_output=True)",
        "work_write": f"open({os.path.join(work, 'ok.txt')!r}, 'w').write('x')",
    }[op]
    # Only OSError/SubprocessError mean "the operation was refused"; anything else (ImportError...) exits 1.
    return (
        "import socket, subprocess, sys\n"
        f"try:\n    {body}\n"
        "except (OSError, subprocess.SubprocessError) as e:\n"
        f"    print(repr(e), file=sys.stderr)\n    sys.exit({EXIT_OP_FAILED})\n"
    )


SANITY = "import socket, subprocess"  # the sandboxed interpreter must at least start and import these


def stdout(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except subprocess.TimeoutExpired:
        return "timeout"


def run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    return p.returncode, p.stderr.strip()[-300:]


def deny_tests(sandboxed, sanity_ok: bool, checks: dict, notes: list[str]) -> dict[str, str]:
    """sandboxed(code, outside, work) -> (exit_code, stderr_tail). Runs the controls and the sandboxed children."""
    results: dict[str, str] = dict.fromkeys(("write_outside", "read_decoy", "network", "spawn"), "skipped")
    if not sanity_ok:
        notes.append("sandboxed interpreter could not start; all deny tests skipped")
        return results
    with tempfile.TemporaryDirectory(prefix="rprobe-") as tmp:
        outside = os.path.realpath(os.path.join(tmp, "outside"))
        work = os.path.realpath(os.path.join(tmp, "work"))
        os.mkdir(outside)
        os.mkdir(work)
        Path(outside, "decoy.txt").write_text("decoy")
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(8)  # backlog completes the handshake, no accept thread needed
            port = srv.getsockname()[1]
            rc, _ = sandboxed(child_code("work_write", outside, port, work), outside, work)
            checks["work_writable"] = rc == 0
            if rc != 0:
                notes.append(f"work dir not writable under the sandbox (exit {rc}); denials may be over-broad")
            for op in results:
                code = child_code(op, outside, port, work)
                rc, err = run([sys.executable, "-c", code])
                if rc != 0:
                    notes.append(f"{op}: positive control failed (exit {rc}): {err}")
                    continue
                rc, err = sandboxed(code, outside, work)
                results[op] = "allowed" if rc == 0 else "denied" if rc == EXIT_OP_FAILED else "skipped"
                if results[op] == "skipped":
                    notes.append(f"{op}: sandboxed child failed unrelated to the operation (exit {rc}): {err}")
                elif results[op] == "denied":
                    notes.append(f"{op}: denied (exit 7, OSError raised): {err or 'stderr not captured'}")
    return results


# ---------------------------------------------------------------- Linux

LANDLOCK_BOOT = r"""
import ctypes, os, sys
abi, work, code = int(sys.argv[1]), sys.argv[2], sys.argv[3]
libc = ctypes.CDLL(None, use_errno=True)
fs_all = (1 << 13) - 1
if abi >= 2: fs_all |= 1 << 13
if abi >= 3: fs_all |= 1 << 14
if abi >= 5: fs_all |= 1 << 15
READ = (1 << 0) | (1 << 2) | (1 << 3)  # execute, read file, read dir
net = (1 << 1) if abi >= 4 else 0       # connect TCP
attr = ctypes.create_string_buffer(int(fs_all).to_bytes(8, "little") + net.to_bytes(8, "little"), 16)
fd = libc.syscall(444, attr, 16 if abi >= 4 else 8, 0)
assert fd >= 0, ctypes.get_errno()
def allow(path, access):
    try: pfd = os.open(path, os.O_PATH)
    except OSError: return
    rule = ctypes.create_string_buffer(access.to_bytes(8, "little") + pfd.to_bytes(4, "little", signed=True), 12)
    assert libc.syscall(445, fd, 1, rule, 0) == 0, (path, ctypes.get_errno())
    os.close(pfd)
for p in ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", sys.base_prefix, sys.prefix]: allow(p, READ)
allow(work, fs_all)
assert libc.prctl(38, 1, 0, 0, 0) == 0
assert libc.syscall(446, fd, 0) == 0, ctypes.get_errno()
os.execv(sys.executable, [sys.executable, "-c", code])
"""


def probe_linux(checks: dict, notes: list[str]) -> dict[str, str]:
    checks["kernel"] = platform.release()
    for key, path in {
        "lsm": "/sys/kernel/security/lsm",
        "apparmor_restrict_unprivileged_userns": "/proc/sys/kernel/apparmor_restrict_unprivileged_userns",
        "unprivileged_userns_clone": "/proc/sys/kernel/unprivileged_userns_clone",
    }.items():
        if os.path.exists(path):
            checks[key] = Path(path).read_text().strip()
    abi = ctypes.CDLL(None, use_errno=True).syscall(444, None, 0, 1)
    checks["landlock_abi"] = (
        abi if abi >= 1 else f"unavailable ({errno.errorcode.get(ctypes.get_errno(), ctypes.get_errno())})"
    )
    bwrap = shutil.which("bwrap")
    if bwrap:
        checks["bwrap_version"] = stdout([bwrap, "--version"])
        rc, err = run([bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-all", "--die-with-parent", "true"])
        checks["bwrap_runs"] = {"ok": rc == 0, "stderr": err}
    else:
        checks["bwrap_version"] = None
    if not (isinstance(abi, int) and abi >= 1):
        return deny_tests(None, False, checks, notes)

    def sandboxed(code: str, outside: str, work: str) -> tuple[int, str]:
        return run([sys.executable, "-c", LANDLOCK_BOOT, str(abi), work, code])

    with tempfile.TemporaryDirectory(prefix="rprobe-sanity-") as w0:
        rc, err = sandboxed(SANITY, "", os.path.realpath(w0))
    if rc != 0:
        notes.append(f"landlock bootstrap failed (exit {rc}): {err}")
    return deny_tests(sandboxed, rc == 0, checks, notes)


# ---------------------------------------------------------------- macOS


def probe_macos(checks: dict, notes: list[str]) -> dict[str, str]:
    checks["macos"] = stdout(["sw_vers", "-productVersion"])
    checks["sandbox_exec"] = shutil.which("sandbox-exec")
    if not checks["sandbox_exec"]:
        return deny_tests(None, False, checks, notes)

    def sandboxed(code: str, outside: str, work: str) -> tuple[int, str]:
        profile = (
            "(version 1)(allow default)(deny network*)"
            f'(deny file-write* (subpath "{outside}"))(deny file-read* (subpath "{outside}"))'
            '(deny process-exec (literal "/bin/sh"))'
        )
        return run(["sandbox-exec", "-p", profile, sys.executable, "-c", code])

    return deny_tests(
        sandboxed,
        run(["sandbox-exec", "-p", "(version 1)(allow default)", sys.executable, "-c", SANITY])[0] == 0,
        checks,
        notes,
    )


# ---------------------------------------------------------------- Windows


def probe_windows(checks: dict, notes: list[str]) -> dict[str, str]:
    assert sys.platform == "win32"  # also tells pyright on other OSes that the ctypes Windows API exists here
    import ctypes.wintypes as w

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ue, shell32 = ctypes.WinDLL("userenv"), ctypes.WinDLL("shell32")
    checks["is_admin"] = bool(shell32.IsUserAnAdmin())

    class SC(ctypes.Structure):
        _fields_ = [
            ("AppContainerSid", ctypes.c_void_p),
            ("Capabilities", ctypes.c_void_p),
            ("CapabilityCount", w.DWORD),
            ("Reserved", w.DWORD),
        ]

    class SI(ctypes.Structure):
        _fields_ = [
            ("cb", w.DWORD),
            ("lpReserved", w.LPWSTR),
            ("lpDesktop", w.LPWSTR),
            ("lpTitle", w.LPWSTR),
            ("x", w.DWORD),
            ("y", w.DWORD),
            ("xs", w.DWORD),
            ("ys", w.DWORD),
            ("xc", w.DWORD),
            ("yc", w.DWORD),
            ("fill", w.DWORD),
            ("flags", w.DWORD),
            ("show", w.WORD),
            ("cbRes2", w.WORD),
            ("res2", ctypes.c_void_p),
            ("hin", ctypes.c_void_p),
            ("hout", ctypes.c_void_p),
            ("herr", ctypes.c_void_p),
        ]

    class SIEX(ctypes.Structure):
        _fields_ = [("StartupInfo", SI), ("lpAttributeList", ctypes.c_void_p)]

    class PI(ctypes.Structure):
        _fields_ = [("hProcess", ctypes.c_void_p), ("hThread", ctypes.c_void_p), ("pid", w.DWORD), ("tid", w.DWORD)]

    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, w.DWORD]
    k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.DWORD)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.TerminateProcess.argtypes = [ctypes.c_void_p, w.UINT]
    adv = ctypes.WinDLL("advapi32")
    adv.FreeSid.argtypes = [ctypes.c_void_p]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    py = getattr(sys, "_base_executable", sys.executable)  # the real interpreter, not a venv launcher
    name = f"researchly.probe.{os.getpid()}"
    sid = ctypes.c_void_p()
    hr = ue.CreateAppContainerProfile(name, name, "probe", None, 0, ctypes.byref(sid))
    if hr:
        notes.append(f"CreateAppContainerProfile hr={hr & 0xFFFFFFFF:#x}; deriving the existing SID")
        if ue.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(sid)) != 0:
            notes.append("could not obtain an AppContainer SID")
            return deny_tests(None, False, checks, notes)
    sid_str = ctypes.c_wchar_p()
    adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_str))
    sid_text = sid_str.value
    k32.LocalFree(sid_str)
    granted: set[str] = set()
    try:

        def sandboxed(code: str, outside: str = "", work: str = "") -> tuple[int, str]:
            if work and work not in granted:  # full access on the probe's own temp work dir only
                granted.add(work)
                g = subprocess.run(["icacls", work, "/grant", f"*{sid_text}:(OI)(CI)F"], capture_output=True, text=True)
                if g.returncode != 0:
                    notes.append(f"icacls grant on the work dir failed: {(g.stdout + g.stderr).strip()[-200:]}")
            sc = SC(sid, None, 0, 0)
            size = ctypes.c_size_t()
            k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            buf = ctypes.create_string_buffer(size.value)
            assert k32.InitializeProcThreadAttributeList(buf, 1, 0, ctypes.byref(size))
            assert k32.UpdateProcThreadAttribute(buf, 0, 0x20009, ctypes.byref(sc), ctypes.sizeof(sc), None, None), (
                ctypes.get_last_error()
            )
            si = SIEX()
            si.StartupInfo.cb = ctypes.sizeof(si)
            si.lpAttributeList = ctypes.addressof(buf)
            pi = PI()
            cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline([py, "-c", code]))
            try:
                if not k32.CreateProcessW(
                    None, cmd, None, None, False, 0x80000, None, sys.base_prefix, ctypes.byref(si), ctypes.byref(pi)
                ):
                    return -1, f"CreateProcessW failed, GetLastError={ctypes.get_last_error()}"
                if k32.WaitForSingleObject(pi.hProcess, 30000) == 0x102:  # WAIT_TIMEOUT: never read 259 as a result
                    k32.TerminateProcess(pi.hProcess, 1)
                    k32.CloseHandle(pi.hProcess)
                    k32.CloseHandle(pi.hThread)
                    return -1, "timeout (child terminated)"
                code_out = w.DWORD()
                k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code_out))
                k32.CloseHandle(pi.hProcess)
                k32.CloseHandle(pi.hThread)
                return code_out.value, ""
            finally:
                k32.DeleteProcThreadAttributeList(buf)

        rc, err = sandboxed(SANITY)
        if rc != 0:  # interpreter not readable by the container: grant read+execute (never write) on base prefix only
            grant = subprocess.run(
                ["icacls", sys.base_prefix, "/grant", "*S-1-15-2-1:(OI)(CI)(RX)"], capture_output=True, text=True
            )
            checks["icacls_grant"] = {"rc": grant.returncode, "out": (grant.stdout + grant.stderr).strip()[-200:]}
            rc, err = sandboxed(SANITY)
        checks["child_starts"] = rc == 0
        if rc != 0:
            notes.append(f"AppContainer child could not start python (exit {rc}): {err}")
        return deny_tests(sandboxed, rc == 0, checks, notes)
    finally:
        ue.DeleteAppContainerProfile(name)
        adv.FreeSid(sid)


def main() -> None:
    system = platform.system()
    checks: dict = {}
    notes: list[str] = []
    mechanism, fn = {
        "Linux": ("landlock", probe_linux),
        "Darwin": ("seatbelt", probe_macos),
        "Windows": ("appcontainer", probe_windows),
    }.get(system, ("none", None))
    tests = fn(checks, notes) if fn else deny_tests(None, False, checks, notes)
    print(
        json.dumps(
            {
                "os": system,
                "arch": platform.machine(),
                "mechanism": mechanism,
                "checks": checks,
                "deny_tests": tests,
                "notes": notes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
