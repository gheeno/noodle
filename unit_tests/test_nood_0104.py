"""NOOD_0104 — agent-friendly `report serve`: the foreground server flushes
its URL lines (piped stdout is block-buffered, so an agent backgrounding the
command saw nothing until the never-arriving exit), and `--background` starts
a detached server, prints the URLs, and returns immediately."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

import pytest


@pytest.fixture
def report_root(tmp_path):
    (tmp_path / "rca.html").write_text("<h1>rca</h1>")
    return tmp_path


def _read_line(stream, timeout=15):
    """First line of `stream`, or None if nothing arrives within timeout —
    a plain readline() would hang forever on the buffered-output bug."""
    box = {}
    t = threading.Thread(target=lambda: box.update(line=stream.readline()), daemon=True)
    t.start()
    t.join(timeout)
    return box.get("line")


def test_serve_report_urls_visible_through_a_pipe(report_root):
    code = ("from noodle.reporting.builder import serve_report; "
            f"serve_report({str(report_root)!r}, '127.0.0.1', 0)")
    # A PYTHONUNBUFFERED in the surrounding shell (CI images often set it)
    # would mask the block-buffering this test exists to catch.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    proc = subprocess.Popen([sys.executable, "-c", code], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        line = _read_line(proc.stdout)
        assert line and "Serving" in line, \
            "serve_report's URL banner never reached the pipe — output is buffered"
        assert "http://127.0.0.1:" in line
    finally:
        proc.kill()
        proc.wait()


def _registry(workspace):
    f = workspace / ".noodle" / "report_servers.json"
    return json.loads(f.read_text()) if f.is_file() else {}


def _kill_registered(workspace):
    from noodle import cli as _cli
    for entry in _registry(workspace).values():
        try:
            os.kill(_cli._pid_of(entry), signal.SIGTERM)   # NOOD_0161 shape
        except OSError:
            pass


def test_background_serve_returns_with_urls(report_root):
    proc = subprocess.run(
        [sys.executable, "-m", "noodle.cli", "report", "serve", str(report_root),
         "--workspace", str(report_root), "--port", "0", "--background"],
        capture_output=True, text=True, timeout=60)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "rca.html" in proc.stdout
        url = next(w for w in proc.stdout.split() if w.startswith("http://") and w.endswith("rca.html"))
        assert b"<h1>rca</h1>" in urllib.request.urlopen(url, timeout=5).read()
        registry = _registry(report_root)
        assert registry, "detached server not registered for `noodle report stop`"
        port = url.rsplit(":", 1)[1].split("/")[0]
        assert port in registry
    finally:
        _kill_registered(report_root)


def test_background_serve_falls_back_when_port_taken(report_root):
    # NOOD_0134 — a taken port used to dead-end the serve with exit 1 and a
    # "retry with -p 0" message: one guaranteed wasted agent round-trip. Now
    # the child falls back to an OS-assigned port and the serve succeeds.
    import socket
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        proc = subprocess.run(
            [sys.executable, "-m", "noodle.cli", "report", "serve", str(report_root),
             "--workspace", str(report_root), "--port", str(port), "--background"],
            capture_output=True, text=True, timeout=60)
        try:
            assert proc.returncode == 0, proc.stdout + proc.stderr
            registry = _registry(report_root)
            assert registry and str(port) not in registry
        finally:
            _kill_registered(report_root)


@pytest.mark.skipif(sys.platform == "win32",
                    reason="probes liveness with os.kill(pid, 0), which Windows treats as terminate")
def test_background_server_outlives_the_launcher(report_root):
    subprocess.run(
        [sys.executable, "-m", "noodle.cli", "report", "serve", str(report_root),
         "--workspace", str(report_root), "--port", "0", "--background"],
        capture_output=True, text=True, timeout=60, check=True)
    try:
        from noodle import cli as _cli
        (port, entry), = _registry(report_root).items()
        pid = _cli._pid_of(entry)                          # NOOD_0161 shape
        time.sleep(0.3)  # launcher long gone; the detached child must not be
        assert b"rca" in urllib.request.urlopen(
            f"http://127.0.0.1:{port}/rca.html", timeout=5).read()
        from noodle import cli
        cli.report_stop(port=None, workspace=str(report_root))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                break
        else:
            pytest.fail("report stop did not kill the detached server")
    finally:
        _kill_registered(report_root)
