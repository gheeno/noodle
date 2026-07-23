"""NOOD_0118 — examples are init-only + secret values redacted from all output.

Two fixes, both browserless and LLM-free:
  A  generate scaffolds the gitignored <app>_secrets.env, never a committed
     <app>_secrets.env.example (examples are an init-only convention)
  B  values loaded from a *secrets.env file are scrubbed from every log line
     (console, file log, and the RCA warnings buffer) — by value, not name
"""

import pytest

from noodle import log
from noodle.repl import generate


# --- B: secret-value redaction -------------------------------------------
@pytest.fixture(autouse=True)
def _clear_secrets():
    log._secret_values.clear()
    log.clear_warnings()
    yield
    log._secret_values.clear()
    log.clear_warnings()


def test_registered_secret_is_redacted_from_console(capsys):
    log.register_secret("hunter2-very-secret")
    log.logger.info("stored PWD = hunter2-very-secret in context")
    out = capsys.readouterr().out
    assert "hunter2-very-secret" not in out
    assert "***" in out


def test_secret_redacted_even_under_a_bland_var_name(capsys):
    # runner._safe_repr masks by name; a connection string stored under "DB_URL" wouldn't
    # trigger it — value-based redaction catches it anyway.
    log.register_secret("oracle://user:p4ss@db.internal:1521/appdb")
    log.logger.info("Set `DB_URL` = oracle://user:p4ss@db.internal:1521/appdb")
    out = capsys.readouterr().out
    assert "p4ss" not in out
    assert "oracle://" not in out


def test_secret_redacted_from_captured_warnings():
    # the warnings buffer feeds the RCA report — a secret must not survive there.
    log.register_secret("ghp_realtokenvalue123")
    log.logger.warning("auth failed with token ghp_realtokenvalue123")
    assert all("ghp_realtokenvalue123" not in w for w in log.get_warnings())


def test_short_and_placeholder_values_are_not_registered(capsys):
    for v in ("abc", "", "CHANGE_ME", "  ", "TODO"):
        log.register_secret(v)
    assert log._secret_values == set()
    log.logger.info("value is CHANGE_ME and code abc")  # nothing to scrub
    out = capsys.readouterr().out
    assert "CHANGE_ME" in out and "abc" in out  # untouched


def test_hooks_load_secrets_registers_file_values(tmp_path):
    from noodle import hooks
    secrets = tmp_path / "app_secrets.env"
    secrets.write_text("APP_PASSWORD=s3cr3t-from-file\nAPP_BLANK=CHANGE_ME\n")
    hooks._load_secrets(secrets)
    assert "s3cr3t-from-file" in log._secret_values
    assert "CHANGE_ME" not in log._secret_values  # placeholder skipped


# --- A: generate writes the gitignored file, not a .example --------------
def _cfg():
    return {"tests_dir": "noodle_tests"}


def test_scaffold_secrets_writes_gitignored_file_not_example(tmp_path):
    generate.scaffold_one("secrets", "acme", _cfg(), str(tmp_path),
                          fields=["user", "password"])
    res = tmp_path / "noodle_tests" / "web" / "acme" / "resources"
    assert (res / "acme_secrets.env").exists()
    assert not (res / "acme_secrets.env.example").exists()
    # gitignore pattern **/resources/*_secrets.env matches the written name
    assert (res / "acme_secrets.env").name.endswith("_secrets.env")


def test_scaffold_resources_writes_gitignored_secrets(tmp_path):
    app_dir = tmp_path / "noodle_tests" / "web" / "acme"
    generate._scaffold_resources(app_dir, "acme", "https://acme.example.com/login")
    res = app_dir / "resources"
    assert (res / "acme_secrets.env").exists()
    assert not (res / "acme_secrets.env.example").exists()
    assert (res / "acme_environments.yaml").exists()


def test_init_scaffolds_gitignore_covering_the_secrets_file(tmp_path):
    # the generated <app>_secrets.env is real now, not a commit-safe .example —
    # init must ship a .gitignore so it can't be committed by accident.
    from typer.testing import CliRunner

    from noodle.cli import app
    result = CliRunner().invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    gi = (tmp_path / ".gitignore").read_text()
    assert "**/resources/*_secrets.env" in gi
    assert "secrets.env" in gi
