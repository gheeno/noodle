"""NOOD_0142 — one-probe typeahead flows + task-first probe output
(authoring-session post-mortem #2: 5 browser probes for one easy test).

F1  --follow: _pick_suggestion matches containment-first, then difflib —
    a correctly-spelled ask finds the site's misspelled row.
F2  --expect: presence verdicts render at the TOP, hits become copy-ready
    `User should see` steps (render + skeleton).
F3  Task-first render: --suggest/--search/--expect blocks print BEFORE the
    initial-page inventory; the inventory is dieted while a task flag is
    active (no tiles/steps flood; --max-controls restores it).
F4  --follow landing: search block renders as a suggestion pick, and the
    skeleton must NOT emit `User searches for` (that would author a submit
    instead of the pick).

No browser, no LLM, no network.
"""
from noodle.agents.web import probe as probe_mod

# --- F1: _pick_suggestion ------------------------------------------------------

def test_pick_containment_beats_fuzzy():
    texts = ["vaccum cleaner", "vaccum cleaner bags", "shop vac"]
    assert probe_mod._pick_suggestion(texts, "vaccum cleaner bags") == 1
    # substring either direction
    assert probe_mod._pick_suggestion(texts, "cleaner") == 0
    assert probe_mod._pick_suggestion(texts, "the shop vac deluxe") == 2


def test_pick_fuzzy_finds_misspelled_row():
    # the real trap: user says "vacuum cleaner", site row says "vaccum cleaner"
    texts = ["robot mop", "vaccum cleaner", "air purifier"]
    assert probe_mod._pick_suggestion(texts, "vacuum cleaner") == 1


def test_pick_no_match_returns_none():
    assert probe_mod._pick_suggestion(["red shoes", "blue shoes"],
                                      "lawn mower") is None
    assert probe_mod._pick_suggestion([], "anything") is None


def test_pick_is_case_insensitive():
    assert probe_mod._pick_suggestion(["Vaccum Cleaner"], "VACCUM cleaner") == 0


# --- fixtures ------------------------------------------------------------------

def _control(name="save", sel='[id="save"]'):
    return {"kind": "button", "name": name, "selector": sel,
            "needs_pom": False, "step": f'clicks "{name}"',
            "visible": True, "hidden": False, "pom_yaml": "", "ambiguous": 0}


def _page(**over):
    pg = {"url": "https://x.test", "title": "X", "controls": [_control()],
          "headings": ["Welcome"], "next_pages": [], "pom_yaml": "",
          "author_ready": True}
    pg.update(over)
    return pg


def _search_block(followed=False):
    sr = {"term": "vaccum cleaner", "controls": [_control("add to cart")],
          "url": "https://x.test/results", "title": "Results",
          "headings": [], "next_pages": [], "pom_yaml": "",
          "results_summary": {
              "text": "212 results", "selector": "span.results", "count": 212,
              "pom_yaml": "results summary:\n  css: span.results\n",
              "suggested_assertion":
                  "the number in 'results summary' should be at least 1"}}
    if followed:
        sr["followed_from"] = "Vaccu"
    return sr


def _suggest_dict(followed=None):
    sg = {"term": "Vaccu",
          "suggestions": ["vaccum cleaner", "vaccum cleaner bags"],
          "rows": [{"text": "vaccum cleaner", "selector": '[id="s0"]'},
                   {"text": "vaccum cleaner bags", "selector": '[id="s1"]'}],
          "steps": ['Then the search suggestions for "Vaccu" include '
                    '"vaccum cleaner"',
                    'When User selects the "vaccum cleaner" suggestion '
                    'for "Vaccu"']}
    if followed:
        sg["followed"] = followed
    return sg


def _render(pg, **kw):
    return probe_mod.render({"pages": [pg], "errors": []}, **kw)


# --- F2: --expect rendering ----------------------------------------------------

def test_expect_verdicts_and_ready_step():
    pg = _page(expect=[
        {"text": "PowerLifter", "found": True, "context": "BISSELL PowerLifter Vacuum"},
        {"text": "Unicorn", "found": False}])
    out = _render(pg)
    assert 'expect "PowerLifter": FOUND' in out
    assert 'Then User should see "PowerLifter"' in out
    assert 'expect "Unicorn": NOT FOUND' in out
    # the miss must never produce a should-see step
    assert 'Then User should see "Unicorn"' not in out


def test_expect_hits_land_in_skeleton_misses_do_not():
    pg = _page(expect=[{"text": "PowerLifter", "found": True, "context": "c"},
                       {"text": "Unicorn", "found": False}])
    steps = probe_mod._skeleton_steps(pg)
    assert 'Then User should see "PowerLifter"' in steps
    assert all("Unicorn" not in s for s in steps)


# --- F3: task-first ordering + inventory diet ----------------------------------

def test_task_blocks_print_before_inventory():
    pg = _page(suggest=_suggest_dict(),
               expect=[{"text": "PowerLifter", "found": True, "context": "c"}],
               search=_search_block())
    out = _render(pg, compact=True)
    i_expect = out.index('expect "PowerLifter"')
    i_suggest = out.index("typeahead suggestions")
    i_search = out.index("after searching")
    i_controls = out.index("controls (")
    assert i_expect < i_suggest < i_search < i_controls


def test_inventory_dieted_when_task_flag_active():
    tiles = [{"kind": "link", "name": f"banner {i}", "selector": f'[id="b{i}"]',
              "needs_pom": True, "step": f'clicks "banner {i}"',
              "visible": True, "hidden": False,
              "pom_yaml": f'banner {i}:\n  css: \'[id="b{i}"]\'\n',
              "ambiguous": 0, "caption_attr": True} for i in range(30)]
    pg = _page(controls=[_control()] + tiles, search=_search_block())
    dieted = _render(pg, compact=True)
    assert "dieted (task flags active)" in dieted
    # explicit --max-controls restores the full inventory
    full = _render(pg, compact=True, max_controls=100)
    assert "dieted (task flags active)" not in full


def test_no_diet_without_task_flags():
    out = _render(_page(), compact=True)
    assert "dieted" not in out


def test_suggest_warning_prints_before_inventory():
    pg = _page(suggest_warning='--follow "x": no suggestion row matches — '
                               'visible: "a"; "b"')
    out = _render(pg, compact=True)
    assert out.index("no suggestion row matches") < out.index("controls (")


# --- F4: --follow landing ------------------------------------------------------

def test_followed_search_block_renders_as_pick():
    pg = _page(suggest=_suggest_dict(followed="vaccum cleaner"),
               search=_search_block(followed=True))
    out = _render(pg, compact=True)
    assert 'after picking the "vaccum cleaner" suggestion for "Vaccu"' in out
    assert "after searching" not in out
    assert '--follow picked "vaccum cleaner"' in out
    # the submit-flow hint must not appear on a pick landing
    assert "User searches for" not in out


def test_skeleton_never_authors_submit_after_follow():
    pg = _page(suggest=_suggest_dict(followed="vaccum cleaner"),
               search=_search_block(followed=True))
    steps = probe_mod._skeleton_steps(pg)
    assert not any("searches for" in s for s in steps)
    assert 'When User selects the "vaccum cleaner" suggestion for "Vaccu"' \
        in steps
    assert "Then the number in 'results summary' should be at least 1" \
        in [s for s in steps if "results summary" in s][0]


def test_skeleton_keeps_submit_for_plain_search():
    pg = _page(search=_search_block())
    steps = probe_mod._skeleton_steps(pg)
    assert 'When User searches for "vaccum cleaner"' in steps


# --- compact payload passthrough ----------------------------------------------

def test_compact_payload_carries_expect_and_followed():
    pg = _page(expect=[{"text": "PowerLifter", "found": True, "context": "c"}],
               suggest=_suggest_dict(followed="vaccum cleaner"),
               search=_search_block(followed=True))
    payload = probe_mod.compact_payload({"pages": [pg], "errors": []})
    cp = payload["pages"][0]
    assert cp["expect"][0]["found"] is True
    assert cp["suggest"]["followed"] == "vaccum cleaner"
    assert cp["search"]["followed_from"] == "Vaccu"
