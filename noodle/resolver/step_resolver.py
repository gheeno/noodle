import difflib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from .desktop_patterns import match as desktop_match
from .patterns import match as pattern_match
from .patterns import normalize_phrasing, normalize_subject
from .perf_patterns import match as perf_match

# Bundled baseline (ships inside the installed package — see pyproject.toml's
# force-include — so a real `pip install noodle`, not just an editable dev
# checkout, still has the curated reference corpus).
_BUNDLED_STEP_DICTIONARY = Path(__file__).resolve().parents[1] / "_docs" / "steps_dictionary.md"
# Dev-checkout fallback: this repo's own docs/, used when there's no bundled
# copy (editable install) and no workspace override set below.
_REPO_STEP_DICTIONARY = Path(__file__).resolve().parents[2] / "docs" / "steps_dictionary.md"
_STEP_KEYWORD_RE = re.compile(r'^(?:Given|When|Then|And|But)\s+(.*)$', re.IGNORECASE)

# NOOD_0027 — the workspace's own docs/ (project-local staged/custom
# entries), set once per process by hooks.before_all (run time) or the
# step-search CLI/agent (--workspace). None = no workspace known yet (e.g.
# unit tests, or `noodle` run from inside this repo as its own workspace).
_workspace_docs_dir: Path | None = None


def set_docs_dir(path: Path | None) -> None:
    """Point step-search/suggestion at a workspace's own docs/ dir (project-
    local staged vocabulary), on top of the bundled curated baseline. Also
    invalidates the in-process caches so switching workspace mid-process
    (a long-lived noodle repl REPL, or a test run) never serves a stale
    result from the previous workspace."""
    global _workspace_docs_dir
    _workspace_docs_dir = Path(path) if path is not None else None
    clear_index_cache()


def _dictionary_paths() -> list[Path]:
    paths = []
    if _BUNDLED_STEP_DICTIONARY.exists():
        paths.append(_BUNDLED_STEP_DICTIONARY)
    elif _REPO_STEP_DICTIONARY.exists():
        paths.append(_REPO_STEP_DICTIONARY)
    if _workspace_docs_dir is not None:
        workspace_dict = _workspace_docs_dir / "steps_dictionary.md"
        # .resolve() before the dedup check — a relative workspace override
        # (e.g. --workspace .) and one of the paths above can point at the
        # exact same file on disk while comparing unequal as bare Path objects.
        if workspace_dict.exists() and \
                workspace_dict.resolve() not in (p.resolve() for p in paths):
            paths.append(workspace_dict)
    return paths


_example_corpus_cache: list[str] | None = None


def _example_corpus() -> list[str]:
    """Every example step line from the dictionary's ```gherkin blocks — the
    living source of 'what a correct step looks like', reused so a typo's
    error message can suggest the real thing instead of just failing.
    Merges the bundled/repo curated baseline with a workspace's own staged
    entries, if a workspace has been set via set_docs_dir()."""
    global _example_corpus_cache
    if _example_corpus_cache is not None:
        return _example_corpus_cache
    corpus = []
    for dictionary_path in _dictionary_paths():
        in_block = False
        block_is_visual = False
        for line in dictionary_path.read_text().splitlines():
            stripped = line.strip()
            if stripped == "```gherkin":
                in_block = True
                block_is_visual = False
                continue
            if stripped == "```":
                in_block = False
                continue
            if not in_block:
                continue
            # NOOD_0067 — @visual examples resolve against visual_patterns.py,
            # not the web table this corpus feeds (alias regressions,
            # example_index, "did you mean" suggestions). Skip their blocks or
            # every visual step reads as an unresolvable web step.
            if stripped.startswith("@") and "@visual" in stripped:
                block_is_visual = True
                continue
            if block_is_visual:
                continue
            m = _STEP_KEYWORD_RE.match(stripped)
            if m and not m.group(1).endswith(":"):  # skip table-driven headers
                corpus.append(m.group(1))
    _example_corpus_cache = corpus
    return corpus


_example_index_cache: list[dict] | None = None


def example_index() -> list[dict]:
    """Phase U — every dictionary example with its section heading and the
    action type it resolves to: [{'section', 'step', 'type'}]. Powers the
    `noodle steps` CLI search and the LSP hover. Empty when docs/ isn't
    present (installed wheel) — callers degrade gracefully."""
    global _example_index_cache
    if _example_index_cache is not None:
        return _example_index_cache
    index = []
    for dictionary_path in _dictionary_paths():
        section, in_block = "", False
        for line in dictionary_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                section = stripped.lstrip("#").strip()
                continue
            if stripped == "```gherkin":
                in_block = True
                continue
            if stripped == "```":
                in_block = False
                continue
            if not in_block:
                continue
            m = _STEP_KEYWORD_RE.match(stripped)
            if not m:
                continue
            step = m.group(1)
            # NOOD_0155 — same best-guess fallthrough as a bare resolve():
            # wok tables (perf, desktop) grade their own dictionary examples.
            result = _table_match(normalize_phrasing(normalize_subject(step)))
            index.append({
                "section": section,
                "step": stripped,
                "type": result[0] if result else None,
            })
    _example_index_cache = index
    return index


def clear_index_cache() -> None:
    """NOOD_0026 — so a step just accepted via
    step_suggestion_engine.accept_suggestion() shows up in example_index()/
    _example_corpus() (and therefore in `noodle steps`, the LSP hover, and
    the next step-search) immediately, without a process restart."""
    global _example_corpus_cache, _example_index_cache
    _example_corpus_cache = None
    _example_index_cache = None


def _suggest(step_text: str) -> str:
    corpus = _example_corpus()
    if not corpus:
        return ""
    lowered = {c.lower(): c for c in corpus}
    hits = difflib.get_close_matches(step_text.lower(), lowered.keys(), n=3, cutoff=0.5)
    if not hits:
        return ""
    lines = "\n".join(f"    {lowered[h]}" for h in hits)
    return f"  Did you mean:\n{lines}\n"

# Every action type the runner can dispatch (orchestrator/runner.py). The LLM
# fallback is validated against this — a syntactically valid JSON with a bogus
# `type` would otherwise dispatch the wrong action (or crash deep in the runner
# with no step context). ponytail: hand-kept mirror of runner.py's if/elif;
# the runner is a flat dispatch with no list to import. If a new action type is
# added there, add it here (test_llm_resolve_rejects guards the common slips).
VALID_TYPES = frozenset({
    'api_call', 'assert_attribute', 'assert_cell', 'assert_compare',
    'assert_count', 'assert_hidden', 'assert_number', 'assert_row_count',
    'assert_semantic',
    'assert_state', 'assert_title', 'assert_url', 'assert_value',
    'assert_value_not',
    'assert_visible', 'block_route', 'check', 'clear', 'click',
    'click_in_row', 'click_in_section', 'close_popups', 'fill', 'hover',
    'load_data', 'load_resource', 'mock_route', 'navigate', 'pixel_baseline', 'press_key',
    'run_command', 'run_script', 'screenshot', 'scroll', 'scroll_to',
    'search', 'select', 'select_suggestion', 'assert_suggestion',
    'set_page', 'set_var', 'set_viewport', 'store_attribute',
    'store_text', 'switch_frame', 'uncheck', 'visual_baseline', 'wait_hidden',
    'wait_load', 'wait_networkidle', 'wait_seconds', 'wait_visible',
    # NOOD_0024 — web pixel/OCR bridge (canvas & terminal UIs)
    'type_text', 'click_at', 'click_text', 'assert_screen_text',
    'assert_screen_text_hidden', 'wait_screen_text', 'assert_buffer',
    'focus_region',
    # NOOD_0114 — element-scoped image scanning (carousels, flyers, banners,
    # logos, avatars): OCR asserts/clicks/extraction + vision-LLM depicts
    'focus_element', 'click_image_text', 'assert_image_text',
    'assert_image_text_hidden', 'read_screen_text', 'read_image_text',
    'read_image_number', 'assert_depicts',
    # NOOD_0025 — browser history, extra clicks, form submit, tab/window
    'go_back', 'go_forward', 'reload', 'double_click', 'right_click', 'submit',
    'assert_new_tab', 'switch_tab', 'close_tab',
    # NOOD_0029 — proper REST HTTP client
    'rest_set_header', 'rest_call', 'rest_assert_status', 'rest_assert_body',
    'rest_assert_body_table', 'rest_assert_header', 'rest_assert_header_table',
    'rest_extract_json',
    # NOOD_0007 — REST auth sugar + OAuth2 client-credentials
    'rest_set_auth', 'rest_oauth2',
    # NOOD_0008 — JS dialogs, upload/download, multi-value select
    'arm_dialog', 'assert_dialog_text', 'upload', 'assert_download',
    'select_multi',
    # NOOD_0009 — drag & drop, cookies/storage, iframe exit, scoped fills/asserts
    'drag', 'clear_cookies', 'clear_storage', 'set_cookie', 'switch_main_frame',
    'fill_in_row', 'fill_in_section', 'assert_in_row', 'assert_in_section',
    # NOOD_0009 — in-process custom Python functions (return value + D.I.)
    'call_function',
    # NOOD_0011 — grids & tables (cells/headers/rows/columns/scrollbars),
    # table-driven form fill, browser session persistence
    'click_cell', 'scroll_table', 'assert_row_values', 'assert_table_headers',
    'assert_column_contains', 'assert_table_rows', 'fill_form_table',
    'save_session',
    # Phases M–S (2026-07) — console/network health, emulation, offline &
    # throttling, a11y, clipboard, websockets, print/PDF
    'assert_no_console_errors', 'assert_no_page_errors', 'assert_no_failed_requests',
    'set_geolocation', 'grant_permissions', 'dismiss_permission_prompt',
    'set_offline', 'throttle_network',
    'assert_a11y', 'write_clipboard', 'assert_clipboard', 'assert_ws_message',
    'emulate_media', 'save_pdf',
    # Phase J — multi-user contexts; Phase L — request/soft assertions
    'new_context', 'use_context', 'assert_request_made', 'soft_assert_check',
    # Phase G4 — app lifecycle; Phase F — mobile gestures
    'app_launch', 'app_assert_running', 'app_stop', 'swipe', 'device_key',
    # NOOD_0032 — native-app gestures (Appium platform tags)
    'long_press', 'hide_keyboard', 'background_app',
    # NOOD_0044 — conditional wrapper ("clicks X if Y appears")
    'run_if',
    # NOOD_0143 — audit gap closure: URL wait, page-edge scroll, storage/
    # cookie value steps, focus/CSS asserts, column sort-order
    'wait_url', 'scroll_edge', 'set_storage', 'assert_storage',
    'assert_cookie', 'assert_focused', 'assert_css', 'assert_column_sorted',
    # NOOD_0155 — woks: performance wok (built-in load generator) and the
    # desktop wok's browserless spreadsheet helpers (cross-wok composition)
    'perf_load', 'perf_assert_time', 'perf_assert_error_rate',
    'perf_assert_throughput', 'perf_report', 'perf_store',
    'desktop_read_cell', 'desktop_assert_cell',
    # NOOD_0152 — step-vocabulary audit: waits that replace the hard sleep,
    # container/until scrolling, mouse primitives, orientation swap, and the
    # scoped assert/fill/clipboard/frame-chain additions. Dispatched by the
    # runner since 0152 but never mirrored here (NOOD_0157), so the LLM
    # fallback rejected every one of them as an unknown type.
    'wait_state', 'wait_count', 'wait_text_change', 'wait_response',
    'scroll_container', 'scroll_until_visible',
    'assert_matches', 'assert_number_tolerance', 'assert_number_between',
    'fill_date', 'switch_frame_chain', 'store_clipboard',
    'assert_download_content',
    'mouse_drag', 'mouse_drag_to', 'drag_edge', 'set_slider',
    'click_modifier', 'context_menu_select',
    'rotate_viewport', 'landscape', 'portrait', 'assert_viewport',
    'assert_request_count',
})


# NOOD_0155 — the three pattern tables that share execute_step's dispatch,
# keyed by wok name. wok.pattern_priority(tags) orders them per scenario: the
# scenario's own wok gets first claim on its grammar; with no routing tags
# the best guess is web-first (the pre-wok behavior, and what a bare
# `resolve(text)` call still does). @visual is a separate table/runner and
# never consults these.
_TABLES = {
    'web': pattern_match,
    'performance': perf_match,
    'desktop': desktop_match,
}


def _table_match(normalized: str, tags=None):
    """(action_type, params) from the tag-prioritized tables, or None."""
    from noodle.wok import pattern_priority
    for name in pattern_priority(tags):
        result = _TABLES[name](normalized)
        if result:
            return result
    return None


def resolve(step_text: str, tags=None) -> dict:
    """
    auto mode (default): pattern match first, LLM fallback on no match.
    full mode (NOODLE_LLM_MODE=full): skip patterns, every step goes to the LLM.

    Either way the LLM is only reachable with NOODLE_MODEL set.
    Raises AssertionError if the step can't be resolved.

    NOOD_0155 — `tags` (the scenario's effective tags) picks which wok's
    pattern table gets first claim on the sentence; None = web-first best
    guess. See wok.pattern_priority.
    """
    full = os.getenv('NOODLE_LLM_MODE', 'auto').lower() == 'full'
    normalized = normalize_phrasing(normalize_subject(step_text))

    if not full:
        result = _table_match(normalized, tags)
        if result:
            action_type, params = result
            return {'type': action_type, **params}

    if os.getenv('NOODLE_MODEL'):
        return _llm_resolve(step_text)

    if full:
        raise AssertionError(
            f"\nNOODLE_LLM_MODE=full but NOODLE_MODEL is not set: \"{step_text}\"\n"
            "  → Set NOODLE_MODEL in .env (e.g. anthropic/claude-sonnet-4-6, "
            "gemini/gemini-1.5-flash, ollama/llama3)"
        )

    raise AssertionError(
        f"\nNo pattern matched: \"{step_text}\"\n"
        f"  Normalized to:      \"{normalized}\"\n"
        f"{_suggest(step_text)}"
        "  → Add a pattern to noodle/resolver/patterns.py\n"
        "  → OR set NOODLE_MODEL in .env to enable LLM fallback"
    )


# Per-run memo of LLM-resolved steps (NOOD_0007). The same sentence repeated
# across scenarios ("User submits the login form" in every login test) costs
# one model call per run instead of one per occurrence. Pattern matches are
# never cached — they're already free. Cleared in hooks.before_all.
_llm_cache: dict[str, dict] = {}


def clear_cache():
    _llm_cache.clear()


def _llm_resolve(step_text: str) -> dict:
    cached = _llm_cache.get(step_text)
    if cached is not None:
        return dict(cached)                 # copy — callers may mutate the action
    action = _llm_resolve_uncached(step_text)
    _llm_cache[step_text] = dict(action)
    return action


def _llm_resolve_uncached(step_text: str) -> dict:
    try:
        from noodle.llm.client import ask
    except ImportError:
        raise AssertionError("LLM fallback requires: pip install noodle[llm]")

    prompt = f"""You are a test automation interpreter. Convert this test step to a JSON action.

Step: "{step_text}"

WEB action types: navigate, search, close_popups, click, fill, hover, press_key, clear, select, check, uncheck, assert_visible, assert_hidden, assert_url, assert_title, assert_value, assert_state, assert_attribute, assert_count, store_text, set_var, store_attribute, assert_compare, scroll, screenshot, wait_load, wait_visible, wait_hidden, wait_seconds, set_viewport, run_if

WEB param keys by type: navigate -> url; search -> query; click/hover/clear -> locator; fill -> locator,value; press_key -> key; select -> locator,value; check/uncheck -> locator; assert_visible/assert_hidden/wait_visible/wait_hidden -> text; assert_url/assert_title -> fragment; assert_value -> locator,value; assert_state -> locator,state; assert_attribute -> locator,attribute,value; assert_count -> count,locator; store_text -> locator,var; set_var -> var,value; store_attribute -> attribute,locator,var; assert_compare -> left,op,right; scroll -> direction; screenshot -> name; set_viewport -> width,height (integers); wait_seconds -> seconds (number, a hard sleep); run_if -> condition (element/text to probe), negate (boolean, true = run when absent), then (the full inner step text to execute when the condition holds)

REST (HTTP API) action types and params:
  rest_set_header -> name,value
  rest_set_auth -> scheme ("bearer" with token, or "basic" with user,password)
  rest_oauth2 -> url,client_id,client_secret (OAuth2 client-credentials token fetch)
  rest_call -> method (GET/POST/PUT/PATCH/DELETE), path; optional body (JSON string), var (store response)
  rest_assert_status -> expected (integer)
  rest_assert_body -> needle (substring expected in body)
  rest_assert_header -> name,value
  rest_extract_json -> key,var (store a JSON field into a variable; key may be a dotted path like data.items[0].id)

Rules:
- Use "path" (not "url") for rest_call. "expected" for rest_assert_status must be an integer.
- For REST steps with a Gherkin data table (step ends with ":"), do NOT use the LLM — those are pattern-only. Never emit a type ending in "_table".

Reply with JSON only.
Web example:  {{"type": "click", "locator": "Login"}}
REST example: {{"type": "rest_call", "method": "POST", "path": "/users", "body": "{{\\"name\\":\\"Alice\\"}}", "var": null}}
Verb phrasings map onto the same action types — never invent a new type:
  "User authenticates using the login button" -> {{"type": "click", "locator": "login button"}}
  "User verifies the dashboard is displayed"  -> {{"type": "assert_visible", "text": "dashboard"}}
"""
    # One retry: models occasionally prefix the JSON with a stray sentence.
    raw = ask(prompt)
    action = _parse_action(raw)
    if action is None:
        raw = ask(prompt)
        action = _parse_action(raw)
    if action is None:
        raise AssertionError(
            f"LLM returned unparseable response for: \"{step_text}\"\nResponse: {raw}"
        )

    t = action.get('type')
    if t not in VALID_TYPES:
        raise AssertionError(
            f"LLM returned an unknown action type {t!r} for: \"{step_text}\"\n"
            f"Response: {raw}\n"
            f"  → Valid types: {', '.join(sorted(VALID_TYPES))}"
        )
    _log_suggestion(step_text, action)
    return action


_SUGGESTIONS_FILENAME = "steps_dictionary_suggestions.md"


def _suggestions_path() -> Path:
    docs_dir = _workspace_docs_dir if _workspace_docs_dir is not None else _REPO_STEP_DICTIONARY.parent
    return docs_dir / _SUGGESTIONS_FILENAME


def _log_suggestion(step_text: str, action: dict) -> None:
    """Append an LLM-resolved step to the suggestions log (NOOD_0049), so a
    human can promote a recurring one into patterns.py instead of it hitting
    the LLM again on every future run. Skips steps already logged, so re-runs
    of the same scenario don't pile up duplicate entries.

    NOOD_0152 — a repeat bumps **Hits** instead of being dropped on the floor.
    The promotion rule is "promote a *recurring* one", but plain dedup erased
    the very signal that rule needs: a step the suite hits 400 times looked
    identical to a one-off typo. Hits is the ranking column when this file is
    fed back for pattern generation."""
    path = _suggestions_path()
    marker = f"`{step_text}`"
    if path.exists():
        text = path.read_text()
        hit = re.search(
            rf"- \*\*Step:\*\* {re.escape(marker)}\n"
            rf"- \*\*Resolved:\*\* .*\n- \*\*Hits:\*\* (\d+)", text)
        if hit:
            path.write_text(
                f"{text[:hit.start(1)]}{int(hit.group(1)) + 1}{text[hit.end(1):]}")
            return
        if marker in text:
            return          # legacy entry, written before Hits existed — leave it
    entry = (
        f"## {datetime.now().isoformat(timespec='seconds')} — {os.getenv('NOODLE_MODEL', 'unknown')}\n"
        f"- **Step:** {marker}\n"
        f"- **Resolved:** `{json.dumps(action, sort_keys=True)}`\n"
        f"- **Hits:** 1\n\n"
    )
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Step Dictionary Suggestions\n\n"
            "LLM-resolved steps, logged for review (NOOD_0049). Promote a recurring "
            "one into `patterns.py` / `steps_dictionary.md`, then delete its entry here.\n\n"
            "**Hits** counts how often the step was resolved by the LLM — rank by it, "
            "highest first. A high-hit entry is costing a model call on every run and "
            "is the best promotion candidate. Always eyeball **Resolved** before "
            "promoting: it is the model's *guess*, not a verified mapping.\n\n"
        )
    with path.open('a') as f:
        f.write(entry)


def _parse_action(raw: str) -> dict | None:
    """Parse the model's reply into an action dict, or None if it isn't JSON.
    Tolerates a ```json fence and leading/trailing prose around the object."""
    import json
    import re
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    # Strip a markdown code fence if the model wrapped the JSON in one.
    text = re.sub(r'^```[a-zA-Z]*\n?|\n?```$', '', text).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the first {...} object embedded in surrounding prose.
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None
