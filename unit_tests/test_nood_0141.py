"""NOOD_0141 — typeahead/async-widget engine smarts (retail-SPA field post-mortem).

P0-1  A named POM key that exists but hasn't resolved is POLLED, then fails
      loudly — never substituted with a fuzzy heal match (the silent
      false-pass that clicked a no-op typeahead icon and reported green).
P0-2  probe()/inspect() run inside an asyncio host (FastMCP) without the
      "Sync API inside asyncio loop" crash.
P1-1  probe --suggest captures the typeahead: exact strings, navigating row
      selectors, no-op icon flags, copy-ready steps.
P1-2  `selects the "..." suggestion [for "..."]` composite + intent-level
      suggestion assertions, registered in the pattern table.
P1-3  DOM-scan ranking tiebreaks toward activation affordance; a click with
      zero observable effect warns instead of staying silent.
P2-1  The probe's POM suggestion prefers the visible twin of a hidden input.

No browser, no LLM, no network.
"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noodle.agents.web import actions, dom_scan, locator
from noodle.agents.web import pom as pom_mod
from noodle.agents.web import probe as probe_mod
from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject


def _match(step_text: str):
    return match(normalize_phrasing(normalize_subject(step_text)))


# --- P1-2: pattern registration ------------------------------------------------

def test_select_suggestion_with_term():
    assert _match('User selects the "vaccum cleaner" suggestion for "Vaccu"') == \
        ("select_suggestion", {"option": "vaccum cleaner", "term": "Vaccu"})


def test_select_suggestion_bare_and_click_alias():
    assert _match('User selects the "vaccum cleaner" suggestion') == \
        ("select_suggestion", {"option": "vaccum cleaner", "term": None})
    # the phrasing weaker models actually emit
    assert _match('User clicks the "vaccum cleaner" suggestion') == \
        ("select_suggestion", {"option": "vaccum cleaner", "term": None})
    assert _match('User picks the "vaccum cleaner" suggestion for "Vaccu"')[0] == \
        "select_suggestion"


def test_suggestion_assertions():
    assert _match('the search suggestions for "Vaccu" include "vaccum"') == \
        ("assert_suggestion", {"term": "Vaccu", "text": "vaccum"})
    assert _match('the search suggestions should contain "cleaner"') == \
        ("assert_suggestion", {"term": None, "text": "cleaner"})
    # the prompt-verbatim shape — must not rot into an assert_visible text hunt
    assert _match('a suggestion bar appears below the search bar') == \
        ("assert_suggestion", {"term": None, "text": None})


def test_existing_search_and_select_not_hijacked():
    assert _match('User searches for "Vaccu"') == ("search", {"query": "Vaccu"})
    assert _match('User selects "Blue" from the color dropdown')[0] == "select"
    # quoted phrase INCLUDING the word suggestion stays a plain click
    assert _match('User clicks the "vaccum cleaner suggestion"')[0] == "click"


# --- P0-1: named POM key never heals into a fuzzy guess ------------------------

def _fast_budget(monkeypatch):
    monkeypatch.setenv("NOODLE_FIND_TIMEOUT", "300")
    monkeypatch.delenv("NOODLE_HEAL_POM_KEYS", raising=False)
    monkeypatch.delenv("NOODLE_MODEL", raising=False)


def test_defined_pom_key_unresolved_fails_loudly(monkeypatch):
    """Entry exists, selector never matches → AssertionError naming the key,
    and the fuzzy chain (dom_scan) is never consulted."""
    _fast_budget(monkeypatch)
    raw = MagicMock()
    raw.count.return_value = 0
    monkeypatch.setattr(pom_mod, "locate", lambda page, text: None)
    monkeypatch.setattr(pom_mod, "raw_locator", lambda page, text: raw)
    monkeypatch.setattr(pom_mod, "entry_summary",
                        lambda text, url="": "css: span.never >> text=x")
    scanned = []
    monkeypatch.setattr(locator.dom_scan, "best_selector",
                        lambda scope, text: scanned.append(text))
    page = MagicMock()
    page.url = "https://example.com"
    with pytest.raises(AssertionError) as e:
        locator.find(page, "vaccum cleaner suggestion")
    msg = str(e.value)
    assert "vaccum cleaner suggestion" in msg
    assert "Not substituting" in msg
    assert "span.never" in msg
    assert scanned == []          # fuzzy chain never entered


def test_defined_pom_key_resolves_after_poll(monkeypatch):
    """The async-widget case: the selector matches a moment later — polled,
    resolved, no failure, no heal."""
    _fast_budget(monkeypatch)
    raw = MagicMock()
    raw.count.side_effect = [0, 0, 2]
    monkeypatch.setattr(pom_mod, "locate", lambda page, text: None)
    monkeypatch.setattr(pom_mod, "raw_locator", lambda page, text: raw)
    page = MagicMock()
    page.url = "https://example.com"
    assert locator.find(page, "suggestion row") is raw.first


def test_missing_pom_key_still_heals(monkeypatch):
    """No entry at all → the pre-0141 behaviour is untouched: accessibility
    then the self-heal chain (dom_scan consulted)."""
    _fast_budget(monkeypatch)
    monkeypatch.setattr(pom_mod, "locate", lambda page, text: None)
    monkeypatch.setattr(pom_mod, "raw_locator", lambda page, text: None)
    monkeypatch.setattr(locator, "_poll_strategies", lambda *a, **k: (None, False))
    monkeypatch.setattr(locator, "_try_strategies", lambda *a, **k: (None, False))
    scanned = []

    def fake_scan(scope, text):
        scanned.append(text)
        return None

    monkeypatch.setattr(locator.dom_scan, "best_selector", fake_scan)
    page = MagicMock()
    page.url = "https://example.com"
    assert locator.find(page, "vaccum cleaner suggestion") is None
    assert scanned                 # heal chain ran


def test_heal_pom_keys_env_restores_legacy(monkeypatch):
    """NOODLE_HEAL_POM_KEYS=true → defined-but-unresolved key falls through to
    the heal chain instead of raising (one-release escape hatch)."""
    _fast_budget(monkeypatch)
    monkeypatch.setenv("NOODLE_HEAL_POM_KEYS", "true")
    raw = MagicMock()
    raw.count.return_value = 0
    monkeypatch.setattr(pom_mod, "locate", lambda page, text: None)
    monkeypatch.setattr(pom_mod, "raw_locator", lambda page, text: raw)
    monkeypatch.setattr(locator, "_poll_strategies", lambda *a, **k: (None, False))
    monkeypatch.setattr(locator, "_try_strategies", lambda *a, **k: (None, False))
    monkeypatch.setattr(locator.dom_scan, "best_selector", lambda *a: None)
    page = MagicMock()
    page.url = "https://example.com"
    assert locator.find(page, "suggestion row") is None   # no raise


def test_cheap_probe_skips_strict_branch(monkeypatch):
    """Absence probes (poll=False/heal=False — run_if, find_first early
    candidates) keep the fast None: no poll, no raise."""
    _fast_budget(monkeypatch)
    raw = MagicMock()
    raw.count.return_value = 0
    monkeypatch.setattr(pom_mod, "locate", lambda page, text: None)
    monkeypatch.setattr(pom_mod, "raw_locator", lambda page, text: raw)
    monkeypatch.setattr(locator, "_try_strategies", lambda *a, **k: (None, False))
    page = MagicMock()
    started = time.monotonic()
    assert locator.find(page, "suggestion row", poll=False, heal=False) is None
    assert time.monotonic() - started < 0.2


def test_pom_raw_locator_and_entry_summary(tmp_path):
    """raw_locator tells 'entry defined' apart from 'nothing matched';
    entry_summary names the selector for the failure message."""
    features = tmp_path / "app" / "features"
    features.mkdir(parents=True)
    resources = tmp_path / "app" / "resources"
    resources.mkdir()
    (resources / "pom.yaml").write_text(
        "suggestion row:\n  css: 'span.nl-suggestion'\n")
    pom_mod.set_context(str(features))
    try:
        pom_mod._load_yaml.cache_clear()
        page = MagicMock()
        page.url = "https://example.com"
        assert pom_mod.raw_locator(page, "suggestion row") is not None
        assert pom_mod.raw_locator(page, "no such key") is None
        assert "span.nl-suggestion" in pom_mod.entry_summary("suggestion row")
        assert pom_mod.entry_summary("no such key") == ""
    finally:
        pom_mod.set_context(None)
        pom_mod._load_yaml.cache_clear()


# --- P0-2: sync Playwright entrypoints survive an asyncio host -----------------

def test_outside_asyncio_runs_in_thread_under_loop():
    @probe_mod.outside_asyncio
    def body(x):
        return x * 2

    async def call():
        return body(21)

    assert asyncio.run(call()) == 42     # would raise without the guard
    assert body(21) == 42                # no loop → direct call


# --- P1-1: --suggest payload shaping (pure) ------------------------------------

def test_suggest_block_shapes_rows_and_steps():
    rows = [
        {"text": "vaccum cleaner", "id": "", "href": "/search?q=vaccum+cleaner",
         "base": '[role="option"]', "icon": "trigger-typeahead-icon"},
        {"text": "vaccum bags", "id": "", "href": "",
         "base": '[role="option"]', "icon": ""},
    ]
    blk = probe_mod._suggest_block(rows, "Vaccu")
    assert blk["suggestions"] == ["vaccum cleaner", "vaccum bags"]
    assert blk["rows"][0]["selector"] == 'a[href="/search?q=vaccum+cleaner"]'
    assert blk["rows"][0]["icon_is_noop"] is True
    assert blk["rows"][1]["selector"] == '[role="option"] >> text=vaccum bags'
    assert "icon_is_noop" not in blk["rows"][1]
    assert blk["steps"] == [
        'Then the search suggestions for "Vaccu" include "vaccum cleaner"',
        'When User selects the "vaccum cleaner" suggestion for "Vaccu"',
    ]
    # every suggested step must resolve in the pattern table
    for step in blk["steps"]:
        assert _match(step.split(" ", 1)[1]) is not None


def test_suggest_block_empty_is_none():
    assert probe_mod._suggest_block([], "Vaccu") is None


def test_suggest_steps_ride_the_skeleton():
    pg = {"headings": [], "suggest": probe_mod._suggest_block(
        [{"text": "vaccum cleaner", "id": "", "href": "", "base": "", "icon": ""}],
        "Vaccu")}
    steps = probe_mod._skeleton_steps(pg)
    assert 'When User selects the "vaccum cleaner" suggestion for "Vaccu"' in steps


# --- P1-3: affordance tiebreak + zero-effect click warning ---------------------

def test_dom_scan_tiebreak_prefers_activation_affordance():
    icon = {"tag": "span", "id": "trigger-icon", "name": "", "testid": "",
            "aria": "vaccum cleaner", "title": "", "ph": "", "cls": "",
            "visible": True, "afford": False}
    row = {"tag": "a", "id": "suggestion-row", "name": "", "testid": "",
           "aria": "vaccum cleaner", "title": "", "ph": "", "cls": "",
           "visible": True, "afford": True}
    scope = MagicMock()
    scope.evaluate.return_value = [icon, row]   # icon first in DOM order
    assert dom_scan.best_selector(scope, "vaccum cleaner") == '[id="suggestion-row"]'
    # the icon still wins when it is the ONLY match — no worse than before
    scope.evaluate.return_value = [icon]
    assert dom_scan.best_selector(scope, "vaccum cleaner") == '[id="trigger-icon"]'


class _EffectPage:
    def __init__(self, mutations=0, navigates=False):
        self.url = "https://example.com/a"
        self._mut = mutations
        self._navigates = navigates

    def evaluate(self, js):
        if "MutationObserver" in js:
            return True                     # arming succeeded
        if self._navigates:
            self.url = "https://example.com/b"
        return self._mut

    def wait_for_timeout(self, ms):
        pass


def test_click_with_no_effect_warns(monkeypatch):
    recorded = []
    from noodle import healing
    monkeypatch.setattr(healing, "record",
                        lambda text, strategy, note="": recorded.append(strategy))
    page = _EffectPage(mutations=0)
    probe = actions._arm_click_probe(page)
    actions._warn_if_no_effect(page, "vaccum cleaner suggestion", probe)
    assert recorded == ["no-effect-click"]


def test_click_with_effect_stays_silent(monkeypatch):
    recorded = []
    from noodle import healing
    monkeypatch.setattr(healing, "record",
                        lambda text, strategy, note="": recorded.append(strategy))
    page = _EffectPage(mutations=5)
    probe = actions._arm_click_probe(page)
    started = time.monotonic()
    actions._warn_if_no_effect(page, "real button", probe)
    assert recorded == []
    assert time.monotonic() - started < 0.5   # exits on first mutation check


# --- P1-2: the composite + assertion actions (fake page) -----------------------

class _Row:
    def __init__(self, text, href_count=0):
        self._text = text
        self._href_count = href_count
        self.clicked = False
        self.link = SimpleNamespace(count=lambda: href_count,
                                    first=SimpleNamespace(click=None))
        self.link.first = SimpleNamespace(click=self._click)

    def _click(self):
        self.clicked = True

    def inner_text(self):
        return self._text

    def locator(self, sel):
        return self.link

    def click(self):
        self.clicked = True


class _RowSet:
    def __init__(self, rows):
        self._rows = rows

    def locator(self, sel):
        return self

    def count(self):
        return len(self._rows)

    def nth(self, i):
        return self._rows[i]


class _SuggestPage:
    def __init__(self, texts):
        self.url = "https://example.com"
        self._rows = [_Row(t) for t in texts]

    def locator(self, sel):
        if sel == '[role="option"]':
            return _RowSet(self._rows)
        return _RowSet([])

    def wait_for_timeout(self, ms):
        pass


def test_assert_suggestions_include_matches(monkeypatch):
    monkeypatch.setenv("NOODLE_TIMEOUT", "300")
    page = _SuggestPage(["vaccum cleaner", "vaccum bags"])
    actions.assert_suggestions_include(page, "cleaner")          # no raise
    actions.assert_suggestions_include(page, None)               # list open


def test_assert_suggestions_include_fails_with_listing(monkeypatch):
    monkeypatch.setenv("NOODLE_TIMEOUT", "300")
    page = _SuggestPage(["vaccum cleaner"])
    with pytest.raises(AssertionError) as e:
        actions.assert_suggestions_include(page, "zzz")
    assert "vaccum cleaner" in str(e.value)    # the evidence an author needs


def test_select_suggestion_clicks_matching_row(monkeypatch):
    monkeypatch.setenv("NOODLE_TIMEOUT", "300")
    typed = []
    page = _SuggestPage(["vaccum cleaner", "vaccum bags"])

    class _Box:
        def click(self):
            pass

        def fill(self, v):
            pass

        def press_sequentially(self, term, delay=0):
            typed.append((term, delay))

    monkeypatch.setattr(actions, "_resolve_search_box", lambda p: _Box())
    actions.select_suggestion(page, "vaccum cleaner", term="Vaccu")
    assert typed == [("Vaccu", 60)]            # per-character typing path
    assert page._rows[0].clicked
    assert not page._rows[1].clicked


def test_select_suggestion_missing_row_fails_prescriptively(monkeypatch):
    monkeypatch.setenv("NOODLE_TIMEOUT", "300")
    page = _SuggestPage(["vaccum bags"])
    with pytest.raises(AssertionError) as e:
        actions.select_suggestion(page, "vaccum cleaner")
    msg = str(e.value)
    assert "vaccum bags" in msg                # what WAS there
    assert 'for "<partial term>"' in msg       # the fix, in the failure


# --- P2-1: probe prefers the visible twin --------------------------------------

def _ctl(**kw):
    base = {"tag": "input", "id": "", "role": "", "type": "text", "name": "",
            "testid": "", "aria": "", "title": "", "ph": "", "alt": "",
            "cls": "", "href": "", "text": "", "label": "", "visible": True,
            "expanded": "", "shadow": ""}
    base.update(kw)
    return base


def test_probe_pom_suggestion_prefers_visible_twin():
    raw = {"controls": [
        _ctl(id="search-input", visible=False),              # hidden twin first
        _ctl(id="search-input-0", aria="search input"),      # the usable one
    ], "headings": []}
    pg = probe_mod.summarize(raw, url="https://example.com")
    hidden = next(c for c in pg["controls"] if not c["visible"])
    assert hidden.get("hidden_twin") is True
    assert 'search-input-0' in "\n".join(hidden["pom"])
    assert 'search-input-0' in pg["pom_yaml"]
    assert '[id="search-input"]' not in pg["pom_yaml"]


# ============================================================================
# NOOD_0141 round 2 — locale walls, typo-verb tier, goal `suggest`, RCA verdict
# ============================================================================

# --- locale-tolerant number parsing (shared parse_number) ----------------------

def test_parse_number_us_and_european_formats():
    cases = {
        "93 results": 93.0,
        "Showing 1,234 items": 1234.0,
        "1.234.567 Ergebnisse": 1234567.0,   # de thousands (repeated dot)
        "1 234,56 résultats": 1234.56,       # fr space thousands + decimal comma
        "3,5 Sterne": 3.5,                   # lone decimal comma
        "4.5 stars": 4.5,                    # lone dot stays US decimal
        "no digits here": None,
    }
    for text, want in cases.items():
        assert probe_mod.parse_number(text) == want, text


def test_count_regex_matches_locale_results_lines():
    for text in ("93 results", "1.234 Ergebnisse", "12 résultats",
                 "1 234 resultados", "57 risultati", "31 producten"):
        assert probe_mod._COUNT_RE.search(text), text


def test_read_number_handles_european_format(monkeypatch):
    monkeypatch.setattr(actions, "get_text",
                        lambda page, t: "1.234,56 Ergebnisse")
    page = MagicMock()
    assert actions.read_number(page, "results summary") == 1234.56


def test_screen_first_number_locale():
    from noodle.agents.web import screen
    assert screen.first_number("Now $1,299.99!") == "1299.99"
    assert screen.first_number("1.299,99 €") == "1299.99"
    assert screen.first_number("no digits") is None


# --- locale mutating/disclosure/echo gates -------------------------------------

def test_mutating_gate_speaks_locales():
    for name in ("Löschen", "Supprimer", "Warenkorb speichern", "confirmar",
                 "In den Warenkorb kaufen", "s'inscrire", "uitloggen"):
        assert probe_mod._is_mutating(name), name
    for name in ("Einstellungen", "Filtres", "más opciones", "menu"):
        assert not probe_mod._is_mutating(name), name


def test_submit_type_is_mutating_whatever_the_language():
    # a submit control with an unrecognizable label must still never be
    # auto-clicked — the attribute is the locale-proof signal
    assert probe_mod._is_mutating_control({"name": "続行", "submit": True})
    assert not probe_mod._is_mutating_control({"name": "続行"})


def test_summarize_stamps_submit_controls():
    raw = {"controls": [_ctl(tag="input", type="submit", id="go-btn")],
           "headings": []}
    pg = probe_mod.summarize(raw, url="https://example.com")
    assert pg["controls"][0].get("submit") is True


def test_discover_skips_submit_control_with_reason():
    controls = [{"name": "続行", "kind": "button", "visible": False,
                 "selector": "#x", "submit": True}]
    cands, skipped = probe_mod._discover_candidates(controls)
    assert cands == []
    assert skipped == [{"name": "続行", "reason": "submit control"}]


def test_disclosure_and_result_echo_speak_locales():
    for name in ("Einstellungen", "paramètres", "ajustes", "impostazioni",
                 "instellingen", "más"):
        assert probe_mod._DISCLOSURE_RE.search(name), name
    for heading in ("1.234 Ergebnisse", "Resultados para zapatos",
                    "Risultati della ricerca", "12 treffer"):
        assert probe_mod._is_search_echo(heading, "zzz"), heading


def test_auth_synonyms_speak_locales():
    alts = locator._synonym_candidates("login button")
    assert "anmelden button" in alts
    assert "connexion button" in alts
    assert "iniciar sesión button" in alts


def test_search_box_shapes_cover_textarea_and_role():
    joined = " ".join(probe_mod._SEARCH_BOXES)
    assert 'form[role="search"] textarea' in joined      # Google-shaped box
    assert 'input[aria-label*="suche" i]' in joined      # localized aria


# --- deterministic typo-verb tier ----------------------------------------------

def test_within_one_edit_truth_table():
    from noodle.resolver.patterns import _within_one_edit
    assert _within_one_edit("clciks", "clicks")     # adjacent transposition
    assert _within_one_edit("clcks", "clicks")      # deletion
    assert _within_one_edit("clickss", "clicks")    # insertion
    assert _within_one_edit("clocks", "clicks")     # substitution
    assert not _within_one_edit("clks", "clicks")   # two edits
    assert not _within_one_edit("xyz", "clicks")


def test_typo_verb_resolves_deterministically():
    assert _match('User clciks the login button') == \
        ("click", {"locator": "login"})
    assert _match('User entrs "bob" in the username field')[0] == "fill"
    assert _match('User selets the "running shoes" suggestion')[0] == \
        "select_suggestion"


def test_ambiguous_typo_is_never_guessed():
    # "chicks" is one edit from BOTH "clicks" and "checks" — must not resolve
    assert _match('User chicks the box') is None


def test_known_verb_never_rewritten():
    # the verb is fine, the phrase just doesn't exist — no fuzzy rerun mangling
    assert _match('User clicks') is None


# --- goal `do: suggest` --------------------------------------------------------

def _suggest_goal(**overrides):
    goal = {"scenario": "Suggestion search works",
            "actions": [{"do": "suggest", "id": "pick",
                         "term": "Vaccu", "option": "vaccum cleaner"}],
            "checks": [{"see": "BISSELL PowerLifter", "after": "pick"}]}
    goal.update(overrides)
    return goal


def _suggest_probe_result():
    return {"pages": [{
        "controls": [], "headings": [], "revealed": [],
        "suggest": probe_mod._suggest_block(
            [{"text": "vaccum cleaner", "id": "", "href": "",
              "base": '[role="option"]', "icon": "trigger-typeahead-icon"},
             {"text": "vaccuum", "id": "", "href": "", "base": "", "icon": ""}],
            "Vaccu"),
    }]}


def test_goal_validate_accepts_suggest():
    from noodle.repl import goal as goal_mod
    assert goal_mod.validate(_suggest_goal()) == []


def test_goal_validate_suggest_requires_term_and_option():
    from noodle.repl import goal as goal_mod
    errs = goal_mod.validate({"scenario": "s",
                              "actions": [{"do": "suggest", "term": "Va"}]})
    assert any("option is required" in e for e in errs)


def test_goal_validate_one_search_or_suggest():
    from noodle.repl import goal as goal_mod
    goal = _suggest_goal()
    goal["actions"] = goal["actions"] + [{"do": "search", "term": "Vaccu"}]
    assert any("one search or suggest" in e for e in goal_mod.validate(goal))


def test_goal_validate_bans_manual_clicks_beside_suggest():
    from noodle.repl import goal as goal_mod
    goal = _suggest_goal()
    goal["actions"] = [{"do": "click", "target": "search icon"}] + goal["actions"]
    assert any("suggestion picking is composite" in e
               for e in goal_mod.validate(goal))


def test_goal_probe_args_carry_suggest_term():
    from noodle.repl import goal as goal_mod
    args = goal_mod.probe_args(_suggest_goal())
    assert args["suggest"] == "Vaccu"
    assert args["search"] is None
    assert args["discover"] is False


def test_goal_checks_after_suggest_are_runtime_asserted():
    from noodle.repl import goal as goal_mod
    ev = goal_mod.evidence(_suggest_goal(), _suggest_probe_result())
    assert ev["blocking"] == []
    assert ev["proven"]["suggest:Vaccu"] == "vaccum cleaner"
    # the post-click results check can't be probe-proven — the run owns it
    assert ev["runtime_asserted"] == ['the user sees "BISSELL PowerLifter"']


def test_goal_suggest_blocks_on_uncaptured_option():
    from noodle.repl import goal as goal_mod
    goal = _suggest_goal()
    goal["actions"][0]["option"] = "totally unrelated"
    ev = goal_mod.evidence(goal, _suggest_probe_result())
    assert any("not among the captured suggestions" in b for b in ev["blocking"])


def test_goal_suggest_blocks_on_probe_warning():
    from noodle.repl import goal as goal_mod
    result = {"pages": [{"controls": [], "headings": [],
                         "suggest_warning": '--suggest "Vaccu": no search box found'}]}
    ev = goal_mod.evidence(_suggest_goal(), result)
    assert any("no search box found" in b for b in ev["blocking"])


def test_goal_compiles_suggest_to_resolvable_steps():
    from noodle.repl import goal as goal_mod
    goal = _suggest_goal()
    ev = goal_mod.evidence(goal, _suggest_probe_result())
    feature, pom = goal_mod.compile_goal(goal, ev, "APP")
    assert 'the search suggestions for "Vaccu" include "vaccum cleaner"' in feature
    assert 'selects the "vaccum cleaner" suggestion for "Vaccu"' in feature
    # intent assertion precedes the pick; the runtime check follows it
    assert feature.index("search suggestions") < feature.index("selects the")
    assert feature.index("selects the") < feature.index("BISSELL")
    # every compiled step must resolve in the deterministic pattern table
    for line in feature.splitlines():
        line = line.strip()
        if line.split(" ", 1)[0] in ("Given", "When", "Then", "And"):
            body = line.split(" ", 1)[1]
            assert _match(body) is not None, body


def test_goal_compile_uses_canonical_page_spelling():
    from noodle.repl import goal as goal_mod
    goal = _suggest_goal()
    goal["actions"][0]["option"] = "VACCUM CLEANER"   # case paraphrase
    ev = goal_mod.evidence(goal, _suggest_probe_result())
    feature, _ = goal_mod.compile_goal(goal, ev, "APP")
    assert '"vaccum cleaner" suggestion' in feature   # page spelling wins


# --- RCA: no-effect click verdict ----------------------------------------------

def test_classify_flags_no_effect_click(tmp_path):
    from noodle.reporting import rca_report
    entry = {"message": "Expected to see 'BISSELL' — not found.", "trace": "",
             "warnings": [],
             "scenario_warnings": ["Click on 'vaccum cleaner suggestion' had "
                                   "no observable effect — no navigation, DOM "
                                   "change, or network request within 1.2s."]}
    verdict = rca_report.classify(entry)
    assert verdict["category"] == "locator-rot"
    assert "vaccum cleaner suggestion" in verdict["reason"]
    assert "suggestion" in verdict["fix"]


def test_classify_survives_legacy_entries_without_scenario_warnings():
    from noodle.reporting import rca_report
    entry = {"message": "Could not find element to click: 'x'", "trace": "",
             "warnings": []}
    assert rca_report.classify(entry)["category"] == "locator-rot"


def test_collect_gathers_scenario_wide_warnings(tmp_path):
    import json

    from noodle.reporting import rca_report
    result = {
        "name": "Search via suggestion", "status": "failed",
        "historyId": "h1", "stop": 1, "labels": [],
        "steps": [
            {"name": "User clicks the suggestion", "status": "passed",
             "statusDetails": {"warnings": [
                 "Click on 'suggestion' had no observable effect — no "
                 "navigation, DOM change, or network request within 1.2s."]}},
            {"name": "the user sees 'BISSELL'", "status": "failed",
             "statusDetails": {"message": "Expected to see 'BISSELL'",
                               "trace": "", "warnings": []}},
        ],
    }
    (tmp_path / "a-result.json").write_text(json.dumps(result))
    entries = rca_report.collect(str(tmp_path))
    assert len(entries) == 1
    assert any("no observable effect" in w
               for w in entries[0]["scenario_warnings"])
    assert entries[0]["heuristic"]["category"] == "locator-rot"
    assert "never advanced" in entries[0]["heuristic"]["reason"]
