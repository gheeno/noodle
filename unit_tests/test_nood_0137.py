"""NOOD_0137 — probe-side cost cuts + first-try-accuracy signals.

Driving a retail-homepage template prompt on main cost ~30 agent
interactions on Copilot-class models while the framework floor is ~5: the
compact probe payload was 29.6 KB of mostly irrelevant output (leaked
OneTrust internals, an uncapped tile slice, hidden facet floods filling the
cap, a page-global author_ready:false pointing at controls the test never
touches) and carried no popup/permission intel — exactly what that prompt
was about. Browser-free checks for:

  C1  OneTrust preference-center internals join the consent denylist
  C2  tile-caption slice: numbered-family collapse + the standard cap
  C3  compact rank — visible first, hidden toggles last, ambiguous last
  C4  author_ready compact-scoped to the suggestions actually shown
  C5  popup/permission signals render + ride the compact payload
  C6  scenario skeleton: assembled from probe observations, dictionary-valid
"""
import json

from noodle.agents.web import probe
from noodle.resolver.patterns import match as pattern_match
from noodle.resolver.patterns import normalize_phrasing
from unit_tests.test_nood_0117 import _c


def _page(controls, **extra):
    pg = probe.summarize({"controls": controls, "headings": []},
                         url="https://x/")
    pg.update(extra)
    return pg


# --- C1: OneTrust preference-center noise -------------------------------------

def test_onetrust_preference_center_ids_are_consent_noise():
    noisy = [_c(id="select-all-vendor-groups-handler", tag="div"),
             _c(id="chkbox-id", tag="div", type="checkbox"),
             _c(id="clear-filters-handler", tag="div"),
             _c(id="filter-cancel-handler", tag="div"),
             _c(tag="span", cls="ot-switch-nob"),
             _c(tag="span", cls="ot-label-status"),
             _c(tag="a", aria="Powered by OneTrust Opens in a new Tab")]
    result = {"pages": [_page(noisy)], "errors": []}
    comp = probe.render(result, compact=True)
    full = probe.render(result, compact=False)
    for c in result["pages"][0]["controls"]:
        assert c["name"] not in comp, f"consent leak: {c['name']}"
        assert c["name"] in full


# --- C2: tile slice capped + numbered families collapsed ----------------------

def _slide_tiles(n):
    return [_c(tag="a", href=f"/s/{i}", aria=f"Go to slide {i}")
            for i in range(1, n + 1)]


def test_numbered_tile_family_collapses_to_one_exemplar():
    tiles = _slide_tiles(9) + [_c(tag="a", href="/f", alt="View the Weekly Flyer now."),
                               _c(tag="a", href="/d", alt="View Weekly Deals now.")]
    comp = probe.render({"pages": [_page(tiles)], "errors": []}, compact=True)
    assert "go to slide 1" in comp
    assert "(+8 more numbered like it)" in comp
    assert "go to slide 2" not in comp
    # distinct captions never group, even when they share a shape
    assert "view the weekly flyer now." in comp
    assert "view weekly deals now." in comp


def test_tile_slice_finally_respects_the_compact_cap():
    tiles = [_c(tag="a", href=f"/t/{i}",
                alt=f"Promo card {chr(65 + i // 26)}{chr(65 + i % 26)}")
             for i in range(40)]   # 40 distinct digit-free captions — no families
    comp = probe.render({"pages": [_page(tiles)], "errors": []}, compact=True)
    assert "more tiles — raise --max-controls" in comp
    shown = comp.count("[link] promo card")
    assert shown == probe.DEFAULT_COMPACT_CAP


def test_marketing_length_tile_caption_collapses_to_its_pom_entry():
    long = ("Get a rewards Mastercard. Earn 4% store loyalty money on "
            "qualifying purchases. Learn more about this offer.")
    tiles = [_c(tag="a", href="/p", aria=long),
             _c(tag="a", href="/f", alt="View the Weekly Flyer now.")]
    comp = probe.render({"pages": [_page(tiles)], "errors": []}, compact=True)
    assert comp.count(long.lower()[:40]) == 1        # POM key only, no dup line
    # short captions keep the full control line (selector + step)
    assert 'clicks "view the weekly flyer now."' in comp


def test_compact_payload_tiles_capped_with_drop_count():
    tiles = _slide_tiles(9) + [_c(tag="a", href=f"/t/{i}", alt=f"Card {i}x")
                               for i in range(30)]
    pg = _page(tiles)
    pg["author_ready"] = probe._author_ready(pg)
    out = probe.compact_payload({"pages": [pg], "errors": []},
                                max_controls=25)["pages"][0]
    assert len(out["tile_captions"]) <= 25
    assert out["tile_captions_dropped"] >= 8   # at least the slide family


# --- C3: compact rank — the cap eats the junk end -----------------------------

def test_visible_control_survives_a_hidden_facet_flood():
    facets = [_c(tag="input", type="checkbox", visible=False,
                 cls=f"nl-checkbox__facet-{i}") for i in range(60)]
    # DOM-last visible control that needs a POM entry (no readable name)
    facets.append(_c(tag="span", cls="nl-switch__slider"))
    comp = probe.render({"pages": [_page(facets)], "errors": []}, compact=True)
    assert "nl switch slider" in comp   # sorted first, not capped away


def test_rank_orders_visible_then_hidden_then_hidden_toggles():
    toggle = _c(tag="input", type="checkbox", visible=False, cls="f-1")
    hidden_btn = _c(tag="div", visible=False, cls="trigger-dev-panel")
    vis = _c(tag="span", cls="nl-switch__slider")
    pg = _page([toggle, hidden_btn, vis])
    names = [c["name"] for c in probe._compact_controls(pg["controls"])]
    assert names == ["nl switch slider", "trigger dev panel", "f 1"]


def test_facet_family_collapses_in_needs_pom_list_and_pom_block():
    facets = [_c(tag="input", type="checkbox", visible=False,
                 cls=f"facet-{i}") for i in range(20)]
    comp = probe.render({"pages": [_page(facets)], "errors": []}, compact=True)
    assert "(+19 more numbered like it)" in comp
    assert comp.count("clicks \"facet") == 1          # one exemplar step line
    assert comp.count("facet 0:") == 1                # one exemplar POM entry
    assert "facet 1:" not in comp


def test_proven_ambiguous_selector_never_offered_in_the_pom_block():
    pg = _page([_c(tag="a", visible=False)])          # the bare-<a> case
    pg["controls"][0]["unique"], pg["controls"][0]["matches"] = False, 137
    comp = probe.render({"pages": [pg], "errors": []}, compact=True)
    assert "⚠ selector matches 137 nodes" in comp     # the honest flag stays
    assert "POM suggestion" not in comp               # nothing pasteable left


# --- C4: author_ready scoped to what compact actually shows -------------------

def _ambiguous_beyond_cap():
    """25 clean hidden controls + one proven-ambiguous control sorted last.
    Digit-free distinct names, so the numbered-family collapse leaves them."""
    clean = [_c(tag="div", visible=False,
                cls=f"panel-{chr(97 + i // 5)}{chr(97 + i % 5)}")
             for i in range(25)]
    pg = _page(clean + [_c(tag="a", visible=False, cls="")])
    bad = pg["controls"][-1]
    bad["unique"], bad["matches"] = False, 137
    pg["author_ready"] = probe._author_ready(pg)
    return pg


def test_capped_away_ambiguity_no_longer_blocks_author_ready():
    pg = _ambiguous_beyond_cap()
    assert pg["author_ready"] is False          # page-global verdict unchanged
    comp = probe.render({"pages": [pg], "errors": []}, compact=True)
    assert "author_ready: false" not in comp    # not among shown suggestions
    full = probe.render({"pages": [pg], "errors": []}, compact=False)
    assert "author_ready: false" in full        # full dump keeps the verdict
    payload = probe.compact_payload({"pages": [pg], "errors": []},
                                    max_controls=25)["pages"][0]
    assert payload["author_ready"] is True


def test_shown_ambiguity_still_blocks_author_ready():
    pg = _page([_c(tag="span", cls="nl-switch__slider")])
    pg["controls"][0]["unique"], pg["controls"][0]["matches"] = False, 30
    pg["author_ready"] = probe._author_ready(pg)
    comp = probe.render({"pages": [pg], "errors": []}, compact=True)
    assert "author_ready: false" in comp
    payload = probe.compact_payload({"pages": [pg], "errors": []})["pages"][0]
    assert payload["author_ready"] is False


# --- C5: popup/permission signals ---------------------------------------------

def _signal_page():
    pg = _page([_c(tag="button", text="Menu")],
               popups_closed=1, permission_prompts=["geolocation"])
    pg["author_ready"] = probe._author_ready(pg)
    return pg


def test_signals_render_with_ready_made_steps():
    out = probe.render({"pages": [_signal_page()], "errors": []}, compact=True)
    assert "permission prompt: geolocation" in out
    assert "the user closes the location prompt" in out
    assert "@permissions:geolocation" in out
    assert "popups: 1 closed during the probe" in out
    assert "closes the popup if it appears within 10 seconds" in out


def test_signals_ride_the_compact_payload():
    blob = probe.compact_payload({"pages": [_signal_page()], "errors": []})
    pg = blob["pages"][0]
    assert pg["popups_closed"] == 1
    assert pg["permission_prompts"] == ["geolocation"]


# --- C6: scenario skeleton ----------------------------------------------------

def _search_page():
    pg = _signal_page()
    sr = probe.summarize({"controls": [], "headings": []}, url="https://x/s")
    sr["term"] = "Hot Wheels"
    sr["results_summary"] = {
        "text": "40 results", "selector": "[id=s]", "count": 40,
        "pom_yaml": "results summary:\n  css: '[id=\"s\"]'\n",
        "suggested_assertion": probe._summary_assertion(),
    }
    pg["search"] = sr
    pg["headings"] = ["Trending Searches"]
    return pg


def test_skeleton_assembles_nav_signals_search_and_floor_in_order():
    steps = probe._skeleton_steps(_search_page())
    assert steps == [
        'Given User is on "{env:<APP>}"',
        "When the user closes the location prompt",
        "And closes the popup if it appears within 10 seconds",
        'When User searches for "Hot Wheels"',
        f"Then {probe._summary_assertion()}",
        'Then the user sees "Trending Searches"',
    ]


def test_skeleton_steps_all_match_the_pattern_table():
    for step in probe._skeleton_steps(_search_page()):
        # behave strips the Gherkin keyword; the catch-all strips the subject
        for prefix in ("Given ", "When ", "Then ", "And ", "the user ",
                       "User "):
            if step.startswith(prefix):
                step = step[len(prefix):]
        step = step.replace("{env:<APP>}", "https://x/")
        assert pattern_match(normalize_phrasing(step)) is not None, step


# --- C7: --discover blocks are signals, not catalogs (Fix A) ------------------

def _discovered(name, n, headings=None):
    """A reveal block as _discover records it: n distinct digit-free
    needs-POM controls (no numbered-family collapse)."""
    controls = [_c(tag="div", cls=f"{name}-{chr(97 + i // 5)}{chr(97 + i % 5)}")
                for i in range(n)]
    rev = probe.summarize({"controls": controls,
                           "headings": headings or []}, url="https://x/")
    rev["revealed_by"], rev["discovered"] = name, True
    return rev


def _control_line_count(comp, name):
    """Control lines carry the `→ step` arrow; headers/pointers don't."""
    return sum(1 for ln in comp.splitlines() if name in ln and "→" in ln)


def test_discovered_block_gets_the_small_cap_and_one_list():
    pg = _page([_c(tag="button", text="Menu")])
    pg["revealed"] = [_discovered("open-menu", 31, headings=["Shop"])]
    comp = probe.render({"pages": [pg], "errors": []}, compact=True)
    assert 'discovered by clicking "open-menu" (31 new controls' in comp
    assert _control_line_count(comp, "open menu") == probe.DISCOVER_COMPACT_CAP
    assert f"… (+{31 - probe.DISCOVER_COMPACT_CAP} more" in comp
    # single list: the honest pointer replaces steps/tiles/exact-texts/POM
    assert 're-probe --click "open-menu" for its steps + POM' in comp
    assert '"Shop"' not in comp                    # no exact-texts list
    assert "copy-ready steps" not in comp.split("discovered by clicking")[1]


def test_explicit_click_reveal_keeps_the_full_compact_block():
    pg = _page([_c(tag="button", text="Menu")])
    rev = _discovered("settings", 31, headings=["Prefs"])
    del rev["discovered"]                          # a --click the caller named
    pg["revealed"] = [rev]
    comp = probe.render({"pages": [pg], "errors": []}, compact=True)
    assert 'revealed after clicking "settings"' in comp
    assert _control_line_count(comp, "settings") == probe.DEFAULT_COMPACT_CAP
    assert '"Prefs"' in comp                       # exact texts stay


def test_explicit_max_controls_still_wins_for_discovered_blocks():
    pg = _page([_c(tag="button", text="Menu")])
    pg["revealed"] = [_discovered("open-menu", 31)]
    comp = probe.render({"pages": [pg], "errors": []}, compact=True,
                        max_controls=20)
    assert _control_line_count(comp, "open menu") == 20


def test_discovered_block_warnings_survive_the_diet():
    pg = _page([_c(tag="button", text="Menu")])
    rev = _discovered("open-menu", 2)
    rev["warnings"] = ["panel re-renders on hover"]
    pg["revealed"] = [rev]
    comp = probe.render({"pages": [pg], "errors": []}, compact=True)
    assert "⚠ panel re-renders on hover" in comp


def test_compact_payload_caps_discovered_blocks_too():
    pg = _page([_c(tag="button", text="Menu")])
    pg["revealed"] = [_discovered("open-menu", 31)]
    out = probe.compact_payload({"pages": [pg], "errors": []},
                                max_controls=40)["pages"][0]
    rev = out["revealed"][0]
    assert len(rev["needs_pom"]) == probe.DISCOVER_COMPACT_CAP
    assert rev["total_controls"] == 31
    assert "truncated" in rev


# --- C8: search-echo headings never offered as verbatim assertions (Fix B) ----

def _echo_search_page():
    pg = _search_page()
    pg["search"]["headings"] = ['Showing Result(s) for "Hot Wheels"',
                                "Hot Wheels Monster Trucks",
                                "Related categories"]
    return pg


def test_result_echo_headings_suppressed_from_search_exact_texts():
    comp = probe.render({"pages": [_echo_search_page()], "errors": []},
                        compact=True)
    assert 'Showing Result(s) for' not in comp
    assert "Hot Wheels Monster Trucks" not in comp   # echoes the term
    assert '"Related categories"' in comp
    # relabelled: results-page texts are observations, not safe assertions
    assert "verify before asserting" in comp
    # the page-level (pre-search) list keeps the verbatim contract
    assert 'exact texts (copy assertions verbatim): "Trending Searches"' in comp


def test_result_echo_suppressed_in_compact_payload_headings():
    payload = probe.compact_payload(
        {"pages": [_echo_search_page()], "errors": []})["pages"][0]
    assert payload["search"]["headings"] == ["Related categories"]
    assert payload["headings"] == ["Trending Searches"]


def test_skeleton_in_compact_render_and_payload_not_in_full():
    pg = _search_page()
    result = {"pages": [pg], "errors": []}
    comp = probe.render(result, compact=True)
    assert "scenario skeleton" in comp
    assert "base_url_key" in comp                  # the <APP> hint
    assert "scenario skeleton" not in probe.render(result, compact=False)
    payload = probe.compact_payload(result)["pages"][0]
    assert payload["skeleton"][0] == 'Given User is on "{env:<APP>}"'
    assert "skeleton" not in json.dumps(payload["search"])   # top level only


# --- C9: constrained goal mode — the engine owns Gherkin/POM/scope ------------
# The 29.185-AIC regression's three red runs were model-authored integration
# mistakes (substituted popup steps, a manual search trigger, a dropped
# match:{}). Goal compilation makes each structurally impossible.

from noodle.repl import goal as goal_mod  # noqa: E402 — section-scoped import
from noodle.repl import validate as _validate  # noqa: E402


def _goal(**over):
    g = {"scenario": "CT search",
         "actions": [{"do": "search", "term": "Hot Wheels", "id": "s"}],
         "checks": [{"see": "Weekly Flyer"},
                    {"count": "results summary", "min": 1, "after": "s"},
                    {"any_of": ["Hot Wheels", "Die Cast"], "min": 1,
                     "after": "s"}],
         "dismissals": ["location_prompt", "popups"]}
    g.update(over)
    return g


def _probe_result(**page_over):
    pg = {"url": "https://x/", "title": "t",
          "controls": [{"name": "view the weekly flyer now.", "kind": "link",
                        "selector": "a[href='/f']", "visible": True,
                        "needs_pom": False, "step": "x"}],
          "headings": [], "pom_yaml": "", "next_pages": [],
          "permission_prompts": ["geolocation"], "popups_closed": 2,
          "search": {"term": "Hot Wheels",
                     "controls": [{"name": "hot wheels monster truck",
                                   "kind": "link", "selector": "a",
                                   "visible": True, "needs_pom": False,
                                   "step": "x"}],
                     "headings": [], "pom_yaml": "",
                     "results_summary": {"text": "40 results",
                                         "selector": '[id="sc"]', "count": 40,
                                         "pom_yaml": "",
                                         "suggested_assertion": "x"}}}
    pg.update(page_over)
    return {"pages": [pg], "errors": []}


def _compiled(goal=None, probe=None):
    g = goal or _goal()
    ev = goal_mod.evidence(g, probe or _probe_result())
    return goal_mod.compile_goal(g, ev, "CT"), ev


def test_goal_validation_rejects_malformed_structure():
    assert goal_mod.validate(_goal()) == []
    assert any("unknown goal field" in e
               for e in goal_mod.validate(_goal(bogus=1)))
    assert any("scenario is required" in e
               for e in goal_mod.validate(_goal(scenario="")))
    dup = _goal(actions=[{"do": "search", "term": "x", "id": "a"},
                         {"do": "click", "target": "menu", "id": "a"}])
    assert any("duplicate action id" in e for e in goal_mod.validate(dup))
    fwd = _goal(checks=[{"see": "x", "after": "nope"}])
    assert any("names no action id" in e for e in goal_mod.validate(fwd))
    assert any("non-empty list" in e for e in goal_mod.validate(
        _goal(checks=[{"any_of": []}])))
    assert any("positive integer" in e for e in goal_mod.validate(
        _goal(checks=[{"count": "results summary", "min": 0}])))


def test_permission_and_popup_evidence_compile_to_separate_ordered_steps():
    (feat, _), _ = _compiled()
    lines = [ln.strip() for ln in feat.splitlines() if ln.strip()]
    nav = lines.index('Given User is on "{env:CT}"')
    perm = lines.index("When the user closes the location prompt")
    pop = lines.index("And closes the popup if it appears within 10 seconds")
    assert nav < perm < pop
    # observed AND requested — still exactly one step each (deduped)
    assert feat.count("location prompt") == 1
    assert feat.count("closes the popup") == 1


def test_composite_search_cannot_gain_a_manual_trigger():
    bad = _goal(actions=[{"do": "click", "target": "Search Trigger"},
                         {"do": "search", "term": "Hot Wheels", "id": "s"}])
    assert any("search is composite" in e for e in goal_mod.validate(bad))
    (feat, _), _ = _compiled()          # a clean goal compiles ONE search step
    assert feat.count('searches for "Hot Wheels"') == 1
    assert 'clicks "search' not in feat.lower()


def test_compiled_pom_always_opens_with_match_block():
    (_, pom), _ = _compiled()
    assert pom.splitlines()[1].startswith("match: {}")


def test_alternatives_both_survive_in_one_constrained_selector():
    (_, pom), _ = _compiled()
    sel_line = next(ln for ln in pom.splitlines()
                    if "Hot Wheels" in ln or "Die Cast" in ln)
    assert "Hot Wheels" in sel_line and "Die Cast" in sel_line


def test_requested_minimum_is_preserved():
    (feat, _), _ = _compiled()
    assert "the number in 'results summary' should be at least 1" in feat
    (feat3, _), _ = _compiled(goal=_goal(
        checks=[{"count": "results summary", "min": 3, "after": "s"}]))
    assert "should be at least 3" in feat3


def test_goal_compiled_steps_all_match_the_pattern_table():
    (feat, _), _ = _compiled()
    chk = _validate.check_feature(feat)
    assert chk["error"] is None
    assert _validate.unmatched(chk) == []


def test_unproven_check_blocks_but_is_never_dropped():
    probe = _probe_result(controls=[])          # no weekly-flyer evidence
    (feat, _), ev = _compiled(probe=probe)
    assert any("Weekly Flyer" in b for b in ev["blocking"])
    assert 'the user sees "Weekly Flyer"' in feat   # request kept verbatim


def test_probe_args_scope_to_the_goal_only():
    args = goal_mod.probe_args(_goal())
    assert args == {"search": "Hot Wheels", "suggest": None, "pick": None,
                    "mutate": None, "click": None,
                    "open_native_controls": False, "discover": False}
    g = _goal(actions=[{"do": "select", "target": "store", "option": "64",
                        "id": "sel"}],
              checks=[], probe={"discover": True})
    args = goal_mod.probe_args(g)
    assert args["open_native_controls"] is True and args["discover"] is True


def test_action_steps_use_probed_canonical_names_and_pom_hidden_triggers():
    g = _goal(actions=[{"do": "click", "target": "Trigger-Dev-Panel"}],
              checks=[], dismissals=[])
    probe = _probe_result(controls=[
        {"name": "trigger dev panel", "kind": "button",
         "selector": "div.trigger-dev-panel", "visible": False,
         "needs_pom": True, "step": "x",
         "pom": ["trigger dev panel:", "  css: 'div.trigger-dev-panel'"]}],
        search=None)
    ev = goal_mod.evidence(g, probe)
    feat, pom = goal_mod.compile_goal(g, ev, "CT")
    assert 'User clicks "trigger dev panel"' in feat   # probed spelling wins
    assert "trigger dev panel:" in pom
