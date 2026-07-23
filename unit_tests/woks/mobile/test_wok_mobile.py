"""NOOD_0155 — mobile wok: registry contract for the Appium domain.

The mobile *engine* tests (driver/gestures, Appium stubs) predate the wok
concept and stay in unit_tests/test_nood_0032.py; this folder owns the wok
boundary — routing, extras, and the step families the wok claims.
"""
from noodle import wok
from noodle.orchestrator.runner import _MOBILE_TYPES
from noodle.resolver.step_resolver import VALID_TYPES


def test_mobile_wok_routing():
    assert wok.wok_for_tags(["appium"]).name == "mobile"
    assert wok.wok_for_tags(["android"]).name == "mobile"
    assert wok.wok_for_tags(["ios", "smoke"]).name == "mobile"
    # Playwright device emulation is web; native desktop platforms are desktop.
    assert wok.wok_for_tags(["mobile"]).name == "web"
    assert wok.wok_for_tags(["windows"]).name == "desktop"


def test_mobile_wok_declares_the_appium_extra():
    w = wok.WOKS["mobile"]
    assert w.extras == ("mobile",)
    assert "appium" in w.probe_modules


def test_mobile_step_family_is_registered():
    # Every step type the mobile agent dispatches must be resolvable (the LLM
    # fallback validates against VALID_TYPES).
    assert _MOBILE_TYPES <= VALID_TYPES
    assert {"swipe", "long_press", "hide_keyboard", "background_app"} <= _MOBILE_TYPES
