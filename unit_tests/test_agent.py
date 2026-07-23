"""NOOD_0035 — agent + workspace self-checks. No browser, no LLM, no network."""
import json
import os

from noodle import config
from noodle.repl import generate, reflect, repl
from noodle.reporting import summary


def _clean_llm_env(monkeypatch):
    """Guarantee NOODLE_MODEL/NOODLE_LLM_URL are absent going in AND properly
    restored by monkeypatch's teardown coming out. monkeypatch.delenv(...,
    raising=False) alone does NOT register an undo when the var is already
    absent (pytest only tracks a delitem when the key pre-exists) — so a test
    that then sets it via raw os.environ[...] = ... would leak it into every
    test that runs afterward in the same process. setenv-then-delenv forces
    pytest to record the "was absent" undo regardless."""
    for key in ("NOODLE_MODEL", "NOODLE_LLM_URL"):
        monkeypatch.setenv(key, "unset")
        monkeypatch.delenv(key)


def test_config_defaults_and_override(tmp_path):
    assert config.load(str(tmp_path))["tests_dir"] == "tests"
    (tmp_path / "noodle.yaml").write_text("tests_dir: custom_tests\nheadless: true\n")
    cfg = config.load(str(tmp_path))
    assert cfg["tests_dir"] == "custom_tests"
    assert cfg["headless"] is True
    assert cfg["browser"] == "chromium"  # unspecified key keeps default


def test_template_pick():
    assert generate.pick_template("login page") is generate._LOGIN
    assert generate.pick_template("search the catalog") is generate._SEARCH
    assert generate.pick_template("browse around") is generate._GENERIC


def test_generate_writes_files(tmp_path):
    cfg = config.load(str(tmp_path))
    feat, pom = generate.generate("the login page", "https://saucedemo.com",
                                  cfg, str(tmp_path))
    assert feat.exists() and pom.exists()
    assert feat.name == "login.feature"
    assert feat.parent == tmp_path / "tests" / "web" / "saucedemo" / "features"
    assert pom.parent == tmp_path / "tests" / "web" / "saucedemo" / "resources" / "pageobjects"
    text = feat.read_text()
    assert 'User is on "https://saucedemo.com"' in text
    assert "username field" in pom.read_text()


def test_generate_scaffolds_resources(tmp_path):
    cfg = config.load(str(tmp_path))
    generate.generate("the login page", "https://saucedemo.com/login", cfg, str(tmp_path))
    res = tmp_path / "tests" / "web" / "saucedemo" / "resources"
    # NOOD_0135 — the FULL supplied URL survives (origin-only storage sent the
    # first run to the host root and read as locator rot)
    assert (res / "saucedemo_environments.yaml").read_text() == "saucedemo: https://saucedemo.com/login\n"
    assert (res / "saucedemo_secrets.env").exists()          # NOOD_0118 — gitignored working file, not a committed .example
    assert not (res / "saucedemo_secrets.env.example").exists()
    # second generate for the same app must not clobber a hand-edited file
    (res / "saucedemo_environments.yaml").write_text("saucedemo: https://staging.saucedemo.com\n")
    generate.generate("search", "https://saucedemo.com", cfg, str(tmp_path))
    assert "staging" in (res / "saucedemo_environments.yaml").read_text()


def test_configure_llm_none_when_unconfigured(tmp_path, monkeypatch):
    _clean_llm_env(monkeypatch)
    cfg = config.load(str(tmp_path))
    assert repl._configure_llm(str(tmp_path), cfg, None, None) is None


def test_configure_llm_picks_up_env_persisted_by_init(tmp_path, monkeypatch):
    """`noodle init --llm` writes NOODLE_MODEL into .env — a fresh noodle repl
    invocation (no --llm flag) must pick it up and enable free-form mode."""
    _clean_llm_env(monkeypatch)
    (tmp_path / ".env").write_text("NOODLE_MODEL=ollama/llava\n")
    cfg = config.load(str(tmp_path))
    result = repl._configure_llm(str(tmp_path), cfg, None, None)
    assert result == "auto"
    assert os.environ["NOODLE_MODEL"] == "ollama/llava"


def test_configure_llm_explicit_flag_overrides_env(tmp_path, monkeypatch):
    _clean_llm_env(monkeypatch)
    (tmp_path / ".env").write_text("NOODLE_MODEL=ollama/llava\n")
    cfg = config.load(str(tmp_path))
    result = repl._configure_llm(str(tmp_path), cfg, "claude", None)
    assert result == "claude"
    assert os.environ["NOODLE_MODEL"] == "anthropic/claude-sonnet-5"


def test_configure_llm_ollama_sets_default_url(tmp_path, monkeypatch):
    _clean_llm_env(monkeypatch)
    cfg = config.load(str(tmp_path))
    repl._configure_llm(str(tmp_path), cfg, "ollama", None)
    assert os.environ["NOODLE_MODEL"] == "ollama/llama3.2"
    assert os.environ["NOODLE_LLM_URL"] == "http://localhost:11434"


def test_configure_llm_model_override(tmp_path, monkeypatch):
    _clean_llm_env(monkeypatch)
    cfg = config.load(str(tmp_path))
    repl._configure_llm(str(tmp_path), cfg, "ollama", "ollama/llava")
    assert os.environ["NOODLE_MODEL"] == "ollama/llava"


def test_normalize_url():
    assert repl._normalize_url("youtube.com") == "https://youtube.com"
    assert repl._normalize_url("https://youtube.com") == "https://youtube.com"
    assert repl._normalize_url("http://localhost:3333") == "http://localhost:3333"


def test_dispatch_create_bare_host(tmp_path):
    cfg = config.load(str(tmp_path))
    repl.dispatch("create test for login at saucedemo.com", cfg, str(tmp_path), llm=None)
    assert (tmp_path / "tests" / "web" / "saucedemo" / "features" / "login.feature").exists()


def test_dispatch_freeform_needs_llm(tmp_path, capsys):
    cfg = config.load(str(tmp_path))
    repl.dispatch("go to youtube.com and search for MKBHD", cfg, str(tmp_path), llm=None)
    assert "need --llm" in capsys.readouterr().out


def test_dispatch_freeform_with_llm(tmp_path, capsys, monkeypatch):
    cfg = config.load(str(tmp_path))
    monkeypatch.setattr(repl, "_extract_plan",
                        lambda text: [{"action": "create", "description": "search for MKBHD",
                                      "url": "youtube.com"}])
    monkeypatch.setattr("noodle.repl.generate.generate_llm",
                        lambda desc, url, c, w, overwrite=False: generate.generate(desc, url, c, w, overwrite))
    keep = repl.dispatch("go to youtube.com, search for MKBHD, and check results",
                         cfg, str(tmp_path), llm="ollama")
    assert keep is True
    feats = list((tmp_path / "tests" / "web" / "youtube" / "features").glob("*.feature"))
    assert feats, "free-form request should generate a feature under the youtube app"
    assert 'User is on "https://youtube.com"' in feats[0].read_text()


def test_extract_plan_parses_json(monkeypatch):
    monkeypatch.setattr("noodle.llm.client.ask",
                        lambda p, system=None: '[{"action": "create", "description": "search", "url": "youtube.com"}]')
    assert repl._extract_plan("go to youtube.com and search") == [
        {"action": "create", "description": "search", "url": "youtube.com"}]
    monkeypatch.setattr("noodle.llm.client.ask", lambda p, system=None: '[]')
    assert repl._extract_plan("hello there") == []


def test_dispatch_freeform_compound_create_run_summary(tmp_path, monkeypatch):
    """Phase 3: one line that asks to create, run, and summarize executes
    all three steps in order, without three separate REPL turns."""
    cfg = config.load(str(tmp_path))
    monkeypatch.setattr(repl, "_extract_plan", lambda text: [
        {"action": "create", "description": "search for MKBHD", "url": "youtube.com"},
        {"action": "run"},
        {"action": "summary"},
    ])
    monkeypatch.setattr("noodle.repl.generate.generate_llm",
                        lambda desc, url, c, w, overwrite=False: generate.generate(desc, url, c, w, overwrite))
    calls = []
    monkeypatch.setattr(repl, "_noodle", lambda *a, **kw: calls.append(a))
    state = {}
    keep = repl.dispatch("create a test for youtube search, run it, and show me the report",
                         cfg, str(tmp_path), llm="ollama", state=state)
    assert keep is True
    assert calls[0] == ("run", state["last_feature"])
    assert calls[1] == ("summary", "--llm", "ollama")


def test_app_from_url():
    assert generate._app_from_url("https://www.example.com/en.html") == "example"
    assert generate._app_from_url("http://localhost:3333") == "localhost"


def test_dispatch_create(tmp_path, capsys):
    cfg = config.load(str(tmp_path))
    keep = repl.dispatch('create test for login at https://example.com',
                         cfg, str(tmp_path), llm=None)
    assert keep is True
    assert (tmp_path / "tests" / "web" / "example" / "features" / "login.feature").exists()


def test_generate_skips_existing_without_overwrite(tmp_path, capsys):
    cfg = config.load(str(tmp_path))
    feat, pom = generate.generate("the login page", "https://saucedemo.com", cfg, str(tmp_path))
    feat.write_text("MODIFIED")
    result = generate.generate("the login page", "https://saucedemo.com", cfg, str(tmp_path))
    assert result is None
    assert feat.read_text() == "MODIFIED"
    assert "already exists" in capsys.readouterr().out


def test_generate_overwrite_replaces(tmp_path):
    cfg = config.load(str(tmp_path))
    feat, pom = generate.generate("the login page", "https://saucedemo.com", cfg, str(tmp_path))
    feat.write_text("MODIFIED")
    result = generate.generate("the login page", "https://saucedemo.com", cfg, str(tmp_path),
                               overwrite=True)
    assert result is not None
    assert "MODIFIED" not in feat.read_text()


def _write_failure_result(results_dir, message="expected ok, got nope"):
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "a-result.json").write_text(json.dumps({
        "name": "Scenario", "status": "failed",
        "labels": [{"name": "feature", "value": "Feat"}],
        "steps": [{"name": 'Then User should see "ok"', "status": "failed"}],
        "statusDetails": {"message": message},
        "start": 0, "stop": 1}))


def test_reflect_returns_false_when_nothing_failed(tmp_path):
    (tmp_path / "artifacts" / "allure-results").mkdir(parents=True)
    feat = tmp_path / "x.feature"
    feat.write_text("ORIGINAL")
    pom = tmp_path / "x_pom.yaml"
    pom.write_text("pom")
    assert reflect.try_fix(feat, pom, str(tmp_path)) is False
    assert feat.read_text() == "ORIGINAL"


def test_reflect_keeps_fix_that_reduces_failures(tmp_path, monkeypatch):
    results = tmp_path / "artifacts" / "allure-results"
    _write_failure_result(results)
    feat = tmp_path / "x.feature"
    feat.write_text("ORIGINAL")
    pom = tmp_path / "x_pom.yaml"
    pom.write_text("pom")

    monkeypatch.setattr("noodle.llm.client.ask", lambda p, system=None: "FIXED")
    calls = []

    def fake_run(path, workspace):
        calls.append(path)
        for f in results.glob("*-result.json"):
            f.unlink()  # simulate: re-run passed, before_all cleared results

    monkeypatch.setattr(reflect, "_run", fake_run)

    assert reflect.try_fix(feat, pom, str(tmp_path)) is True
    assert calls == [str(feat)]
    assert feat.read_text() == "FIXED\n"


def test_reflect_reverts_fix_that_doesnt_help(tmp_path, monkeypatch):
    results = tmp_path / "artifacts" / "allure-results"
    _write_failure_result(results)
    feat = tmp_path / "x.feature"
    feat.write_text("ORIGINAL")
    pom = tmp_path / "x_pom.yaml"
    pom.write_text("pom")

    monkeypatch.setattr("noodle.llm.client.ask", lambda p, system=None: "STILL BROKEN")
    monkeypatch.setattr(reflect, "_run", lambda path, workspace: None)  # failure stays on disk

    assert reflect.try_fix(feat, pom, str(tmp_path)) is False
    assert feat.read_text() == "ORIGINAL"


def test_dispatch_create_skips_existing_without_overwrite(tmp_path):
    cfg = config.load(str(tmp_path))
    repl.dispatch("create test for login at saucedemo.com", cfg, str(tmp_path), llm=None)
    feat = tmp_path / "tests" / "web" / "saucedemo" / "features" / "login.feature"
    feat.write_text("MODIFIED")
    repl.dispatch("create test for login at saucedemo.com", cfg, str(tmp_path), llm=None)
    assert feat.read_text() == "MODIFIED"
    repl.dispatch("create test for login at saucedemo.com overwrite", cfg, str(tmp_path), llm=None)
    assert "MODIFIED" not in feat.read_text()


def test_dispatch_run_that_uses_last_created(tmp_path, monkeypatch):
    cfg = config.load(str(tmp_path))
    calls = []
    monkeypatch.setattr(repl, "_noodle", lambda *a, **kw: calls.append(a))
    state = {}
    repl.dispatch("create test for login at saucedemo.com", cfg, str(tmp_path), None, state)
    repl.dispatch("run that", cfg, str(tmp_path), None, state)
    assert calls[-1] == ("run", state["last_feature"])


def test_dispatch_run_that_without_prior_create(tmp_path, capsys):
    cfg = config.load(str(tmp_path))
    repl.dispatch("run that", cfg, str(tmp_path), None, {})
    assert "Nothing created yet" in capsys.readouterr().out


def test_dispatch_quit_and_help(tmp_path, capsys):
    cfg = config.load(str(tmp_path))
    assert repl.dispatch("quit", cfg, str(tmp_path), None) is False
    assert repl.dispatch("help", cfg, str(tmp_path), None) is True
    assert "commands" in capsys.readouterr().out


def test_summary_counts(tmp_path):
    d = tmp_path / "allure-results"
    d.mkdir()
    (d / "a-result.json").write_text(json.dumps({
        "name": "Valid login", "status": "passed",
        "labels": [{"name": "feature", "value": "Login"}],
        "steps": [], "start": 1000, "stop": 2000}))
    (d / "b-result.json").write_text(json.dumps({
        "name": "Bad login", "status": "failed",
        "labels": [{"name": "feature", "value": "Login"}],
        "steps": [{"name": 'Then User should see "ok"', "status": "failed"}],
        "start": 2000, "stop": 5000}))
    s = summary.collect(str(d))
    assert s["passed"] == 1 and s["failed"] == 1
    assert s["seconds"] == 4
    assert s["failures"][0]["feature"] == "Login"
    out = summary.render(str(d))
    assert "1 passed" in out and "1 failed" in out and "Bad login" in out


# --- NOOD_0019 — detection-based resource scaffolding ------------------------

def test_scaffold_referenced_resources_creates_only_whats_used(tmp_path):
    app_dir = tmp_path / "tests" / "web" / "busterblock"
    plain = '@web\nFeature: X\n  Scenario: Y\n    Then User should see "ok"\n'
    assert generate._scaffold_referenced_resources(app_dir, plain) == []

    feature = (
        "@web\nFeature: X\n"
        "  @precondition:cart_preseeded\n"
        "  Scenario: Y\n"
        "    Given uses this payload 'payloads/seed_cart.json'\n"
        "    When User calls the function "
        "'resources/functions/helpers.py:make_username' and saves the result as `USERNAME`\n"
    )
    # mirrors generate_llm()'s real pipeline: rewrite short function paths first
    feature = generate._rewrite_function_paths(feature, app_dir)
    written = generate._scaffold_referenced_resources(app_dir, feature)
    assert len(written) == 3
    payload = app_dir / "resources" / "payloads" / "seed_cart.json"
    func = app_dir / "resources" / "functions" / "helpers.py"
    precond = app_dir / "resources" / "preconditions.yaml"
    assert set(written) == {payload, func, precond}
    assert "def make_username(" in func.read_text()
    assert "cart_preseeded:" in precond.read_text()

    # a second, unrelated function appended to the same file, not clobbered
    feature2 = feature.replace("make_username", "greet")
    generate._scaffold_referenced_resources(app_dir, feature2)
    text = func.read_text()
    assert "def make_username(" in text and "def greet(" in text


def test_rewrite_function_paths_prefixes_app_relative_form(tmp_path):
    app_dir = tmp_path / "tests" / "web" / "busterblock"
    feature = "When User calls the function 'resources/functions/helpers.py:add'\n"
    out = generate._rewrite_function_paths(feature, app_dir)
    assert f"'{app_dir.as_posix()}/resources/functions/helpers.py:add'" in out
    # module form (no .py, no resources/ prefix) is left untouched
    module_form = "When User calls the function 'os.path:basename'\n"
    assert generate._rewrite_function_paths(module_form, app_dir) == module_form


# --- NOOD_0019 — scaffold_one / granular commands ----------------------------

def test_scaffold_one_writes_each_kind(tmp_path):
    cfg = config.load(str(tmp_path))
    res = tmp_path / "tests" / "web" / "busterblock" / "resources"

    p = generate.scaffold_one("environments", "busterblock", cfg, str(tmp_path),
                              url="http://localhost:3333")
    assert p.read_text() == "busterblock: http://localhost:3333\n"

    p = generate.scaffold_one("secrets", "busterblock", cfg, str(tmp_path),
                              fields=["username", "password"])
    assert "BUSTERBLOCK_USERNAME=" in p.read_text()
    assert "BUSTERBLOCK_PASSWORD=" in p.read_text()

    p = generate.scaffold_one("pom", "busterblock", cfg, str(tmp_path))
    assert p == res / "pageobjects" / "busterblock_pom.yaml"
    assert "maps a phrase used in a .feature step" in p.read_text().lower()

    p = generate.scaffold_one("preconditions", "busterblock", cfg, str(tmp_path),
                              name="reset_state")
    assert "reset_state:" in p.read_text()

    p = generate.scaffold_one("payload", "busterblock", cfg, str(tmp_path), name="seed_cart")
    assert p == res / "payloads" / "seed_cart.json"

    p = generate.scaffold_one("function", "busterblock", cfg, str(tmp_path),
                              name="helpers", fields=["make_username"])
    assert "def make_username(" in p.read_text()

    p = generate.scaffold_one("data", "busterblock", cfg, str(tmp_path),
                              name="users", fields=["username", "password"])
    assert p.read_text() == "username,password\n"


def test_scaffold_one_never_clobbers_existing(tmp_path, capsys):
    cfg = config.load(str(tmp_path))
    p = generate.scaffold_one("environments", "busterblock", cfg, str(tmp_path),
                              url="http://localhost:3333")
    p.write_text("busterblock: http://staging.example.com\n")
    generate.scaffold_one("environments", "busterblock", cfg, str(tmp_path),
                          url="http://localhost:3333")
    assert "staging" in p.read_text()
    assert "already exists" in capsys.readouterr().out


def test_match_scaffold_command_parses_kind_app_fields():
    m = repl._match_scaffold_command(
        "generate the secrets file for busterblock to store the username and password")
    assert m == {"action": "scaffold", "kind": "secrets", "app": "busterblock",
                 "fields": ["username", "password"]}
    assert repl._match_scaffold_command("generate the environments yaml for busterblock"
                                        )["kind"] == "environments"
    assert repl._match_scaffold_command("generate the pom for busterblock")["kind"] == "pom"
    assert repl._match_scaffold_command("run all") is None


def test_dispatch_scaffold_command_writes_file(tmp_path):
    cfg = config.load(str(tmp_path))
    state: dict = {}
    repl.dispatch("generate the environments yaml for busterblock", cfg, str(tmp_path),
                  None, state)
    assert (tmp_path / "tests" / "web" / "busterblock" / "resources" /
            "busterblock_environments.yaml").exists()
    assert state["last_app"] == "busterblock"

    # a follow-up with no app named resolves from state, not a fresh prompt
    repl.dispatch("generate the pom", cfg, str(tmp_path), None, state)
    assert (tmp_path / "tests" / "web" / "busterblock" / "resources" /
            "pageobjects" / "busterblock_pom.yaml").exists()


def test_dispatch_scaffold_command_needs_an_app(tmp_path, capsys):
    cfg = config.load(str(tmp_path))
    repl.dispatch("generate the secrets file", cfg, str(tmp_path), None, {})
    assert "Which app" in capsys.readouterr().out


def test_lookup_app_url_reads_back_scaffolded_environments(tmp_path):
    cfg = config.load(str(tmp_path))
    generate.scaffold_one("environments", "busterblock", cfg, str(tmp_path),
                          url="http://localhost:3333")
    assert repl._lookup_app_url(cfg, str(tmp_path), "busterblock") == "http://localhost:3333"
    assert repl._lookup_app_url(cfg, str(tmp_path), "nosuchapp") is None


# --- NOOD_0108 — plain per-app environments.yaml (no app prefix) -------------

def test_lookup_app_url_reads_plain_environments_yaml(tmp_path):
    cfg = config.load(str(tmp_path))
    res = tmp_path / "tests" / "web" / "myshop" / "resources"
    res.mkdir(parents=True)
    (res / "environments.yaml").write_text("myshop: https://shop.example.com\n")
    assert repl._lookup_app_url(cfg, str(tmp_path), "myshop") == "https://shop.example.com"


def test_app_from_existing_url_matches_plain_environments_yaml(tmp_path):
    """A hand-made package using resources/environments.yaml is found by the
    reverse URL→app lookup, so generate() adds to it instead of scaffolding a
    duplicate package."""
    cfg = config.load(str(tmp_path))
    res = tmp_path / "tests" / "web" / "myshop" / "resources"
    res.mkdir(parents=True)
    (res / "environments.yaml").write_text("myshop: https://shop.example.com\n")
    assert generate._app_from_existing_url(
        "https://shop.example.com/en.html", cfg, str(tmp_path)) == "myshop"


def test_agent_templates_seed_base_url_into_app_package_not_root_env():
    """The scaffolded AGENTS.md / prompt template must direct base URLs into
    the app's resources/environments.yaml — never the workspace-root .env
    (NOOD_0108). Guards against the instruction text drifting back."""
    from noodle import cli

    # NOOD_0125 — base-URL seeding is a rule, so it lives in AGENTS.md only;
    # the prompt stopped duplicating it.
    assert "resources/environments.yaml" in cli._AGENTS_MD
    assert "write its base URL into `.env`" not in cli._AGENTS_MD


# ---------------------------------------------------------------------------
# NOOD_0043 — Allure report gaps: "open/serve the report" regenerate first,
# the plan schema knows open_report, and the prompt vocabulary can't drift
# from the real pattern table.


def test_dispatch_open_report_regenerates_first(tmp_path, monkeypatch):
    cfg = config.load(str(tmp_path))
    calls = []
    monkeypatch.setattr(repl, "_noodle", lambda *a, **kw: calls.append(a))
    assert repl.dispatch("open the report", cfg, str(tmp_path), llm=None) is True
    assert calls == [("report", "generate"), ("report", "open")]


def test_dispatch_serve_report(tmp_path, monkeypatch):
    cfg = config.load(str(tmp_path))
    calls = []
    monkeypatch.setattr(repl, "_noodle", lambda *a, **kw: calls.append(a))
    assert repl.dispatch("serve the report", cfg, str(tmp_path), llm=None) is True
    assert calls == [("report", "generate"), ("report", "serve")]


def test_dispatch_serve_rca_report_still_wins(tmp_path, monkeypatch):
    """'serve the rca report' must keep hitting the RCA branch, not the new
    Allure serve branch."""
    cfg = config.load(str(tmp_path))
    called = []
    monkeypatch.setattr(repl, "_serve_rca", lambda w: called.append(w))
    assert repl.dispatch("serve the rca report", cfg, str(tmp_path), llm=None) is True
    assert called == [str(tmp_path)]


def test_extract_plan_accepts_open_report(monkeypatch):
    monkeypatch.setattr(
        "noodle.llm.client.ask",
        lambda p, system=None: '[{"action": "run"}, {"action": "open_report"}]')
    assert repl._extract_plan("run it and show me the report") == [
        {"action": "run"}, {"action": "open_report"}]


def test_plan_open_report_step_builds_and_opens(tmp_path, monkeypatch):
    cfg = config.load(str(tmp_path))
    calls = []
    monkeypatch.setattr(repl, "_noodle", lambda *a, **kw: calls.append(a))
    repl._run_step({"action": "open_report"}, cfg, str(tmp_path), None, {}, False)
    assert calls == [("report", "generate"), ("report", "open")]


def test_step_vocabulary_matches_pattern_table():
    """Drift guard: every example step in the LLM prompt vocabulary must
    still resolve via the real pattern table. A line here that stops
    matching teaches the model step grammar the engine will fail on."""
    from noodle.repl import prompts
    from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject

    steps = [ln.strip() for ln in prompts.STEP_VOCABULARY.splitlines()]
    steps = [s for s in steps if s.split(" ", 1)[0] in ("Given", "When", "Then")]
    assert steps, "vocabulary parse found no example steps"
    bad = [s for s in steps
           if match(normalize_phrasing(normalize_subject(s.split(" ", 1)[1]))) is None]
    assert not bad, "prompt vocabulary drifted from patterns.py:\n  " + "\n  ".join(bad)
