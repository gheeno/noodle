import json
import os
import re
from collections import defaultdict
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from playwright.sync_api import sync_playwright

from noodle import config as config_module
from noodle import healing, log
from noodle.agents.web import locator as locator_module
from noodle.agents.web import pom as pom_module
from noodle.log import logger
from noodle.reporting import paths as _paths

_VALID_BROWSERS = {"chromium", "firefox", "webkit", "safari", "edge"}

# NOOD_0052 — Safari rides Playwright's WebKit engine (same as `webkit`, the
# friendlier name); Edge is Chromium launched through the locally-installed
# Edge browser channel — Microsoft Edge must be installed on the machine.
_ENGINE_ALIASES = {"safari": ("webkit", None), "edge": ("chromium", "msedge")}

# --- Custom hook registry ---
# Valid events mirror behave's lifecycle names.
_VALID_EVENTS = frozenset({
    "before_all", "before_feature", "before_scenario",
    "after_step", "after_scenario", "after_all",
})
_registry: dict[str, list] = defaultdict(list)


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
        data = yaml.safe_load(path.read_text()) or {}
        for key, value in data.items():
            k = key.upper()
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


def before_all(context):
    # NOOD_0062 — explicit ".env": bare load_dotenv() find_dotenv()-walks up
    # from THIS file's directory, not the workspace cwd, so a workspace's own
    # .env was silently skipped whenever the engine ran as a package.
    log._secret_values.clear()             # NOOD_0118 — fresh redaction set per run, before we register any
    _snapshot_shell_env()                  # NOOD_0133 — pre-run env keys beat every config file
    load_dotenv(".env")                    # config (committed) — cwd = workspace
    _load_secrets(Path("secrets.env"))     # secrets (gitignored) — soon AKV
    _load_environments()
    _loaded_package_dirs.clear()
    _suite_results.clear()
    if os.getenv("NOODLE_PARALLEL_WORKER") == "1":
        # Each behavex worker is its own process — give it a private results
        # subdir so workers don't wipe/overwrite each other's files. The CLI
        # cleans the shared parent once, before spawning the workers.
        os.environ["NOODLE_RESULTS_DIR"] = str(_paths.artifacts_root() / "allure-results" / f"p{os.getpid()}")
        log.attach_file_handler(str(_paths.logs_dir() / f"noodle.p{os.getpid()}.log"))
    else:
        _clean_allure_results()
        log.attach_file_handler(str(_paths.logs_dir() / "noodle.log"))
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


def _clean_allure_results():
    """Delete stale per-scenario JSON + junit from a previous run so the report
    and the quarantine exit-code scan (cli.py) only reflect THIS run. Keeps the
    dir itself and reports/allure-history/ (trend data lives there)."""
    results = _paths.results_dir()
    if not results.is_dir():
        return
    for f in results.glob("*-result.json"):
        f.unlink(missing_ok=True)
    for f in results.glob("*-attachment.*"):
        f.unlink(missing_ok=True)
    # junit.xml now lives in reports/, but clean a pre-move leftover too —
    # allure generate would ingest it and double-count every scenario.
    (results / "junit.xml").unlink(missing_ok=True)


def before_feature(context, feature):
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


def _tag_value(tags, prefix: str) -> str | None:
    """The value of a @prefix:value tag, or None."""
    return next((t.split(':', 1)[1] for t in tags if t.startswith(prefix + ':')), None)


def ignore_https_errors(tags) -> bool:
    """NOOD_0089 — most sites under test live in dev/sandbox environments with
    self-signed or missing certs, so TLS errors are ignored BY DEFAULT in every
    browser (it's a context option, so chromium/firefox/webkit and the
    safari/edge aliases all get it). Toggle back per-scenario with
    @secure_certs, or run-wide with NOODLE_IGNORE_HTTPS_ERRORS=false, when a
    test must see the certificate error."""
    if 'secure_certs' in tags:
        return False
    return os.getenv("NOODLE_IGNORE_HTTPS_ERRORS", "true").lower() not in ("0", "false", "no")


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
    console_errors: list = []
    page_errors: list = []
    failed_requests: list = []
    requests: list = []
    mutations: list = []
    failed_responses: list = []
    ws_frames: list = []
    context._console_errors = console_errors
    context._page_errors = page_errors
    context._failed_requests = failed_requests
    context._requests = requests
    context._mutations = mutations
    context._failed_responses = failed_responses
    context._ws_frames = ws_frames

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

    context.page.on("console", _on_console)
    context.page.on("pageerror", _on_page_error)
    context.page.on("requestfailed", _on_request_failed)
    context.page.on("request", _on_request)
    context.page.on("response", _on_response)
    context.page.on("websocket", _on_websocket)


def before_scenario(context, scenario):
    tags = set(scenario.effective_tags)

    # @live scenarios hit a real external site — opt-in only, so CI and casual
    # runs never make surprise network calls. Set NOODLE_RUN_LIVE=1 to run.
    if 'live' in tags and os.getenv("NOODLE_RUN_LIVE", "").lower() not in ("1", "true", "yes"):
        scenario.skip("@live is opt-in — set NOODLE_RUN_LIVE=1 to run real-site tests")
        return

    # @llm scenarios need a model at run time — skip (not fail) when none is
    # configured, so the no-LLM default run stays green (NOOD_0065).
    if 'llm' in tags and not os.getenv("NOODLE_MODEL"):
        scenario.skip("@llm needs NOODLE_MODEL in .env (e.g. anthropic/claude-sonnet-5) — "
                      "see README § LLM augmentation")
        return

    # @terminal scenarios need the OCR engine — skip (not fail) where tesseract
    # isn't installed, so a suite stays green on a box without the [visual] extra.
    if 'terminal' in tags and not _ocr_available():
        scenario.skip("OCR engine (tesseract) not installed — pip install noodle[visual]")
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
    context._evidence_request = False      # NOOD_0153 — "( take a screenshot )" marker
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
            scenario.skip(
                "Appium client not installed — pip install noodle[mobile] "
                "(and a running Appium server + device/emulator)"
            )
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

    context._pw = sync_playwright().start()
    engine, channel = _ENGINE_ALIASES.get(browser_name, (browser_name, None))
    browser_type = getattr(context._pw, engine)
    # Phase H (F4) — NOODLE_REMOTE_URL points at a remote Playwright/CDP
    # endpoint (BrowserStack, Sauce Labs, a Playwright grid): connect instead
    # of launching locally. The rest of the lifecycle is identical.
    remote_url = os.getenv("NOODLE_REMOTE_URL")
    if remote_url:
        context._browser = browser_type.connect(remote_url, slow_mo=slow_mo)
        logger.info(f"\n  🌐 Connected to remote browser: {remote_url.split('?')[0]}")
    else:
        launch_opts = {"headless": headless, "slow_mo": slow_mo}
        if channel:
            launch_opts["channel"] = channel
        context._browser = browser_type.launch(**launch_opts)

    ctx_opts = {"ignore_https_errors": ignore_https_errors(tags)}
    if 'mobile' in tags:
        device_name = "iPhone 13" if 'iphone' in tags else "Pixel 5"
        ctx_opts.update(context._pw.devices[device_name])
    viewport = _viewport_from(tags)
    if viewport:                            # explicit size wins over device preset
        ctx_opts['viewport'] = viewport
    if 'record_video' in tags:
        videos_dir = _paths.videos_dir()
        os.makedirs(videos_dir, exist_ok=True)
        ctx_opts['record_video_dir'] = str(videos_dir)
    ctx_opts.update(_emulation_opts(tags))  # Phase N/O — geo/perms/locale/tz/scheme/offline

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
    unmatched step counts as capturable."""
    try:
        from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject
        for step in reversed(list(getattr(scenario, "steps", None) or [])):
            m = match(normalize_phrasing(normalize_subject(step.name)))
            if m is None or m[0] not in _PAGE_KILLING_ACTIONS:
                return step
    except Exception:
        pass
    return None


def after_step(context, step):
    _run_hooks("after_step", context, step)
    # NOOD_0018 — drain this step's ⚠️ WARNING+ log lines regardless of
    # pass/fail, so they never bleed into the next step's capture.
    step_warnings = log.get_warnings()
    log.clear_warnings()
    # NOOD_0156 — this step's healing events (snapshot taken by
    # runner.execute_step): recorded on the step result whether it passed or
    # failed, so the run payload surfaces every substitution — the Canadian
    # Tire false pass hid two of them behind a green exit code.
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
        # on this step; clear the flags so they can't leak into a retry.
        context._evidence_request = False
        context._manual_screenshot = None
        shots_dir = _paths.screenshots_dir()
        os.makedirs(shots_dir, exist_ok=True)
        safe_name = step.name.replace(" ", "_").replace("/", "_")[:80]
        raw_path = str(shots_dir / f"FAILED_{safe_name}.png")
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
                    if marked.get("matched") or marked.get("expected"):
                        annotated_path = _annotate.draw_failure_markers(
                            raw_path, step.name[:60], marked)
                    else:
                        annotated_path = _annotate.draw_not_found(raw_path, step.name[:60])
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
                step_warnings = (([note] if note else [])
                                 + ([stuck] if stuck else [])
                                 + ([resp] if resp else [])
                                 + [f"URL: {context.page.url}", *step_warnings])
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
        requested = bool(ctx_get(context, "_evidence_request"))
        context._manual_screenshot = None
        context._evidence_request = False
        page = ctx_get(context, "page")
        if manual:
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
                if _evidence.wanted(tags, requested, is_last, page is not None):
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
                        evidence_meta["valid"] = ((bool(fresh)
                                                   or evidence_meta.get("refocused") is True)
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
            net_path.write_text(json.dumps({
                "console_errors": context._console_errors,
                "page_errors": context._page_errors,
                "failed_requests": context._failed_requests,
                "requests": context._requests,
                # NOOD_0156 — mutation-aware RCA inputs
                "mutations": ctx_get(context, "_mutations", []),
                "failed_responses": ctx_get(context, "_failed_responses", []),
                "ws_frames": context._ws_frames,
            }, indent=2))
            ar_net = _allure_result(context)
            if ar_net is not None:
                ar_net.add_attachment("network log", str(net_path), "application/json")
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

    ar = _allure_result(context)
    if ar is not None:
        ar.finish(scenario)
        _writer.write_result(ar)
        _suite_results.append(ar)

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
    for attr, method in [
        ("_bctx",    lambda r: r.close()),
        ("_browser", lambda r: r.close()),
        ("_pw",      lambda r: r.stop()),
    ]:
        # NOOD_0025: none of these are set for @api/@appium scenarios — same
        # ctx_get requirement as above.
        resource = ctx_get(context, attr, None)
        if resource is not None:
            try:
                method(resource)
            except Exception:
                pass


def after_all(context):
    _run_hooks("after_all", context)
    parallel = os.getenv("NOODLE_PARALLEL_WORKER") == "1"
    rdir = _paths.results_dir()
    # In parallel mode keep every output inside the worker's own dir and skip
    # the report build — the CLI merges all worker dirs into one report once.
    healing.write_report(
        str(rdir / "healing-report.txt") if parallel
        else str(_paths.reports_dir() / "healing-report.txt")
    )
    # NOOD_0080 — persist this run's LLM spend BEFORE the RCA render below
    # (its footer reads llm_cost*.json). Workers write per-pid files; the
    # CLI's merge flattens them and readers sum every llm_cost*.json.
    from noodle.llm import cost as _llm_cost
    _llm_cost.write_json(rdir)
    if not parallel:
        logger.info(f"\n  💰 {_llm_cost.format_line()}")
    if _REPORTING and _suite_results:
        # NOOD_0022 — environment.properties + categories.json so the report's
        # Environment and Categories widgets aren't empty. Written into the
        # (worker) results dir; the parallel merge flattens them up.
        from noodle.reporting import allure_meta
        allure_meta.write_meta(rdir)
        # junit.xml must stay OUT of allure-results — allure generate ingests
        # every format it finds there, so a junit copy doubles each scenario.
        # Parallel workers keep theirs in the worker dir; the CLI merges them.
        _junit.write_junit(
            _suite_results,
            str(rdir / "junit.xml") if parallel else str(_paths.reports_dir() / "junit.xml"),
        )
        if not parallel:
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
