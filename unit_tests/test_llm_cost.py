"""NOOD_0080 — token/dollar cost ledger, persistence, and estimator.

No live model: responses are stub objects and litellm is monkeypatched, so we
assert the *accounting* (record, merge, persist, format) without a network
call or the litellm import cost.
"""
import json
import sys
import types

import pytest

from noodle.llm import cost


class _Usage:
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _Resp:
    def __init__(self, prompt=100, completion=20, model="anthropic/claude-sonnet-5"):
        self.usage = _Usage(prompt, completion)
        self.model = model


@pytest.fixture(autouse=True)
def _fresh_ledger():
    cost.reset()
    yield
    cost.reset()


def _fake_litellm(monkeypatch, usd=0.01, raises=False):
    ll = types.ModuleType("litellm")

    def completion_cost(completion_response=None):
        if raises:
            raise ValueError("model not in pricing table")
        return usd

    ll.completion_cost = completion_cost
    monkeypatch.setitem(sys.modules, "litellm", ll)


# --- record + summary ---------------------------------------------------------

def test_record_accumulates_tokens_and_usd(monkeypatch):
    _fake_litellm(monkeypatch, usd=0.01)
    cost.record("llm", _Resp(100, 20))
    cost.record("llm", _Resp(50, 10))
    cost.record("rca", _Resp(1000, 30))

    s = cost.summary()
    assert s["calls"] == 3
    assert s["input_tokens"] == 1150
    assert s["output_tokens"] == 60
    assert s["usd"] == pytest.approx(0.03)
    assert s["by_purpose"]["rca"]["input_tokens"] == 1000
    assert s["by_purpose"]["llm"]["calls"] == 2


def test_unknown_pricing_still_counts_tokens(monkeypatch):
    _fake_litellm(monkeypatch, raises=True)  # e.g. self-hosted Ollama build
    cost.record("llm", _Resp(100, 20, model="ollama/llama3"))

    s = cost.summary()
    assert s["input_tokens"] == 100
    assert s["usd"] is None
    assert "cost unknown" in cost.format_line(s)


def test_record_never_raises_on_garbage(monkeypatch):
    _fake_litellm(monkeypatch)
    cost.record("llm", object())  # no .usage, no .model — must not blow up
    assert cost.summary()["calls"] == 1


def test_summary_none_when_no_calls():
    assert cost.summary() is None
    assert "none" in cost.format_line()


# --- persistence + parallel merge ----------------------------------------------

def test_write_and_load_roundtrip(tmp_path, monkeypatch):
    _fake_litellm(monkeypatch, usd=0.02)
    monkeypatch.delenv("NOODLE_PARALLEL_WORKER", raising=False)
    cost.record("llm", _Resp(100, 20))
    cost.write_json(tmp_path)

    total = cost.load_total(tmp_path)
    assert (tmp_path / "llm_cost.json").exists()
    assert total["calls"] == 1
    assert total["usd"] == pytest.approx(0.02)


def test_load_total_sums_parallel_worker_files(tmp_path):
    for i, tokens in enumerate([(100, 10), (200, 20)]):
        (tmp_path / f"llm_cost.p{i}.json").write_text(json.dumps({
            "by_purpose": {"llm": {"calls": 1, "input_tokens": tokens[0],
                                   "output_tokens": tokens[1], "usd": 0.01}},
            "model": "anthropic/claude-sonnet-5",
        }))

    total = cost.load_total(tmp_path)
    assert total["calls"] == 2
    assert total["input_tokens"] == 300
    assert total["usd"] == pytest.approx(0.02)


def test_write_json_noop_without_calls(tmp_path):
    cost.write_json(tmp_path)
    assert list(tmp_path.glob("llm_cost*.json")) == []


def test_load_total_none_when_absent(tmp_path):
    assert cost.load_total(tmp_path) is None
    assert cost.load_total(tmp_path / "missing") is None


# --- client.py integration -----------------------------------------------------

def test_ask_records_into_ledger(monkeypatch):
    from noodle.llm import client
    resp = _Resp(42, 7)
    ll = types.ModuleType("litellm")
    ll.completion = lambda **kw: types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="hi"))],
        usage=resp.usage, model=resp.model)
    ll.completion_cost = lambda completion_response=None: 0.005
    monkeypatch.setitem(sys.modules, "litellm", ll)
    monkeypatch.setenv("NOODLE_MODEL", "anthropic/claude-sonnet-5")
    monkeypatch.delenv("NOODLE_LLM_MAX_CALLS", raising=False)
    client.reset_calls()

    assert client.ask("hello") == "hi"
    s = cost.summary()
    assert s["by_purpose"]["llm"]["input_tokens"] == 42
    assert s["usd"] == pytest.approx(0.005)


def test_ask_vision_rca_pool_buckets_as_rca(monkeypatch):
    from noodle.llm import client
    ll = types.ModuleType("litellm")
    ll.completion = lambda **kw: types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))],
        usage=_Usage(500, 30), model="m")
    ll.completion_cost = lambda completion_response=None: 0.02
    monkeypatch.setitem(sys.modules, "litellm", ll)
    monkeypatch.setenv("NOODLE_MODEL", "m")
    monkeypatch.delenv("NOODLE_RCA_MAX_CALLS", raising=False)
    client.reset_calls()

    client.ask_vision("what broke?", "aGk=", cap_var="NOODLE_RCA_MAX_CALLS")
    assert cost.summary()["by_purpose"]["rca"]["calls"] == 1


# --- estimator -------------------------------------------------------------------

def test_estimate_tokens_and_floor(monkeypatch):
    ll = types.ModuleType("litellm")
    ll.token_counter = lambda model=None, text=None: 250
    ll.cost_per_token = lambda model=None, prompt_tokens=0, completion_tokens=0: (0.00075, 0.0)
    monkeypatch.setitem(sys.modules, "litellm", ll)

    est = cost.estimate("Feature: login\n  Scenario: ...", model="anthropic/claude-sonnet-5")
    assert est["input_tokens"] == 250
    assert est["usd_input_floor"] == pytest.approx(0.00075)


def test_estimate_unknown_pricing(monkeypatch):
    ll = types.ModuleType("litellm")
    ll.token_counter = lambda model=None, text=None: 99

    def _boom(**kw):
        raise ValueError("no pricing")

    ll.cost_per_token = _boom
    monkeypatch.setitem(sys.modules, "litellm", ll)

    est = cost.estimate("hi", model="ollama/llama3")
    assert est["input_tokens"] == 99
    assert est["usd_input_floor"] is None
