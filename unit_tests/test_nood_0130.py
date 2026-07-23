"""NOOD_0130 — restore prompt-credential handling.

NOOD_0126 made every agent surface reject prompt credentials and leave empty
`*_secrets.env` placeholders, and CLI preflight was opt-in — so `author_test`
could report a credential missing yet return `ready: true`, and a plain
`noodle run` launched a browser into a login it could never complete. This
restores the proven NOOD_0125 workflow as a documented temporary policy: accept
prompt credentials via `secret_values`, write them ONLY into the app-local
gitignored `<app>_secrets.env`, never echo them, block `ready` on missing creds,
and preflight every CLI browser run by default. General, not app-specific.
No browser, no LLM anywhere.
"""
import json
from pathlib import Path

import pytest
from dotenv import dotenv_values

from noodle.repl import core


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "noodle.yaml").write_text("tests_dir: noodle_tests\nenv_file: .env\n")
    return ws


_FEATURE = (
    "@web\nFeature: Login\n\n  Scenario: signs in\n"
    '    Given User is on "{env:SHOP}"\n'
    '    When User enters "{env:SHOP_USERNAME}" in the username field\n'
    '    And User enters "{env:SHOP_PASSWORD}" in the password field\n'
    "    And User clicks the login button\n"
    '    Then User should see "Dashboard"\n'
)
_POM = 'match: {}\nusername field:\n  id: "u"\nlogin:\n  css: "button"\n'


def _author(ws, **over):
    kw = dict(app_name="Shop", base_url="http://localhost:9",
              feature_path="login", feature_content=_FEATURE, pom_content=_POM,
              required_secret_keys=["SHOP_USERNAME", "SHOP_PASSWORD"],
              workspace=str(ws))
    kw.update(over)
    return core.author_test(**kw)


def _secrets_path(ws) -> Path:
    return ws / "noodle_tests" / "web" / "shop" / "resources" / "shop_secrets.env"


def _scaffold_behave(ws) -> None:
    """A behave-base marker so `noodle run <feature>` gets past _find_behave_base
    to the (patched) subprocess call — the CLI resolves the base by an ancestor
    `steps/` dir. Nothing runs; the subprocess is mocked in these tests."""
    (ws / "noodle_tests" / "web" / "shop" / "features" / "steps").mkdir(
        parents=True, exist_ok=True)


# --- Check: prompt credentials populate the app-local secrets file -----------

def test_secret_values_populate_app_local_file(tmp_path):
    ws = _ws(tmp_path)
    r = _author(ws, secret_values={"SHOP_USERNAME": "alice", "SHOP_PASSWORD": "hunter2"})
    assert r["ok"]
    d = dotenv_values(_secrets_path(ws))
    assert d["SHOP_USERNAME"] == "alice" and d["SHOP_PASSWORD"] == "hunter2"
    # written to the app package, never the workspace root
    assert not (ws / "secrets.env").exists() and not (ws / ".env").exists()


def test_ready_true_once_all_referenced_creds_supplied(tmp_path):
    ws = _ws(tmp_path)
    r = _author(ws, secret_values={"SHOP_USERNAME": "alice", "SHOP_PASSWORD": "hunter2"})
    assert r["ready"] and r["blocking"] == [] and r["missing_secret_keys"] == []


# --- Check: no secret value appears in results or CLI output -----------------

def test_secret_values_never_in_result(tmp_path):
    ws = _ws(tmp_path)
    r = _author(ws, secret_values={"SHOP_USERNAME": "alice", "SHOP_PASSWORD": "topsecret9"})
    blob = json.dumps(r, default=str)
    assert "alice" not in blob and "topsecret9" not in blob
    # created_secret_keys reports only KEY NAMES for supplied values (already present)
    assert r["created_secret_keys"] == []


def test_cli_author_never_prints_secret_values(tmp_path):
    from typer.testing import CliRunner

    from noodle.cli import app
    ws = _ws(tmp_path)
    spec = tmp_path / "spec.yaml"
    spec.write_text(json.dumps({
        "app_name": "Shop", "base_url": "http://localhost:9",
        "feature_path": "login", "feature_content": _FEATURE, "pom_content": _POM,
        "required_secret_keys": ["SHOP_USERNAME", "SHOP_PASSWORD"],
        "secret_values": {"SHOP_USERNAME": "alice", "SHOP_PASSWORD": "topsecret9"},
        "overwrite": True}))                       # overwrite is a spec field, not a flag
    for args in (["author", "--spec", str(spec), "-w", str(ws)],
                 ["author", "--spec", str(spec), "-w", str(ws), "--json"]):
        r = CliRunner().invoke(app, args)
        assert r.exit_code == 0, r.output
        assert "alice" not in r.output and "topsecret9" not in r.output
    # but the file really got the values
    d = dotenv_values(_secrets_path(ws))
    assert d["SHOP_USERNAME"] == "alice" and d["SHOP_PASSWORD"] == "topsecret9"


# --- Check: existing unrelated keys/comments survive updates -----------------

def test_unrelated_keys_and_comments_preserved(tmp_path):
    ws = _ws(tmp_path)
    _author(ws)                                   # scaffold placeholders
    sp = _secrets_path(ws)
    sp.write_text("# my note\nUNRELATED=leave-me\nSHOP_USERNAME=old\nSHOP_PASSWORD=old\n")
    r = _author(ws, overwrite=True, secret_values={"SHOP_PASSWORD": "new"})
    assert r["ok"]
    text = sp.read_text()
    d = dotenv_values(sp)
    assert "# my note" in text and d["UNRELATED"] == "leave-me"
    assert d["SHOP_USERNAME"] == "old"            # not supplied → untouched
    assert d["SHOP_PASSWORD"] == "new"            # supplied → replaced


def test_supplied_value_overrides_existing_same_key(tmp_path):
    ws = _ws(tmp_path)
    _author(ws)
    sp = _secrets_path(ws)
    sp.write_text("SHOP_USERNAME=alice\nSHOP_PASSWORD=old\n")
    _author(ws, overwrite=True, secret_values={"SHOP_PASSWORD": "rotated"})
    assert dotenv_values(sp)["SHOP_PASSWORD"] == "rotated"


def test_values_with_special_chars_round_trip(tmp_path):
    ws = _ws(tmp_path)
    tricky = 'p@ss "w0rd" #1\\x'
    _author(ws, secret_values={"SHOP_USERNAME": "a b", "SHOP_PASSWORD": tricky})
    d = dotenv_values(_secrets_path(ws))
    assert d["SHOP_USERNAME"] == "a b" and d["SHOP_PASSWORD"] == tricky


# --- Check: a write failure restores the original secrets file byte-for-byte -

def test_write_failure_restores_secrets_bytes(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    _author(ws)
    sp = _secrets_path(ws)
    sp.write_text("SHOP_USERNAME=keep\nSHOP_PASSWORD=keep\n")
    before = sp.read_bytes()

    real = core.os.replace
    def boom(src, dst):                           # fail only on the feature write
        if str(dst).endswith(".feature"):
            raise OSError("disk full")
        return real(src, dst)
    monkeypatch.setattr(core.os, "replace", boom)

    r = _author(ws, overwrite=True, secret_values={"SHOP_PASSWORD": "rotated"})
    assert not r["ok"] and "rolled back" in r["error"]
    assert sp.read_bytes() == before              # the rotation never stuck


# --- Check: missing/empty credentials return ready:false ---------------------

def test_missing_credential_blocks_ready(tmp_path):
    ws = _ws(tmp_path)
    r = _author(ws, secret_values={"SHOP_USERNAME": "alice"})   # PASSWORD absent
    assert r["ok"] and not r["ready"]
    assert r["missing_secret_keys"] == ["SHOP_PASSWORD"]
    assert any("SHOP_PASSWORD" in b for b in r["blocking"])


def test_no_secret_values_leaves_empty_placeholders_not_ready(tmp_path):
    ws = _ws(tmp_path)
    r = _author(ws)                               # nothing supplied
    d = dotenv_values(_secrets_path(ws))
    assert d["SHOP_USERNAME"] == "" and d["SHOP_PASSWORD"] == ""
    assert not r["ready"]
    assert set(r["missing_secret_keys"]) == {"SHOP_USERNAME", "SHOP_PASSWORD"}


# --- Check: key/value validation --------------------------------------------

def test_invalid_secret_key_rejected_before_any_write(tmp_path):
    ws = _ws(tmp_path)
    r = core.author_test(app_name="Shop", base_url="http://h", feature_path="x",
                         feature_content=_FEATURE, secret_values={"bad key": "v"},
                         workspace=str(ws))
    assert not r["ok"] and "invalid secret key" in r["error"]
    assert not (ws / "noodle_tests").exists()     # nothing written


def test_empty_secret_value_rejected(tmp_path):
    ws = _ws(tmp_path)
    r = core.author_test(app_name="Shop", base_url="http://h", feature_path="x",
                         feature_content=_FEATURE, secret_values={"SHOP_PASSWORD": ""},
                         workspace=str(ws))
    assert not r["ok"] and "empty value" in r["error"]


# --- Check: plain `noodle run` preflights by default -------------------------

def _run(cli, ws, extra=()):
    from typer.testing import CliRunner
    feat = "noodle_tests/web/shop/features/login.feature"
    return CliRunner().invoke(cli.app, ["run", feat, "-w", str(ws), *extra])


def test_plain_run_preflights_and_launches_no_browser(tmp_path, monkeypatch):
    from noodle import cli
    ws = _ws(tmp_path)
    _author(ws)                                   # empty placeholder secrets
    ran = []
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: ran.append(1))
    r = _run(cli, ws)                             # NO --preflight flag
    assert r.exit_code == 2 and ran == []         # blocked before behave
    assert "preflight failed" in r.stdout


def test_no_preflight_escape_hatch_reaches_browser(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from noodle import cli
    ws = _ws(tmp_path)
    _author(ws)                                   # empty placeholder secrets
    _scaffold_behave(ws)
    # pin so the CLI's single-app-run block doesn't mutate (and leak) os.environ
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    ran = []
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: ran.append(1) or MagicMock(returncode=0))
    _run(cli, ws, ["--no-preflight"])
    assert ran == [1]                             # the run proceeded


def test_complete_creds_pass_preflight_and_reach_browser(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from noodle import cli
    ws = _ws(tmp_path)
    _author(ws, secret_values={"SHOP_USERNAME": "alice", "SHOP_PASSWORD": "hunter2"})
    _scaffold_behave(ws)
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    ran = []
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: ran.append(1) or MagicMock(returncode=0))
    r = _run(cli, ws)                             # default preflight, but creds are set
    assert ran == [1] and "preflight failed" not in r.stdout


# --- Check: MCP author_test forwards secret_values, omits them from results --

def test_mcp_author_forwards_secret_values_write_only(tmp_path, monkeypatch):
    server = pytest.importorskip("noodle.mcp.server")
    ws = _ws(tmp_path)
    monkeypatch.setattr(server, "_WORKSPACE", str(ws))
    r = server.author_test(app_name="Shop", base_url="http://localhost:9",
                           feature_path="login", feature_content=_FEATURE,
                           pom_content=_POM,
                           required_secret_keys=["SHOP_USERNAME", "SHOP_PASSWORD"],
                           secret_values={"SHOP_USERNAME": "alice", "SHOP_PASSWORD": "topsecret9"},
                           workspace=None)
    assert r["ok"] and r["ready"]
    assert "alice" not in json.dumps(r, default=str)
    assert dotenv_values(_secrets_path(ws))["SHOP_PASSWORD"] == "topsecret9"


# --- Check: generated secrets path stays gitignored --------------------------

def test_generated_secrets_path_is_gitignored():
    # NOOD_0118 rule scaffolded by `noodle init`; the value-carrying file the
    # NOOD_0130 path writes must match it so a populated secret can't be committed.
    from noodle.cli import _GITIGNORE
    assert "**/resources/*_secrets.env" in _GITIGNORE


# --- (ceiling checks retired by NOOD_0159 — see noodle/instruction_budget.py)


# --- Check: every always-on surface states the app-local write-only policy ----

def test_prompt_credential_policy_on_every_surface():
    from noodle import cli
    from unit_tests.test_nood_0110 import REPO
    surfaces = {
        "AGENTS.md": cli._AGENTS_MD,
        "PROMPT_TEMPLATE": cli._PROMPT_TEMPLATE,
        "claude skill": (REPO / ".claude/skills/noodle/SKILL.md").read_text(),
        "copilot skill": (REPO / ".copilot/skills/noodle/SKILL.md").read_text(),
    }
    for name, text in surfaces.items():
        low = text.lower()
        assert "secrets.env" in low, f"{name}: no secrets.env pointer"
        # the write-only-to-the-app-file rule, in surface-appropriate wording
        assert "prompt credential" in low or "credentials in the prompt" in low \
            or "any value here" in low, f"{name}: no prompt-credential policy"
