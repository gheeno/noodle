"""NOOD_0241 — the shell-leak audit's fixes: agent-mode usage errors, CLI ⇄
tool parity + `noodle capabilities`, payload_complete, generated host
policy + `doctor --policy`, cd-free workspace resolution, invocation
hygiene, ledger door.

Pins (mirroring the audit's §8 assertions):
- the audit's exact failing command now PARSES — every documented tool
  argument has a CLI twin, so the exit-2 cascade that provoked the leaks
  cannot start;
- a usage error with --json / NOODLE_AGENT_MODE=1 is ONE parseable JSON
  envelope on stdout, exit code preserved, naming every unknown token,
  every valid flag and the spec keys — and never suggesting --help;
- `noodle capabilities --json` maps every ast-parsed MCP tool argument of
  author_test / run_and_report / probe_page to a flag or spec key;
- every payload carries payload_complete (boundary test in test_nood_0164);
- the generated policy denies each canned leak line while a bare
  Bash(noodle:*) allow-list would have PERMITTED the `--help | head` probe
  — proving the metacharacter deny entries are the load-bearing part;
- `doctor --policy` exit 0 on a fresh workspace, 1 when the policy rots;
- '.' resolves via NOODLE_WORKSPACE, then a noodle.yaml walk-up;
- a composed parent command line stamps invocation_clean: false; a bare
  invocation, an interactive shell and harness plumbing do not;
- the benchmark ledger records which door carried each attempt.
"""
import ast
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from noodle import agent_policy, capabilities, cli, invocation
from noodle.cli import app

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent


def _mcp_args(tool: str) -> list[str]:
    """The MCP tool's signature, ast-parsed — no mcp import needed."""
    tree = ast.parse((REPO / "noodle" / "mcp" / "server.py").read_text(
        encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == tool:
            return [a.arg for a in node.args.args + node.args.kwonlyargs]
    raise AssertionError(f"{tool} not found in mcp/server.py")


# --- agent-mode usage errors --------------------------------------------------

def test_unknown_option_with_json_is_one_parseable_envelope():
    r = runner.invoke(app, ["author", "--nope", "--json"])
    assert r.exit_code == 2                      # click's usage exit, preserved
    data = json.loads(r.output)
    assert data["ok"] is False
    assert data["error"] == "unknown_option"
    assert "--nope" in data["unknown"]
    assert "--spec" in data["valid_options"]
    assert "--help" not in data["valid_options"]
    assert data["spec_keys"] == list(cli._SPEC_KEYS)
    assert "--help" not in r.output              # never recruit the shell
    assert "Try" not in r.output


def test_every_unknown_option_is_named_not_just_the_first():
    r = runner.invoke(app, ["author", "--frobnicate", "--environment-values",
                            "x", "--json"])
    data = json.loads(r.output)
    assert set(data["unknown"]) >= {"--frobnicate", "--environment-values"}
    # a spec key guessed as a flag gets pointed at the spec key
    assert "environment_values" in data["did_you_mean"]["--environment-values"]


def test_agent_mode_env_var_flips_errors_to_json(monkeypatch):
    monkeypatch.setenv("NOODLE_AGENT_MODE", "1")
    r = runner.invoke(app, ["author", "--nope"])
    assert r.exit_code == 2
    assert json.loads(r.output)["error"] == "unknown_option"


def test_human_error_rendering_is_untouched(monkeypatch):
    monkeypatch.delenv("NOODLE_AGENT_MODE", raising=False)
    r = runner.invoke(app, ["author", "--nope"])
    assert r.exit_code == 2
    with pytest.raises(json.JSONDecodeError):
        json.loads(r.output)


def test_bad_parameter_honours_json_too():
    r = runner.invoke(app, ["author", "--headless", "--json"])
    assert r.exit_code == 2
    data = json.loads(r.output)
    assert data["ok"] is False and "--run" in data["detail"]


def test_green_path_exit_codes_survive_the_interception():
    assert runner.invoke(app, ["author", "--vocabulary", "--json"]).exit_code == 0
    r = runner.invoke(app, ["author", "--vocabulary", "--section", "nope",
                            "--json"])
    assert r.exit_code == 1                       # non-usage failure exit kept


# --- the audit's trigger command ----------------------------------------------

def test_the_audited_flag_guesses_now_parse(monkeypatch):
    """`--app`, `--base-url`, `--run`, `--headless` were guessed off the tool
    surface and all exited 2 as unknown options — the cascade that provoked
    `--help | head`. They are real flags now; the same line only asks for
    the missing prompt/spec."""
    r = runner.invoke(app, ["author", "--app", "x", "--base-url", "y",
                            "--run", "--headless", "--json"])
    data = json.loads(r.output)
    assert data["error"] != "unknown_option"
    assert "unknown" not in data
    assert "--spec" in data["detail"] or "--prompt" in data["detail"]


def test_run_forward_flags_reach_the_engine(monkeypatch):
    seen = {}
    monkeypatch.setattr("noodle.repl.core.author_test",
                        lambda **kw: seen.update(kw) or {"ok": True,
                                                         "ready": True})
    monkeypatch.setattr("noodle.repl.core.collapse_green",
                        lambda result, workspace=None: result)
    r = runner.invoke(app, ["author", "--prompt", "1. go to https://x",
                            "--run", "--headless", "--retries", "2",
                            "--no-serve-reports", "--app", "shop",
                            "--base-url", "https://x", "--json"])
    assert r.exit_code == 0, r.output
    assert seen["headless"] is True and seen["retries"] == 2
    assert seen["serve_reports"] is False
    assert seen["app_name"] == "shop" and seen["base_url"] == "https://x"
    assert seen["door"] == "cli"


def test_flag_and_spec_key_are_mutually_exclusive():
    r = runner.invoke(app, ["author", "--app-name", "a", "--spec-text",
                            "app_name: b\nbase_url: https://x\ngoal: {}",
                            "--json"])
    assert r.exit_code == 2
    assert "app_name" in json.loads(r.output)["detail"]


# --- capabilities: the parity manifest ------------------------------------------

def test_capabilities_covers_every_mcp_tool_argument():
    m = capabilities.manifest()
    for op, tool in (("author", "author_test"), ("run", "run_and_report"),
                     ("probe", "probe_page")):
        args = m["operations"][op]["arguments"]
        for name in _mcp_args(tool):
            assert name in args, f"{tool}.{name} missing from manifest"
            entry = args[name]
            assert entry.get("flag") or entry.get("spec_key"), \
                f"{tool}.{name} has no CLI counterpart — parity broken"


def test_capabilities_flags_exist_on_the_real_commands():
    m = capabilities.manifest()
    author = m["operations"]["author"]["arguments"]
    assert author["app_name"]["flag"] == "--app-name / --app"
    assert author["run_after_author"]["flag"] == "--run"
    assert author["headless"]["flag"].startswith("--headless")
    assert m["spec_keys"] == list(cli._SPEC_KEYS)


def test_capabilities_command_and_slices():
    r = runner.invoke(app, ["capabilities"])
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert data["ok"] is True and data["payload_complete"] is True
    r = runner.invoke(app, ["capabilities", "--operation", "author"])
    assert json.loads(r.output)["operation"] == "author"
    assert runner.invoke(app, ["capabilities", "--operation", "nope"]).exit_code == 1


def test_spec_keys_constant_matches_the_spec_branch():
    """The literal data.get() calls in author's spec branch ARE the spec
    surface; the constant must never drift from them."""
    import inspect
    src = inspect.getsource(cli.author)
    for key in cli._SPEC_KEYS:
        assert f'data.get("{key}"' in src, f"spec key {key} not read from spec"


# --- policy: prose becomes a control --------------------------------------------

def test_policy_denies_every_canned_leak_and_allows_clean_noodle():
    for leak in agent_policy.PROBE_LEAKS:
        assert agent_policy.denies(leak), leak
    assert agent_policy.denies(agent_policy.PROBE_CLEAN) is None
    assert agent_policy.allows(agent_policy.PROBE_CLEAN)


def test_a_bare_allow_list_would_have_permitted_the_help_pipe():
    """The audit's key implementation detail: `noodle author --help | head
    -50` still STARTS with noodle, so Bash(noodle:*) alone waves it
    through. The composition deny entries are what actually block it."""
    leak = "noodle author --help | head -50"
    assert agent_policy.allows(leak)                       # prefix says yes
    assert agent_policy.denies(leak, agent_policy.DENY_COMPOSITION)
    assert agent_policy.denies(leak, agent_policy.DENY_EXECUTABLES) is None


def test_install_merges_and_is_idempotent(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"allow": ["Bash(make:*)"]}, "model": "opus"}),
        encoding="utf-8")
    first = agent_policy.install(tmp_path)
    assert set(first.values()) <= {"created", "updated"}
    merged = json.loads((tmp_path / ".claude" / "settings.json").read_text(
        encoding="utf-8"))
    assert merged["model"] == "opus"                       # foreign key kept
    assert "Bash(make:*)" in merged["permissions"]["allow"]
    assert set(agent_policy.DENY) <= set(merged["permissions"]["deny"])
    assert set(agent_policy.install(tmp_path).values()) == {"kept"}


def test_init_writes_policy_and_doctor_policy_gates(tmp_path, monkeypatch):
    r = runner.invoke(app, ["init", str(tmp_path)])
    assert r.exit_code == 0, r.output
    for rel in agent_policy.FILES:
        assert (tmp_path / rel).is_file(), rel
    r = runner.invoke(app, ["doctor", str(tmp_path), "--policy"])
    assert r.exit_code == 0, r.output
    assert "workspace.policy" in r.output
    # rot the enforcing file: doctor --policy must fail loudly
    f = tmp_path / ".claude" / "settings.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    data["permissions"]["deny"] = []
    f.write_text(json.dumps(data), encoding="utf-8")
    r = runner.invoke(app, ["doctor", str(tmp_path), "--policy"])
    assert r.exit_code == 1
    assert "FAIL" in r.output


def test_init_no_agent_policy_opts_out(tmp_path):
    r = runner.invoke(app, ["init", str(tmp_path), "--no-agent-policy"])
    assert r.exit_code == 0, r.output
    assert not (tmp_path / ".claude" / "settings.json").exists()
    assert not (tmp_path / ".copilot" / "agent-policy.json").exists()


def test_doctor_policy_outside_a_workspace_is_exit_2(tmp_path):
    r = runner.invoke(app, ["doctor", str(tmp_path), "--policy"])
    assert r.exit_code == 2


# --- cd-free workspace resolution ------------------------------------------------

def test_explicit_workspace_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("NOODLE_WORKSPACE", str(tmp_path))
    assert cli._ws_resolve("elsewhere") == "elsewhere"


def test_env_var_beats_walk_up(monkeypatch, tmp_path):
    monkeypatch.setenv("NOODLE_WORKSPACE", str(tmp_path / "ws"))
    assert cli._ws_resolve(".") == str(tmp_path / "ws")


def test_walk_up_finds_the_nearest_noodle_yaml(monkeypatch, tmp_path):
    monkeypatch.delenv("NOODLE_WORKSPACE", raising=False)
    (tmp_path / "noodle.yaml").write_text("tests_dir: noodle_tests\n",
                                          encoding="utf-8")
    sub = tmp_path / "noodle_tests" / "app"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert cli._ws_resolve(".") == str(tmp_path)
    monkeypatch.chdir(tmp_path)                # cwd IS the workspace → '.'
    assert cli._ws_resolve(".") == "."


def test_no_workspace_anywhere_stays_dot(monkeypatch, tmp_path):
    monkeypatch.delenv("NOODLE_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli._ws_resolve(".") == "."


# --- invocation hygiene ------------------------------------------------------------

@pytest.mark.parametrize("cmd,dirty", [
    ("/bin/zsh -c noodle author --help | head -50", True),
    ("/bin/zsh -c cd ws && noodle probe https://x --compact", True),
    ("/bin/bash -c noodle author --spec-text 'spec' --json 2>&1 | tee /tmp/o",
     True),
    ("/bin/zsh -c noodle capabilities --json > out.json", True),
    ("/bin/zsh -c noodle run noodle_tests/app --headless --retries 0 --json",
     False),
    ("/bin/zsh -c noodle author --prompt '1. go to https://x; search a&b' "
     "--json", False),                       # quoted metachars are NOT ours
    ("-zsh", False),                         # interactive shell
    ("node /usr/local/bin/some-agent --serve", False),   # direct exec
    ("/bin/zsh -c source /snap/env.zsh && eval x > /log ; noodle list --json",
     False),                                 # harness plumbing around noodle
])
def test_composition_detector(cmd, dirty):
    assert bool(invocation.composition(cmd)) is dirty, cmd


def test_composed_invocation_stamps_the_payload(monkeypatch):
    invocation.report.cache_clear()
    monkeypatch.setattr(invocation, "parent_command",
                        lambda: "/bin/zsh -c noodle author --help | head -5")
    stamp = invocation.report()
    assert stamp["invocation_clean"] is False
    assert "piped into head" in stamp["invocation_note"]
    invocation.report.cache_clear()
    monkeypatch.setattr(invocation, "parent_command", lambda: "-zsh")
    assert invocation.report() == {}
    invocation.report.cache_clear()


# --- ledger door -----------------------------------------------------------------

def test_ledger_records_the_door(tmp_path, monkeypatch):
    from noodle import benchmark as bench
    monkeypatch.setattr(bench, "active", lambda ws: {"specs": ["B1"]})
    monkeypatch.setattr(bench, "_spec_of", lambda fp, specs: "B1")
    monkeypatch.setattr(bench, "_tokens", lambda result, ws: 0)
    monkeypatch.setattr(bench, "_corrections", lambda a, r, res: (0, None))
    for door, want in ((None, "direct"), ("cli", "cli"), ("mcp", "mcp")):
        bench.record(str(tmp_path), feature_path="spec_B1.feature",
                     started=0.0, seconds=1.0, result={}, door=door)
        line = json.loads((tmp_path / bench.LEDGER_FILE).read_text(
            encoding="utf-8").splitlines()[-1])
        assert line["door"] == want


# --- MCP door parity (signature level) ---------------------------------------------

def test_mcp_author_test_carries_the_full_door_surface():
    """auto_fix was CLI-only (the NOOD_0197 parity test read the impl, not
    the door); the run-forwarding trio is new. All four must be on the MCP
    tool so neither surface is privileged."""
    args = _mcp_args("author_test")
    for name in ("auto_fix", "headless", "retries", "serve_reports"):
        assert name in args, f"author_test lost {name}"


def test_committed_openapi_names_the_new_arguments():
    spec = json.loads((REPO / "docs" / "openapi.json").read_text(
        encoding="utf-8"))
    body = spec["paths"]["/api/tools/author_test"]["post"]["requestBody"]
    props = body["content"]["application/json"]["schema"]["properties"]
    for name in ("auto_fix", "headless", "retries", "serve_reports"):
        assert name in props, f"docs/openapi.json is stale — missing {name}"
