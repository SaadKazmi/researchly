"""Native-mode supervision (spec §4.2).

A detached supervisor (`python -m researchly_cli.supervise LOG -- CMD...`) restarts its child with backoff.
Stopping it takes the whole tree down. On Windows the supervisor sits in a kill-on-close Job Object, so even a
crashed supervisor takes its children with it. On POSIX it leads its own process group, which `terminate_tree`
sweeps.
"""

import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

import psutil

MAX_LOG_BYTES = 10 * 1024 * 1024


def detached() -> dict[str, Any]:
    """Popen kwargs for a process that outlives the CLI and leads its own process group."""
    if sys.platform == "win32":
        # A hidden console of its own, not DETACHED_PROCESS: a console-less parent makes every console child
        # (the venv launcher's interpreter, the app) pop up a visible window that closes the tree when closed.
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        }
    return {"start_new_session": True}


def spawn(cmd: Sequence[str], log: Path, env: dict[str, str] | None = None) -> psutil.Process:
    """Start `cmd` detached, appending its output to `log`."""
    log.parent.mkdir(parents=True, exist_ok=True)
    kwargs = detached()
    with log.open("ab") as out:
        try:
            child = subprocess.Popen(list(cmd), stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env, **kwargs)  # noqa: S603
        except PermissionError:  # the CLI's own Job Object forbids breakaway (some terminals and CI hosts)
            if sys.platform != "win32" or "creationflags" not in kwargs:
                raise
            kwargs["creationflags"] &= ~subprocess.CREATE_BREAKAWAY_FROM_JOB
            child = subprocess.Popen(list(cmd), stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env, **kwargs)  # noqa: S603
    return psutil.Process(child.pid)


def supervisor_cmd(log: Path, cmd: Sequence[str]) -> list[str]:
    return [sys.executable, "-m", "researchly_cli.supervise", str(log), "--", *cmd]


def rotate(log: Path, max_bytes: int = MAX_LOG_BYTES) -> None:
    if log.exists() and log.stat().st_size > max_bytes:
        with contextlib.suppress(OSError):  # held open elsewhere (Windows): try again at the next restart
            log.replace(log.with_name(log.name + ".1"))


def run_forever(
    cmd: Sequence[str],
    log: Path,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> NoReturn:
    """Restart `cmd` whenever it exits, 2, 4, 8 … 30 s apart; a run longer than a minute resets the backoff."""
    failures = 0
    while True:
        rotate(log)
        started = clock()
        with log.open("ab") as out:
            popen(list(cmd), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT).wait()
        failures = 1 if clock() - started > 60 else failures + 1
        sleep(min(2**failures, 30))


def terminate_tree(pid: int, created: float | None = None, timeout: float = 10.0) -> None:
    """Stop a supervisor and everything under it: terminate, wait, then kill."""
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        proc = None
    if proc is not None and created is not None:
        try:
            if abs(proc.create_time() - created) > 1:  # tolerance: clock steps shift psutil's derived create time
                return  # the PID now belongs to an unrelated process
        except psutil.AccessDenied:
            return  # another user's process: not ours
    procs: list[psutil.Process] = []
    if proc is not None:
        with contextlib.suppress(psutil.NoSuchProcess):
            procs = [proc, *proc.children(recursive=True)]
    for p in procs:
        with contextlib.suppress(psutil.NoSuchProcess):
            p.terminate()
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        with contextlib.suppress(psutil.NoSuchProcess):
            p.kill()
    if sys.platform != "win32":  # orphans of a crashed supervisor are still in its process group
        # Even with the supervisor gone this is safe: POSIX never hands out a PID still in use as a process
        # group ID, and the PID is free, so any group with this ID is the supervisor's own.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)


def kill_on_close_job() -> None:
    """Put this process in a Job Object that kills every member when its last handle closes (Windows)."""
    assert sys.platform == "win32"
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", Basic),
            ("IoInfo", ctypes.c_ulonglong * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # 9: extended
        raise ctypes.WinError(ctypes.get_last_error())
    if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    # The handle is deliberately never closed: the job lives exactly as long as this process.


def main(argv: Sequence[str]) -> NoReturn:
    args = list(argv)
    sep = args.index("--")
    if sys.platform == "win32":
        kill_on_close_job()
    run_forever(args[sep + 1 :], Path(args[0]))


if __name__ == "__main__":
    main(sys.argv[1:])
