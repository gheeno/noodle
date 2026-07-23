"""NOOD_0155 — the wok registry: Noodle's formal capability-domain concept.

unit_tests/woks/ is the per-wok isolation boundary: each wok's tests live in
their own subfolder (web/, mobile/, desktop/, performance/) so capability work
on one wok can be regression-checked alone (`pytest unit_tests/woks/desktop`)
without cross-contaminating the others. This file covers the concept itself.
"""
from noodle import wok
from noodle.hooks import appium_platform


def test_the_four_woks_exist():
    assert set(wok.WOKS) == {"web", "mobile", "desktop", "performance"}


def test_every_wok_is_fully_described():
    for w in wok.WOKS.values():
        assert w.name and w.title and w.blurb
        assert w.engines and w.tags
        assert w.samples.startswith("sample_feature_tests/")
        assert w.unit_tests == f"unit_tests/woks/{w.name}"
        assert w.screenshots  # every wok must state its screenshot capability


def test_untagged_scenario_cooks_in_the_web_wok():
    assert wok.wok_for_tags([]).name == "web"
    assert wok.wok_for_tags(["smoke", "capability"]).name == "web"


def test_routing_precedence_matches_the_engine():
    # These pairs mirror hooks.before_scenario + steps/catch_all.py — the
    # registry must never disagree with what the runtime actually does.
    cases = {
        ("perf",): "performance",
        ("visual",): "desktop",
        ("appium",): "mobile",
        ("android",): "mobile",
        ("ios",): "mobile",
        ("windows",): "desktop",       # Appium native desktop
        ("mac",): "desktop",
        ("api",): "web",               # REST rides in the web wok
        ("terminal", "web"): "web",    # browser-embedded terminal (OCR bridge)
        ("mobile",): "web",            # @mobile = Playwright device EMULATION
        ("mobile", "android"): "web",  # @mobile wins — pre-NOOD_0032 meaning
    }
    for tags, expected in cases.items():
        assert wok.wok_for_tags(tags).name == expected, tags


def test_mobile_emulation_precedence_agrees_with_appium_platform():
    # hooks.appium_platform is the runtime's arbiter of '@mobile @android';
    # the registry's precedence must track it exactly.
    for tags in (["mobile", "android"], ["mobile", "windows"], ["android"], ["windows"]):
        platform = appium_platform(tags)
        routed = wok.wok_for_tags(tags).name
        if platform in ("android", "ios"):
            assert routed == "mobile"
        elif platform in ("windows", "mac"):
            assert routed == "desktop"
        else:
            assert routed == "web"


def test_pattern_priority_is_tag_aware():
    # The scenario's wok gets first claim on its own step grammar; no tags →
    # web-first best guess (the pre-wok behavior).
    assert wok.pattern_priority(None) == ("web", "performance", "desktop")
    assert wok.pattern_priority([]) == ("web", "performance", "desktop")
    assert wok.pattern_priority(["web", "smoke"]) == ("web", "performance", "desktop")
    assert wok.pattern_priority(["perf"]) == ("performance", "web", "desktop")
    assert wok.pattern_priority(["windows"]) == ("desktop", "web", "performance")
    assert wok.pattern_priority(["mac"]) == ("desktop", "web", "performance")
    # Mobile scenarios keep web-first — the Appium step family resolves via
    # the web table's verbs (click/fill/swipe...), it has no table of its own.
    assert wok.pattern_priority(["appium", "android"]) == ("web", "performance", "desktop")


def test_installed_probe_never_imports():
    # Probe-only contract: core-dep woks are always ready; a wok with a
    # bogus probe module reports not-installed instead of raising.
    assert wok.installed(wok.WOKS["web"])
    assert wok.installed(wok.WOKS["performance"])
    fake = wok.Wok(name="x", title="X", blurb="b", engines=("e",), tags=("t",),
                   extras=("nope",), probe_modules=("definitely_not_a_module",))
    assert not wok.installed(fake)


def test_wok_cli_lists_all_woks():
    from typer.testing import CliRunner

    from noodle.cli import app
    result = CliRunner().invoke(app, ["wok"])
    assert result.exit_code == 0
    for name in ("Web", "Mobile", "Desktop", "Performance"):
        assert name in result.output
    assert "docs/woks.md" in result.output


def test_wok_cli_detail_and_unknown():
    from typer.testing import CliRunner

    from noodle.cli import app
    detail = CliRunner().invoke(app, ["wok", "desktop"])
    assert detail.exit_code == 0
    assert "unit_tests/woks/desktop" in detail.output
    unknown = CliRunner().invoke(app, ["wok", "banana"])
    assert unknown.exit_code == 1
