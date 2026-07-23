"""NOOD_0155 — web wok: the mature wok must be untouched by the wok concept.

The broad web regression suite stays where it always was (unit_tests/*.py —
NOOD_0154 and earlier); this folder guards the web wok's *boundary*: adding
the perf/desktop step tables must never change how a web step resolves.
"""
from noodle import wok
from noodle.resolver.step_resolver import resolve


def test_web_verbs_still_resolve_to_web_actions():
    # A sample across the web vocabulary — each must hit the web table,
    # not fall through to a wok table.
    cases = {
        'User clicks "Login"': 'click',
        'User navigates to "https://example.com"': 'navigate',
        'fills "Username" with "kai"': 'fill',
        '"Welcome" should be visible': 'assert_visible',
        'takes a screenshot "home"': 'screenshot',
    }
    for step, expected in cases.items():
        assert resolve(step)['type'] == expected, step


def test_wok_tables_only_consulted_after_web_misses():
    # The perf/desktop phrasings are namespaced — no web pattern matches them,
    # and no wok pattern matches core web phrasing. Spot-check both directions.
    perf = resolve('runs a load test on "http://x" with 2 users for 1 seconds')
    assert perf['type'] == 'perf_load'
    web = resolve('User clicks "Run"')       # 'runs...' prefix must not confuse
    assert web['type'] == 'click'


def test_browser_engine_tags_stay_in_the_web_wok():
    for tag in ("firefox", "webkit", "safari", "edge", "headed", "headless"):
        assert wok.wok_for_tags([tag]).name == "web"
