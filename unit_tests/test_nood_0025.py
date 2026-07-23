"""NOOD_0025 — regression suite session findings. No browser, no LLM, no network.

Covers: the login/search generator template POM keys (a scaffolded test for
practicetestautomation.com's login page failed end-to-end because the
"login button:" stub key can never be matched — the click pattern strips the
trailing " button" before doing the POM lookup), the Allure history-trend
seeding gap (report_dir/history/ was never copied back into results_dir/
before the next generate, so trend widgets never accumulated past one run),
and the after_scenario hook hardening (see test_hooks_hardening.py for the
KeyError-cascade coverage).
"""
import json
from pathlib import Path

from noodle.repl import generate
from noodle.reporting import builder
from noodle.resolver.patterns import match


def test_login_template_pom_key_matches_click_locator():
    """'clicks the login button' resolves to locator 'login' (the " button"
    suffix is stripped) — the template's POM stub key must be 'login', not
    'login button', or a real override in it is silently never found."""
    assert match("clicks the login button") == ("click", {"locator": "login"})
    _, pom_tpl = generate._LOGIN
    pom = pom_tpl.format(name="x")
    assert "\nlogin:\n" in pom
    assert "\nlogin button:\n" not in pom


def test_search_template_pom_key_matches_click_locator():
    assert match("clicks the search button") == ("click", {"locator": "search"})
    _, pom_tpl = generate._SEARCH
    pom = pom_tpl.format(name="x")
    assert "\nsearch:\n" in pom
    assert "\nsearch button:\n" not in pom


def test_generate_points_allure3_at_history_config(tmp_path, monkeypatch):
    """NOOD_0039: Allure 3 has no --history-path CLI flag — trend history only
    works via `historyPath` in a config file passed with --config. generate()
    must write that config and invoke allure with it, or every report starts
    a fresh 1-point trend."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        # NOOD_0055 — generate() now reads returncode to report success
        return builder.subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    # NOOD_0101 — stub the PATH lookup too: generate() skips (rightly) when
    # the allure CLI isn't installed, but this test is about the command line
    # it builds, and must not need a real allure install to run.
    monkeypatch.setattr(builder, "_allure_bin", lambda: "allure")
    results = tmp_path / "allure-results"
    results.mkdir()
    report = tmp_path / "reports" / "allure-report"

    builder.generate(str(results), str(report))

    (cmd,) = calls
    # NOOD_0052 — the binary is resolved via shutil.which (Windows allure.cmd),
    # so compare its basename, not the bare string "allure".
    assert Path(cmd[0]).name.split(".")[0] == "allure"
    assert cmd[1:3] == ["generate", str(results)]
    assert cmd[cmd.index("-o") + 1] == str(report)
    config = Path(cmd[cmd.index("--config") + 1])
    assert config.is_file()
    history = str(tmp_path / "reports" / "allure-history" / "history.jsonl")
    assert json.dumps(history) in config.read_text()
    # history dir must pre-exist or allure can't create the JSONL inside it
    assert (tmp_path / "reports" / "allure-history").is_dir()


def _artifacts_tree_with_history(root):
    history = root / "reports" / "allure-history"
    history.mkdir(parents=True)
    (history / "history.jsonl").write_text(json.dumps({"data": {"passed": 3}}))
    (root / "allure-results").mkdir()
    (root / "allure-results" / "a-result.json").write_text("{}")


def test_clean_preserves_history_by_default(tmp_path):
    """NOOD_0025/NOOD_0039: `noodle archive` alone does not persist the trend
    across a `clean` — it only zips a snapshot, nothing unzips it back. `clean`
    itself must keep reports/allure-history/ (Allure 3's JSONL history, folded
    into the next generate) while wiping everything else."""
    from typer.testing import CliRunner

    from noodle.cli import app
    _artifacts_tree_with_history(tmp_path / "artifacts")

    result = CliRunner().invoke(app, ["clean", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert not (tmp_path / "artifacts" / "allure-results").exists()
    kept = tmp_path / "artifacts" / "reports" / "allure-history" / "history.jsonl"
    assert kept.is_file()
    assert json.loads(kept.read_text()) == {"data": {"passed": 3}}


def test_clean_purge_history_removes_everything(tmp_path):
    from typer.testing import CliRunner

    from noodle.cli import app
    _artifacts_tree_with_history(tmp_path / "artifacts")

    result = CliRunner().invoke(app, ["clean", "--workspace", str(tmp_path), "--purge-history"])

    assert result.exit_code == 0
    assert not (tmp_path / "artifacts").exists()
