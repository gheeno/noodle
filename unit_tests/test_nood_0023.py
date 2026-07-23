"""NOOD_0023 — consolidated artifacts/ tree: network/log capture, auto-RCA,
and the noodle clean/archive/artifacts CLI commands."""
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# paths.py — new category helpers
# ---------------------------------------------------------------------------

def test_network_and_logs_dir_default(monkeypatch):
    from noodle.reporting import paths
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    assert str(paths.network_dir()) == "artifacts/network"
    assert str(paths.logs_dir()) == "artifacts/logs"


def test_network_and_logs_dir_respect_artifacts_root(monkeypatch):
    from noodle.reporting import paths
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", "out")
    assert str(paths.network_dir()) == "out/network"
    assert str(paths.logs_dir()) == "out/logs"


# ---------------------------------------------------------------------------
# log.py — sys log file handler
# ---------------------------------------------------------------------------

def test_attach_file_handler_writes_log_messages(tmp_path):
    from noodle import log
    target = tmp_path / "logs" / "noodle.log"
    log.attach_file_handler(str(target))
    try:
        log.logger.info("hello from the sys log")
        for h in log.logger.handlers:
            h.flush()
        assert "hello from the sys log" in target.read_text()
    finally:
        log.logger.removeHandler(log._file_handler)
        log._file_handler = None


def test_attach_file_handler_replaces_previous(tmp_path):
    from noodle import log
    first = tmp_path / "a.log"
    second = tmp_path / "b.log"
    log.attach_file_handler(str(first))
    log.attach_file_handler(str(second))
    try:
        assert log._file_handler.baseFilename == str(second)
        assert sum(1 for h in log.logger.handlers if isinstance(h, __import__("logging").FileHandler)) == 1
    finally:
        log.logger.removeHandler(log._file_handler)
        log._file_handler = None


# ---------------------------------------------------------------------------
# hooks.after_scenario — network log dump, on by default, attached to Allure
# ---------------------------------------------------------------------------

def _make_scenario(name="Some Scenario"):
    return SimpleNamespace(name=name, effective_tags=[], set_status=lambda s: None)


def _base_context(**overrides):
    ctx = SimpleNamespace(
        _scenario_failed=False,
        _console_errors=None,
        _page_errors=None,
        _failed_requests=None,
        _requests=None,
        _ws_frames=None,
    )
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


@pytest.fixture(autouse=True)
def _quiet_after_scenario(monkeypatch):
    """Stub everything after_scenario touches besides the bit under test."""
    from noodle import hooks
    monkeypatch.setattr(hooks, "_run_hooks", lambda *a, **k: None)
    monkeypatch.setattr(hooks, "_allure_result", lambda ctx: None)
    from noodle import preconditions
    monkeypatch.setattr(preconditions, "run", lambda *a, **k: None)
    from noodle import app_lifecycle
    monkeypatch.setattr(app_lifecycle, "stop_all", lambda: None)


def test_network_log_written_on_failure(tmp_path, monkeypatch):
    from noodle import hooks
    from noodle.reporting import paths
    monkeypatch.chdir(tmp_path)

    ctx = _base_context(
        _scenario_failed=True,
        _console_errors=["TypeError: boom"],
        _page_errors=[],
        _failed_requests=["GET /api — net::ERR_FAILED"],
        _requests=["GET /"],
        _ws_frames=[],
    )
    hooks.after_scenario(ctx, _make_scenario("Checkout fails"))

    out = paths.network_dir() / "Checkout_fails.json"
    assert out.is_file()
    data = json.loads(out.read_text())
    assert data["console_errors"] == ["TypeError: boom"]
    assert data["failed_requests"] == ["GET /api — net::ERR_FAILED"]


def test_network_log_written_on_pass_too(tmp_path, monkeypatch):
    """Not gated on failure — a passed scenario's network activity is just as
    worth having attached to its Allure result as a failed one's."""
    from noodle import hooks
    from noodle.reporting import paths
    monkeypatch.chdir(tmp_path)

    ctx = _base_context(
        _scenario_failed=False,
        _console_errors=[],
        _page_errors=[],
        _failed_requests=[],
        _requests=["GET /"],
        _ws_frames=[],
    )
    hooks.after_scenario(ctx, _make_scenario("All good"))

    out = paths.network_dir() / "All_good.json"
    assert out.is_file()
    data = json.loads(out.read_text())
    assert data["requests"] == ["GET /"]


def test_network_log_attached_to_allure_result(tmp_path, monkeypatch):
    from noodle import hooks
    monkeypatch.chdir(tmp_path)

    class _FakeResult:
        def __init__(self):
            self.attachments = []

        def add_attachment(self, name, path, mime_type):
            self.attachments.append((name, path, mime_type))

        def finish(self, scenario):
            pass

    fake_result = _FakeResult()
    monkeypatch.setattr(hooks, "_allure_result", lambda ctx: fake_result)
    monkeypatch.setattr(hooks._writer, "write_result", lambda ar: None)

    ctx = _base_context(_scenario_failed=False, _console_errors=[], _page_errors=[],
                         _failed_requests=[], _requests=["GET /"], _ws_frames=[])
    hooks.after_scenario(ctx, _make_scenario("Attach me"))

    assert len(fake_result.attachments) == 1
    name, path, mime_type = fake_result.attachments[0]
    assert name == "network log"
    assert path.endswith("Attach_me.json")
    assert mime_type == "application/json"


def test_no_network_log_when_listeners_never_wired(tmp_path, monkeypatch):
    """@api / non-web scenarios have no page — _console_errors stays None."""
    from noodle import hooks
    from noodle.reporting import paths
    monkeypatch.chdir(tmp_path)

    ctx = _base_context(_scenario_failed=True)  # _console_errors is None
    hooks.after_scenario(ctx, _make_scenario("API scenario"))

    assert not paths.network_dir().exists()


# ---------------------------------------------------------------------------
# hooks.after_all — heuristic RCA auto-written alongside junit/healing
# ---------------------------------------------------------------------------

def test_after_all_writes_rca_when_a_scenario_failed(tmp_path, monkeypatch):
    from noodle import hooks
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hooks, "_run_hooks", lambda *a, **k: None)
    monkeypatch.setattr(hooks.healing, "write_report", lambda *a, **k: None)
    monkeypatch.setattr(hooks, "_REPORTING", True)
    monkeypatch.setattr(hooks, "_suite_results", [SimpleNamespace(result={"status": "failed"})])
    monkeypatch.setattr(hooks._junit, "write_junit", lambda *a, **k: None)
    monkeypatch.setattr(hooks._builder, "generate", lambda *a, **k: None)
    from noodle.reporting import allure_meta
    monkeypatch.setattr(allure_meta, "write_meta", lambda *a, **k: None)
    monkeypatch.setattr(hooks._rca_report, "render_markdown", lambda results_dir: "# RCA Report\nfound one")
    monkeypatch.delenv("NOODLE_PARALLEL_WORKER", raising=False)

    hooks.after_all(object())

    from noodle.reporting import paths
    rca = paths.reports_dir() / "rca.md"
    assert rca.is_file()
    assert "found one" in rca.read_text()


def test_after_all_writes_rca_even_when_nothing_failed(tmp_path, monkeypatch):
    # NOOD_0082 — a green run writes rca.md + rca.html too (the "no failures"
    # page), so `noodle report serve` always has both reports to host.
    from noodle import hooks
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hooks, "_run_hooks", lambda *a, **k: None)
    monkeypatch.setattr(hooks.healing, "write_report", lambda *a, **k: None)
    monkeypatch.setattr(hooks, "_REPORTING", True)
    monkeypatch.setattr(hooks, "_suite_results", [SimpleNamespace(result={"status": "passed"})])
    monkeypatch.setattr(hooks._junit, "write_junit", lambda *a, **k: None)
    monkeypatch.setattr(hooks._builder, "generate", lambda *a, **k: None)
    from noodle.reporting import allure_meta
    monkeypatch.setattr(allure_meta, "write_meta", lambda *a, **k: None)
    monkeypatch.delenv("NOODLE_PARALLEL_WORKER", raising=False)

    hooks.after_all(object())

    from noodle.reporting import paths
    assert (paths.reports_dir() / "rca.md").is_file()
    assert "No failed or errored scenarios" in (paths.reports_dir() / "rca.html").read_text()


# ---------------------------------------------------------------------------
# CLI — clean / archive / artifacts
# ---------------------------------------------------------------------------

def _make_artifacts_tree(root: Path):
    (root / "allure-results").mkdir(parents=True)
    (root / "allure-results" / "a-result.json").write_text("{}")
    (root / "reports").mkdir()
    (root / "reports" / "junit.xml").write_text("<testsuite/>")
    (root / "screenshots").mkdir()
    (root / "screenshots" / "FAILED_x.png").write_bytes(b"\x89PNG")


def test_clean_removes_artifacts_tree(tmp_path):
    from typer.testing import CliRunner

    from noodle.cli import app
    _make_artifacts_tree(tmp_path / "artifacts")

    result = CliRunner().invoke(app, ["clean", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert not (tmp_path / "artifacts").exists()


def test_clean_no_artifacts_is_a_noop(tmp_path):
    from typer.testing import CliRunner

    from noodle.cli import app
    result = CliRunner().invoke(app, ["clean", "--workspace", str(tmp_path)])
    assert result.exit_code == 0
    assert "Nothing to clean" in result.output


def test_archive_zips_artifacts_with_timestamp(tmp_path):
    from typer.testing import CliRunner

    from noodle.cli import app
    _make_artifacts_tree(tmp_path / "artifacts")

    result = CliRunner().invoke(app, ["archive", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    zips = list((tmp_path / "archives").glob("artifacts_*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as z:
        names = z.namelist()
    assert any("allure-results/a-result.json" in n for n in names)
    assert any("reports/junit.xml" in n for n in names)


def test_archive_without_artifacts_exits_nonzero(tmp_path):
    from typer.testing import CliRunner

    from noodle.cli import app
    result = CliRunner().invoke(app, ["archive", "--workspace", str(tmp_path)])
    assert result.exit_code != 0


def test_artifacts_lists_categories_with_counts(tmp_path):
    from typer.testing import CliRunner

    from noodle.cli import app
    _make_artifacts_tree(tmp_path / "artifacts")

    result = CliRunner().invoke(app, ["artifacts", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "allure-results/" in result.output
    assert "reports/" in result.output
    assert "screenshots/" in result.output
    assert "1 file" in result.output  # each category here has exactly one file
