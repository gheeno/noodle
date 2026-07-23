"""NOOD_0115 — assertion/locator + result-count gap fixes.

The retail SPA flyer+search session: promo-tile captions that exist ONLY as
alt/aria-label attributes were invisible to every text-node-based assertion,
assert_count could not express a structural "N product cards" check, no step
parsed a number out of '93 results', and call_function shlex-split captured
page text into multiple positional args. Browser-free — pages are mocked,
matching test_assert_count.py / test_nood_0113.py precedent.
"""
from unittest.mock import MagicMock

import pytest

from noodle import healing
from noodle.agents.web import actions, inspect_locator, probe
from noodle.orchestrator import script_runner
from noodle.resolver.step_resolver import resolve

# ---------------------------------------------------------------------------
# #1 — assert_visible / assert_hidden resolve accessible names via find()
# ---------------------------------------------------------------------------

def _visible_loc(visible=True):
    loc = MagicMock()
    loc.is_visible.return_value = visible
    return loc


def test_assert_visible_passes_on_alt_only_caption(monkeypatch):
    """No text node anywhere — get_by_text can never match — but find()
    resolves the tile by its accessible name (img alt)."""
    monkeypatch.setattr(actions, "find", lambda page, text, **kw: _visible_loc())
    page = MagicMock()
    page.get_by_text.return_value.first.wait_for.side_effect = RuntimeError("no text node")

    actions.assert_visible(page, "Weekly Flyer")   # no raise


def test_assert_visible_probe_uses_cheap_pass(monkeypatch):
    calls = {}

    def fake_find(page, text, **kw):
        calls["kw"] = kw
        return _visible_loc()

    monkeypatch.setattr(actions, "find", fake_find)
    actions.assert_visible(MagicMock(), "Weekly Flyer")
    # NOOD_0157 — literal assertions also disable the DOM-attribute scan, and
    # accept any visible match (duplicates prove an existence check).
    assert calls["kw"] == {"poll": False, "heal": False,
                           "allow_dom_scan": False, "any_match": True}


def test_assert_visible_still_fails_when_nothing_resolves(monkeypatch):
    monkeypatch.setattr(actions, "find", lambda page, text, **kw: None)
    page = MagicMock()
    page.url = "http://x"
    page.get_by_text.return_value.first.wait_for.side_effect = RuntimeError("absent")

    with pytest.raises(AssertionError, match="Expected to see 'Ghost'"):
        actions.assert_visible(page, "Ghost")


def test_assert_visible_hidden_resolution_does_not_pass(monkeypatch):
    monkeypatch.setattr(actions, "find",
                        lambda page, text, **kw: _visible_loc(visible=False))
    page = MagicMock()
    page.url = "http://x"
    page.get_by_text.return_value.first.wait_for.side_effect = RuntimeError("absent")

    with pytest.raises(AssertionError):
        actions.assert_visible(page, "Ghost")


def test_assert_hidden_catches_visible_alt_only_caption(monkeypatch):
    """'should not see X' must not vacuously pass just because the caption
    has no text node — the tile is right there, named by its alt text."""
    monkeypatch.setattr(actions, "find", lambda page, text, **kw: _visible_loc())
    page = MagicMock()
    page.url = "http://x"
    page.get_by_text.return_value.count.return_value = 0

    with pytest.raises(AssertionError, match="accessible name"):
        actions.assert_hidden(page, "Weekly Flyer")


def test_assert_hidden_passes_when_nothing_resolves(monkeypatch):
    monkeypatch.setattr(actions, "find", lambda page, text, **kw: None)
    page = MagicMock()
    page.get_by_text.return_value.count.return_value = 0

    actions.assert_hidden(page, "Gone")   # no raise


# ---------------------------------------------------------------------------
# #3 — assert_count resolves POM entries for structural counts
# ---------------------------------------------------------------------------

def test_assert_count_uses_pom_entry_for_structural_count(monkeypatch):
    cards = MagicMock()
    cards.locator.return_value.count.return_value = 93
    monkeypatch.setattr(actions.pom, "locate_all", lambda page, text: cards)
    page = MagicMock()

    actions.assert_count(page, 90, "{pom:products}", ">=")

    cards.locator.assert_called_once_with("visible=true")   # visible-only, like text path
    page.get_by_text.assert_not_called()


def test_assert_count_explicit_pom_miss_fails_loudly(monkeypatch):
    monkeypatch.setattr(actions.pom, "locate_all", lambda page, text: None)
    page = MagicMock()
    page.url = "http://x"

    with pytest.raises(AssertionError, match="No POM entry for explicit"):
        actions.assert_count(page, 90, "{pom:products}", ">=")


def test_assert_count_without_pom_stays_substring_counter(monkeypatch):
    monkeypatch.setattr(actions.pom, "locate_all", lambda page, text: None)
    page = MagicMock()
    page.get_by_text.return_value.locator.return_value.count.return_value = 2
    page.url = "http://x"

    with pytest.raises(AssertionError, match="Expected at least 3 visible 'product'"):
        actions.assert_count(page, 3, "product", ">=")
    page.get_by_text.assert_called_once_with("product", exact=False)


# ---------------------------------------------------------------------------
# #4 — read_number / assert_number
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("93 results", 93.0),
    ("Showing 1,234 items", 1234.0),
    ("4.5 stars", 4.5),
    ("Total: $1,299.99", 1299.99),
])
def test_read_number_parses_first_number(monkeypatch, text, expected):
    monkeypatch.setattr(actions, "get_text", lambda page, locator: text)
    assert actions.read_number(MagicMock(), "results summary") == expected


def test_read_number_no_number_fails_with_text(monkeypatch):
    monkeypatch.setattr(actions, "get_text", lambda page, locator: "No results found")
    page = MagicMock()
    page.url = "http://x"
    with pytest.raises(AssertionError, match="No number found .* 'No results found'"):
        actions.read_number(page, "results summary")


def test_assert_number_at_least_passes_and_fails(monkeypatch):
    monkeypatch.setattr(actions, "get_text", lambda page, locator: "93 results")
    page = MagicMock()
    page.url = "http://x"

    actions.assert_number(page, "results summary", 90, ">=")   # 93 >= 90

    with pytest.raises(AssertionError, match="at least 100 — found 93"):
        actions.assert_number(page, "results summary", 100, ">=")


def test_resolver_matches_number_phrasings():
    got = resolve("the number in 'results summary' should be at least 90")
    assert got["type"] == "assert_number"
    assert got["locator"] == "results summary"
    assert got["count"] == 90.0 and got["op"] == ">="

    assert resolve("the number in 'cart badge' should be at most 10")["op"] == "<="
    assert resolve("the number in 'stock' should be more than 0")["op"] == ">"
    assert resolve("the number in 'errors' should be less than 5")["op"] == "<"
    got = resolve("the number in 'review count' should be exactly 12")
    assert got["op"] == "==" and got["count"] == 12.0


# ---------------------------------------------------------------------------
# #5 — call_function raw arg + shlex-splitting hint
# ---------------------------------------------------------------------------

@pytest.fixture()
def helpers_py(tmp_path):
    f = tmp_path / "helpers.py"
    f.write_text(
        "def parse_int(text):\n"
        "    import re\n"
        "    return int(re.search(r'\\d+', text).group(0))\n"
    )
    return f


def test_call_function_raw_passes_whole_string(helpers_py):
    assert script_runner.call_function(f"{helpers_py}:parse_int",
                                       "93 results", raw=True) == 93


def test_call_function_split_arity_error_hints_at_shlex(helpers_py):
    with pytest.raises(AssertionError) as e:
        script_runner.call_function(f"{helpers_py}:parse_int", "93 results")
    msg = str(e.value)
    assert "shlex-split" in msg and "raw arg" in msg and "2 tokens" in msg


def test_call_function_unrelated_error_has_no_hint(helpers_py):
    with pytest.raises(AssertionError) as e:
        script_runner.call_function(f"{helpers_py}:parse_int", "nodigits")
    assert "shlex-split" not in str(e.value)


# ---------------------------------------------------------------------------
# #2 — probe flags attribute-only captions and pre-emits POM entries
# ---------------------------------------------------------------------------

def _tile(alt="Weekly Flyer", aria="", title="", text=""):
    return {"tag": "a", "id": "", "role": "", "type": "", "name": "",
            "testid": "", "aria": aria, "title": title, "ph": "", "cls": "",
            "href": "/flyer", "text": text, "label": "", "alt": alt,
            "visible": True}


def test_probe_names_alt_only_tile_by_its_caption():
    result = probe.summarize({"controls": [_tile()]}, url="http://x/en.html")
    (c,) = result["controls"]
    assert c["name"] == "weekly flyer"
    assert c["caption_attr_only"] is True


def test_probe_emits_alt_text_pom_entry_unconditionally():
    result = probe.summarize({"controls": [_tile()]}, url="http://x/en.html")
    assert 'weekly flyer:' in result["pom_yaml"]
    # NOOD_0117 — single-quoted YAML scalar (valid for any selector/caption)
    assert "alt_text: 'Weekly Flyer'" in result["pom_yaml"]


def test_probe_aria_only_control_flagged_with_css_entry():
    result = probe.summarize(
        {"controls": [_tile(alt="", aria="Shop Tires")]}, url="http://x")
    (c,) = result["controls"]
    assert c["caption_attr_only"] is True
    # NOOD_0117 — the selector carries double quotes ([aria-label="…"]); the
    # POM value is single-quoted so it stays valid YAML (the old
    # `css: "[aria-label="Shop Tires"]"` form did not parse).
    import yaml
    assert yaml.safe_load(result["pom_yaml"])[c["name"]]["css"] == c["selector"]


def test_probe_text_control_not_flagged():
    result = probe.summarize(
        {"controls": [_tile(alt="", text="Weekly Flyer")]}, url="http://x")
    (c,) = result["controls"]
    assert "caption_attr_only" not in c


def test_probe_render_warns_on_attr_only_caption():
    result = probe.summarize({"controls": [_tile()]}, url="http://x")
    out = probe.render({"pages": [result], "errors": []})
    assert "caption is attribute-only" in out


# ---------------------------------------------------------------------------
# #6 — inspect_locator: candidates / resolve_on / render (no browser)
# ---------------------------------------------------------------------------

def _match_loc(n, tag="img", text="", visible=True):
    loc = MagicMock()
    loc.count.return_value = n
    h = loc.nth.return_value
    h.evaluate.return_value = tag
    h.inner_text.return_value = text
    h.is_visible.return_value = visible
    loc.first = loc
    return loc


def test_candidates_labels_alt_only_source(monkeypatch):
    monkeypatch.setattr(inspect_locator.pom, "locate_all", lambda page, text: None)
    monkeypatch.setattr(inspect_locator.dom_scan, "best_selector", lambda p, t: None)
    page = MagicMock()
    zero = _match_loc(0)
    for m in ("get_by_role", "get_by_label", "get_by_placeholder",
              "get_by_title", "get_by_text"):
        getattr(page, m).return_value = zero
    page.get_by_alt_text.return_value = _match_loc(1, tag="img", visible=True)

    out = inspect_locator.candidates(page, "Weekly Flyer")

    assert [c["source"] for c in out] == ["image alt text"]
    assert out[0]["count"] == 1
    assert out[0]["matches"][0]["visible"] is True


def test_candidates_reports_pom_source(monkeypatch):
    monkeypatch.setattr(inspect_locator.pom, "locate_all",
                        lambda page, text: _match_loc(2, tag="li"))
    monkeypatch.setattr(inspect_locator.dom_scan, "best_selector", lambda p, t: None)
    page = MagicMock()
    zero = _match_loc(0)
    for m in ("get_by_role", "get_by_label", "get_by_placeholder",
              "get_by_title", "get_by_text", "get_by_alt_text"):
        getattr(page, m).return_value = zero

    out = inspect_locator.candidates(page, "{pom:products}")

    assert out[0]["source"] == "pom.yaml (explicit {pom:products})"
    assert out[0]["count"] == 2


def test_resolve_on_reports_pick_and_heal_tier(monkeypatch):
    from noodle.agents.web import locator as loc_mod
    healing.reset()
    picked = _match_loc(1, tag="a", text="Weekly Flyer", visible=True)

    def fake_find(page, text, **kw):
        healing.record(text, "dom-scan", "a[class~='tile']")
        return picked

    monkeypatch.setattr(loc_mod, "find", fake_find)
    try:
        r = inspect_locator.resolve_on(MagicMock(), "Weekly Flyer")
    finally:
        healing.reset()

    assert r["tag"] == "a" and r["visible"] is True
    assert r["healed"] == ["dom-scan (a[class~='tile'])"]


def test_resolve_on_none_when_unresolvable(monkeypatch):
    from noodle.agents.web import locator as loc_mod
    monkeypatch.setattr(loc_mod, "find", lambda page, text, **kw: None)
    assert inspect_locator.resolve_on(MagicMock(), "Ghost") is None


def test_inspect_render_readable():
    result = {
        "url": "http://x", "text": "Weekly Flyer", "error": None,
        "candidates": [{"source": "image alt text", "count": 1,
                        "matches": [{"tag": "img", "text": "", "visible": True}]}],
        "resolved": {"tag": "a", "text": "Weekly Flyer", "visible": True,
                     "healed": []},
        "screenshot": None,
    }
    out = inspect_locator.render(result)
    assert "[image alt text] 1 match(es)" in out
    assert "find() resolves: <a>" in out


def test_inspect_render_unresolvable_says_step_will_fail():
    result = {"url": "http://x", "text": "Ghost", "error": None,
              "candidates": [], "resolved": None, "screenshot": None}
    out = inspect_locator.render(result)
    assert "NOTHING" in out and "no source matches" in out
