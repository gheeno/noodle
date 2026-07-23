"""NOOD_0026 — `noodle step-search` CLI command, the scriptable/CI twin of
noodle repl's `find a step for ...`. No browser, no network — LLM calls are
monkeypatched. NOOD_0027 — --accept writes are redirected away from the real
repo's docs/ via --workspace tmp_path (the same flag a real external test
repo would pass).
"""
from typer.testing import CliRunner

from noodle.cli import app
from noodle.llm import client


def test_step_search_prints_best_match():
    result = CliRunner().invoke(app, ["step-search", "store a return param and use it to another step"])
    assert result.exit_code == 0
    assert "Best match" in result.output
    assert "stores" in result.output.lower()


def test_step_search_no_fit_exits_1_and_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("NOODLE_MODEL", raising=False)

    result = CliRunner().invoke(
        app, ["step-search", "frobnicate the sprocket widget", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "No good match" in result.output
    assert "not auto-generated" in result.output
    assert not (tmp_path / "docs" / "agent_patterns.yaml").exists()


def test_step_search_accept_writes_files(monkeypatch, tmp_path):
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    monkeypatch.setattr(client, "ask", lambda *a, **k: "click")

    result = CliRunner().invoke(
        app, ["step-search", "frobnicate the sprocket widget", "--accept",
              "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "Suggested new step" in result.output
    assert "Wrote" in result.output
    assert (tmp_path / "docs" / "agent_patterns.yaml").exists()
    assert (tmp_path / "docs" / "steps_dictionary.md").exists()


def test_step_search_without_accept_does_not_write(monkeypatch, tmp_path):
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    monkeypatch.setattr(client, "ask", lambda *a, **k: "click")

    result = CliRunner().invoke(
        app, ["step-search", "frobnicate the sprocket widget", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "Re-run with --accept" in result.output
    assert not (tmp_path / "docs" / "agent_patterns.yaml").exists()


def test_step_search_no_llm_flag_skips_llm_even_if_configured(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    monkeypatch.setattr(client, "ask", lambda *a, **k: calls.append(1) or "click")

    result = CliRunner().invoke(
        app, ["step-search", "frobnicate the sprocket widget", "--no-llm",
              "--workspace", str(tmp_path)])

    assert calls == []
    assert "not auto-generated" in result.output


def test_step_search_workspace_accept_resolves_at_run_time(monkeypatch, tmp_path):
    """NOOD_0027 round trip: a step accepted via --workspace must actually
    resolve through patterns.match() when that same workspace's docs/ is
    later loaded — the exact thing that was silently broken before this fix
    (accept said 'Wrote', but a real external-workspace run never saw it)."""
    monkeypatch.setenv("NOODLE_MODEL", "ollama/llama3")
    monkeypatch.setattr(client, "ask", lambda *a, **k: "click")

    result = CliRunner().invoke(
        app, ["step-search", "frobnicate the sprocket widget", "--accept",
              "--workspace", str(tmp_path)])
    assert result.exit_code == 0

    from noodle.resolver import patterns
    patterns.set_agent_patterns_dir(tmp_path / "docs")
    resolved = patterns.match(patterns.normalize_subject("User frobnicates the sprocket widget"))
    assert resolved == ("click", {"locator": "<TODO>"})


def test_steps_reference_is_portable_from_external_workspace(monkeypatch, tmp_path):
    """NOOD_0145 — `noodle steps` runs fine from an external test workspace
    (the dictionary is bundled in the installed package), but its footer used
    to point at the SOURCE-repo path docs/steps_dictionary.md. An agent in a
    workspace reads that as <workspace>/docs/…, searches a directory that
    does not exist, and concludes the documentation is missing."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["steps", "search"])

    assert result.exit_code == 0
    # NOOD_0161 — bare `noodle steps` is the section index now, so the footer
    # points at `noodle docs`/read_docs for the whole dictionary. Still the
    # NOOD_0145 contract: portable references, never a source-repo path.
    assert "noodle docs steps_dictionary" in result.output
    assert "read_docs('steps_dictionary')" in result.output
    assert "docs/steps_dictionary.md" not in result.output
