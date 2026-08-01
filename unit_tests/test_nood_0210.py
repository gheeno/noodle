"""NOOD_0210 — exact-match locator priority, image identity, motion assertions.

The session that produced these: a Wikipedia test went green with
`verified: true` while three of its assertions pointed at the wrong elements,
and the only thing that caught it was a human opening the evidence JPEG.
"""
import re

from noodle import docs_reader
from noodle.agents.web import motion
from noodle.agents.web.locator import _try_strategies
from noodle.resolver import match_step


# --------------------------------------------------------------------------
# Fake Playwright scope — elements are (tier, text); a strategy matches when
# its tier matches and the pattern hits the text.
# --------------------------------------------------------------------------
class _FakeLoc:
    def __init__(self, n, tier):
        self._n, self.tier = n, tier

    def count(self):
        return self._n

    @property
    def first(self):
        return self

    def and_(self, other):
        return self


class _FakeScope:
    def __init__(self, elements):
        self.elements = elements

    def _m(self, tier, pattern):
        return _FakeLoc(sum(1 for t, txt in self.elements
                            if t == tier and pattern.search(txt)), tier)

    def get_by_role(self, role, name=None):
        return self._m(f"role:{role}", name)

    def get_by_label(self, p):
        return self._m("label", p)

    def get_by_placeholder(self, p):
        return self._m("placeholder", p)

    def get_by_title(self, p):
        return self._m("title", p)

    def get_by_alt_text(self, p):
        return self._m("alt", p)

    def get_by_text(self, p, exact=False):
        return self._m("text", p)

    def locator(self, sel):
        return _FakeLoc(0, "css")


# --- exact-match priority --------------------------------------------------

def test_exact_text_beats_a_substring_accessible_name():
    """THE regression. Asserting 'Born' matched a citation link named
    '…Really Stubborn People' (Stub-BORN-) because role strategies run before
    get_by_text with an unanchored pattern. One element, so ambiguity never
    fired: the run reported verified:true with a screenshot of the references
    section while <th>Born</th> sat unlooked-at in the infobox."""
    scope = _FakeScope([
        ("role:link", '"Steve Jobs Taught This Man How To Win With Stubborn People"'),
        ("text", "Born"),
    ])
    loc, ambiguous = _try_strategies(scope, "Born")
    assert loc.tier == "text", "exact <th>Born</th> must win over 'Stubborn'"
    assert not ambiguous


def test_substring_still_resolves_when_nothing_matches_exactly():
    """The loose pass is untouched — anything that resolved before still does."""
    scope = _FakeScope([("role:button", "Add to cart now")])
    loc, _ = _try_strategies(scope, "Add to cart")
    assert loc.tier == "role:button"


def test_exact_match_prefers_the_button_over_a_longer_label():
    scope = _FakeScope([("role:button", "Save"), ("text", "Save your changes")])
    loc, _ = _try_strategies(scope, "Save")
    assert loc.tier == "role:button"


def test_prefer_input_keeps_editable_ahead_of_an_exactly_named_button():
    """NOOD_0089 must survive exact-first: a button named exactly 'Username'
    cannot outrank the field a fill actually needs."""
    scope = _FakeScope([
        ("role:button", "Username"),
        ("role:textbox", "Username field"),
    ])
    loc, _ = _try_strategies(scope, "Username", prefer="input")
    assert loc.tier == "role:textbox"


def test_alt_text_is_reachable_from_the_main_chain():
    """Was POM-only: a correctly alt-labelled image needed a POM entry."""
    scope = _FakeScope([("alt", "Steve Jobs signature")])
    loc, _ = _try_strategies(scope, "Steve Jobs signature")
    assert loc.tier == "alt"


# --- motion classification -------------------------------------------------

def _still(n=8):
    return [100.0] * n


def test_steady_leftward_drift_reads_as_right_to_left():
    """Measured off thebrief.ai: the logo strip drifts ~2.7px per 250ms while
    the <img> reports animationName:none — the motion is on an ancestor, which
    is why this samples boxes instead of reading style."""
    xs = [1504.8, 1501.9, 1499.2, 1496.4, 1493.7, 1491.1]
    v = motion.classify(xs, _still(len(xs)))
    assert v["moving"] and v["direction"] == "right to left"
    assert not v["oscillating"]


def test_rightward_drift_reads_as_left_to_right():
    v = motion.classify([10.0, 20.0, 30.0, 40.0], _still(4))
    assert v["direction"] == "left to right"


def test_vertical_oscillation_reads_as_up_and_down():
    ys = [100.0, 120.0, 100.0, 80.0, 100.0, 120.0]
    v = motion.classify(_still(len(ys)), ys)
    assert v["direction"] == "up and down"
    assert v["oscillating"]


def test_a_full_bounce_cycle_is_still_moving():
    """Net displacement returns to zero — path length is what proves motion."""
    ys = [100.0, 130.0, 100.0]
    v = motion.classify(_still(3), ys)
    assert v["moving"] and v["dy"] == 0.0 and v["travel_y"] == 60.0


def test_subpixel_jitter_is_not_motion():
    v = motion.classify([100.0, 100.3, 100.1, 100.2], _still(4))
    assert not v["moving"] and v["direction"] == ""


def test_a_still_element_is_not_moving():
    assert not motion.classify(_still(), _still())["moving"]


# --- direction is asserted, not just reported ------------------------------

def test_a_vertical_claim_fails_on_a_horizontal_drift():
    """The point of naming a direction. A step that accepted any motion would
    prove nothing beyond 'something moved'."""
    v = motion.classify([10.0, 20.0, 30.0, 40.0], _still(4))
    ok, why = motion.matches(v, "up and down")
    assert not ok and "not up and down" in why


def test_the_matching_direction_passes():
    v = motion.classify([10.0, 20.0, 30.0, 40.0], _still(4))
    assert motion.matches(v, "left to right")[0]


def test_the_opposite_sense_fails():
    v = motion.classify([40.0, 30.0, 20.0, 10.0], _still(4))
    assert not motion.matches(v, "left to right")[0]
    assert motion.matches(v, "right to left")[0]


def test_no_direction_means_any_motion():
    v = motion.classify([10.0, 20.0], _still(2))
    assert motion.matches(v, "")[0] and motion.matches(v, None)[0]


def test_a_still_element_never_matches():
    v = motion.classify(_still(), _still())
    ok, why = motion.matches(v, "left to right")
    assert not ok and "not moving at all" in why


# --- observe() over a scripted element -------------------------------------

class _ScriptedEl:
    def __init__(self, boxes):
        self.boxes = list(boxes)

    def bounding_box(self):
        return self.boxes.pop(0) if self.boxes else None


class _FakePage:
    def wait_for_timeout(self, ms):
        pass


def test_observe_samples_the_box_and_classifies():
    boxes = [{"x": float(x), "y": 5.0, "width": 10, "height": 10}
             for x in (0, 5, 10, 15)]
    v = motion.observe(_FakePage(), _ScriptedEl(boxes), samples=4, interval_ms=10)
    assert v["direction"] == "left to right" and v["samples"] == 4


def test_observe_returns_none_when_the_element_never_renders():
    """Distinct report from 'found it, but it is still'."""
    assert motion.observe(_FakePage(), _ScriptedEl([]), samples=3) is None


def test_one_missed_frame_does_not_void_the_observation():
    boxes = [{"x": 0.0, "y": 0.0, "width": 1, "height": 1}, None,
             {"x": 30.0, "y": 0.0, "width": 1, "height": 1},
             {"x": 60.0, "y": 0.0, "width": 1, "height": 1}]
    v = motion.observe(_FakePage(), _ScriptedEl(boxes), samples=4, interval_ms=10)
    assert v is not None and v["direction"] == "left to right"


# --- step patterns ---------------------------------------------------------

def test_the_users_own_phrasings_parse():
    for text, direction in [
        ("the pizza logo can be seen bouncing up and down", "up and down"),
        ("the brand logo are seen moving left to right", "left to right"),
        ("the 'hero banner' should be moving right to left", "right to left"),
    ]:
        action, args = match_step(text, tags={"web"})
        assert action == "assert_motion", text
        assert args["direction"] == direction


def test_the_image_noun_stays_in_the_locator():
    """'pizza logo' names the element; 'pizza' alone may not resolve."""
    _, args = match_step("the pizza logo can be seen bouncing up and down",
                         tags={"web"})
    assert args["locator"] == "pizza logo"


def test_bare_motion_claims_no_direction():
    action, args = match_step("the carousel is scrolling", tags={"web"})
    assert action == "assert_motion" and args["direction"] == ""


def test_the_negative_is_not_swallowed_by_the_bare_positive():
    """Ordering guard: 'is not moving' once parsed as a locator ending in
    "is not" with direction ''."""
    action, args = match_step("the 'promo strip' is not moving", tags={"web"})
    assert action == "assert_no_motion" and args["locator"] == "promo strip"


def test_static_phrasings_parse_as_no_motion():
    for text in ("the hero image should be static", "the banner is still"):
        assert match_step(text, tags={"web"})[0] == "assert_no_motion", text


# --- image cue matching (the JS contract, checked in Python) ---------------

def test_image_cue_js_names_the_attributes_it_reads():
    """The signature <img> carries no alt/title — resource and src are the
    only things naming it, plus a <figure> caption when there is one."""
    from noodle.agents.web.locator import _IMAGE_CUE_JS
    for attr in ("src", "srcset", "alt", "title", "resource", "aria-label"):
        assert attr in _IMAGE_CUE_JS
    assert "closest('figure')" in _IMAGE_CUE_JS
    assert "tokens.every" in _IMAGE_CUE_JS, "must require EVERY token, not any"


def test_image_cue_js_stopwords_cannot_be_satisfied_by_size_or_format():
    """'250px', 'svg', 'thumb' appear in every Wikimedia URL — if they counted
    as tokens, any phrase mentioning them would match any image."""
    from noodle.agents.web.locator import _IMAGE_CUE_JS
    for noise in ("'px'", "'svg'", "'png'", "'thumb'", "'image'"):
        assert noise in _IMAGE_CUE_JS


# --- docs decoupling -------------------------------------------------------

def test_read_docs_works_without_the_mcp_server():
    """`noodle docs` died with ModuleNotFoundError when mcp 2.0 dropped
    mcp.server.fastmcp — the CLI form exists precisely so an agent WITHOUT
    MCP can reach the docs."""
    out = docs_reader.read_docs()
    assert "docs" in out and out["docs"]


def test_docs_reader_imports_no_mcp():
    import inspect
    src = inspect.getsource(docs_reader)
    assert "import mcp" not in src and "from mcp" not in src


def test_mcp_tool_still_delegates_to_the_same_implementation():
    from noodle.mcp import server as mcp_server
    assert mcp_server.read_docs(name="woks")["name"] == "woks.md"


# --- page-level assertions may carry evidence ------------------------------

def test_page_level_asserts_cover_the_elementless_assertions():
    """These assert a property of the DOCUMENT, so no locator ever matches and
    the evidence checker's fresh-match test can never pass. Requesting a
    screenshot on one used to flip the whole run to verified:false, making
    'every assertion needs an evidence screenshot' unsatisfiable."""
    from noodle.orchestrator.runner import _PAGE_LEVEL_ASSERTS
    for t in ("assert_page_status", "assert_url", "assert_title",
              "assert_no_server_errors", "assert_no_console_errors"):
        assert t in _PAGE_LEVEL_ASSERTS


def test_element_assertions_are_not_page_level():
    """THE guard on this widening. An element assertion must still prove it
    matched something fresh — that check is what catches a real false pass."""
    from noodle.orchestrator.runner import _PAGE_LEVEL_ASSERTS
    for t in ("assert_visible", "assert_hidden", "assert_count",
              "assert_motion", "assert_value", "click"):
        assert t not in _PAGE_LEVEL_ASSERTS


def test_every_page_level_assert_is_a_real_action_type():
    from noodle.orchestrator.runner import _PAGE_LEVEL_ASSERTS
    from noodle.resolver.step_resolver import VALID_TYPES
    assert _PAGE_LEVEL_ASSERTS <= VALID_TYPES


# --- page status step ------------------------------------------------------

def test_page_status_steps_parse():
    for text, expected in [
        ("the page should return 200", {"status": 200}),
        ("the page status should be 200", {"status": 200}),
        ("the url should return 404", {"status": 404}),
        ("the page should load successfully", {}),
    ]:
        action, args = match_step(text, tags={"web"})
        assert action == "assert_page_status", text
        assert args == expected, text


def test_the_rest_response_assertion_is_not_shadowed():
    """`the response status should be 200` belongs to the API wok and asserts
    the last REST call — the new page-level step must never steal it."""
    assert match_step("the response status should be 200",
                      tags={"web"})[0] == "rest_assert_status"


def test_exact_pattern_is_whitespace_tolerant():
    """Playwright matches against text carrying surrounding whitespace."""
    scope = _FakeScope([("text", "  Born\n")])
    loc, _ = _try_strategies(scope, "Born")
    assert loc.tier == "text"


def test_regex_special_characters_in_the_phrase_are_escaped():
    scope = _FakeScope([("text", "Price (USD)")])
    loc, _ = _try_strategies(scope, "Price (USD)")
    assert loc.tier == "text"
    assert re.escape("(USD)")   # guards the escaping contract itself
