"""NOOD_0094 — HTTPS/cert errors ignored by default in the scaffolded workspace,
and the agent test-development fix→rerun loop cap (NOODLE_DEV_FIX_ATTEMPTS).
No browser, no LLM, no network."""
import pytest

from noodle import config
from noodle.cli import _AGENTS_MD, _env_stub

# --- dev_fix_attempts (agent test-dev loop cap) ------------------------------

def test_dev_fix_attempts_default(monkeypatch):
    monkeypatch.delenv("NOODLE_DEV_FIX_ATTEMPTS", raising=False)
    assert config.dev_fix_attempts() == 10


def test_dev_fix_attempts_env_override(monkeypatch):
    monkeypatch.setenv("NOODLE_DEV_FIX_ATTEMPTS", "3")
    assert config.dev_fix_attempts() == 3


@pytest.mark.parametrize("bad", ["0", "-4", "notanint", ""])
def test_dev_fix_attempts_floor_and_garbage(monkeypatch, bad):
    # never returns < 1 (an empty loop is pointless); garbage falls back to 10
    monkeypatch.setenv("NOODLE_DEV_FIX_ATTEMPTS", bad)
    assert config.dev_fix_attempts() >= 1


# --- workspace template defaults ---------------------------------------------

def test_env_stub_verifies_https_by_default():
    # NOOD_0177 — a fresh workspace must VERIFY certificates. The line is still
    # set rather than commented, so it stays discoverable as an editable
    # default, but a new workspace is now secure until someone opts out.
    stub = _env_stub()
    assert "\nNOODLE_IGNORE_HTTPS_ERRORS=false" in stub
    assert "NOODLE_IGNORE_HTTPS_ERRORS=true" not in stub


def test_env_stub_documents_dev_fix_attempts():
    assert "NOODLE_DEV_FIX_ATTEMPTS" in _env_stub()


def test_agents_md_documents_dev_loop():
    assert "NOODLE_DEV_FIX_ATTEMPTS" in _AGENTS_MD
