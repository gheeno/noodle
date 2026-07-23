"""NOOD_0022 — local process parallelism (behavex) reporting safety.

The risk parallelism introduces is workers clobbering each other in the shared
allure-results/ dir. These cover the three fixes: per-worker results dir, the
skipped wipe, and the merge-back.
"""
import os

from noodle.reporting.paths import results_dir


def test_results_dir_default(monkeypatch):
    monkeypatch.delenv("NOODLE_RESULTS_DIR", raising=False)
    monkeypatch.delenv("NOODLE_ARTIFACTS_DIR", raising=False)
    assert str(results_dir()) == "artifacts/allure-results"


def test_results_dir_env_override(monkeypatch):
    monkeypatch.setenv("NOODLE_RESULTS_DIR", "allure-results/p999")
    assert str(results_dir()) == "allure-results/p999"


def test_before_all_parallel_uses_pid_dir_and_skips_wipe(monkeypatch):
    from noodle import hooks
    monkeypatch.setattr(hooks, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(hooks, "_load_environments", lambda: None)
    monkeypatch.setattr(hooks, "_load_keyvault", lambda: None)
    monkeypatch.setattr(hooks, "_run_hooks", lambda *a, **k: None)
    monkeypatch.setattr(hooks.healing, "reset", lambda: None)
    wiped = []
    monkeypatch.setattr(hooks, "_clean_allure_results", lambda: wiped.append(True))
    monkeypatch.setenv("NOODLE_PARALLEL_WORKER", "1")
    monkeypatch.delenv("NOODLE_RESULTS_DIR", raising=False)

    hooks.before_all(object())

    assert wiped == []  # parallel worker must NOT wipe the shared dir
    assert os.environ["NOODLE_RESULTS_DIR"] == f"artifacts/allure-results/p{os.getpid()}"


def test_before_all_sequential_wipes_shared_dir(monkeypatch):
    from noodle import hooks
    monkeypatch.setattr(hooks, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(hooks, "_load_environments", lambda: None)
    monkeypatch.setattr(hooks, "_load_keyvault", lambda: None)
    monkeypatch.setattr(hooks, "_run_hooks", lambda *a, **k: None)
    monkeypatch.setattr(hooks.healing, "reset", lambda: None)
    wiped = []
    monkeypatch.setattr(hooks, "_clean_allure_results", lambda: wiped.append(True))
    monkeypatch.delenv("NOODLE_PARALLEL_WORKER", raising=False)

    hooks.before_all(object())
    assert wiped == [True]


def test_merge_flattens_results_merges_junit_removes_worker_dirs(tmp_path):
    from noodle.cli import _merge_worker_results
    results = tmp_path / "allure-results"
    results.mkdir()
    for w, name in [("p1", "a"), ("p2", "b")]:
        (results / w).mkdir()
        (results / w / f"{name}-result.json").write_text("{}")
        (results / w / "junit.xml").write_text(f'<testsuite name="{w}" tests="1"/>')

    _merge_worker_results(results)

    assert {f.name for f in results.glob("*-result.json")} == {"a-result.json", "b-result.json"}
    assert list(results.glob("p*")) == []                  # no leftover worker dirs
    # NOOD_0008: merged junit lands OUTSIDE allure-results so allure generate
    # can't ingest scenarios twice.
    merged = (tmp_path / "reports" / "junit.xml").read_text()
    assert merged.count("<testsuite ") == 2                # both suites under <testsuites>


def test_merge_junits_skips_missing_and_malformed(tmp_path):
    from noodle.reporting.junit import merge_junits
    good = tmp_path / "good.xml"
    good.write_text('<testsuite name="g" tests="1"/>')
    bad = tmp_path / "bad.xml"
    bad.write_text("not xml <<<")
    out = merge_junits([good, bad, tmp_path / "missing.xml"], tmp_path / "out.xml")
    text = out.read_text()
    assert text.count("<testsuite ") == 1                  # only the good one


def test_parallel_toggle_flag_env_and_default(monkeypatch):
    from typer.testing import CliRunner

    from noodle import cli
    seen = {}
    monkeypatch.setattr(cli, "_run_parallel",
                        lambda path, n, tag, env, cwd=".", scheme="feature":
                        seen.update(n=n, scheme=scheme) or 0)
    # keep the single-process default path from actually launching behave
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    run = CliRunner()

    seen.clear()
    assert run.invoke(cli.app, ["run", "tests/", "--parallel", "3", "--headless"]).exit_code == 0
    assert seen["n"] == 3                                   # flag wins
    assert seen["scheme"] == "feature"                      # default scheme

    seen.clear()
    assert run.invoke(cli.app, ["run", "tests/", "--parallel", "3", "--headless",
                                "--parallel-scheme", "scenario"]).exit_code == 0
    assert seen["scheme"] == "scenario"                     # flag passes through

    # invalid scheme rejected before any run starts
    res = run.invoke(cli.app, ["run", "tests/", "--parallel", "3", "--headless",
                               "--parallel-scheme", "file"])
    assert res.exit_code != 0

    seen.clear()
    assert run.invoke(cli.app, ["run", "tests/", "--headless"],
                      env={"NOODLE_PARALLEL_PROCESSES": "2"}).exit_code == 0
    assert seen["n"] == 2                                   # env drives when no flag

    seen.clear()
    assert run.invoke(cli.app, ["run", "tests/", "--headless"],
                      env={"NOODLE_PARALLEL_PROCESSES": "0"}).exit_code == 0
    assert "n" not in seen                                  # default = single process


def test_clean_removes_worker_dirs(tmp_path):
    from noodle.cli import _clean_results_root
    (tmp_path / "old-result.json").write_text("{}")
    (tmp_path / "junit.xml").write_text("<x/>")
    (tmp_path / "p7").mkdir()
    (tmp_path / "p7" / "x-result.json").write_text("{}")

    _clean_results_root(tmp_path)

    assert list(tmp_path.glob("*-result.json")) == []
    assert not (tmp_path / "junit.xml").exists()
    assert not (tmp_path / "p7").exists()
