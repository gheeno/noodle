"""NOOD_0122 — browser vs page popup interaction: composite close, permission
allow/deny aliases, and rejection of text entry into permission prompts."""
import pytest

from noodle.agents.web import actions
from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject


def _resolve(text):
    return match(normalize_phrasing(normalize_subject(text)))


# --- resolver: composite close ---------------------------------------------

def test_composite_close_denies_named_permission():
    # the exact requested phrase
    assert _resolve("close any and all popups including the geolocation prompt") == \
        ("close_popups", {"deny_permissions": ["geolocation"]})
    assert _resolve("closes all popups including the location prompt") == \
        ("close_popups", {"deny_permissions": ["location"]})
    assert _resolve("closes any popups including the notifications prompt") == \
        ("close_popups", {"deny_permissions": ["notifications"]})


def test_plain_close_stays_dom_only():
    # no permission named -> never touches browser permission state
    assert _resolve("closes all popups") == ("close_popups", {})
    assert _resolve("closes the popups") == ("close_popups", {})
    assert _resolve("closes all popups within 5 seconds") == \
        ("close_popups", {"within": 5})


# --- resolver: allow / deny aliases ----------------------------------------

@pytest.mark.parametrize("noun", ["location", "geolocation", "notifications",
                                  "camera", "microphone"])
def test_allow_prompt_grants_permission(noun):
    assert _resolve(f"accepts the {noun} prompt") == \
        ("grant_permissions", {"permissions": noun})
    assert _resolve(f"allows the {noun} prompt") == \
        ("grant_permissions", {"permissions": noun})


@pytest.mark.parametrize("noun", ["location", "geolocation", "notifications",
                                  "camera", "microphone"])
def test_deny_prompt_dismisses_permission(noun):
    assert _resolve(f"dismisses the {noun} prompt") == \
        ("dismiss_permission_prompt", {"permission": noun})
    assert _resolve(f"closes the {noun} prompt") == \
        ("dismiss_permission_prompt", {"permission": noun})


def test_bare_prompt_verbs_still_route_to_js_dialog():
    # regressions: "the prompt" alone is a JS dialog, not a permission bubble
    assert _resolve("accepts the prompt") == \
        ("arm_dialog", {"response": "accept", "answer": None})
    assert _resolve("dismisses the prompt") == \
        ("arm_dialog", {"response": "dismiss", "answer": None})


# --- resolver: reject text entry into a permission prompt -------------------

def test_type_into_permission_prompt_rejected():
    with pytest.raises(AssertionError, match="no text"):
        _resolve("types 'Toronto' into the location prompt")
    with pytest.raises(AssertionError, match="no text"):
        _resolve("enters 'x' into the notifications prompt")


def test_type_into_page_field_still_fills():
    # a real DOM field named "location" is not a permission prompt
    assert _resolve("types 'Toronto' into the location field") == \
        ("fill", {"value": "Toronto", "locator": "location field"})
    # bare JS prompt text entry is untouched
    assert _resolve("types 'Toronto' into the prompt") == \
        ("arm_dialog", {"response": "accept", "answer": "Toronto"})


# --- action: composite runs DOM sweep AND only the named deny ---------------

def test_close_popups_denies_only_named_permission(monkeypatch):
    # page.keyboard.press runs inside a try/except, so a bare object is enough
    swept = []
    denied = []
    monkeypatch.setattr(actions, "_sweep_popups", lambda page: swept.append(1) or 0)
    monkeypatch.setattr(actions, "dismiss_permission_prompt",
                        lambda p, perm: denied.append(perm))

    actions.close_popups(object(), deny_permissions=["geolocation"])
    assert swept == [1]            # DOM sweep ran
    assert denied == ["geolocation"]  # only the named permission


def test_close_popups_without_permissions_denies_nothing(monkeypatch):
    denied = []
    monkeypatch.setattr(actions, "_sweep_popups", lambda page: 0)
    monkeypatch.setattr(actions, "dismiss_permission_prompt",
                        lambda p, perm: denied.append(perm))
    actions.close_popups(object())
    assert denied == []


# --- action: grant canonicalizes aliases ------------------------------------

def test_grant_permissions_canonicalizes_aliases():
    granted = {}

    class _Ctx:
        def grant_permissions(self, perms):
            granted["perms"] = perms

    page = type("P", (), {"context": _Ctx()})()
    actions.grant_permissions(page, "location, notification")
    assert granted["perms"] == ["geolocation", "notifications"]

    # non-prompt Playwright names pass through untouched
    actions.grant_permissions(page, "clipboard-read,geolocation")
    assert granted["perms"] == ["clipboard-read", "geolocation"]


# --- action: engine-detected deny + surfaced CDP failure --------------------

def test_dismiss_permission_surfaces_chromium_cdp_failure():
    class _Ctx:
        browser = type("B", (), {"browser_type": type("T", (), {"name": "chromium"})()})()

        def new_cdp_session(self, page):
            raise RuntimeError("boom")

    page = type("P", (), {"url": "https://x.test/", "context": _Ctx()})()
    with pytest.raises(RuntimeError, match="boom"):
        actions.dismiss_permission_prompt(page, "location")


def test_dismiss_permission_unknown_kind_raises():
    with pytest.raises(AssertionError, match="Unknown permission prompt"):
        actions.dismiss_permission_prompt(None, "bluetooth")
