"""NOOD_0082 — re-serve / re-host reports (Allure + RCA together), serve older
archived runs, `noodle report list`, and the MCP serve/list/stop tools."""
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from noodle.cli import app
from noodle.repl import core
from noodle.reporting import builder, rca_report

runner = CliRunner()


def _make_ws(tmp_path: Path, with_allure: bool = True) -> Path:
    """A workspace with a finished run's reports tree on disk."""
    ws = tmp_path / "ws"
    reports = ws / "artifacts" / "reports"
    (ws / "artifacts" / "allure-results").mkdir(parents=True)
    reports.mkdir(parents=True)
    if with_allure:
        (reports / "allure-report").mkdir()
        (reports / "allure-report" / "index.html").write_text("<h1>allure</h1>")
    (reports / "rca.html").write_text("<h1>rca</h1>")
    return ws


# --- both reports, every run -------------------------------------------------

def test_write_reports_always_writes_both_even_when_green(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    out = rca_report.write_reports(str(results), str(tmp_path / "reports"))
    assert Path(out["rca_md"]).is_file()
    html = Path(out["rca_html"]).read_text()
    assert "No failed or errored scenarios" in html


def test_report_generate_writes_rca_even_without_allure(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path, with_allure=False)
    monkeypatch.setattr(builder, "_allure_bin", lambda: None)
    (ws / "artifacts" / "reports" / "rca.html").unlink()
    r = runner.invoke(app, ["report", "generate", "--workspace", str(ws)])
    assert r.exit_code == 1  # NOOD_0055 contract kept: no Allure build → exit 1
    assert (ws / "artifacts" / "reports" / "rca.html").is_file()
    assert (ws / "artifacts" / "reports" / "rca.md").is_file()


# --- serve target resolution ---------------------------------------------------

def test_serve_default_is_reports_root_hosting_both(tmp_path):
    from noodle.cli import _resolve_serve_target
    ws = _make_ws(tmp_path)
    target = Path(_resolve_serve_target(None, str(ws)))
    assert target == ws / "artifacts" / "reports"
    urls = builder.report_urls(str(target), "127.0.0.1", 8000)
    assert "http://127.0.0.1:8000/allure-report/index.html" in urls
    assert "http://127.0.0.1:8000/rca.html" in urls


def test_serve_default_rebuilds_missing_rca_from_results(tmp_path):
    from noodle.cli import _resolve_serve_target
    ws = _make_ws(tmp_path)
    (ws / "artifacts" / "reports" / "rca.html").unlink()
    target = Path(_resolve_serve_target(None, str(ws)))
    assert (target / "rca.html").is_file()


def test_serve_explicit_dir_is_untouched(tmp_path):
    from noodle.cli import _resolve_serve_target
    assert _resolve_serve_target(str(tmp_path), ".") == str(tmp_path)


def test_serve_explicit_reports_dir_rebuilds_when_stale(tmp_path, monkeypatch):
    """NOOD_0091 — an explicit `<app>/report/reports` path used to skip the
    NOOD_0089 staleness rebuild entirely, so `noodle report serve <path>` kept
    showing yesterday's Allure/RCA next to a freshly-run allure-results/."""
    from noodle.cli import _resolve_serve_target
    ws = _make_ws(tmp_path)
    results = ws / "artifacts" / "allure-results"
    reports = ws / "artifacts" / "reports"
    import os as _os
    for f in (reports / "rca.html", reports / "allure-report" / "index.html"):
        _os.utime(f, (1, 1))
    (results / "r-result.json").write_text("{}")
    target = Path(_resolve_serve_target(str(reports), str(ws)))
    assert target == reports
    assert (reports / "rca.html").read_text() != "<h1>rca</h1>"


def test_serve_explicit_app_root_rebuilds_and_redirects_to_reports(tmp_path):
    """Explicit target = the artifacts root itself (allure-results/ and
    reports/ as siblings, e.g. <app>/report) — resolve to reports/ and
    freshen it, instead of serving the root as-is (no allure-report there)."""
    from noodle.cli import _resolve_serve_target
    ws = _make_ws(tmp_path)
    app_root = ws / "artifacts"
    results = app_root / "allure-results"
    reports = app_root / "reports"
    import os as _os
    for f in (reports / "rca.html", reports / "allure-report" / "index.html"):
        _os.utime(f, (1, 1))
    (results / "r-result.json").write_text("{}")
    target = Path(_resolve_serve_target(str(app_root), str(ws)))
    assert target == reports
    assert (reports / "rca.html").read_text() != "<h1>rca</h1>"


def test_serve_stamp_resolves_and_extracts_archive(tmp_path):
    from noodle.cli import _resolve_serve_target
    ws = _make_ws(tmp_path)
    zip_path = ws / "archives" / "artifacts_20260713_101112.zip"
    zip_path.parent.mkdir()
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("reports/rca.html", "<h1>old rca</h1>")
        z.writestr("reports/allure-report/index.html", "<h1>old allure</h1>")
    target = Path(_resolve_serve_target("20260713_101112", str(ws)))
    assert target.name == "reports"
    assert (target / "rca.html").read_text() == "<h1>old rca</h1>"


def test_serve_zip_without_reports_falls_back_to_root(tmp_path):
    from noodle.cli import _resolve_serve_target
    zip_path = tmp_path / "odd.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("something.txt", "x")
    target = Path(_resolve_serve_target(str(zip_path), str(tmp_path)))
    assert (target / "something.txt").is_file()


def test_serve_missing_archive_is_a_clear_error(tmp_path):
    import typer

    from noodle.cli import _resolve_serve_target
    with pytest.raises(typer.BadParameter):
        _resolve_serve_target("19990101_000000", str(tmp_path))


def test_report_urls_empty_dir_falls_back_to_root_listing(tmp_path):
    assert builder.report_urls(str(tmp_path), "127.0.0.1", 9) == ["http://127.0.0.1:9/"]


# --- background server (MCP path) ----------------------------------------------

def test_no_in_process_server_thread_survives(tmp_path):
    """NOOD_0162 — the daemon-thread server this file used to round-trip is
    deleted: NOOD_0161 made every hosting path a detached child, so an
    in-process one would hand out URLs that die with the process."""
    assert not hasattr(builder, "start_report_server")
    assert not hasattr(builder, "_SERVERS")
    assert "stopped_ports" not in core.stop_report_servers(str(tmp_path))


def test_core_serve_report_rebuilds_rca_and_binds_localhost(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    (ws / "artifacts" / "reports" / "rca.html").unlink()
    monkeypatch.setattr(builder, "_allure_bin", lambda: None)
    r = core.serve_report(workspace=str(ws), port=0)
    try:
        assert r["ok"] is True
        assert any(u.endswith("/rca.html") for u in r["urls"])
        assert (ws / "artifacts" / "reports" / "rca.html").is_file()
    finally:
        core.stop_report_servers()


def test_core_serve_report_explicit_dir_rebuilds_when_stale(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    results = ws / "artifacts" / "allure-results"
    reports = ws / "artifacts" / "reports"
    import os as _os
    for f in (reports / "rca.html", reports / "allure-report" / "index.html"):
        _os.utime(f, (1, 1))
    (results / "r-result.json").write_text("{}")
    monkeypatch.setattr(builder, "_allure_bin", lambda: None)
    r = core.serve_report(workspace=str(ws), report_dir=str(reports), port=0)
    try:
        assert r["ok"] is True
        assert (reports / "rca.html").read_text() != "<h1>rca</h1>"
    finally:
        core.stop_report_servers()


def test_core_serve_report_errors_when_nothing_to_serve(tmp_path):
    r = core.serve_report(workspace=str(tmp_path / "empty"))
    assert r["ok"] is False
    assert "no reports" in r["error"]


# --- report list ----------------------------------------------------------------

def test_list_reports_shows_live_and_archives(tmp_path):
    ws = _make_ws(tmp_path)
    (ws / "archives").mkdir()
    (ws / "archives" / "artifacts_20260713_101112.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    data = core.list_reports(str(ws))
    assert data["live"]["allure"] and data["live"]["rca"]
    assert data["archives"][0]["stamp"] == "20260713_101112"


def test_cli_report_list_json(tmp_path):
    ws = _make_ws(tmp_path)
    r = runner.invoke(app, ["report", "list", "--workspace", str(ws), "--json"])
    assert r.exit_code == 0
    assert '"allure": true' in r.output


def test_cli_report_list_empty_workspace_hints(tmp_path):
    r = runner.invoke(app, ["report", "list", "--workspace", str(tmp_path)])
    assert r.exit_code == 0
    assert "none" in r.output


# --- MCP tools --------------------------------------------------------------------

def test_mcp_serve_and_stop_tools(tmp_path, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    ws = _make_ws(tmp_path)
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    info = server.serve_report()
    try:
        assert info["ok"] is True
        assert info["host"] == "127.0.0.1"
    finally:
        stopped = server.stop_report_server()
    # NOOD_0161 — the server is a detached child now (its URLs must survive
    # this MCP server restarting), so stop goes through `noodle report stop`.
    assert str(info["port"]) in stopped["detached"]


def test_mcp_list_reports_tool(tmp_path, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    ws = _make_ws(tmp_path)
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    assert server.list_reports()["live"]["rca"] is True


def test_run_and_report_carries_rca_paths(tmp_path, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    ws = _make_ws(tmp_path)
    (ws / "noodle.yaml").write_text("tests_dir: tests\n")
    (ws / "tests").mkdir()
    (ws / "tests" / "x.feature").write_text("Feature: F\n")
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    monkeypatch.setattr(core, "run_test", lambda *a, **k: {"ok": True})
    # NOOD_0131 — paths point into the freshness-checked reports root the run
    # hook already built, not a second build's return value.
    r = server.run_and_report("x")
    assert r["rca_html"].endswith("rca.html") and r["rca_md"].endswith("rca.md")
    assert r["report"].endswith("index.html")
