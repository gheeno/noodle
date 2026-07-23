"""Smoke test: noodle -> LiteLLM -> an OpenAI-compatible local endpoint.

This is the contract noodle relies on to run against Foundry Local (or any
OpenAI-compatible local server) on a network where Ollama/Hugging Face are
blocked. A stdlib stub stands in for the Foundry Local web service so the test
has no external dependency.

Covers:
  - client.ask() round-trips text through the endpoint.
  - step_resolver.resolve() falls back to the LLM (Trigger 1) when no regex
    pattern matches, and parses the model's JSON action.

Run just this file with logs of the model exchange:
    uv run --with litellm --with pytest python tests/test_llm_openai_endpoint.py
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

litellm = pytest.importorskip("litellm")  # skip if the [llm] extra isn't installed

from noodle.llm.client import ask  # noqa: E402 — needs the importorskip above
from noodle.resolver import step_resolver  # noqa: E402


class _Stub:
    """A minimal OpenAI-compatible /v1/chat/completions server (Foundry stand-in)."""

    def __init__(self):
        self.reply = "ok"          # set by each test to whatever the "model" should say
        self.requests = []         # parsed request bodies, newest last
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                stub.requests.append(body)
                out = {
                    "id": "stub", "object": "chat.completion", "model": body.get("model"),
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": stub.reply}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._srv = HTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self._srv.server_address[1]}/v1"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self):
        self._srv.shutdown()

    @property
    def last_prompt(self):
        """The user prompt noodle actually sent the model on the last call."""
        return self.requests[-1]["messages"][-1]["content"]


@pytest.fixture
def foundry(monkeypatch):
    stub = _Stub()
    # exactly the .env Foundry Local config (port = the stub's)
    monkeypatch.setenv("NOODLE_MODEL", "openai/qwen2.5-7b-instruct-generic-cpu")
    monkeypatch.setenv("NOODLE_LLM_URL", stub.endpoint)
    monkeypatch.setenv("OPENAI_API_KEY", "not-needed")  # LiteLLM's openai/ path requires it set
    yield stub
    stub.stop()


def test_ask_round_trips_through_the_endpoint(foundry):
    foundry.reply = "the model is reachable"
    assert ask("are you there?") == "the model is reachable"
    # noodle sent our prompt to the endpoint
    assert foundry.last_prompt == "are you there?"


def test_ask_sends_system_message_when_given(foundry):
    foundry.reply = "ok"
    ask("hello", system="be terse")
    sent = foundry.requests[-1]["messages"]
    assert sent[0] == {"role": "system", "content": "be terse"}
    assert sent[-1] == {"role": "user", "content": "hello"}


def test_ask_without_system_omits_it(foundry):
    foundry.reply = "ok"
    ask("hello")
    assert foundry.requests[-1]["messages"] == [{"role": "user", "content": "hello"}]


def test_resolve_falls_back_to_llm_on_no_pattern_match(foundry):
    # "does a barrel roll" is not a known verb -> no regex matches -> Trigger 1 fires.
    foundry.reply = '{"type": "click", "locator": "Login"}'
    action = step_resolver.resolve("User does a barrel roll")
    assert action == {"type": "click", "locator": "Login"}
    # the step text noodle asked the model to interpret reached the endpoint
    assert "does a barrel roll" in foundry.last_prompt


def test_unset_api_key_is_the_documented_gotcha(foundry, monkeypatch):
    # Without OPENAI_API_KEY, LiteLLM's openai/ provider errors before the call.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(Exception):
        ask("this should fail fast")


# --- NOOD_0018-1: LLM fallback output validation -------------------------

def test_resolve_rejects_unknown_action_type(foundry):
    # A syntactically valid JSON with a bogus type must NOT dispatch — it would
    # otherwise run the wrong action or crash deep in the runner.
    foundry.reply = '{"type": "frobnicate", "locator": "Login"}'
    with pytest.raises(AssertionError, match="unknown action type 'frobnicate'"):
        step_resolver.resolve("User does something unknown")


def test_resolve_accepts_advertised_type_the_old_list_missed(foundry):
    # `search` is advertised in the prompt and dispatchable — must pass validation.
    foundry.reply = '{"type": "search", "query": "tool box"}'
    action = step_resolver.resolve("User looks for a tool box")
    assert action == {"type": "search", "query": "tool box"}


def test_resolve_strips_markdown_fence(foundry):
    foundry.reply = '```json\n{"type": "click", "locator": "Login"}\n```'
    action = step_resolver.resolve("User does a barrel roll")
    assert action == {"type": "click", "locator": "Login"}


def test_resolve_recovers_json_from_surrounding_prose(foundry):
    foundry.reply = 'Sure! Here is the action: {"type": "click", "locator": "OK"}'
    action = step_resolver.resolve("User confirms the dialog")
    assert action == {"type": "click", "locator": "OK"}


def test_resolve_retries_once_then_raises_on_garbage(foundry):
    foundry.reply = "not json at all, sorry"
    with pytest.raises(AssertionError, match="unparseable response"):
        step_resolver.resolve("User does a nonsense step")


def test_valid_types_mirrors_the_runner_dispatch():
    """Guard the hand-kept VALID_TYPES against drift from runner.py's dispatch."""
    import re
    from pathlib import Path
    runner = Path(step_resolver.__file__).parent.parent / "orchestrator" / "runner.py"
    dispatched = set(re.findall(r"t == '([a-z0-9_]+)'", runner.read_text()))
    assert dispatched == set(step_resolver.VALID_TYPES)


if __name__ == "__main__":
    # Verbose run: show exactly what the model received and returned for Trigger 1.
    stub = _Stub()
    os.environ.update(
        NOODLE_MODEL="openai/qwen2.5-7b-instruct-generic-cpu",
        NOODLE_LLM_URL=stub.endpoint,
        OPENAI_API_KEY="not-needed",
    )
    stub.reply = '{"type": "click", "locator": "Login"}'
    step = "User finalizes the login form"  # NOOD_0025 gave "submits" a real pattern

    print(f"\n  endpoint (Foundry stand-in): {stub.endpoint}")
    print(f"  feature step (no regex match): {step!r}\n")
    action = step_resolver.resolve(step)

    print("  ── what noodle SENT the model ───────────────────────────────")
    print("    " + stub.last_prompt.replace("\n", "\n    "))
    print("  ── what the model RETURNED ────────────────────────────────────")
    print(f"    {stub.reply}")
    print("  ── how noodle USED it (parsed action) ───────────────────────")
    print(f"    {action}\n")
    assert action == {"type": "click", "locator": "Login"}
    print("  PASS: no-match step -> LLM -> parsed action.\n")
    stub.stop()
