"""Callable agent core (NOOD_0045 Phases 1–2).

The REPL (noodle/repl/repl.py) and the MCP server (noodle/mcp/server.py)
share this one API. Every function RETURNS data instead of printing, so any
transport can relay it. Engine invocations go through the CLI in a captured
subprocess — same isolation the REPL always used, but the text comes back to
the caller instead of leaking to this process's stdout.

Also home of the persistent agent state (artifacts/agent_state.json): what
the REPL kept in an in-process dict ("run it" pronoun memory) now survives
across processes, sessions and transports.
"""
import io
import json
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from noodle import config, counters
from noodle.reporting import paths as _paths

# --- persistent state --------------------------------------------------------

# Only durable facts persist. Transient flow control (autoran_feature — "skip
# the run right after an autorun") must die with the process, or a bare
# "run it" next session would wrongly report "already ran".
_DURABLE_KEYS = {"last_feature", "last_pom", "last_app",
                 "last_run_target", "last_run_at",
                 # NOOD_0156 — which features/apps were authored from a
                 # structured intent contract (and whether it blocked): the
                 # manual-fallback gate reads this to refuse auto-running
                 # hand-written Gherkin around a blocked goal.
                 "intent_contracts"}


def _state_path(workspace: str = ".") -> Path:
    return Path(workspace) / _paths.artifacts_root() / "agent_state.json"


def load_state(workspace: str = ".") -> dict:
    try:
        data = json.loads(_state_path(workspace).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict, workspace: str = ".") -> None:
    p = _state_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({k: v for k, v in state.items()
                             if k in _DURABLE_KEYS}, indent=2) + "\n")


# --- shared helpers ----------------------------------------------------------

def normalize_url(url: str) -> str:
    """Bare host -> https:// so 'youtube.com' works in a create request.
    file:// passes through (NOOD_0115 — local fixture pages for inspect/probe)."""
    return url if re.match(r"^(https?|file)://", url, re.I) else f"https://{url}"


def _engine(*args, workspace: str = ".") -> subprocess.CompletedProcess:
    """Invoke the engine CLI in the workspace, output captured for the caller.

    NOOD_0055 — bounded by NOODLE_ENGINE_TIMEOUT seconds (default 900, 0
    disables): a wedged browser run must not hang an MCP tool call forever.
    On timeout the child is killed and a synthetic rc-124 result comes back
    instead of an exception, so transports report it like any other failure."""
    timeout = float(os.getenv("NOODLE_ENGINE_TIMEOUT", "900") or "0") or None
    if args and args[0] == "run":
        counters.bump("browser_launch")     # every engine run is one browser
    cmd = [sys.executable, "-m", "noodle.cli", *args]
    try:
        return subprocess.run(cmd, cwd=workspace, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        return subprocess.CompletedProcess(
            cmd, 124, stdout=out,
            stderr=err + f"\nnoodle engine timed out after {timeout:g}s "
                         f"(NOODLE_ENGINE_TIMEOUT)")


def _capture(fn, *args, **kwargs):
    """Run a print()-ing legacy function, returning (result, its stdout).
    ponytail: cheaper than refactoring generate.py's prints into returns —
    revisit if a second consumer needs structured sub-events, not text."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


def find_feature(cfg: dict, workspace: str, name: str) -> str | None:
    """Feature or directory lookup by path, stem substring, or dir name
    (moved from repl.py so the MCP server gets the same resolution the REPL
    always had). Directories are valid run targets — the engine hands them
    to behave, which runs every .feature underneath."""
    fdir = Path(workspace) / cfg["tests_dir"]
    if "/" in name or "\\" in name or name.lower().endswith(".feature"):
        for base in (Path(workspace), fdir):
            candidate = base / name
            if candidate.is_file() or candidate.is_dir():
                try:
                    return str(candidate.relative_to(workspace))
                except ValueError:
                    return str(candidate)  # name was already absolute
        return None
    if not fdir.is_dir():
        return None
    for f in fdir.rglob("*.feature"):
        if name.lower() in f.stem.lower():
            return str(f.relative_to(workspace))
    for d in fdir.rglob(name):
        if d.is_dir():
            return str(d.relative_to(workspace))
    return None


def resolve_target(target: str | None, workspace: str = ".") -> dict:
    """Which feature does "run the test" mean? Resolution order (NOOD_0045
    Phase 2): explicit target → persisted last_feature → most recently
    modified .feature under tests_dir → error with what exists."""
    counters.bump("target_resolution")
    cfg = config.load(workspace)
    if target:
        feat = find_feature(cfg, workspace, target)
        return ({"feature": feat} if feat
                else {"error": f"no feature matched {target!r}"})
    state = load_state(workspace)
    feat = state.get("last_feature")
    if feat and (Path(workspace) / feat).is_file():
        return {"feature": feat}
    fdir = Path(workspace) / cfg["tests_dir"]
    candidates = sorted(fdir.rglob("*.feature"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return {"feature": str(candidates[0].relative_to(workspace))}
    return {"error": f"no .feature files under {cfg['tests_dir']}"}


# --- the core API ------------------------------------------------------------

def create_test(description: str, url: str, *, use_llm: bool = False,
                overwrite: bool = False, workspace: str = ".",
                append_to: str | None = None) -> dict:
    """Generate a .feature + POM skeleton. Rule-based by default; use_llm
    routes through NOODLE_MODEL. append_to (a feature stem) adds this test
    case's scenario(s) to that existing feature instead of a new file —
    rule-based generation only; ignored when use_llm is set. Persists
    last_feature/pom/app state."""
    from noodle.repl import generate
    cfg = config.load(workspace)
    if use_llm:
        # NOOD_0080 — long-lived MCP/REPL process: scope the ledger to this
        # generation so the llm_cost reported below is this call's, not the
        # session's running total.
        from noodle.llm import cost as _cost
        _cost.reset()
    gen = generate.generate_llm if use_llm else generate.generate
    kwargs = {"overwrite": overwrite}
    if append_to and not use_llm:
        kwargs["append_to"] = append_to
    result, output = _capture(gen, description, normalize_url(url),
                              cfg, workspace, **kwargs)
    if result is None:
        return {"ok": False, "output": output.strip()}
    feat, pom = result
    # NOOD_0155 — wok tagging: the templates/LLM default to @web; when the
    # description (or the generated steps) prove another wok ("load test the
    # checkout", "@api"), swap the engine's own default for the right routing
    # tag. Engine-generated content only — append_to targets a file whose
    # existing tags are author intent, so it is never retagged.
    wok_tag = None
    if not append_to:
        from noodle import wok as _wok
        feat_text = Path(feat).read_text()
        inferred = _wok.infer_tag(description, feat_text)
        if inferred not in _wok.routing_tags_in(feat_text):
            Path(feat).write_text(_wok.retag_feature(feat_text, inferred))
            wok_tag = inferred
    texts = [Path(feat).read_text()]
    if Path(pom).exists():
        texts.append(Path(pom).read_text())
    runnable = not any(generate._PLACEHOLDER_RE.search(t) for t in texts)
    # NOOD_0055 — persist workspace-relative paths, same shape write_feature
    # stores: resolve_target rejoins them with the workspace, and a cwd-relative
    # path breaks as soon as another process (MCP server vs REPL) loads the state.
    rel_feat = os.path.relpath(feat, workspace)
    rel_pom = os.path.relpath(pom, workspace)
    state = load_state(workspace)
    state.update(last_feature=rel_feat, last_pom=rel_pom,
                 last_app=Path(feat).parent.parent.name)
    save_state(state, workspace)
    out = {"ok": True, "feature": rel_feat, "pom": rel_pom,
           "runnable": runnable, "output": output.strip()}
    if wok_tag:
        out["wok_tag"] = wok_tag
    if use_llm:
        from noodle.llm import cost as _cost
        if c := _cost.summary():
            out["llm_cost"] = c
    return out


def run_test(target: str | None = None, *, tag: str | None = None,
             workspace: str = ".", headless: bool | None = None,
             browser: str | None = None, retries: int | None = None,
             parallel: int | None = None,
             parallel_scheme: str = "feature",
             _resolved: str | None = None) -> dict:
    """Run a feature (or a tag filter). target=None resolves via state (see
    resolve_target). headless=None defers to the workspace's .env/noodle.yaml
    default; True/False forces --headless/--headed — most MCP hosts have no
    display, so a caller running headless-only needs a way to force it
    without editing the workspace's .env out-of-band (NOOD_0059).
    browser/retries/parallel/parallel_scheme mirror the CLI flags (NOOD_0084);
    None defers to workspace config. Validated here — MCP callers otherwise
    only get the CLI's usage-text failure back.
    Returns exit code + the structured last-run result."""
    from noodle.cli import _VALID_BROWSERS
    if browser is not None and browser not in _VALID_BROWSERS:
        return {"ok": False, "error": f"unsupported browser {browser!r}; "
                f"valid: {', '.join(sorted(_VALID_BROWSERS))}"}
    if parallel_scheme not in ("feature", "scenario"):
        return {"ok": False, "error": f"unsupported parallel_scheme "
                f"{parallel_scheme!r}; valid: feature, scenario"}
    mode_flag = [] if headless is None else (["--headless"] if headless else ["--headed"])
    if browser:
        mode_flag += ["--browser", browser]
    if retries is not None:
        mode_flag += ["--retries", str(retries)]
    if parallel:
        mode_flag += ["--parallel", str(parallel), "--parallel-scheme", parallel_scheme]
    ws = Path(workspace)
    if tag:
        cfg = config.load(workspace)
        proc = _engine("run", cfg["tests_dir"], "--tag", tag, *mode_flag, workspace=workspace)
        ran = f"tag:{tag}"
    elif target is None and (ws / "features").is_dir() and not (ws / "noodle.yaml").exists():
        # NOOD_0086 — workspace points at an app package (noodle_tests/app1):
        # run just that app. The CLI re-roots onto the real workspace (nearest
        # noodle.yaml ancestor) and routes artifacts into <app>/report/.
        proc = _engine("run", *mode_flag, workspace=workspace)
        ran = str(ws)
    else:
        if _resolved is None:                 # NOOD_0131 — run_and_report
            r = resolve_target(target, workspace)   # passes its resolution in
            if "error" in r:
                return {"ok": False, **r}
            _resolved = r["feature"]
        ran = _resolved
        proc = _engine("run", ran, *mode_flag, workspace=workspace)
    state = load_state(workspace)
    state["last_run_target"] = ran
    state["last_run_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state, workspace)
    result = last_result(workspace)
    result.update(ok=proc.returncode == 0, target=ran,
                  exit_code=proc.returncode,
                  output=(proc.stdout + proc.stderr)[-4000:].strip())
    # NOOD_0147 — engine-side failure-trigger detection: a fired trigger rides
    # the payload the driving agent already reads, so the session-end
    # diagnostic happens without any always-on instruction text.
    from noodle import diagnostics as _diag
    fired = _diag.track_run(workspace, ran, failed=proc.returncode != 0)
    if fired:
        result["diagnostic_due"] = _diag.due_hint(fired)
    return result


def last_result(workspace: str = ".") -> dict:
    """Structured last-run result from allure-results (counts, failures,
    wall time) — the data `noodle summary` formats, returned as a dict."""
    from noodle.reporting import summary as _summary
    results = str(_paths.last_run_root(workspace) / "allure-results")
    result = _summary.collect(results)
    # NOOD_0080 — surface the run's own LLM spend to MCP/REPL callers so
    # driving agents can relay it in chat (written by hooks.after_all).
    from noodle.llm import cost as _cost
    llm_cost = _cost.load_total(results)
    if llm_cost:
        result["llm_cost"] = llm_cost
    return result


# --- NOOD_0128: preflight, one-shot run/report, atomic authoring ------------

def _app_dir_for(feature_rel: str, workspace: str) -> Path | None:
    """The app package dir a run target points at — the `features/` parent when
    the target is a .feature, or the dir itself when it already holds a
    `features/`. Its `resources/` is where env + secrets live for preflight."""
    p = (Path(workspace) / feature_rel).resolve()
    if (p / "features").is_dir():
        return p
    for parent in p.parents:
        if parent.name == "features":
            return parent.parent
    return None


def _resolved_env(app_dirs, workspace: str) -> dict:
    """The env values a run would see, WITHOUT launching behave: real OS env
    (wins), then root .env/secrets.env/environments.yaml, then EACH involved app
    package's resources/*. Mirrors hooks.before_all's setdefault precedence.
    Accepts one app dir or a list (a multi-app dir run touches several)."""
    import yaml
    from dotenv import dotenv_values
    env = {k.upper(): v for k, v in os.environ.items()}

    def _dotenv(p: Path):
        if p.is_file():
            for k, v in dotenv_values(p).items():
                if v is not None:
                    env.setdefault(k.upper(), v)

    def _yaml(p: Path):
        if not p.is_file():
            return
        try:
            data = yaml.safe_load(p.read_text()) or {}
        except Exception:
            return
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, (str, int, float)):
                    env.setdefault(str(k).upper(), str(v))

    ws = Path(workspace)
    _dotenv(ws / ".env")
    _dotenv(ws / "secrets.env")
    _yaml(ws / "environments.yaml")
    if isinstance(app_dirs, (str, Path)) or app_dirs is None:
        app_dirs = [app_dirs] if app_dirs else []
    for app_dir in app_dirs:
        app_dir = Path(app_dir)
        res = app_dir / "resources"
        _dotenv(res / ".env")
        _dotenv(res / "secrets.env")
        _dotenv(res / f"{app_dir.name}_secrets.env")
        for envy in res.glob("*environments.yaml"):
            _yaml(envy)
    return env


_TEMPLATE_MARKER_RE = re.compile(r"^<[^>]*>$")


def _is_placeholder(value) -> bool:
    from noodle import log
    if value is None:
        return True
    v = str(value).strip()
    return (not v or v.upper() in log._PLACEHOLDERS
            or bool(_TEMPLATE_MARKER_RE.match(v)))


# NOOD_0130 — temporary prompt-credential path (docs/todo/secret-broker.md is the
# future masked-broker replacement). Values are written ONLY to the app-local
# gitignored *_secrets.env and never returned/echoed.
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


def _dotenv_quote(v: str) -> str:
    """Quote a secret value so python-dotenv reads it back byte-for-byte —
    bare when safe, double-quoted (escaping \\ and ") when it holds whitespace,
    #, or a quote. Verified round-trip in test_nood_0130."""
    if v and not re.search(r"[\s#'\"\\]", v):
        return v
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _validate_secret_values(values: dict) -> tuple[dict, str | None]:
    """Env-var-name-shaped keys, non-empty string values. Returns (clean, error).
    Rejects on the FIRST bad entry with a key-name-only message — never the value."""
    clean = {}
    for k, v in (values or {}).items():
        if not isinstance(k, str) or not _ENV_KEY_RE.match(k):
            return {}, f"invalid secret key {k!r} — must match [A-Za-z_][A-Za-z0-9_]*"
        if not isinstance(v, str) or v == "":
            return {}, f"secret {k} has an empty value — omit the key or supply a real value"
        clean[k] = v
    return clean, None


def _apply_secrets(base_text: str, values: dict, placeholder_keys) -> str:
    """Merge into a *_secrets.env body: update each `values` key in place (append
    if absent), then append `placeholder_keys` still missing as bare `KEY=`.
    Comments, unrelated keys and ordering survive."""
    remaining = dict(values)
    present, out = set(), []
    for line in base_text.splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m:
            present.add(m.group(1))
            if m.group(1) in remaining:
                out.append(f"{m.group(1)}={_dotenv_quote(remaining.pop(m.group(1)))}")
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={_dotenv_quote(v)}")
        present.add(k)
    for k in placeholder_keys:
        if k not in present:
            out.append(f"{k}=")
            present.add(k)
    text = "\n".join(out)
    return text + "\n" if text else ""


def preflight(target: str | None = None, workspace: str = ".", *,
              resolved_feature: str | None = None) -> dict:
    """NOOD_0128 — the secret/config readiness gate the reviewed session
    lacked: check every {env:KEY} a run would reference resolves to a REAL
    value before any browser launches. A missing key or a placeholder value
    (CHANGE_ME/empty/<marker>) is an error, so run_and_report returns it
    instead of burning a 50s browser run that can only fail at login. Also
    carries the redundant-post-nav-wait warnings. Skipped (ok=True) when the
    target can't resolve to concrete feature files (e.g. a tag run).
    resolved_feature (NOOD_0131) skips re-resolution when the caller already
    resolved the run target — run_and_report resolves once for preflight AND
    the run."""
    if resolved_feature is None:
        r = resolve_target(target, workspace)
        resolved_feature = r.get("feature")
        if not resolved_feature:
            return {"ok": True, "skipped": r.get("error", "no concrete target"),
                    "missing_secret_keys": [], "errors": [], "warnings": []}
    feat = resolved_feature
    root = (Path(workspace) / feat)
    files = ([root] if root.is_file()
             else sorted(root.rglob("*.feature")) if root.is_dir() else [])
    # Each feature may live in its own package — load every involved app's env.
    app_dirs = []
    for f in files:
        for parent in f.resolve().parents:
            if parent.name == "features":
                if parent.parent not in app_dirs:
                    app_dirs.append(parent.parent)
                break
    env = _resolved_env(app_dirs, workspace)
    refs, warnings = [], []
    from noodle.repl import validate
    for f in files:
        text = f.read_text()
        for k in validate.env_refs(text):
            if k not in refs:
                refs.append(k)
        warnings += validate.redundant_post_nav_waits(text)
    missing, errors = [], []
    for key in refs:
        if _is_placeholder(env.get(key)):
            missing.append(key)
            errors.append(
                f"{{env:{key}}} is {'missing' if key not in env else 'a placeholder'} "
                f"— set it in the app's resources/*_secrets.env or environments.yaml "
                f"before running.")
    return {"ok": not errors, "target": feat, "missing_secret_keys": missing,
            "errors": errors, "warnings": warnings}


def run_and_report(target: str | None = None, *, tag: str | None = None,
                   workspace: str = ".", headless: bool | None = None,
                   browser: str | None = None, retries: int | None = None,
                   parallel: int | None = None, parallel_scheme: str = "feature",
                   preflight_check: bool = True, compact_rca: bool = True,
                   serve_reports: bool = False) -> dict:
    """One bounded payload for the whole run→report→(serve) loop (NOOD_0128).
    Preflights secrets first (no browser on missing creds), runs, folds the
    compact RCA in on red, and optionally serves — so a driving agent needs one
    call, not the run + get_rca + report + serve chain the reviewed session
    used. NOOD_0131 — one target resolution feeds preflight AND the run, and
    the reports the run hook already built are freshness-checked once (rebuilt
    only when missing/stale — parallel runs and allure-less environments still
    get repaired) instead of unconditionally regenerated; serving reuses that
    verified root without a second check."""
    from noodle.reporting import builder
    ws = workspace
    # NOOD_0086 app-package runs (workspace IS the app dir) resolve inside
    # run_test/the CLI — everything else resolves exactly once, here.
    app_pkg_run = (target is None and not tag and (Path(ws) / "features").is_dir()
                   and not (Path(ws) / "noodle.yaml").exists())
    resolved = None
    if not tag and not app_pkg_run:
        r = resolve_target(target, ws)
        if "error" in r:
            return {"ok": False, **r}
        resolved = r["feature"]
    if preflight_check and not tag:
        pf = (preflight(workspace=ws, resolved_feature=resolved) if resolved
              else preflight(None, ws))
        if not pf["ok"]:
            return {"ok": False, "target": pf.get("target"), "preflight": pf,
                    "error": "preflight failed — no browser launched; fix "
                    f"missing/placeholder secrets: {', '.join(pf['missing_secret_keys'])}"}
    result = run_test(target, tag=tag, workspace=ws, headless=headless,
                      browser=browser, retries=retries, parallel=parallel,
                      parallel_scheme=parallel_scheme, _resolved=resolved)
    root = _paths.last_run_root(ws)
    reports = root / "reports"
    builder.ensure_fresh_reports(str(root / "allure-results"), str(reports))
    idx = reports / "allure-report" / "index.html"
    result["report"] = str(idx) if idx.is_file() else None
    result["rca_html"] = str(reports / "rca.html")
    result["rca_md"] = str(reports / "rca.md")
    # NOOD_0156 — a green-but-unverified run gets the compact RCA too: the
    # passed-with-healing lines are the evidence an agent needs before it may
    # claim the requested behavior actually passed (failed == 0 AND
    # verified == true, per docs/agent-playbook.md).
    if compact_rca and (not result.get("ok")
                        or result.get("verified") is False):
        result["rca_compact"] = rca(ws, compact=True)
    if serve_reports:
        served = serve_report(workspace=ws, ensure_fresh=False)
        if served.get("ok"):
            result["served"] = {k: v for k, v in served.items() if k != "ok"}
    return result


def _norm_app(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "app").lower()).strip("_") or "app"


def _model_configured(workspace: str) -> bool:
    """Is an LLM fallback available? NOODLE_MODEL in the process env or the
    workspace .env — the same value hooks.before_all loads before a run, so an
    unmatched step's runtime fallback is real, not wishful."""
    if os.getenv("NOODLE_MODEL"):
        return True
    env_file = Path(workspace) / config.load(workspace).get("env_file", ".env")
    if env_file.is_file():
        from dotenv import dotenv_values
        return bool(dotenv_values(env_file).get("NOODLE_MODEL"))
    return False


def _planner_verdict(result: dict, model_calls: int) -> dict:
    """NOOD_0169 — the bounded planner's typed terminal outcome for a prompt
    flow, derived from the one author/probe/run transaction (the state
    machine never loops engine-side; repair is one caller-driven transition
    keyed by next_action, with probe evidence persisted in
    artifacts/probe_goal.json and the intent contract in agent state)."""
    author = result.get("author") if isinstance(result.get("author"), dict) \
        else result
    run = result.get("run") if isinstance(result.get("run"), dict) else None
    blocking = author.get("blocking") or []
    if blocking:
        state = ("EXTERNAL_FAILURE"
                 if author.get("next_action") == "external_app_failure"
                 else "EVIDENCE_MISSING")
    elif run is None or run.get("skipped"):
        state = "READY"
    elif run.get("ok") and run.get("verified") is not False:
        state = "VERIFIED"
    else:
        state = "RUN_FAILED"
    out = {"state": state,
           "budgets": {"interpretation_model_calls": model_calls,
                       "probes": 1 if author.get("source") == "goal" else 0,
                       "runs": 0 if run is None or run.get("skipped") else 1}}
    if blocking:
        out["unresolved"] = blocking[0]
    if author.get("next_action"):
        out["next_action"] = author["next_action"]
    return out


def author_test(*, prompt: str | None = None,
                app_name: str | None = None, base_url: str | None = None,
                feature_path: str | None = None,
                goal: dict | None = None,
                feature_content: str | None = None, **kw) -> dict:
    """NOOD_0169 — the public author door, adding `prompt` mode: plain-
    English steps compiled through the three-pass deterministic prompt
    compiler (see prompt_expander). Routing is bounded: deterministic fast
    path first; contextual dataflow only for incomplete clauses; at most ONE
    model interpretation call (NOODLE_MODEL) for clauses outside the grammar
    — typed conflicts never go to the model. Every mode passes the same
    intent-contract review BEFORE any browser launches, and the payload
    carries `prompt_expansion` (translation_mode, clause coverage,
    provenance-tagged inferences) plus a `planner` terminal verdict. A
    prompt containing a URL also derives app_name/base_url/feature_path, so
    the whole call can be just {prompt, run_after_author}. Everything else
    defers unchanged to the documented transaction below
    (_author_test_impl)."""
    expansion, model_calls = None, 0
    if prompt is not None:
        if goal is not None or feature_content is not None:
            return {"ok": False, "error":
                    "prompt is mutually exclusive with goal/feature_content"}
        from noodle.repl import goal as goal_mod
        from noodle.repl import prompt_expander
        ws = kw.get("workspace", ".")
        exp = prompt_expander.expand(prompt, base_url=base_url)
        if not exp["ok"] and exp.get("unresolved") \
                and not exp.get("conflicts") and _model_configured(ws):
            # one interpretation call, only for grammar misses — a typed
            # conflict is a user contradiction no model may guess past
            model_calls = 1
            try:
                exp = prompt_expander.model_fallback(prompt,
                                                     base_url=base_url)
            except Exception as e:
                exp = {**exp, "error": exp["error"]
                       + f" (model fallback failed: {e})"}
        if exp["ok"]:
            review = prompt_expander.review_contract(exp)
            if not review["ok"]:
                exp = {**exp, "ok": False,
                       "error": "intent-contract review failed — no browser "
                       "launched: " + "; ".join(review["problems"])}
        if not exp["ok"]:
            needs = bool(exp.get("unresolved")) and not exp.get("conflicts")
            return {"ok": False, "error": exp["error"],
                    "needs_interpretation": needs,
                    "unrecognized_steps": exp.get("unrecognized") or [],
                    "unresolved": exp.get("unresolved") or [],
                    "conflicts": exp.get("conflicts") or [],
                    "assumptions": exp.get("assumptions") or [],
                    "example": goal_mod.EXAMPLE,
                    "vocabulary": goal_mod.vocabulary(),
                    "planner": {"state": ("NEEDS_INTERPRETATION" if needs
                                          else "CONTRACT_BLOCKED"),
                                "budgets": {"interpretation_model_calls":
                                            model_calls,
                                            "probes": 0, "runs": 0}}}
        goal = exp["goal"]
        base_url = base_url or exp["base_url"]
        app_name = app_name or exp["app_name"]
        feature_path = feature_path or exp["feature_path"]
        expansion = {"goal": goal, "assumptions": exp["assumptions"],
                     "translation_mode": exp.get("translation_mode"),
                     "coverage": exp.get("coverage") or [],
                     "inferences": exp.get("inferences") or [],
                     "unresolved": []}
    if not (app_name and base_url and feature_path):
        return {"ok": False, "error":
                "app_name, base_url and feature_path are required "
                "(a prompt with a 'go to <url>' step derives all three)"}
    result = _author_test_impl(app_name=app_name, base_url=base_url,
                               feature_path=feature_path, goal=goal,
                               feature_content=feature_content, **kw)
    if expansion and isinstance(result, dict):
        result["prompt_expansion"] = expansion
        if result.get("author") or result.get("blocking") is not None:
            result["planner"] = _planner_verdict(result, model_calls)
    return result


def _author_test_impl(*, app_name: str, base_url: str, feature_path: str,
                feature_content: str | None = None,
                pom_content: str | None = None,
                environment_values: dict | None = None,
                required_secret_keys: list[str] | None = None,
                secret_values: dict | None = None,
                goal: dict | None = None, run_after_author: bool = False,
                overwrite: bool = False,
                allow_unverified_intent: bool = False,
                workspace: str = ".") -> dict:
    """NOOD_0128/0129 — write a whole test package in one transaction:
    locate/create the app package (no sample_app copy), write environments.yaml
    + POM + feature, create ONLY missing secret placeholders, validate, and roll
    back on any write failure — restoring every ORIGINAL byte, not just files we
    created (NOOD_0129). Replaces the copy→rename→4-edits→validate round-trips
    the reviewed session spent.

    NOOD_0130 — `secret_values` (a {KEY: value} mapping from the ORIGINAL user
    prompt) is written ONLY into the app-local gitignored `<app>_secrets.env`,
    preserving unrelated keys/comments; values are never echoed or returned.
    This is a deliberate, temporary transcript-risk acceptance until the masked
    broker (docs/todo/secret-broker.md) ships.

    Returns paths, validation, warnings, missing_secret_keys, the derived
    `base_url_key`, and a `ready` flag with `blocking` reasons. `ready: true`
    means: Gherkin parsed, every step matched, POM selector scope passed, and
    every {env:KEY} the feature references resolves to a real value (NOOD_0131)
    — a separate `validate --resolve`/`preflight` call adds nothing.

    NOOD_0137/0139 constrained mode — `goal` (mutually exclusive with
    feature_content; an OBJECT, not a string — goal.EXAMPLE is the minimal
    valid one, and every rejection returns it): the engine runs ONE
    goal-scoped probe, deterministically compiles the feature + POM from the
    validated goal and probe evidence (see noodle/repl/goal.py). Every goal
    action target is POM'd from its probe selector; a control reachable only
    after an explicit reveal click requires that click first; a check anchored
    after data the probe never entered is kept but labelled runtime_asserted.
    NOOD_0141 — {do: suggest, term, option} is the typeahead pick: the probe
    captures the suggestion list for `term`, the requested option must be
    among it (canonical page spelling wins), and the compiler emits the
    intent assertion + the composite suggestion step; checks after it are
    runtime_asserted (the click-through only happens at run time).
    An unprovable requested action/check blocks — never dropped or broadened.
    `run_after_author=true` then runs ONCE (headless, retries=0), serves both
    reports, and forces failure when 0 scenarios passed — one bounded
    {author, run} payload, zero extra model round-trips."""
    import yaml

    from noodle.repl import generate, validate
    if (feature_content is None) == (goal is None):
        return {"ok": False,
                "error": "pass exactly one of feature_content or goal"}
    cfg = config.load(workspace)
    # Reuse an existing package for this URL, else a fresh web/<app> package —
    # same layout generate.py produces (docs/feature-packages.md).
    existing = generate._app_from_existing_url(base_url, cfg, workspace)
    app = existing or _norm_app(app_name)
    app_dir = (Path(workspace) / cfg["tests_dir"] / "web" / app).resolve()
    features_dir = app_dir / "features"
    stem = Path(feature_path).name
    stem = stem[:-8] if stem.endswith(".feature") else stem
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", stem) or "test"
    feat_dest = (features_dir / f"{stem}.feature").resolve()
    if not feat_dest.is_relative_to(features_dir):
        return {"ok": False, "error": "feature_path escapes the app features/ dir"}

    # NOOD_0137 — constrained goal mode: validate the goal (no browser on a
    # malformed one), run the ONE goal-scoped probe, compile deterministically.
    goal_ev = None
    nav_env: list[tuple[str, str]] = []
    if goal is not None:
        from noodle.repl import goal as goal_mod
        # NOOD_0156 follow-up — canonicalize obvious loose input (free-text
        # dismissals, implicit destinations) before rejecting anything; every
        # rewrite is echoed back as goal_normalized.
        goal, norm_notes = goal_mod.normalize(goal)
        errs = goal_mod.validate(goal)
        if errs:
            # NOOD_0161 — ship the minimal valid goal WITH the errors: a
            # rejection that only names what's wrong costs a schema-recovery
            # round trip (CLI help, docs queries) before the retry.
            return {"ok": False,
                    "error": "invalid goal: " + "; ".join(errs),
                    "example": goal_mod.EXAMPLE,
                    # NOOD_0169 — the full key tables beside the minimal
                    # example: schema recovery in zero further round trips.
                    "vocabulary": goal_mod.vocabulary(),
                    **({"goal_normalized": norm_notes} if norm_notes else {})}
        if feat_dest.exists() and not overwrite:
            return {"ok": False, "error":
                    f"{feat_dest.name} exists — pass overwrite=true to replace"}
        # NOOD_0156 — ordered navigation contract: probe EVERY requested URL
        # in order (one browser, state carries), interacting only on the last
        # — earlier URLs are setup navigation, not action pages.
        nav_env = goal_mod.navigation_env(goal, app)
        probe_urls = " ".join(u for _, u in nav_env) if nav_env else base_url
        probe_result = probe_page(
            probe_urls, act_on="last" if len(nav_env) > 1 else "each",
            **goal_mod.probe_args(goal))
        # Raw snapshot goes to artifacts/debug, never the model-visible payload.
        dbg = Path(workspace) / _paths.artifacts_root() / "probe_goal.json"
        try:
            dbg.parent.mkdir(parents=True, exist_ok=True)
            dbg.write_text(json.dumps(probe_result, indent=2, default=str))
        except OSError:
            pass
        goal_ev = goal_mod.evidence(goal, probe_result)
        # NOOD_0156 — automatic postcondition synthesis: a goal with actions
        # but no checks gets an explicit generated `Then` derived from the
        # last meaningful action + probe evidence (emitted into the .feature,
        # never a hidden runtime check); when no deterministic postcondition
        # exists, the goal BLOCKS with suggested checks instead of compiling
        # an assertion-free flow. allow_no_assertion opts out deliberately.
        synth = goal_mod.infer_postcondition(goal, goal_ev)
        generated_checks = synth["generated"]
        if generated_checks:
            goal = dict(goal, actions=synth["actions"],
                        checks=synth["checks"])
            goal_ev = goal_mod.evidence(goal, probe_result)
        goal_ev["blocking"] = goal_ev["blocking"] + synth["blocking"]
        feature_content, pom_content = goal_mod.compile_goal(
            goal, goal_ev, app.upper(),
            nav_keys=[k for k, _ in nav_env] or None)

    # NOOD_0155 — wok tagging: authored content that already carries a routing
    # tag is caller intent and stays untouched; content with none gets the
    # inferred tag (goal wording + step signals) so it runs in the right wok
    # — and validates below with that wok's grammar priority.
    from noodle import wok as _wok
    feature_content, wok_tag_added = _wok.ensure_tag(
        feature_content,
        description=(goal or {}).get("scenario", "") if isinstance(goal, dict) else "")

    # Validate BEFORE touching disk — a parse error means nothing gets written.
    check = validate.check_feature(feature_content)
    if check["error"]:
        return {"ok": False, "error": f"not valid Gherkin: {check['error']}"}
    unmatched_steps = validate.unmatched(check)
    llm_required = validate.llm_image_steps(feature_content)
    if pom_content is not None:
        try:
            pom_data = yaml.safe_load(pom_content)
        except Exception as e:
            return {"ok": False, "error": f"POM is not valid YAML: {e}"}
        if pom_data is not None and not isinstance(pom_data, dict):
            return {"ok": False, "error": "POM must be a YAML mapping "
                    f"(key: selector), got {type(pom_data).__name__}"}
    supplied_secrets, secret_err = _validate_secret_values(secret_values)
    if secret_err:
        return {"ok": False, "error": secret_err}
    if feat_dest.exists() and not overwrite:
        return {"ok": False,
                "error": f"{feat_dest.name} exists — pass overwrite=true to replace"}

    content = feature_content if feature_content.endswith("\n") else feature_content + "\n"
    if llm_required:
        content = validate.annotate_llm_image_steps(content)

    res_dir = app_dir / "resources"
    env_path = res_dir / f"{app}_environments.yaml"
    secrets_path = res_dir / f"{app}_secrets.env"
    pom_path = res_dir / "pageobjects" / f"{stem}_pom.yaml"

    # environments.yaml — merge (keep existing keys), always set the app base
    # URL. NOOD_0135 — store the FULL normalized URL (path/query/fragment
    # included), never just scheme://netloc: origin-only storage sent the first
    # run to the host root instead of the requested page and turned a
    # navigation bug into an apparent locator failure.
    supplied_url = normalize_url(base_url)
    env_map = {}
    if env_path.is_file():
        try:
            env_map = yaml.safe_load(env_path.read_text()) or {}
        except Exception:
            env_map = {}
    env_map[app] = supplied_url
    # NOOD_0156 — the navigation contract's ordered URLs live here; the
    # compiled feature carries only {env:KEY} references.
    for k, v in nav_env:
        env_map[k] = normalize_url(v)
    for k, v in (environment_values or {}).items():
        env_map[k] = v

    # NOOD_0144 — a re-authored package must not accumulate dead config: the
    # reviewed session's repeated re-authors left stale env keys behind, and
    # the next lap kept resolving against them. A key is stale when NO feature
    # or POM in the app references it (and it isn't the app URL key or a value
    # this very call supplied). Goal mode owns the whole package, so with
    # overwrite it PRUNES stale keys; feature mode (caller-owned content,
    # NOOD_0129 merge contract) only reports them.
    stale_env_keys = []
    if env_map:
        refs = {r.upper() for r in validate.env_refs(content)}
        for f in features_dir.glob("*.feature"):
            if f != feat_dest:
                refs |= {r.upper() for r in validate.env_refs(f.read_text())}
        for pf in (res_dir / "pageobjects").glob("*.yaml"):
            refs |= {r.upper() for r in validate.env_refs(pf.read_text())}
        keep = refs | {app.upper()} | {
            str(k).upper() for k in (environment_values or {})}
        stale_env_keys = [k for k in env_map if str(k).upper() not in keep]
        if goal is not None and overwrite:
            for k in stale_env_keys:
                del env_map[k]
    env_text = "".join(f"{k}: {v}\n" for k, v in env_map.items())

    # secrets — write supplied prompt values (NOOD_0130); create only the
    # remaining MISSING required keys as placeholders; never clobber an existing
    # value we weren't given a replacement for.
    from dotenv import dotenv_values
    existing_secret_keys = (set(dotenv_values(secrets_path))
                            if secrets_path.is_file() else set())
    want_keys = [k for k in (required_secret_keys or [])]
    new_secret_keys = [k for k in want_keys
                       if k not in existing_secret_keys and k not in supplied_secrets]

    # NOOD_0129 — honest overwrite rollback: on any write failure, restore
    # every ORIGINAL byte (not just the files we newly created). We back up
    # each existing file's bytes before touching it and write via a sibling
    # temp + os.replace, so a partial write never lands over the original.
    created = []                                # files we made — unlink on failure
    backups: dict[Path, bytes] = {}             # existing files — restore on failure
    try:
        (res_dir / "pageobjects").mkdir(parents=True, exist_ok=True)
        features_dir.mkdir(parents=True, exist_ok=True)

        def _write(path: Path, text: str):
            if path.exists():
                backups.setdefault(path, path.read_bytes())
            else:
                created.append(path)
            tmp = path.with_name(path.name + ".noodle-tmp")
            try:
                tmp.write_text(text)
                os.replace(tmp, path)
            except OSError:
                tmp.unlink(missing_ok=True)
                raise

        _write(env_path, env_text)
        pom_text = None
        removed_stale_pom = False
        if pom_content is not None:
            pom_text = pom_content if pom_content.endswith("\n") else pom_content + "\n"
            _write(pom_path, pom_text)
        elif goal is not None and pom_path.is_file():
            # NOOD_0144 — goal mode owns the whole package: when the compile
            # needs no POM, a previous lap's generated <stem>_pom.yaml is dead
            # weight that keeps resolving. Remove it inside the transaction
            # (backed up — rollback restores the bytes like any overwrite).
            backups.setdefault(pom_path, pom_path.read_bytes())
            pom_path.unlink()
            removed_stale_pom = True
        if supplied_secrets or new_secret_keys:
            # Merge supplied values in place + append still-missing placeholders,
            # preserving existing keys/comments. Written through _write (temp +
            # os.replace) so it joins the same backup/rollback transaction.
            base = (secrets_path.read_text() if secrets_path.is_file()
                    else generate._SECRETS_EXAMPLE)
            _write(secrets_path, _apply_secrets(base, supplied_secrets, new_secret_keys))
        _write(feat_dest, content)
    except OSError as e:
        for p in created:                       # remove what we newly created
            p.unlink(missing_ok=True)
        for p, data in backups.items():         # restore what we overwrote
            p.write_bytes(data)
        return {"ok": False, "error": f"write failed, rolled back: {e}"}

    ws_res = Path(workspace).resolve()
    def rel(p):
        return os.path.relpath(p, ws_res)
    state = load_state(workspace)
    state.update(last_feature=rel(feat_dest), last_pom=rel(pom_path), last_app=app)
    save_state(state, workspace)

    # NOOD_0130 — recompute AFTER the write so supplied values count as present.
    env_after = _resolved_env(app_dir, workspace)
    missing_secret_keys = [k for k in want_keys
                           if _is_placeholder(env_after.get(k.upper()))]
    # NOOD_0131 Phase 2 — honest readiness: EVERY {env:KEY} the feature
    # references must resolve after the writes, not only keys the caller listed
    # as credentials. The baseline session referenced {env:BASE_URL} while the
    # URL landed under the derived app key — readiness said true and the
    # mismatch cost a browser launch.
    unresolved_refs = [k for k in validate.env_refs(content)
                       if _is_placeholder(env_after.get(k))]

    # NOOD_0129 — the readiness gate the reviewed session paid a separate
    # `validate --resolve` call for. A package is NOT ready to run when a step
    # matches no deterministic pattern and no NOODLE_MODEL is set to fall back
    # to (opt in with an @llm tag), when the POM can never scope to the
    # feature's URLs (its keys silently never apply — validate --resolve's own
    # hard-fail), or (NOOD_0130) when a referenced credential is still unset.
    # Files are still written so the caller can fix in place.
    blocking = []
    if unmatched_steps and not _model_configured(workspace) and "@llm" not in content:
        blocking.append(
            f"{len(unmatched_steps)} step(s) match no deterministic pattern and "
            "no NOODLE_MODEL is set to fall back to — rephrase to a vocabulary "
            "step (noodle://vocabulary), set a model, or tag the scenario @llm: "
            + "; ".join(unmatched_steps))
    if pom_content is not None:
        blocking += validate.lint_pom_scopes(pom_path)
    if missing_secret_keys:
        blocking.append(
            f"{len(missing_secret_keys)} credential(s) still unset — populate "
            f"resources/{app}_secrets.env (or supply secret_values / process env) "
            "before running: " + ", ".join(missing_secret_keys))
    if unresolved := [k for k in unresolved_refs if k not in missing_secret_keys]:
        blocking.append(
            f"{len(unresolved)} {{env:}} reference(s) resolve to nothing — the "
            f"app's base URL is stored under the key '{app}' (reference it as "
            f"{{env:{app.upper()}}}); set any other key in the app's "
            "environments.yaml, secrets.env, or environment_values: "
            + ", ".join(unresolved))
    # NOOD_0135 — URL fidelity: readiness must verify the URL a run will
    # actually resolve, not just that the key exists. A process env var or
    # environment_values override silently redirecting the run is a blocker.
    resolved_url = env_after.get(app.upper())
    if resolved_url != supplied_url:
        blocking.append(
            f"app URL mismatch — supplied '{supplied_url}' but '{app.upper()}' "
            f"resolves to '{resolved_url}' (an OS env var, environment_values, "
            "or another env file overrides the app package); align them before "
            "running")
    if goal_ev is not None:
        # Unproven requested actions/checks block FIRST — the compiled
        # artifacts still carry every request verbatim (never dropped).
        blocking = goal_ev["blocking"] + blocking
    result = {
        "ok": True, "app": app, "app_dir": rel(app_dir),
        "base_url_key": app.upper(),
        "feature": rel(feat_dest),
        "pom": rel(pom_path) if pom_content is not None else None,
        "environments": rel(env_path), "secrets": rel(secrets_path),
        "created_secret_keys": new_secret_keys,
        "missing_secret_keys": missing_secret_keys,
        "unmatched": unmatched_steps,
        "warnings": validate.redundant_post_nav_waits(content),
        "llm_required": llm_required,
        # NOOD_0135 — explicit: authoring already parsed, matched, linted and
        # resolved everything; a separate validate call adds nothing.
        "validated": not blocking,
        "ready": not blocking,
        # NOOD_0144 — honest naming: ready gates AUTHORING only; the reviewed
        # session read it as runtime-ready and over-claimed a green.
        "ready_means": "static authoring checks — runtime is proven only by the run",
        "blocking": blocking,
        # NOOD_0156 — semantic honesty: manual feature_content is finished
        # Gherkin the engine can validate but whose INTENT it never received,
        # so ready:true there is syntax/static readiness only. Only the
        # structured goal path — where every request is preserved verbatim,
        # bindings carry probe provenance, and generated prerequisites carry
        # required_by evidence — may claim intent_verified. The overloaded
        # meaning is split (intent contract v2): `goal_verified` = every goal
        # action/check compiled with probe/compiler provenance;
        # `intent_verified` = the whole contract (navigation, actions,
        # identity checks, evidence markers) traces to a compiled step — see
        # intent_trace.
        "source": "goal" if goal is not None else "manual",
        "goal_verified": goal is not None and not (goal_ev or {}).get("blocking"),
        "intent_verified": goal is not None and not blocking,
    }
    if wok_tag_added:
        result["wok_tag"] = wok_tag_added
    # NOOD_0156 follow-up — every lenient-input rewrite, echoed back so the
    # caller sees exactly what the engine understood.
    if goal is not None and norm_notes:
        result["goal_normalized"] = norm_notes
    # NOOD_0144 — package hygiene, surfaced instead of silently accumulating.
    if stale_env_keys:
        result["pruned_env_keys" if goal is not None and overwrite
               else "stale_env_keys"] = stale_env_keys
    if removed_stale_pom:
        result["removed_stale_pom"] = rel(pom_path)
    if pom_content is not None and isinstance(pom_data, dict):
        # element keys only — skip the scope header and nested scoped blocks
        if unused_pom := [k for k, v in pom_data.items()
                          if k != "match"
                          and not (isinstance(v, dict) and "match" in v)
                          and str(k).lower() not in content.lower()]:
            result["unused_pom_keys"] = unused_pom
    if goal is not None:
        # Bounded, model-visible: the compiled artifacts + what was proven —
        # never the raw probe dump (that lives in artifacts/probe_goal.json).
        # `runtime_asserted` checks depend on data the probe never entered: kept
        # in the feature, but honestly labelled — the run, not the probe, proves
        # them.
        result["compiled"] = {"feature": content, "pom": pom_text}
        result["evidence"] = {k: goal_ev[k] for k in
                              ("proven", "runtime_asserted",
                               "permission_prompts", "popups_closed")
                              if k in goal_ev}
        # NOOD_0169 — setup-vs-action page health: broken setup URLs are
        # preserved with a warning; a broken final page already blocked.
        if goal_ev.get("navigation_health"):
            result["evidence"]["navigation_health"] = \
                goal_ev["navigation_health"]
        if goal_ev.get("bound_targets"):
            result["evidence"]["bound_targets"] = goal_ev["bound_targets"]
        # NOOD_0156 — intent provenance: what the user asked for, what got
        # bound to concrete probe evidence, and every extra step with its
        # required_by/evidence justification.
        result["intent"] = goal_mod.intent_summary(goal, goal_ev)
        # NOOD_0156 — the compact requirement→evidence trace; intent_verified
        # is false the moment any entry lacks provenance. Raw evidence stays
        # in artifacts/probe_goal.json.
        trace = goal_mod.intent_trace(goal, goal_ev)
        result["intent_trace"] = trace
        if not all(t.get("ok") for t in trace):
            result["intent_verified"] = False
        # ONE typed repair code per blocked payload — the driving agent fixes
        # the named gap instead of inventing an exploration strategy.
        if blocking:
            result["next_action"] = goal_mod.next_action(blocking)
        # NOOD_0156 — synthesized assertions are visible in the payload, not
        # only in the compiled feature text: the caller must know a check was
        # engine-generated (and why) rather than user-supplied.
        if generated_checks:
            result["generated_checks"] = generated_checks
        # Record the intent contract so a later manual re-author of the same
        # feature/app cannot silently auto-run around a blocked goal.
        state = load_state(workspace)
        contracts = state.get("intent_contracts") or {}
        entry = {"blocked": bool(blocking),
                 "intent_verified": result["intent_verified"]}
        contracts[rel(feat_dest)] = dict(entry)
        contracts[f"app:{app}"] = dict(entry)
        state["intent_contracts"] = contracts
        save_state(state, workspace)
    if not run_after_author:
        return result
    if not result["ready"]:
        return {"ok": False, "author": result,
                "run": {"skipped": "authoring not ready — no run browser "
                                   "launched", "blocking": blocking}}
    if goal is None and not allow_unverified_intent:
        # NOOD_0156 — the manual-fallback gate: once a structured intent
        # contract exists for this feature/app, hand-written feature_content
        # must not auto-run around it (the 'Choose options' path — a blocked
        # goal silently became a guessed manual run). Files are written;
        # only the automatic run is refused. allow_unverified_intent=true is
        # the explicit expert override — autonomous agents must not set it.
        contracts = load_state(workspace).get("intent_contracts") or {}
        contract = contracts.get(rel(feat_dest)) or contracts.get(f"app:{app}")
        if contract:
            return {"ok": False, "author": result,
                    "next_action": "fix_blocked_goal",
                    "run": {"skipped": (
                        "a structured intent contract exists for this "
                        "feature/app — manual feature_content is never "
                        "intent-verified and cannot auto-run around it; fix "
                        "the goal's blockers instead (expert override: "
                        "allow_unverified_intent=true)"),
                        "blocking": blocking}}
    run = run_and_report(result["feature"], workspace=workspace,
                         headless=True, retries=0, serve_reports=True)
    if run.get("ok") and not run.get("passed"):
        # An empty run exits 0 — a green that ran nothing is a failure.
        run["ok"] = False
        run["error"] = ("0 scenarios passed — forced failure despite exit "
                        f"code {run.get('exit_code')}")
    return {"ok": bool(run.get("ok")), "author": result, "run": run}


def init_workspace(path: str, llm: str | None = None,
                   model: str | None = None) -> dict:
    """Scaffold a test workspace (NOOD_0084) — the CLI's `noodle init` as a
    callable: noodle.yaml, .env, sample feature + POMs, engine glue. Existing
    files are never overwritten. llm/model persist NOODLE_MODEL into the new
    .env (see cli.init)."""
    from noodle.cli import init as _init
    _, out = _capture(_init, path, llm, model)
    return {"ok": True, "workspace": str(Path(path).resolve()),
            "output": out.strip()}


def cost_estimate(target: str, *, model: str | None = None,
                  workspace: str = ".") -> dict:
    """Pre-flight LLM token/$ estimate for a file (NOOD_0084) — the CLI
    `noodle cost <file>` estimate branch as a callable. target resolves
    cwd-relative first, then workspace-relative. model=None uses the
    workspace .env's NOODLE_MODEL."""
    from dotenv import load_dotenv

    from noodle.llm import cost as _cost
    load_dotenv(Path(workspace) / ".env")
    p = Path(target)
    if not p.is_file():
        p = Path(workspace) / target
    if not p.is_file():
        return {"ok": False, "error": f"no such file: {target}"}
    try:
        est = _cost.estimate(p.read_text(), model=model)
    except ImportError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "target": str(p), **est}


def list_tests(workspace: str = ".", query: str | None = None) -> dict:
    """Inventory of every .feature under tests_dir — path, feature name, tags.
    Regex parse; no behave dry-run, no browser.

    NOOD_0162 — index first, detail on request: scenario names are the bulk
    (25 KB unfiltered in this repo) and a caller routes on path and tags, so
    the unfiltered call returns `scenario_count` instead. `query` substring-
    matches path/feature/scenario/tag and returns `scenarios` for the
    features that match."""
    cfg = config.load(workspace)
    fdir = Path(workspace) / cfg["tests_dir"]
    q = (query or "").lower()
    out = []
    for f in sorted(fdir.rglob("*.feature")):
        text = f.read_text()
        feat_m = re.search(r"^\s*Feature:\s*(.+)$", text, re.M)
        entry = {
            "path": str(f.relative_to(workspace)),
            "feature": feat_m.group(1).strip() if feat_m else "",
            "scenarios": [s.strip() for s in re.findall(
                r"^\s*Scenario(?: Outline)?:\s*(.+)$", text, re.M)],
            "tags": sorted(set(re.findall(r"@([\w:.-]+)", text))),
        }
        if q and q not in " ".join(
                [entry["path"], entry["feature"], *entry["scenarios"],
                 *entry["tags"]]).lower():
            continue
        if not q:
            entry["scenario_count"] = len(entry.pop("scenarios"))
        out.append(entry)
    note = (f"{len(out)} feature(s) matching '{query}'." if q else
            "scenario names omitted — pass query='<substring>' (matches path, "
            "feature, scenario or tag) to get them for matching features.")
    return {"tests": out, "note": note}


def validate_feature(content: str, workspace: str = ".") -> dict:
    """Dry-run feature text against the pattern table (no browser). Lets an
    external agent pre-flight its own Gherkin before write_feature.

    NOOD_0055 — points the pattern table at the workspace's own docs/ first,
    exactly like hooks.before_all does at run time: without it, steps accepted
    into <workspace>/docs/agent_patterns.yaml validated as unmatched here while
    resolving fine in a real run."""
    from noodle.repl import validate
    from noodle.resolver import patterns as _patterns
    _patterns.set_agent_patterns_dir(Path(workspace) / "docs")
    result = validate.check_feature(content)
    return {"error": result["error"],
            "steps": [{"step": line, "matched": ok} for line, ok in result["steps"]],
            "unmatched": validate.unmatched(result),
            # NOOD_0114 — vision-LLM image steps: nondeterministic, flagged
            # so the caller knows the scenario will carry @potential-flake.
            "llm_required": validate.llm_image_steps(content),
            # NOOD_0128 — semantic warning: an explicit page-load wait right
            # after navigation is redundant and can time out on a slow SPA.
            "warnings": validate.redundant_post_nav_waits(content)}


def write_feature(path: str, content: str, *, overwrite: bool = False,
                  allow_unverified_intent: bool = False,
                  workspace: str = ".") -> dict:
    """Write caller-authored Gherkin into the workspace (the fully-LLM-free
    MCP path: external agent writes vocabulary-compliant steps, we validate
    and store). Path must stay inside tests_dir — MCP callers are a trust
    boundary.

    NOOD_0169 — the repair provenance gate: a feature compiled from a
    structured goal/prompt contract cannot be repaired by hand-editing its
    Gherkin (the 'click here' drift entered exactly this way — a recovery
    click with no source or probe provenance). Repairs re-enter
    author_test(goal/prompt): normalize → validate → review → probe →
    compile. allow_unverified_intent=true is the explicit expert override —
    autonomous agents must not set it."""
    cfg = config.load(workspace)
    tests_root = (Path(workspace) / cfg["tests_dir"]).resolve()
    dest = (Path(workspace) / path).resolve()
    if not dest.is_relative_to(tests_root):
        return {"ok": False, "error": f"path must be under {cfg['tests_dir']}/"}
    if dest.suffix != ".feature":
        return {"ok": False, "error": "path must end in .feature"}
    if dest.exists() and not overwrite:
        return {"ok": False, "error": f"{path} exists — pass overwrite=true to replace"}
    if not allow_unverified_intent:
        try:
            rel = str(dest.relative_to(Path(workspace).resolve()))
        except ValueError:
            rel = path
        if (load_state(workspace).get("intent_contracts") or {}).get(rel):
            return {"ok": False, "next_action": "fix_blocked_goal",
                    "error": "a structured intent contract exists for this "
                    "feature — hand-edited Gherkin has no source or probe "
                    "provenance and cannot replace it; repair the goal via "
                    "author_test(goal/prompt) instead (expert override: "
                    "allow_unverified_intent=true)"}
    # NOOD_0155 — wok tagging: caller content with a routing tag is intent
    # (kept verbatim); with none, add the tag inferred from the steps so the
    # feature runs — and validates below — in the right wok.
    from noodle import wok as _wok
    content, wok_tag_added = _wok.ensure_tag(content)
    check = validate_feature(content, workspace)
    if check["error"]:
        return {"ok": False, "error": f"not valid Gherkin: {check['error']}"}
    # NOOD_0114 — vision-LLM image steps get a ⚠ comment + @potential-flake
    # tag written into the file, so the user sees the flake risk in the
    # .feature itself.
    if check["llm_required"]:
        from noodle.repl import validate
        content = validate.annotate_llm_image_steps(content)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content if content.endswith("\n") else content + "\n")
    rel = str(dest.relative_to(Path(workspace).resolve()))
    state = load_state(workspace)
    state["last_feature"] = rel
    save_state(state, workspace)
    out = {"ok": True, "feature": rel, "unmatched": check["unmatched"],
           "llm_required": check["llm_required"]}
    if wok_tag_added:
        out["wok_tag"] = wok_tag_added
    return out


def probe_page(url: str, *, timeout_ms: int = 15000,
               click: list[str] | None = None,
               do: list[str] | None = None,
               search: str | None = None,
               suggest: str | None = None,
               pick: str | None = None,
               mutate: str | None = None,
               follow: str | None = None,
               expect: list[str] | None = None,
               open_native_controls: bool = False,
               max_reveal_depth: int = 1,
               discover: bool = False, act_on: str | None = None,
               workspace: str = ".") -> dict:
    """NOOD_0113 — proactive DOM probe: open the page(s) headless and return
    actionable controls + POM suggestions + vocabulary-shaped steps, so an
    agent writes the feature right on the first pass instead of discovering
    locator gaps one failed run at a time. `url` may hold several URLs
    (space/comma separated) — probed sequentially in one browser. `click`
    (NOOD_0116) names reveal controls to click, in order, each followed by a
    fresh snapshot of what it revealed. `open_native_controls` (NOOD_0128)
    auto-enumerates native <select> options and click-opens custom comboboxes
    (bounded by `max_reveal_depth`, never a state-mutating control).
    `discover` (NOOD_0136) auto-clicks bounded generic disclosure candidates
    when the caller doesn't know trigger names yet. `suggest` (NOOD_0141)
    types the partial term per-character into the search box and captures the
    typeahead: exact suggestion strings, navigating row selectors, no-op icon
    flags, copy-ready steps. `follow` (NOOD_0142, with suggest) clicks the
    captured suggestion row matching this text (fuzzy — a misspelled site row
    still matches) and summarizes the landed page like `search`. `expect`
    (NOOD_0142) verifies each text is present on the page the probe ended on
    — one FOUND/NOT-FOUND verdict per text, the cheap alternative to dumping
    controls just to confirm a product name. `do` (NOOD_0144) executes an
    ordered fill/select/click transaction ("enter <value> in <field>" /
    "select <option> from <dropdown>" / "click <name>"), diffing state after
    each action — the whole fill → save → new-state flow in ONE session;
    {env:KEY} in a value resolves engine-side (workspace env chain) so raw
    credentials never transit the transcript."""
    from noodle.agents.web import probe as _probe
    urls = [normalize_url(u) for u in re.split(r"[,\s]+", url.strip()) if u]
    if not urls:
        return {"pages": [], "errors": [{"url": url, "error": "no URL given"}]}
    # NOOD_0169 — several URLs in one browser IS an ordered navigation
    # contract (session priming, then the action page): the goal path already
    # acted only on the last URL, but the CLI/MCP default ran search/clicks/do
    # on EVERY page — a setup page then reported "no search box found",
    # poisoning author_ready and the reader's next decision.
    if act_on is None:
        act_on = "last" if len(urls) > 1 else "each"
    if do and any("{env:" in a for a in do):
        env = _resolved_env(None, workspace)
        refs = {r for a in do for r in re.findall(
            r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}", a)}
        if missing := sorted(r for r in refs
                             if _is_placeholder(env.get(r.upper()))):
            return {"pages": [], "errors": [{"url": url, "error":
                    "unresolved {env:} in do actions — set in the workspace "
                    "env files first: " + ", ".join(missing)}]}
        do = [re.sub(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}",
                     lambda m: env[m.group(1).upper()], a) for a in do]
    return _probe.probe(urls, timeout_ms=timeout_ms, clicks=click, do=do,
                        search=search, suggest=suggest, pick=pick,
                        mutate=mutate, follow=follow, expect=expect,
                        open_native_controls=open_native_controls,
                        max_reveal_depth=max_reveal_depth, discover=discover,
                        act_on=act_on)


def probe_app(platform: str | None = None, *, compact: bool = False) -> dict:
    """NOOD_0136 — native-app probe: one Appium session, one page_source
    snapshot, normalized controls with lookup strategy + suggested step.
    Snapshot-only; nothing is tapped. Same env contract as tagged runs.
    NOOD_0162 — compact caps the node list (visible first) with a `truncated`
    note; author_ready/coverage/warnings/POM entries always pass whole."""
    from noodle.agents.mobile import probe as _mprobe
    result = _mprobe.probe_app(platform)
    return _mprobe.compact_payload(result) if compact else result


def inspect_locator(url: str, text: str, *, timeout_ms: int = 15000,
                    screenshot: str | None = None) -> dict:
    """NOOD_0115 — "why does/would this phrase resolve to X": run find()'s
    exact resolution machinery against a live page, headless, and report
    every candidate (source, visibility) plus what find() actually picks —
    the debugging question that otherwise costs a throwaway Playwright
    script per locator mystery."""
    from noodle.agents.web import inspect_locator as _inspect
    return _inspect.inspect(normalize_url(url.strip()), text,
                            timeout_ms=timeout_ms, screenshot_path=screenshot)


def search_step(query: str, *, use_llm: bool = False, workspace: str = ".") -> dict:
    """Nearest existing step for a plain-English action description."""
    from noodle.resolver import patterns as _patterns
    from noodle.resolver import step_resolver
    from noodle.resolver.step_search_engine import search_step as _search
    docs_dir = Path(workspace) / "docs"
    step_resolver.set_docs_dir(docs_dir)
    _patterns.set_agent_patterns_dir(docs_dir)
    result = _search(query, use_llm=use_llm)
    # NOOD_0058 — found means "safe to use as-is": only a high-confidence
    # match. A low-confidence best guess stays in `step`/`candidates` so a
    # caller can still show it, but an agent keying on `found` alone falls
    # through to the step-suggestion path instead of taking a wrong step.
    candidates = [{"step": s.step, "score": round(s.score, 3)}
                  for s in result.shortlist[:3]]
    return {"found": bool(result.match) and result.confidence == "high",
            "step": result.match.step if result.match else None,
            "confidence": result.confidence if result.match else None,
            "llm_used": result.llm_used,
            "reason": result.reason,
            "candidates": candidates}


def rca(workspace: str = ".", *, compact: bool = False) -> str:
    """Per-failure root-cause markdown for the last run. compact (NOOD_0117):
    verdict + failing step + fix only — the cheap first read."""
    from noodle.reporting import rca_report as _rca
    results = str(_paths.last_run_root(workspace) / "allure-results")
    return _rca.render_compact(results) if compact else _rca.render_markdown(results)


def summary_text(workspace: str = ".") -> str:
    """The `noodle summary` prose, returned instead of printed."""
    from noodle.reporting import summary as _summary
    root = _paths.last_run_root(workspace)
    return _summary.render(str(root / "allure-results"),
                           str(root / "reports" / "allure-report"))


def build_report(workspace: str = ".") -> dict:
    """Regenerate both reports (Allure HTML + RCA md/html) from the latest results."""
    proc = _engine("report", "generate", workspace=workspace)
    reports = _paths.last_run_root(workspace) / "reports"
    return {"ok": proc.returncode == 0,
            "report": str(reports / "allure-report" / "index.html"),
            "rca_html": str(reports / "rca.html"), "rca_md": str(reports / "rca.md"),
            "output": (proc.stdout + proc.stderr).strip()}


def list_reports(workspace: str = ".") -> dict:
    """NOOD_0082 — what `report serve` can host: the live reports root (with
    what's actually in it) and the timestamped archives/ zips of earlier runs."""
    from datetime import datetime
    root = _paths.last_run_root(workspace) / "reports"
    live = None
    if root.is_dir():
        idx = root / "allure-report" / "index.html"
        live = {"path": str(root), "allure": idx.is_file(),
                "rca": (root / "rca.html").is_file(),
                "generated_at": (datetime.fromtimestamp(idx.stat().st_mtime)
                                 .isoformat(timespec="seconds") if idx.is_file() else None)}
    archives = [{"path": str(z), "stamp": z.stem.removeprefix("artifacts_"),
                 "size_mb": round(z.stat().st_size / 1e6, 1)}
                for z in sorted((Path(workspace) / "archives").glob("artifacts_*.zip"))]
    return {"live": live, "archives": archives}


def serve_report(workspace: str = ".", report_dir: str | None = None, port: int = 0,
                 *, ensure_fresh: bool = True) -> dict:
    """NOOD_0082 — host the reports root (Allure + rca.html) on localhost and
    return the URLs. Localhost only by design: failure screenshots can contain
    typed credentials — teammates get `noodle report serve --host`.
    Missing reports are rebuilt from allure-results first when possible;
    ensure_fresh=False (NOOD_0131) skips that check when the caller just
    verified the root itself (run_and_report) — standalone serving keeps it.

    NOOD_0161 — a DETACHED child, the same one `noodle run --serve` has spawned
    since NOOD_0134, and a live server for this root is reused. This used to be
    a daemon thread inside the CALLING process: an agent's MCP server. Its URLs
    died whenever that server restarted, and port=0 minted a new URL every run,
    so the links handed to a user went dead over and over."""
    from noodle import cli as _cli  # lazy both ways: cli imports core too
    from noodle.reporting import builder
    root = Path(report_dir) if report_dir else _paths.last_run_root(workspace) / "reports"
    # NOOD_0089/0091 — rebuilds missing OR stale reports, so one root can't
    # host an Allure report and an rca.html from two different runs. Applies
    # equally to an explicit report_dir: an <app>/report path (holding
    # allure-results/ and reports/ as siblings) or the reports/ dir itself.
    if not ensure_fresh:
        pass
    elif not report_dir:
        builder.ensure_fresh_reports(
            str(_paths.last_run_root(workspace) / "allure-results"), str(root))
    elif (root / "allure-results").is_dir() and (root / "reports").is_dir():
        builder.ensure_fresh_reports(str(root / "allure-results"), str(root / "reports"))
        root = root / "reports"
    elif (root.parent / "allure-results").is_dir():
        builder.ensure_fresh_reports(str(root.parent / "allure-results"), str(root))
    if not root.is_dir():
        return {"ok": False, "error": f"no reports at {root} — run a test or `noodle report generate` first"}
    return _cli._spawn_report_server(str(root), workspace, "127.0.0.1", port)


def stop_report_servers(workspace: str = ".") -> dict:
    """Shut down every report server this workspace has hosted — the detached
    children `serve_report` spawns (NOOD_0161). Delegates to `noodle report
    stop`, which already prunes dead pids and catches ad-hoc `python -m
    http.server` hosts (NOOD_0095).

    ponytail: a subprocess for a SIGTERM loop, deliberate (NOOD_0162 §6) —
    it reuses the tested stop path whole instead of a second kill loop that
    would drift. Extract report_stop's body into a plain function if this
    ever lands in a hot path."""
    out = subprocess.run([sys.executable, "-m", "noodle.cli", "report", "stop",
                          "--workspace", workspace],
                         capture_output=True, text=True)
    return {"ok": True, "detached": out.stdout.strip() or out.stderr.strip()}
