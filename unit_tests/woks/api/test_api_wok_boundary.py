"""NOOD_0191 — the API wok's isolation boundary.

API was listed as a corner of the web wok on the grounds that REST "shares
the web session lifecycle". It never did: hooks.before_scenario returns
early for @api with no browser, no context and no tracing. The mislabelling
cost a user a capability — an agent asked for API tests told them Noodle was
a web UI framework and to use Postman instead.

These guard the property that makes API its own wok: it needs no browser.
"""
from noodle import wok


def test_api_is_its_own_wok_not_a_corner_of_web():
    assert "api" in wok.WOKS
    assert wok.wok_for_tags(["api"]).name == "api"
    assert "api" not in wok.WOKS["web"].tags


def test_api_beats_web_tags_because_it_means_no_browser():
    # A scenario carrying both is browserless — hooks checks 'api' first.
    assert wok.wok_for_tags(["web", "api"]).name == "api"
    assert wok.wok_for_tags(["api", "smoke"]).name == "api"


def test_api_needs_no_optional_dependency():
    w = wok.WOKS["api"]
    assert w.extras == ()
    assert wok.installed(w)


def test_api_scenarios_never_open_a_browser():
    """The runtime contract, read off hooks.py itself rather than restated:
    the @api branch nulls the page and returns before any browser launch."""
    import inspect

    from noodle import hooks
    src = inspect.getsource(hooks.before_scenario)
    head = src.split("if 'api' in tags:")[1].split("return")[0]
    assert "context.page = None" in head


def test_rest_steps_carry_no_playwright_import():
    """A browserless suite must be installable without browsers."""
    import inspect

    from noodle.agents.web import rest_client
    assert "playwright" not in inspect.getsource(rest_client).lower()


def test_rest_and_web_steps_compose_in_one_scenario():
    """NOOD_0191 — "generate data via API, then verify it in the UI" is the
    most common real mix, and it works today: verified live when this landed,
    a @web scenario doing GET -> extract -> navigate -> assert went green.

    What makes it work is that REST needs no page at all, so it is legal both
    in a browserless @api scenario AND in a @web one. The runner grants that
    by ACTION-TYPE PREFIX, not by an allow-list entry — so the contract to
    guard is the prefix rule plus the naming convention it rests on."""
    import inspect

    from noodle.orchestrator import runner

    src = inspect.getsource(runner)
    assert "startswith('rest_')" in src, \
        "the rest_ browserless bypass is gone — REST would now demand a page"

    # ...and every REST action type still carries the prefix that bypass keys
    # on. A step renamed to `api_call` would silently start needing a browser.
    from noodle.resolver import step_resolver
    known = getattr(step_resolver, "VALID_TYPES", None) or \
        getattr(step_resolver, "_VALID_TYPES")
    rest_types = {t for t in known if "rest" in t}
    assert rest_types, "no REST action types found — did the table move?"
    assert all(t.startswith("rest_") for t in rest_types), sorted(rest_types)
    assert {"rest_call", "rest_assert_status", "rest_extract_json"} <= rest_types


def test_rest_steps_are_never_gated_at_all():
    """The sharpest framing of this wok, and the one worth pinning: REST is
    plain I/O, like run_command or a JDBC call to seed a fixture — available
    in EVERY scenario, no tag required. The runner refuses a step only when
    `page is None`, and rest_* is exempt there too, so there is no gate on
    REST anywhere. If a future change ever makes @api a PRECONDITION for REST
    rather than an opt-out from the browser, this fails."""
    import inspect

    from noodle.orchestrator import runner
    src = inspect.getsource(runner._execute_action if
                            hasattr(runner, "_execute_action") else runner)
    # the one guard, and its shape: gated only on a missing page
    assert "page is None and not (t.startswith('rest_')" in src


def test_docs_say_the_tag_is_an_optout_not_a_gate():
    from pathlib import Path
    woks = (Path(__file__).resolve().parents[3] / "docs" / "woks.md").read_text(encoding="utf-8")
    assert "The API wok is a lifecycle, not a gate" in woks
    assert "writing API tests needs no tag" in woks
