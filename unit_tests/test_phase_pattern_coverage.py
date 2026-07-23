"""NOOD_0025 — pattern coverage: history, double/right click, submit,
select-in, tab/window management. Pure routing + tab helpers (no browser)."""
from noodle.orchestrator import runner
from noodle.resolver.patterns import match, normalize_subject


def _resolve(text):
    return match(normalize_subject(text))


# --- new verb routing -------------------------------------------------------

def test_browser_history():
    assert _resolve('User goes back') == ('go_back', {})
    assert _resolve('I go forward') == ('go_forward', {})
    assert _resolve('User reloads the page') == ('reload', {})
    assert _resolve('User refreshes the page') == ('reload', {})


def test_double_and_right_click():
    assert _resolve('User double-clicks "Jaws"') == ('double_click', {'locator': 'Jaws'})
    assert _resolve('User right-clicks "File"') == ('right_click', {'locator': 'File'})
    assert _resolve('I double-click the row') == ('double_click', {'locator': 'row'})


def test_submit_form():
    assert _resolve('User submits the login form') == ('submit', {'locator': 'login'})
    assert _resolve('User submits the search form') == ('submit', {'locator': 'search'})


def test_select_in_and_from_both_work():  # regression + new
    assert _resolve('User selects "Action" from the genre') == ('select', {'value': 'Action', 'locator': 'genre'})
    assert _resolve('User selects "Action" in the genre filter') == ('select', {'value': 'Action', 'locator': 'genre filter'})


# --- tab / window routing ---------------------------------------------------

def test_new_tab_assert():
    assert _resolve('a new tab should open') == ('assert_new_tab', {})
    assert _resolve('a new window should open') == ('assert_new_tab', {})


def test_switch_tab_targets():
    assert _resolve('User switches to the new tab') == ('switch_tab', {'target': 'new'})
    assert _resolve('User switches to the previous tab') == ('switch_tab', {'target': 'previous'})
    assert _resolve('User switches to the original tab') == ('switch_tab', {'target': 'original'})


def test_switch_frame_still_wins_over_tab():  # regression: quoted+frame != tab
    assert _resolve('User switches to the "checkout" frame') == ('switch_frame', {'name': 'checkout'})


def test_close_tab():
    assert _resolve('User closes the tab') == ('close_tab', {})
    assert _resolve('User closes the new tab') == ('close_tab', {})


# --- tab helpers (no browser, fake context) ---------------------------------

class _FakePage:
    def __init__(self, ctx):
        self._ctx = ctx
        self.fronted = False
    def bring_to_front(self): self.fronted = True
    def close(self): self._ctx.pages.remove(self)
    def wait_for_event(self, event, **kwargs):
        from playwright._impl._errors import TimeoutError as _PWTimeout
        raise _PWTimeout("no popup in fake context")


class _FakeBctx:
    def __init__(self): self.pages = []


class _FakeCtx:
    def __init__(self, n):
        self._bctx = _FakeBctx()
        for _ in range(n):
            self._bctx.pages.append(_FakePage(self._bctx))
        self.page = self._bctx.pages[0]


def test_switch_to_new_and_back():
    ctx = _FakeCtx(2)
    first, second = ctx._bctx.pages
    runner._switch_tab(ctx, 'new')
    assert ctx.page is second and second.fronted
    runner._switch_tab(ctx, 'previous')
    assert ctx.page is first


def test_assert_new_tab_raises_when_only_one():
    ctx = _FakeCtx(1)
    try:
        runner._switch_tab(ctx, 'new', assert_opened=True)
        assert False, "expected AssertionError"
    except AssertionError as e:
        assert "new tab" in str(e)


def test_close_tab_falls_back_to_first():
    ctx = _FakeCtx(2)
    first = ctx._bctx.pages[0]
    ctx.page = ctx._bctx.pages[1]
    runner._close_tab(ctx)
    assert ctx.page is first and len(ctx._bctx.pages) == 1


def test_in_new_tab_suffix_regex():
    import re
    pat = r'\s+in the (?:new|last) (?:tab|window)$'
    assert re.search(pat, 'User should see "PLAY" in the new tab', re.I)
    assert re.search(pat, 'User clicks "Close Tab" in the new tab', re.I)
    assert not re.search(pat, 'User clicks "Add to Cart"', re.I)
