import sys

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


def _start(tmp_path) -> tuple[psutil.Process, list[psutil.Process]]:
    sup = spawn(supervisor_cmd(tmp_path / "app.log", SLEEPER), tmp_path / "supervisor.log")
    assert wait_until(lambda: bool(_sleepers(sup)), 30, 0.2), (tmp_path / "supervisor.log").read_text()
    return sup, _sleepers(sup)


def test_terminate_tree_stops_supervisor_and_child(tmp_path):
    sup, kids = _start(tmp_path)
    terminate_tree(sup.pid, sup.create_time())
    assert wait_until(lambda: not _alive(sup) and not any(_alive(k) for k in kids), 10, 0.2)


def test_child_dies_with_a_killed_supervisor(tmp_path):
    sup, kids = _start(tmp_path)
    sup.kill()  # a crash: no clean shutdown
    if sys.platform != "win32":
        terminate_tree(sup.pid)  # POSIX: `stop` sweeps the supervisor's process group
    # Windows: the kill-on-close Job Object takes the child down by itself
    assert wait_until(lambda: not any(_alive(k) for k in kids), 10, 0.2)


def test_terminate_tree_ignores_a_reused_pid(tmp_path):
    sup, kids = _start(tmp_path)
    terminate_tree(sup.pid, sup.create_time() - 1000)  # recorded create time does not match: not ours
    assert _alive(sup) and all(_alive(k) for k in kids)
    terminate_tree(sup.pid, sup.create_time())


@pytest.mark.skipif(sys.platform != "win32", reason="console windows are a Windows concern")
def test_supervisor_tree_shows_no_console_window(tmp_path):
    import ctypes
    from ctypes import wintypes

    sup, _ = _start(tmp_path)
    user32 = ctypes.WinDLL("user32")
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

    try:
        assert not wait_until(window_shown, 3, 0.2), "a console window popped up for the detached supervisor"
    finally:
        terminate_tree(sup.pid, sup.create_time())


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
