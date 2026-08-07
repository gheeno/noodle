import functools
import json
import logging
import os
import re
import shlex
import time
import traceback
from collections import defaultdict
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from playwright.sync_api import sync_playwright

from noodle import config as config_module
from noodle import healing, log
from noodle.agents.web import browser_pool as _browser_pool
from noodle.agents.web import locator as locator_module
from noodle.agents.web import pom as pom_module
from noodle.log import logger
from noodle.reporting import paths as _paths

# NOOD_0052 — Safari rides Playwright's WebKit engine (same as `webkit`, the
# friendlier name); Edge is Chromium launched through the locally-installed
# Edge browser channel — Microsoft Edge must be installed on the machine.
# NOOD_0179 — the table itself now lives in noodle.config so the probe resolves
# engines through the same one; these names stay as the module's public spelling.
_VALID_BROWSERS = config_module.VALID_BROWSERS
_ENGINE_ALIASES = config_module.ENGINE_ALIASES

# --- Custom hook registry ---
# Valid events mirror behave's lifecycle names.
_VALID_EVENTS = frozenset({
    "before_all", "before_feature", "before_scenario",
    "after_step", "after_scenario", "after_all",
})
_registry: dict[str, list] = defaultdict(list)


# NOOD_0177 — variables that make a child process load attacker-chosen code
# before it runs a line of its own. Never settable from workspace config.
_BLOCKED_ENV_KEYS = frozenset({
    "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH",
    "NODE_OPTIONS", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
    "PATH", "BROWSER_PATH", "PERL5OPT", "RUBYOPT", "GEM_PATH",
})


# NOOD_0177 — a URL's query string is where SSO callbacks, password-reset links
# and pre-signed storage URLs carry their credential, and these land in Allure,
# the network log and the CI Tests tab. Everything RCA reads is in the path.
def _safe_url(url) -> str:
    try:
        s = str(url or "")
        base, sep, _ = s.partition("?")
        return f"{base}?…" if sep else base
    except Exception:
        return ""


def _safe_failed_request(line) -> str:
    """NOOD_0197 — redact only the URL inside a 'METHOD url — failure'
    composite. Running _safe_url over the whole line partitioned on the first
    '?', destroying the failure tail RCA parses — so failed requests with a
    query string silently vanished from mutation_verdict while bare URLs
    fired."""
    m = re.match(r"^(\S+)\s+(\S+)\s+—\s+(.*)$", str(line or ""))
    if not m:
        return _safe_url(line)
    method, url, failure = m.groups()
    return f"{method} {_safe_url(url)} — {failure}"


def _safe_ws_frame(frame) -> dict:
    """Keep the shape of a WebSocket frame, drop the body — an auth handshake
    puts the bearer token in the first frame's payload."""
    try:
        f = dict(frame or {})
        payload = f.pop("payload", "")
        f["payload_len"] = len(payload) if isinstance(payload, (str, bytes)) else 0
        f["url"] = _safe_url(f.get("url", ""))
        return f
    except Exception:
        return {"payload_len": 0}


def register(event: str, fn):
    """Register a callable for a lifecycle event.

    Note: before_all hooks must be registered in environment.py (step files
    are loaded after before_all fires and would miss it).
    """
    if event not in _VALID_EVENTS:
        raise ValueError(f"Unknown hook event {event!r}. Valid: {sorted(_VALID_EVENTS)}")
    _registry[event].append(fn)


def hook(event: str):
    """Decorator — @hook('before_scenario') def my_hook(context, scenario): ..."""
    def decorator(fn):
        register(event, fn)
        return fn
    return decorator


def _run_hooks(event: str, *args):
    # NOOD_0025: a user-registered hook (tests/steps/custom_hooks.py) that
    # raises used to propagate straight out of noodle.hooks.before_scenario/
    # after_scenario. behave's own HOOK-ERROR wrapper swallows that at the
    # top level, but everything *after* the raise in the same function body
    # — browser/context/tracing cleanup, most of all — never ran. One
    # crashing custom hook leaked a whole sync_playwright() instance, which
    # then poisoned every following scenario in the process with "Sync API
    # inside the asyncio loop". Isolate each hook so one failure can't take
    # down the framework's own teardown or the rest of the run.
    for fn in _registry[event]:
        try:
            fn(*args)
        except Exception as e:
            # NOOD_0187 — NOODLE_STRICT_HOOKS=1 re-raises: by default a broken
            # user hook degrades to a logged error and the run can still go
            # green, which is right for teardown-safety but wrong when the
            # hook IS the test setup. Strict mode lets CI refuse that green.
            if os.getenv("NOODLE_STRICT_HOOKS", "").strip().lower() in (
                    "1", "true", "yes", "on"):
                raise
            logger.error(f"\n  ❌ {event} hook '{fn.__name__}' raised: {e!r}")

try:
    from noodle.reporting import annotate as _annotate
    from noodle.reporting import builder as _builder
    from noodle.reporting import junit as _junit
    from noodle.reporting import rca_report as _rca_report
    from noodle.reporting import writer as _writer
    _REPORTING = True
except ImportError:
    _REPORTING = False

# Accumulated scenario results for JUnit XML, populated in after_scenario.
_suite_results = []

# NOOD_0202 — attempts per (feature file, scenario name), for the build log's
# retry marker. behave's autoretry re-runs the scenario in place, so the hooks
# fire again with no attempt number of their own.
_attempts: dict = {}


# NOOD_0202 — has the run-wide TLS warning been said once? See _ignore_https().
_certs_warned = False


def _report_engine_crash(fn):
    """NOOD_0202 — log a hook crash before behave swallows it.

    behave reports one line for an exception escaping a hook — `ABORTED:
    HOOK-ERROR in hook=before_all` — and prints the traceback to *stdout*, which
    `--quiet` has already diverted to run.log. In CI that means an engine
    regression aborts the build with no class, no message and no traceback on
    the console: you have to download an artifact to learn what broke.

    These six hooks are the engine's boundaries with behave, which is why they
    get this and ordinary engine functions don't — a blanket try/except over
    every method would swallow failures rather than surface them. Re-raises
    immediately: behave still decides what the crash means for the run.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.error(f"ENGINE ERROR in {fn.__name__}: "
                         f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            raise
    return wrapper


def _retry_tag(attempt: int) -> str:
    """' [retry 1/2]' on a re-run, empty on the first attempt. The denominator
    is what a run was ALLOWED (NOODLE_RETRIES), so a CI reader can tell 'last
    chance' from 'more to come'."""
    if attempt <= 1:
        return ""
    return f" [retry {attempt - 1}/{os.getenv('NOODLE_RETRIES', '1')}]"


def _load_environments():
    """Load base URLs from environments.yaml into os.environ (uppercased).
    Real env vars win, so CI can override without editing the file.

    Precedence (NOOD_0133 — same model as .env/secrets):
      shell/CI env  >  per-app resources/[<app>_]environments.yaml  >
      <cwd>/environments.yaml  >  nothing
    (both the plain and the app-prefixed name are accepted, NOOD_0108).
    A per-app file overrides root-file values; only pre-run env beats it."""
    import yaml

    def _apply(path: Path, override: bool = False):
        if not path.exists():
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key, value in data.items():
            k = key.upper()
            # NOOD_0177 — a config file has no legitimate reason to set the
            # dynamic-loader or interpreter hooks, and this file is scaffolded
            # with a header calling it "safe to commit", so a reviewer reads it
            # as inert. It isn't: the next child process (Allure's Node binary,
            # the behave subprocess, the browser) inherits os.environ.
            if k in _BLOCKED_ENV_KEYS:
                logger.warning(
                    f"\n  ⚠️  Ignoring {k} from {path.name} — loader/interpreter "
                    "variables cannot be set from workspace config.")
                continue
            if override and not _shell_owned(k):
                os.environ[k] = str(value)
            else:
                os.environ.setdefault(k, str(value))

    _apply(Path.cwd() / "environments.yaml")
    tests_dir = config_module.load(".")["tests_dir"]
    for suite_env in sorted(Path.cwd().glob(f"{tests_dir}/**/resources/*environments.yaml")):
        _apply(suite_env, override=True)


# App dirs whose resources/.env + <app>_secrets.env have already been loaded
# this run — before_feature fires once per .feature file, but many files
# share one app dir, so re-loading would just be wasted parses.
_loaded_package_dirs: set[str] = set()

# NOOD_0133 — keys the PROCESS environment owned before any .env file was
# loaded: shell exports, CI variables, the CLI-injected NOODLE_* flags. These
# always win. Keys sourced from the root .env are deliberately NOT in here —
# a package resources/.env overrides them. Plain load_dotenv() (override=False,
# first write wins) made every package-level override of a root-.env key dead
# config: the documented per-app precedence was false for any key root .env set.
_shell_env_keys: set[str] = set()
_shell_snapshot_taken = False


def _snapshot_shell_env():
    global _shell_snapshot_taken
    _shell_env_keys.clear()
    _shell_env_keys.update(os.environ)
    _shell_snapshot_taken = True


def _shell_owned(key: str) -> bool:
    """Was this key in the environment before any config file loaded? Lazily
    snapshots for callers that never ran before_all (repl/MCP helpers) — by
    then any parent-loaded root .env is already in os.environ, so those keys
    read as shell-owned and the old first-write behaviour is preserved there.
    ponytail: provenance is per-process only; export a provenance list if a
    parent-process path ever needs true package-beats-root."""
    if not _shell_snapshot_taken:
        _snapshot_shell_env()
    return key in _shell_env_keys


def _load_env_file(path: Path, override: bool):
    """Precedence in one place: shell/CI env > package files > root files.
    override=False is plain load_dotenv (root loads — first write wins,
    real env beats the file). override=True (package loads) beats values
    sourced from root files but never touches a shell-owned key."""
    if not override:
        load_dotenv(path)
        return
    for key, value in dotenv_values(path).items():
        if value is not None and not _shell_owned(key):
            os.environ[key] = value


def _load_package_env(app_dir: Path):
    """Load <app_dir>/resources/.env + [<app_dir.name>_]secrets.env — the
    per-app equivalent of the root loads in before_all. Package values
    override root .env/secrets values on a key collision (NOOD_0133); only
    the pre-run process environment beats them. Loaded lowest-priority file
    FIRST (override=True is last-write-wins), so within the package the
    priority stays .env > secrets.env > <app>_secrets.env — same winners as
    before_all's first-write root cascade."""
    key = str(app_dir)
    if key in _loaded_package_dirs:
        return
    _loaded_package_dirs.add(key)
    resources_dir = app_dir / "resources"
    _load_secrets(resources_dir / f"{app_dir.name}_secrets.env", override=True)
    _load_secrets(resources_dir / "secrets.env", override=True)
    _load_env_file(resources_dir / ".env", override=True)


def _load_secrets(path: Path, override: bool = False):
    """Env-file load + register every value with log.register_secret so it's
    scrubbed from all output (NOOD_0118). dotenv_values parses the file without
    mutating os.environ, giving us the values to redact even for keys already
    set by a higher-priority source."""
    _load_env_file(path, override)
    if path.exists():
        for value in dotenv_values(path).values():
            log.register_secret(value)


@_report_engine_crash
def before_all(context):
    # NOOD_0171 — correlation: adopt the run_id the CLI/MCP minted (it crosses
    # the subprocess boundary via env — contextvars don't), or mint one for a
    # bare `behave` invocation. workspace = the tenant dimension on a shared
    # multi-team server (the /data/<team> leaf = cwd basename).
    # NOOD_0202 — FIRST, before anything that can throw: this is the child's only
    # channel to the CI console, and a crash in the setup below (the engine
    # regression case) has nothing to report itself on until it exists. The file
    # handler attaches further down and is unconditional; this is the live
    # stream, and only when something is watching one.
    if log.progress_mode():
        log.attach_progress_handler()
    rid = os.environ.setdefault("NOODLE_RUN_ID", log.new_run_id())
    log.bind(run_id=rid, workspace=os.path.basename(os.getcwd()) or ".")
    # NOOD_0062 — explicit ".env": bare load_dotenv() find_dotenv()-walks up
    # from THIS file's directory, not the workspace cwd, so a workspace's own
    # .env was silently skipped whenever the engine ran as a package.
    log._secret_values.clear()             # NOOD_0118 — fresh redaction set per run, before we register any
    log.register_env_secrets()             # NOOD_0171 — host/container-injected secrets (env values → scrub set)
    _snapshot_shell_env()                  # NOOD_0133 — pre-run env keys beat every config file
    load_dotenv(".env")                    # config (committed) — cwd = workspace
    _load_secrets(Path("secrets.env"))     # secrets (gitignored) — soon AKV
    _load_environments()
    _loaded_package_dirs.clear()
    _suite_results.clear()
    _attempts.clear()          # NOOD_0202 — fresh retry counters per run
    _purged.clear()            # NOOD_0229 — fresh retention bookkeeping per run
    globals()["_certs_warned"] = False   # NOOD_0202 — say the TLS notice once per run
    if os.getenv("NOODLE_PARALLEL_WORKER") == "1":
        # Each behavex worker is its own process — give it a private results
        # subdir so workers don't wipe/overwrite each other's files. The CLI
        # cleans the shared parent once, before spawning the workers.
        # NOOD_0195 — as_posix: this env var crosses into worker subprocesses
        # and Allure's own tooling, and Windows accepts forward slashes in a
        # path anyway. One spelling everywhere beats two.
        os.environ["NOODLE_RESULTS_DIR"] = (
            _paths.artifacts_root() / "allure-results" / f"p{os.getpid()}").as_posix()
        log.attach_file_handler(str(_paths.logs_dir() / f"noodle.p{os.getpid()}.log"))
        # NOOD_0171 — all workers share the run_id (inherited via env), so a
        # merged log needs a per-lane tag to disentangle interleaved features/
        # scenarios. pid matches this worker's noodle.p<pid>.log filename.
        # NOOD_0202 — `lane` is the same idea in a form a human can read on a
        # build console: 10 workers streaming to one stderr interleave, and a
        # 5-digit pid per line is not something anyone tracks by eye. The build
        # log prefixes it as [w3], so `grep '\[w3\]'` replays that lane in order.
        from noodle import runlock as _runlock
        log.bind(worker=os.getpid(), lane=_runlock.worker_index())
    else:
        _begin_run_results()
        _paths.clean_worker_leaves()   # NOOD_0187 — stale p<pid>/ leaves from a prior parallel run
        log.attach_file_handler(str(_paths.logs_dir() / "noodle.log"))
    # NOOD_0187 — a core-only install (no [reporting]/[all] extra) silently
    # produced NO allure-results, NO junit, NO RCA: a run that writes no
    # evidence must at least say so once, loudly.
    if not _REPORTING:
        logger.warning(
            "\n  ⚠️  Reporting extras not installed — this run will produce NO "
            "allure-results, junit.xml or RCA report. Install with: "
            "pip install noodle[all]")
    # NOOD_0183 — this process's lane, 1..N (always 1 when sequential). Exported
    # so a suite can give each lane its own account: define SHOP_USER_1..N and
    # `{env:SHOP_USER}` resolves to this lane's, no feature-file change.
    from noodle import runlock
    os.environ["NOODLE_WORKER_INDEX"] = str(runlock.worker_index())
    healing.reset()
    # NOOD_0007 — fresh LLM budget + step-resolution memo per run.
    from noodle.llm import client as _llm_client
    from noodle.resolver import patterns as _patterns
    from noodle.resolver import step_resolver as _step_resolver
    _llm_client.reset_calls()
    # NOOD_0080 — fresh token/dollar ledger per run.
    from noodle.llm import cost as _llm_cost
    _llm_cost.reset()
    _step_resolver.clear_cache()
    # NOOD_0033 — fresh once-per-run deprecation warnings for legacy [X]/`X`.
    from noodle.orchestrator import runner as _runner
    _runner._deprecation_warned.clear()
    # NOOD_0027 — cwd is already the workspace (behave itself is launched
    # with cwd=workspace, noodle/cli.py's `run` command), so any step
    # accepted via `noodle step-search --accept --workspace <this>` resolves
    # here too, not just at search time.
    _step_resolver.set_docs_dir(Path.cwd() / "docs")
    _patterns.set_agent_patterns_dir(Path.cwd() / "docs")
    _load_keyvault()
    _run_hooks("before_all", context)


def _begin_run_results():
    """Open this run's results.

    NOOD_0229 — this used to delete every `*-result.json` and
    `*-attachment.*` in the directory, which is why authoring a second
    feature into an app package destroyed the first one's result AND its
    evidence image, then rebuilt the report from what survived. Now the run
    only stamps its identity and clears the per-run ledgers that must not
    accumulate; stale results are replaced per feature/scenario, as they are
    re-run, by _purge_prior_results() below. `NOODLE_FRESH_RESULTS=1`
    restores the full wipe on purpose for one run (`noodle report reset`
    does it once, out of band). An env var and not a `--flag`: `noodle run
    --help` is a capped always-on surface with under 100 bytes of headroom
    and a Typer row costs ~250, so the flag would have been funded by
    deleting guidance agents read on every call (same call as NOOD_0223).

    Trend data still lives in reports/allure-history/ and is untouched either
    way.
    """
    from noodle.reporting import scope as _scope
    results = _paths.results_dir()
    if os.getenv("NOODLE_FRESH_RESULTS", "").strip().lower() in ("1", "true", "yes", "on"):
        _scope.clean_all(results)
    else:
        _scope.clean_run_ledgers(results)
    _scope.begin_run(results)


# NOOD_0229 — feature files / scenario keys whose prior results this process
# has already replaced. A retry re-enters before_scenario, and purging again
# would delete the failed first attempt that makes the pass read as flaky.
_purged: set = set()


def _scenario_filter_active(context) -> bool:
    """True when this run selects SOME scenarios of a feature (--name/--tags),
    so re-running the file must not evict the siblings it never touched."""
    cfg = getattr(context, "config", None)
    if cfg is None:
        return True                      # unknown → the narrower purge
    if getattr(cfg, "name", None):
        return True
    return bool(getattr(getattr(cfg, "tags", None), "ands", None))


def _purge_prior_results(*, keys=(), feature_files=()):
    """Replace what this run re-runs, keep what it doesn't (NOOD_0229)."""
    from noodle.reporting import scope as _scope
    fresh = [x for x in (*keys, *feature_files) if x and x not in _purged]
    if not fresh:
        return
    _purged.update(fresh)
    try:
        _scope.purge(_paths.results_dir(), keys=keys,
                     feature_files=feature_files,
                     keep_since_ms=_scope.run_started_ms(_paths.results_dir()))
    except OSError:
        pass          # retention is a nicety — never fail a run over it


@_report_engine_crash
def before_feature(context, feature):
    log.bind(feature=Path(feature.filename).name)  # NOOD_0171 — correlation
    # NOOD_0202 — the build log's grouping header (maven's "Running <class>").
    # json mode already carries the feature on every record via bind(); a text
    # console has nothing else to group the scenario lines under.
    log.telemetry("feature.start", f"Feature: {feature.name}",
                  file=Path(feature.filename).name)
    # NOOD_0229 — this file is being re-run in full, so its prior results are
    # superseded: drop them (and their attachments) before the first scenario
    # writes, which also retires results for scenarios that have since been
    # renamed or deleted. Under a --name/--tags filter only some scenarios run,
    # so the purge drops to per-scenario in before_scenario instead.
    if not _scenario_filter_active(context):
        _purge_prior_results(feature_files=[feature.filename])
    feature_dir = Path(feature.filename).parent
    # Tell POM loader which folder to look in for local pom.yaml
    pom_module.set_context(str(feature_dir))
    # .feature files live in <app_dir>/features/ — resources/ is the sibling
    # one level up, so the env/secrets loader takes the app dir, not feature_dir.
    _load_package_env(feature_dir.parent)

    # Flaky-test retry: re-run a failed scenario up to NOODLE_RETRIES extra
    # times (default 1). Retries fire ONLY on failure, so green scenarios cost
    # nothing. @no_retry opts a scenario out (e.g. a known-failing assertion).
    retries = int(os.getenv("NOODLE_RETRIES", "1"))
    if retries > 0:
        from behave.contrib.scenario_autoretry import patch_scenario_with_autoretry
        for scenario in feature.scenarios:
            if 'no_retry' not in scenario.effective_tags:
                patch_scenario_with_autoretry(scenario, max_attempts=retries + 1)
    _run_hooks("before_feature", context, feature)


def _ocr_available():
    """True when pytesseract + the tesseract binary are usable. Cached."""
    global _OCR_OK
    if _OCR_OK is None:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            _OCR_OK = True
        except Exception:
            _OCR_OK = False
    return _OCR_OK


_OCR_OK = None


def _appium_available():
    """True when the Appium Python client is importable. Cached."""
    global _APPIUM_OK
    if _APPIUM_OK is None:
        try:
            import appium  # noqa: F401
            _APPIUM_OK = True
        except ImportError:
            _APPIUM_OK = False
    return _APPIUM_OK


_APPIUM_OK = None


def _viewport_from(tags) -> dict | None:
    """Viewport for the scenario: @viewport:1920x1080 tag wins, then the
    NOODLE_VIEWPORT env var; None = Playwright's default (and @mobile device
    presets keep their own)."""
    raw = next((t.split(':', 1)[1] for t in tags if t.startswith('viewport:')), None) \
        or os.getenv("NOODLE_VIEWPORT")
    if not raw:
        return None
    m = re.match(r'^\s*(\d+)\s*[xX]\s*(\d+)\s*$', raw)
    if not m:
        raise ValueError(f"Bad viewport {raw!r} — expected WIDTHxHEIGHT, e.g. 1920x1080")
    return {"width": int(m.group(1)), "height": int(m.group(2))}


# NOOD_0181 — the steps dictionary's popup taxonomy tells testers to "add a
# disable flag" when a Chrome product bubble (sign-in, save-password, translate)
# shows up in a headed run, but there was nowhere to put one: launch() only ever
# got headless/slow_mo/channel. e.g.
#   NOODLE_BROWSER_ARGS="--disable-features=PasswordManagerOnboarding --guest"
# shlex, not .split(), so a flag whose value contains a space survives.
def _browser_args() -> list[str]:
    return shlex.split(os.getenv("NOODLE_BROWSER_ARGS", "").strip())


# NOOD_0181 — the device roster used to be two hardcoded names; Playwright
# ships ~130 presets, each carrying the viewport, DPR, user-agent and (the part
# that matters) hasTouch/isMobile. Gherkin tags cannot contain spaces, so
# @device:pixel_7 / @device:iphone-15-pro match case- and separator-insensitively.
def _device_from(tags, devices) -> str | None:
    """The Playwright device preset for this scenario, or None for desktop.
    @device:<name> wins; @mobile keeps its historic iPhone 13 / Pixel 5 default."""
    raw = _tag_value(tags, 'device')
    if raw:
        want = raw.replace('_', ' ').replace('-', ' ').strip().lower()
        for name in devices.keys():
            if name.lower() == want:
                return name
        close = [n for n in devices if want.split()[0] in n.lower()][:8]
        raise ValueError(
            f"Unknown device {raw!r} — no Playwright preset matches. "
            + (f"Did you mean: {', '.join(close)}?" if close else
               "See `playwright.devices` for the full list.")
        )
    if 'mobile' in tags:
        return "iPhone 13" if 'iphone' in tags else "Pixel 5"
    return None


def _tag_value(tags, prefix: str) -> str | None:
    """The value of a @prefix:value tag, or None."""
    return next((t.split(':', 1)[1] for t in tags if t.startswith(prefix + ':')), None)


def ignore_https_errors(tags) -> bool:
    """Whether the browser context skips TLS certificate validation.

    NOOD_0177 — this now defaults to VERIFYING. It used to default to ignoring
    (NOOD_0089's reasoning: dev/sandbox sites carry self-signed certs), but the
    same engine runs against live production with real credentials from
    secrets.env, and a fail-open default meant any on-path attacker — corporate
    proxy, hostile Wi-Fi, DNS spoof — could MITM the login, collect the password
    the test types, and still see the run go green.

    Relaxing it is now an explicit decision, per-scenario with @insecure_certs
    or run-wide with NOODLE_IGNORE_HTTPS_ERRORS=true, and either way it logs a
    warning naming the risk. @secure_certs still forces verification and always
    wins, so existing scenarios that used it keep their meaning.
    """
    if 'secure_certs' in tags:
        return False
    env = os.getenv("NOODLE_IGNORE_HTTPS_ERRORS", "").strip().lower()
    env_wide = env in ("1", "true", "yes", "on")
    ignore = ('insecure_certs' in tags) or env_wide
    # NOOD_0202 — the env-var form is a RUN-level fact (the message says so), and
    # this runs per browser context: a 109-scenario suite printed the same
    # warning 109 times, a quarter of the whole CI build log. Once per process
    # for the run-wide switch; @insecure_certs still warns per scenario, because
    # there it IS a per-scenario decision and the step's RCA should carry it.
    global _certs_warned
    if ignore and not (env_wide and _certs_warned):
        _certs_warned = _certs_warned or env_wide
        logger.warning(
            "\n  ⚠️  TLS certificate validation is DISABLED for this run "
            "(NOODLE_IGNORE_HTTPS_ERRORS / @insecure_certs). Any credential this "
            "test types can be intercepted. Never use it against production.")
    return ignore


# NOOD_0032 — platform tags that imply an Appium session with default caps.
# @mobile is NOT here: it stays Playwright device emulation (web).
_APPIUM_PLATFORM_TAGS = ('android', 'ios', 'windows', 'mac')


def page_pin(tags) -> str | None:
    """@page:<name> tag — pins the POM active page for the whole scenario, up
    front. Same effect as the 'User is on the "<name>" page' step
    (agents/web/actions.set_page), but declared as a tag so it survives even
    if no step ever navigates (e.g. the URL is set by a Background 'is on'
    step and never changes again) and is visible at a glance in the feature
    file, instead of buried in step text."""
    return _tag_value(tags, 'page')


def appium_platform(tags) -> str | None:
    """The Appium platform a scenario is tagged for, or None. Pure — tested
    without a running Appium server. @mobile wins: '@mobile @android' keeps
    its pre-NOOD_0032 meaning (Playwright Pixel-5 web emulation, not Appium)."""
    if 'mobile' in tags:
        return None
    return next((t for t in _APPIUM_PLATFORM_TAGS if t in tags), None)


def _emulation_opts(tags) -> dict:
    """Phase N/O (F9/F10) — geolocation, permissions, locale, timezone,
    color-scheme and offline for new_context(). @tag:value wins, then the
    NOODLE_* env var — same convention as @viewport/_viewport_from.
    Locale/timezone/color-scheme are context-creation-time only (Playwright
    has no runtime setter); geolocation/permissions also have runtime steps."""
    opts = {}
    geo = _tag_value(tags, 'geo') or os.getenv("NOODLE_GEOLOCATION")
    if geo:
        try:
            lat, lon = (float(p.strip()) for p in geo.split(",", 1))
        except ValueError:
            raise ValueError(f"Bad geolocation {geo!r} — expected 'lat,lon', e.g. '51.5,-0.12'")
        opts['geolocation'] = {"latitude": lat, "longitude": lon}
    perms = _tag_value(tags, 'permissions') or os.getenv("NOODLE_PERMISSIONS")
    if perms:
        opts['permissions'] = [p.strip() for p in perms.split(",") if p.strip()]
    locale = _tag_value(tags, 'locale') or os.getenv("NOODLE_LOCALE")
    if locale:
        opts['locale'] = locale
    tz = _tag_value(tags, 'timezone') or os.getenv("NOODLE_TIMEZONE")
    if tz:
        opts['timezone_id'] = tz
    scheme = _tag_value(tags, 'color_scheme') or os.getenv("NOODLE_COLOR_SCHEME")
    if scheme:
        opts['color_scheme'] = scheme
    if 'offline' in tags or os.getenv("NOODLE_OFFLINE", "").lower() in ("1", "true", "yes"):
        opts['offline'] = True
    return opts


def _wire_capture_listeners(context):
    """Phase M/L/R (F8/F13) — passive per-scenario capture: console errors,
    uncaught JS errors, failed requests, every request URL, websocket frames.
    Mirrors _downloads' lifecycle: reset here, read by assertion actions.
    Listeners are additive — nothing asserts unless a step asks."""
    context._console_errors = []
    context._page_errors = []
    context._failed_requests = []
    context._requests = []
    context._mutations = []
    context._failed_responses = []
    context._ws_frames = []
    attach_capture_listeners(context, context.page)


def attach_capture_listeners(context, page):
    """NOOD_0187 — attach the capture listeners to `page`, appending into the
    scenario's EXISTING lists. Split out of _wire_capture_listeners so a named
    browser context ('buyer'/'seller') gets the same console/network/ws
    capture as the primary page — before this, the second user's failures
    debugged blind and half the assertion vocabulary silently saw nothing."""
    console_errors = context._console_errors
    page_errors = context._page_errors
    failed_requests = context._failed_requests
    requests = context._requests
    mutations = context._mutations
    failed_responses = context._failed_responses
    ws_frames = context._ws_frames

    def _on_console(msg):
        if msg.type == "error":
            console_errors.append(msg.text)

    def _on_page_error(err):
        page_errors.append(str(err))

    def _on_request_failed(req):
        failed_requests.append(f"{req.method} {req.url} — {req.failure}")

    def _on_request(req):
        requests.append(req.url)
        # NOOD_0156 — mutation-shaped requests get their own ledger (method +
        # URL): a failed postcondition after an add/save action is diagnosed
        # against these instead of a generic assertion-mismatch verdict.
        if req.method not in ("GET", "HEAD", "OPTIONS"):
            mutations.append(f"{req.method} {req.url}")
        # NOOD_0089 — feed the smart-wait network-activity clock (locator's
        # poll loop asks "is the page still loading?" at its deadline).
        from noodle.agents.web import activity as _activity
        _activity.note_request(req.url)

    def _on_response(resp):
        # NOOD_0156 — non-success statuses, passively: request-level failures
        # (aborts) land in failed_requests; an HTTP 4xx/5xx completes "fine"
        # at that layer and was previously invisible to RCA.
        try:
            if resp.status >= 400:
                failed_responses.append(
                    f"{resp.status} {resp.request.method} {resp.url}")
        except Exception:
            pass

    def _on_websocket(ws):
        ws.on("framesent", lambda payload: ws_frames.append(
            {"url": ws.url, "direction": "sent", "payload": payload}))
        ws.on("framereceived", lambda payload: ws_frames.append(
            {"url": ws.url, "direction": "received", "payload": payload}))

    page.on("console", _on_console)
    page.on("pageerror", _on_page_error)
    page.on("requestfailed", _on_request_failed)
    page.on("request", _on_request)
    page.on("response", _on_response)
    page.on("websocket", _on_websocket)


# NOOD_0183 — one Playwright driver + browser per worker process, reused across
# scenarios; measured 0.672s → 0.034s of per-scenario setup (driver spawn
# 0.28s + launch 0.23s + first context 0.16s, against a bare new_context).
# At 1000 scenarios that is ~11 minutes of pure process spawning removed, and
# it is what Playwright's own runner does: browser per worker, context per test.
# Keyed on everything that changes what the browser IS — engine, channel,
# headless, slow_mo, args, remote endpoint — so an @firefox or @headed scenario
# never gets served the wrong one. NOODLE_REUSE_BROWSER=0 restores relaunching.
_browsers: dict[tuple, tuple] = {}

# NOOD_0203 — ONE driver per process, N browsers off it. Playwright's sync API
# allows exactly one live `sync_playwright().start()` per thread; a second one
# raises "using Playwright Sync API inside the asyncio loop". So the first
# scenario whose tags change the launch key (@slow's slow_mo, @firefox,
# @headed in a headless run, NOODLE_BROWSER_ARGS) used to blow up in
# before_scenario and take every scenario after it with it.
_pw = None


def _driver():
    global _pw
    if _pw is None:
        _pw = sync_playwright().start()
    return _pw


def _reuse_browsers() -> bool:
    return os.getenv("NOODLE_REUSE_BROWSER", "1").strip().lower() not in ("0", "false", "no", "off")


def _launch(engine, launch_opts, remote_url):
    pw = _driver()
    browser_type = getattr(pw, engine)
    if remote_url:
        browser = browser_type.connect(remote_url, slow_mo=launch_opts.get("slow_mo", 0))
        logger.info(f"\n  🌐 Connected to remote browser: {remote_url.split('?')[0]}")
    else:
        if launch_opts.get("args"):
            logger.info(f"\n  🚩 Extra browser args: {launch_opts['args']}")
        try:
            browser = browser_type.launch(**launch_opts)
        except Exception:
            # NOOD_0239 — a `playwright install --only-shell` host (common in
            # CI) has no full Chromium build, so the channel we added above
            # cannot launch. Degrade to the shell rather than fail the whole
            # suite: the shell runs almost every site, and the ones it can't
            # are the ones that channel exists for. Only ever retracts the
            # channel WE chose — an explicit @edge is the caller's and re-raises.
            if launch_opts.get("channel") != _browser_pool.default_channel(engine):
                raise
            degraded = {k: v for k, v in launch_opts.items() if k != "channel"}
            logger.info("\n  ⚠️  full Chromium build not installed — using the "
                        f"headless shell ({_browser_pool.install_command('chromium')})")
            browser = browser_type.launch(**degraded)
    return pw, browser


def _browser_for(engine, launch_opts, remote_url):
    """The (playwright, browser) pair for these launch options — cached unless
    reuse is off. A browser that died between scenarios (crash, external kill)
    is evicted and relaunched once, so one bad scenario can't poison the rest
    of the file."""
    if not _reuse_browsers():
        return _launch(engine, launch_opts, remote_url)
    key = (engine, remote_url, tuple(sorted(
        (k, tuple(v) if isinstance(v, list) else v) for k, v in launch_opts.items())))
    cached = _browsers.get(key)
    if cached is not None and cached[1].is_connected():
        return cached
    if cached is not None:
        _close_pair(cached)
        del _browsers[key]
    _browsers[key] = _launch(engine, launch_opts, remote_url)
    return _browsers[key]


def _close_pair(pair) -> None:
    # Only the browser: the driver is shared with every other cached entry.
    try:
        pair[1].close()
    except Exception:
        pass


def close_cached_browsers() -> None:
    global _pw
    for pair in list(_browsers.values()):
        _close_pair(pair)
    _browsers.clear()
    if _pw is not None:
        try:
            _pw.stop()
        except Exception:
            pass
        _pw = None


def _record_skip(scenario, reason: str) -> None:
    """NOOD_0187 — a gated skip used to leave no trace in any report: the
    scenario vanished from Allure/junit/summary and a fully gated-off suite
    read as a clean run. Write a skipped result so every reader sees it."""
    if _REPORTING:
        try:
            _, r = _writer.write_skip_result(scenario, reason)
            _suite_results.append(r)
        except Exception:
            pass


@_report_engine_crash
def before_scenario(context, scenario):
    log.bind(scenario=scenario.name)  # NOOD_0171 — correlation
    log.start_scenario_log()  # NOOD_0205 — this scenario's own slice of the log
    context._noodle_scenario_t0 = time.monotonic()  # NOOD_0173 — scenario.end duration
    # NOOD_0202 — which attempt is this? behave's autoretry re-enters this hook,
    # so a retried scenario printed twice on the build log with nothing saying
    # why — which reads as a duplicate, or as two scenarios sharing a name.
    _key = (getattr(getattr(scenario, "feature", None), "filename", ""), scenario.name)
    _attempt = _attempts[_key] = _attempts.get(_key, 0) + 1
    context._noodle_attempt = _attempt
    # NOOD_0229 — this scenario's prior-run results are superseded the moment
    # it starts again. before_feature already covers the unfiltered case; this
    # is the --name/--tags one, where evicting the whole file would delete
    # siblings this run never re-ran. Attempts of THIS run are protected by
    # keep_since_ms, so a retry still reads as flaky.
    if _REPORTING:
        _purge_prior_results(keys=_writer.scenario_key(scenario))
    # NOOD_0202 — the body IS the console line in text mode, so it names the
    # scenario. It read literally "▶️ scenario.start" before, which told a build
    # log nothing.
    log.telemetry("scenario.start", f"  Scenario: {scenario.name}{_retry_tag(_attempt)}",
                  attempt=_attempt, tags=sorted(scenario.effective_tags))
    tags = set(scenario.effective_tags)

    # NOOD_0183 — @serial / @lock:<name>: hold a cross-worker mutex for the
    # whole scenario. Taken BEFORE any skip check below so a scenario that
    # skips never leaves a lock behind (after_scenario releases either way),
    # and before the browser launch so a waiting lane isn't burning a browser.
    from noodle import runlock
    runlock.touch_worker_lane()   # NOOD_0187 — keep this lane's TTL alive on long features
    for _lock in runlock.lock_names(tags):
        runlock.acquire(_lock)

    # @live scenarios hit a real external site — opt-in only, so CI and casual
    # runs never make surprise network calls. Set NOODLE_RUN_LIVE=1 to run.
    if 'live' in tags and os.getenv("NOODLE_RUN_LIVE", "").lower() not in ("1", "true", "yes"):
        reason = "@live is opt-in — set NOODLE_RUN_LIVE=1 to run real-site tests"
        scenario.skip(reason)
        _record_skip(scenario, reason)
        return

    # @llm scenarios need a model at run time — skip (not fail) when none is
    # configured, so the no-LLM default run stays green (NOOD_0065).
    if 'llm' in tags and not os.getenv("NOODLE_MODEL"):
        reason = ("@llm needs NOODLE_MODEL in .env (e.g. anthropic/claude-sonnet-5) — "
                  "see README § LLM augmentation")
        scenario.skip(reason)
        _record_skip(scenario, reason)
        return

    # @terminal scenarios need the OCR engine — skip (not fail) where tesseract
    # isn't installed, so a suite stays green on a box without the [visual] extra.
    if 'terminal' in tags and not _ocr_available():
        reason = "OCR engine (tesseract) not installed — pip install noodle[visual]"
        scenario.skip(reason)
        _record_skip(scenario, reason)
        return

    # Per-scenario locator/POM state — reset so tags/pins don't leak between scenarios.
    locator_module.set_strict('strict' in tags or None)
    locator_module.set_ocr_fallback('ocr_fallback' in tags or None)  # Phase T
    locator_module.set_frame(None)        # 11.2 — clear any iframe scope
    locator_module.clear_last_match()     # NOOD_0008 phase 8 — no cross-scenario marks
    from noodle.agents.web import activity as _activity
    _activity.reset()                     # NOOD_0089 — fresh network-quiet clock
    pom_module.set_active_page(page_pin(tags))
    from noodle.agents.web import screen as _screen
    _screen.set_region(None)              # 0024 — clear any OCR focus region
    context._vars = {}                     # 11.1 — run-scoped stored values
    context._scenario_failed = False       # set by after_step; gates trace save
    context._manual_screenshot = None      # NOOD_0153 — explicit screenshot step's path
    context._evidence_last_step = _evidence_last_step(scenario)  # NOOD_0157
    context._mobile = None                 # Phase F — set by the @appium path below
    context._soft_failures = []            # Phase L — @soft assertion collection
    context._named_contexts = {}           # Phase J — name -> Page
    context._named_bctxs = {}              # Phase J — name -> BrowserContext

    # @api scenarios are pure REST — no browser, no tracing (NOOD_0007). REST
    # steps go through rest_client; a web step fails with a clear error in the
    # runner. Keeps API-only suites fast and CI images browser-free.
    if 'api' in tags:
        context.page = None
        if _REPORTING:
            context._allure_result = _writer.ScenarioResult(scenario)
        from noodle import preconditions
        preconditions.run(scenario, "setup")
        _run_hooks("before_scenario", context, scenario)
        return

    # NOOD_0155 — @perf scenarios (performance wok) generate HTTP load via the
    # built-in load generator: no browser, same browserless lifecycle as @api.
    # Reporting/preconditions/custom hooks all still run; the latency chart
    # (perf_report step) rides the screenshot pipeline into Allure + RCA.
    if 'perf' in tags:
        context.page = None
        if _REPORTING:
            context._allure_result = _writer.ScenarioResult(scenario)
        from noodle import preconditions
        preconditions.run(scenario, "setup")
        _run_hooks("before_scenario", context, scenario)
        return

    # Phase F — @appium scenarios drive a device/emulator via Appium instead of
    # a browser. Steps route through the mobile agent (runner._execute_mobile).
    # NOOD_0032 — @android/@ios/@windows/@mac imply @appium and pick default
    # capabilities for that platform (Windows 11 native apps and macOS included
    # — appium-windows-driver / appium-mac2-driver, see docs/native-apps.md).
    platform = appium_platform(tags)
    if 'appium' in tags or platform:
        # No Appium client installed — skip (not fail) rather than burning
        # retries on an ImportError that a re-run can't fix, same treatment
        # as the @terminal/OCR check above.
        if not _appium_available():
            reason = ("Appium client not installed — pip install noodle[mobile] "
                      "(and a running Appium server + device/emulator)")
            scenario.skip(reason)
            _record_skip(scenario, reason)
            return
        context.page = None
        if _REPORTING:
            context._allure_result = _writer.ScenarioResult(scenario)
        from noodle import preconditions
        preconditions.run(scenario, "setup")
        # User before_scenario hooks (e.g. session_id bookkeeping) must run
        # before the Appium connection attempt — after_scenario's matching
        # hooks always run, so if start_session() below fails, skipping this
        # would leave them reading state that was never set (NOOD_0018).
        _run_hooks("before_scenario", context, scenario)
        from noodle.agents.mobile import driver as _mobile_driver
        context._mobile = _mobile_driver.start_session(platform)
        return

    # Bug 3: warn when @headed and @headless both appear — @headed wins but conflict
    # is almost always a forgotten debug tag that will break CI silently.
    if 'headed' in tags and 'headless' in tags:
        logger.warning(
            f"\n  [noodle] WARNING: scenario '{scenario.name}' has both @headed and "
            f"@headless — @headed wins. Remove one tag to suppress this warning."
        )

    if 'headed' in tags:
        headless = False
    elif 'headless' in tags:
        headless = True
    else:
        headless = os.getenv("NOODLE_HEADLESS", "false").lower() == "true"

    slow_mo = 500 if 'slow' in tags else 0

    # Bug 4: validate browser name before passing to getattr(playwright, name)
    if 'firefox' in tags:
        browser_name = 'firefox'
    elif 'webkit' in tags:
        browser_name = 'webkit'
    elif 'safari' in tags:
        browser_name = 'safari'
    elif 'edge' in tags:
        browser_name = 'edge'
    else:
        browser_name = os.getenv("NOODLE_BROWSER", "chromium")

    if browser_name not in _VALID_BROWSERS:
        raise ValueError(
            f"Unsupported browser '{browser_name}'. "
            f"Valid options: {', '.join(sorted(_VALID_BROWSERS))}"
        )

    # NOOD_0153 — follow mode: headed runs scroll each matched element into
    # view so the watcher's viewport tracks what the engine acts on (the fix
    # for the up-and-down scroll-hunting testers reported). Headless runs have
    # no watcher — skip the extra scrolls. NOODLE_FOLLOW overrides either way.
    locator_module.set_follow(not headless)

    timeout = int(os.getenv("NOODLE_TIMEOUT", "10000"))

    engine, channel = _ENGINE_ALIASES.get(browser_name, (browser_name, None))
    # NOOD_0239 — the RUN gets the full Chromium build too. NOOD_0237 taught
    # browser_pool to ask for `channel="chromium"` instead of the stripped
    # headless shell, but only the PROBE goes through that pool: a headless run
    # still launched the shell, so the probe and the run drove two different
    # browsers and a site that needs the full build was probeable but not
    # runnable. Headless only — a headed launch already IS the full build.
    if headless and not channel:
        channel = _browser_pool.default_channel(engine)
    # Phase H (F4) — NOODLE_REMOTE_URL points at a remote Playwright/CDP
    # endpoint (BrowserStack, Sauce Labs, a Playwright grid): connect instead
    # of launching locally. The rest of the lifecycle is identical.
    remote_url = os.getenv("NOODLE_REMOTE_URL")
    launch_opts = {"headless": headless, "slow_mo": slow_mo}
    if channel:
        launch_opts["channel"] = channel
    if extra_args := _browser_args():
        launch_opts["args"] = extra_args
    context._pw, context._browser = _browser_for(engine, launch_opts, remote_url)

    ctx_opts = {"ignore_https_errors": ignore_https_errors(tags)}
    device_name = _device_from(tags, context._pw.devices)
    if device_name:
        ctx_opts.update(context._pw.devices[device_name])
        logger.info(f"\n  📱 Emulating {device_name}")
    viewport = _viewport_from(tags)
    if viewport:                            # explicit size wins over device preset
        ctx_opts['viewport'] = viewport
    if 'record_video' in tags:
        videos_dir = _paths.videos_dir()
        os.makedirs(videos_dir, exist_ok=True)
        ctx_opts['record_video_dir'] = str(videos_dir)
    ctx_opts.update(_emulation_opts(tags))  # Phase N/O — geo/perms/locale/tz/scheme/offline
    # NOOD_0239 — mask the headless UA, exactly as the probe does, so evidence
    # proven at authoring time reproduces at run time. Must sit above the
    # _named_ctx_opts copy: a second user ('buyer'/'seller') that kept the stock
    # UA would stall on precisely the sites this exists for. A @device preset
    # brings its own real UA and is left untouched.
    ctx_opts = _browser_pool.context_options(context._browser, ctx_opts)
    # NOOD_0187 — options a NAMED context ('buyer'/'seller') must inherit:
    # cert policy + emulation. Deliberately NOT storage_state — a second user
    # being a different session is the point of a named context.
    context._named_ctx_opts = dict(ctx_opts)
    context._named_ctx_opts.pop('storage_state', None)
    context._named_ctx_opts.pop('record_video_dir', None)

    # NOOD_0011 — reuse a saved login session (cookies + localStorage): log in
    # once, save via "saves the browser session as '<file>'", then point
    # NOODLE_STORAGE_STATE at that file — every scenario starts authenticated.
    # The standard answer to SSO/MFA login walls (Microsoft 365, Google, …).
    state = os.getenv("NOODLE_STORAGE_STATE")
    if state and os.path.exists(state):
        ctx_opts['storage_state'] = state

    # NOOD_0143 — @http_credentials: browser-level HTTP Basic auth, documented
    # in steps_dictionary's popup taxonomy since NOOD_0131 but never
    # implemented (the phantom the coverage audit caught). Credentials come
    # from env/secrets (NOODLE_HTTP_USER / NOODLE_HTTP_PASSWORD) — never a
    # feature file; a missing pair fails loudly before any navigation.
    if 'http_credentials' in tags:
        user = os.getenv("NOODLE_HTTP_USER")
        password = os.getenv("NOODLE_HTTP_PASSWORD")
        if not (user and password):
            raise AssertionError(
                "@http_credentials needs NOODLE_HTTP_USER and "
                "NOODLE_HTTP_PASSWORD set (put them in the app's secrets file)")
        ctx_opts['http_credentials'] = {"username": user, "password": password}

    context._bctx = context._browser.new_context(**ctx_opts)
    # NOOD_0177 — re-seed the acknowledged tab count for THIS scenario. The
    # browser context above is brand new (one page), but `_tabs_acked` is set
    # on behave's context during a scenario and survived into the next one, so
    # a scenario that followed one which opened a tab started life already
    # "expecting" two — and `a new tab should open` then fell through to the
    # wait_for_event path that cannot see an already-fired popup. That is why
    # trailer.feature passed alone and failed in the suite.
    context._tabs_acked = 1

    # Playwright tracing — DOM snapshots + network + sources. Started for every
    # scenario, but only SAVED on failure (after_scenario); discarded on pass so
    # green runs cost no disk. The trace viewer is the headline debugging edge
    # over Selenium/Selenide. ponytail: always-on capture, save-on-fail; the only
    # cheaper option (start-on-retry) needs a retry loop we don't have yet.
    try:
        context._bctx.tracing.start(screenshots=True, snapshots=True, sources=True)
        context._tracing = True
    except Exception:
        context._tracing = False

    context.page = context._bctx.new_page()
    context.page.set_default_timeout(timeout)

    # NOOD_0008 gap #4 — record downloads passively so `a file should be
    # downloaded` works after the click that triggered it, race-free.
    # (a plain function, not list.append — Playwright's sync wrapper can't
    # introspect builtin methods)
    downloads: list = []
    context._downloads = downloads

    def _record_download(download):
        downloads.append(download)

    context.page.on("download", _record_download)
    _wire_capture_listeners(context)   # Phase M/L/R — console/network/ws capture

    if _REPORTING:
        context._allure_result = _writer.ScenarioResult(scenario)

    # Data preconditions — seed BusterBlock state via @precondition:NAME before the
    # UI test runs (the JDBC-fixture analog). Setup failures abort the scenario.
    from noodle import preconditions
    preconditions.run(scenario, "setup")
    _run_hooks("before_scenario", context, scenario)


def _allure_result(context):
    """Return context._allure_result only when it's a real ScenarioResult instance."""
    if not _REPORTING:
        return None
    from noodle.orchestrator.runner import ctx_get
    # NOOD_0025: plain getattr(..., None) doesn't default on behave's real
    # Context — its __getattr__ raises KeyError, not AttributeError — so a
    # scenario that errors before before_scenario ever sets _allure_result
    # would crash here with an unrelated KeyError, burying the real failure.
    result = ctx_get(context, "_allure_result", None)
    if result is None:
        return None
    # Guard against MagicMock contexts in tests — only accept real ScenarioResult objects.
    if not isinstance(result, _writer.ScenarioResult):
        return None
    return result


# NOOD_0157 — evidence 'last' mode must aim at the last step that can still
# produce pixels: a scenario ending in a tab close leaves nothing to
# screenshot, so the shot fires on the newest step before the page-killing
# tail. Steps that merely don't touch an element (waits, popup sweeps, API
# teardown) are NOT skipped — their page is alive, and evidence.capture()'s
# refocus fallback re-outlines the scenario's last element there.
_PAGE_KILLING_ACTIONS = {"close_tab"}


def _evidence_last_step(scenario):
    """The step evidence 'last' mode fires on, or None to mean steps[-1].
    Pattern-table only (never the LLM fallback) and best-effort: an
    unmatched step counts as capturable.

    NOOD_0211 — prefers the last ASSERTION (`Then`, or an `And` chained under
    one). The default shot is meant to prove the scenario's outcome, and a run
    ending in a click/navigate produced a picture of a page nobody asserted
    anything about. Falls back to the last capturable step of any type, so a
    scenario with no Then at all still gets its shot.
    """
    try:
        from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject
        steps = list(getattr(scenario, "steps", None) or [])

        def capturable(step):
            m = match(normalize_phrasing(normalize_subject(step.name)))
            return m is None or m[0] not in _PAGE_KILLING_ACTIONS

        for want_assertion in (True, False):
            for step in reversed(steps):
                if want_assertion and getattr(step, "step_type", None) != "then":
                    continue
                if capturable(step):
                    return step
    except Exception:
        pass
    return None


def _resolved_element_line(context) -> str:
    """NOOD_0227 (A4) — the most recent element resolution, one line for the
    failure payload: phrase, selector, visible text and page URL. Labelled
    honestly when it belongs to an earlier step (a failing assertion resolves
    nothing itself). Empty string when nothing resolved yet; never raises."""
    try:
        from noodle.agents.web import locator as _loc
        from noodle.orchestrator.runner import ctx_get
        lm = _loc.last_match()
        if lm is None:
            return ""
        phrase, loc = lm
        m = re.search(r"selector='(.*?)'>?$", repr(loc))
        sel = m.group(1) if m else ""
        txt = ""
        try:
            txt = " ".join((loc.inner_text(timeout=250) or "").split())[:80]
        except Exception:
            pass
        seq0 = ctx_get(context, "_match_seq_at_step_start", None)
        own = seq0 is not None and _loc.match_seq() != seq0
        return ("resolved element"
                + ("" if own else " (from an earlier step)")
                + f": '{log.redact(str(phrase))}'"
                + (f" → {sel}" if sel else "")
                + (f" — text: '{log.redact(txt)}'" if txt else "")
                + (f" on {_safe_url(_loc.last_match_url())}"
                   if _loc.last_match_url() else ""))
    except Exception:
        return ""


@_report_engine_crash
def after_step(context, step):
    _run_hooks("after_step", context, step)
    # NOOD_0202 — per-step build log, DEBUG only. At INFO a 1000-scenario suite
    # would emit ~10k console lines; this is the tier you turn on
    # (`--log-level DEBUG`) to find WHERE a wedged run actually stopped. The
    # logger level gates it — no extra branch needed.
    log.telemetry("step.end", f"    Step: {step.keyword} {step.name}",
                  level=logging.DEBUG, step=step.name,
                  status=str(getattr(step.status, "name", step.status)),
                  duration_ms=int((getattr(step, "duration", 0) or 0) * 1000))
    # NOOD_0018 — drain this step's ⚠️ WARNING+ log lines regardless of
    # pass/fail, so they never bleed into the next step's capture.
    step_warnings = log.get_warnings()
    log.clear_warnings()
    # NOOD_0156 — this step's healing events (snapshot taken by
    # runner.execute_step): recorded on the step result whether it passed or
    # failed, so the run payload surfaces every substitution — a reviewed
    # session's false pass hid two of them behind a green exit code.
    from noodle.orchestrator.runner import ctx_get as _ctx_get
    _heal0 = _ctx_get(context, "_healing_at_step_start", None)
    step_healing = healing.events_since(_heal0) if isinstance(_heal0, int) else []
    # NOOD_0090 — behave gives raised exceptions Status.error, a different enum
    # member from Status.failed; == "failed" misses it and the errored step got
    # recorded as passed. has_failed() covers failed/error/hook_error; the
    # getattr fallback keeps plain-string status doubles in unit tests working.
    if getattr(step.status, "has_failed", lambda: step.status == "failed")():
        context._scenario_failed = True
        # NOOD_0153 — the failure screenshot supersedes any evidence request
        # on this step; clear the manual path so it can't leak into a retry.
        # (NOOD_0228 — the per-step flags are gone: the tag directives are
        # read per step from the scenario, so nothing is left to leak.)
        context._manual_screenshot = None
        shots_dir = _paths.screenshots_dir()
        os.makedirs(shots_dir, exist_ok=True)
        # NOOD_0177 — scrub BEFORE building the filename, and drop path
        # separators on both platforms. A filename survives every later text
        # redaction (and annotate.py burns the step name into the PNG itself),
        # so a literal credential in step text became a permanent artifact name.
        # Windows was also escapable here: '/' was stripped but '\' was not.
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", log.redact(step.name))[:80]
        raw_path = str(shots_dir / f"FAILED_{safe_name}.png")
        # NOOD_0173 — step.fail telemetry. error message is redacted by the log
        # filter; @api scenarios have no page, so no screenshot.
        _exc = getattr(step, "exception", None)
        # NOOD_0202 — an AssertionError is the test doing its job. Anything else
        # is the ENGINE (or a page object) throwing, and on a CI console the two
        # looked identical: "Step failed: <name>", no class, no message. Name the
        # exception so a reader can tell "my test found a bug" from "the
        # framework broke" without downloading run.log.
        _engine_error = _exc is not None and not isinstance(_exc, AssertionError)
        _detail = f"\n      {type(_exc).__name__}: {_exc}" if _engine_error else ""
        log.telemetry("step.fail", f"    Step failed: {step.name}{_detail}",
                      level=logging.ERROR, step=step.name,
                      error_class=type(_exc).__name__ if _exc else "",
                      error=(getattr(step, "error_message", "") or "")[:500],
                      screenshot=raw_path if getattr(context, "page", None) is not None else None)
        annotated_path = None
        shot_taken = False
        try:
            # @api scenarios have no page — report the failure without a shot.
            if getattr(context, "page", None) is not None:
                # NOOD_0008 phase 8 — outline matched (red) / POM-expected
                # (green dashed) elements in the live page before shooting.
                try:
                    marked = locator_module.mark_failure(context.page)
                except Exception:
                    marked = {}
                context.page.screenshot(path=raw_path, full_page=True)
                shot_taken = True
                logger.info(f"\n  📸 Screenshot saved: {raw_path}")
                ar = _allure_result(context)
                if ar is not None:
                    # NOOD_0187 — redact BEFORE the label is burned into the
                    # image pixels. NOOD_0177 scrubbed the filename, but the
                    # raw step name still went to annotate.py, which draws it
                    # into the PNG — a literal credential in step text became
                    # permanent pixels, base64-inlined into rca.html.
                    label = log.redact(step.name)[:60]
                    if marked.get("matched") or marked.get("expected"):
                        annotated_path = _annotate.draw_failure_markers(
                            raw_path, label, marked)
                    else:
                        annotated_path = _annotate.draw_not_found(raw_path, label)
                    # NOOD_0035: only annotated_path is attached to the Allure
                    # result below — the raw copy is now dead weight (double
                    # the PNG storage per failure). Drop it.
                    if annotated_path:
                        try:
                            os.remove(raw_path)
                        except OSError:
                            pass
        except Exception:
            pass
        # NOOD_0135 — every failure carries the ACTUAL page URL, and a
        # navigation-mismatch verdict when the goto landed off the requested
        # path: the reviewed session debugged locators for nine runs while the
        # engine knew it was on the wrong page all along.
        if getattr(context, "page", None) is not None:
            try:
                from noodle.agents.web import actions as _actions
                note = _actions.nav_mismatch(context.page)
                # NOOD_0145 — and a wrong-action-target verdict when the last
                # submit-like click produced no page change: the destination
                # this step expected was never reached.
                stuck = _actions.stuck_click(context.page)
                # NOOD_0167 — and the app's own announcement after the last
                # click (ARIA alert/status/live or toast), so the RCA can
                # quote why the app refused instead of guessing at locators.
                resp = _actions.page_response(context.page)
                # NOOD_0222 — and what the failed page renders, quoted, so
                # nobody OCRs the screenshot to learn "Your cart is empty".
                # Redacted: a login page can echo typed credentials as text.
                shown = _actions.page_text(context.page)
                # NOOD_0227 (A4) — WHICH element the engine last acted on,
                # in the failure payload itself: "which element did the click
                # hit?" cost the reviewed session four ~10×-priced vision
                # screenshot reads that this one line answers. After the
                # verdict + URL lines — compact RCA reads those first.
                rline = _resolved_element_line(context)
                step_warnings = (([note] if note else [])
                                 + ([stuck] if stuck else [])
                                 + ([resp] if resp else [])
                                 + ([log.redact(shown)] if shown else [])
                                 + [f"URL: {_safe_url(context.page.url)}"]
                                 + ([rline] if rline else [])
                                 + [*step_warnings])
            except Exception:
                pass
        ar = _allure_result(context)
        if ar is not None:
            ar.add_step(step, "failed", annotated_path or (raw_path if shot_taken else None),
                        warnings=step_warnings, healing=step_healing)

        # Agentic RCA (opt-in: NOODLE_RCA + NOODLE_MODEL). Classify the
        # failure's root cause from the screenshot and tag the Allure result so
        # the report can be filtered by category. Best-effort, never raises.
        from noodle import rca
        # NOOD_0035 deleted raw_path once the annotated copy exists — hand RCA
        # whichever file survived or it reads a ghost and silently skips.
        verdict = rca.review(step.name, step.error_message, annotated_path or raw_path)
        if verdict is not None and ar is not None:
            ar.result["labels"].extend([
                {"name": "rca_category", "value": verdict["label"]},
                {"name": "rca_confidence", "value": verdict.get("confidence", "")},
                {"name": "rca_reason", "value": verdict.get("reason", "")},
                {"name": "rca_fix", "value": verdict.get("suggested_fix", "")},
            ])
    else:
        # NOOD_0153 — evidence screenshot for a PASSED step, when the gates say
        # so: the "( take a screenshot )" marker / @evidence tag / NOODLE_EVIDENCE
        # mode ('last' by default — the final step of a still-green scenario).
        # The explicit "takes a screenshot" step's own file is attached instead
        # of shooting twice. All best-effort: evidence never fails a green step.
        from noodle.orchestrator.runner import ctx_get
        evidence_path, evidence_name, evidence_meta = None, None, None
        manual = ctx_get(context, "_manual_screenshot")
        context._manual_screenshot = None
        page = ctx_get(context, "page")
        # NOOD_0227 (D1) / NOOD_0228 — per-step directives arrive as scenario
        # tags (@evidence:steps=2,7 / @evidence:skip=3), compiled by goal mode
        # from checks[].evidence or derived from the brief: run configuration
        # as metadata. This is now the ONLY per-step source — the step-text
        # spelling is refused at parse time, so there is no second channel to
        # merge and no precedence question to get wrong.
        requested = suppressed = False
        try:
            from noodle.reporting import evidence as _ev_dir
            _sc = ctx_get(context, "scenario")
            _want, _skip = _ev_dir.step_directives(
                getattr(_sc, "effective_tags", None) or [])
            if _want or _skip:
                _steps = getattr(_sc, "steps", None) or []
                _pos = _steps.index(step) + 1 if step in _steps else None
                requested = _pos in _want
                suppressed = _pos in _skip
        except Exception:
            pass
        if manual and not suppressed:
            evidence_path, evidence_name = manual, "screenshot"
        elif not ctx_get(context, "_scenario_failed", False):
            try:
                from noodle.reporting import evidence as _evidence
                scenario = ctx_get(context, "scenario")
                tags = getattr(scenario, "effective_tags", None) or []
                steps = getattr(scenario, "steps", None) or []
                # NOOD_0157 — 'last' aims at the last step that can produce
                # pixels (skips a page-killing tail like a tab close).
                last_step = (ctx_get(context, "_evidence_last_step", None)
                             or (steps[-1] if steps else None))
                is_last = last_step is step
                # NOOD_0211 — behave reports both `Then` and an `And`/`But`
                # chained under it as step_type 'then', which is exactly the
                # "this step is an assertion" signal the evidence modes need.
                is_assertion = getattr(step, "step_type", None) == "then"
                if _evidence.wanted(tags, requested, is_last, page is not None,
                                    is_assertion, suppressed):
                    seq0 = ctx_get(context, "_match_seq_at_step_start", None)
                    fresh = seq0 is not None and locator_module.match_seq() != seq0
                    evidence_path = _evidence.capture(page, step.name, fresh)
                    evidence_name = "evidence"
                    # NOOD_0156 — evidence is only VALID when this step freshly
                    # resolved its element through an exact tier: a shot whose
                    # match came from fuzzy healing (dom-scan/partial-text/
                    # vision/OCR) pictures a loosely related element, not proof.
                    evidence_meta = _evidence.last_meta()
                    if evidence_meta is not None:
                        fuzzy = sorted({h["strategy"] for h in step_healing
                                        if h.get("strategy")
                                        in healing.FUZZY_STRATEGIES})
                        src = evidence_meta.get("source")
                        if src in healing.FUZZY_STRATEGIES and src not in fuzzy:
                            fuzzy.append(src)
                        if fuzzy:
                            evidence_meta["fuzzy_healing"] = fuzzy
                        # NOOD_0157 — refocused evidence (elementless final
                        # step, same page, element re-verified visible) is as
                        # good as fresh: the shot still proves the element.
                        # NOOD_0210 — a page-level assertion (HTTP status, url,
                        # title, console/network health) has no element to
                        # match by design, so `fresh` can never be true for it.
                        # The shot proves the page whose property just passed;
                        # requiring an element box made "every assertion needs
                        # an evidence screenshot" unsatisfiable next to a
                        # verified green.
                        page_level = ctx_get(context, "_noodle_page_level_assert",
                                             False)
                        if page_level:
                            evidence_meta["page_level"] = True
                        evidence_meta["valid"] = ((bool(fresh)
                                                   or evidence_meta.get("refocused") is True
                                                   or bool(page_level))
                                                  and not fuzzy)
            except Exception:
                pass
        ar = _allure_result(context)
        if ar is not None:
            # NOOD_0021 — a passed step can still have logged an ambiguous-locator/
            # self-heal warning (lenient mode never fails the build on these). Keep
            # them on the result so they're visible in `noodle rca-report` instead
            # of only ever appearing on console output that scrolls away.
            ar.add_step(step, "passed", warnings=step_warnings,
                        attachment_path=evidence_path,
                        attachment_name=evidence_name,
                        healing=step_healing, evidence_meta=evidence_meta)


@_report_engine_crash
def after_scenario(context, scenario):
    # User after_scenario hooks run first — context.page is still alive here.
    _run_hooks("after_scenario", context, scenario)

    # Teardown first, so it always runs even if the scenario failed (the point of
    # teardown). Failures here are logged, not raised — see preconditions.run.
    from noodle import preconditions
    preconditions.run(scenario, "teardown")

    # Phase L — @soft scenarios collect assertion failures instead of stopping;
    # if any were collected and no step failed hard, fail the scenario now so
    # a soft-failed run can never report green.
    from noodle.orchestrator.runner import ctx_get
    soft = ctx_get(context, "_soft_failures") or []
    if soft and not getattr(context, "_scenario_failed", False):
        context._scenario_failed = True
        logger.error(
            "\n  ❌ Soft assertion failure(s):\n"
            + "\n".join(f"    - {s}" for s in soft)
        )
        try:
            scenario.set_status("failed")
        except Exception:
            pass
        # NOOD_0187 — record the failure on the result too. set_status only
        # changes behave's console verdict: no STEP failed, so the Allure/
        # junit result said "passed" AND behave's exit code stayed 0 — the
        # report and the build both lied about a soft-failed scenario. The
        # synthetic failed step makes every reader (and the CLI's results-
        # derived exit code) agree with the console.
        ar_soft = _allure_result(context)
        if ar_soft is not None:
            ar_soft.add_failure(
                "soft assertion failure(s)",
                "\n".join(f"- {s}" for s in soft))

    # Network/console capture (from _wire_capture_listeners) — one log per
    # scenario, attached to its Allure result at test-case level so it shows
    # up against the page(s) that scenario actually visited. On by default
    # (not gated on failure): writing a small JSON and copying it into
    # allure-results/ is cheap, and knowing a passed scenario's requests were
    # clean is as useful as seeing a failed one's weren't.
    # NOOD_0025: @api/@appium scenarios never wire these listeners, so the
    # None check (not a truthy check) is required — ctx_get(..., False)
    # doesn't default on behave's real Context (KeyError, not AttributeError).
    if ctx_get(context, "_console_errors", None) is not None:
        try:
            net_dir = _paths.network_dir()
            os.makedirs(net_dir, exist_ok=True)
            safe_name = scenario.name.replace(" ", "_").replace("/", "_")[:80]
            net_path = net_dir / f"{safe_name}.json"
            # NOOD_0177 — this file is attached to Allure and published by CI.
            # Request URLs keep their query strings (?access_token=, ?sig=, SAS)
            # and ws_frames held RAW payloads both directions, where a GraphQL/WS
            # auth handshake carries the bearer token in frame 0. Strip queries,
            # drop frame bodies (keep url/direction/length, which is what RCA
            # actually reads), then value-scrub the whole document.
            net_doc = {
                "console_errors": context._console_errors,
                "page_errors": context._page_errors,
                "failed_requests": [_safe_failed_request(u)
                                    for u in context._failed_requests],
                "requests": [_safe_url(u) for u in context._requests],
                # NOOD_0156 — mutation-aware RCA inputs
                "mutations": ctx_get(context, "_mutations", []),
                "failed_responses": ctx_get(context, "_failed_responses", []),
                "ws_frames": [_safe_ws_frame(f) for f in context._ws_frames],
            }
            net_path.write_text(log.redact(json.dumps(net_doc, indent=2)), encoding="utf-8")
            ar_net = _allure_result(context)
            if ar_net is not None:
                ar_net.add_attachment("network log", str(net_path), "application/json")
        except Exception:
            pass

    # NOOD_0216 — the api wok's evidence: the scenario's REST request/response
    # transcript (runner._execute_rest records every call, bodies capped and
    # value-scrubbed), attached exactly like the web wok's network log. This
    # is what makes the wok registry's "recorded request/response pair on the
    # Allure step" claim true instead of aspirational — @api scenarios never
    # wire the browser listeners above, so without it they attached nothing.
    rest_ev = ctx_get(context, "_rest_evidence", None)
    if rest_ev:
        try:
            net_dir = _paths.network_dir()
            os.makedirs(net_dir, exist_ok=True)
            safe_name = scenario.name.replace(" ", "_").replace("/", "_")[:80]
            api_path = net_dir / f"{safe_name}.api.json"
            api_path.write_text(log.redact(json.dumps(rest_ev, indent=2)),
                                encoding="utf-8")
            ar_api = _allure_result(context)
            if ar_api is not None:
                ar_api.add_attachment("api log", str(api_path), "application/json")
        except Exception:
            pass

    # NOOD_0018 Phase 4 — visual baseline diff on PASSING web scenarios
    # (opt-in: NOODLE_VISUAL_BASELINE). Failures already get a screenshot +
    # classifier; this is the cheap detector for "passed but looks wrong".
    # The warning is appended to the last step so rca-report's
    # collect_warnings() surfaces it in "passed with warnings". Best-effort.
    from noodle.agents.visual import baseline as _baseline
    if _baseline.enabled() and not context._scenario_failed \
            and getattr(context, "page", None) is not None:
        try:
            visual_warning = _baseline.check(context.page, scenario.name)
        except Exception:
            visual_warning = None
        if visual_warning:
            logger.warning(f"\n  ⚠️  {visual_warning}")
            ar_vis = _allure_result(context)
            if ar_vis is not None and ar_vis.result["steps"]:
                ar_vis.result["steps"][-1].setdefault(
                    "statusDetails", {}).setdefault(
                    "warnings", []).append(visual_warning)

    # NOOD_0205 — this scenario's slice of the run log, attached as evidence
    # alongside its screenshots. Popped unconditionally (even with no result to
    # attach it to) so the buffer never carries into the next scenario. The
    # junit writer emits every result attachment as an [[ATTACHMENT|path]]
    # marker, so this reaches Azure's Tests tab as well as the Allure report.
    scenario_log = log.pop_scenario_log()

    ar = _allure_result(context)
    if ar is not None:
        if scenario_log:
            try:
                log_dir = _paths.logs_dir()
                os.makedirs(log_dir, exist_ok=True)
                safe_name = scenario.name.replace(" ", "_").replace("/", "_")[:80]
                # NOOD_0206 — the result uuid makes it unique. Two scenarios
                # sharing a name (same title in two features, an outline's rows,
                # or two --parallel workers writing the same path at once) wrote
                # over each other's log, so a report attached the WRONG
                # scenario's log — the exact class of lie NOOD_0187 closed
                # everywhere else.
                log_path = log_dir / f"{safe_name}_{ar.uuid[:8]}.log"
                log_path.write_text(log.redact(scenario_log), encoding="utf-8")
                ar.add_attachment("run log", str(log_path), "text/plain")
            except Exception:
                pass
        ar.finish(scenario)
        _writer.write_result(ar)
        _suite_results.append(ar)

    # NOOD_0187 — @record_video: the .webm was recorded and never referenced
    # by any report. Resolve its future path while the page is alive; the file
    # is only complete after the context closes below, so the attach (and a
    # result re-write) happens at the end of this function.
    video_path = None
    if ar is not None:
        try:
            vid = getattr(ctx_get(context, "page", None), "video", None)
            if vid is not None:
                video_path = str(vid.path())
        except Exception:
            video_path = None

    # Stop tracing BEFORE closing the context (tracing.stop needs it alive).
    # Save the zip only when the scenario failed; otherwise discard.
    # NOOD_0025: @api/@appium scenarios return early in before_scenario and
    # never set context._tracing — behave's Context.__getattr__ raises
    # KeyError (not AttributeError) for unset attrs, so plain getattr(...,
    # False) doesn't default here and blew up as a HOOK-ERROR on every
    # non-web scenario, doubling the run via auto-retry.
    if ctx_get(context, "_tracing", False):
        bctx = getattr(context, "_bctx", None)
        try:
            if context._scenario_failed and bctx is not None:
                traces_dir = _paths.traces_dir()
                os.makedirs(traces_dir, exist_ok=True)
                safe_name = scenario.name.replace(" ", "_").replace("/", "_")[:80]
                trace_path = str(traces_dir / f"{safe_name}.zip")
                bctx.tracing.stop(path=trace_path)
                logger.info(f"\n  🧭 Trace saved: {trace_path}"
                            f"\n     View it: playwright show-trace {trace_path}")
            elif bctx is not None:
                bctx.tracing.stop()       # passed — discard
        except Exception:
            pass

    # Phase G4 — kill any app launched via "launches the app ..." even on
    # failure (mirrors @precondition's teardown-even-on-failure guarantee).
    from noodle import app_lifecycle
    app_lifecycle.stop_all()

    # Phase F — quit the Appium session even on scenario failure.
    mobile = ctx_get(context, "_mobile")
    if mobile is not None:
        try:
            mobile.quit()
        except Exception:
            pass
        context._mobile = None

    # Phase J — close named contexts before the primary one.
    for bctx in (ctx_get(context, "_named_bctxs") or {}).values():
        try:
            bctx.close()
        except Exception:
            pass

    # Bug 6: clean up each resource independently so a failure on one
    # (e.g. _bctx never created) does not skip stopping _pw and leak
    # an orphaned browser process.
    # NOOD_0183 — with browser reuse on (the default) only the CONTEXT closes
    # here: it is the unit that owns cookies, storage, cache, permissions and
    # pages, so a fresh one per scenario is the same isolation the old
    # relaunch bought, minus 0.64s of process spawning. The browser and the
    # Playwright driver stay in the per-process cache and close in after_all.
    _teardown = [("_bctx", lambda r: r.close())]
    if not _reuse_browsers():
        # NOOD_0203 — the browser goes, the driver does not: it is the
        # per-process singleton, stopped once in close_cached_browsers().
        _teardown += [("_browser", lambda r: r.close())]
    for attr, method in _teardown:
        # NOOD_0025: none of these are set for @api/@appium scenarios — same
        # ctx_get requirement as above.
        resource = ctx_get(context, attr, None)
        if resource is not None:
            try:
                method(resource)
            except Exception:
                pass

    # NOOD_0187 — the context is closed, so the video file is complete: attach
    # it and re-write the result JSON (same uuid, plain overwrite).
    if video_path and ar is not None and Path(video_path).is_file():
        try:
            ar.add_attachment("video", video_path, "video/webm")
            _writer.write_result(ar)
        except Exception:
            pass

    # NOOD_0183 — release every lock this scenario took, pass or fail. Runs
    # after teardown so the next lane never sees a half-closed browser holding
    # the shared account.
    from noodle import runlock
    runlock.release_all()

    # NOOD_0173 — scenario.end telemetry (feature/scenario ride the correlation
    # context; status is behave's final Status enum).
    _sc_t0 = ctx_get(context, "_noodle_scenario_t0", None)
    _sc_status = (getattr(getattr(scenario, "status", None), "name", None)
                  or str(getattr(scenario, "status", "")))
    _sc_ms = int((time.monotonic() - _sc_t0) * 1000) if _sc_t0 else None
    # NOOD_0202 — the maven-style result line: icon, name, elapsed.
    _verdict = {"passed": "PASSED", "failed": "FAILED", "error": "FAILED",
                "skipped": "SKIPPED", "untested": "SKIPPED"}.get(
                    _sc_status, (_sc_status or "UNKNOWN").upper())
    # isinstance, not `or 1`: a MagicMock context double returns a Mock here and
    # Mock has no ordering, so the marker's `<= 1` would raise inside a hook.
    _attempt = ctx_get(context, "_noodle_attempt", 1)
    _attempt = _attempt if isinstance(_attempt, int) else 1
    # A red scenario logs at ERROR so `grep '^\[ERROR\]'` over a CI build log
    # yields exactly the failures — the first thing anyone does with one.
    log.telemetry("scenario.end",
                  f"  {_verdict}: {scenario.name}"
                  + (f" ({_sc_ms / 1000:.1f}s)" if _sc_ms is not None else "")
                  + _retry_tag(_attempt),
                  level=logging.ERROR if _verdict == "FAILED" else logging.INFO,
                  status=_sc_status, duration_ms=_sc_ms, attempt=_attempt,
                  tags=sorted(scenario.effective_tags))


_worker_atexit_registered = False


def _worker_process_cleanup():
    """NOOD_0187 — close the cached browsers and free this worker's lane at
    PROCESS exit. behavex runs after_all once per FEATURE FILE (not once per
    worker process), so closing there rebuilt the browser for every file —
    the NOOD_0183 reuse win vanished under --parallel — and releasing the lane
    per feature made a worker's per-lane credentials churn mid-run."""
    close_cached_browsers()
    from noodle import runlock
    runlock.release_worker_index()


@_report_engine_crash
def after_all(context):
    _run_hooks("after_all", context)
    global _worker_atexit_registered
    from noodle import runlock
    runlock.release_all()
    parallel = os.getenv("NOODLE_PARALLEL_WORKER") == "1"
    if parallel and _reuse_browsers():
        if not _worker_atexit_registered:
            import atexit
            atexit.register(_worker_process_cleanup)
            _worker_atexit_registered = True
    else:
        # NOOD_0183 — the reused browsers outlive every scenario; close before
        # the report build so a wedged browser can't keep the process alive
        # past the artifacts a run is judged on.
        close_cached_browsers()
        runlock.release_worker_index()
    rdir = _paths.results_dir()
    # In parallel mode keep every output inside the worker's own dir and skip
    # the report build — the CLI merges all worker dirs into one report once.
    # NOOD_0187 — behavex fires after_all per FEATURE in a reused worker
    # process, so fixed filenames (junit.xml, healing-report.txt, the cost
    # ledger) were overwritten by every feature and the merged artifacts kept
    # only each worker's LAST feature. Parallel outputs get a unique suffix;
    # the CLI merge globs them all.
    import uuid as _uuid
    slice_id = _uuid.uuid4().hex[:8]
    healing.write_report(
        str(rdir / f"healing-report.{slice_id}.txt") if parallel
        else str(_paths.reports_dir() / "healing-report.txt")
    )
    # NOOD_0080 — persist this run's LLM spend BEFORE the RCA render below
    # (its footer reads llm_cost*.json). Workers write per-pid files; the
    # CLI's merge flattens them and readers sum every llm_cost*.json.
    from noodle.llm import cost as _llm_cost
    _llm_cost.write_json(rdir, suffix=slice_id if parallel else None)
    if not parallel:
        logger.info(f"\n  💰 {_llm_cost.format_line()}")
    if _REPORTING:
        # NOOD_0022 — environment.properties + categories.json so the report's
        # Environment and Categories widgets aren't empty. Written into the
        # (worker) results dir; the parallel merge flattens them up.
        # NOOD_0187 — no longer gated on _suite_results: an empty run writes an
        # empty junit.xml and an honest empty report instead of leaving last
        # run's artifacts in place for CI to publish as this run's.
        from noodle.reporting import allure_meta
        allure_meta.write_meta(rdir)
        # junit.xml must stay OUT of allure-results — allure generate ingests
        # every format it finds there, so a junit copy doubles each scenario.
        # Parallel workers keep theirs in the worker dir; the CLI merges them.
        _junit.write_junit(
            _suite_results,
            str(rdir / f"junit.{slice_id}.xml") if parallel
            else str(_paths.reports_dir() / "junit.xml"),
            results_dir=rdir,
        )
        if not parallel:
            # NOOD_0187 — stamp retried-then-green results as flaky before the
            # report build so Allure's Retries tab shows them.
            from noodle.reporting import summary as _summary
            _summary.mark_flaky(str(rdir))
            _builder.generate()
            # NOOD_0082 — heuristic RCA is free (no LLM call); write rca.md +
            # rca.html alongside the Allure report on EVERY run (a green run
            # renders the "no failures" page) so `noodle report serve` always
            # has both to host. The `--llm` narrative stays opt-in.
            _rca_report.write_reports(str(rdir), str(_paths.reports_dir()))


def _load_keyvault():
    """Load secrets from Azure Key Vault when NOODLE_KEYVAULT_URL is set
    (managed identity / az login in CI). No URL → no-op, .env is used. Fetched
    secrets override env so the vault is the source of truth when configured."""
    url = os.getenv("NOODLE_KEYVAULT_URL")
    # Azure leaves "$(VAR)" literal when the variable is undefined — treat that
    # (and empty) as "no vault configured".
    if not url or url.startswith("$("):
        return
    try:
        from noodle.secrets_akv import load_into_environ
    except ImportError as e:
        raise RuntimeError(
            "NOODLE_KEYVAULT_URL is set but the Azure SDK is missing — "
            "install with: pip install noodle[azure]"
        ) from e
    count = load_into_environ(url)
    logger.info(f"\n  🔑 Loaded {count} secret(s) from Key Vault")
