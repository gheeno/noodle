"""NOOD_0116 — noodle run --quiet diverts the behave stream to run.log.

The cost diagnosis behind the flag: an agent-driven run's biggest resident
context blob is the full behave console stream. --quiet sends it to
<artifacts>/run.log and prints only the summary; the default path stays
byte-for-byte untouched. Behave itself is stubbed — no browser, no run."""
from unittest.mock import MagicMock

from typer.testing import CliRunner

from noodle import cli

runner = CliRunner()


def _stub_run_env(monkeypatch, tmp_path, record):
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    monkeypatch.setattr(cli, "_resolve_run_target",
                        lambda w, p: (str(ws), p or "tests"))
    monkeypatch.setattr(cli.config, "load",
                        lambda w: {"tests_dir": "tests", "browser": "chromium",
                                   "headless": True})
    monkeypatch.setattr(cli, "_app_report_dir", lambda c, p: None)
    monkeypatch.setattr(cli._paths, "record_last_run_root", lambda c: None)
    monkeypatch.setattr(cli, "_write_last_run", lambda *a, **k: None)

    def fake_run(args, **kw):
        record.update(kw)
        return MagicMock(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return ws


def test_run_quiet_diverts_stream_and_prints_summary(monkeypatch, tmp_path):
    record = {}
    ws = _stub_run_env(monkeypatch, tmp_path, record)
    result = runner.invoke(cli.app, ["run", "tests", "-w", str(ws), "--quiet"])
    assert result.exit_code == 0
    # behave's stdout went to a file handle, stderr folded into it
    assert record.get("stdout") is not None
    assert record.get("stderr") == cli.subprocess.STDOUT
    log = ws / cli._paths.artifacts_root() / "run.log"
    assert log.exists()
    assert "Run summary" in result.output
    assert "run.log" in result.output


def test_run_default_stream_untouched(monkeypatch, tmp_path):
    # NOOD_0117 — non-TTY callers now default to quiet, so pinning the
    # verbose path needs the explicit human override.
    monkeypatch.setenv("NOODLE_QUIET", "0")
    record = {"stdout": "unset"}
    ws = _stub_run_env(monkeypatch, tmp_path, record)
    result = runner.invoke(cli.app, ["run", "tests", "-w", str(ws)])
    assert result.exit_code == 0
    assert record["stdout"] == "unset"      # no capture kwargs passed
    assert not (ws / cli._paths.artifacts_root() / "run.log").exists()
