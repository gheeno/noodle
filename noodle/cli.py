import errno
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import typer
from typer.core import TyperGroup

from noodle import config, payload_budget
from noodle.reporting import paths as _paths


class _OrderedGroup(TyperGroup):
    """List commands alphabetically in --help (Typer's default is definition
    order, which buried validate/inspect/probe/rca-report in a hard-to-scan
    pile). ponytail: one override, no plugin."""
    def list_commands(self, ctx):
        return sorted(super().list_commands(ctx))


app = typer.Typer(cls=_OrderedGroup, help="Noodle — AI-powered BDD test runner",
                  add_completion=False)


def _version_callback(value: bool):
    # NOOD_0133 — not just a number: name the resolved build path + git SHA so
    # a stale site-packages copy shadowing the dev clone is visible at a glance.
    if value:
        from noodle import install_check
        typer.echo(install_check.build_line())
        # NOOD_0156 — a dist-info version lagging the checkout's pyproject is
        # exactly the "old version after git pull" confusion; name the cure.
        vr = install_check.version_report()
        if vr["mismatch"]:
            typer.echo(f"  ⚠️ the source checkout declares {vr['source']} but "
                       f"the installed metadata recorded {vr['installed']} — "
                       "refresh it: `noodle update` (`noodle doctor` for the "
                       "full diagnosis)")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(False, "--version", callback=_version_callback,
                                 is_eager=True,
                                 help="Print version, resolved build path and git SHA"),
):
    pass

_VALID_BROWSERS = {"chromium", "firefox", "webkit", "safari", "edge"}

# NOOD_0055 — invoke behave through this interpreter, not a bare "behave" from
# PATH: GUI-launched MCP hosts (Claude Desktop etc.) spawn noodle with a minimal
# PATH that has no venv bin dir, and `python -m behave` always resolves.
_BEHAVE_CMD = [sys.executable, "-m", "behave"]

# Truthy values accepted from environment (beyond the canonical "true").
_TRUTHY = {"1", "true", "yes", "on"}


def _normalize_headless(raw: str) -> str:
    """Normalise any truthy/falsy env-var spelling to canonical 'true'/'false'."""
    return "true" if raw.strip().lower() in _TRUTHY else "false"


def _find_behave_base(feature_path: Path) -> Path:
    """
    Walk up from the feature file's parent to find the behave root — the nearest
    ancestor that contains a steps/ subdirectory or an environment.py file.
    Falls back to 'tests/' if no marker is found (standard layout).

    Stops at the workspace root (the directory holding noodle.yaml) —
    NOOD_0027: without this bound, a workspace missing either marker would
    keep walking past its own root into an unrelated ancestor directory,
    a real risk once sibling test/engine repos share a parent folder.
    """
    for directory in [feature_path.parent, *feature_path.parent.parents]:
        if (directory / "steps").is_dir() or (directory / "environment.py").exists():
            return directory
        if (directory / "noodle.yaml").exists():
            break
    return Path("tests")


def _app_report_dir(cwd: str, path: str) -> Path | None:
    """<app>/report when the run targets a single app package — the app dir
    itself, its features/ dir, or one .feature inside it. None for suite-wide
    runs (artifacts then stay in the classic <workspace>/artifacts)."""
    p = (Path(cwd) / path).resolve()
    if p.suffix == ".feature":
        p = p.parent
    if p.name == "features":
        p = p.parent
    return p / "report" if (p / "features").is_dir() else None


def _resolve_run_target(workspace: str, path: str | None) -> tuple[str, str | None]:
    """NOOD_0086 — let noodle be invoked from inside an app package (cwd or
    --workspace pointing at the app dir, e.g. `cd noodle_tests/app1 && noodle
    run`): re-root on the nearest ancestor holding noodle.yaml — so .env,
    secrets and config resolve — and target just this app. Returns the
    (workspace, path) pair to actually use; unchanged when not applicable."""
    ws = Path(workspace)
    if path is None and (ws / "features").is_dir() and not (ws / "noodle.yaml").exists():
        for d in [ws.resolve(), *ws.resolve().parents]:
            if (d / "noodle.yaml").exists():
                return str(d), os.path.relpath(ws.resolve(), d)
    return workspace, path


def _agent_quiet() -> bool:
    """NOOD_0117 — should this run default to --quiet? NOODLE_QUIET decides
    when set ("1"/"true"/"yes"/"on" → quiet, anything else → verbose);
    otherwise a non-TTY stdout (agent tool call, CI, MCP subprocess) is the
    signal. sys.stdout.isatty() + env only — no POSIX-only checks."""
    env = os.getenv("NOODLE_QUIET")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    try:
        return not sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False



def _json_out(payload, **dumps_kwargs) -> None:
    """Print an agent-facing JSON payload, bounded (NOOD_0164). Every `--json`
    door is a spill door: a harness that can't inline the payload writes it to
    a temp file and the agent pays inferences to `jq` back what the command
    already told it. Same budget as the MCP boundary, one helper so a new
    `--json` flag can't quietly skip it.

    NOOD_0165 — measured at the indent it PRINTS at (a 7,556 B payload renders
    as 10,240 B at indent=2, and the compact measurement never saw those
    2.7 KB), and a payload that has to be trimmed is written whole to
    `.noodle/last_payload.json` first: the agent reads that path with its own
    file tools instead of grepping the harness's spill file."""
    bounded = payload_budget.bound(payload, indent=2)
    if isinstance(bounded, dict) and "payload_note" in bounded:
        full = _write_full_payload(payload)
        if full:
            bounded["payload_note"] += f" Full payload: {full}"
    typer.echo(json.dumps(bounded, indent=2, default=str, **dumps_kwargs))


def _write_full_payload(payload) -> str | None:
    """The untrimmed payload on disk, or None if cwd isn't writable."""
    try:
        path = Path(".noodle") / "last_payload.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str))
        return str(path.resolve())
    except OSError:
        return None


@app.command()
def run(
    path: str = typer.Argument(None, help="Path to .feature files or directory (default: workspace tests_dir)"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir (noodle.yaml, .env)"),
    headless: bool = typer.Option(False, "--headless", help="Run browser without UI"),
    headed: bool = typer.Option(False, "--headed", help="Force a visible browser"),
    tag: str = typer.Option(None, "--tag", "-t", help="Filter by tag e.g. smoke"),
    browser: str = typer.Option(None, "--browser", "-b", help="chromium|firefox|webkit|safari|edge"),
    retries: int = typer.Option(None, "--retries", help="Re-run a failed scenario N times (0 off)"),
    log_level: str = typer.Option(None, "--log-level", help="DEBUG|INFO|WARNING|ERROR"),
    parallel: int = typer.Option(None, "--parallel", help="N feature files at once via behavex (web, headless, parallel extra)"),
    parallel_scheme: str = typer.Option("feature", "--parallel-scheme", help="With --parallel: shard by 'feature' (a browser per file) or 'scenario'"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Live behave stream to <artifacts>/run.log, stdout gets the summary (automatic off a TTY)"),
    preflight: bool = typer.Option(None, "--preflight/--no-preflight", help="Resolve every {env:KEY} before the browser; missing aborts (exit 2). Default on"),
    serve: bool = typer.Option(False, "--serve", help="Host the Allure + RCA reports after the run, print URLs"),
    json_out: bool = typer.Option(False, "--json", help="One bounded JSON payload instead of the human summary; implies --quiet"),
):
    """Run .feature files.

    Full flag reference: noodle docs cli-reference
    """
    # NOOD_0117 — agents/CI never benefit from the live stream (it's the
    # single heaviest resident context blob per fix→rerun lap); a human at a
    # TTY keeps it. Env var beats detection either way, cross-platform.
    # NOOD_0131 — --json promises ONE parseable object on stdout, so it
    # implies quiet (the live behave stream would corrupt it).
    quiet = quiet or json_out or _agent_quiet()
    # NOOD_0133 — every run names the build it executes: a user should never be
    # unable to tell whether the CLI is the dev tree or a stale installed copy.
    from noodle import install_check
    if not json_out:
        typer.echo(f"  🧬 {install_check.build_line()}")
        install_check.warn_if_stale(typer.echo)
    workspace, path = _resolve_run_target(workspace, path)
    cfg = config.load(workspace)
    # No path given → run the workspace's tests dir. browser/headless fall
    # back to the workspace config when the flags aren't set.
    if path is None:
        path = cfg["tests_dir"]
    if browser is None:
        browser = cfg["browser"]
    # Toggle: flag wins; otherwise fall back to the env var (lets CI/local flip
    # parallelism without changing the command). 0 or unset = single process.
    if parallel is None:
        parallel = int(os.getenv("NOODLE_PARALLEL_PROCESSES", "0") or "0")
    # Bug 2: reject mutually exclusive flags up front
    if headed and headless:
        raise typer.BadParameter(
            "--headed and --headless are mutually exclusive. Pass one or neither.",
            param_hint="'--headed' / '--headless'",
        )

    # Bug 4: validate browser name before it reaches Playwright
    if browser not in _VALID_BROWSERS:
        raise typer.BadParameter(
            f"Unsupported browser '{browser}'. Valid options: {', '.join(sorted(_VALID_BROWSERS))}",
            param_hint="'--browser'",
        )

    # NOOD_0128/0130 — secret readiness BEFORE the browser: a doomed login run is
    # the most expensive way to learn a credential is a placeholder. ON BY DEFAULT
    # for every run now (NOOD_0130); --no-preflight is the explicit escape hatch.
    # Runs after arg validation so a bad-flag error still surfaces first.
    from noodle.repl import core
    do_preflight = preflight if preflight is not None else True
    if do_preflight:
        pf = core.preflight(path, workspace=workspace)
        if not json_out:                      # NOOD_0131 — one object on stdout
            for w in pf.get("warnings", []):
                typer.echo(f"  ⚠ {w}")
        if not pf["ok"]:
            if json_out:
                _json_out({"ok": False, "preflight": pf})
            else:
                typer.echo("  ✗ preflight failed — not launching a browser:")
                for e in pf["errors"]:
                    typer.echo(f"    • {e}")
            raise typer.Exit(2)

    env = os.environ.copy()

    # Bug 1: always write a canonical "true"/"false" — never pass raw env through
    if headed:
        env["NOODLE_HEADLESS"] = "false"
    elif headless:
        env["NOODLE_HEADLESS"] = "true"
    else:
        default = "true" if cfg["headless"] else "false"
        env["NOODLE_HEADLESS"] = _normalize_headless(env.get("NOODLE_HEADLESS", default))

    env["NOODLE_BROWSER"] = browser
    if retries is not None:
        env["NOODLE_RETRIES"] = str(retries)
    if log_level is not None:
        env["NOODLE_LOG_LEVEL"] = log_level

    # Run inside the workspace so behave finds its .env, environments.yaml and
    # writes allure-results there. workspace="." keeps the in-repo behaviour.
    cwd = workspace

    # NOOD_0086 — single-app runs keep everything inside the app package:
    # the whole artifacts tree (allure-results, screenshots, reports, trend
    # history) lands in <app>/report/ instead of <workspace>/artifacts/, so
    # every app-under-test stays self-contained. Suite-wide runs keep the
    # classic root. An explicit NOODLE_ARTIFACTS_DIR always wins. The pointer
    # file lets summary/rca/report/MCP find this run from a fresh process.
    if "NOODLE_ARTIFACTS_DIR" not in os.environ:
        app_root = _app_report_dir(cwd, path)
        if app_root is not None:
            rel = os.path.relpath(app_root, Path(cwd).resolve())
            os.environ["NOODLE_ARTIFACTS_DIR"] = rel
            env["NOODLE_ARTIFACTS_DIR"] = rel
            if not json_out:
                typer.echo(f"  📁 Single-app run — artifacts → {app_root}")
    _paths.record_last_run_root(cwd)

    # NOOD_0093 — a run overwrites its artifacts root in place. The Allure trend
    # history (reports/allure-history/) survives the wipe and carries prior-run
    # trends into the new report, so there's nothing to archive first. `noodle
    # archive` remains for the rare "stash this exact run" case.

    # Parallel: behavex runs N behave workers, each writing to its own results
    # subdir (set in hooks.before_all). We clean once, run, flatten, report.
    if parallel > 0:
        if parallel_scheme not in ("feature", "scenario"):
            raise typer.BadParameter(
                f"Unsupported scheme '{parallel_scheme}'. Valid options: feature, scenario",
                param_hint="'--parallel-scheme'",
            )
        raise typer.Exit(_run_parallel(path, parallel, tag, env, cwd, parallel_scheme))

    # Bug 5: derive behave base from the passed path, not a hardcoded 'tests/'
    if path.endswith(".feature"):
        feature_path = (Path(cwd) / path).resolve()
        base = _find_behave_base(feature_path)
        # NOOD_0008: --include is a regex over the whole feature *path*, not just
        # this file's stem — a bare stem (e.g. "login") also matches same-named
        # files in unrelated app packages under the shared base. Anchor on the
        # full relative path so only this file is selected.
        rel = feature_path.relative_to(base.resolve())
        include = re.escape(rel.as_posix()) + "$"
        args = [*_BEHAVE_CMD, str(base), "--include", include, "--no-capture"]
    else:
        args = [*_BEHAVE_CMD, path, "--no-capture"]

    if tag:
        args += ["--tags", tag]

    # NOOD_0116 --quiet: the biggest context-cost of an agent-driven run is
    # the full behave console stream staying resident per LLM call. Divert it
    # to <artifacts>/run.log and print only the summary below.
    if quiet:
        log_path = Path(cwd) / _paths.artifacts_root() / "run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as lf:
            result = subprocess.run(args, env=env, cwd=cwd,
                                    stdout=lf, stderr=subprocess.STDOUT)
    else:
        result = subprocess.run(args, env=env, cwd=cwd)
    rc = result.returncode

    results_root = str(Path(cwd) / _paths.artifacts_root() / "allure-results")
    # @quarantine is non-blocking: if every failed scenario this run is tagged
    # @quarantine, don't fail the build — they still ran and report as failed.
    if rc != 0 and _all_failures_quarantined(results_root) is True:
        if not json_out:
            typer.echo("\n  🔶 Only @quarantine scenarios failed — not failing the build.")
        rc = 0

    data = _write_last_run(results_root, rc, cwd)
    # NOOD_0147 — engine-side failure-trigger detection: a fired trigger is
    # surfaced with the summary so the driving agent logs a session diagnostic
    # (docs/session-diagnostics.md) without needing any always-on instruction.
    from noodle import diagnostics as _diag
    diag_fired = _diag.track_run(cwd, path, failed=rc != 0)
    if quiet and not json_out:
        from noodle.reporting import summary as _summary
        typer.echo(_summary.render(results_root, summary=data))
        typer.echo(f"full console log → {log_path}")
    if diag_fired and not json_out:
        # NOOD_0145 — portable references only: the CLI works with MCP
        # blocked, and a repo-relative doc path would resolve against the
        # workspace and look missing.
        typer.echo(f"  🩺 diagnostic due ({', '.join(diag_fired)}) — at session "
                   "end run `noodle diagnostic log` (fields: `noodle diagnostic guide`)")

    # NOOD_0128 — one combined result: serve reports + emit the bounded JSON the
    # run_and_report MCP tool returns, so a shell-driven agent gets parity.
    # NOOD_0131 — the payload reuses the one collect() above (no rescan), and
    # json mode prints NOTHING else: one parseable object on stdout.
    served = None
    if serve:
        # NOOD_0134 — a detached child, not an in-process daemon thread: `run`
        # exits right after printing, and a thread's URLs died with it. The
        # child rebuilds stale reports itself and registers for `report stop`.
        served = _spawn_report_server(str(_paths.last_run_root(cwd) / "reports"),
                                      cwd, "127.0.0.1", 0)
        if json_out:
            pass
        elif served.get("ok"):
            for u in served.get("urls", []):
                typer.echo(f"  📊 {u}")
        else:
            typer.echo(f"  ⚠ report serve: {served.get('error')}")
    if json_out:
        payload = {k: v for k, v in data.items() if k != "at"}
        from noodle.llm import cost as _cost
        if llm_cost := _cost.load_total(results_root):
            payload["llm_cost"] = llm_cost
        reports = _paths.last_run_root(cwd) / "reports"
        payload["report"] = str(reports / "allure-report" / "index.html")
        payload["rca_html"] = str(reports / "rca.html")
        # NOOD_0156 — the compact RCA also rides a green-but-unverified run:
        # its passed-with-healing lines explain why verified is false.
        if rc != 0 or data.get("verified") is False:
            payload["rca_compact"] = core.rca(cwd, compact=True)
        if diag_fired:
            payload["diagnostic_due"] = _diag.due_hint(diag_fired)
        if served and served.get("ok"):
            payload["served"] = {k: v for k, v in served.items() if k != "ok"}
        _json_out(payload)
    raise typer.Exit(rc)


def _write_last_run(results_root: str, rc: int, cwd: str = ".") -> dict:
    """NOOD_0045 Phase 4 — persist the structured run outcome to
    artifacts/last_run.json so shell/CI agents get machine-readable results
    without re-parsing allure-results themselves. Returns the collected data
    (NOOD_0131) so the caller reuses one scan for the quiet summary and
    --json payload instead of re-collecting per consumer."""
    from noodle.reporting import summary as _summary
    data = _summary.collect(results_root)
    data["exit_code"] = rc
    data["at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    out = Path(cwd) / _paths.artifacts_root() / "last_run.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2) + "\n")
    except OSError:
        pass  # reporting nicety — never fail the run over it
    return data


def _all_failures_quarantined(results_dir: str):
    """Scan this run's Allure results. Returns:
      True  — there were failures and ALL are @quarantine
      False — at least one non-quarantine failure
      None  — nothing to judge (no results / reporting off / no failures)
    """
    d = Path(results_dir)
    files = list(d.glob("*-result.json")) if d.is_dir() else []
    if not files:
        return None
    failed = []
    for f in files:
        try:
            r = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if r.get("status") == "failed":
            tags = {lab.get("value") for lab in r.get("labels", []) if lab.get("name") == "tag"}
            failed.append("quarantine" in tags)
    if not failed:
        return None
    return all(failed)


def _run_parallel(path: str, processes: int, tag: str, env: dict, cwd: str = ".",
                  scheme: str = "feature") -> int:
    """Run feature files concurrently via behavex, then merge into one report."""
    try:
        import behavex  # noqa: F401
    except ImportError:
        raise typer.BadParameter(
            'Parallel runs need behavex. Install: pip install -e ".[parallel]"',
            param_hint="'--parallel'",
        )
    results = Path(cwd) / _paths.artifacts_root() / "allure-results"
    _clean_results_root(results)            # workers skip the wipe in parallel mode
    env = {**env, "NOODLE_PARALLEL_WORKER": "1"}
    # Same PATH concern as _BEHAVE_CMD — resolve through this interpreter.
    args = [sys.executable, "-m", "behavex", path,
            "--parallel-processes", str(processes),
            "--parallel-scheme", scheme]
    if tag:
        args += ["--tags", tag]
    rc = subprocess.run(args, env=env, cwd=cwd).returncode
    _merge_worker_results(results)          # flatten p*/ so report + scan read one dir

    # NOOD_0052 — don't trust behavex's exit code: it has been observed
    # returning 0 with failed scenarios in the workers. The merged results
    # are the ground truth — any non-quarantine failure fails the build.
    quarantined = _all_failures_quarantined(str(results))
    if quarantined is False:
        rc = rc or 1
    elif rc != 0 and quarantined is True:
        typer.echo("\n  🔶 Only @quarantine scenarios failed — not failing the build.")
        rc = 0
    from noodle.reporting import rca_report as _rca_report
    from noodle.reporting.builder import generate
    reports_root = Path(cwd) / _paths.artifacts_root() / "reports"
    generate(str(results), str(reports_root / "allure-report"))
    # NOOD_0082 — parallel runs get the same always-written rca.md/rca.html
    # tail the single-process hooks path writes (hooks skip it per-worker).
    _rca_report.write_reports(str(results), str(reports_root))
    _write_last_run(str(results), rc, cwd)
    return rc


def _clean_results_root(results: Path):
    """Pre-run wipe of last run's flattened results + leftover worker subdirs."""
    if not results.is_dir():
        return
    import shutil
    for f in results.glob("*-result.json"):
        f.unlink(missing_ok=True)
    for f in results.glob("*-attachment.*"):
        f.unlink(missing_ok=True)
    (results / "junit.xml").unlink(missing_ok=True)
    for d in results.glob("p*"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


def _merge_worker_results(results: Path):
    """Flatten each worker dir into the shared dir so the existing report build +
    quarantine scan (both read the flat dir) work unchanged, then remove the now-
    empty worker dirs. Per-worker junit files merge into one reports/junit.xml —
    same artifact a single-process run produces. uuid filenames don't collide.
    Cross-platform: pathlib rename + rmtree only."""
    import shutil

    from noodle.reporting import junit as _junit

    worker_dirs = sorted(d for d in results.glob("p*") if d.is_dir())
    junits = [d / "junit.xml" for d in worker_dirs]
    for d in worker_dirs:
        for f in d.iterdir():
            if f.is_file() and f.name != "junit.xml":
                f.rename(results / f.name)
    if any(j.is_file() for j in junits):
        # Merged junit lands OUTSIDE allure-results so allure generate doesn't
        # ingest scenarios twice (once from JSON, once from XML).
        _junit.merge_junits(junits, results.parent / "reports" / "junit.xml")
    for d in worker_dirs:
        shutil.rmtree(d, ignore_errors=True)


_NOODLE_YAML = """\
# noodle.yaml — workspace config for the Noodle Test Framework.
# PURPOSE: tells the engine where your tests and reports live and which
#          browser to use. Paths are relative to this file.
# YOU EDIT: rarely — flip headless to false to always watch runs locally,
#           or change the browser. CLI flags (--browser, --headed, --tag)
#           override these values; --headed forces a visible browser for
#           this run only.
tests_dir: noodle_tests
env_file: .env
reports_dir: artifacts/reports
browser: chromium   # chromium | firefox | webkit | safari | edge (safari = Playwright WebKit; edge needs MS Edge installed)
headless: true       # set false to see the browser by default; --headed overrides per-run
"""

_GITIGNORE = """\
# Secrets stay local — never commit them (NOOD_0118). The engine loads these
# at run time and scrubs their values from all output.
secrets.env
**/resources/*_secrets.env
# Session diagnostics are machine-local agent self-reports for the Noodle
# team (NOOD_0147) — share via `noodle diagnostic bundle`, never via git.
diagnostics/
# Run-local engine state (last-run pointer, diag_state, report-server pids).
.noodle/
"""

_ENV_STUB_BASE = """\
# .env — workspace settings, safe to commit. NO SECRETS here: put credentials
# in secrets.env (gitignored) and reference them in tests as {env:MY_PASSWORD}.
# PURPOSE: run-wide defaults the engine reads on every run.
# YOU EDIT: uncomment / change values as needed.
NOODLE_BROWSER=chromium         # chromium | firefox | webkit | safari | edge
NOODLE_HEADLESS=false           # true in CI
NOODLE_TIMEOUT=10000            # per-action timeout, milliseconds (clicks, page loads)
#NOODLE_FIND_TIMEOUT=120000     # element-find + page-load budget, ms — a CEILING, not a wait: steps proceed the instant the element appears. Slow internal site? raise it, e.g. 300000 (5 min)
#NOODLE_WAIT_EXTENSION=30000    # one extra wait granted at the find deadline while the page is still loading (network active)
# Authoring on a SLOW/spinner-heavy site? Uncomment this dev-loop floor so a missed
# element fails fast instead of eating the full ceiling — then RAISE/REMOVE it for the
# real CI run, or a genuinely slow-but-valid load will false-fail:
#NOODLE_FIND_TIMEOUT=25000
#NOODLE_WAIT_EXTENSION=15000
#NOODLE_SETTLE_TIMEOUT=15000    # settled-page early exit, ms: once the page is done (network quiet + DOM stable) a find that still hasn't matched stops polling early instead of exhausting the full budget; 0 disables
NOODLE_IGNORE_HTTPS_ERRORS=true  # dev/sandbox sites: TLS + self-signed/invalid cert errors ignored by default in all browsers; set false (or tag @secure_certs) to surface them
#NOODLE_AUTO_DISMISS=true       # auto-close overlays that block a click, with an RCA warning; set false to fail instead
#NOODLE_DEV_FIX_ATTEMPTS=10      # agent test-dev loop: CEILING on cause-backed fix+rerun attempts (first failure: reproduce once with probe --do, never guess-per-lap) before reporting the test as flaky
#NOODLE_VIEWPORT=1920x1080      # run-wide viewport (or @viewport:WxH tag per scenario)
#NOODLE_RETRIES=0               # retry failed scenarios N times
#NOODLE_LOG_LEVEL=INFO
# Session diagnostics (NOOD_0147) — agent failure self-reports in diagnostics/
# (gitignored; see docs/session-diagnostics.md for the trigger definitions):
#NOODLE_DIAG_MAX=25             # reports kept before the oldest rotate out
#NOODLE_DIAG_SLOW_MIN=20        # dev wall-clock minutes that make the slow-dev trigger fire
#NOODLE_DIAG_COST_BUDGET=20     # driving-agent session spend (AIC/credits) for the over-budget trigger
"""


def _env_stub(llm: str = None, model: str = None) -> str:
    """`.env` content for a fresh workspace. Without --llm, NOODLE_MODEL stays
    commented out (patterns-only, $0) same as before. With --llm, persist the
    resolved model here so `noodle repl` picks it up next time with no flags —
    see docs/agent-playbook.md."""
    if not llm:
        return _ENV_STUB_BASE + \
            "#NOODLE_MODEL=                  # LLM fallback for unmatched steps, e.g. anthropic/claude-sonnet-5 (cloud) or ollama/llama3.2 (local)\n"
    resolved = model or config.LLM_PRESETS.get(llm, llm)
    stub = _ENV_STUB_BASE + \
        f"NOODLE_MODEL={resolved}   # persisted by `noodle init --llm {llm}`\n"
    if llm == "ollama":
        stub += "NOODLE_LLM_URL=http://localhost:11434   # local Ollama server\n"
    return stub

# behave glue — re-exports from the installed engine so behave discovers the
# lifecycle hooks and the single catch-all step matcher. behave itself requires
# a file named exactly environment.py and a folder named exactly steps/ at the
# root it's pointed at — these two names/positions are a behave contract, not
# a Noodle convention, so they live at the tests root (noodle_tests/), not
# per-app.
_ENVIRONMENT_PY = """\
# Engine glue, auto-created by `noodle init` — DO NOT EDIT.
# PURPOSE: re-exports the framework's behave lifecycle hooks so behave finds them.
from noodle.hooks import (
    before_all,
    before_feature,
    before_scenario,
    after_step,
    after_scenario,
    after_all,
)
"""

_CATCH_ALL_PY = """\
# Engine glue, auto-created by `noodle init` — DO NOT EDIT.
# behave auto-imports noodle_tests/steps/*.py at startup. The engine registers one
# regex catch-all that routes each Gherkin line to the right agent. The z_ prefix
# keeps it last in load order so any project-local steps register first.
from noodle.steps.catch_all import *  # noqa: F401,F403
"""

_SAMPLE_FEATURE = """\
# sample login.feature — a template to copy, auto-created by `noodle init`.
# PURPOSE: shows the step vocabulary. Copy the whole sample_app/ package to
# noodle_tests/<your-app>/ and adapt.
# YOU EDIT: yes — or delete once you have real tests.
#
# Steps are plain English matched against the framework's step dictionary:
#   https://github.com/gheeno/noodle/blob/main/docs/steps_dictionary.md
# Check any step you write without a browser:  noodle validate noodle_tests/ --resolve
#
# Element names resolve through the page objects in
# resources/pageobjects/login_pom.yaml (per-page), then this app's
# resources/pom.yaml, then the global noodle_tests/pom.yaml.
#
# {env:VARS} resolve from .env / environments.yaml; secrets from secrets.env.
# The steps below are commented out so a fresh workspace runs green —
# uncomment and edit them to make this a real test.
Feature: Sample — login
  Template showing the step vocabulary. Not a real test yet.

  Scenario: User logs in
    # Given User is on '{env:BASE_URL}'
    # When User enters 'standard_user' in the username field
    # And User enters '{env:MY_PASSWORD}' in the password field
    # And User clicks the 'Login' button
    # Then User should see 'Products'
"""

_GLOBAL_POM = """\
# Global POM — applies to ALL feature files, auto-created by `noodle init`.
# PURPOSE: elements shared across pages/features (nav bars, cookie banners…).
# YOU EDIT: yes. Per-app elements go in
#   noodle_tests/<your-app>/resources/pageobjects/<page>_pom.yaml  instead
#   (see the sample).
#
# Selector types: css | xpath | id | testid | text | role | label | placeholder | title | alt_text
#
# Example entries (uncomment and adapt):
#
# navigation menu:
#   role: navigation
#
# cookie accept:
#   id: "onetrust-accept-btn-handler"
"""

_SAMPLE_POM = """\
# sample login_pom.yaml — a template to copy, auto-created by `noodle init`.
# PURPOSE: page objects for login.feature. The filename minus '_pom' is the
# page name ('login') used for pinning + matching. One file per page.
# YOU EDIT: yes — copy alongside your own app as
#   noodle_tests/<your-app>/resources/pageobjects/<page>_pom.yaml
#
# Element names here become usable directly in feature steps, e.g.
#   When User clicks the 'Login' button
# resolves 'login' through this file before any auto-locating — "clicks the
# X button/link" steps strip the trailing button/link word before matching,
# so the key below is 'login:', not 'login button:'.
#
# Selector types: css | xpath | id | testid | text | role | label | placeholder | title | alt_text
#
# Example entries (uncomment and adapt):
#
# username field:
#   id: "user-name"
#
# password field:
#   id: "password"
#
# login:
#   css: "input[type='submit']"
"""

_WORKSPACE_README = """\
# Noodle test workspace

Scaffolded by `noodle init`. What's here:

| File | Purpose | You edit? |
|---|---|---|
| `noodle.yaml` | engine config — paths, browser, headless | rarely |
| `.env` | run-wide settings (hidden file — `ls -a` to see it) | yes |
| `AGENTS.md` / `CLAUDE.md` | instructions for AI coding agents driving this workspace | rarely |
| `noodle_tests/sample_app/features/login.feature` | step-vocabulary template | copy & adapt |
| `noodle_tests/sample_app/resources/pageobjects/login_pom.yaml` | page-object template for the sample feature | copy & adapt |
| `noodle_tests/sample_app/report/` | this app's run output (results + reports) | never |
| `noodle_tests/pom.yaml` | global page objects, shared across all tests | yes |
| `noodle_tests/environment.py` | engine glue | never |
| `noodle_tests/steps/z_catch_all.py` | engine glue | never |
| `diagnostics/` | agent failure self-reports (gitignored) — `noodle diagnostic bundle` to share | never |

## Layout — one package per app-under-test

Each app you test gets its own self-contained package under `noodle_tests/`:

```
noodle_tests/<your-app>/
├── features/     .feature files
├── resources/    pageobjects/*_pom.yaml, app-local pom.yaml
└── report/       this app's run output — results, Allure + RCA reports, history
```

Running a single app (`noodle run noodle_tests/<your-app>`) writes that
run's entire artifacts tree into its `report/` folder, so each app keeps
its own isolated results (`noodle summary` / `noodle report serve` follow
the last run automatically). Workspace-wide runs use `artifacts/`.
You can also `cd noodle_tests/<your-app>` and run any noodle command right
there — `noodle run`, `summary`, `report serve`, `archive` all operate on
that app alone.

## Next steps

1. Author your first test in one call: `noodle author --spec <spec.yaml> --run`
   (the `author_test` MCP tool with `run_after_author=true`) — it creates the
   whole app package under `noodle_tests/web/<your-app>/`, runs it, and serves
   the reports. `sample_app/` is a step-vocabulary reference to read, not a
   starting workflow — vocabulary: `docs/steps_dictionary.md` in the noodle repo.
2. Credentials → your app's `resources/<app>_secrets.env` (gitignore it).
   Base URL → your app's `resources/environments.yaml` (`<app>: https://…`).
   Both are referenced in features as `{env:KEY}` — keep them in the app
   package, not the workspace root.
3. Check steps without a browser: `noodle validate noodle_tests/ --resolve`
4. Run: `noodle run` — or interactively: `noodle repl`

Full guide: README.md § Agentic mode, in the noodle repo.
"""

_REPORT_README = """\
# This app's run output lives here — auto-written by `noodle run`, do not edit.
# A run targeting this app (e.g. `noodle run noodle_tests/sample_app`) writes
# its whole artifacts tree into this folder: allure-results/, reports/
# (Allure HTML + rca.html + junit.xml), screenshots, traces, logs and the
# Allure trend history — so every app package keeps its own results.
# Workspace-wide runs (`noodle run` with no app path) use <workspace>/artifacts/.
"""

_AGENTS_MD = """\
# AI agent instructions — Noodle test workspace

You are in a Noodle BDD test workspace: plain-English Gherkin matched
against a fixed step dictionary — no step code. Full reference: the
agent-playbook — `read_docs('agent-playbook')` (CLI: `noodle docs
agent-playbook`) — every catalog this file points at.

North star: deterministic, plain-English, token-cheap, honest.

Nouns: **engine** = the installed framework (never edited here);
THIS folder = a **workspace** (`noodle init`; refresh `--force`);
**wok** = capability area, tag-routed (`read_docs('woks')`).

## The pipeline — 3 operations

1. Probe — ONLY for an unfamiliar page or SPA: `probe_page`
   (`noodle probe <url> --compact`). Fold ALL discovery into ONE probe
   per flow; never re-probe to grep. The flag catalog — panels, `--do`
   fill→save transactions, typeahead, native dropdowns, `--discover`,
   `--find` (matches whole — never grep payloads) — is in the
   playbook. Output is author-ready unless
   `author_ready: false` — a STOP: fix the named gap, never
   hand-author around it. A gate `--do` can't cross? Budget ONE
   exploratory run. Skip the probe ONLY when every control is standard
   AND visible; hidden/config/custom/SPA: probe first.
2. Author once — reuse first: `list_tests(query=<app>)`; copying a
   green same-app test and retargeting `{env:}` beats authoring
   (playbook §1).
   `author_test` (`noodle author --spec -` heredoc — no temp spec
   file; keys in the tool schema / `--help`): the whole package in one
   transaction. New single-flow test: `--prompt` (numbered user steps,
   passed RAW) or `goal`, + `--run` — THE rule;
   `after: start|<action id>` anchors a check to its page (none =
   end state);
   feature_content only on a named goal blocker. `ready: true` =
   parsed, matched, POM scoped, `{env:}` resolved — do not validate/
   preflight separately; run next. `ready: false`? Fix `blocking`,
   re-author — no bypass, no guessed action. Base URL lands in
   `resources/environments.yaml` under the returned `base_url_key`;
   use that key. Steps: probe output or `search_step`
   (`noodle steps <kw>…` — all words, ONE call); never `use_llm=True`
   when you can author
   directly; `append_to` adds a scenario sans regen (llm-performance).
   Result-pick binding and `evidence: screenshot`: playbook.
3. Execute + report — one call: `run_and_report` with `headless=True,
   retries=0, serve_reports=True` (`noodle run noodle_tests/<app>
   --headless --retries 0 --json --serve`): preflights secrets, runs,
   serves both reports, folds compact RCA in on red — never separate
   validate/RCA/serve calls. Green = `failed == 0` AND
   `verified: true`; `verified: false` (fuzzy healing behind a pass)
   is NOT a pass — read `unverified_reasons`/`healing_events`. A
   screenshot filename is not evidence — open the image.

Red? Budget: one probe, one run — more needs a named cause. Cheapest
evidence first: `rca_compact` names the cause and fix;
screenshot/network capture only if
inconclusive — vision costs ~10× text. Reproduce it ONCE (`probe
--do` replays the flow), re-author from evidence; cause-backed fixes
only, cap NOODLE_DEV_FIX_ATTEMPTS (default 10).
Wrong element? `noodle inspect <url> "<phrase>"` (`inspect_locator`).
Hand-edited? `validate_feature` before re-running. Failure taxonomy
(mutation-failed = fix the ACTION, never the assertion): playbook.

## Rules

- Steps must match the dictionary; never invent phrasing.
- Never invent assertion text absent from probe evidence; assert
  durable state, not a toast. Dynamic/decorated text? Assert the
  smallest stable substring; never silently drop the asked-for verify.
- Report success ONLY on passed AND `verified: true`; a healed/warned
  green is an anomaly — say so and log it.
- Selectors live in POM files, never inline (playbook: POM scoping).
- Never hardcode credentials/URLs — `{env:KEY}` via the app's env yaml
  + gitignored `<app>_secrets.env`. Prompt credentials: use without
  re-asking, write ONLY there (once, as `secret_values`), never in
  features/POM/env/prose/output.
- One user goal per scenario; pre-reqs in `Background:` or tags.
- Re-hosting an older run: ONLY `noodle report serve` (`serve_report`)
  — never `allure serve`, `http.server`, or `file://`.
- Payloads are pre-bounded: read as returned, no jq/grep/sed/head
  pipes; URLs pre-checked (no curl); workspace map: `noodle list`,
  not find/ls sweeps.
- Progress updates: max 2 sentences of current intent (e.g. "Serving
  the reports now"); quote only failing steps/errors. "do not output
  the shell command"? Then echo no command line.
- Keep each app's files in its own package: features, resources, report.
- This file and the skill card are already in context — don't re-read
  them. Scope every search to the app package, never `artifacts/`.
- Session diagnostics: a run result flags `diagnostic_due`,
  ~20 AIC burned, or the prompt says "--diagnostic"? Session end: ONE
  `log_diagnostic` (`noodle diagnostic log`) —
  read_docs('session-diagnostics'). Else nothing.
"""

# Paste-clean by construction (NOOD_0107): every line flush-left, one logical
# item per line, no markdown indentation and no hard-wrapped sentences — so it
# survives a code block, a Teams/Slack chat, or any plain-text editor intact.
# NOOD_0125 — a task brief, not a second operating manual. Rules 1-8 lived
# here AND in AGENTS.md (auto-loaded by every agent client); the duplicate
# block rode along on every model call for no benefit. The prompt now carries
# only the facts the agent can't infer (app, URL, goal, acceptance, creds,
# shell preference) plus one pointer to AGENTS.md. Removed "Steps a human
# would take" — the agent owns procedure via probe + step dictionary.
_PROMPT_TEMPLATE = """\
Fill the [BRACKETS], then paste this whole message into your agent. Delete any optional line you don't need.

Use Noodle to create and run this test. Read and follow the workspace AGENTS.md first — it carries every operating rule (the probe → author → execute pipeline, RCA-first fixes, report serving).

App under test: [APP NAME]
Base URL: [https://...]
User goal: [what a human is trying to accomplish]
Verify: [what proves it worked]
Credentials/config: [none | USERNAME=… PASSWORD=… | keys already in secrets.env / environments.yaml] — any value here is written only to the app's gitignored `<app>_secrets.env` and referenced as `{env:KEY}`; the agent never repeats it in features, POM, env files, or its replies.
Shell commands in replies: [ok | do not output the shell command]

After the run, always include both the Allure and RCA report links. On a red run, also include the compact RCA reason.
"""

# NOOD_0107 — the rules must reach the agent even when the user pastes no
# prompt at all. NOOD_0117: no @-import — Claude Code (like Copilot) now
# loads AGENTS.md natively, and the @-import made the same 250 lines ride
# along TWICE on every model call (the single biggest per-call token line
# item in the NOOD_0117 cost audit). Clients that only read CLAUDE.md get
# the plain-text pointer and read the file once. Plain files only —
# symlinks break on Windows checkouts.
_CLAUDE_MD_POINTER = ("Workspace instructions live in AGENTS.md — auto-loaded "
                      "by most agent clients; read it now if yours doesn't.\n")


def _template_files(root: Path) -> dict:
    """The generated instruction/template files, mapping path → current content.
    Shared by `init` (writes/refreshes) and `doctor` (read-only staleness check)
    so the two can't drift on which files they consider (NOOD_0128)."""
    sample = root / "noodle_tests" / "sample_app"
    return {
        root / "README.md": _WORKSPACE_README,
        root / "AGENTS.md": _AGENTS_MD,
        root / "CLAUDE.md": _CLAUDE_MD_POINTER,
        root / "PROMPT_TEMPLATE.md": _PROMPT_TEMPLATE,
        sample / "features" / "login.feature": _SAMPLE_FEATURE,
        sample / "resources" / "pageobjects" / "login_pom.yaml": _SAMPLE_POM,
    }


@app.command()
def doctor(
    path: str = typer.Argument(".", help="Directory to diagnose (default: current dir). Doctor walks this path and its ancestors to find an engine checkout or a workspace (noodle.yaml) — never siblings or the wider filesystem."),
    scope: str = typer.Option("auto", "--scope", help="auto | engine | workspace | install — force a profile instead of auto-detecting. `install` inspects the build + every `noodle` launcher on PATH only."),
    json_out: bool = typer.Option(False, "--json", help="Emit one bounded JSON object (context + checks with stable IDs) instead of text."),
):
    """NOOD_0138 — context-aware, read-only health check. Always checks the
    INSTALL (resolved build path, editable vs non-editable copy, git SHA, and
    launcher PROVENANCE on PATH — identical duplicates are info, conflicting
    builds are a failure with the exact reinstall command). Then, by context:
    an ENGINE checkout gets editable-linkage and stray-workspace-file checks
    (never template comparison); a generated WORKSPACE gets config/layout/
    template-drift/MCP checks with `noodle init` remediation (NOOD_0128).
    Changes nothing; exit 0 = healthy, 1 = findings, 2 = bad path/scope."""
    from noodle import doctor as _doctor
    try:
        ctx, checks = _doctor.diagnose(path, scope)
    except _doctor.DoctorError as e:
        typer.echo(f"doctor: {e}", err=True)
        raise typer.Exit(2)
    code = _doctor.exit_code(checks)
    typer.echo(_doctor.render_json(ctx, checks) if json_out else _doctor.render_text(ctx, checks))
    raise typer.Exit(code)


@app.command(short_help="Re-link this install to its engine checkout — the step after `git pull`/`git checkout`.")
def update():
    """NOOD_0156 — re-link the running `noodle` to its engine checkout. THE
    step after `git pull` or `git checkout <branch>`: an editable install
    keeps the CODE current, but a branch that changed dependencies or the
    version needs the reinstall to land, and a non-editable copy needs it for
    everything. Runs exactly the command `noodle doctor` recommends, in the
    clone, against THIS interpreter — so it repairs the environment whose
    `noodle` you just invoked, venv or system, without choosing one for you.
    Deliberately never runs git: pull and checkout stay yours, because what to
    do with a dirty tree or a branch you chose on purpose is not this
    command's call. Exit 0 = install refreshed."""
    from noodle import install_check
    clone = install_check.clone_root()
    if clone is None:
        typer.echo("update: no noodle engine checkout found — this build is not linked to a "
                   "clone, and no clone sits at or above the current directory.\n"
                   "        cd into your noodle clone and re-run `noodle update`.", err=True)
        raise typer.Exit(2)
    argv = install_check.reinstall_argv()
    typer.echo(f"  🧬 before: {install_check.build_line()}")
    typer.echo(f"  $ {' '.join(argv)}\n    (in {clone})")
    rc = subprocess.run(argv, cwd=clone).returncode
    if rc != 0:
        typer.echo(f"\nupdate: reinstall failed (exit {rc}) — your existing install is untouched. "
                   "Run the command above by hand for the full resolver output.", err=True)
        if os.name == "nt":
            # Windows holds the running noodle.exe open, so a reinstall can't
            # replace the launcher from inside it. -m runs the same command
            # with no shim in the picture.
            typer.echo("        On Windows the running launcher can be locked — retry as "
                       "`python -m noodle update`.", err=True)
        raise typer.Exit(rc)
    typer.echo("  ✅ install refreshed — confirm with `noodle --version`")
    others = install_check.shims_on_path()[1:]
    if others:
        typer.echo(f"  ⚠️ {len(others)} other `noodle` launcher(s) on PATH were NOT touched: "
                   + ", ".join(others) + "\n     `noodle doctor` reports whether they run a "
                   "different build.")


@app.command()
def docs(
    name: str = typer.Argument(None, help="Doc to read, e.g. agent-playbook. Omit for the index (name, summary, byte cost per doc)."),
    section: str = typer.Option(None, "--section", "-s", help="One section of a doc, by `## ` title (exact or substring) or 1-based number"),
    query: str = typer.Option(None, "--query", "-q", help="Grep every doc; hits carry doc + section + line"),
):
    """Read framework docs — the CLI form of the MCP read_docs tool
    (NOOD_0160), so an agent without MCP still reaches content the
    instruction surfaces only point at. Large docs return a section index;
    fetch one section rather than the whole file."""
    from noodle.mcp.server import read_docs
    out = read_docs(name=name, query=query, section=section)
    if "content" in out:
        typer.echo(out["content"])
    else:
        _json_out(out, ensure_ascii=False)
    if "error" in out:
        raise typer.Exit(1)


@app.command()
def init(
    path: str = typer.Argument(".", help="Directory to scaffold the workspace in — or the literal word 'mcp' to write MCP client config instead (same as `noodle init-mcp`)"),
    llm: str = typer.Option(None, "--llm", help="claude | gemini | ollama — persist NOODLE_MODEL into .env so `noodle repl` picks it up automatically, no --llm flag needed next time"),
    model: str = typer.Option(None, "--model", help="Override the default model string for --llm, e.g. anthropic/claude-haiku-4-5"),
    force: bool = typer.Option(False, "--force", help="Refresh outdated template files (AGENTS.md, README.md, samples…) in an existing workspace; originals are backed up to *.bak. Config files (.env, noodle.yaml, pom.yaml) are never touched."),
):
    """Scaffold a test workspace (noodle.yaml, .env, README.md, AGENTS.md AI
    instructions, PROMPT_TEMPLATE.md, and a noodle_tests/sample_app/ template
    package with features/, resources/pageobjects/ and report/ folders, plus
    the global noodle_tests/pom.yaml and engine glue). Each app-under-test
    gets its own package (see docs/feature-packages.md). --llm writes
    NOODLE_MODEL (and NOODLE_LLM_URL for ollama) into .env. Also wires MCP
    client config (.mcp.json, .vscode/mcp.json, .copilot/mcp-config.json)
    and the /noodle agent skill (.claude/skills/, .copilot/skills/) for
    Claude Code and Copilot CLI (NOOD_0098) — skipped silently if this
    install doesn't ship them (a wheel install, not a git checkout).

    Re-running on an EXISTING workspace is safe (NOOD_0089): engine-glue files
    are kept in sync with the installed engine automatically, template files
    that drifted from the current scaffold are reported (refresh with
    --force, originals saved as *.bak), and your config/POM files are never
    overwritten."""
    if path == "mcp":
        return init_mcp(".", force=force)
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    tests = root / "noodle_tests"
    sample = tests / "sample_app"
    # Three ownership classes, three upgrade policies (NOOD_0089):
    #   glue     — engine re-exports; must match the installed engine → auto-sync
    #   template — docs/samples users copy from → refresh only with --force (+ .bak)
    #   config   — user-owned settings/POM → never overwritten by init
    glue = {
        tests / "environment.py": _ENVIRONMENT_PY,
        tests / "steps" / "z_catch_all.py": _CATCH_ALL_PY,
        sample / "report" / "README.md": _REPORT_README,
    }
    templates = _template_files(root)
    config_files = {
        root / "noodle.yaml": _NOODLE_YAML,
        root / ".env": _env_stub(llm, model),
        root / ".gitignore": _GITIGNORE,      # NOOD_0118 — keep generated <app>_secrets.env out of git
        tests / "pom.yaml": _GLOBAL_POM,
    }
    env_path = root / ".env"
    env_existed = env_path.exists()
    created, updated, stale = [], [], []

    def _write(f: Path, text: str):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    for f, text in {**glue, **templates, **config_files}.items():
        if not f.exists():
            _write(f, text)
            created.append(str(f))
            continue
        if f.read_text() == text or f in config_files:
            continue  # up to date, or user-owned config — leave alone
        if f in glue:
            _write(f, text)
            updated.append(str(f))
        elif force:
            f.rename(f.with_suffix(f.suffix + ".bak"))
            _write(f, text)
            updated.append(f"{f} (old copy → {f.name}.bak)")
        else:
            stale.append(str(f))
    if created:
        typer.echo("Created:\n  " + "\n  ".join(created))
    if updated:
        typer.echo("Updated to match this noodle version:\n  " + "\n  ".join(updated))
    if stale:
        typer.echo(
            "Outdated templates kept (they differ from this noodle version — "
            "possibly your own edits):\n  " + "\n  ".join(stale)
            + "\n  → re-run `noodle init --force` to refresh them; "
              "originals are saved as *.bak")
    if not (created or updated or stale):
        typer.echo(f"Workspace already up to date at {root.resolve()}")
    if llm and env_existed:
        typer.echo(f"Note: --llm ignored — {env_path} already exists; "
                   f"add NOODLE_MODEL yourself or delete .env and re-run init.")
    # NOOD_0095 — wire MCP client config in the same shot: every agent-driven
    # workspace needs it, and forgetting `noodle init-mcp` left agents falling
    # back to raw CLI + port-hunting.
    typer.echo("\nMCP client config:")
    init_mcp(path, force=False)
    # NOOD_0098 — same reasoning for the /noodle skill (Claude Code, Copilot
    # CLI): without it, a workspace has MCP tools but no slash-command
    # shortcut, and the gap only surfaces as "why did /noodle disappear"
    # after `noodle init` in a fresh folder.
    typer.echo("\nAgent skill (/noodle slash command):")
    _copy_skills(root, force)
    typer.echo(f"\nNext: cd {path} && noodle repl  — next steps in README.md")
    typer.echo("Note: .env is a hidden file — `ls -a` to see it.")
    # NOOD_0133 — init is the first post-install command, the best moment to
    # catch a stale non-editable copy shadowing the clone, before tests exist.
    from noodle import install_check
    install_check.warn_if_stale(typer.echo)


# Skill sources ship in the engine repo, not the installed noodle package —
# present for a git checkout / editable install, absent for a wheel (same
# caveat as docs/, see mcp/server.py:_docs_dir). Best-effort: silently skip
# if not found rather than fail `init` over an optional convenience.
_SKILL_DIRS = [
    (Path(".claude") / "skills" / "noodle", "Claude Code"),
    (Path(".copilot") / "skills" / "noodle", "Copilot CLI"),
]


def _copy_skills(root: Path, force: bool) -> None:
    engine_root = Path(__file__).resolve().parent.parent
    for rel, label in _SKILL_DIRS:
        src, dst = engine_root / rel, root / rel
        if not (src / "SKILL.md").is_file():
            continue  # not shipped with this install — nothing to copy
        if dst.exists() and not force:
            typer.echo(f"  {dst}: kept (already present — --force to refresh)")
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        typer.echo(f"  {dst}: installed ({label})")


# MCP client-config stubs written by `noodle init mcp` (NOOD_0089).
def _resolve_mcp_command() -> str:
    """Absolute path to the noodle-mcp launcher. A bare "noodle-mcp" only
    resolves if it happens to be on the invoking process's PATH (editors
    often launch MCP servers with a minimal env) — resolving to an absolute
    path here means the written config keeps working even if that PATH
    later drops the venv/bin dir it came from (NOOD_0100). shutil.which(...,
    path=...) rather than a manual filename join: on Windows the installed
    launcher is "noodle-mcp.exe" (console_scripts are always compiled to a
    .exe there), which only shutil.which's PATHEXT-aware search resolves —
    a plain Path(...) / "noodle-mcp" match would silently miss it."""
    venv_bin = str(Path(sys.executable).parent)
    return (shutil.which("noodle-mcp", path=venv_bin)
            or shutil.which("noodle-mcp")
            or "noodle-mcp")


def _merge_mcp_json(f: Path, container_key: str, entry: dict, force: bool) -> str:
    """Insert the noodle server under `container_key` in JSON file `f`,
    preserving everything else. Returns created|updated|kept."""
    data = {}
    if f.exists():
        try:
            data = json.loads(f.read_text() or "{}")
        except json.JSONDecodeError:
            return f"kept (unparseable JSON — fix {f} by hand)"
    servers = data.setdefault(container_key, {})
    if "noodle" in servers and servers["noodle"] == entry:
        return "kept (already configured)"
    if "noodle" in servers and not force:
        return "kept (existing noodle entry differs — --force to overwrite)"
    existed = f.exists()
    servers["noodle"] = entry
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2) + "\n")
    return "updated" if existed else "created"


@app.command("init-mcp")
def init_mcp(
    path: str = typer.Argument(".", help="Workspace directory"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing differing noodle entry"),
):
    """Wire this workspace up for MCP-driven agents (`noodle init mcp` works
    too): writes/merges the noodle server into `.mcp.json` (Claude Code),
    `.vscode/mcp.json` (VS Code Copilot agent mode), and
    `.copilot/mcp-config.json` (standalone Copilot CLI). Existing config is
    merged, never clobbered.

    In a CI pipeline (Azure DevOps etc.) there is no interactive agent to
    read these files — pipelines should call the noodle CLI directly
    (`noodle run …`), see azure-pipelines.yml in the noodle repo. The files
    are still written (harmless, and lets a pipeline commit them for the
    team), with a note so nobody waits for an MCP server that never starts."""
    root = Path(path)
    entry = {"command": _resolve_mcp_command(), "args": []}
    results = {
        root / ".mcp.json": _merge_mcp_json(root / ".mcp.json", "mcpServers", entry, force),
        root / ".vscode" / "mcp.json": _merge_mcp_json(root / ".vscode" / "mcp.json", "servers",
                                                       {"type": "stdio", **entry}, force),
        root / ".copilot" / "mcp-config.json": _merge_mcp_json(
            root / ".copilot" / "mcp-config.json", "mcpServers", entry, force),
    }
    for f, status in results.items():
        typer.echo(f"  {f}: {status}")
    if os.getenv("TF_BUILD") or os.getenv("CI"):
        typer.echo("\nCI environment detected: MCP config is for interactive agents "
                   "(Claude Code, Copilot). Pipelines should run the noodle CLI "
                   "directly — e.g. `noodle run noodle_tests/ --workspace .`.")
    else:
        typer.echo("\nDone. Claude Code picks up .mcp.json automatically; VS Code "
                   "Copilot reads .vscode/mcp.json (enable MCP in settings); "
                   "Copilot CLI reads .copilot/mcp-config.json (launch `copilot` "
                   "from this directory). The server runs on demand — nothing to "
                   "start manually.")


@app.command()
def author(
    spec: str = typer.Option(None, "--spec", help="Path to a JSON or YAML spec file (or '-' for stdin) with: app_name, base_url, feature_path, and EITHER feature_content (one Gherkin string; pom_content is likewise one YAML string, never a filename map) OR goal (NOOD_0137 constrained mode — the engine probes and compiles the feature/POM itself; see author_test). Optionally: environment_values, required_secret_keys, secret_values, overwrite."),
    prompt: str = typer.Option(None, "--prompt", help="NOOD_0169 — numbered plain-English steps ('1. go to <url> 2. search for X 3. add to cart 4. verify cart has X'); the engine expands them deterministically into a goal (ambiguous steps borrow their subject from neighbouring steps, every inference echoed under prompt_expansion.assumptions) and derives app_name/base_url/feature_path from the URL. No spec file needed; combine with --run for prompt → authored → run → reports in ONE call."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    as_json: bool = typer.Option(False, "--json", help="Structured output for agents/CI"),
    run: bool = typer.Option(False, "--run", help="NOOD_0137 — atomic author+run: after a ready author, run once (headless, retries=0), serve both reports, and fail when 0 scenarios passed. Blocked authoring launches no browser."),
):
    """NOOD_0128 — write a whole test package in one transaction (app package +
    environments.yaml + POM + feature + missing secret placeholders), validated,
    with rollback on failure. Replaces the copy-sample_app → rename → edit×4 →
    validate sequence. NOOD_0130 — a spec `secret_values` map (from the original
    prompt) is written ONLY into the gitignored `<app>_secrets.env`; its values
    are never printed or returned. Any required key left without a value is a
    placeholder to populate locally.

    \b
    NOOD_0165 — goal mode, the whole spec (the engine probes, compiles the
    Gherkin + POM, and with --run runs it; you never look up step phrasings,
    dismissal wording or docs for a goal):
      app_name: <app>
      base_url: <url>
      feature_path: noodle_tests/<app>/features/<name>.feature
      goal:
        scenario: Search returns matching results
        dismissals: \\[location_prompt, popups]
        actions: [{do: search, term: "<term>"}]
        checks: [{count: results, min: 1}, {any_of: ["<text>"]}]
    \b

    Exit 0 means READY: Gherkin parsed, every step matched, POM selector scope
    passed, and every {env:KEY} the feature references resolves (NOOD_0131) —
    run it next; a separate `noodle validate`/preflight adds nothing."""
    import yaml

    from noodle.repl import core
    if (spec is None) == (prompt is None):
        raise typer.BadParameter("pass exactly one of --spec or --prompt",
                                 param_hint="'--spec' / '--prompt'")
    if prompt is not None:
        # NOOD_0169 — prompt mode: expansion + derivation happen engine-side
        result = core.author_test(prompt=prompt, run_after_author=run,
                                  workspace=workspace)
    else:
        raw = sys.stdin.read() if spec == "-" else Path(spec).read_text()
        try:
            data = yaml.safe_load(raw) or {}
        except Exception as e:
            raise typer.BadParameter(f"spec is not valid JSON/YAML: {e}", param_hint="'--spec'")
        if not isinstance(data, dict):
            raise typer.BadParameter("spec must be a JSON/YAML object", param_hint="'--spec'")
        missing = [k for k in ("app_name", "base_url", "feature_path")
                   if not data.get(k)]
        if not data.get("feature_content") and not data.get("goal"):
            missing.append("feature_content (or goal)")
        if missing:
            raise typer.BadParameter(f"spec missing required field(s): {', '.join(missing)}",
                                     param_hint="'--spec'")
        result = core.author_test(
            app_name=data["app_name"], base_url=data["base_url"],
            feature_path=data["feature_path"],
            feature_content=data.get("feature_content"),
            pom_content=data.get("pom_content"),
            environment_values=data.get("environment_values"),
            required_secret_keys=data.get("required_secret_keys"),
            secret_values=data.get("secret_values"),   # NOOD_0130 — write-only, never echoed
            goal=data.get("goal"), run_after_author=run,
            overwrite=bool(data.get("overwrite", False)),
            # NOOD_0156 — explicit expert override for the manual-fallback gate;
            # autonomous agents must never set it.
            allow_unverified_intent=bool(data.get("allow_unverified_intent", False)),
            workspace=workspace)
    if as_json:
        _json_out(result)
        raise typer.Exit(0 if result["ok"] else 1)
    # NOOD_0169 — say what was predicted, right where the result is read
    exp = result.get("prompt_expansion")
    if exp and exp.get("translation_mode"):
        typer.echo(f"  · translation: {exp['translation_mode']}")
    for a in (exp or {}).get("assumptions", []):
        typer.echo(f"  ~ {a}")
    for s in result.get("unrecognized_steps", []):
        typer.echo(f"  ✗ {s}")
    if result.get("planner"):
        typer.echo(f"  · planner: {result['planner']['state']}")
    if "run" in result and "author" in result:      # NOOD_0137 atomic shape
        a, r = result["author"], result["run"]
        typer.echo(f"  {'✓' if a.get('ready') else '✗'} authored {a.get('feature')}")
        for b in a.get("blocking", []):
            typer.echo(f"    {b}")
        if r.get("skipped"):
            typer.echo(f"  ✗ run skipped: {r['skipped']}")
        else:
            typer.echo(f"  {'✓' if r.get('ok') else '✗'} run: "
                       f"{r.get('passed', 0)} passed, {r.get('failed', 0)} failed"
                       + (f" — {r['error']}" if r.get("error") else ""))
            for u in (r.get("served") or {}).get("urls", []):
                typer.echo(f"    {u}")
        raise typer.Exit(0 if result["ok"] else 1)
    if not result["ok"]:
        typer.echo(f"  ✗ {result['error']}")
        raise typer.Exit(1)
    typer.echo(f"  ✓ authored {result['feature']}")
    for label, key in (("POM", "pom"), ("environments", "environments"),
                       ("secrets", "secrets")):
        if result.get(key):
            typer.echo(f"    {label}: {result[key]}")
    if result["missing_secret_keys"]:
        typer.echo("  ⚠ populate these secret keys locally before running: "
                   + ", ".join(result["missing_secret_keys"]))
    for w in result.get("warnings", []):
        typer.echo(f"  ⚠ {w}")
    if result.get("unmatched"):
        typer.echo("  ⚠ steps needing an LLM fallback (rephrase to the dictionary): "
                   + "; ".join(result["unmatched"]))
    if result.get("blocking"):
        typer.echo("  ✗ NOT READY — fix before running (no separate validate needed):")
        for b in result["blocking"]:
            typer.echo(f"    {b}")
        raise typer.Exit(1)
    raise typer.Exit(0)


@app.command()
def summary(
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    llm: str = typer.Option("none", "--llm", help="none | claude | gemini | ollama — richer narrative via litellm"),
    as_json: bool = typer.Option(False, "--json", help="Structured output (counts + failures) for agents/CI (NOOD_0045)"),
):
    """Plain-English summary of the last run (follows the last run's
    artifacts root — <app>/report/ for single-app runs, artifacts/ otherwise)."""
    from noodle.reporting import summary as _summary
    results = str(_paths.last_run_root(workspace) / "allure-results")
    if as_json:
        _json_out(_summary.collect(results))
    elif llm and llm != "none":
        typer.echo(_summary.summarize_llm(results))
    else:
        report = str(_paths.last_run_root(workspace) / "reports" / "allure-report")
        typer.echo(_summary.render(results, report))


@app.command()
def cost(
    target: str = typer.Argument(None, help="Prompt or .feature file to estimate — omit to show the last run's actual spend"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    model: str = typer.Option(None, "--model", help="Model string to price against (default: NOODLE_MODEL from the workspace .env)"),
    as_json: bool = typer.Option(False, "--json", help="Structured output for agents/CI"),
):
    """LLM token/dollar cost (NOOD_0080): the last run's actual spend, or a
    pre-flight token estimate for a file. Covers Noodle's own NOODLE_MODEL
    calls only — a driving agent's (Claude/Copilot) spend is its own bill."""
    from noodle.llm import cost as _cost
    if target is None:
        results = str(_paths.last_run_root(workspace) / "allure-results")
        total = _cost.load_total(results)
        if as_json:
            _json_out(total or {})
        else:
            typer.echo(f"  💰 {_cost.format_line(total)}")
        return
    # Estimate mode: workspace .env supplies NOODLE_MODEL unless --model given.
    from dotenv import load_dotenv
    load_dotenv(Path(workspace) / ".env")
    text = Path(target).read_text()
    est = _cost.estimate(text, model=model)
    if as_json:
        _json_out(est)
        return
    usd = (f"~${est['usd_input_floor']:.4f}" if est["usd_input_floor"] is not None
           else "pricing unknown (self-hosted/free?)")
    typer.echo(f"  💰 Estimate for {target}: {est['input_tokens']:,} input tokens | "
               f"{usd} input-cost floor (output tokens unknowable pre-run) | "
               f"model {est['model']}")


@app.command("rca-report")
def rca_report(
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    out: str = typer.Option(None, "--out", "-o", help="Write to this file instead of stdout"),
    llm: bool = typer.Option(False, "--llm", help="Add a prose narrative via NOODLE_MODEL (text-only, no vision needed)"),
    propose_fix: bool = typer.Option(False, "--propose-fix", help="Ask NOODLE_MODEL for a unified-diff fix per failure (text-only, never applied)"),
    serve: bool = typer.Option(False, "--serve", help="Also render rca.html and open it in the browser (no server needed — self-contained page, like `noodle repl`'s 'serve the rca')"),
    compact: bool = typer.Option(False, "--compact", help="NOOD_0117 cheap-evidence-first: verdict + failing step + suggested fix per failure, a few lines total — read this before any screenshot or network capture."),
):
    """Root-cause every failed/errored scenario from the last run into Markdown.

    Merges a free, instant heuristic classifier (pattern-matches the assertion
    message + captured console warnings) with noodle/rca.py's agentic verdict
    when NOODLE_RCA + a vision-capable NOODLE_MODEL produced one."""
    from noodle.reporting import rca_report as _rca
    results = str(_paths.last_run_root(workspace) / "allure-results")
    if compact:
        typer.echo(_rca.render_compact(results))
        return
    if propose_fix:
        md = _rca.propose_fixes(results, workspace, config.load(workspace)["tests_dir"])
    else:
        md = _rca.render_markdown_llm(results) if llm else _rca.render_markdown(results)
    if out:
        Path(out).write_text(md)
        typer.echo(f"RCA report written to {out}")
    else:
        typer.echo(md)
    if serve:
        html_dir = _paths.last_run_root(workspace) / "reports"
        html_path = _rca.open_html(results, str(html_dir / "rca.html"))
        typer.echo(f"RCA HTML opened from {html_path}")


@app.command()
def validate(
    path: str = typer.Argument(None, help="Path to validate (default: workspace tests_dir)"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    resolve: bool = typer.Option(False, "--resolve", help="Also dry-run every step against the pattern table — shows which steps need the LLM fallback"),
    as_json: bool = typer.Option(False, "--json", help="With --resolve: per-file matched/unmatched steps as JSON (NOOD_0045)"),
):
    """Parse .feature files and check variable references — no browser launched."""
    workspace, path = _resolve_run_target(workspace, path)
    if path is None:
        path = config.load(workspace)["tests_dir"]
    target = Path(workspace) / path
    if resolve:
        # NOOD_0055 — same workspace-docs wiring as `noodle run` (hooks.before_all)
        # and step-search: without it, steps accepted into the workspace's own
        # docs/agent_patterns.yaml dry-ran as unmatched here.
        from noodle.resolver import patterns as _patterns
        from noodle.resolver import step_resolver
        docs_dir = Path(workspace) / "docs"
        step_resolver.set_docs_dir(docs_dir)
        _patterns.set_agent_patterns_dir(docs_dir)
        if as_json:
            raise typer.Exit(_validate_resolve_json(target))
        rc = _validate_resolve(target)
        # NOOD_0126 — under --resolve, a POM file that can never scope to the
        # feature's URL is a hard stop, not a warning: its keys silently never
        # resolve, which is exactly the failure this dry-run exists to catch.
        scope_fail = _lint_pom_scopes(target, hard=True)
        raise typer.Exit(rc or scope_fail)
    result = subprocess.run([*_BEHAVE_CMD, path, "--dry-run", "--no-capture"], cwd=workspace)
    _lint_pom_scopes(target)
    raise typer.Exit(result.returncode)


def _lint_pom_scopes(target: Path, hard: bool = False) -> int:
    """POM lints. The orphan-key lint (NOOD_0109) always warn-only. The
    auto-scope lint (NOOD_0022; a *_pom.yaml whose stem can never appear in any
    sibling feature's URL, so its keys silently never apply) warns by default,
    but with hard=True (validate --resolve, NOOD_0126) it's a hard failure —
    returns 1 so the caller exits non-zero. Fix is a `match: {}` block."""
    from noodle.repl import validate as _validate
    # A .feature path still lints its app package — walk up to the app dir.
    if target.suffix == ".feature":
        target = target.parent.parent
    warnings = _validate.lint_pom_scopes(target)
    if warnings:
        typer.echo(f"\nPOM auto-scope lint — {len(warnings)} warning(s):")
        for w in warnings:
            typer.echo(w)
        if hard:
            typer.echo("  → these fail `validate --resolve`: add `match: {}` "
                       "(applies on every URL) or a real `match:` block to each "
                       "file above before running.")
    orphans = _validate.lint_pom_orphan_keys(target)
    if orphans:
        typer.echo(f"\nPOM key lint — {len(orphans)} warning(s):")
        for w in orphans:
            typer.echo(w)
    return 1 if (hard and warnings) else 0


def _validate_resolve(target: Path) -> int:
    """Classify every step in every .feature under target as [pattern] or [LLM].
    Exit 1 only on parse errors — LLM-fallback steps are legal, just flagged."""
    from noodle.repl import validate as _validate
    files = [target] if target.suffix == ".feature" else sorted(target.rglob("*.feature"))
    if not files:
        typer.echo(f"No .feature files under {target}")
        return 1
    rc = 0
    for f in files:
        typer.echo(f"\n{f}")
        result = _validate.check_feature(f.read_text(), filename=str(f))
        if result["error"]:
            rc = 1
        typer.echo(_validate.render(result))
    return rc


def _validate_resolve_json(target: Path) -> int:
    """--resolve --json: the same classification as _validate_resolve, as one
    JSON array for agents/CI. Exit 1 only on parse errors, same contract."""
    from noodle.repl import validate as _validate
    files = [target] if target.suffix == ".feature" else sorted(target.rglob("*.feature"))
    out, rc = [], 0
    for f in files:
        result = _validate.check_feature(f.read_text(), filename=str(f))
        if result["error"]:
            rc = 1
        out.append({"path": str(f), "error": result["error"],
                    "steps": [{"step": line, "matched": ok}
                              for line, ok in result["steps"]]})
    _json_out(out)
    return rc if files else 1


@app.command("list")
def list_scenarios(
    path: str = typer.Argument(None, help="Path to scan (default: workspace tests_dir)"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    as_json: bool = typer.Option(False, "--json", help="Feature/tag inventory as JSON, no behave dry-run — scenario names only with --query (NOOD_0162)"),
    query: str = typer.Option(None, "--query", help="With --json: substring match over path/feature/scenario/tag; matching features carry their scenario names"),
):
    """List all discovered scenarios without running them."""
    if as_json:
        from noodle.repl import core
        _json_out(core.list_tests(workspace, query=query))
        return
    if path is None:
        path = config.load(workspace)["tests_dir"]
    subprocess.run([
        *_BEHAVE_CMD, path, "--dry-run", "--no-capture",
        "--format", "pretty", "--no-skipped",
    ], cwd=workspace)


@app.command()
def steps(
    keyword: list[str] = typer.Argument(None, help="Filter(s) — each matches the step text, its section, or its action type (e.g. 'clipboard'). Several keywords = one call, union of hits (NOOD_0169: a reviewed session paid 10 calls for 10 words)"),
):
    """Search the step dictionary and print matching example steps — fast
    in-terminal lookup for agents and manual testers (Phase U)."""
    from noodle.resolver.step_resolver import example_index
    index = example_index()
    if not index:
        # NOOD_0145 — installed distributions bundle the dictionary
        # (noodle/_docs/); "run from a repo checkout" was stale advice, and
        # the bundled path is an implementation detail — never printed.
        typer.echo("Step dictionary not found in the installed Noodle "
                   "package. Reinstall Noodle or use a source checkout.")
        raise typer.Exit(1)
    if not keyword:
        # NOOD_0161 — bare `noodle steps` dumped the whole dictionary (20 KB).
        # Same shape as `noodle docs` on a large doc: index first, section on
        # request. The filter already existed; nothing told the caller to use it.
        counts: dict[str, int] = {}
        for e in index:
            counts[e["section"]] = counts.get(e["section"], 0) + 1
        typer.echo(f"{len(index)} steps in {len(counts)} sections — "
                   "`noodle steps <keyword>` prints one (keyword matches the "
                   "step text, its section, or its action type):\n")
        for section, n in counts.items():
            typer.echo(f"  {section}  ({n})")
        return
    # NOOD_0169 — several keywords in ONE call (union, dictionary order,
    # deduped): the per-word loop was 10 separate CLI round trips.
    kws = [k.lower() for k in keyword]
    picked, missed = set(), []
    for kw in kws:
        kw_hits = [i for i, e in enumerate(index)
                   if kw in e["step"].lower()
                   or kw in e["section"].lower()
                   or kw in (e["type"] or "")]
        if not kw_hits:
            missed.append(kw)
        picked.update(kw_hits)
    hits = [index[i] for i in sorted(picked)]
    if not hits:
        typer.echo(f"No steps matching {', '.join(repr(k) for k in kws)}. "
                   "Try a broader word, or `noodle steps` for everything.")
        raise typer.Exit(1)
    for kw in missed:
        typer.echo(f"(no steps matching '{kw}')")
    section = None
    for e in hits:
        if e["section"] != section:
            section = e["section"]
            typer.echo(f"\n{section}")
        note = "" if e["type"] else "   # resolved by the LLM fallback"
        typer.echo(f"  {e['step']}{note}")
    # NOOD_0145 — a portable reference, not a source-repo path: agents running
    # in an external test workspace read "docs/steps_dictionary.md" as
    # <workspace>/docs/…, search a directory that doesn't exist, and conclude
    # the documentation is missing.
    typer.echo(
        f"\n{len(hits)} step(s). Section index: `noodle steps`; full "
        "reference: `noodle docs steps_dictionary` (MCP: "
        "read_docs('steps_dictionary'))"
    )


@app.command()
def wok(
    name: str = typer.Argument(None, help="A wok to inspect (web, mobile, desktop, performance); omit to list all"),
):
    """NOOD_0155 — show Noodle's woks (capability work areas): what each one
    tests, the engine behind it, its routing tags, and whether its optional
    dependencies are installed on this machine."""
    from noodle import wok as _wok
    chosen = [_wok.WOKS[name]] if name in _wok.WOKS else None
    if name and chosen is None:
        typer.echo(f"No wok named '{name}'. Woks: {', '.join(_wok.WOKS)}")
        raise typer.Exit(1)
    for w in (chosen or _wok.WOKS.values()):
        ready = "ready" if _wok.installed(w) else \
            f"needs: pip install noodle[{','.join(w.extras)}]"
        typer.echo(f"\n🍜 {w.title} wok ({w.name}) — {ready}")
        typer.echo(f"   {w.blurb}")
        typer.echo(f"   tags: {' '.join('@' + t for t in w.tags)}")
        for engine in w.engines:
            typer.echo(f"   engine: {engine}")
        if chosen:
            typer.echo(f"   samples:     {w.samples}")
            typer.echo(f"   unit tests:  {w.unit_tests}")
            typer.echo(f"   screenshots: {w.screenshots}")
    typer.echo("\nEvery wok speaks Gherkin and reports through Allure + RCA. "
               "Full concept doc: docs/woks.md")


@app.command("step-search")
def step_search_cmd(
    query: str = typer.Argument(..., help="Plain-English description of the step you're looking for"),
    workspace: str = typer.Option(".", "--workspace", "-w",
        help="Workspace whose docs/ holds the project's own staged vocabulary "
             "(read AND written here — same place `noodle run --workspace` "
             "loads accepted suggestions from)"),
    accept: bool = typer.Option(False, "--accept",
        help="Non-interactively write the suggested new step (docs/agent_patterns.yaml "
             "+ steps_dictionary.md) if one is offered — for CI/scripting"),
    no_llm: bool = typer.Option(False, "--no-llm",
        help="Skip the local LLM tie-breaker even if NOODLE_MODEL is set"),
):
    """Find the closest existing step for a plain-English description (the
    step-search-engine: deterministic ranking + an optional local-LLM
    tie-break). No good match -> drafts a new one (the step-suggestion-
    engine); --accept writes it non-interactively. See "Finding a step /
    suggesting a new one" in docs/steps_dictionary.md."""
    from noodle.repl import step_suggestion_engine as sse
    from noodle.resolver import patterns as _patterns
    from noodle.resolver import step_resolver
    from noodle.resolver.step_search_engine import search_step

    docs_dir = Path(workspace) / "docs"
    step_resolver.set_docs_dir(docs_dir)
    _patterns.set_agent_patterns_dir(docs_dir)

    result = search_step(query, use_llm=not no_llm)
    if result.match:
        conf = result.confidence + (", via LLM" if result.llm_used else "")
        typer.echo(f"Best match ({conf} confidence):")
        typer.echo(f"  {result.match.step}")
        typer.echo(f"  section: {result.match.section}   type: {result.match.type}")
        return

    typer.echo(f"No good match for: {query!r}")
    suggestion = sse.draft_suggestion(query, result, use_llm=not no_llm)
    if not suggestion.fits_existing_type:
        typer.echo(suggestion.rationale)
        raise typer.Exit(1)

    typer.echo("Suggested new step:")
    typer.echo(f"  {suggestion.keyword} {suggestion.phrase}")
    typer.echo(f"  action_type: {suggestion.action_type}  ({suggestion.rationale})")
    if accept:
        written = sse.accept_suggestion(suggestion)
        typer.echo(f"→ Wrote {written['patterns_file']}")
        typer.echo(f"→ Wrote {written['dictionary_file']}")
    else:
        typer.echo("Re-run with --accept to save it (or use the y/N prompt in noodle repl).")


@app.command()
def probe(
    url: str = typer.Argument(..., help="Page URL to probe (space/comma-separate several to probe them in one browser)"),
    json_out: bool = typer.Option(False, "--json", help="Compact author-evidence JSON (as probe_page)"),
    full: bool = typer.Option(False, "--full", help="With --json, the RAW uncapped payload"),
    timeout: int = typer.Option(15000, "--timeout", help="Per-page load timeout in ms"),
    click: list[str] = typer.Option(None, "--click", help="Control to click before a fresh snapshot (name or raw selector), repeatable — reveal controls, never mutating buttons"),
    do_: list[str] = typer.Option(None, "--do", help="Stateful transaction after --click reveals, in order: 'enter <v> in <field>', 'select <opt> from <dropdown>', 'click <name>'. REAL actions, deltas under revealed; {env:KEY} resolves; with --search runs on the landed page"),
    search: str = typer.Option(None, "--search", help="Run the site search with this term and summarize the RESULTS page: new controls, the 'NN results' element + POM entry, its count assertion"),
    suggest: str = typer.Option(None, "--suggest", help="Type this partial term per-character and capture the TYPEAHEAD rows + copy-ready steps"),
    pick: str = typer.Option(None, "--pick", help="With --search: bind 'any matching result' to ONE concrete caption (ambiguity refuses instead of guessing) and snapshot it; '*' = any"),
    follow: str = typer.Option(None, "--follow", help="With --suggest: click the captured suggestion row matching this text and summarize where it lands"),
    expect: list[str] = typer.Option(None, "--expect", help="Verify this text is on the landed page; repeatable, FOUND/NOT FOUND at the TOP"),
    compact: bool = typer.Option(False, "--compact", help="Readable summary of author-critical evidence only (POM-needing controls, POM YAML, headings)"),
    section: str = typer.Option("all", "--section", help="One slice only: controls | pom | steps | headings | revealed | all"),
    max_controls: int = typer.Option(None, "--max-controls", help="Cap each control list at N (compact caps at 25)"),
    open_native: bool = typer.Option(False, "--open-native", help="Enumerate native <select> options and click-open custom comboboxes too"),
    max_reveal_depth: int = typer.Option(1, "--max-reveal-depth", help="With --open-native, levels of combobox-in-combobox to follow"),
    discover: bool = typer.Option(False, "--discover", help="Trigger NAMES unknown? Clicks bounded disclosure candidates, deltas under revealed. Only for an unnamed control gating needed UI"),
    find: str = typer.Option(None, "--find", help="Only controls/result-items matching this text, pre-cap — replaces payload greps"),
):
    """Proactive DOM probe: every actionable control (visible AND hidden) with
    a ready selector, POM YAML for the ones that need it, a suggested step
    each, exact heading texts. Run it BEFORE authoring against an unfamiliar
    or SPA page. Full flag reference: noodle docs cli-reference
    """
    if section not in ("controls", "pom", "steps", "headings", "revealed", "all"):
        raise typer.BadParameter(
            f"Unsupported section '{section}'. Valid: controls, pom, steps, headings, revealed, all",
            param_hint="'--section'")
    from noodle.repl import core as _core
    result = _core.probe_page(url, timeout_ms=timeout,
                              click=list(click) if click else None,
                              do=list(do_) if do_ else None,
                              search=search, suggest=suggest,
                              pick=pick, follow=follow,
                              expect=list(expect) if expect else None,
                              open_native_controls=open_native,
                              max_reveal_depth=max_reveal_depth,
                              discover=discover)
    if find:
        # NOOD_0169 — one control out of a big page, pre-cap: the answer the
        # payload-spill grep round trips used to reconstruct by hand.
        from noodle.agents.web.probe import find_controls, render_find
        if json_out:
            _json_out({"find": find, "matches": find_controls(result, find)})
        else:
            typer.echo(render_find(result, find))
    elif json_out:
        # NOOD_0161 — JSON is the agent's door, so it defaults to the compact
        # author-evidence payload MCP probe_page already returns. Raw-by-default
        # cost a reviewed session a spilled temp file and a jq pass to re-derive
        # keys compact hands over whole. --full opts back into the dump.
        from noodle.agents.web.probe import compact_payload
        payload = result if full else compact_payload(result, max_controls or 40)
        _json_out(payload)
    else:
        from noodle.agents.web.probe import render
        typer.echo(render(result, compact=compact, section=section,
                          max_controls=max_controls))
    if not result["pages"]:
        raise typer.Exit(1)


@app.command("probe-app")
def probe_app(
    platform: str = typer.Argument(..., help="android | ios | windows | mac — picks the app from NOODLE_<PLATFORM>_APP; NOODLE_APPIUM_CAPS / NOODLE_APPIUM_URL are honoured exactly like a tagged run"),
    json_out: bool = typer.Option(False, "--json", help="Emit the probe payload as JSON — node list capped, visible first"),
    full: bool = typer.Option(False, "--full", help="With --json, every node instead of the capped list"),
):
    """NOOD_0136 — native-app probe: start the platform's Appium session,
    snapshot the accessibility tree ONCE, and dump every interactive node with
    its lookup strategy (accessibility id / resource-id / xpath), visibility,
    a suggested step, and paste-ready POM entries for nameless nodes.
    Snapshot-only: nothing is tapped. A tree with no accessible names returns
    coverage: visual_only and points at @ocr_fallback instead of fabricating
    selectors. Run this BEFORE authoring native steps."""
    from noodle.repl import core as _core
    result = _core.probe_app(platform.lower())
    if json_out:
        # NOOD_0162 — same door, same default as `probe --json`: compact unless
        # asked. A real native screen is hundreds of nodes.
        from noodle.agents.mobile.probe import compact_payload
        _json_out(result if full else compact_payload(result))
    else:
        from noodle.agents.mobile.probe import render as _render_app
        typer.echo(_render_app(result))
    if result.get("error"):
        raise typer.Exit(1)


@app.command()
def inspect(
    url: str = typer.Argument(..., help="Page URL to load (headless)"),
    text: str = typer.Argument(..., help="The locator phrase to resolve — same text a step would use"),
    json_out: bool = typer.Option(False, "--json", help="Emit the raw payload as JSON instead of the readable summary"),
    timeout: int = typer.Option(15000, "--timeout", help="Page load timeout in ms"),
    screenshot: str = typer.Option(None, "--screenshot", help="Also save a screenshot with the resolved element outlined red"),
):
    """NOOD_0115 — resolve a locator phrase against a live page with the exact
    machinery find() uses and show every candidate: source (text node / alt /
    aria-label / title / POM key / DOM scan), visibility, and which element
    find() actually picks (with any self-heal tier it needed). The one-command
    replacement for the throwaway Playwright scripts every locator mystery
    used to cost."""
    from noodle.repl import core as _core
    result = _core.inspect_locator(url, text, timeout_ms=timeout,
                                   screenshot=screenshot)
    if json_out:
        _json_out(result)
    else:
        from noodle.agents.web.inspect_locator import render
        typer.echo(render(result))
    if result["error"]:
        raise typer.Exit(1)


@app.command()
def repl(
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir holding noodle.yaml, noodle_tests/, .env"),
    llm: str = typer.Option(None, "--llm", help="claude | gemini | ollama — turn on free-form requests, run-failure repair, and compound-request planning for this session"),
    model: str = typer.Option(None, "--model", help="Override the default model string for --llm"),
):
    """Launch the interactive plain-English shell (NOOD_0056 — folded into
    `noodle` itself; no longer a separate `noodle-repl` binary). Rule-based
    keyword matching by default, no LLM required; --llm adds free-form
    requests. See docs/design-history.md Phase Y (NOOD_0056) for what this
    is (and isn't)."""
    from noodle.repl import repl as _repl
    _repl.run(workspace, llm, model)


@app.command()
def record(
    output: str = typer.Option(None, "--output", "-o", help="Path to write the generated .feature file (default: <workspace>/<tests_dir>/recorded.feature)"),
    name: str = typer.Option("Recorded Feature", "--name", "-n", help="Feature/scenario name"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir holding noodle.yaml, noodle_tests/, .env"),
):
    """Record a new test by performing actions in a browser."""
    from noodle.recorder.recorder import Recorder
    if output is None:
        output = str(Path(workspace) / config.load(workspace)["tests_dir"] / "recorded.feature")
    Recorder(output_path=output, feature_name=name).record()


# ---------------------------------------------------------------------------
# report subcommand group
# ---------------------------------------------------------------------------

report_app = typer.Typer(cls=_OrderedGroup, help="Manage Allure test reports")
app.add_typer(report_app, name="report")


@report_app.command("open")
def report_open(
    report_dir: str = typer.Argument(None, help="Path to the Allure report directory (default: the workspace's last-run reports)"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
):
    """Open the last Allure report in the browser."""
    from noodle.reporting.builder import open_report
    open_report(report_dir or str(_paths.last_run_root(workspace) / "reports" / "allure-report"))


@report_app.command("generate")
def report_generate(
    results_dir: str = typer.Argument(None, help="Path to allure-results/ (default: the workspace's last-run results)"),
    report_dir: str = typer.Option(None, "--out", "-o", help="Output directory (default: the workspace's last-run reports)"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
):
    """Re-generate BOTH reports (Allure HTML + RCA md/html) from existing results."""
    from noodle.reporting import rca_report as _rca_report
    from noodle.reporting.builder import generate
    results = results_dir or str(_paths.last_run_root(workspace) / "allure-results")
    report = report_dir or str(_paths.last_run_root(workspace) / "reports" / "allure-report")
    ok = generate(results, report)
    # NOOD_0082 — regenerate means both reports: RCA lands next to allure-report/
    # so one `report serve` hosts the pair. Needs no allure binary, so it's
    # written even when the Allure build was skipped.
    written = _rca_report.write_reports(results, str(Path(report).parent))
    typer.echo(f"RCA report written to {written['rca_html']}")
    # NOOD_0055 — exit 1 when no report was built (allure missing / generate
    # failed) so CI and the MCP run_and_report stop reporting phantom success.
    if not ok:
        raise typer.Exit(1)


def _resolve_serve_target(target: str | None, workspace: str) -> str:
    """NOOD_0082 — what `report serve` should host:

    - default (no arg): the last run's reports root (NOOD_0086 — <app>/report/
      reports for a single-app run, artifacts/reports otherwise) — the root holding
      allure-report/ AND rca.html, so one server hosts both. If either is
      missing but allure-results/ exist (fresh shell after a run), rebuild
      first so re-hosting always works.
    - archives/*.zip path, or a bare stamp like 20260713_101112 (resolved to
      <workspace>/archives/artifacts_<stamp>.zip): extract to a temp dir and
      serve that run's reports/ tree — `noodle report list` shows the stamps.
    - any other explicit dir: same staleness rebuild as the default case
      (NOOD_0091 — an explicit <app>/report path used to serve whatever
      allure-report/rca.html were already on disk, so a fresh run's results
      sat next to yesterday's HTML until someone ran `report generate` by hand).
    """
    if target:
        p = Path(target)
        if re.fullmatch(r"\d{8}_\d{6}", target):
            p = Path(workspace) / "archives" / f"artifacts_{target}.zip"
        if p.suffix == ".zip":
            if not p.is_file():
                raise typer.BadParameter(f"Archive not found: {p} — `noodle report list` shows what's available.")
            import zipfile
            out = Path(tempfile.mkdtemp(prefix="noodle_report_"))
            with zipfile.ZipFile(p) as z:
                z.extractall(out)
            typer.echo(f"  📦 Extracted {p.name} → {out}")
            reports = out / "reports"
            if reports.is_dir():
                return str(reports)
            typer.echo("  (archive has no reports/ tree — serving its root)")
            return str(out)
        from noodle.reporting.builder import ensure_fresh_reports
        if (p / "allure-results").is_dir() and (p / "reports").is_dir():
            # p is an artifacts root (e.g. <app>/report) holding both siblings.
            ensure_fresh_reports(str(p / "allure-results"), str(p / "reports"))
            return str(p / "reports")
        if (p.parent / "allure-results").is_dir():
            # p is itself the reports/ dir, allure-results a sibling of it.
            ensure_fresh_reports(str(p.parent / "allure-results"), str(p))
        return str(p)
    root = _paths.last_run_root(workspace) / "reports"
    results = _paths.last_run_root(workspace) / "allure-results"
    # NOOD_0089 — rebuild missing OR stale (older than the newest result
    # JSON): serving a leftover rca.html beside a newer allure-report showed
    # failures from a different run as if they were this one's.
    from noodle.reporting.builder import ensure_fresh_reports
    ensure_fresh_reports(str(results), str(root))
    return str(root)


# NOOD_0089 — cross-process registry of `report serve` servers, so
# `noodle report stop` can kill a server started in another terminal (or left
# behind by an agent). {port: pid}, workspace-local like the last-run pointer.
_REPORT_PIDFILE = Path(".noodle") / "report_servers.json"


def _report_pids(workspace: str) -> dict:
    try:
        return json.loads((Path(workspace) / _REPORT_PIDFILE).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_report_pids(workspace: str, data: dict) -> None:
    f = Path(workspace) / _REPORT_PIDFILE
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data) + "\n")
    except OSError:
        pass  # registry is a nicety — never fail serving over it


def _pid_of(entry) -> int:
    """A registry value was a bare pid until NOOD_0161 gave it the served root
    and host (so a serve can be REUSED, not duplicated). Old pidfiles from a
    previous install are still on disk — read both shapes."""
    return entry["pid"] if isinstance(entry, dict) else entry


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # someone else's process — alive, just not ours
    except OSError:
        return False
    return True


def _live_report_server(workspace: str, target: str, host: str):
    """NOOD_0161 — the port already serving this exact report root on this
    host, if its process is still up. Reports are rebuilt IN PLACE every run,
    so that server is already serving the new run's HTML: reusing it keeps the
    URL the user has open valid instead of minting a new port per run."""
    want = str(Path(target).resolve())
    for prt, entry in _report_pids(workspace).items():
        if not isinstance(entry, dict):
            continue        # pre-NOOD_0161 entry — no root recorded, can't match
        if entry.get("root") == want and entry.get("host") == host \
                and _pid_alive(entry["pid"]):
            return int(prt)
    return None


def _looks_like_report_dir(d: Path) -> bool:
    """A dir an agent would host reports from: the reports root (holds
    rca.html and/or allure-report/) or the Allure report itself."""
    return (d / "rca.html").is_file() or (d / "allure-report").is_dir() \
        or d.name == "allure-report"


def _adhoc_report_servers() -> dict:
    """NOOD_0095 — agents sometimes host reports with a raw
    `python -m http.server 8000` instead of `noodle report serve`, so the
    pidfile registry never hears about them and `noodle report stop` said
    "nothing to stop" while the report stayed up. Find listening processes
    that are serving a report tree — cwd looks like one, or an http.server
    `--directory`/trailing-path arg points at one. Returns {port: pid}.
    Best-effort: no lsof (Windows) → {}."""
    def _lsof(*args) -> str:
        try:
            return subprocess.run(["lsof", *args], capture_output=True,
                                  text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    by_port, pid = {}, None
    for line in _lsof("-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn").splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid and pid != os.getpid():
            prt = line.rsplit(":", 1)[-1]
            if prt.isdigit():
                by_port[prt] = pid
    if not by_port:
        return {}
    cwd_of, cur = {}, None
    pids = ",".join(sorted({str(p) for p in by_port.values()}))
    for line in _lsof("-a", "-p", pids, "-d", "cwd", "-Fpn").splitlines():
        if line.startswith("p"):
            cur = int(line[1:])
        elif line.startswith("n") and cur:
            cwd_of[cur] = line[1:]

    def _served_dirs(p: int):
        if p in cwd_of:
            yield Path(cwd_of[p])
        # NOOD_0101 — -ww: with COLUMNS set (pytest sets it; so do some CI
        # shells), ps truncates piped output to that width, cutting off the
        # --directory path this scan exists to find.
        args = subprocess.run(["ps", "-ww", "-p", str(p), "-o", "command="],
                              capture_output=True, text=True).stdout.split()
        if "http.server" in " ".join(args):
            for i, a in enumerate(args):
                if a in ("--directory", "-d") and i + 1 < len(args):
                    yield Path(args[i + 1])

    return {prt: p for prt, p in by_port.items()
            if any(_looks_like_report_dir(d) for d in _served_dirs(p))}


@report_app.command("stop")
def report_stop(
    port: int = typer.Option(None, "--port", "-p", help="Only stop the server on this port (default: all)"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
):
    """Stop hosted report servers (Allure + RCA) — ones started by `noodle
    report serve` from any terminal (via the workspace's
    .noodle/report_servers.json registry), and ad-hoc ones an agent started
    with a raw `python -m http.server` on a report dir. Registry entries
    whose process is already gone are pruned silently."""
    import signal
    data = _report_pids(workspace)
    adhoc = {prt: pid for prt, pid in _adhoc_report_servers().items()
             if prt not in data}
    if not data and not adhoc:
        typer.echo("No hosted report servers found — nothing to stop.")
        return
    remaining = {}
    for prt, entry in data.items():
        pid = _pid_of(entry)
        if port is not None and str(port) != prt:
            remaining[prt] = entry
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            typer.echo(f"  🛑 Stopped report server on port {prt} (pid {pid})")
        except (ProcessLookupError, PermissionError, OSError):
            typer.echo(f"  (port {prt}: pid {pid} already gone — pruned)")
    _write_report_pids(workspace, remaining)
    for prt, pid in adhoc.items():
        if port is not None and str(port) != prt:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            typer.echo(f"  🛑 Stopped ad-hoc report server on port {prt} (pid {pid})")
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _urls_http_ok(urls: list[str]) -> bool:
    """NOOD_0166 — prove every served URL answers 200 BEFORE handing it out,
    so the payload's `http_ok: true` replaces the curl lap agents ran on each
    URL. Localhost HEADs against our own no-store server: sub-second."""
    import urllib.request
    for url in urls:
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status != 200:
                    return False
        except Exception:
            return False
    return True


def _spawn_report_server(target: str, workspace: str, host: str, port: int) -> dict:
    """NOOD_0104 — spawn the blocking server as a detached child, wait for its
    bind, and return {"ok": True, "port", "pid", "urls"} (or {"ok": False,
    "error"}). The bind signal is the NOOD_0089 pidfile: the child registers
    {port: pid} only after a successful bind, so an entry with the child's pid
    means the URLs are live (and `-p 0` reports the real port). The URLs
    outlive the calling command — NOOD_0134: `run --serve` used an in-process
    daemon thread, so its URLs died the moment `noodle run` exited.

    NOOD_0161 — and a live server for this root is REUSED, not duplicated: the
    URL handed to a user stopped changing on every run. An EXPLICIT port is a
    request, not a preference, so it always gets its own server; reuse applies
    to `port=0` — every agent path, and the human's `--background` default."""
    import time

    from noodle.reporting.builder import report_urls
    if port == 0 and (live := _live_report_server(workspace, target, host)) is not None:
        urls = report_urls(target, host, live)
        # NOOD_0166 — a registry entry can outlive a usable server (recycled
        # pid, wedged socket): reuse only URLs that ANSWER, else fall through
        # to a fresh spawn instead of handing out dead links.
        if _urls_http_ok(urls):
            return {"ok": True, "reused": True, "report_dir": target,
                    "host": host, "port": live,
                    "pid": _pid_of(_report_pids(workspace)[str(live)]),
                    "urls": urls, "http_ok": True}
    log = Path(workspace) / ".noodle" / "report_server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "noodle.cli", "report", "serve", target,
           "--workspace", workspace, "--host", host, "--port", str(port)]
    detach = {"start_new_session": True} if os.name == "posix" else \
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    # Snapshot the registry first: a dead server's stale entry could carry a
    # recycled pid equal to the child's — only a NEW entry proves the bind.
    before = {(prt, _pid_of(e)) for prt, e in _report_pids(workspace).items()}
    with log.open("ab") as lf:
        child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf, **detach)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        bound = next((prt for prt, entry in _report_pids(workspace).items()
                      if _pid_of(entry) == child.pid
                      and (prt, child.pid) not in before), None)
        if bound is not None:
            urls = report_urls(target, host, int(bound))
            return {"ok": True, "report_dir": target, "host": host,
                    "port": int(bound), "pid": child.pid, "urls": urls,
                    "http_ok": _urls_http_ok(urls)}
        if child.poll() is not None:
            tail = ""
            try:
                tail = log.read_text(errors="replace")[-2000:].rstrip()
            except OSError:
                pass
            return {"ok": False, "error": f"report server exited with code "
                                          f"{child.returncode} — {log} says:\n{tail}"}
        time.sleep(0.1)
    return {"ok": False,
            "error": f"report server (pid {child.pid}) didn't bind within 30s — check {log}."}


def _serve_detached(target: str, workspace: str, host: str, port: int) -> None:
    """`report serve --background` — spawn, print the URLs, exit non-zero on failure."""
    served = _spawn_report_server(target, workspace, host, port)
    if not served["ok"]:
        typer.echo(served["error"])
        raise typer.Exit(1)
    how = "Already serving" if served.get("reused") else "Serving"
    typer.echo(f"{how} {target} at http://{host}:{served['port']}  "
               f"(pid {served['pid']} — `noodle report stop` to stop)")
    for url in served["urls"]:
        typer.echo(f"  → {url}")


@report_app.command("serve")
def report_serve(
    report_dir: str = typer.Argument(None, help="Reports root or Allure report dir, an archives/*.zip, or a bare archive stamp like 20260713_101112 (default: the workspace's last-run reports root — hosts the Allure report AND rca.html together)"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (default localhost-only). Pass --host 0.0.0.0 to share with teammates on the same network — the report's failure screenshots/traces can contain typed credentials, so only do this on a trusted network"),
    port: int = typer.Option(None, "--port", "-p", help="Port to serve on (default: 8000 foreground, falling back to an OS-assigned port if taken / OS-assigned for --background — agents never hit a port-conflict retry)"),
    background: bool = typer.Option(False, "--background", "-b", help="Start the server detached, print the URLs, and return immediately (for agents/scripts) — stop it later with `noodle report stop`"),
):
    """Serve the last run's reports (Allure + RCA) on localhost, re-host an
    older archived run, or share with a teammate via --host 0.0.0.0.
    Stop it with Ctrl+C, or `noodle report stop` from any other terminal."""
    from noodle.reporting.builder import serve_report
    # NOOD_0126 — background is the agent path; default it to an OS-assigned
    # port (0) so a stale 8000 never forces a retry. Foreground stays 8000
    # (bookmarkable for a human watching).
    if port is None:
        port = 0 if background else 8000
    target = _resolve_serve_target(report_dir, workspace)
    if background:
        _serve_detached(target, workspace, host, port)
        return
    # NOOD_0089 — register for `noodle report stop` only once the bind
    # succeeds (on_bound carries the actual port, so -p 0 registers right),
    # and unregister only OUR OWN entry: a serve that lost the port race must
    # not pop the pid of the server that's actually holding it.
    bound = {}

    def _on_bound(actual_port: int):
        bound["port"] = str(actual_port)
        # NOOD_0161 — record WHAT is being served, not just by whom: that's
        # what lets the next run reuse this server instead of opening a
        # second one on a new port and handing the user a new URL.
        _write_report_pids(workspace, {
            **_report_pids(workspace),
            bound["port"]: {"pid": os.getpid(), "host": host,
                            "root": str(Path(target).resolve())}})

    try:
        try:
            serve_report(target, host, port, on_bound=_on_bound)
        except OSError as e:
            # NOOD_0134 — a taken port must never dead-end the serve (8000 is
            # only a bookmarkable first try): fall back to an OS-assigned one.
            if e.errno != errno.EADDRINUSE or port == 0:
                typer.echo(f"Can't bind {host}:{port} ({e.strerror or e}) — try another --port, or -p 0 for an OS-assigned one.")
                raise typer.Exit(1)
            typer.echo(f"Port {port} is taken — using an OS-assigned one instead.")
            serve_report(target, host, 0, on_bound=_on_bound)
    finally:
        pids = _report_pids(workspace)
        if "port" in bound and bound["port"] in pids \
                and _pid_of(pids[bound["port"]]) == os.getpid():
            del pids[bound["port"]]
            _write_report_pids(workspace, pids)


@report_app.command("list")
def report_list(
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable list for agents"),
):
    """List what `report serve` can host: the live report and archived runs."""
    from noodle.repl import core
    data = core.list_reports(workspace)
    if as_json:
        _json_out(data)
        return
    live = data["live"]
    if live:
        parts = [p for p, on in (("allure", live["allure"]), ("rca", live["rca"])) if on] or ["empty"]
        typer.echo(f"live     {live['path']}  [{', '.join(parts)}]"
                   + (f"  generated {live['generated_at']}" if live["generated_at"] else ""))
    else:
        typer.echo("live     (none — run a test or `noodle report generate`)")
    for a in data["archives"]:
        typer.echo(f"archive  {a['stamp']}  {a['size_mb']} MB  →  noodle report serve {a['stamp']}")
    if not data["archives"]:
        typer.echo("archive  (none — runs overwrite in place; `noodle archive` stashes a run on demand)")


# ---------------------------------------------------------------------------
# NOOD_0023 — one artifacts/ root for everything a run produces (allure
# results/report, junit, RCA, healing, screenshots, traces, videos, network,
# logs) — Java's `target/` equivalent. clean/archive/artifacts operate on the
# whole tree instead of each category having its own housekeeping command.
# ---------------------------------------------------------------------------

@app.command()
def clean(
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    purge_history: bool = typer.Option(
        False, "--purge-history",
        help="Also delete the Allure trend history (default: preserved across clean)"),
):
    """Delete the artifacts/ tree — everything a run regenerates.

    NOOD_0025/NOOD_0039: the Allure trend history (reports/allure-history/,
    Allure 3's JSONL history file) is kept across the wipe by default —
    `allure generate` folds it into the next report's trend widgets, and
    `noodle archive` alone doesn't achieve this (it zips a snapshot; nothing
    ever unzips it back before the next run reads the live tree).
    --purge-history for a true full wipe.
    """
    root = _paths.last_run_root(workspace)
    if not root.is_dir():
        typer.echo(f"Nothing to clean — {root} doesn't exist.")
        return
    history = root / "reports" / "allure-history"
    saved_history = None
    if not purge_history and history.is_dir():
        saved_history = Path(tempfile.mkdtemp()) / "history"
        shutil.move(str(history), str(saved_history))
    shutil.rmtree(root)
    if saved_history is not None:
        history.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(saved_history), str(history))
        os.rmdir(saved_history.parent)
        typer.echo(f"Removed {root} (kept Allure trend history — --purge-history to wipe it too)")
    else:
        typer.echo(f"Removed {root}")


@app.command()
def archive(
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    out: str = typer.Option("archives", "--out", "-o", help="Directory to write the zip into"),
):
    """Zip the artifacts/ tree with a timestamp, for stashing a run's reports
    before the next `noodle run` overwrites them."""
    root = _paths.last_run_root(workspace)
    if not root.is_dir():
        typer.echo(f"Nothing to archive — {root} doesn't exist.")
        raise typer.Exit(code=1)
    out_dir = Path(workspace) / out
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = shutil.make_archive(str(out_dir / f"artifacts_{stamp}"), "zip", root_dir=root)
    typer.echo(f"Archived {root} -> {archive_path}")


@app.command()
def artifacts(
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
):
    """List what the artifacts/ tree holds, by category — so an agent (or you)
    can see what a run produced without knowing each report's path."""
    root = _paths.last_run_root(workspace)
    if not root.is_dir():
        typer.echo(f"No artifacts yet — {root} doesn't exist. Run `noodle run` first.")
        return
    for category in sorted(root.iterdir()):
        files = [f for f in category.rglob("*") if f.is_file()] if category.is_dir() else [category]
        size_kb = sum(f.stat().st_size for f in files) / 1024
        typer.echo(f"{category.relative_to(root)}/  "
                   f"({len(files)} file{'s' if len(files) != 1 else ''}, {size_kb:.1f} KB)")


# NOOD_0147 — session diagnostics: the CLI face of noodle/diagnostics.py.
# `log` is what a driving agent calls at session end when a failure trigger
# fired (trigger table: AGENTS.md / docs/session-diagnostics.md); `bundle`
# is what a tester runs to send the folder back in one file.
diagnostic_app = typer.Typer(cls=_OrderedGroup,
                             help="Session diagnostics — agent failure self-reports (see docs/session-diagnostics.md)")
app.add_typer(diagnostic_app, name="diagnostic")


@diagnostic_app.command("log")
def diagnostic_log(
    app_name: str = typer.Argument(..., help="App-under-test the session was developing/running"),
    trigger: list[str] = typer.Option(..., "--trigger", "-t",
                                      help="Fired trigger(s): hard-fail | first-attempt-fail | slow-dev | over-budget | manual (repeatable)"),
    summary: str = typer.Option(..., "--summary", "-s", help="One short paragraph: what went wrong"),
    timeline: str = typer.Option(None, "--timeline", help="Steps taken this session, in order"),
    cause: str = typer.Option(None, "--cause", help="Suspected root cause"),
    fixes: str = typer.Option(None, "--fixes", help="Fixes tried and their outcomes"),
    duration_min: float = typer.Option(None, "--duration-min", help="Dev wall-clock, minutes"),
    attempts: int = typer.Option(None, "--attempts", help="Fix+rerun attempts spent"),
    agent: str = typer.Option(None, "--agent", help="Driving agent/model (e.g. 'codex 5.3')"),
    agent_cost: str = typer.Option(None, "--agent-cost", help="The agent's OWN session spend (e.g. '23 AIC')"),
    session: str = typer.Option(None, "--session", help="Stable session id — a repeat log call updates the same file"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
):
    """Write this session's failure self-report into the workspace's
    gitignored diagnostics/ folder. Engine facts (last-run result, compact
    RCA verdict, llm_cost, version) are appended automatically — supply only
    what lives in the agent's session memory. Capped + deduped: a repeat
    call for the same session/app updates the existing file, and the folder
    keeps at most NOODLE_DIAG_MAX (default 25) reports."""
    from noodle import diagnostics as _diag
    try:
        result = _diag.write_diagnostic(
            workspace, app=app_name, triggers=trigger, summary=summary,
            timeline=timeline, suspected_cause=cause, fixes_tried=fixes,
            duration_min=duration_min, attempts=attempts, agent=agent,
            agent_cost=agent_cost, session=session)
    except ValueError as e:
        typer.echo(f"noodle: {e}", err=True)
        raise typer.Exit(code=1)
    verb = "Updated" if result["updated"] else "Wrote"
    typer.echo(f"{verb} {result['path']} ({result['count']} diagnostic(s) on disk)")
    for name in result["rotated_out"]:
        typer.echo(f"  rotated out (NOODLE_DIAG_MAX): {name}")


@diagnostic_app.command("list")
def diagnostic_list(
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
):
    """List the diagnostics on disk, newest first — file, app, triggers, when."""
    from noodle import diagnostics as _diag
    entries = _diag.list_diagnostics(workspace)
    if not entries:
        typer.echo(f"No diagnostics in {_diag.diag_dir(workspace)} — nothing has triggered one.")
        return
    for e in entries:
        typer.echo(f"{e['file']}  app={e.get('app')}  triggers={','.join(e.get('triggers') or [])}  at={e.get('at')}")


@diagnostic_app.command("guide")
def diagnostic_guide():
    """Print the full session-diagnostics contract (triggers, fields, caps,
    tuning) — the CLI's own copy of the doc, for MCP-blocked environments
    where read_docs isn't reachable. Bundled into installed distributions."""
    from noodle import diagnostics as _diag
    text = _diag.guide_text()
    if not text:
        typer.echo("session-diagnostics guide not found in the installed "
                   "Noodle package. Reinstall Noodle or use a source checkout.")
        raise typer.Exit(code=1)
    typer.echo(text)


@diagnostic_app.command("bundle")
def diagnostic_bundle(
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
):
    """Zip every diagnostic into one file to send back to the Noodle team.
    Secrets never enter diagnostics (values are scrubbed at write time), so
    the bundle is safe to share."""
    from noodle import diagnostics as _diag
    result = _diag.bundle(workspace)
    if "error" in result:
        typer.echo(result["error"])
        raise typer.Exit(code=1)
    typer.echo(f"Bundled {result['count']} diagnostic(s) -> {result['path']}")


# NOOD_0161 — `noodle --help` rendered every command's FULL docstring into the
# command list: 14.7 KB, and the reviewed session pulled 36 KB of help scanning
# for flags. NOOD_0156 fixed exactly this for `update` with an explicit
# short_help; this generalizes it. Typer's rich formatter prefers short_help,
# so derive one per command — the full docstring still shows in
# `noodle <cmd> --help`, which is where the rationale belongs.
def _short_help(doc: str) -> str:
    text = re.sub(r"^NOOD_\d+\s+[—-]\s*", "", " ".join(doc.split()))
    first = text.split(". ")[0].rstrip(".")
    return first if len(first) <= 110 else first[:107].rsplit(" ", 1)[0] + "…"


for _cmd in app.registered_commands:
    if not _cmd.short_help and _cmd.callback and _cmd.callback.__doc__:
        _cmd.short_help = _short_help(_cmd.callback.__doc__)


if __name__ == "__main__":
    app()
