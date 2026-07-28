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
    obj = json.loads(p.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert obj["event"] == "run.end"
    assert obj["run_id"] == "deadbeefdeadbeef"
    assert obj["attributes"]["failed"] == 0
    assert obj["attributes"]["secret_token"] == "***"


def test_redact_attrs_leaves_counts_and_paths_untouched():
    out = log._redact_attrs({"input_tokens": 1200, "duration_ms": 41,
                             "screenshot_path": "/artifacts/a.png", "model": "sonnet-5"})
    assert out == {"input_tokens": 1200, "duration_ms": 41,
                   "screenshot_path": "/artifacts/a.png", "model": "sonnet-5"}


# --- phase 3: MCP server audit trail -------------------------------------
def test_route_console_to_stderr_flips_the_handler():
    _use_json()
    log.route_console_to_stderr()
    live = [h for h in log.logger.handlers if isinstance(h, log._LiveStreamHandler)]
    assert live and all(h._stream_name == "stderr" for h in live)


def test_mcp_tool_event_is_logged_per_call(capsys):
    from noodle.mcp import server
    _use_json()
    log.route_console_to_stderr()
    server.server_info()   # a cheap read-only tool; still goes through _tool()
    events = [json.loads(ln) for ln in capsys.readouterr().err.strip().splitlines()
              if '"mcp.tool"' in ln]
    assert events, "no mcp.tool event emitted"
    e = events[-1]
    assert e["event"] == "mcp.tool"
    assert e["attributes"]["tool"] == "server_info"
    assert e["attributes"]["ok"] is True
    assert e["attributes"]["duration_ms"] >= 0
    assert e["attributes"]["payload_bytes"] > 0
    assert e["run_id"]                     # correlated to this call


def test_mcp_tool_event_marks_a_logical_failure(capsys):
    from noodle.mcp import server
    _use_json()
    log.route_console_to_stderr()
    r = server.run_test(browser="notabrowser")   # rejected before any subprocess
    assert r["ok"] is False
    events = [json.loads(ln) for ln in capsys.readouterr().err.strip().splitlines()
              if '"mcp.tool"' in ln]
    assert events[-1]["attributes"]["tool"] == "run_test"
    assert events[-1]["attributes"]["ok"] is False


def test_mcp_auth_deny_is_logged_without_the_key(capsys):
    import asyncio

    from noodle.mcp import server
    _use_json()
    log.route_console_to_stderr()

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    guarded = server._require_key(inner, "the-real-key-abc")
    sent = []

    async def send(m):
        sent.append(m)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "path": "/mcp", "client": ("203.0.113.7", 5555),
             "headers": [(b"x-api-key", b"wrong-guess-xyz")]}
    asyncio.run(guarded(scope, receive, send))
    assert sent[0]["status"] == 401

    err = capsys.readouterr().err
    assert "the-real-key-abc" not in err and "wrong-guess-xyz" not in err  # never log either value
    deny = [json.loads(ln) for ln in err.strip().splitlines() if '"mcp.auth.deny"' in ln]
    assert deny, "no mcp.auth.deny event"
    assert deny[-1]["severity_text"] == "WARNING"
    assert deny[-1]["attributes"] == {"remote_ip": "203.0.113.7", "path": "/mcp",
                                      "reason": "bad key"}


# --- phase 4: AI-governance events (NOOD_0172) ---------------------------
import types  # noqa: E402


def _fake_litellm(content='{"ok": 1}', model="anthropic/claude-sonnet-5",
                  in_tok=120, out_tok=8):
    usage = types.SimpleNamespace(prompt_tokens=in_tok, completion_tokens=out_tok)
    msg = types.SimpleNamespace(content=content)
    resp = types.SimpleNamespace(model=model, usage=usage,
                                 choices=[types.SimpleNamespace(message=msg)])
    return types.SimpleNamespace(completion=lambda **kw: resp)


def _events(err, name):
    return [json.loads(ln) for ln in err.strip().splitlines() if f'"{name}"' in ln]


def test_llm_call_event_carries_model_tokens_and_host(monkeypatch, capsys):
    from noodle.llm import client, cost
    client.reset_calls()
    cost.reset()
    monkeypatch.setenv("NOODLE_MODEL", "anthropic/claude-sonnet-5")
    monkeypatch.delenv("NOODLE_LLM_URL", raising=False)
    monkeypatch.setattr(client, "_litellm", _fake_litellm)
    _use_json()
    client.ask("classify this")
    e = _events(capsys.readouterr().err, "llm.call")[-1]
    a = e["attributes"]
    assert a["purpose"] == "llm"
    assert a["model"] == "anthropic/claude-sonnet-5"
    assert a["input_tokens"] == 120 and a["output_tokens"] == 8
    assert a["duration_ms"] >= 0
    assert a["api_host"] == "anthropic"      # provider prefix, never a key-bearing URL


def test_llm_call_event_purpose_is_rca_for_vision_rca_pool(monkeypatch, capsys):
    import base64

    from noodle.llm import client, cost
    client.reset_calls()
    cost.reset()
    monkeypatch.setenv("NOODLE_MODEL", "anthropic/claude-sonnet-5")
    monkeypatch.setattr(client, "_litellm", lambda: _fake_litellm(content='{"category":"A"}'))
    _use_json()
    client.ask_vision("why did it fail", base64.b64encode(b"img").decode(),
                      cap_var="NOODLE_RCA_MAX_CALLS")
    assert _events(capsys.readouterr().err, "llm.call")[-1]["attributes"]["purpose"] == "rca"


def test_llm_call_capped_event_on_spend_cap(monkeypatch, capsys):
    from noodle.llm import client
    client.reset_calls()
    monkeypatch.setenv("NOODLE_LLM_MAX_CALLS", "1")
    _use_json()
    client._check_cap()                      # 1st call — allowed, count → 1
    with pytest.raises(AssertionError):
        client._check_cap()                  # 2nd — cap hit, logs + raises
    e = _events(capsys.readouterr().err, "llm.call")[-1]
    assert e["severity_text"] == "WARNING"
    assert e["attributes"]["capped"] is True and e["attributes"]["cap"] == 1


def test_locator_heal_event_flags_fuzzy_strategies(capsys):
    from noodle import healing
    _use_json()
    healing.reset()
    healing.record("Sign in", "partial-text", "matched 'Sign'")
    healing.record("Menu", "scroll")
    evs = _events(capsys.readouterr().err, "locator.heal")
    fuzzy = next(e for e in evs if e["attributes"]["original"] == "Sign in")
    confident = next(e for e in evs if e["attributes"]["original"] == "Menu")
    assert fuzzy["attributes"]["technique"] == "partial-text"
    assert fuzzy["attributes"]["fuzzy"] is True
    assert confident["attributes"]["fuzzy"] is False   # scroll is an exact, off-screen match
    healing.reset()


def test_rca_verdict_is_marked_ai_authored(monkeypatch, tmp_path, capsys):
    from noodle import rca
    from noodle.llm import client
    monkeypatch.setenv("NOODLE_RCA", "1")
    monkeypatch.setenv("NOODLE_MODEL", "anthropic/claude-sonnet-5")
    monkeypatch.setattr(client, "ask_vision",
                        lambda **kw: '{"category":"B","confidence":"high",'
                                     '"reason":"label changed","suggested_fix":"pin the POM"}')
    shot = tmp_path / "fail.png"
    shot.write_bytes(b"\x89PNG fake")
    _use_json()
    verdict = rca.review("clicks Sign in", "not found", str(shot))
    assert verdict["ai_authored"] is True
    assert verdict["model"] == "anthropic/claude-sonnet-5"
    e = _events(capsys.readouterr().err, "rca.verdict")[-1]
    assert e["attributes"]["ai_authored"] is True and e["attributes"]["label"] == "locator-rot"


def test_llm_payload_log_is_opt_in_and_redacted(monkeypatch, tmp_path, capsys):
    from noodle.llm import client, cost
    client.reset_calls()
    cost.reset()
    monkeypatch.setenv("NOODLE_MODEL", "anthropic/claude-sonnet-5")
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("NOODLE_RUN_ID", "testrun01")
    monkeypatch.setattr(client, "_litellm", lambda: _fake_litellm(content="the answer"))
    log.register_secret("hunter2-secret-value")

    # off by default → no file
    client.ask("password is hunter2-secret-value")
    assert not (tmp_path / "llm" / "testrun01.jsonl").exists()

    # opted in → written, redacted
    monkeypatch.setenv("NOODLE_LOG_LLM_PAYLOADS", "1")
    client.ask("password is hunter2-secret-value")
    body = (tmp_path / "llm" / "testrun01.jsonl").read_text(encoding="utf-8")
    assert "hunter2-secret-value" not in body and "***" in body
    assert json.loads(body.strip().splitlines()[-1])["purpose"] == "llm"
