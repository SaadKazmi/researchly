import sys
from pathlib import Path

import psutil
import pytest

from researchly_cli.state import wait_until
from researchly_cli.supervise import rotate, run_forever, spawn, supervisor_cmd, terminate_tree

SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]


def _sleepers(root: psutil.Process) -> list[psutil.Process]:
    found = []
    for p in root.children(recursive=True):
        try:
            if "time.sleep(120)" in " ".join(p.cmdline()):
                found.append(p)
        except psutil.NoSuchProcess:
            pass
    return found


def _alive(p: psutil.Process) -> bool:
    try:
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.fixture
def start(tmp_path):
    """Start a supervised sleeper; whatever happens in the test, the tree is torn down afterwards."""
    started: list[tuple[int, float]] = []

    def _start() -> tuple[psutil.Process, list[psutil.Process]]:
        sup = spawn(supervisor_cmd(tmp_path / "app.log", SLEEPER), tmp_path / "supervisor.log")
        started.append((sup.pid, sup.create_time()))
        assert wait_until(lambda: bool(_sleepers(sup)), 30, 0.2), (tmp_path / "supervisor.log").read_text()
        return sup, _sleepers(sup)

    yield _start
    for pid, created in started:
        terminate_tree(pid, created)


def test_terminate_tree_stops_supervisor_and_child(start):
    sup, kids = start()
    terminate_tree(sup.pid, sup.create_time())
    assert wait_until(lambda: not _alive(sup) and not any(_alive(k) for k in kids), 10, 0.2)


def test_child_dies_with_a_killed_supervisor(start):
    sup, kids = start()
    sup.kill()  # a crash: no clean shutdown
    if sys.platform != "win32":
        terminate_tree(sup.pid)  # POSIX: `stop` sweeps the supervisor's process group
    # Windows: the kill-on-close Job Object takes the child down by itself
    assert wait_until(lambda: not any(_alive(k) for k in kids), 10, 0.2)


def test_terminate_tree_ignores_a_reused_pid(start):
    sup, kids = start()
    terminate_tree(sup.pid, sup.create_time() - 1000)  # recorded create time does not match: not ours
    assert _alive(sup) and all(_alive(k) for k in kids)


@pytest.mark.skipif(sys.platform != "win32", reason="console windows are a Windows concern")
def test_supervisor_tree_shows_no_console_window(start):
    import ctypes
    from ctypes import wintypes

    sup, _ = start()
    user32 = ctypes.WinDLL("user32")
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    visible: list[int] = []
    pids: set[int] = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def check(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            visible.append(owner.value)
        return True

    def window_shown() -> bool:
        pids.update(p.pid for p in [sup, *sup.children(recursive=True)])
        user32.EnumWindows(check, 0)
        return bool(visible)

    assert not wait_until(window_shown, 3, 0.2), "a console window popped up for the detached supervisor"


class _Exited:
    def wait(self) -> int:
        return 1


class _Stop(Exception):
    pass


def test_backoff_doubles_to_30_and_resets_after_a_long_run(tmp_path):
    ticks = iter([0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 100])  # (start, end) per run; the 7th run lasts 94 s
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 7:
            raise _Stop

    with pytest.raises(_Stop):
        run_forever(
            ["x"], tmp_path / "app.log", popen=lambda *a, **k: _Exited(), sleep=sleep, clock=lambda: next(ticks)
        )
    assert sleeps == [2, 4, 8, 16, 30, 30, 2]


def test_rotate_keeps_one_previous_log(tmp_path):
    log = tmp_path / "app.log"
    log.write_bytes(b"x" * 11)
    rotate(log, max_bytes=10)
    assert not log.exists()
    assert (tmp_path / "app.log.1").read_bytes() == b"x" * 11


def test_rotate_is_best_effort_when_the_log_is_held_open(tmp_path, monkeypatch):
    def held_open(self, target):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(Path, "replace", held_open)
    log = tmp_path / "app.log"
    log.write_bytes(b"x" * 11)
    rotate(log, max_bytes=10)  # must not raise: a failed rotation would kill the supervisor and end restarts
    assert log.read_bytes() == b"x" * 11
