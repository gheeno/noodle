import json
import os
import re

from noodle import app_lifecycle
from noodle.agents.web import actions
from noodle.log import logger
from noodle.orchestrator import script_runner
from noodle.resolver.step_resolver import resolve


class SoftAssertionReport(AssertionError):
    """Raised by 'all soft assertions should pass' (Phase L). A distinct type
    so the @soft handler in catch_all re-raises it instead of collecting the
    report itself as one more soft failure."""


_SENSITIVE_VAR_RE = re.compile(r"token|auth|secret|password|passwd|key|jwt|bearer", re.I)


def _safe_repr(name: str, value) -> str:
    """Mask a stored/extracted variable's value in log output if its name
    looks sensitive (token, auth, secret, password, key, jwt, bearer) — the
    value itself is still stored in context._vars unmasked, only the log
    line changes. Header dicts are never logged directly, but an extracted
    value (e.g. a JWT pulled out via 'extract token') was previously printed
    in full regardless of what it was named."""
    if _SENSITIVE_VAR_RE.search(name):
        s = str(value)
        return repr(s[:6] + "...") if len(s) > 6 else "'***'"
    return repr(value)


def ctx_get(context, name: str, default=None):
    """getattr with a default that also survives behave's Context, whose
    __getattr__ raises KeyError (not AttributeError) for unset attributes."""
    try:
        return getattr(context, name, default)
    except KeyError:
        return default


def _store_script_output(context, out: str, var: str | None):
    """Stash a script's stdout in `SCRIPT_OUTPUT` (always) and an optional named
    var, so a later step can assert on it (e.g. `SCRIPT_OUTPUT` should contain …)."""
    context._vars['SCRIPT_OUTPUT'] = out
    if var:
        context._vars[var.upper().replace(" ", "_")] = out


# One warning per unique legacy ref per run, not one per occurrence — a ref
# repeated across every scenario would otherwise drown the log.
_deprecation_warned: set[str] = set()


def _warn_deprecated(old: str, new: str) -> None:
    if old in _deprecation_warned:
        return
    _deprecation_warned.add(old)
    logger.warning(f"\n  ⚠️  Deprecated syntax {old} — use {new} instead")


def substitute(text: str, extra: dict | None = None) -> str:
    """Expand parameter references (NOOD_0033 unified syntax):

      {env:name}  → config: real OS env → .env → secrets.env →
                    environments.yaml (all loaded into os.environ at startup),
                    with captured-store fallback.
      {var:name}  → a value captured during this run (set/store/extract/
                    function result) — NEVER config.

    {pom:...} is not substituted here — it flows through to the locator
    (agents/web/pom.py). The prefix anchor means literal JSON in step text
    ('{"name":"Alice"}') is never touched. Unknown refs are left untouched.

    Legacy forms `name` (var) and [name] (env) still resolve, with a
    deprecation warning once per unique ref per run.
    """
    extra = extra or {}
    def _key(raw: str) -> str:
        return raw.strip().upper().replace(" ", "_")

    def env_lookup(key: str) -> str | None:
        if key in extra:
            return extra[key]
        return os.getenv(key)

    def unified(m):
        source, name = m.group(1), m.group(2)
        key = _key(name)
        val = extra.get(key) if source == 'var' else env_lookup(key)
        return m.group(0) if val is None else val
    text = re.sub(r'\{(env|var):([^}]+)\}', unified, text)

    def backtick(m):                       # legacy — captured values only
        key = _key(m.group(1))
        if key in extra:
            _warn_deprecated(f"`{m.group(1)}`", f"{{var:{m.group(1)}}}")
            return extra[key]
        return m.group(0)
    text = re.sub(r'`([^`]+)`', backtick, text)

    def bracket(m):                        # legacy — .env / config
        key = _key(m.group(1))
        val = env_lookup(key)
        if val is None:
            return m.group(0)
        _warn_deprecated(f"[{m.group(1)}]", f"{{env:{m.group(1)}}}")
        return val
    return re.sub(r'\[([^\]]+)\]', bracket, text)


# NOOD_0062 — headings that read as generic labels. Gherkin always treats the
# first table row as headings, so a tester who omits the label row loses their
# first data row silently. If the headings aren't generic labels, they ARE data.
_TABLE_LABEL_HEADINGS = frozenset({
    'field', 'fields', 'value', 'values', 'key', 'keys', 'name', 'names',
    'column', 'columns', 'header', 'headers', 'row', 'rows', 'label',
    'labels', 'input', 'setting', 'option', 'payload', 'data', 'text',
})


def _table_cells(context, step_text: str, headings_as_data: bool = True) -> list[list[str]]:
    """Rows of the step's Gherkin data table as lists of [VAR]-substituted cell
    values. Headings are labels only (like the REST table asserts) — unless
    they don't look like labels, in which case they're treated as the first
    data row (NOOD_0062: a table written without a `| field | value |` header
    row would otherwise silently drop its first entry). Pass
    headings_as_data=False for steps whose headings carry meaning of their
    own (assert_table_rows uses them as column names)."""
    if getattr(context, 'table', None) is None:
        raise AssertionError(
            f"This step needs a Gherkin data table under it: \"{step_text}\"\n"
            "  → End the step with ':' and indent a | … | table below it"
        )
    rows = [[substitute(c, context._vars) for c in row.cells]
            for row in context.table]
    headings = list(getattr(context.table, 'headings', None) or [])
    if headings_as_data and headings and \
            not all(h.strip().lower() in _TABLE_LABEL_HEADINGS for h in headings):
        rows.insert(0, [substitute(h, context._vars) for h in headings])
    return rows


def _row_get(row, name: str, default: str | None = None) -> str | None:
    """Case-insensitive Gherkin table cell lookup (NOOD_0062) — `| KEY |` and
    `| key |` both satisfy a step that documents `| Key |`."""
    for heading, cell in zip(row.headings, row.cells):
        if heading.strip().lower() == name.lower():
            return cell
    return default


def _pages(context):
    """Every open page in the scenario's browser context (newest last)."""
    bctx = getattr(context, "_bctx", None)
    return list(bctx.pages) if bctx is not None else [context.page]


def _focus(context, page):
    """Make `page` the active page. Popup tabs don't inherit the scenario's
    default timeout, so re-apply it here or assertions on a new tab wait 30s."""
    context.page = page
    to = getattr(page, "set_default_timeout", None)
    if to is not None:
        to(int(os.getenv("NOODLE_TIMEOUT", "10000")))
    front = getattr(page, "bring_to_front", None)
    if front is not None:
        front()


def _switch_tab(context, target, assert_opened=False):
    """Point context.page at another open tab. ponytail: previous/first/main all
    mean pages[0] — a real back-stack only matters past 2 tabs, add then."""
    if assert_opened:
        page = context.page
        timeout_ms = int(os.getenv("NOODLE_TIMEOUT", "10000"))
        from playwright._impl._errors import TimeoutError as _PWTimeout
        try:
            # wait_for_event("popup") retrieves the queued popup event even if
            # the click already fired — no need to wrap the click.
            new_page = page.wait_for_event("popup", timeout=timeout_ms)
            _focus(context, new_page)
            return
        except _PWTimeout:
            raise AssertionError("Expected a new tab to open, but only one tab is open")
    pages = _pages(context)
    _focus(context, pages[-1] if target in ('new', 'last') else pages[0])


def _close_tab(context):
    if len(_pages(context)) > 1:
        context.page.close()
        _focus(context, _pages(context)[0])


# Action types that never touch the page — legal in browser-less @api scenarios.
_BROWSERLESS_TYPES = frozenset({
    'assert_compare', 'call_function', 'load_data', 'load_resource', 'run_command',
    'run_script', 'set_var', 'wait_seconds',
    # Phase G4 — app lifecycle; Phase L — soft assertion report
    'app_launch', 'app_assert_running', 'app_stop', 'soft_assert_check',
})

# Steps the mobile agent handles when a scenario is tagged @appium — or a
# platform tag: @android/@ios/@windows/@mac (Phase F; NOOD_0032).
_MOBILE_TYPES = frozenset({
    'click', 'fill', 'type_text', 'swipe', 'device_key',
    'assert_visible', 'assert_hidden', 'wait_visible',
    'long_press', 'hide_keyboard', 'background_app', 'screenshot',
})


def _execute_mobile(context, action):
    """Phase F / NOOD_0032 — dispatch a step to the Appium driver
    (Android, iOS, Windows 11 native apps, macOS)."""
    from noodle.agents.mobile import actions as mobile_actions
    d = context._mobile
    t = action['type']
    if t == 'click':
        mobile_actions.tap(d, action['locator'])
    elif t == 'fill':
        mobile_actions.fill(d, action['locator'], action['value'])
    elif t == 'type_text':
        mobile_actions.type_text(d, action['text'])
    elif t == 'swipe':
        mobile_actions.swipe(d, action['direction'])
    elif t == 'device_key':
        mobile_actions.device_key(d, action['key'])
    elif t == 'long_press':
        mobile_actions.long_press(d, action['locator'])
    elif t == 'hide_keyboard':
        mobile_actions.hide_keyboard(d)
    elif t == 'background_app':
        mobile_actions.background_app(d, action['seconds'])
    elif t == 'screenshot':
        from noodle.reporting import paths as _rpaths
        mobile_actions.screenshot(d, action['name'], str(_rpaths.screenshots_dir()))
    elif t == 'assert_visible':
        mobile_actions.assert_visible(d, action['text'])
    elif t == 'assert_hidden':
        mobile_actions.assert_hidden(d, action['text'])
    elif t == 'wait_visible':
        timeout_s = (action.get('timeout') or int(os.getenv("NOODLE_TIMEOUT", "10000"))) / 1000
        mobile_actions.assert_visible(d, action['text'], timeout_s)


# NOOD_0155 — woks. Performance-wok and desktop-spreadsheet steps never touch
# the page, so they dispatch before the browser guard and compose into any
# scenario: an @api or @perf run has no browser, and a @web scenario can pull
# a spreadsheet value or a load-test metric mid-flow (cross-wok).
_PERF_TYPES = frozenset({
    'perf_load', 'perf_assert_time', 'perf_assert_error_rate',
    'perf_assert_throughput', 'perf_report', 'perf_store',
})

_DESKTOP_FILE_TYPES = frozenset({'desktop_read_cell', 'desktop_assert_cell'})


def _resources_path(context, file: str) -> str:
    """Resolve a fixture path the same way load_data/load_resource do —
    relative to the feature's app resources/ folder (absolute paths win; and
    when there's no feature, e.g. the repl, fall back to the path as given)."""
    if os.path.isabs(file):
        return file
    feature = ctx_get(context, "feature")
    filename = getattr(feature, "filename", None) if feature is not None else None
    if filename:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(filename)))
        candidate = os.path.join(app_dir, 'resources', file)
        if os.path.exists(candidate) or not os.path.exists(file):
            return candidate
    return file


def _last_load(context):
    result = ctx_get(context, "_perf_result")
    if result is None:
        raise AssertionError(
            "No load test has run in this scenario — start with: "
            "When runs a load test on \"<url>\" with 10 users for 30 seconds")
    return result


def _execute_perf(context, action):
    """NOOD_0155 — performance wok: built-in load generator + assertions.
    The run's metrics live on the context, so assertion/report/store steps
    grade the most recent load test."""
    from noodle.agents.perf import loadgen
    t = action['type']
    if t == 'perf_load':
        result = loadgen.run_load(
            action['url'], users=action.get('users', 5),
            duration_s=action.get('duration_s'),
            total_requests=action.get('requests'))
        context._perf_result = result
        logger.info(f"\n  🍜 Load test: {result.summary()}")
        return
    result = _last_load(context)
    if t == 'perf_assert_time':
        got = result.metric(action['metric'])
        assert got <= action['max_ms'], \
            f"{action['metric']} response time is {got:.0f}ms — " \
            f"expected under {action['max_ms']}ms ({result.summary()})"
    elif t == 'perf_assert_error_rate':
        got = result.error_rate_pct
        assert got <= action['max_pct'], \
            f"Error rate is {got:.1f}% — expected under {action['max_pct']}% " \
            f"({result.errors} of {result.count} requests failed)"
    elif t == 'perf_assert_throughput':
        got = result.throughput_rps
        assert got >= action['min_rps'], \
            f"Throughput is {got:.1f} req/s — expected at least {action['min_rps']} req/s"
    elif t == 'perf_report':
        from noodle.agents.perf import chart
        from noodle.reporting import paths as _rpaths
        safe = action['name'].replace(" ", "_").replace("/", "_")
        path = chart.render(result, str(_rpaths.screenshots_dir() / f"{safe}.png"))
        # Same NOOD_0153 seam as the web screenshot step: hooks.after_step
        # attaches _manual_screenshot to the Allure/RCA reports.
        context._manual_screenshot = path
        logger.info(f"\n  📈 Load test chart saved: {path}")
    elif t == 'perf_store':
        key = action['var'].upper().replace(" ", "_")
        got = result.metric(action['metric'])
        context._vars[key] = f"{got:.0f}" if got == int(got) else f"{got:.2f}"
        logger.info(f"\n  💾 Stored `{key}` = {context._vars[key]} ({action['metric']})")


def _execute_desktop(context, action):
    """NOOD_0155 — desktop wok, browserless side: spreadsheet cell access
    (agents/desktop/spreadsheet). UI driving stays with the visual/Appium
    agents."""
    from noodle.agents.desktop import spreadsheet
    t = action['type']
    path = _resources_path(context, action['file'])
    value = spreadsheet.read_cell(path, action['cell'], action.get('sheet'))
    if t == 'desktop_read_cell':
        key = action['var'].upper().replace(" ", "_")
        context._vars[key] = value
        logger.info(f"\n  💾 Stored `{key}` = {value!r} "
                    f"(cell {action['cell']} of {action['file']})")
    elif t == 'desktop_assert_cell':
        expected = action['expected']
        assert value == expected, \
            f"Cell {action['cell']} of {action['file']} is {value!r} — expected {expected!r}"


def _new_named_context(context, name: str):
    """Phase J — a second, isolated browser session ('buyer'/'seller' flows)."""
    browser = ctx_get(context, "_browser")
    if browser is None:
        raise AssertionError(
            "Named browser contexts need a browser — not available in @api/@appium scenarios"
        )
    bctx = browser.new_context()
    page = bctx.new_page()
    page.set_default_timeout(int(os.getenv("NOODLE_TIMEOUT", "10000")))
    if ctx_get(context, "_named_bctxs") is None:
        context._named_bctxs, context._named_contexts = {}, {}
    context._named_bctxs[name] = bctx
    context._named_contexts[name] = page
    if ctx_get(context, "_primary_page") is None:
        context._primary_page = context.page
    logger.info(f"\n  👥 New browser context '{name}'")


def _use_named_context(context, name: str):
    """Phase J — point context.page at a named context (or back to the primary)."""
    if name.lower() in ("main", "default", "primary"):
        primary = ctx_get(context, "_primary_page")
        if primary is not None:
            context.page = primary
        return
    pages = ctx_get(context, "_named_contexts") or {}
    if name not in pages:
        raise AssertionError(
            f"No browser context named '{name}' — create it first with: "
            f"Given a new browser context as '{name}'"
        )
    if ctx_get(context, "_primary_page") is None:
        context._primary_page = context.page
    context.page = pages[name]
    logger.info(f"\n  👥 Acting as '{name}'")


def _json_path(data, path: str):
    """Walk a dotted path with optional [n] indexes ('data.items[0].id')
    through parsed JSON. Raises AssertionError naming the part that missed."""
    cur = data
    for name, idx in re.findall(r'([^.\[\]]+)|\[(\d+)\]', path):
        if name:
            if not isinstance(cur, dict) or name not in cur:
                raise AssertionError(f"Key '{name}' not found walking '{path}' in response JSON")
            cur = cur[name]
        else:
            i = int(idx)
            if not isinstance(cur, list) or i >= len(cur):
                raise AssertionError(f"Index [{i}] out of range walking '{path}' in response JSON")
            cur = cur[i]
    return cur


def _oauth2_fetch(context, url: str, client_id: str, client_secret: str):
    """Client-credentials grant → Authorization: Bearer <token> in _REST_HEADERS.
    Grant params are kept in _vars so a later 401 can refresh once (rest_call).
    The token/secret are never logged."""
    import urllib.parse

    from noodle.agents.web import rest_client
    form = urllib.parse.urlencode({
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
    })
    status, resp, _ = rest_client.rest_call(
        'POST', url, form, {'Content-Type': 'application/x-www-form-urlencoded'})
    assert status == 200, f"OAuth2 token fetch failed: {status} {resp[:200]}"
    try:
        token = json.loads(resp).get('access_token')
    except ValueError:
        token = None
    assert token, f"OAuth2 response has no access_token: {resp[:200]}"
    hdrs = json.loads(context._vars.get('_REST_HEADERS', '{}'))
    hdrs['Authorization'] = f"Bearer {token}"
    context._vars['_REST_HEADERS'] = json.dumps(hdrs)
    context._vars['_REST_OAUTH'] = json.dumps(
        {'url': url, 'client_id': client_id, 'client_secret': client_secret})


def execute_step(step_text: str, context):
    if ctx_get(context, "_vars", None) is None:
        context._vars = {}
    step_text = substitute(step_text, context._vars)
    # NOOD_0153 — evidence bookkeeping. The match-seq snapshot lets
    # hooks.after_step tell whether THIS step resolved an element (draw the
    # evidence box) or not (screenshot only — a stale box would lie). The
    # trailing "( take a screenshot )" marker flags this step for an evidence
    # shot and is stripped so the inner step resolves normally (patterns.
    # _pre_clean strips it for every non-runner resolution path too).
    from noodle.agents.web import locator as _locator
    from noodle.resolver.patterns import EVIDENCE_MARKER_RE
    context._match_seq_at_step_start = _locator.match_seq()
    # NOOD_0156 — healing snapshot: hooks.after_step attributes any healing
    # events recorded from here on to THIS step, so the run result can report
    # per-step resolution provenance (and compute `verified`).
    from noodle import healing as _healing
    context._healing_at_step_start = _healing.event_count()
    m = EVIDENCE_MARKER_RE.search(step_text)
    if m:
        step_text = step_text[:m.start()]
        context._evidence_request = True
    # "... in the new tab" (NOOD_0025) — run the rest of the step against the
    # newest page, then drop the suffix so the inner verb resolves normally.
    m = re.search(r'\s+in the (?:new|last) (?:tab|window)$', step_text, re.IGNORECASE)
    if m:
        pages = _pages(context)
        if len(pages) > 1:
            _focus(context, pages[-1])
        step_text = step_text[:m.start()]
    # NOOD_0155 — the scenario's tags pick which wok's pattern table gets
    # first claim on the sentence (wok.pattern_priority); no scenario (repl,
    # unit tests) → web-first best guess.
    scenario = ctx_get(context, "scenario")
    action = resolve(step_text,
                     tags=set(getattr(scenario, "effective_tags", None) or []))
    page = context.page

    t = action['type']

    # Phase F — @appium scenarios route supported steps to the mobile agent.
    if ctx_get(context, "_mobile") is not None and t in _MOBILE_TYPES:
        return _execute_mobile(context, action)

    # NOOD_0155 — wok steps that never need a page dispatch before the
    # browser guard, so they work in @perf/@api scenarios AND inside a @web
    # scenario (cross-wok composition).
    if t in _PERF_TYPES:
        return _execute_perf(context, action)
    if t in _DESKTOP_FILE_TYPES:
        return _execute_desktop(context, action)

    # @api/@appium scenarios run without a browser (hooks skips Playwright).
    # REST and non-UI steps work; a web step gets a clear error instead of a
    # deep AttributeError on a None page.
    if page is None and not (t.startswith('rest_') or t in _BROWSERLESS_TYPES):
        raise AssertionError(
            f"This scenario has no browser, but this step needs one: \"{step_text}\"\n"
            "  → Remove the @api/@appium tag, or use a step the agent supports"
        )

    if t == 'set_page':
        actions.set_page(action['name'])
    elif t == 'navigate':
        actions.navigate(page, action['url'])
    elif t == 'click':
        actions.click(page, action['locator'])
    # --- NOOD_0025: history, extra clicks, form submit, tab/window ----------
    elif t == 'go_back':
        actions.go_back(page)
    elif t == 'go_forward':
        actions.go_forward(page)
    elif t == 'reload':
        actions.reload(page)
    elif t == 'double_click':
        actions.double_click(page, action['locator'])
    elif t == 'right_click':
        actions.right_click(page, action['locator'])
    elif t == 'submit':
        actions.submit(page, action['locator'])
    elif t == 'assert_new_tab':
        _switch_tab(context, 'new', assert_opened=True)
    elif t == 'switch_tab':
        _switch_tab(context, action['target'])
    elif t == 'close_tab':
        _close_tab(context)
    elif t == 'fill':
        actions.fill(page, action['locator'], action['value'])
    elif t == 'clear':
        actions.clear(page, action['locator'])
    elif t == 'select':
        actions.select_option(page, action['locator'], action['value'])
    elif t == 'select_multi':
        actions.select_multi(page, action['locator'], action['values'])
    # --- NOOD_0008: JS dialogs, upload, download ------------------------------
    elif t == 'arm_dialog':
        actions.arm_dialog(page, context._vars, action['response'], action.get('answer'))
    elif t == 'assert_dialog_text':
        actions.assert_dialog_text(context._vars, action['text'])
    elif t == 'upload':
        actions.upload(page, action['locator'], action['path'])
    elif t == 'assert_download':
        actions.assert_download(page, getattr(context, '_downloads', []), action.get('name'))
    elif t == 'check':
        actions.check(page, action['locator'])
    elif t == 'uncheck':
        actions.uncheck(page, action['locator'])
    elif t == 'assert_visible':
        actions.assert_visible(page, action['text'])
    elif t == 'assert_hidden':
        actions.assert_hidden(page, action['text'])
    elif t == 'assert_url':
        actions.assert_url(page, action['fragment'], action.get('mode', 'contains'))
    elif t == 'wait_load':
        actions.wait_load(page)
    elif t == 'wait_networkidle':
        actions.wait_networkidle(page)
    elif t == 'wait_visible':
        actions.wait_visible(page, action['text'], action.get('timeout'))
    elif t == 'wait_seconds':
        actions.wait_seconds(action['seconds'])
    elif t == 'wait_url':
        actions.wait_url(page, action['fragment'], action.get('mode', 'contains'))
    elif t == 'scroll':
        actions.scroll(page, action['direction'])
    # --- NOOD_0152: waits that replace the hard sleep ------------------------
    elif t == 'wait_state':
        actions.wait_state(page, action['locator'], action['state'], action.get('timeout'))
    elif t == 'wait_count':
        actions.wait_count(page, action['locator'], action['count'],
                           action.get('op', '=='), action.get('timeout'))
    elif t == 'wait_text_change':
        actions.wait_text_change(page, action['locator'], action.get('was'),
                                 action.get('timeout'))
    elif t == 'wait_response':
        actions.wait_response(page, action['fragment'], action.get('timeout'))
    elif t == 'scroll_container':
        actions.scroll_container(page, action['locator'], action['direction'])
    elif t == 'scroll_until_visible':
        actions.scroll_until_visible(page, action.get('text'))
    elif t == 'scroll_edge':
        actions.scroll_edge(page, action['edge'])
    elif t == 'scroll_to':
        actions.scroll_to(page, action['locator'])
    elif t == 'assert_title':
        actions.assert_title(page, action['fragment'])
    elif t == 'assert_semantic':
        actions.assert_semantic(page, action['assertion'])
    elif t == 'visual_baseline':
        actions.visual_baseline(page, action['name'], action.get('ignore'))
    elif t == 'pixel_baseline':
        actions.pixel_baseline(page, action['name'])
    elif t == 'screenshot':
        # NOOD_0153 — remember the path so hooks.after_step attaches the
        # explicit "takes a screenshot" shot to the Allure/RCA reports too
        # (it used to land on disk only, invisible to both reports).
        context._manual_screenshot = actions.screenshot(page, action['name'])
    elif t == 'search':
        actions.search(page, action['query'])
    # --- NOOD_0141: typeahead suggestions -------------------------------------
    elif t == 'select_suggestion':
        actions.select_suggestion(page, action['option'], action.get('term'))
    elif t == 'assert_suggestion':
        actions.assert_suggestions_include(page, action.get('text'), action.get('term'))
    elif t == 'close_popups':
        actions.close_popups(page, within=action.get('within', 0),
                             deny_permissions=action.get('deny_permissions'))
    # --- NOOD_0044: conditional steps ----------------------------------------
    elif t == 'run_if':
        visible = actions.is_visible(page, action['condition'])
        if visible != bool(action.get('negate')):
            execute_step(action['then'], context)
        else:
            logger.info(
                f"\n  🔀 Condition not met ('{action['condition']}'"
                f"{' absent' if not action.get('negate') else ' present'})"
                f" — skipped: {action['then']}"
            )
    # --- Phase 11 ---
    elif t == 'press_key':
        actions.press_key(page, action['key'])
    elif t == 'hover':
        actions.hover(page, action['locator'])
    elif t == 'wait_hidden':
        actions.wait_hidden(page, action['text'], action.get('timeout'))
    elif t == 'assert_value':
        actions.assert_value(page, action['locator'], action['value'])
    elif t == 'assert_matches':
        actions.assert_matches(page, action['locator'], action['pattern'])
    elif t == 'assert_number_tolerance':
        actions.assert_number_tolerance(page, action['locator'], action['expected'],
                                        action['tolerance'])
    elif t == 'assert_number_between':
        actions.assert_number_between(page, action['locator'], action['low'], action['high'])
    elif t == 'fill_date':
        actions.fill_date(page, action['locator'], action['offset_days'])
    elif t == 'switch_frame_chain':
        actions.switch_frame_chain(page, action['names'])
    elif t == 'store_clipboard':
        context._vars[action['var']] = actions.store_clipboard(page)
        logger.info(f"\n  💾 Stored `{action['var']}` = "
                    f"{_safe_repr(action['var'], context._vars[action['var']])}")
    elif t == 'assert_download_content':
        actions.assert_download_content(page, ctx_get(context, '_downloads', []),
                                        action.get('needle'), action.get('rows'))
    elif t == 'assert_value_not':
        actions.assert_value_not(page, action['locator'], action['value'])
    elif t == 'assert_state':
        actions.assert_state(page, action['locator'], action['state'])
    elif t == 'assert_attribute':
        actions.assert_attribute(page, action['locator'], action['attribute'], action['value'])
    elif t == 'assert_css':
        actions.assert_css(page, action['locator'], action['prop'], action['value'])
    elif t == 'assert_focused':
        actions.assert_focused(page, action['locator'])
    elif t == 'assert_count':
        actions.assert_count(page, action['count'], action['locator'], action.get('op', '=='))
    elif t == 'assert_number':
        actions.assert_number(page, action['locator'], action['count'], action.get('op', '=='))
    elif t == 'store_text':
        key = action['var'].upper().replace(" ", "_")
        context._vars[key] = actions.get_text(page, action['locator'])
        logger.info(f"\n  💾 Stored `{key}` = {_safe_repr(key, context._vars[key])}")
    elif t == 'click_in_row':
        actions.click_in_row(page, action['locator'], action['row'])
    elif t == 'click_in_section':
        actions.click_in_section(page, action['locator'], action['section'])
    elif t == 'assert_cell':
        actions.assert_cell(page, action['row'], action['column'], action['expected'])
    elif t == 'assert_row_count':
        actions.assert_row_count(page, action['count'])
    # --- NOOD_0011: grids & tables, session persistence -----------------------
    elif t == 'click_cell':
        actions.click_cell(page, action['row'], action['column'])
    elif t == 'scroll_table':
        actions.scroll_table(page, action['direction'], action.get('name'))
    elif t == 'assert_row_values':
        if action.get('values') is not None:       # inline quoted list
            actions.assert_row_values(page, action['row'], action['values'])
        else:                                      # | column | value | table
            pairs = [(c[0], c[1]) for c in _table_cells(context, step_text)]
            actions.assert_row_columns(page, action['row'], pairs)
    elif t == 'assert_table_headers':
        names = action.get('names')
        if names is None:                          # | column | table, one per row
            names = [c[0] for c in _table_cells(context, step_text)]
        actions.assert_table_headers(page, names)
    elif t == 'assert_column_contains':
        values = action.get('values')
        if values is None:                         # | value | table, one per row
            values = [c[0] for c in _table_cells(context, step_text)]
        actions.assert_column_contains(page, action['column'], values)
    elif t == 'assert_column_sorted':
        actions.assert_column_sorted(page, action['column'], action.get('descending', False))
    elif t == 'assert_table_rows':
        rows = _table_cells(context, step_text, headings_as_data=False)
        headings = [substitute(h, context._vars) for h in context.table.headings]
        actions.assert_table_rows(page, headings, rows)
    elif t == 'fill_form_table':
        for cells in _table_cells(context, step_text):
            actions.fill(page, cells[0], cells[1])
    elif t == 'save_session':
        actions.save_session(context, action['path'])
    elif t == 'switch_frame':
        actions.switch_frame(page, action['name'])
    # --- NOOD_0009: drag & drop, cookies/storage, iframe exit, scoped steps ---
    elif t == 'switch_main_frame':
        actions.switch_main_frame()
    elif t == 'drag':
        actions.drag(page, action['source'], action['target'])
    # --- NOOD_0152: mouse-level interactions ---------------------------------
    elif t == 'mouse_drag':
        actions.mouse_drag(page, action['locator'], action.get('dx', 0), action.get('dy', 0))
    elif t == 'mouse_drag_to':
        actions.mouse_drag_to(page, action['source'], action['target'])
    elif t == 'drag_edge':
        actions.drag_edge(page, action['locator'], action.get('dx', 0),
                          action.get('dy', 0), action.get('edge', 'right'))
    elif t == 'set_slider':
        actions.set_slider(page, action['locator'], action['value'])
    elif t == 'click_modifier':
        actions.click_modifier(page, action['locator'], action['modifiers'])
    elif t == 'context_menu_select':
        actions.context_menu_select(page, action['locator'], action['item'])
    elif t == 'clear_cookies':
        actions.clear_cookies(page)
    elif t == 'clear_storage':
        actions.clear_storage(page, action['kind'])
    elif t == 'set_cookie':
        actions.set_cookie(page, action['name'], action['value'])
    elif t == 'set_storage':
        actions.set_storage(page, action['kind'], action['key'], action['value'])
    elif t == 'assert_storage':
        actions.assert_storage(page, action['kind'], action['key'], action['value'])
    elif t == 'assert_cookie':
        actions.assert_cookie(page, action['name'], action.get('value'))
    elif t == 'fill_in_row':
        actions.fill_in_row(page, action['locator'], action['row'], action['value'])
    elif t == 'fill_in_section':
        actions.fill_in_section(page, action['locator'], action['section'], action['value'])
    elif t == 'assert_in_row':
        actions.assert_in_row(page, action['text'], action['row'], action['negate'])
    elif t == 'assert_in_section':
        actions.assert_in_section(page, action['text'], action['section'], action['negate'])
    # --- Phase 12 ---
    elif t == 'set_var':
        key = action['var'].upper().replace(" ", "_")
        context._vars[key] = action['value']
        logger.info(f"\n  💾 Set `{key}` = {_safe_repr(key, context._vars[key])}")
    elif t == 'store_attribute':
        key = action['var'].upper().replace(" ", "_")
        context._vars[key] = actions.get_attribute_value(page, action['locator'], action['attribute'])
        logger.info(f"\n  💾 Stored `{key}` = {_safe_repr(key, context._vars[key])}")
    elif t == 'assert_compare':
        actions.assert_compare(action['left'], action['op'], action['right'])
    # --- Phase D ---
    elif t == 'mock_route':
        actions.mock_route(page, action['url'], action['status'], action.get('body'))
    elif t == 'block_route':
        actions.block_route(page, action['url'])
    elif t == 'api_call':
        # NOOD_0011 — carry the REST auth headers so "sets the bearer token"
        # guards api_call too (Graph/OData-style data setup + verification).
        hdrs = json.loads(context._vars.get('_REST_HEADERS', '{}'))
        actions.api_call(page, action['method'], action['url'], action.get('body'), hdrs)
    elif t == 'load_data':
        # Same app_dir/resources/ resolution as load_resource below — the docs
        # (feature-packages.md's resolution table) promise both fixture-style
        # loaders resolve relative to the app's resources/ folder.
        feature_dir = os.path.dirname(os.path.abspath(context.feature.filename))
        app_dir = os.path.dirname(feature_dir)
        full = os.path.join(app_dir, 'resources', action['file'])
        context._vars.update(actions.load_data(full))
        logger.info(f"\n  📦 Loaded test data from {action['file']}")
    elif t == 'load_resource':
        # .feature files live in <app_dir>/features/ — resources/ is the
        # sibling one level up.
        feature_dir = os.path.dirname(os.path.abspath(context.feature.filename))
        app_dir = os.path.dirname(feature_dir)
        paths = [action['path']] if action['path'] else \
            [_row_get(row, 'payload') or row.cells[0] for row in context.table]
        for rel in paths:
            full = os.path.join(app_dir, 'resources', rel)
            with open(full) as fh:
                content = fh.read()
            # Compact JSON so substituted values stay single-line for regex patterns.
            try:
                content = json.dumps(json.loads(content))
            except (json.JSONDecodeError, ValueError):
                content = content.strip()
            stem = os.path.splitext(os.path.basename(rel))[0].upper().replace('-', '_').replace(' ', '_')
            context._vars[f'PAYLOAD_{stem}'] = content
            context._vars['PAYLOAD'] = content  # ponytail: last-loaded wins; named vars cover multi-file use
            logger.info(f"\n  📄 Loaded resource {rel}")
    # --- NOOD_0016: run an external script / command -----------------------
    elif t == 'run_script':
        out = script_runner.run_script(action['path'], action.get('args'))
        _store_script_output(context, out, action.get('var'))
        logger.info(f"\n  🛠  Ran script {action['path']} → {out!r}")
    elif t == 'run_command':
        out = script_runner.run_command(action['command'])
        _store_script_output(context, out, action.get('var'))
        logger.info(f"\n  🛠  Ran command {action['command']!r} → {out!r}")
    elif t == 'call_function':
        result = script_runner.call_function(action['spec'], action.get('args'),
                                             raw=action.get('raw', False))
        # dict/list → JSON so downstream JSON-path asserts work; None → ''.
        out = (json.dumps(result) if isinstance(result, (dict, list))
               else '' if result is None else str(result))
        context._vars['FUNCTION_RESULT'] = out
        if action.get('var'):
            context._vars[action['var'].upper().replace(' ', '_')] = out
        logger.info(f"\n  🛠  Called function {action['spec']} → {out!r}")
    # --- NOOD_0024: web pixel/OCR bridge (canvas & terminal UIs) -----------
    elif t in ('type_text', 'click_at', 'click_text', 'assert_screen_text',
               'assert_screen_text_hidden', 'wait_screen_text', 'assert_buffer',
               'focus_region',
               # NOOD_0114 — element-scoped image scanning
               'focus_element', 'click_image_text', 'assert_image_text',
               'assert_image_text_hidden', 'read_screen_text',
               'read_image_text', 'read_image_number', 'assert_depicts'):
        from noodle.agents.web import screen
        if t == 'type_text':
            screen.type_text(page, action['text'])
        elif t == 'click_at':
            screen.click_at(page, action['x'], action['y'])
        elif t == 'click_text':
            screen.click_text(page, action['text'])
        elif t == 'assert_screen_text':
            screen.assert_text_visible(page, action['text'])
        elif t == 'assert_screen_text_hidden':
            screen.assert_text_hidden(page, action['text'])
        elif t == 'wait_screen_text':
            screen.wait_text_visible(page, action['text'])
        elif t == 'assert_buffer':
            screen.assert_buffer_contains(page, action['text'])
        elif t == 'focus_region':
            from noodle.agents.visual import regions
            vp = page.viewport_size or {"width": 1280, "height": 720}
            screen.set_region(regions.parse_region(action['region'], (vp["width"], vp["height"])))
        # NOOD_0114 — element-scoped image scanning (carousels, flyers, logos…)
        elif t == 'focus_element':
            screen.focus_element(page, action['locator'])
        elif t == 'click_image_text':
            screen.click_text_in(page, action['locator'], action['text'])
        elif t == 'assert_image_text':
            screen.assert_image_text(page, action['locator'], action['text'])
        elif t == 'assert_image_text_hidden':
            screen.assert_image_text_hidden(page, action['locator'], action['text'])
        # spelled as == chains so the VALID_TYPES drift guard's scrape sees them
        elif t == 'read_screen_text' or t == 'read_image_text' or t == 'read_image_number':
            key = action['var'].upper().replace(" ", "_")
            loc = action.get('locator')
            context._vars[key] = (screen.read_number(page, loc)
                                  if t == 'read_image_number'
                                  else screen.read_text(page, loc))
            logger.info(f"\n  💾 Stored `{key}` = {_safe_repr(key, context._vars[key])}")
        elif t == 'assert_depicts':
            screen.assert_depicts(page, action['desc'], action.get('locator'))
    elif t == 'set_viewport':
        page.set_viewport_size({'width': action['width'], 'height': action['height']})
    # NOOD_0152 — orientation swap reads the LIVE viewport and transposes it,
    # so it composes with whatever size a prior step set (named breakpoint,
    # explicit WxH, or the @viewport tag) instead of hardcoding a device.
    elif t == 'rotate_viewport':
        vp = page.viewport_size
        if not vp:
            raise AssertionError(
                "Can't rotate: the page has no viewport size (a browser launched "
                "with no_viewport=True fills the window and cannot be rotated)."
            )
        w, h = vp['width'], vp['height']
        want = action.get('orientation')
        # "to landscape"/"to portrait" is idempotent — already there means no-op.
        # A bare "rotates the device" always swaps.
        if want == 'landscape':
            new = (max(w, h), min(w, h))
        elif want == 'portrait':
            new = (min(w, h), max(w, h))
        else:
            new = (h, w)
        page.set_viewport_size({'width': new[0], 'height': new[1]})
    elif t == 'assert_viewport':
        actions.assert_viewport(page, action['width'], action.get('height'))
    # --- NOOD_0029: proper REST HTTP client ------------------------------------
    elif t == 'rest_set_header':
        hdrs = json.loads(context._vars.get('_REST_HEADERS', '{}'))
        hdrs[action['name']] = action['value']
        context._vars['_REST_HEADERS'] = json.dumps(hdrs)
    elif t == 'rest_set_auth':
        hdrs = json.loads(context._vars.get('_REST_HEADERS', '{}'))
        if action['scheme'] == 'bearer':
            hdrs['Authorization'] = f"Bearer {action['token']}"
        else:                               # basic
            import base64
            if 'user' not in action or 'password' not in action:
                raise AssertionError(
                    f"rest_set_auth (basic) needs 'user' and 'password' — got: {action}"
                )
            cred = base64.b64encode(
                f"{action['user']}:{action['password']}".encode()).decode()
            hdrs['Authorization'] = f"Basic {cred}"
        context._vars['_REST_HEADERS'] = json.dumps(hdrs)
    elif t == 'rest_oauth2':
        _oauth2_fetch(context, action['url'], action['client_id'], action['client_secret'])
    elif t == 'rest_call':
        from noodle.agents.web import rest_client
        base = context._vars.get('REST_BASE_URL', '')
        hdrs = json.loads(context._vars.get('_REST_HEADERS', '{}'))
        path = action['path']
        url = path if path.startswith('http') else base.rstrip('/') + '/' + path.lstrip('/')
        status, body, headers = rest_client.rest_call(action['method'], url, action.get('body'), hdrs)
        if status == 401 and '_REST_OAUTH' in context._vars:
            # Token likely expired — refresh once and retry once, never loop.
            o = json.loads(context._vars['_REST_OAUTH'])
            _oauth2_fetch(context, o['url'], o['client_id'], o['client_secret'])
            hdrs = json.loads(context._vars.get('_REST_HEADERS', '{}'))
            status, body, headers = rest_client.rest_call(action['method'], url, action.get('body'), hdrs)
        context._vars['REST_STATUS'] = str(status)
        context._vars['REST_BODY'] = body
        context._vars['REST_HEADERS'] = json.dumps(dict(headers))
        if action.get('var'):
            context._vars[action['var'].upper().replace(' ', '_')] = body
        logger.info(f"\n  🌐 {action['method']} {url} → {status}")
    elif t == 'rest_assert_status':
        actual = int(context._vars.get('REST_STATUS', 0))
        assert actual == action['expected'], f"Expected status {action['expected']}, got {actual}"
    elif t == 'rest_extract_json':
        body = context._vars.get('REST_BODY', '{}')
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise AssertionError(f"REST_BODY is not valid JSON: {body[:100]}") from exc
        key = action['key']
        if '.' in key or '[' in key:        # NOOD_0007 — dotted/indexed path
            value = _json_path(data, key)
        else:                               # legacy flat key (first item of a list)
            item = data[0] if isinstance(data, list) else data
            if key not in item:
                raise AssertionError(f"Key '{key}' not found in response JSON")
            value = item[key]
        target = action['var'].upper().replace(' ', '_')
        context._vars[target] = str(value)
        logger.info(f"\n  💾 Extracted '{key}' → `{target}` = "
                    f"{_safe_repr(key + ' ' + target, context._vars[target])}")
    elif t == 'rest_assert_body':
        body = context._vars.get('REST_BODY', '')
        assert action['needle'] in body, f"Response body does not contain '{action['needle']}'"
    elif t == 'rest_assert_body_table':
        body = context._vars.get('REST_BODY', '')
        for row in context.table:
            key = _row_get(row, 'Key') or row.cells[0]
            value = (_row_get(row, 'Value')
                     or (row.cells[1] if len(row.cells) > 1 else '')).strip()
            assert key in body, f"Response body does not contain key '{key}'"
            if value:
                assert value in body, f"Response body does not contain value '{value}' for key '{key}'"
    elif t == 'rest_assert_header':
        headers = json.loads(context._vars.get('REST_HEADERS', '{}'))
        actual = next((v for k, v in headers.items() if k.lower() == action['name'].lower()), None)
        assert actual is not None, f"Response header '{action['name']}' not found"
        assert action['value'].lower() in actual.lower(), \
            f"Header '{action['name']}': expected '{action['value']}', got '{actual}'"
    elif t == 'rest_assert_header_table':
        headers = json.loads(context._vars.get('REST_HEADERS', '{}'))
        for row in context.table:
            name = _row_get(row, 'Header') or row.cells[0]
            expected = (_row_get(row, 'Value')
                        or (row.cells[1] if len(row.cells) > 1 else '')).strip()
            actual = next((v for k, v in headers.items() if k.lower() == name.lower()), None)
            assert actual is not None, f"Response header '{name}' not found"
            if expected:
                assert expected.lower() in actual.lower(), \
                    f"Header '{name}': expected '{expected}', got '{actual}'"
    # --- Phases M–S (2026-07): console/network health, emulation, offline,
    # a11y, clipboard, websockets, print/PDF ---------------------------------
    elif t == 'assert_no_console_errors':
        actions.assert_no_console_errors(page, ctx_get(context, '_console_errors', []))
    elif t == 'assert_no_page_errors':
        actions.assert_no_page_errors(page, ctx_get(context, '_page_errors', []))
    elif t == 'assert_no_failed_requests':
        actions.assert_no_failed_requests(page, ctx_get(context, '_failed_requests', []))
    elif t == 'assert_request_made':
        actions.assert_request_made(page, ctx_get(context, '_requests', []), action['url'])
    elif t == 'assert_request_count':
        actions.assert_request_count(page, ctx_get(context, '_requests', []),
                                     action['count'], action.get('op', '<'))
    elif t == 'soft_assert_check':
        soft = ctx_get(context, '_soft_failures') or []
        if soft:
            n = len(soft)
            msgs = "\n".join(f"    - {s}" for s in soft)
            soft.clear()                     # reported here — don't double-fail in hooks
            raise SoftAssertionReport(f"{n} soft assertion(s) failed:\n{msgs}")
    elif t == 'set_geolocation':
        actions.set_geolocation(page, action['coords'])
    elif t == 'grant_permissions':
        actions.grant_permissions(page, action['permissions'])
    elif t == 'dismiss_permission_prompt':
        actions.dismiss_permission_prompt(page, action.get('permission', 'location'))
    elif t == 'set_offline':
        actions.set_offline(page, action['offline'])
    elif t == 'throttle_network':
        actions.throttle_network(page, action['profile'])
    elif t == 'assert_a11y':
        actions.assert_a11y(page, action.get('impact'), action.get('max', 0))
    elif t == 'write_clipboard':
        actions.write_clipboard(page, action['text'])
    elif t == 'assert_clipboard':
        actions.assert_clipboard(page, action['text'])
    elif t == 'assert_ws_message':
        actions.assert_ws_message(page, ctx_get(context, '_ws_frames', []),
                                  action['contains'], action.get('direction'))
    elif t == 'emulate_media':
        actions.emulate_media(page, action['media'])
    elif t == 'save_pdf':
        actions.save_pdf(page, action['path'])
    # --- Phase J: multi-user / multi-context flows ---------------------------
    elif t == 'new_context':
        _new_named_context(context, action['name'])
    elif t == 'use_context':
        _use_named_context(context, action['name'])
    # --- Phase G4: app lifecycle ---------------------------------------------
    elif t == 'app_launch':
        app_lifecycle.launch(action['command'])
    elif t == 'app_assert_running':
        app_lifecycle.assert_running(action.get('port'))
    elif t == 'app_stop':
        app_lifecycle.stop()
    # --- Phase F gestures reaching the web agent (no @appium tag) ------------
    elif t in ('swipe', 'long_press', 'hide_keyboard', 'background_app'):
        raise AssertionError(
            f"'{step_text}' is a mobile/native gesture — tag the scenario "
            f"@appium, @android, @ios, @windows or @mac"
        )
    elif t == 'device_key':
        # On web, "presses the back button" is a normal click on that control.
        actions.click(page, action['key'])
    else:
        raise AssertionError(f"Unknown action type: '{t}'")
