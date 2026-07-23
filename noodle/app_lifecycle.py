"""App lifecycle primitive (Phase G4, shared desktop + REST need, NOOD_0006 gap 2).

`launches the app "cmd"` starts a process; `the app should be running [on port N]`
health-checks it (process alive, or a TCP port probe); every launched process is
killed in hooks.after_scenario even when the scenario failed — the same
teardown-even-on-failure guarantee @precondition gives.
"""
import os
import shlex
import socket
import subprocess
import time

from noodle.log import logger

# Processes launched this scenario. hooks.after_scenario calls stop_all().
_procs: list[subprocess.Popen] = []


def launch(command: str) -> None:
    """Start `command` detached (stdout/stderr to the framework log fds).
    ponytail: shlex split, no shell — a pipeline needs `runs the command` instead."""
    proc = subprocess.Popen(shlex.split(command),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _procs.append(proc)
    logger.info(f"\n  🚀 Launched app: {command!r} (pid {proc.pid})")


def assert_running(port: int | None = None, timeout: float | None = None):
    """The most recently launched app is alive; with `port`, a TCP connect to
    localhost:port must succeed within the step timeout (covers servers that
    take a moment to bind)."""
    if not _procs:
        raise AssertionError("No app was launched — use 'launches the app \"...\"' first")
    proc = _procs[-1]
    secs = timeout if timeout is not None else int(os.getenv("NOODLE_TIMEOUT", "10000")) / 1000
    deadline = time.monotonic() + secs
    while True:
        alive = proc.poll() is None
        if alive and port is None:
            return
        if alive and port is not None and _port_open(port):
            return
        if not alive:
            raise AssertionError(
                f"The app exited early (pid {proc.pid}, exit code {proc.returncode})"
            )
        if time.monotonic() > deadline:
            raise AssertionError(f"The app is running but port {port} did not open within {secs:.0f}s")
        time.sleep(0.2)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop() -> None:
    """Stop the most recently launched app (explicit step)."""
    if _procs:
        _terminate(_procs.pop())


def stop_all() -> None:
    """Kill every launched process — called from after_scenario, never raises."""
    while _procs:
        _terminate(_procs.pop())


def _terminate(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            logger.info(f"\n  🛑 Stopped app (pid {proc.pid})")
    except Exception:
        pass
