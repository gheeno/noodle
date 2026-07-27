"""NOOD_0192 — repo awareness and shell-agnostic install.

Two asks that share a shape: Noodle knowing something about the machine and
the codebase it was dropped into, instead of assuming a workspace already
configured by someone who read the docs.
"""
import json

import pytest

from noodle import doctor, install_check, repo_scan
from noodle.repl import task

# --- repo scan -----------------------------------------------------------------

@pytest.fixture
def devrepo(tmp_path):
    """A small but realistic polyrepo: a FastAPI service with an OpenAPI
    spec, a React front end, an existing test dir, and generated output the
    repo's own .gitignore excludes."""
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "pyproject.toml").write_text(
        '[project]\nname = "orders"\ndependencies = ["fastapi", "uvicorn"]\n')
    (tmp_path / "api" / "openapi.yaml").write_text(
        "openapi: 3.0.0\npaths:\n"
        "  /orders:\n    get: {summary: list}\n    post: {summary: create}\n"
        "  /orders/{id}:\n    delete: {summary: remove}\n")
    (tmp_path / "api" / "tests").mkdir()
    (tmp_path / "api" / "tests" / "test_orders.py").write_text("")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text(json.dumps(
        {"scripts": {"dev": "vite", "build": "vite build"},
         "dependencies": {"react": "^18"}}))
    (tmp_path / ".gitignore").write_text("build_output/\nnode_modules/\n")
    (tmp_path / "build_output").mkdir()
    (tmp_path / "build_output" / "package.json").write_text('{"name":"junk"}')
    return tmp_path


def test_the_stack_is_reported_not_guessed(devrepo):
    rep = repo_scan.scan(str(devrepo))
    assert rep["ok"]
    assert set(rep["stacks"]) == {"python", "node"}
    assert set(rep["frameworks"]) == {"fastapi", "react"}
    assert rep["woks"] == ["web", "api"], "a UI and an API means both"


def test_the_openapi_spec_is_the_api_test_plan(devrepo):
    """The endpoints the developer already wrote down. Without these, "write
    API tests for this repo" needs a round trip just to learn the paths."""
    rep = repo_scan.scan(str(devrepo))
    assert rep["api_specs"] == ["api/openapi.yaml"]
    assert rep["endpoints_total"] == 3
    assert set(rep["endpoints"]) == {"GET /orders", "POST /orders",
                                     "DELETE /orders/{id}"}


def test_how_the_repo_serves_itself_comes_from_the_repo(devrepo):
    assert "npm run dev" in repo_scan.scan(str(devrepo))["serve"]


def test_generated_output_the_repo_ignores_is_not_scanned(devrepo):
    """A gitignored build dir full of manifests would otherwise dominate
    every list — and on this repo it did."""
    rep = repo_scan.scan(str(devrepo))
    assert not any("build_output" in t for t in rep["test_dirs"])
    assert rep["stacks"] == sorted({"python", "node"})


def test_existing_test_dirs_are_found_by_token_not_substring(tmp_path):
    for d in ("unit_tests", "__tests__", "latest_release"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x.txt").write_text("")
    found = repo_scan.scan(str(tmp_path))["test_dirs"]
    assert "unit_tests" in found and "__tests__" in found
    assert "latest_release" not in found, "'latest' is not a test dir"


def test_a_vague_ask_comes_back_with_questions_never_a_guess(devrepo):
    """The AI-SDLC opener: "review this repo and write tests for the new
    features" names no URL, no steps and no boundary. The answer is the
    questions, so the caller can settle them and re-send."""
    out = task.route("Review this repo and generate new API tests and new "
                     "WEB tests for the features we just built",
                     workspace=str(devrepo))
    assert out["intent"] == "scan" and out["ok"] is True
    assert "generate" in out["also_matched"], "both intents were named"
    assert out["need"] == ["answers_to_questions"]
    joined = " ".join(out["questions"])
    assert "base URL" in joined              # where does it answer
    assert "web tests, API tests, or both" in joined
    assert "boundary" in joined              # what is "the new features"
    assert "npm run dev" in " ".join(out["serve"])


def test_the_scan_writes_nothing_and_runs_nothing(devrepo):
    before = sorted(p.name for p in devrepo.rglob("*"))
    repo_scan.scan(str(devrepo))
    assert sorted(p.name for p in devrepo.rglob("*")) == before


def test_a_missing_path_is_an_error_not_a_crash(tmp_path):
    rep = repo_scan.scan(str(tmp_path / "nope"))
    assert rep["ok"] is False and "not a directory" in rep["error"]


# --- shell-agnostic install ------------------------------------------------------

def test_the_login_shell_check_is_advisory_when_unanswerable(monkeypatch):
    """No $SHELL (containers, CI, Windows) is not a broken install."""
    monkeypatch.delenv("SHELL", raising=False)
    r = install_check.login_shell_report()
    assert r["checked"] is False
    assert doctor._login_shell_check().status == "info"


def test_a_shell_that_cannot_find_noodle_warns_with_its_own_fix(monkeypatch):
    """A fish user who never ran `uv tool update-shell` gets green from every
    other check and 'command not found' in their own terminal."""
    monkeypatch.setattr(install_check, "login_shell_report",
                        lambda *a, **k: {"shell": "fish", "checked": True,
                                         "found": False,
                                         "fix": install_check._PATH_FIX["fish"]})
    c = doctor._login_shell_check()
    assert c.status == "warn"
    assert "fish" in c.summary
    assert "uv tool update-shell" in c.remediation
    assert "fish_add_path" in c.remediation, "the shell's own PATH command"


def test_every_shell_gets_a_fix_line(monkeypatch):
    """The general answer is `uv tool update-shell` — it writes bash, zsh,
    fish and PowerShell itself. Only the manual fallback is shell-specific."""
    for shell in ("bash", "zsh", "fish", "nu", ""):
        monkeypatch.setenv("SHELL", f"/bin/{shell}" if shell else "")
        assert "uv tool update-shell" in install_check.login_shell_report()["fix"]
