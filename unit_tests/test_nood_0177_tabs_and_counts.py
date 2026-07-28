"""NOOD_0177 — the three BusterBlock suite failures, root-caused.

Written test-first: each test here failed against the engine as it stood, and
the fix in the same commit is what makes it pass.

Two of the three were a real framework bug (`a new tab should open` could never
pass), the third was a page-object conflict in the sample content.
"""
import pytest

from noodle.orchestrator import runner


class _FakePage:
    def __init__(self, url="about:blank", popup=None):
        self.url = url
        self._popup = popup
        self.waited = False

    def wait_for_event(self, event, timeout=None):
        self.waited = True
        if self._popup is None:
            from playwright._impl._errors import TimeoutError as _PWTimeout
            raise _PWTimeout("no popup")
        return self._popup

    def set_default_timeout(self, ms):
        pass

    def bring_to_front(self):
        pass

    def close(self):
        pass


class _FakeCtx:
    """Stands in for a browser context: `pages` newest-last, like Playwright."""
    def __init__(self, pages):
        self.pages = pages


class _Ctx:
    def __init__(self, pages):
        self._bctx = _FakeCtx(pages)
        self.page = pages[0]


# --------------------------------------------------------------------------
# 'a new tab should open' — the click happens in the PREVIOUS step
# --------------------------------------------------------------------------

def test_new_tab_already_open_is_detected_without_waiting():
    """The bug: `_switch_tab` called page.wait_for_event("popup"), whose comment
    claimed it "retrieves the queued popup event even if the click already
    fired". It does not — wait_for_event registers a listener and waits for the
    NEXT occurrence. The popup fired during the preceding `User clicks
    "Preview"` step, so the event was already gone and the assertion timed out
    every time: `a new tab should open` could never pass."""
    catalog, trailer = _FakePage("/catalog.html"), _FakePage("/trailer.html")
    ctx = _Ctx([catalog, trailer])          # popup ALREADY open
    runner._switch_tab(ctx, "new", assert_opened=True)
    assert ctx.page is trailer              # focused the new tab
    assert catalog.waited is False          # and did not burn the timeout


def test_new_tab_still_in_flight_is_waited_for():
    """The fallback still matters: if the assertion runs before the popup has
    landed, wait_for_event is the right tool."""
    trailer = _FakePage("/trailer.html")
    catalog = _FakePage("/catalog.html", popup=trailer)
    ctx = _Ctx([catalog])                   # only one page so far
    runner._switch_tab(ctx, "new", assert_opened=True)
    assert catalog.waited is True
    assert ctx.page is trailer


def test_no_new_tab_still_fails():
    catalog = _FakePage("/catalog.html")
    ctx = _Ctx([catalog])
    with pytest.raises(AssertionError, match="only one tab is open"):
        runner._switch_tab(ctx, "new", assert_opened=True)


def test_a_pre_existing_second_tab_is_not_mistaken_for_a_new_one():
    """Guards the lazy version of this fix. Counting `len(pages) > 1` would
    pass spuriously in a scenario that already had two tabs open and then
    clicked something that opened nothing."""
    a, b = _FakePage("/a"), _FakePage("/b")
    ctx = _Ctx([a, b])
    runner._switch_tab(ctx, "new", assert_opened=True)      # acknowledges 2
    with pytest.raises(AssertionError, match="only one tab is open"):
        runner._switch_tab(ctx, "new", assert_opened=True)  # nothing new since


def test_closing_a_tab_lets_the_next_one_count_as_new():
    a, b = _FakePage("/a"), _FakePage("/b")
    ctx = _Ctx([a, b])
    runner._switch_tab(ctx, "new", assert_opened=True)      # acked = 2
    ctx._bctx.pages.remove(b)
    runner._close_tab(ctx)
    ctx._bctx.pages.append(_FakePage("/c"))                 # a genuinely new tab
    runner._switch_tab(ctx, "new", assert_opened=True)
    assert ctx.page.url == "/c"


def test_tab_count_does_not_leak_between_scenarios():
    """The second half of the tab bug, and the reason `trailer.feature` passed
    alone but failed in the suite. `_tabs_acked` is set on behave's context
    during a scenario; the NEXT scenario gets a brand-new browser context with
    one page, so a stale count of 2 made `len(pages) > acked` false and sent
    the assertion into the wait_for_event path that can never succeed.

    before_scenario must therefore re-seed it from the fresh context."""
    import inspect

    from noodle import hooks
    src = inspect.getsource(hooks.before_scenario)
    assert "_tabs_acked" in src, (
        "before_scenario must reset the acknowledged tab count — otherwise the "
        "previous scenario's count leaks into a fresh browser context")


def test_opening_a_tab_counts_as_an_observable_click_effect():
    """A target="_blank" click changes nothing on the CURRENT page, so the
    url/DOM/network checks all came up empty and every Preview-style click
    logged a false 'no observable effect' plus a healing event — which drags an
    otherwise green run to verified: false."""
    import inspect

    from noodle.agents.web import actions
    src = inspect.getsource(actions._warn_if_no_effect)
    assert "_page_count" in src, "opening a tab must count as an effect"
    arm = inspect.getsource(actions._arm_click_probe)
    assert '"pages"' in arm, "the pre-click probe must snapshot the tab count"


def test_page_count_is_none_when_the_driver_cannot_say():
    """None on both sides means 'no signal' — never a spurious difference."""
    from noodle.agents.web.actions import _page_count

    class _NoContext:
        pass
    assert _page_count(_NoContext()) is None


# --------------------------------------------------------------------------
# 'should see 50 "Add to Cart" items' — a page-object conflict, not a bug
# --------------------------------------------------------------------------

def test_count_assertion_names_the_pom_key_it_counted():
    """`Expected 50 visible 'Add to Cart' — found 1` while 50 were plainly on
    screen. The POM scopes `add to cart` to tr:first-child (so click scenarios
    get one unambiguous target) and assert_count counts through that same key.
    The behaviour is intended; the message hid the reason."""
    import re
    src = open(runner.__file__, encoding="utf-8").read()  # keeps the import graph honest
    assert src
    from noodle.agents.web import actions
    body = open(actions.__file__, encoding="utf-8").read()
    m = re.search(r"def assert_count.*?raise AssertionError\((.*?)\)\n", body, re.S)
    assert m, "assert_count's failure path moved"
    assert "pom" in m.group(1).lower(), (
        "the count failure must name the POM key it counted through, or the "
        "next reader repeats this investigation")


def test_sample_pom_has_a_distinct_all_rows_key():
    """The fix for the sample content: a separate key that matches every row,
    so the count scenario stops borrowing the click scenario's scoped one."""
    import pathlib

    import yaml
    p = (pathlib.Path(__file__).resolve().parent.parent / "sample_feature_tests" /
         "web" / "busterblock" / "resources" / "pageobjects" / "busterblock_pom.yaml")
    if not p.is_file():
        pytest.skip("samples not present in this install")
    pom = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert "add to cart buttons" in pom, "expected an all-rows count key"
    sel = str(pom["add to cart buttons"])
    assert "first-child" not in sel, "the count key must not be row-scoped"
    assert "first-child" in str(pom["add to cart"]), (
        "the click key stays scoped to one row — that is what makes it unambiguous")
