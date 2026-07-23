"""Phase 9.1b — accessibility ambiguity detection in the locator."""
import pytest

from noodle.agents.web import locator


class _FakeLoc:
    def __init__(self, n):
        self._n = n
        self.first = f"<first of {n}>"

    def count(self):
        return self._n

    def element_handles(self):
        return []


class _FakePage:
    """get_by_role(button) is the first strategy tried — return a preset count."""
    def __init__(self, n):
        self.n = n

    def get_by_role(self, *a, **k):
        return _FakeLoc(self.n)

    # remaining strategies never reached once button matches; stub as empty
    def __getattr__(self, _name):
        return lambda *a, **k: _FakeLoc(0)


def test_unique_match_is_not_ambiguous():
    loc, ambiguous = locator._try_strategies(_FakePage(1), "login")
    assert loc.count() == 1          # full locator returned; caller takes .first
    assert ambiguous is False


def test_multiple_matches_flagged_ambiguous():
    loc, ambiguous = locator._try_strategies(_FakePage(3), "search")
    assert loc.count() == 3
    assert ambiguous is True


def test_strict_mode_raises_on_unresolved_ambiguity():
    locator.set_strict(True)
    try:
        with pytest.raises(AssertionError, match="Ambiguous locator"):
            locator._on_ambiguous(_FakePage(2), "search", _FakeLoc(2))
    finally:
        locator.set_strict(None)


def test_lenient_mode_returns_first(capsys):
    locator.set_strict(False)
    try:
        result = locator._on_ambiguous(_FakePage(2), "search", _FakeLoc(2))
        assert result is not None
        assert "lenient mode" in capsys.readouterr().out
    finally:
        locator.set_strict(None)
