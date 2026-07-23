"""NOOD_0095 — `noodle report stop` also stops ad-hoc report servers: agents
sometimes host the Allure/RCA tree with a raw `python -m http.server` instead
of `noodle report serve`, so the pidfile registry never hears about them."""
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

import pytest

from noodle import cli

needs_lsof = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("lsof") is None,
    reason="ad-hoc server discovery needs lsof (POSIX)")


def test_looks_like_report_dir(tmp_path):
    assert not cli._looks_like_report_dir(tmp_path)
    (tmp_path / "rca.html").write_text("x")
    assert cli._looks_like_report_dir(tmp_path)
    report = tmp_path / "allure-report"
    report.mkdir()
    assert cli._looks_like_report_dir(report)     # the Allure dir itself
    assert cli._looks_like_report_dir(tmp_path)   # the root holding it


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn_http_server(cwd, *extra_args):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port),
         "--bind", "127.0.0.1", *extra_args],
        cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    return port, proc


@pytest.fixture
def report_root(tmp_path):
    (tmp_path / "rca.html").write_text("<h1>rca</h1>")
    (tmp_path / "allure-report").mkdir()
    return tmp_path


@needs_lsof
def test_adhoc_discovery_finds_raw_http_server(report_root):
    port, proc = _spawn_http_server(report_root)
    try:
        assert cli._adhoc_report_servers().get(str(port)) == proc.pid
    finally:
        proc.kill()
        proc.wait()


@needs_lsof
def test_adhoc_discovery_finds_directory_flag_server(report_root, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    port, proc = _spawn_http_server(elsewhere, "--directory", str(report_root))
    try:
        assert cli._adhoc_report_servers().get(str(port)) == proc.pid
    finally:
        proc.kill()
        proc.wait()


@needs_lsof
def test_adhoc_discovery_ignores_non_report_servers(tmp_path):
    port, proc = _spawn_http_server(tmp_path)  # empty dir — not a report tree
    try:
        assert str(port) not in cli._adhoc_report_servers()
    finally:
        proc.kill()
        proc.wait()


@needs_lsof
def test_report_stop_kills_adhoc_server(report_root, capsys):
    port, proc = _spawn_http_server(report_root)
    try:
        cli.report_stop(port=None, workspace=str(report_root))
        assert proc.wait(timeout=5) == -signal.SIGTERM
        out = capsys.readouterr().out
        assert f"ad-hoc report server on port {port}" in out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@needs_lsof
def test_report_stop_port_filter_spares_other_adhoc_servers(report_root, capsys):
    port, proc = _spawn_http_server(report_root)
    try:
        cli.report_stop(port=port + 1, workspace=str(report_root))
        time.sleep(0.2)
        assert proc.poll() is None  # wrong port → left running
    finally:
        proc.kill()
        proc.wait()


def test_report_stop_nothing_running(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_adhoc_report_servers", dict)
    cli.report_stop(port=None, workspace=str(tmp_path))
    assert "nothing to stop" in capsys.readouterr().out
