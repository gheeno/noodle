"""NOOD_0171 — structured logging (phase 1) + the compliance guarantee (phase 2).

Phase 1: NOODLE_LOG_FORMAT=json emits OTel-shaped JSON to stderr, carrying the
run_id correlation context; text mode stays byte-for-byte the old console.

Phase 2: no secret leaks into any log sink — whether it came from a *secrets.env
file (NOOD_0118, covered there) OR from an injected env var (the container case
this ticket adds). The six sinks (console, file log, captured-warnings buffer,
Allure JSON, rca.html, diagnostics/*.md) all funnel through exactly two
chokepoints — the logger's _RedactFilter and log.redact() — so asserting on
those two + the JSON path proves the guarantee without launching a browser.
"""
import json
import os

import pytest

from noodle import log


def _reconfigure_from_env():
    log.logger.handlers.clear()
    log.logger.filters.clear()
    log.logger._noodle_configured = False
    log._configure()


def _use_json():
    os.environ["NOODLE_LOG_FORMAT"] = "json"
    _reconfigure_from_env()


@pytest.fixture(autouse=True)
def _isolate():
    """Clean scrub-set + context per test; always restore text mode after (other
    tests read stdout and assume the plain console)."""
    log._secret_values.clear()
    log.clear_warnings()
    log._context.set({})
    yield
    log._secret_values.clear()
    log.clear_warnings()
    log._context.set({})
    os.environ.pop("NOODLE_LOG_FORMAT", None)
    _reconfigure_from_env()


def _last_json(err: str) -> dict:
    return json.loads(err.strip().splitlines()[-1])


# --- phase 1: format + correlation ---------------------------------------
def test_text_mode_is_the_unchanged_console(capsys):
    log.event("locator.heal", "🩹 healed 'Sign in'", original="a", healed_to="b")
    out = capsys.readouterr().out
    assert "🩹 healed 'Sign in'" in out
    assert "locator.heal" not in out   # event name never pollutes the human console
    assert "healed_to" not in out      # nor do attributes


def test_json_mode_emits_otel_shaped_line_to_stderr(capsys):
    _use_json()
    log.logger.info("📋 POM resolved 'Sign in'")
    cap = capsys.readouterr()
    assert cap.out == ""               # stdout stays clean (MCP protocol channel)
    obj = _last_json(cap.err)
    assert obj["body"] == "📋 POM resolved 'Sign in'"
    assert obj["severity_text"] == "INFO"
    assert obj["severity_number"] == 9
    assert obj["service_name"] == "noodle"
    assert obj["timestamp"].endswith("Z")


def test_json_line_carries_the_correlation_context(capsys):
    _use_json()
    log.bind(run_id="9f2c4ab1d0e37c58", workspace="team-b", feature="login.feature")
    log.logger.info("something happened")
    obj = _last_json(capsys.readouterr().err)
    assert obj["run_id"] == "9f2c4ab1d0e37c58"
    assert obj["workspace"] == "team-b"
    assert obj["feature"] == "login.feature"


def test_event_attributes_appear_only_in_json(capsys):
    _use_json()
    log.event("llm.call", "🤖 model call", model="anthropic/claude-sonnet-5",
              input_tokens=1200, output_tokens=48)
    obj = _last_json(capsys.readouterr().err)
    assert obj["event"] == "llm.call"
    assert obj["attributes"]["model"] == "anthropic/claude-sonnet-5"
    assert obj["attributes"]["input_tokens"] == 1200   # token COUNTS survive — governance data, not a secret


def test_new_run_id_is_16_hex_chars():
    rid = log.new_run_id()
    assert len(rid) == 16
    int(rid, 16)  # raises if not hex


# --- phase 2: env-secret sweep -------------------------------------------
def test_env_regex_matches_container_secrets_not_config():
    for k in ("NOODLE_MCP_API_KEY", "NOODLE_HTTP_PASSWORD", "ANTHROPIC_API_KEY",
              "OPENAI_API_KEY", "DATABASE_CONNECTION_STRING", "GITHUB_TOKEN"):
        assert log._SENSITIVE_ENV_RE.search(k), k
    for k in ("NOODLE_HEADLESS", "NOODLE_MODEL", "NOODLE_ARTIFACTS_DIR",
              "NOODLE_BROWSER", "PATH"):
        assert not log._SENSITIVE_ENV_RE.search(k), k


def test_register_env_secrets_registers_credential_values(monkeypatch):
    monkeypatch.setenv("NOODLE_MCP_API_KEY", "mcp-key-abcdef123")
    monkeypatch.setenv("NOODLE_HTTP_PASSWORD", "http-pw-abcdef123")
    monkeypatch.setenv("NOODLE_HEADLESS", "true")   # config, not a secret
    log.register_env_secrets()
    assert "mcp-key-abcdef123" in log._secret_values
    assert "http-pw-abcdef123" in log._secret_values


def test_injected_env_secret_scrubbed_from_console_warnings_and_disk(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_PASSWORD", "leaky-pw-zzz999")
    log.register_env_secrets()
    log.logger.info("connecting with leaky-pw-zzz999")     # sink: console + file log
    log.logger.warning("auth failed: leaky-pw-zzz999")     # sink: captured warnings → Allure/RCA
    on_disk = log.redact("conn=leaky-pw-zzz999")            # sink: diagnostics/*.md writer
    out = capsys.readouterr().out
    assert "leaky-pw-zzz999" not in out and "***" in out
    assert all("leaky-pw-zzz999" not in w for w in log.get_warnings())
    assert "leaky-pw-zzz999" not in on_disk


# --- phase 2: attribute + key-name redaction -----------------------------
def test_attributes_key_name_masking(capsys):
    _use_json()
    log.event("mcp.tool", "tool ran", tool="run_and_report", api_key="sk-should-not-appear",
              password="p@ssw0rd", access_key="AKIA-nope")
    obj = _last_json(capsys.readouterr().err)
    assert obj["attributes"]["tool"] == "run_and_report"
    assert obj["attributes"]["api_key"] == "***"
    assert obj["attributes"]["password"] == "***"
    assert obj["attributes"]["access_key"] == "***"
    assert "sk-should-not-appear" not in json.dumps(obj)


def test_attributes_value_scrub_under_a_bland_key(capsys):
    log.register_secret("bland-secret-value-9999")
    _use_json()
    log.event("x", "b", note="see bland-secret-value-9999 in the url")
    obj = _last_json(capsys.readouterr().err)
    assert obj["attributes"]["note"] == "see *** in the url"
    assert "bland-secret-value-9999" not in json.dumps(obj)


def test_file_log_is_json_in_json_mode(tmp_path):
    # The file log is the reliable sink in CLI mode (immune to behave's capture);
    # in json mode it must be structured+correlated+redacted, not human text.
    import logging as _logging
    _use_json()
    log.bind(run_id="deadbeefdeadbeef", workspace="corp")
    p = tmp_path / "noodle.log"
    log.attach_file_handler(str(p))
    log.event("run.end", "done", failed=0, secret_token="sk-nope")
    for h in log.logger.handlers:
        if isinstance(h, _logging.FileHandler):
            h.flush()
    obj = json.loads(p.read_text().strip().splitlines()[-1])
    assert obj["event"] == "run.end"
    assert obj["run_id"] == "deadbeefdeadbeef"
    assert obj["attributes"]["failed"] == 0
    assert obj["attributes"]["secret_token"] == "***"


def test_redact_attrs_leaves_counts_and_paths_untouched():
    out = log._redact_attrs({"input_tokens": 1200, "duration_ms": 41,
                             "screenshot_path": "/artifacts/a.png", "model": "sonnet-5"})
    assert out == {"input_tokens": 1200, "duration_ms": 41,
                   "screenshot_path": "/artifacts/a.png", "model": "sonnet-5"}
