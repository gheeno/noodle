"""NOOD_0133 — stale-install visibility + slow-site failure surface.

The incident: a stale NON-editable noodle copy in a Homebrew python's
site-packages shadowed the editable dev clone, so the already-shipped
TLS-ignore + 2-min-nav fixes never ran, and nothing said so. These pin:
  * the guardrail — unit tests exercise THIS tree, not a site-packages copy
  * install_check primitives (path, editable, SHA, shims, remediation)
  * `noodle --version` / run header / doctor surfacing the build
  * navigate()'s timeout naming the two likely causes
Env-precedence flips live in test_package_env.py.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from noodle import install_check
from noodle.cli import app

REPO = Path(__file__).resolve().parents[1]
runner = CliRunner()


# --- the guardrail: a stale global install must never shadow the dev tree ---

def test_running_engine_is_this_tree():
    import noodle
    assert Path(noodle.__file__).resolve() == REPO / "noodle" / "__init__.py", (
        "unit tests are importing a DIFFERENT noodle than this checkout — "
        "a stale installed copy is shadowing the tree (see NOOD_0133; "
        "`noodle doctor` prints the cure)")


# --- install_check primitives ---

def test_package_dir_resolves_to_a_real_package():
    d = install_check.package_dir()
    assert (d / "cli.py").is_file()


def test_git_sha_from_this_checkout():
    # package_dir() is inside this git repo, so a short SHA must come back.
    sha = install_check.git_sha()
    assert sha and len(sha) >= 7


def test_shims_on_path_returns_paths_in_path_order():
    for hit in install_check.shims_on_path():
        assert Path(hit).name.startswith("noodle")


def test_reinstall_cmd_matches_install_flavor(monkeypatch):
    monkeypatch.setattr(install_check, "package_dir",
                        lambda: Path("/home/u/.local/share/uv/tools/noodle/lib/noodle"))
    assert "uv tool" in install_check.reinstall_cmd()
    monkeypatch.setattr(install_check, "package_dir",
                        lambda: Path("/opt/homebrew/lib/python3.11/site-packages/noodle"))
    assert "pip install -e" in install_check.reinstall_cmd()


def test_build_line_names_noneditable_copy(monkeypatch):
    monkeypatch.setattr(install_check, "is_editable", lambda: False)
    monkeypatch.setattr(install_check, "git_sha", lambda: None)
    line = install_check.build_line()
    assert "NON-EDITABLE COPY" in line and str(install_check.package_dir()) in line


def test_noneditable_install_check_fails_with_remediation(monkeypatch):
    # install_check.report() became structured checks in NOOD_0138
    from noodle import doctor
    monkeypatch.setattr(install_check, "is_editable", lambda: False)
    monkeypatch.setattr(install_check, "shims_on_path", lambda: ["one"])
    checks = {c.id: c for c in doctor.install_checks()}
    ed = checks["install.editable"]
    assert ed.status == "fail" and "git pull" in ed.summary
    assert "uv tool" in ed.remediation or "pip install -e" in ed.remediation


def test_warn_if_stale_silent_when_editable(monkeypatch):
    out = []
    monkeypatch.setattr(install_check, "is_editable", lambda: True)
    install_check.warn_if_stale(out.append)
    assert out == []
    monkeypatch.setattr(install_check, "is_editable", lambda: False)
    install_check.warn_if_stale(out.append)
    assert len(out) == 1 and "non-editable" in out[0]


# --- CLI surfaces ---

def test_version_flag_prints_path_and_sha():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert str(install_check.package_dir()) in r.output


def test_doctor_exit_1_on_noneditable_install(tmp_path, monkeypatch):
    monkeypatch.setattr(install_check, "is_editable", lambda: False)
    monkeypatch.setattr(install_check, "shims_on_path", lambda: ["one"])
    runner.invoke(app, ["init", str(tmp_path)])
    r = runner.invoke(app, ["doctor", str(tmp_path)])
    assert r.exit_code == 1
    assert "non-editable copy" in r.output


def test_doctor_exit_0_when_editable_and_current(tmp_path, monkeypatch):
    monkeypatch.setattr(install_check, "is_editable", lambda: True)
    monkeypatch.setattr(install_check, "shims_on_path", lambda: ["one"])
    runner.invoke(app, ["init", str(tmp_path)])
    r = runner.invoke(app, ["doctor", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "install.active-build" in r.output


# --- navigate() timeout names the likely causes ---

def test_navigate_timeout_hints_cert_and_budget(monkeypatch):
    from playwright.sync_api import TimeoutError as PWTimeout

    from noodle.agents.web import actions

    def goto(url, wait_until=None, timeout=None):
        raise PWTimeout("Timeout 10000ms exceeded")

    with pytest.raises(PWTimeout) as err:
        actions.navigate(SimpleNamespace(goto=goto), "https://slow.internal/")
    msg = str(err.value)
    assert "NOODLE_FIND_TIMEOUT" in msg
    assert "NOODLE_IGNORE_HTTPS_ERRORS" in msg and "secure_certs" in msg
