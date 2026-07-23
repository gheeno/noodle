"""NOOD_0009 — phrase-alias normalization: wording drift is canonicalized
before pattern matching, for ANY step in the dictionary (not one action).

The collision-regression tests and the full-corpus scan are the safety net
for future alias additions: a badly-scoped alias (e.g. one that accidentally
swallows another action family's wording) fails loudly here instead of
silently misrouting a step at runtime.
"""
from noodle.resolver import match_step
from noodle.resolver.step_resolver import _example_corpus


def _resolve(text):
    # NOOD_0155 — match_step, not patterns.match: the dictionary now carries
    # wok-table examples (perf, desktop) that resolve through the same
    # web-first fallthrough the runtime uses.
    return match_step(text)


# --- alias coverage ----------------------------------------------------------

def test_equal_to_synonyms():
    assert _resolve("PRICE is equal to '9.99'") == \
        ("assert_compare", {"left": "PRICE", "op": "==", "right": "9.99"})
    assert _resolve("PRICE is now equal to '9.99'") == \
        ("assert_compare", {"left": "PRICE", "op": "==", "right": "9.99"})
    assert _resolve("PRICE equals '9.99'") == \
        ("assert_compare", {"left": "PRICE", "op": "==", "right": "9.99"})


def test_not_equal_synonyms():
    assert _resolve("PRICE is not equal to '0.00'") == \
        ("assert_compare", {"left": "PRICE", "op": "!=", "right": "0.00"})
    assert _resolve("PRICE does not equal '0.00'") == \
        ("assert_compare", {"left": "PRICE", "op": "!=", "right": "0.00"})


def test_canonical_wording_unaffected():
    assert _resolve("PRICE should equal '9.99'") == \
        ("assert_compare", {"left": "PRICE", "op": "==", "right": "9.99"})
    assert _resolve("PRICE should not equal '0.00'") == \
        ("assert_compare", {"left": "PRICE", "op": "!=", "right": "0.00"})


# --- collision regressions ----------------------------------------------------
# "should match" deliberately has NO alias to "should equal" — pixel/visual
# baseline steps already own that phrase. If someone adds one, these break.

def test_pixel_baseline_not_hijacked_by_equal_alias():
    assert _resolve("the screen should match the pixel baseline") == \
        ("pixel_baseline", {"name": "default"})
    assert _resolve('the "header" screen should match the baseline') == \
        ("pixel_baseline", {"name": "header"})


def test_greater_less_equal_to_not_hijacked():
    assert _resolve("COUNT should be greater than or equal to '1'") == \
        ("assert_compare", {"left": "COUNT", "op": ">=", "right": "1"})
    assert _resolve("COUNT should be less than or equal to '100'") == \
        ("assert_compare", {"left": "COUNT", "op": "<=", "right": "100"})


def test_url_should_equal_not_hijacked():
    assert _resolve("the url should equal 'https://example.com/done'") == \
        ("assert_url", {"fragment": "https://example.com/done", "mode": "exact"})


# --- full corpus scan ---------------------------------------------------------
# Every example in docs/steps_dictionary.md must still resolve — this is the
# general mechanism the alias table plugs into: add a synonym, run the suite,
# a collision with ANY documented step shows up here immediately.

def test_every_documented_example_resolves():
    corpus = _example_corpus()
    assert len(corpus) > 100, "corpus loader found suspiciously few examples — check docs path"
    unresolved = [line for line in corpus if _resolve(line) is None]
    assert not unresolved, f"{len(unresolved)} documented steps no longer match: {unresolved}"
