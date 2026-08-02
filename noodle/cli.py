import errno
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import typer
from typer.core import TyperGroup

from noodle import config, log, payload_budget
from noodle.reporting import paths as _paths


def _log_run_end(results_root: str, rc: int, t0: float, data: dict | None = None) -> None:
    """NOOD_0173 — the one authoritative run.end (json mode only): counts,
    exit code, wall time, LLM spend, and model/engine provenance the behave
    child can't see. Emitted once from the CLI (not per behave worker), so a
    parallel run gets exactly one run.end. `data` reuses the CLI's own scan."""
    if not (log._json_mode() or log.progress_mode()):   # NOOD_0202 — CI too
        return
    from noodle import install_check
    from noodle.llm import cost as _cost
    from noodle.reporting import summary as _summary
    s = data if data is not None else _summary.collect(results_root)
    usd = (_cost.load_total(results_root) or {}).get("usd")
    vr = install_check.version_report()
    # NOOD_0202 — the build log's verdict line (maven's BUILD SUCCESS + Total
    # time), so a CI console reading one stream gets the outcome on it.
    _secs = time.monotonic() - t0
    log.event("run.end", f"Run finished — {s.get('passed')} passed, "
                         f"{s.get('failed')} failed, exit {rc} ({_secs:.1f}s)",
              level=logging.ERROR if rc else logging.INFO,
              duration_ms=int((time.monotonic() - t0) * 1000),
              passed=s.get("passed"), failed=s.get("failed"),
              verified=s.get("verified"), exit_code=rc, llm_usd=usd,
              model=os.getenv("NOODLE_MODEL"),
              llm_mode=os.getenv("NOODLE_LLM_MODE", "auto"),
              engine_version=vr.get("source") or vr.get("installed"),
              git_sha=install_check.git_sha())


class _OrderedGroup(TyperGroup):
    """List commands alphabetically in --help (Typer's default is definition
    order, which buried validate/inspect/probe/rca-report in a hard-to-scan
    pile). ponytail: one override, no plugin."""
    def list_commands(self, ctx):
        return sorted(super().list_commands(ctx))


# NOOD_0195 — the console speaks UTF-8, whatever the OS default is. Windows
# gives stdout the ANSI code page (cp1252), which has no ✅ ⚠️ ❌ 📸 — and the
# engine prints those on every run. A single emoji in a message therefore raised
# UnicodeEncodeError from typer.echo and took the whole command down; worse, it
# did so while REPORTING another error, so the real failure was never seen.
# errors="replace" keeps a genuinely legacy console degrading to '?' rather than
# crashing. Cheaper and more honest than stripping symbols from every message.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):   # redirected to a non-text stream
        pass

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

_VALID_BROWSERS = config.VALID_BROWSERS   # NOOD_0179 — one table, in noodle.config

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


def _arg_text(value: str) -> tuple[str, bool]:
    """NOOD_0198 — a text argument that may name where the text lives:
    `-` reads stdin, an existing file reads that file, anything else IS the
    text. Returns (text, indirect) so a caller can still tell a typo'd path
    from an inline document.

    The gap this closes is a shell one. A generator upstream (an AI-SDLC
    orchestrator writing a handoff file, a CI job templating a story) has the
    prompt in a file, and the only way in was `--prompt "$(cat story.md)"` —
    which command-substitutes every backtick in the file. Machine-written
    markdown is mostly code fences, so that is both a corrupted prompt and a
    path from the generator's output into the caller's shell.

    ponytail: existing-file-wins, no scheme prefix. The ceiling is a prompt
    whose literal text is also a real filename in cwd; `--prompt "$(cat -)"`
    style stdin is the escape hatch, and a numbered-step prompt never
    collides in practice."""
    if value == "-":
        return sys.stdin.read(), True
    try:
        p = Path(value)
        if p.is_file():
            return p.read_text(encoding="utf-8"), True
    except OSError:                # inline docs can exceed PATH_MAX
        pass
    return value, False


def _write_full_payload(payload) -> str | None:
    """The untrimmed payload on disk, or None if cwd isn't writable."""
    try:
        path = Path(".noodle") / "last_payload.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return str(path.resolve())
    except OSError:
        return None


def _serve_default() -> bool:
    """NOOD_0200 — the reports are the deliverable of EVERY run, and the CLI
    is the primary door when MCP is blocked: serve them by default so the
    second and third test of a sequential authoring session still hand the
    user a clickable URL. CI has no human to click one (and would leak a
    detached server per job) — auto-off there; NOODLE_SERVE_REPORTS is the
    explicit switch either way."""
    env = os.getenv("NOODLE_SERVE_REPORTS", "").strip().lower()
    if env:
        return env not in ("0", "false", "no")
    return not (os.getenv("CI") or os.getenv("TF_BUILD"))


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
    parallel: int = typer.Option(None, "--parallel", help="N feature files at once (behavex); -1 = per CPU"),
    sequential: bool = typer.Option(False, "--sequential", help="Force one process"),
    parallel_scheme: str = typer.Option("feature", "--parallel-scheme", help="'feature' (a file's scenarios stay serial) or 'scenario'"),
    name: str = typer.Option(None, "--name", "-n", help="Only scenarios whose name contains this"),
    failed: bool = typer.Option(False, "--failed", help="Re-run last run's failures"),
    shard: str = typer.Option(None, "--shard", help="i/N — deterministic feature-file slice, for splitting across machines"),
    run_timeout: int = typer.Option(None, "--timeout", help="Kill the run after N seconds (exit 124, partial results kept)"),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop at the first failure"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Live behave stream to <artifacts>/run.log, stdout gets the summary (automatic off a TTY)"),
    preflight: bool = typer.Option(None, "--preflight/--no-preflight", help="Resolve every {env:KEY} before the browser; missing aborts (exit 2). Default on"),
    serve: bool = typer.Option(None, "--serve/--no-serve", help="Host the Allure + RCA reports after the run, print URLs. Default on (off in CI)"),
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
    # NOOD_0202 — `--json` is by construction a machine-facing caller (an agent
    # tool call, a wrapper parsing stdout), and it is the one that pays per byte.
    # Never stream the CI progress log into its captured pipe — not even inside
    # a pipeline, where CI=true would otherwise turn it on. Explicit wins.
    if json_out and not os.getenv("NOODLE_LOG_PROGRESS"):
        os.environ["NOODLE_LOG_PROGRESS"] = "0"
    if serve is None:
        serve = _serve_default()
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
    # NOOD_0183 — an explicit --sequential beats both, so a workspace that sets
    # NOODLE_PARALLEL_PROCESSES in .env can still be debugged one scenario at a
    # time without editing config. -1 means "one worker per core".
    if sequential:
        if parallel > 0:
            typer.echo("  ↩️  --sequential — running one process (parallelism off)")
        parallel = 0
    elif parallel < 0:
        parallel = os.cpu_count() or 1
    # NOOD_0187 — Windows ProcessPoolExecutor hard-caps workers at 61, and
    # behavex raises ValueError past it; -1 on a big box hit exactly that.
    if os.name == "nt" and parallel > 60:
        if not json_out:
            typer.echo(f"  ⚠ --parallel {parallel} capped to 60 (Windows ProcessPoolExecutor limit)")
        parallel = 60
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

    # NOOD_0171 — correlation id for the whole run. Reuse an inherited one (an
    # MCP tool call that triggered this run already set it, so the run stitches
    # back to that call); otherwise mint one. The behave child adopts it in
    # hooks.before_all.
    env.setdefault("NOODLE_RUN_ID", os.urandom(8).hex())

    # Bug 1: always write a canonical "true"/"false" — never pass raw env through
    if headed:
        env["NOODLE_HEADLESS"] = "false"
    elif headless:
        env["NOODLE_HEADLESS"] = "true"
    else:
        default = "true" if cfg["headless"] else "false"
        env["NOODLE_HEADLESS"] = _normalize_headless(env.get("NOODLE_HEADLESS", default))

    env["NOODLE_BROWSER"] = browser

    # NOOD_0173 — run.start telemetry (json mode only). Bind the run_id so the
    # CLI's own run.start/run.end correlate with the child's scenario/step events.
    _run_t0 = time.monotonic()
    log.bind(run_id=env["NOODLE_RUN_ID"])
    # NOOD_0202 — in progress mode the whole build log rides stderr, so a CI
    # console reads ONE ordered stream. typer.echo (summary, report URLs) keeps
    # stdout, which is what a pipeline step parses or an agent captures.
    if log.progress_mode():
        log.route_console_to_build_log()
    log.telemetry("run.start", f"Run started — {path}", target=path, tags=tag,
                  browser=browser, headless=env["NOODLE_HEADLESS"] == "true",
                  parallel=parallel)
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

    # NOOD_0183 — --failed re-runs last run's red scenarios by name, the
    # dev-loop lap that matters on a 1000-scenario suite. It composes with
    # --name (both become behave --name filters, which OR together).
    names = [name] if name else []
    if failed:
        last_failed = _failed_scenario_names(cwd)
        if not last_failed:
            typer.echo("  ✅ No failed scenarios recorded in the last run — nothing to re-run.")
            raise typer.Exit(0)
        typer.echo(f"  🔁 Re-running {len(last_failed)} failed scenario(s) from the last run")
        # NOOD_0187 — behave matches --name as a REGEX: a scenario named
        # "Add item (2 of 3)" or "Price is $5.00" re-ran the wrong set or
        # aborted with re.error. Escape — these are literal names.
        names += [re.escape(n) for n in last_failed]

    # NOOD_0187 — wall-clock budget for the whole run. Without one, a wedged
    # browser held the CI job to ITS timeout and lost every artifact with it.
    if run_timeout is None:
        run_timeout = int(os.getenv("NOODLE_RUN_TIMEOUT", "0") or "0")

    # NOOD_0187 — --shard i/N: deterministic feature-file slices so a suite
    # splits across machines with no scheduler (ADO/GHA matrix legs pass their
    # own index). Sorted paths → every host partitions identically.
    shard_include = None
    if shard:
        slice_rels = _shard_slice(cwd, path, shard)
        if not slice_rels:
            if json_out:
                _json_out({"ok": True, "passed": 0, "failed": 0, "skipped": 0,
                           "notes": [f"shard {shard}: 0 feature files in this slice"]})
            else:
                typer.echo(f"  🧩 shard {shard}: 0 feature files in this slice — nothing to run.")
            raise typer.Exit(0)
        if not json_out:
            typer.echo(f"  🧩 shard {shard}: {len(slice_rels)} feature file(s)")
        shard_include = "(" + "|".join(re.escape(p) + "$" for p in slice_rels) + ")"

    # NOOD_0187 — --quiet works identically in both modes now: the parallel
    # early-return used to skip the whole reporting tail (--json printed
    # NOTHING on a parallel run, behavex streamed 1000s of scenarios into CI
    # logs). One log path, one tail.
    log_path = None
    if quiet:
        log_path = Path(cwd) / _paths.artifacts_root() / "run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

    # Parallel: behavex runs N behave workers, each writing to its own results
    # subdir (set in hooks.before_all). We clean once, run, flatten, report.
    if parallel > 0:
        if parallel_scheme not in ("feature", "scenario"):
            raise typer.BadParameter(
                f"Unsupported scheme '{parallel_scheme}'. Valid options: feature, scenario",
                param_hint="'--parallel-scheme'",
            )
        # NOOD_0183 — two guards, both warnings not errors: the run is still
        # valid, it just won't behave the way the author probably expects.
        if parallel_scheme == "scenario":
            typer.echo(
                "  ⚠ --parallel-scheme scenario splits ONE feature file across workers.\n"
                "    Scenarios sharing a login, a seeded record or an ordered setup will\n"
                "    collide. The default 'feature' scheme keeps a file's scenarios serial.")
        if env.get("NOODLE_HEADLESS") != "true":
            typer.echo(f"  ⚠ --parallel {parallel} with a headed browser opens {parallel} "
                       "windows fighting for focus — add --headless.")
        rc, timed_out = _run_parallel(
            path, parallel, tag, env, cwd, parallel_scheme, names,
            shard_include=shard_include, timeout=run_timeout,
            fail_fast=fail_fast, log_path=log_path)
    else:
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
        elif shard_include:
            args = [*_BEHAVE_CMD, path, "--include", shard_include, "--no-capture"]
        else:
            args = [*_BEHAVE_CMD, path, "--no-capture"]

        if tag:
            args += ["--tags", tag]
        for n in names:
            args += ["--name", n]
        if fail_fast:
            args += ["--stop"]

        # NOOD_0116 --quiet: the biggest context-cost of an agent-driven run is
        # the full behave console stream staying resident per LLM call. Divert it
        # to <artifacts>/run.log and print only the summary below.
        rc, timed_out = _run_behave(args, env, cwd, log_path=log_path,
                                    timeout=run_timeout)

    results_root = str(Path(cwd) / _paths.artifacts_root() / "allure-results")
    from noodle.reporting import summary as _summary
    data = _summary.collect(results_root)

    if timed_out and not json_out:
        typer.echo(f"  ⏱ run killed after {run_timeout}s (--timeout) — "
                   "reporting the partial results below")

    # A sharded matrix leg legitimately can filter to zero scenarios, so
    # --shard (and NOODLE_ALLOW_EMPTY=1) downgrade exit 3 to a warning.
    allow_empty = bool(shard) or os.getenv(
        "NOODLE_ALLOW_EMPTY", "").strip().lower() in ("1", "true", "yes", "on")
    rc, rc_notes = _derive_exit_code(data, rc, timed_out, allow_empty)
    if rc_notes:
        # --json keeps its one-object stdout contract (NOOD_0131): the notes
        # ride the payload instead of corrupting the stream.
        data["notes"] = rc_notes
        if not json_out:
            for note in rc_notes:
                typer.echo(f"  {note}")

    # @quarantine is non-blocking: if every failed scenario this run is tagged
    # @quarantine, don't fail the build — they still ran and report as failed.
    # NOOD_0187 — unless NOODLE_STRICT_QUARANTINE says otherwise, and the
    # override is recorded in last_run.json instead of vanishing into stdout.
    if rc != 0 and _all_failures_quarantined(results_root) is True:
        if os.getenv("NOODLE_STRICT_QUARANTINE", "").strip().lower() in ("1", "true", "yes", "on"):
            if not json_out:
                typer.echo("  🔶 Only @quarantine scenarios failed — NOODLE_STRICT_QUARANTINE keeps the build red.")
        else:
            if not json_out:
                typer.echo("\n  🔶 Only @quarantine scenarios failed — not failing the build.")
            data["quarantine_overrode_exit"] = True
            rc = 0

    if timed_out:
        data["timed_out"] = True
        if parallel == 0:
            # The killed behave child never reached after_all — build the
            # reports here off the partial per-scenario results on disk.
            _summary.mark_flaky(results_root)
            from noodle.reporting import rca_report as _rca_report
            from noodle.reporting.builder import generate as _generate
            reports_root = Path(cwd) / _paths.artifacts_root() / "reports"
            _generate(results_root, str(reports_root / "allure-report"))
            _rca_report.write_reports(results_root, str(reports_root))

    data = _write_last_run(results_root, rc, cwd, data)
    _log_run_end(results_root, rc, _run_t0, data)   # NOOD_0173 — one run.end (json mode)
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
        # NOOD_0187 — only advertise files that exist: a missing allure binary
        # (or a killed run) used to hand agents an index.html path to nowhere —
        # the phantom-success class NOOD_0055 fixed for `report generate`.
        report_idx = reports / "allure-report" / "index.html"
        rca_file = reports / "rca.html"
        payload["report"] = str(report_idx) if report_idx.is_file() else None
        payload["rca_html"] = str(rca_file) if rca_file.is_file() else None
        # NOOD_0202 — the run log by path (--json suppresses the console stream)
        log_file = _paths.last_run_root(cwd) / "logs" / "noodle.log"
        payload["log"] = str(log_file) if log_file.is_file() else None
        # NOOD_0200 — evidence images by PATH: the agent reads the file via
        # its harness file-read, never an `open`/`cat` hunt through rca.md.
        from noodle.reporting import rca_report as _rca_rep
        if ev := _rca_rep.collect_evidence(results_root):
            payload["evidence"] = [{"step": e["step"], "status": e["status"],
                                    "path": e["path"]} for e in ev]
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


def _write_last_run(results_root: str, rc: int, cwd: str = ".",
                    data: dict | None = None) -> dict:
    """NOOD_0045 Phase 4 — persist the structured run outcome to
    artifacts/last_run.json so shell/CI agents get machine-readable results
    without re-parsing allure-results themselves. Returns the collected data
    (NOOD_0131) so the caller reuses one scan for the quiet summary and
    --json payload instead of re-collecting per consumer. `data` (NOOD_0187):
    a dict collect() already produced, possibly annotated by the caller."""
    from noodle.reporting import summary as _summary
    data = data if data is not None else _summary.collect(results_root)
    data["exit_code"] = rc
    data["at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    out = Path(cwd) / _paths.artifacts_root() / "last_run.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # reporting nicety — never fail the run over it
    return data


def _all_failures_quarantined(results_dir: str):
    """Scan this run's Allure results. Returns:
      True  — there were failures and ALL are @quarantine
      False — at least one non-quarantine failure
      None  — nothing to judge (no results / reporting off / no failures)

    NOOD_0187 — reads the historyId-deduped LAST attempt per scenario:
    counting raw files meant a failed-then-retried-green scenario still
    registered as a live failure here (and got re-run by --failed).
    """
    from noodle.reporting import summary as _summary
    results = _summary.latest_results(results_dir)
    if not results:
        return None
    failed = []
    for r in results:
        if r.get("status") in ("failed", "broken"):
            tags = {lab.get("value") for lab in r.get("labels", []) if lab.get("name") == "tag"}
            failed.append("quarantine" in tags)
    if not failed:
        return None
    return all(failed)


def _failed_scenario_names(cwd: str = ".") -> list[str]:
    """NOOD_0183 — scenario names that failed in the last run, read from the
    allure results that run already wrote (no extra bookkeeping file to keep
    in sync). Works after a parallel run too — the merge flattens worker dirs
    into this one before anything reads it. NOOD_0187 — last attempt only:
    a retried-then-green scenario is not a failure to re-run."""
    from noodle.reporting import summary as _summary
    results = str(_paths.last_run_root(cwd) / "allure-results")
    return sorted({r["name"] for r in _summary.latest_results(results)
                   if r.get("status") in ("failed", "broken") and r.get("name")})


def _shard_slice(cwd: str, path: str, shard: str) -> list[str]:
    """NOOD_0187 — deterministic slice i of N of the .feature files under
    `path`, sorted by relative path so every machine partitions the suite
    identically. Returns base-relative posix paths for an --include regex."""
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", shard)
    if not m:
        raise typer.BadParameter("--shard must be i/N, e.g. 3/16", param_hint="'--shard'")
    i, n = int(m.group(1)), int(m.group(2))
    if not (1 <= i <= n):
        raise typer.BadParameter(f"shard index {i} outside 1..{n}", param_hint="'--shard'")
    base = Path(cwd) / path
    if base.is_file():
        raise typer.BadParameter(
            "--shard slices a directory of .feature files, not a single file",
            param_hint="'--shard'")
    feats = sorted(p.relative_to(base).as_posix() for p in base.rglob("*.feature"))
    return feats[i - 1::n]


def _run_behave(args: list, env: dict, cwd: str, log_path=None,
                timeout: int = 0) -> tuple[int, bool]:
    """Run behave/behavex with an optional wall-clock budget (NOOD_0187).
    On timeout the whole process GROUP is killed (behavex spawns workers,
    workers spawn browsers) and rc is 124 — the per-scenario results already
    on disk still feed the report tail. Returns (rc, timed_out). The
    no-timeout path stays subprocess.run — it is the seam unit tests fake."""
    kw: dict = {"env": env, "cwd": cwd}
    lf = None
    if log_path is not None:
        lf = open(log_path, "w", encoding="utf-8")
        kw["stdout"] = lf
        # NOOD_0202 — stderr is the child's progress channel: in CI let it
        # through to the build console (this is the whole fix — folding it into
        # run.log is what made a quiet run silent AND kept every scenario/step
        # event off the log stream docs/logging.md promises). With no console
        # watching it folds in as before: an agent's captured pipe must not
        # grow a firehose.
        if not log.progress_mode():
            kw["stderr"] = subprocess.STDOUT
    try:
        if not timeout:
            return subprocess.run(args, **kw).returncode, False
        if os.name != "nt":
            kw["start_new_session"] = True   # so the timeout can kill the group
        proc = subprocess.Popen(args, **kw)
        try:
            return proc.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                import signal
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    proc.kill()
            else:
                proc.kill()   # ponytail: no job objects — grandchildren may linger on Windows
            proc.wait()
            return 124, True
    finally:
        if lf is not None:
            lf.close()


def _derive_exit_code(data: dict, rc: int, timed_out: bool,
                      allow_empty: bool) -> tuple[int, list[str]]:
    """NOOD_0187 — the written results are the ground truth for the exit code
    in BOTH modes: behave exits 0 on a soft-assert-only failure, and behavex
    has been observed exiting 0 with failed scenarios in the workers. And a
    run that executed NOTHING must not exit 0 — a typo'd --tag/--name/path
    produced a green pipeline that tested nothing. Exit 3 is distinct from
    behave's 1 and preflight's 2. Returns (rc, console notes)."""
    notes = []
    if data.get("failed", 0) > 0:
        rc = rc or 1
    ran = data.get("passed", 0) + data.get("failed", 0) + data.get("skipped", 0)
    if ran == 0 and not timed_out:
        if allow_empty:
            notes.append("⚠ 0 scenarios ran in this slice")
        else:
            notes.append("✗ 0 scenarios ran — nothing was tested (bad path, "
                         "--tag or --name?). Failing the run; "
                         "NOODLE_ALLOW_EMPTY=1 overrides.")
            rc = rc or 3
    return rc, notes


def _run_parallel(path: str, processes: int, tag: str, env: dict, cwd: str = ".",
                  scheme: str = "feature", names: list[str] | None = None,
                  shard_include: str | None = None, timeout: int = 0,
                  fail_fast: bool = False, log_path=None) -> tuple[int, bool]:
    """Run feature files concurrently via behavex, then merge into one report.
    Exit-code honesty, quarantine and last_run.json live in run()'s shared
    tail (NOOD_0187) — this only runs, merges and builds the reports."""
    try:
        import behavex  # noqa: F401
    except ImportError:
        raise typer.BadParameter(
            'Parallel runs need behavex. Install: pip install -e ".[parallel]"',
            param_hint="'--parallel'",
        )
    results = Path(cwd) / _paths.artifacts_root() / "allure-results"
    _clean_results_root(results)            # workers skip the wipe in parallel mode
    _paths.clean_worker_leaves(Path(cwd) / _paths.artifacts_root())
    # NOOD_0183 — clear locks/lanes left by a killed previous run, once, in the
    # parent. A worker must never do this: it would free a live sibling's lock.
    from noodle import runlock
    runlock.reset_control_dir(cwd)
    env = {**env, "NOODLE_PARALLEL_WORKER": "1"}
    # Same PATH concern as _BEHAVE_CMD — resolve through this interpreter.
    # NOOD_0187 — `-o` keeps behavex's own output out of ./output (two runs in
    # one workspace clobbered it) and --no-report skips its duplicate HTML
    # report: the Allure report built below is the one anybody reads.
    args = [sys.executable, "-m", "behavex", path,
            "--parallel-processes", str(processes),
            "--parallel-scheme", scheme,
            "-o", str(Path(cwd) / _paths.artifacts_root() / "behavex"),
            "--no-report"]
    if tag:
        args += ["--tags", tag]
    for n in names or []:
        args += ["--name", n]
    if shard_include:
        args += ["--include", shard_include]
    if fail_fast:
        args += ["--stop"]
    rc, timed_out = _run_behave(args, env, cwd, log_path=log_path, timeout=timeout)
    _merge_worker_results(results)          # flatten p*/ so report + scan read one dir

    from noodle.reporting import rca_report as _rca_report
    from noodle.reporting import summary as _summary
    from noodle.reporting.builder import generate
    _summary.mark_flaky(str(results))       # NOOD_0187 — stamp retried-green as flaky
    reports_root = Path(cwd) / _paths.artifacts_root() / "reports"
    generate(str(results), str(reports_root / "allure-report"))
    # NOOD_0082 — parallel runs get the same always-written rca.md/rca.html
    # tail the single-process hooks path writes (hooks skip it per-worker).
    _rca_report.write_reports(str(results), str(reports_root))
    return rc, timed_out


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
    # NOOD_0187 — per-feature junit slices, healing slices and cost ledgers
    # from the previous run: cost.load_total() rglobs llm_cost*.json, so a
    # stale ledger inflated the next run's reported spend.
    for f in (*results.glob("junit.*.xml"), *results.glob("llm_cost*.json"),
              *results.glob("healing-report*.txt")):
        f.unlink(missing_ok=True)
    for d in results.glob("p*"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


def _merge_worker_results(results: Path):
    """Flatten each worker dir into the shared dir so the existing report build +
    quarantine scan (both read the flat dir) work unchanged, then remove the now-
    empty worker dirs. Per-worker junit slices merge into one reports/junit.xml —
    same artifact a single-process run produces. uuid filenames don't collide.

    NOOD_0187 — three fixes: junit/healing files are per-FEATURE slices now
    (behavex runs after_all once per feature, so fixed names kept only each
    worker's last feature); fixed-name metadata (environment.properties,
    categories.json) skips the move when the target exists instead of
    crashing Windows (os.rename onto an existing dst raises there); and the
    per-worker traces/network/screenshots/videos leaves flatten up too, so
    the parent-rendered RCA (traces section, mutation classifier) actually
    sees them — under --parallel it silently read empty dirs."""
    import shutil

    from noodle.reporting import junit as _junit

    worker_dirs = sorted(d for d in results.glob("p*") if d.is_dir())
    junits = [j for d in worker_dirs for j in sorted(d.glob("junit*.xml"))]
    healing_texts = []
    for d in worker_dirs:
        for f in sorted(d.iterdir()):
            if not f.is_file() or f.name.startswith("junit"):
                continue
            if f.name.startswith("healing-report"):
                try:
                    healing_texts.append(f.read_text(encoding="utf-8"))
                except OSError:
                    pass
                continue
            target = results / f.name
            if target.exists():
                continue   # fixed-name metadata — first worker's copy wins
            shutil.move(str(f), str(target))
    if junits:
        # Merged junit lands OUTSIDE allure-results so allure generate doesn't
        # ingest scenarios twice (once from JSON, once from XML).
        _junit.merge_junits(junits, results.parent / "reports" / "junit.xml")
    if healing_texts:
        out = results.parent / "reports" / "healing-report.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(healing_texts), encoding="utf-8")
    for d in worker_dirs:
        shutil.rmtree(d, ignore_errors=True)

    # Flatten the per-worker artifact leaves (traces/network/…): the RCA and
    # its mutation-aware classifier read the flat dirs. Name collisions get a
    # worker prefix (two features CAN share a scenario name across apps).
    root = results.parent
    for sub in ("traces", "network", "screenshots", "videos"):
        base = root / sub
        if not base.is_dir():
            continue
        for wd in sorted(d for d in base.glob("p[0-9]*") if d.is_dir()):
            for f in sorted(wd.iterdir()):
                if not f.is_file():
                    continue
                target = base / f.name
                if target.exists():
                    target = base / f"{wd.name}-{f.name}"
                try:
                    shutil.move(str(f), str(target))
                except OSError:
                    pass
            shutil.rmtree(wd, ignore_errors=True)


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
# NOOD_0177 — run output must never be committable. Playwright traces record
# request AND response headers (Authorization, Set-Cookie) plus DOM snapshots;
# the network log holds request URLs; screenshots capture authenticated pages;
# a saved browser session is a pre-authenticated cookie jar that bypasses MFA.
# The engine's own .gitignore has always covered these — the scaffold did not,
# so `noodle init` -> run -> `git add .` published them.
artifacts/
reports/
archives/
output/
baselines/
**/report/
session*.json
docs/steps_dictionary_suggestions.md
"""


def _merge_gitignore(root: Path):
    """NOOD_0204 — .gitignore is the one scaffold file that needs MERGING.

    It is neither glue (always overwrite) nor user config (never touch): most
    repos already have one from repo-init, and the old config-file skip meant
    they never received the run-output ignores — so traces, screenshots and
    saved sessions (see _GITIGNORE's own comment for the stakes) stayed
    committable in exactly the repos with collaborators and history. Append
    only the missing entries, under one marked block, idempotently; never
    remove or reorder what the user wrote. Returns 'created', 'updated' or
    None for the init summary.
    """
    f = root / ".gitignore"
    if not f.exists():
        f.write_text(_GITIGNORE, encoding="utf-8")
        return "created"
    existing = f.read_text(encoding="utf-8")
    have = {ln.strip() for ln in existing.splitlines()}
    missing = [ln for ln in _GITIGNORE.splitlines()
               if ln.strip() and not ln.startswith("#") and ln.strip() not in have]
    if not missing:
        return None
    block = ("\n# --- added by noodle init: run output and secrets must never "
             "be committable (NOOD_0177) ---\n" + "\n".join(missing) + "\n")
    f.write_text(existing.rstrip("\n") + "\n" + block, encoding="utf-8")
    return "updated"


_ENV_STUB_BASE = """\
# .env — workspace settings, safe to commit. NO SECRETS here: put credentials
# in secrets.env (gitignored) and reference them in tests as {env:MY_PASSWORD}.
# PURPOSE: run-wide defaults the engine reads on every run.
# YOU EDIT: uncomment / change values as needed.
NOODLE_BROWSER=chromium         # chromium | firefox | webkit | safari | edge
NOODLE_HEADLESS=false           # headed for a watching human — read by bare `behave` only; `noodle run` follows noodle.yaml (headless: true), CI passes --headless
NOODLE_TIMEOUT=10000            # per-action timeout, milliseconds (clicks, page loads)
#NOODLE_REST_TIMEOUT=30         # REST/API budget, SECONDS — a ceiling, not a wait: the step continues the instant the response lands. Slow report/batch/cold-start endpoint? raise it, e.g. 180. Per step: "... within 180 seconds"
#NOODLE_FIND_TIMEOUT=120000     # element-find + page-load budget, ms — a CEILING, not a wait: steps proceed the instant the element appears. Slow internal site? raise it, e.g. 300000 (5 min)
#NOODLE_WAIT_EXTENSION=30000    # one extra wait granted at the find deadline while the page is still loading (network active)
#NOODLE_DOM_SCAN_MAX=3000       # elements the DOM-attribute heal tier walks before it stops. Any attribute counts (`class` included), so on a component-framework app this is really a DOM-node cap. Big ERP/CRM SPA where a step can't find an element the page clearly has? raise it, e.g. 25000
# Authoring on a SLOW/spinner-heavy site? Uncomment this dev-loop floor so a missed
# element fails fast instead of eating the full ceiling — then RAISE/REMOVE it for the
# real CI run, or a genuinely slow-but-valid load will false-fail:
#NOODLE_FIND_TIMEOUT=25000
#NOODLE_WAIT_EXTENSION=15000
#NOODLE_SETTLE_TIMEOUT=15000    # settled-page early exit, ms: once the page is done (network quiet + DOM stable) a find that still hasn't matched stops polling early instead of exhausting the full budget; 0 disables
# NOOD_0177 — TLS certificates are VERIFIED by default. Set this to true ONLY for a
# dev/sandbox site with a self-signed cert, and never for a run that types real
# credentials: disabling it lets any on-path proxy read them. Per-scenario: @insecure_certs.
NOODLE_IGNORE_HTTPS_ERRORS=false
#NOODLE_AUTO_DISMISS=true       # auto-close overlays that block a click, with an RCA warning; set false to fail instead
#NOODLE_DEV_FIX_ATTEMPTS=10      # agent test-dev loop: CEILING on cause-backed fix+rerun attempts (first failure: reproduce once with probe --do, never guess-per-lap) before reporting the test as flaky
#NOODLE_VIEWPORT=1920x1080      # run-wide viewport (or @viewport:WxH tag per scenario)
#NOODLE_RETRIES=0               # retry failed scenarios N times
#NOODLE_LOG_LEVEL=INFO          # DEBUG adds a line per step to the CI build log (the `mvn -X` tier — shows WHERE a wedged run stopped)
#NOODLE_LOG_PROGRESS=1          # stream a maven-style build log to stderr. Auto-on when CI/TF_BUILD is set; agents (MCP, --json) force it off so an LLM session isn't charged for it. See docs/logging.md
#NOODLE_LOG_FORMAT=json         # ship that same stream as one JSON object per line, for a platform log store
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
| `azure-pipelines/azure-pipelines.yml` | runs this workspace in Azure DevOps | once |
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

1. Author your first test in one call: `noodle author --prompt "<the ask>" --run`
   (the `author_test` MCP tool with `run_after_author=true`) — it creates the
   whole app package under `noodle_tests/web/<your-app>/`, runs it, and serves
   the reports. No spec file needed — the prompt goes in as typed. `sample_app/` is a step-vocabulary reference to read, not a
   starting workflow — vocabulary: `docs/steps_dictionary.md` in the noodle repo.
2. Credentials → your app's `resources/<app>_secrets.env` (gitignore it).
   Base URL → your app's `resources/environments.yaml` (`<app>: https://…`).
   Both are referenced in features as `{env:KEY}` — keep them in the app
   package, not the workspace root.
3. Check steps without a browser: `noodle validate noodle_tests/ --resolve`
4. Run: `noodle run` — or interactively: `noodle repl`

## Run it in Azure DevOps

`azure-pipelines/azure-pipelines.yml` was scaffolded for you. It is the only
CI file this repo owns — every step lives in the engine's own template, so a CI
upgrade is a version bump, not a diff.

Before the first run:

1. **Fill the two `REPLACE_ME` placeholders** — the engine repo's
   `<Project>/<Repo>`, and the engine version to pin
   (`refs/tags/<version>`). Both fail at *compile* time, which produces no job
   log: a run that dies before any job appears is almost always one of these.
2. **`workspaceDir`** — leave it empty when this repo's root is the workspace
   (the default here); set it to the path when the workspace is a folder inside
   a bigger repo.
3. **Point the pipeline definition** at `/azure-pipelines/azure-pipelines.yml`.
   Azure stores the YAML path in the *pipeline*, not the repo — moving the file
   does not move the pipeline (Edit → ⋮ → Triggers → YAML → path).
4. **Grant the pipeline read access to the engine repo** (Project Settings →
   Repositories → the engine repo → Security → `<Project> Build Service`), or
   the run dies at compile time.
5. *(Optional)* install the `qameta.allure-azure-pipelines` extension once per
   organization for the Allure tab — or set `publishAllureTab: false`. The
   Tests tab and the downloadable report publish either way.

**Secrets never go in the pipeline file or in git** — use `keyVaultUrl` or
`secretEnv` mapped from a variable group.

Full walkthrough: `docs/ci-project-repo.md` in the noodle engine repo. Editing
this pipeline later — schedules, tag filters, speed, pools, secrets, and a
troubleshooting table: `docs/ci-workspace-pipeline.md`.

Full guide: README.md § Agentic mode, in the noodle repo.
"""

_AZURE_PIPELINE = """\
# Azure DevOps pipeline for this Noodle workspace — scaffolded by `noodle init`.
#
# This is the ONLY CI file this repo owns. Every step (checkout, install,
# browsers, Allure CLI, run, publish) lives in the engine's own template, so
# upgrading CI is a version bump here, not a diff.
#
# TWO PLACEHOLDERS to fill in before the first run. Both fail at COMPILE time,
# which produces no job log — if a run dies before any job appears, look here:
#   1. `name:` below — <Project>/<Repo> of the engine repo in Azure Repos
#      (a bare repo name only resolves inside one project).
#   2. `default:` of noodleRef — the engine version to pin, e.g. refs/tags/1.0.0a21.
#
# Also check `workspaceDir`: empty when this repo's ROOT is the workspace (what
# `noodle init` scaffolds by default); a path like `tests/noodle` when the
# workspace is a folder inside a bigger repo.
#
# Setup guide:  docs/ci-project-repo.md      (in the noodle engine repo)
# Editing this: docs/ci-workspace-pipeline.md — a recipe per common change
#               (engine version, tag filter, nightly schedule, speed, agent
#               pool, secrets) plus a troubleshooting table.

name: noodle-tests-$(Date:yyyyMMdd)$(Rev:.r)

trigger:
  - main

pr:
  - main

parameters:
  # 'all', not '': Azure's Run-pipeline panel won't advance while a runtime
  # string parameter is empty, which made the whole-suite run unreachable.
  - name: testTag
    displayName: 'Tag filter — "all" runs every feature and scenario'
    type: string
    default: all
  - name: shard
    displayName: 'Fan out to one agent per .feature file (large suites only)'
    type: boolean
    default: false
  - name: parallelProcesses
    displayName: 'Parallel test processes within the job (0 = single process)'
    type: number
    default: 4
  # PIN IT. A branch is not a build: bump this deliberately once a new engine
  # version has been validated against this suite. It is a runtime parameter so
  # an engine feature branch can be tried from a one-off run — pick it in the
  # Run panel — without committing anything here.
  - name: noodleRef
    displayName: 'Engine version — refs/tags/<version>, or refs/heads/<branch> to try a build'
    type: string
    default: refs/tags/REPLACE_ME

resources:
  repositories:
    - repository: noodle
      type: git                 # 'github' + endpoint: <service connection> off Azure Repos
      name: REPLACE_ME/noodle   # the engine repo, <Project>/<Repo>
      # The two repos have INDEPENDENT branches. The branch you pick when you
      # run this pipeline applies to THIS repo only; the engine version comes
      # from here and nowhere else (omit `ref` and Azure takes the engine's
      # default branch — never a branch matching yours).
      ref: ${{ parameters.noodleRef }}

jobs:
  - template: ci/azure/noodle-tests.yml@noodle
    parameters:
      # This repo IS the workspace — noodle.yaml sits at its root. A workspace
      # nested inside a product repo passes its path instead (e.g. tests/noodle).
      workspaceDir: ''
      testTag: ${{ parameters.testTag }}
      shard: ${{ parameters.shard }}
      parallelProcesses: ${{ parameters.parallelProcesses }}
      # extras: '[visual]'      # ONLY if the suite uses @visual / @appium
      # publishAllureTab: false # if the org has no Allure extension

      # Secrets, one knob: keyVaultUrl for Azure Key Vault, or secretEnv for an
      # explicit KEY: $(VAR) map. See the workspace README § Secrets. Never put
      # a secret value in this file or in git.
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
against a fixed step dictionary — no step code. Full reference:
`read_docs('agent-playbook')` (CLI: `noodle docs agent-playbook`).

North star: deterministic, plain-English, token-cheap, honest.

Nouns: **engine** = the installed framework (never edited here);
THIS folder = a **workspace** (`noodle init`; refresh `--force`);
**wok** = capability area, tag-routed (`read_docs('woks')`). Noodle is
universal, not web-only: independent woks are web · api (REST,
browserless, `@api`) · mobile · desktop · perf. Never say "Noodle
can't": read the wok.

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
   ONE command = package+run+reports:
   `noodle author --prompt "<the ask, verbatim>" --run --json`
   (MCP: `author_test`) — ask passed RAW, never `--help` first;
   a refusal names the rewrite. `--spec '<yaml>'`
   (inline, never a heredoc) or `goal` once `--prompt` refuses;
   feature_content only on a named goal blocker. `ready: true` =
   parsed, matched, POM scoped, `{env:}` resolved — do not validate/
   preflight separately; run next. `ready: false`? Fix `blocking`,
   re-author — no bypass, no guessed action. Base URL →
   `resources/environments.yaml` (`base_url_key`). Steps: probe
   output or `search_step` (`noodle steps <kw>…` — all words, ONE
   call); `use_llm=True` last; `append_to` appends a scenario
   (llm-performance). Result-pick binding, `after:` anchoring,
   `evidence: screenshot`: playbook.
3. Execute + report — one call: `run_and_report` with `headless=True,
   retries=0, serve_reports=True` (`noodle run noodle_tests/<app>
   --headless --retries 0 --json`): preflights secrets, runs,
   serves both reports, folds compact RCA in on red — never separate
   validate/RCA/serve calls. Green = `failed == 0` AND
   `verified: true`; `verified: false` (fuzzy healing behind a pass)
   is NOT a pass — read `unverified_reasons`/`healing_events`.
   Screenshot proof: read the image at `evidence[].path`.

Red? Budget: one probe, one run — more needs a named cause. Cheapest
evidence first: `rca_compact` names the cause and fix;
screenshot/network capture only if
inconclusive — vision costs ~10× text. Reproduce it ONCE (`probe
--do` replays the flow), re-author from evidence; cause-backed fixes
only, cap NOODLE_DEV_FIX_ATTEMPTS (default 10).
Hand-edited? `validate_feature` before re-running. Wrong element
(`inspect_locator`) and the failure taxonomy: playbook.

## Rules

- Steps must match the dictionary; never invent phrasing.
- Never invent assertion text absent from probe evidence; assert
  durable state, not a toast. Dynamic/decorated text? Assert the
  smallest stable substring; never silently drop the asked-for verify.
  An OR is ONE `sees any of` step, never narrowed to a member;
  blocked authoring IS correct.
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
  pipes; URLs pre-checked (no curl); schema: `noodle author
  --vocabulary`; workspace map: `noodle list`, not find/ls sweeps.
- Progress updates: max 2 sentences of current intent (e.g. "Serving
  the reports now"); quote only failing steps/errors. "do not output
  the shell command"? Then echo no command line.
- Keep each app's files in its own package.
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
        # NOOD_0205 — its own folder, because a repo grows more than one
        # pipeline (nightly, PR, release) and that's where every other Azure
        # repo keeps them. A template file: written when missing, refreshed
        # only with --force, never silently overwritten.
        root / "azure-pipelines" / "azure-pipelines.yml": _AZURE_PIPELINE,
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
    from noodle.docs_reader import read_docs
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
        # .gitignore is NOT here — it append-merges via _merge_gitignore below
        tests / "pom.yaml": _GLOBAL_POM,
    }
    env_path = root / ".env"
    env_existed = env_path.exists()
    created, updated, stale = [], [], []

    def _write(f: Path, text: str):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")

    for f, text in {**glue, **templates, **config_files}.items():
        if not f.exists():
            _write(f, text)
            created.append(str(f))
            continue
        if f.read_text(encoding="utf-8") == text or f in config_files:
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
    gi_state = _merge_gitignore(root)
    if gi_state == "created":
        created.append(str(root / ".gitignore"))
    elif gi_state == "updated":
        updated.append(f"{root / '.gitignore'} (missing run-output ignores appended)")
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
    # NOOD_0174/0177 — scope the VS Code `noodle` language to this workspace's
    # tests_dir so our extension stops fighting Cucumber over `.feature`, and
    # doesn't over-claim a sibling framework's features in a monorepo.
    settings = root / ".vscode" / "settings.json"
    glob = _noodle_assoc(config.load(str(root)).get("tests_dir", "noodle_tests"))
    typer.echo("\nVS Code language association:")
    typer.echo(f"  {settings}: {_merge_vscode_association(settings, glob)}")
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
            data = json.loads(f.read_text(encoding="utf-8") or "{}")
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
    f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return "updated" if existed else "created"


# NOOD_0174 — the VS Code extension no longer claims `.feature` globally (that
# collided with Cucumber extensions over who owns highlighting/LSP). Instead we
# scope our `noodle` language via a per-workspace `files.associations` glob —
# it only applies to THIS workspace folder, so a separate Cucumber project (a
# different folder, no association) is untouched.
# NOOD_0175 — the glob must match where features actually live, or the LSP never
# attaches (no hover); a hardcoded `**/noodle_tests/**` broke workspaces whose
# tests_dir differs.
# NOOD_0176 — so scope it to the workspace's ACTUAL tests_dir:
# `**/<tests_dir>/**/*.feature` (default scaffold → `**/noodle_tests/**`). This
# narrows the claim to the noodle subtree, so a monorepo that also holds a
# Selenium/Playwright project under the same opened folder keeps those
# `.feature` files for Cucumber. The prior broad `**/*.feature` default is
# migrated away on re-init (see below). The engine repo's own hand-committed
# .vscode/settings.json stays `**/*.feature` — it's all-noodle with a mixed
# web/api layout and has no foreign framework to protect.
_ASSOC_LANG = "noodle"
_STALE_ASSOC = "**/*.feature"  # our pre-0177 default — removed on re-init


def _noodle_assoc(tests_dir: str) -> str:
    return f"**/{tests_dir}/**/*.feature"


def _merge_vscode_association(f: Path, glob: str) -> str:
    """Map this workspace's feature files (under `glob`) to the `noodle`
    language in `.vscode/settings.json`, preserving every other setting. Drops
    our old broad `**/*.feature` default if it's the one we wrote, so re-init
    narrows an existing workspace. created|updated|kept."""
    data = {}
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            return f"kept (unparseable JSON — fix {f} by hand)"
    assoc = data.setdefault("files.associations", {})
    # Migrate our own prior broad default to the scoped glob (skip if the
    # workspace genuinely wants both, i.e. tests_dir itself is root-level).
    stale = (glob != _STALE_ASSOC and assoc.get(_STALE_ASSOC) == _ASSOC_LANG)
    if assoc.get(glob) == _ASSOC_LANG and not stale:
        return "kept (already configured)"
    existed = f.exists()
    if stale:
        del assoc[_STALE_ASSOC]
    assoc[glob] = _ASSOC_LANG
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
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


# NOOD_0174 — the extension's runtime deps are vendored under
# vscode-extension/node_modules, so installing it needs no vsce/.vsix/`code`
# CLI: drop (link) the folder into the editor's extensions dir and reload.
# ponytail: symlink so the install tracks the clone (a `git pull` + reload
# picks up fixes, no repackage); copytree only where symlinks are refused.
def _install_extension_into(src: Path, ext_dir: Path) -> tuple[Path, str]:
    """Link/copy the shipped extension `src` into `ext_dir`, replacing any
    prior noodle install first. Returns (destination, 'linked'|'copied')."""
    ext_dir.mkdir(parents=True, exist_ok=True)
    for old in ext_dir.glob("noodle.noodle*"):        # a stale sideload is the
        if old.is_symlink() or old.is_file():         # usual reason highlighting
            old.unlink()                              # /LSP silently stops working
        else:
            shutil.rmtree(old)
    ver = json.loads((src / "package.json").read_text(encoding="utf-8"))["version"]
    dst = ext_dir / f"noodle.noodle-{ver}"            # VS Code's publisher.name-version
    try:
        dst.symlink_to(src, target_is_directory=True)
        return dst, "linked"
    except OSError:                                    # Windows w/o dev-mode, etc.
        shutil.copytree(src, dst)
        return dst, "copied"


@app.command("install-extension")
def install_extension(
    extensions_dir: str = typer.Option(
        None, "--extensions-dir",
        help="Editor extensions dir. Default ~/.vscode/extensions; use "
             "~/.cursor/extensions (Cursor) or ~/.vscode-oss/extensions (VSCodium)."),
):
    """Install the VS Code extension (syntax highlighting, step squiggles,
    {env:/var:/pom:} hover/completion) — no vsce, no .vsix, no `code` CLI.
    Deps ship vendored, so this just links the extension into your editor;
    fully quit VS Code and reopen to load it. Re-run after a `git pull` is
    unnecessary for a symlinked install — a Reload Window suffices.

    Pair with `noodle init`, which scopes the extension to this workspace's
    feature files (.vscode/settings.json files.associations) so it no longer
    collides with a Cucumber/Gherkin extension over `.feature`."""
    src = Path(__file__).resolve().parent.parent / "vscode-extension"
    if not (src / "package.json").is_file():
        typer.echo("vscode-extension/ isn't part of this install (installed from a "
                   "wheel?) — clone the noodle repo to install the editor extension.")
        raise typer.Exit(1)
    ext_dir = Path(extensions_dir).expanduser() if extensions_dir else \
        Path.home() / ".vscode" / "extensions"
    dst, how = _install_extension_into(src, ext_dir)
    typer.echo(f"  {how}: {dst} -> {src}")
    typer.echo("Fully quit VS Code (Cmd+Q / close all windows) and reopen to load it.\n"
               "Then run `noodle init` in your workspace if you haven't — it adds the "
               "files.associations that keeps this from colliding with Cucumber.")


@app.command()
def author(
    spec: str = typer.Option(None, "--spec", help="A JSON or YAML spec: a file path, '-' for stdin, or the document itself inline (NOOD_0197 — no heredoc or temp file needed: --spec \"$(cat)\" style plumbing is never required, quote the YAML directly). Fields: app_name, base_url, feature_path, and EITHER feature_content (one Gherkin string; pom_content is likewise one YAML string, never a filename map) OR goal (NOOD_0137 constrained mode — the engine probes and compiles the feature/POM itself; see author_test). Optionally: environment_values, required_secret_keys, secret_values, overwrite."),
    prompt: str = typer.Option(None, "--prompt", help="NOOD_0169 — numbered plain-English steps ('1. go to <url> 2. search for X 3. add to cart 4. verify cart has X'), inline OR (NOOD_0198) a file path / '-' for stdin, so a generator upstream can hand off a written file without `\"$(cat ...)\"` mangling its backticks; the engine expands them deterministically into a goal (ambiguous steps borrow their subject from neighbouring steps, every inference echoed under prompt_expansion.assumptions) and derives app_name/base_url/feature_path from the URL. No spec file needed; combine with --run for prompt → authored → run → reports in ONE call."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    as_json: bool = typer.Option(False, "--json", help="Structured output for agents/CI"),
    run: bool = typer.Option(False, "--run", help="NOOD_0137 — atomic author+run: after a ready author, run once (headless, retries=0), serve both reports, and fail when 0 scenarios passed. Blocked authoring launches no browser."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing .feature at the target path. A blocked authoring attempt leaves its files behind (fix-in-place contract) — without this flag the retry refuses. Spec key `overwrite` works too; prompt mode has only this flag."),
    vocabulary: bool = typer.Option(False, "--vocabulary", help="NOOD_0197 — print the goal vocabulary, a minimal example, and the prompt grammar as JSON, then exit. The schema on demand instead of discoverability-by-rejection (no more scraping --help for it)."),
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
    if vocabulary:
        # NOOD_0197 — the goal schema + prompt grammar on demand. Before this
        # the only machine-readable copy rode a rejection payload, so agents
        # learned the vocabulary by failing (or by paging --help through sed).
        from noodle.repl import goal as goal_mod
        from noodle.repl.prompt_expander import VERBS_HELP
        _json_out({"ok": True, "example": goal_mod.EXAMPLE,
                   "vocabulary": goal_mod.vocabulary(),
                   "prompt_grammar": VERBS_HELP})
        raise typer.Exit(0)
    if (spec is None) == (prompt is None):
        raise typer.BadParameter("pass exactly one of --spec or --prompt",
                                 param_hint="'--spec' / '--prompt'")
    if prompt is not None:
        # NOOD_0169 — prompt mode: expansion + derivation happen engine-side
        # NOOD_0198 — ...and the steps may arrive as a file path or on stdin
        prompt, _ = _arg_text(prompt)
        result = core.author_test(prompt=prompt, run_after_author=run,
                                  overwrite=overwrite, workspace=workspace)
    else:
        # NOOD_0197 — --spec accepts the document inline: an argument
        # that resolves to no file is the spec itself. This removes the
        # heredoc/temp-file dance (and the shell approval it costs an
        # agent) the moment --prompt can't express a flow.
        raw, indirect = _arg_text(spec)
        if not indirect and ":" not in raw and "{" not in raw:
            raise typer.BadParameter(f"spec file not found: {spec}",
                                     param_hint="'--spec'")
        try:
            data = yaml.safe_load(raw) or {}
        except Exception as e:
            raise typer.BadParameter(f"spec is not valid JSON/YAML: {e}", param_hint="'--spec'")
        if not isinstance(data, dict):
            raise typer.BadParameter("spec must be a JSON/YAML object", param_hint="'--spec'")
        # NOOD_0207 — feature_path is no longer required: with a goal, the
        # engine derives it from the scenario (and relocates it regardless).
        # NOOD_0213 — neither are app_name/base_url when the goal navigates
        # to a URL: author_test derives all three from navigation[0]. The
        # gate here only rejects what the engine cannot derive downstream.
        goal_navs = (data.get("goal") or {}).get("navigation") \
            if isinstance(data.get("goal"), dict) else None
        goal_has_url = bool(goal_navs) and isinstance(goal_navs[0], str) \
            and goal_navs[0].startswith(("http://", "https://"))
        missing = [] if goal_has_url else \
            [k for k in ("app_name", "base_url") if not data.get(k)]
        if not data.get("feature_path") and not data.get("goal"):
            missing.append("feature_path (or goal, which derives it)")
        if not data.get("feature_content") and not data.get("goal"):
            missing.append("feature_content (or goal)")
        if missing:
            raise typer.BadParameter(f"spec missing required field(s): {', '.join(missing)}",
                                     param_hint="'--spec'")
        result = core.author_test(
            app_name=data.get("app_name"), base_url=data.get("base_url"),
            feature_path=data.get("feature_path"),
            feature_content=data.get("feature_content"),
            pom_content=data.get("pom_content"),
            environment_values=data.get("environment_values"),
            required_secret_keys=data.get("required_secret_keys"),
            secret_values=data.get("secret_values"),   # NOOD_0130 — write-only, never echoed
            goal=data.get("goal"), run_after_author=run,
            overwrite=overwrite or bool(data.get("overwrite", False)),
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
def scan(
    path: str = typer.Argument(".", help="Repo root to scan"),
    as_json: bool = typer.Option(True, "--json/--no-json", help="One bounded JSON payload (default) or a short human summary"),
):
    """NOOD_0192 — what is this repo, and what can Noodle test in it?

    A deterministic scan of marker files: stack, framework, how the repo says
    it serves itself, any OpenAPI spec (with its endpoints — that IS the API
    test plan), where tests already live, and the QUESTIONS still open before
    a test can be authored. No LLM, no code execution, nothing written.

    The answer to "review this repo and write me some tests": run this, settle
    `questions` with the developer, then send the steps to `noodle task`."""
    from noodle import repo_scan
    rep = repo_scan.scan(path)
    if as_json:
        _json_out(rep)
        raise typer.Exit(0 if rep.get("ok") else 1)
    if not rep.get("ok"):
        typer.echo(f"  ✗ {rep.get('error')}")
        raise typer.Exit(1)
    typer.echo(f"  📁 {rep['root']}")
    typer.echo(f"  · stack: {', '.join(rep['stacks']) or 'unrecognized'}"
               + (f" ({', '.join(rep['frameworks'])})" if rep["frameworks"] else ""))
    typer.echo(f"  · woks:  {', '.join(rep['woks'])}")
    for s in rep["serve"]:
        typer.echo(f"  · serve: {s}")
    for s in rep["api_specs"]:
        typer.echo(f"  · spec:  {s} ({rep['endpoints_total']} endpoints)")
    for e in rep["endpoints"]:
        typer.echo(f"      {e}")
    for t in rep["test_dirs"]:
        typer.echo(f"  · tests: {t}")
    for q in rep["questions"]:
        typer.echo(f"  → ask: {q}")
    raise typer.Exit(0)


@app.command("api-scan")
def api_scan(
    base_url: str = typer.Argument(None, help="Probe ONE server: liveness, OpenAPI document, real endpoint list, copy-ready steps. Omit to sweep localhost."),
    port: list[int] = typer.Option(None, "--port", "-p", help="Extra port(s) for the localhost sweep"),
    repo: str = typer.Option(".", "--repo", help="Repo whose config hints extra ports (server.port, compose mappings, PORT=)"),
):
    """NOOD_0201 — live API discovery, the api wok's `noodle probe`.

    With a URL: fetch the app's OpenAPI document from the well-known routes
    (/openapi.json, /v3/api-docs, ...) and print the REAL endpoints — the fix
    for authoring POST /greeting when the app serves /greeting/new. Without
    one: sweep the well-known localhost ports for live HTTP servers (the dev
    loop already hosts the app under test). Loopback only; nothing guessed —
    ambiguity comes back as questions."""
    from noodle import api_probe
    rep = api_probe.probe(base_url) if base_url \
        else api_probe.discover(ports=list(port) if port else None,
                                repo_root=repo)
    _json_out(rep)
    raise typer.Exit(0 if rep.get("ok") else 1)


@app.command("ticket")
def ticket_cmd(
    source: str = typer.Argument(..., help="JIRA issue JSON — a file path, '-' for stdin, or the JSON inline"),
    base_url: str = typer.Option(None, "--base-url", help="Base URL of the service under test. Omitted: discovered from a running localhost app"),
    discover: bool = typer.Option(True, "--discover/--no-discover", help="Sweep localhost and read the app's OpenAPI document to resolve the ticket's endpoints against what it really serves"),
):
    """NOOD_0201 — read a ticket payload; get one authorable goal per criterion.

    A workflow agent's raw JIRA issue (Atlassian Document Format, spec-link
    boilerplate, ACs as loose Given/When/Then prose) is the test plan — this
    reads it deterministically: no LLM, no network beyond the discovery probe,
    nothing written. Spec-link noise is stripped, criteria that cannot be shown
    through the API land in `not_automatable` with the reason, and each
    remaining criterion's endpoint is resolved against the routes the app
    REALLY serves — so a ticket saying POST /greeting authors against
    POST /greeting/new instead of a 404. Whatever is still missing (a payload,
    a base URL, an ambiguous route) comes back in `questions`."""
    from noodle import api_probe
    from noodle import ticket as ticket_mod
    text, _ = _arg_text(source)
    parsed = ticket_mod.parse(text)
    if not parsed.get("ok"):
        _json_out(parsed)
        raise typer.Exit(1)
    endpoints: list[str] = []
    if discover:
        if base_url is None:
            found = api_probe.discover(repo_root=".")
            cands = found.get("api_candidates") or []
            if len(cands) == 1:
                base_url = cands[0]
            else:
                parsed.setdefault("questions", []).extend(
                    found.get("questions") or [])
        if base_url:
            probed = api_probe.probe(base_url)
            endpoints = probed.get("endpoints") or []
            if probed.get("questions"):
                parsed["questions"].extend(probed["questions"])
    plan = ticket_mod.plan(parsed, endpoints=endpoints, base_url=base_url)
    _json_out({**plan, "ticket": parsed, "base_url": base_url,
               "endpoints_served": endpoints[:20]})
    raise typer.Exit(0 if plan.get("ok") else 1)


@app.command()
def task(
    text: str = typer.Argument(None, help="What you want, in plain English — 'write a test that ...', 'run the tests', 'did it pass'. Noodle picks the command. NOOD_0198 — a file path or '-' reads the text from there instead."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
    tag: str = typer.Option(None, "--tag", "-t", help="Filter a run to this tag"),
    headed: bool = typer.Option(False, "--headed", help="Visible browser (local demo; CI and containers are headless)"),
    no_serve: bool = typer.Option(False, "--no-serve", help="Skip hosting the reports afterwards"),
    show_contract: bool = typer.Option(False, "--contract", help="Print the calling contract (intents, prompt grammar, goal vocabulary, envelope schema) and exit — fetch this ONCE instead of learning the grammar through rejections."),
    as_json: bool = typer.Option(True, "--json/--no-json", help="One bounded JSON envelope (default) or a short human summary"),
):
    """NOOD_0191 — one door: free text in, one envelope out.

    Routes to the command you meant — generate / update / run / report /
    verdict — by a deterministic keyword scan, with no recognized verb
    defaulting to generate. Dispatches to commands that already exist; it
    adds no authoring logic and makes no LLM call.

    When the text can't be compiled it does NOT guess: the envelope carries
    `need` (the missing inputs), `grammar` and `next`, so a driving agent
    fills them in and re-sends. `--contract` returns that grammar up front."""
    from noodle.repl import task as _task
    if show_contract:
        _json_out(_task.contract())
        raise typer.Exit(0)
    if not text:
        raise typer.BadParameter("pass the task text, or --contract",
                                 param_hint="'TEXT'")
    # NOOD_0198 — the same door as `author --prompt`: an orchestrator that
    # wrote the ask to a file passes the path, not `"$(cat ...)"`.
    text, _ = _arg_text(text)
    result = _task.route(text, workspace=workspace, tag=tag,
                         headless=not headed, serve=not no_serve)
    if as_json:
        _json_out(result)
        raise typer.Exit(0 if result["ok"] else 1)
    typer.echo(f"  · intent: {result['intent']} ({result['confidence']})")
    if result.get("feature"):
        typer.echo(f"  · feature: {result['feature']}")
    if result.get("run"):
        r = result["run"]
        typer.echo(f"  {'✓' if result['ok'] else '✗'} "
                   f"{r.get('passed', 0)} passed, {r.get('failed', 0)} failed"
                   f" — {result.get('verdict')}")
    for url in result.get("reports") or []:
        typer.echo(f"    {url}")
    if result.get("error"):
        typer.echo(f"  ✗ {result['error']}")
    if result.get("need"):
        typer.echo(f"  → need: {', '.join(result['need'])}")
    for q in result.get("questions") or []:      # NOOD_0192 — scan intent
        typer.echo(f"  → ask: {q}")
    if result.get("next"):
        typer.echo(f"  → next: {result['next']}")
    raise typer.Exit(0 if result["ok"] else 1)


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
    text = Path(target).read_text(encoding="utf-8")
    est = _cost.estimate(text, model=model)
    if as_json:
        _json_out(est)
        return
    usd = (f"~${est['usd_input_floor']:.4f}" if est["usd_input_floor"] is not None
           else "pricing unknown (self-hosted/free?)")
    typer.echo(f"  💰 Estimate for {target}: {est['input_tokens']:,} input tokens | "
               f"{usd} input-cost floor (output tokens unknowable pre-run) | "
               f"model {est['model']}")


def _regression_workspace(quiet: bool = False) -> Path:
    """A fresh gitignored `regression_runs/<stamp>_<build>_<sha>/` in the
    engine CLONE (NOOD_0190 — it used to resolve against cwd and drop an
    un-gitignored folder wherever you happened to stand), scaffolded by the
    real `noodle init`. New folder every run: a reused workspace inherits the
    previous build's features and stops measuring generation."""
    from noodle import install_check
    vr = install_check.version_report()
    # The folder is stamped with the CHECKOUT's version, so a stale install
    # would file its results under code it never ran.
    if vr.get("mismatch"):
        typer.echo(f"Install records {vr['installed']} but this checkout is "
                   f"{vr['source']} — run `noodle update` first, else the "
                   "benchmark measures the old build under the new name.")
        raise typer.Exit(1)
    build = vr.get("source") or vr.get("installed") or "unknown"
    sha = (install_check.git_sha() or "nogit")[:7]
    root = (install_check.clone_root() or Path.cwd()) / "regression_runs"
    base = f"{datetime.now():%Y%m%d-%H%M%S}_{build}_{sha}"
    ws, n = root / base, 1
    while True:
        try:
            ws.mkdir(parents=True)   # mkdir IS the claim — atomic, so two
            break                    # same-second runs can't share a folder
        except FileExistsError:
            n += 1
            ws = root / f"{base}_{n}"
    if quiet:
        # The benchmark table is the output. `noodle init` prints ~30 lines of
        # scaffold inventory — evidence when you asked for a workspace
        # (--init), noise in front of the numbers you actually asked for.
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            init(path=str(ws))
    else:
        init(path=str(ws))
    return ws


def _write_verdict(verdict: dict, workspace: Path) -> dict:
    """verdict.json + verdict.html in the build folder, and verdict.html into
    the served reports dir too — so the ACs live at /verdict.html beside
    /allure-report/index.html and /rca.html."""
    from noodle import regression
    (workspace / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    html = regression.render_html(verdict)
    (workspace / "verdict.html").write_text(html, encoding="utf-8")
    verdict["saved"] = str(workspace / "verdict.json")
    try:
        reports = _paths.last_run_root(str(workspace.resolve())) / "reports"
        if reports.is_dir():
            (reports / "verdict.html").write_text(html, encoding="utf-8")
            verdict["served"] = str(reports / "verdict.html")
    except Exception:
        pass  # results file outside a workspace — the local verdict.html stands
    return verdict


@app.command("feature-regression")
def feature_regression(
    score_file: str = typer.Option(None, "--score", help="Re-score an existing run's results JSON instead of running the benchmark — prints the table and writes verdict.json/html next to it"),
    init_ws: bool = typer.Option(False, "--init", help="Only scaffold the fresh benchmark workspace under <clone>/regression_runs/<stamp>_<build>_<sha>/ and stop — don't run the benchmark"),
    as_json: bool = typer.Option(False, "--json", help="One bounded JSON payload instead of the table"),
):
    """Core-product regression benchmark (NOOD_0185): prove prompt → .feature
    generation is still fast and accurate after engine changes. Runs only when
    asked; nothing schedules it.

    No args → it RUNS (NOOD_0190): fresh workspace, both canonical prompts
    authored + run, one combined Allure + RCA + verdict served, benchmark
    table printed. Exit 0 = PASS, 1 = REGRESSED. Every number comes from the
    engine's own payload — generation time, run time, corrections (self-heal
    events, flaky retries, re-author passes), generated feature+POM lines.
    --init scaffolds the workspace without running; --score re-scores an
    existing results.json. Triage prose: `noodle docs feature-regression`."""
    from noodle import regression
    if score_file:
        # NOOD_0188 — score against the workspace holding the run's artifacts,
        # so the audit can cross-check green/verified against last_run.json.
        ws = Path(score_file).resolve().parent
        verdict = regression.score(json.loads(Path(score_file).read_text(encoding="utf-8")),
                                   workspace=str(ws))
    else:
        ws = _regression_workspace(quiet=not init_ws)
        if init_ws:
            typer.echo(f"\n  🧪 benchmark workspace: {ws} — new folder every "
                       "run; features, reports and verdicts all stay here")
            return
        from noodle import install_check
        results = {**regression.execute(str(ws)),
                   "engine": install_check.version_report().get("source")}
        (ws / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        verdict = regression.score(results, workspace=str(ws))
    _write_verdict(verdict, ws)
    if as_json:
        _json_out(verdict)
    else:
        typer.echo(regression.render_table(verdict))
    raise typer.Exit(0 if verdict["verdict"] == "PASS" else 1)


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
        Path(out).write_text(md, encoding="utf-8")
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
        result = _validate.check_feature(f.read_text(encoding="utf-8"), filename=str(f))
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
        result = _validate.check_feature(f.read_text(encoding="utf-8"), filename=str(f))
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
    url: str = typer.Argument(..., help="Page URL to probe (space/comma-separate several for one browser)"),
    json_out: bool = typer.Option(False, "--json", help="Compact author-evidence JSON (as probe_page)"),
    full: bool = typer.Option(False, "--full", help="With --json, the RAW uncapped payload"),
    timeout: int = typer.Option(15000, "--timeout", help="Per-page load timeout in ms"),
    click: list[str] = typer.Option(None, "--click", help="Control to click before a fresh snapshot (name or raw selector), repeatable — reveals only, never mutating buttons"),
    do_: list[str] = typer.Option(None, "--do", help="Transaction after --click, in order: 'enter <v> in <field>', 'select <opt> from <dropdown>', 'click <name>', 'switch to <new|original> tab'. REAL actions, deltas under revealed; {env:KEY} resolves; with --search on the landed page"),
    search: str = typer.Option(None, "--search", help="Run the site search and summarize the RESULTS page: new controls, the 'NN results' element + POM entry, count assertion"),
    suggest: str = typer.Option(None, "--suggest", help="Type this partial term per-character and capture the TYPEAHEAD rows + copy-ready steps"),
    pick: str = typer.Option(None, "--pick", help="With --search: bind 'any matching result' to ONE caption (ambiguity refuses, never guesses) and snapshot it; '*' = any"),
    follow: str = typer.Option(None, "--follow", help="With --suggest: click the captured suggestion row matching this text and summarize where it lands"),
    expect: list[str] = typer.Option(None, "--expect", help="Verify this text is on the landed page; repeatable, FOUND/NOT FOUND at the TOP"),
    compact: bool = typer.Option(False, "--compact", help="Author-critical evidence only (POM-needing controls, POM YAML, headings)"),
    section: str = typer.Option("all", "--section", help="One slice only: controls | pom | steps | headings | revealed | all"),
    max_controls: int = typer.Option(None, "--max-controls", help="Cap each control list at N (compact caps at 25)"),
    open_native: bool = typer.Option(False, "--open-native", help="Enumerate native <select> options and click-open custom comboboxes too"),
    max_reveal_depth: int = typer.Option(1, "--max-reveal-depth", help="With --open-native, levels of combobox-in-combobox to follow"),
    discover: bool = typer.Option(False, "--discover", help="Trigger NAMES unknown? Clicks bounded disclosure candidates, deltas under revealed. Only for an unnamed control gating needed UI"),
    find: str = typer.Option(None, "--find", help="Only controls/result-items matching this text, pre-cap — replaces payload greps"),
    brief: bool = typer.Option(False, "--brief", help="Step templates once, not one sentence per control"),
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
        payload = (result if full
                   else compact_payload(result, max_controls or 40, brief=brief))
        _json_out(payload)
    else:
        from noodle.agents.web.probe import render
        typer.echo(render(result, compact=compact, section=section,
                          max_controls=max_controls, brief=brief))
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
        return json.loads((Path(workspace) / _REPORT_PIDFILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_report_pids(workspace: str, data: dict) -> None:
    f = Path(workspace) / _REPORT_PIDFILE
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data) + "\n", encoding="utf-8")
    except OSError:
        pass  # registry is a nicety — never fail serving over it


def _pid_of(entry) -> int:
    """A registry value was a bare pid until NOOD_0161 gave it the served root
    and host (so a serve can be REUSED, not duplicated). Old pidfiles from a
    previous install are still on disk — read both shapes."""
    return entry["pid"] if isinstance(entry, dict) else entry


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        # NOOD_0195 — `os.kill(pid, 0)` below is the POSIX liveness idiom, and
        # on Windows it is actively destructive: signal 0 IS CTRL_C_EVENT
        # there, so Python calls GenerateConsoleCtrlEvent and the "probe"
        # delivers a Ctrl+C to that process GROUP. A stale registry entry
        # therefore interrupted whatever now shares the console — in CI that
        # was pytest itself (an async KeyboardInterrupt mid-run); on a tester's
        # machine it is their own shell. Every other signal value fares no
        # better: those route to TerminateProcess, so the probe would kill a
        # recycled pid belonging to a stranger. Opening a handle only reads.
        import ctypes
        from ctypes import wintypes
        QUERY_LIMITED_INFORMATION, STILL_ACTIVE, ACCESS_DENIED = 0x1000, 259, 5
        # use_last_error so get_last_error() is meaningful, and DECLARE the
        # signatures: ctypes defaults restype to a 32-bit int, which truncates
        # a 64-bit HANDLE and then fails in ways that surface as an unrelated
        # SystemError.
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        handle = k32.OpenProcess(QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # ERROR_ACCESS_DENIED — it exists, it just isn't ours.
            return ctypes.get_last_error() == ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            k32.CloseHandle(handle)
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
        base = Path(cwd_of[p]) if p in cwd_of else Path(".")
        if p in cwd_of:
            yield base
        # NOOD_0101 — -ww: with COLUMNS set (pytest sets it; so do some CI
        # shells), ps truncates piped output to that width, cutting off the
        # served path this scan exists to find.
        # NOOD_0185 — any directory on the command line, whoever spawned it:
        # `http.server --directory X` AND `noodle report serve X` (whose cwd is
        # wherever the run was launched, not the report tree — that's why
        # `report stop` outside the serving workspace found nothing to stop).
        args = subprocess.run(["ps", "-ww", "-p", str(p), "-o", "command="],
                              capture_output=True, text=True).stdout.split()
        for a in args:
            if a.startswith("-"):
                continue
            d = Path(a) if Path(a).is_absolute() else base / a
            if d.is_dir():
                yield d

    return {prt: p for prt, p in by_port.items()
            if any(_looks_like_report_dir(d) for d in _served_dirs(p))}


@report_app.command("stop")
def report_stop(
    port: int = typer.Option(None, "--port", "-p", help="Only stop the server on this port (default: all)"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace dir"),
):
    """Stop hosted report servers (Allure + RCA) — ones this workspace's
    .noodle/report_servers.json registry knows about, plus any other listening
    process serving a report tree, whatever workspace it was started from and
    whether it was `noodle report serve` or a raw `python -m http.server`.
    Registry entries whose process is already gone are pruned silently."""
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
        # NOOD_0177 — pid came straight out of .noodle/report_servers.json and
        # was never int()-checked, so a crafted registry made this signal
        # anything the user owns. os.kill(-1, SIGTERM) is the sharp case: POSIX
        # sends it to EVERY process the user can signal, turning a routine
        # `noodle report stop` into a session-wide kill.
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            typer.echo(f"  (port {prt}: ignoring non-numeric pid {pid!r})")
            continue
        if pid <= 1:
            typer.echo(f"  (port {prt}: refusing to signal pid {pid})")
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
                tail = log.read_text(errors="replace", encoding="utf-8")[-2000:].rstrip()
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
            # NOOD_0195 — EACCES counts as taken. http.server sets
            # allow_reuse_address, and Windows answers a second bind on a
            # SO_REUSEADDR socket with WSAEACCES ("access permissions"), not
            # WSAEADDRINUSE — so the fallback never fired there and the serve
            # dead-ended with exactly the wasted round trip NOOD_0134 removed.
            if e.errno not in (errno.EADDRINUSE, errno.EACCES) or port == 0:
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
    # NOOD_0177 — containment BEFORE rmtree. artifacts_root() is
    # Path(os.getenv("NOODLE_ARTIFACTS_DIR", "artifacts")), and pathlib's
    # absolute-RHS rule makes Path(workspace) / "/Users/you" evaluate to
    # "/Users/you" — so an absolute NOODLE_ARTIFACTS_DIR (or a pointer file
    # holding one) turned `noodle clean` into rm -rf of that path. No attacker
    # needed: a CI variable typo was enough.
    ws_resolved = Path(workspace).resolve()
    if not root.resolve().is_relative_to(ws_resolved):
        typer.secho(
            f"Refusing to clean {root.resolve()} — it is outside the workspace "
            f"({ws_resolved}). Check NOODLE_ARTIFACTS_DIR and "
            f"{_paths._POINTER}.", fg=typer.colors.RED)
        raise typer.Exit(1)
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
