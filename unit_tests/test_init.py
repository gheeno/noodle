"""noodle init scaffolds a complete, self-describing workspace (NOOD_0010)."""
import json

from typer.testing import CliRunner

from noodle.cli import app

runner = CliRunner()


def test_init_creates_all_workspace_files(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    for rel in [
        "noodle.yaml",
        ".env",
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "noodle_tests/environment.py",
        "noodle_tests/steps/z_catch_all.py",
        "noodle_tests/sample_app/features/login.feature",
        "noodle_tests/pom.yaml",
        "noodle_tests/sample_app/resources/pageobjects/login_pom.yaml",
        "noodle_tests/sample_app/report/README.md",
    ]:
        assert (tmp_path / rel).exists(), f"missing {rel}"
    # every generated file opens with a purpose comment
    for rel in ["noodle.yaml", ".env", "noodle_tests/sample_app/features/login.feature"]:
        assert (tmp_path / rel).read_text().startswith("#"), f"no header comment in {rel}"
    # noodle.yaml points the engine at the scaffolded tests dir
    assert "tests_dir: noodle_tests" in (tmp_path / "noodle.yaml").read_text()
    # sample feature points at the steps dictionary and keeps steps commented
    sample = (tmp_path / "noodle_tests/sample_app/features/login.feature").read_text()
    assert "steps_dictionary" in sample
    assert "    # Given" in sample
    # feature template points at its POM file
    assert "pageobjects/login_pom.yaml" in sample
    # AI instructions cover layout + workflow; CLAUDE.md defers to AGENTS.md
    agents = (tmp_path / "AGENTS.md").read_text()
    assert "noodle_tests/<app>" in agents and "validate" in agents
    assert "AGENTS.md" in (tmp_path / "CLAUDE.md").read_text()


def test_app_report_dir_detection(tmp_path):
    from noodle.cli import _app_report_dir
    runner.invoke(app, ["init", str(tmp_path)])
    sample = "noodle_tests/sample_app"
    expect = tmp_path / sample / "report"
    # app dir, its features/ dir, and a single .feature all map to <app>/report
    for target in [sample, f"{sample}/features", f"{sample}/features/login.feature"]:
        assert _app_report_dir(str(tmp_path), target) == expect.resolve(), target
    # suite-wide targets don't get a per-app report dir
    assert _app_report_dir(str(tmp_path), "noodle_tests") is None


def test_last_run_root_follows_pointer(tmp_path, monkeypatch):
    """NOOD_0086 — single-app runs route artifacts into <app>/report/; the
    pointer file lets a fresh process (summary/rca/report/MCP) find them."""
    from noodle.reporting import paths as _paths
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    runner.invoke(app, ["init", str(tmp_path)])
    app_report = tmp_path / "noodle_tests/sample_app/report"
    # no pointer yet → classic artifacts/
    assert _paths.last_run_root(str(tmp_path)) == tmp_path / "artifacts"
    # a routed run records its root; later readers follow it
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", "noodle_tests/sample_app/report")
    _paths.record_last_run_root(str(tmp_path))
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR")
    assert _paths.last_run_root(str(tmp_path)) == app_report
    # explicit env var wins over the pointer
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", "elsewhere")
    assert _paths.last_run_root(str(tmp_path)) == tmp_path / "elsewhere"
    # stale pointer (root deleted) → falls back to artifacts/
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR")
    import shutil
    shutil.rmtree(app_report)
    assert _paths.last_run_root(str(tmp_path)) == tmp_path / "artifacts"


def test_summary_follows_routed_run(tmp_path, monkeypatch):
    """`noodle summary` in a fresh process reads the app's report/ tree after
    a single-app run routed artifacts there."""
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    from noodle.reporting import paths as _paths
    runner.invoke(app, ["init", str(tmp_path)])
    results = tmp_path / "noodle_tests/sample_app/report/allure-results"
    results.mkdir(parents=True)
    (results / "a-result.json").write_text(
        '{"name": "S", "status": "passed", "start": 0, "stop": 1}')
    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", "noodle_tests/sample_app/report")
    _paths.record_last_run_root(str(tmp_path))
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR")
    r = runner.invoke(app, ["summary", "--workspace", str(tmp_path), "--json"])
    assert r.exit_code == 0
    assert json.loads(r.output)["passed"] == 1


def test_init_is_idempotent(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    marker = tmp_path / ".env"
    marker.write_text("USER_EDITED=true\n")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    assert marker.read_text() == "USER_EDITED=true\n"  # never overwrites


def test_init_without_llm_leaves_model_commented_out(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    env_text = (tmp_path / ".env").read_text()
    assert "#NOODLE_MODEL=" in env_text
    assert "NOODLE_LLM_URL" not in env_text


def test_init_llm_ollama_persists_model_and_url(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path), "--llm", "ollama"])
    assert result.exit_code == 0
    env_text = (tmp_path / ".env").read_text()
    assert "NOODLE_MODEL=ollama/llama3.2" in env_text
    assert "NOODLE_LLM_URL=http://localhost:11434" in env_text


def test_init_llm_claude_no_ollama_url(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path), "--llm", "claude"])
    assert result.exit_code == 0
    env_text = (tmp_path / ".env").read_text()
    assert "NOODLE_MODEL=anthropic/claude-sonnet-5" in env_text
    assert "NOODLE_LLM_URL" not in env_text


def test_init_llm_model_override(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path), "--llm", "ollama", "--model", "ollama/llava"])
    assert result.exit_code == 0
    assert "NOODLE_MODEL=ollama/llava" in (tmp_path / ".env").read_text()


def test_init_llm_ignored_when_env_already_exists(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    before = (tmp_path / ".env").read_text()
    result = runner.invoke(app, ["init", str(tmp_path), "--llm", "ollama"])
    assert result.exit_code == 0
    assert (tmp_path / ".env").read_text() == before  # untouched, not silently rewritten
    assert "ignored" in result.output
