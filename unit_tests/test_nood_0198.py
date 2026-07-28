"""NOOD_0198 — a text argument may name where the text lives.

The AI-SDLC handoff (an orchestrator writes the ask to a file, then invokes
Noodle) had exactly one way in: `--prompt "$(cat story.md)"`. Inside double
quotes the shell command-substitutes every backtick, and machine-written
markdown is mostly code fences — so that path both corrupted the prompt and
ran the generator's output as shell.

`--spec` already accepted a path or `-`; `--prompt` and `task TEXT` did not.
One helper, same rule everywhere: `-` is stdin, an existing file is its
contents, anything else is the text itself.
"""
import io

import pytest
import typer

from noodle import cli

# --- the helper's three branches ---------------------------------------------

def test_existing_file_is_read_not_treated_as_text(tmp_path):
    f = tmp_path / "story.md"
    f.write_text("1. Go to https://example.com\n2. Verify `total`\n",
                 encoding="utf-8")
    text, indirect = cli._arg_text(str(f))
    assert indirect is True
    # The backticks survive verbatim — the whole point of not going via $(cat).
    assert text == "1. Go to https://example.com\n2. Verify `total`\n"


def test_dash_reads_stdin(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("1. Go to https://x.io"))
    assert cli._arg_text("-") == ("1. Go to https://x.io", True)


def test_plain_text_is_itself(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)          # nothing here can shadow the text
    prompt = "1. Go to https://example.com 2. Search for \"chair\""
    assert cli._arg_text(prompt) == (prompt, False)


def test_absurdly_long_text_does_not_raise(tmp_path, monkeypatch):
    """A prompt longer than PATH_MAX makes Path.is_file() raise OSError on
    some platforms; that must read as 'not a file', never as a crash."""
    monkeypatch.chdir(tmp_path)
    long = "1. Go to https://example.com " + ("x" * 9000)
    assert cli._arg_text(long) == (long, False)


# --- the doors that use it ----------------------------------------------------

def _author_prompt(monkeypatch, arg, tmp_path):
    """Invoke `author --prompt <arg>`, returning what reached the engine."""
    seen = {}

    def _fake(**kw):
        seen.update(kw)
        return {"ok": True, "ready": True}

    monkeypatch.setattr("noodle.repl.core.author_test", _fake)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(typer.Exit) as e:
        cli.author(spec=None, prompt=arg, workspace=".", as_json=True,
                   run=False, overwrite=False, vocabulary=False)
    assert e.value.exit_code == 0
    return seen["prompt"]


def test_author_prompt_accepts_a_file_path(monkeypatch, tmp_path):
    f = tmp_path / "NOOD-1234.md"
    f.write_text("1. Go to https://shop.test\n2. Verify \"Order placed\"\n",
                 encoding="utf-8")
    assert _author_prompt(monkeypatch, str(f), tmp_path).startswith(
        "1. Go to https://shop.test")


def test_author_prompt_still_accepts_inline_text(monkeypatch, tmp_path):
    inline = "1. Go to https://shop.test 2. Verify \"Order placed\""
    assert _author_prompt(monkeypatch, inline, tmp_path) == inline


def test_task_text_accepts_a_file_path(monkeypatch, tmp_path):
    f = tmp_path / "ask.md"
    f.write_text("write a test that goes to https://shop.test\n",
                 encoding="utf-8")
    seen = {}
    monkeypatch.setattr("noodle.repl.task.route",
                        lambda text, **kw: seen.update(text=text)
                        or {"ok": True, "intent": "generate"})
    monkeypatch.chdir(tmp_path)
    with pytest.raises(typer.Exit):
        cli.task(text=str(f), workspace=".", tag=None, headed=False,
                 no_serve=True, show_contract=False, as_json=True)
    assert seen["text"] == "write a test that goes to https://shop.test\n"


# --- the NOOD_0197 behaviour this refactor must not break --------------------

def test_spec_typo_path_is_still_rejected(monkeypatch, tmp_path):
    """A path-shaped --spec that names no file is a typo, not a document."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(typer.BadParameter, match="spec file not found"):
        cli.author(spec="specs/nope.yaml", prompt=None, workspace=".",
                   as_json=True, run=False, overwrite=False, vocabulary=False)


def test_inline_yaml_spec_still_parses(monkeypatch, tmp_path):
    """NOOD_0197's inline document: not a file, but has ':' — so it's a spec."""
    seen = {}
    monkeypatch.setattr("noodle.repl.core.author_test",
                        lambda **kw: seen.update(kw) or {"ok": True})
    monkeypatch.chdir(tmp_path)
    with pytest.raises(typer.Exit):
        cli.author(spec="app_name: shop\nbase_url: https://shop.test\n"
                        "feature_path: f.feature\nfeature_content: 'x'\n",
                   prompt=None, workspace=".", as_json=True, run=False,
                   overwrite=False, vocabulary=False)
    assert seen["app_name"] == "shop"
