"""NOOD_0018 — heuristic RCA report generator (noodle/reporting/rca_report.py).

Covers the pure classify() rules (no model, no I/O) and collect()/render_markdown()
reading real allure-results JSON on disk. No browser, no LLM.
"""
import json

from noodle.reporting import rca_report as rr


def _entry(message="", trace="", warnings=None):
    return {"message": message, "trace": trace, "warnings": warnings or []}


# --- classify() --------------------------------------------------------------

def test_classify_unhandled_exception_is_test_script():
    e = _entry(trace="Traceback (most recent call last):\n  ...\nKeyError: 'user'")
    v = rr.classify(e)
    assert v["category"] == "test-script"
    assert "KeyError" in v["reason"]


def test_classify_ignores_assertion_error_traceback():
    e = _entry(message="Expected 'X' to contain 'Y'",
                trace="Traceback (most recent call last):\n  ...\nAssertionError: boom")
    v = rr.classify(e)
    assert v["category"] != "test-script"


def test_classify_rate_limit_is_environment_flap():
    e = _entry(message="reached the daily request limit for our public API")
    assert rr.classify(e)["category"] == "environment-flap"


def test_classify_vision_locate_failed_is_config_gap():
    e = _entry(warnings=["vision-locate failed for 'X': litellm.APIConnectionError"])
    assert rr.classify(e)["category"] == "config-gap"


def test_classify_ambiguous_locator_is_locator_rot():
    e = _entry(warnings=["Ambiguous locator 'Checkout' — matched multiple elements"])
    assert rr.classify(e)["category"] == "locator-rot"


def test_classify_healed_partial_text_is_locator_rot():
    e = _entry(warnings=["Healed: matched 'catalog heading' via partial text 'catalog'"])
    assert rr.classify(e)["category"] == "locator-rot"


def test_classify_no_browser_is_test_script():
    e = _entry(message='This scenario has no browser, but this step needs one: "uses this payload"')
    assert rr.classify(e)["category"] == "test-script"


def test_classify_could_not_find_element_is_locator_rot():
    e = _entry(message="Could not find element to read: 'movie count'")
    assert rr.classify(e)["category"] == "locator-rot"


def test_classify_element_not_visible_timeout_is_locator_rot():
    """NOOD_0123 — a fill()/click() timeout on a hidden target is a visibility
    problem, not the 'unknown' catch-all and not a framework bug, even though it
    arrives wrapped in a Playwright TimeoutError traceback."""
    e = _entry(
        message="Locator.fill: Timeout 10000ms exceeded.",
        trace="Traceback (most recent call last):\n  ...\n"
              "playwright._impl._errors.TimeoutError: Locator.fill: Timeout "
              "10000ms exceeded.\nCall log:\n  - waiting for locator '#search-input'\n"
              "  -   element is not visible\n")
    v = rr.classify(e)
    assert v["category"] == "locator-rot"
    assert "hidden" in v["reason"]


def test_classify_http_5xx_is_environment_flap():
    e = _entry(message="ASSERT FAILED: Expected status 200, got 503")
    assert rr.classify(e)["category"] == "environment-flap"


def test_classify_http_4xx_is_test_data():
    e = _entry(message="ASSERT FAILED: Expected status 200, got 405")
    assert rr.classify(e)["category"] == "test-data"


def test_classify_generic_expected_mismatch_is_app_regression():
    e = _entry(message="Expected to see 'Selected count: 2' on page — not found.")
    assert rr.classify(e)["category"] == "app-regression"


def test_classify_response_body_does_not_contain_is_app_regression():
    e = _entry(message="Response body does not contain 'Google Pixel 6 Pro'")
    assert rr.classify(e)["category"] == "app-regression"


def test_classify_leaked_undefined_is_app_regression_medium_confidence():
    # NOOD_0021 — 'undefined'/'null'/'NaN' visible in the DOM is a stronger
    # signal than the generic mismatch rule below it, so it must win and get
    # medium (not low) confidence.
    e = _entry(message="Expected 'trailer runtime' to NOT contain 'undefined' — actual: 'undefined min'")
    v = rr.classify(e)
    assert v["category"] == "app-regression"
    assert v["confidence"] == "medium"


def test_classify_falls_back_to_unknown():
    e = _entry(message="something completely novel happened")
    assert rr.classify(e)["category"] == "unknown"


# --- collect() / render_markdown() -------------------------------------------

def _write_result(tmp_path, **overrides):
    result = {
        "uuid": overrides.get("uuid", "u1"),
        "historyId": overrides.get("historyId", "h1"),
        "name": overrides.get("name", "A scenario"),
        "fullName": "F: A scenario",
        "labels": overrides.get("labels", [{"name": "feature", "value": "F"}]),
        "steps": overrides.get("steps", [{
            "name": "Then something",
            "status": "failed",
            "statusDetails": {"message": "boom", "trace": "", "warnings": []},
        }]),
        "status": overrides.get("status", "failed"),
        "stop": overrides.get("stop", 1000),
    }
    (tmp_path / f"{result['uuid']}-result.json").write_text(json.dumps(result))


def test_collect_skips_passed_scenarios(tmp_path):
    _write_result(tmp_path, uuid="u1", status="passed")
    assert rr.collect(str(tmp_path)) == []


def test_collect_dedupes_retries_keeping_latest(tmp_path):
    _write_result(tmp_path, uuid="u1", historyId="h1", stop=1000)
    _write_result(tmp_path, uuid="u2", historyId="h1", stop=2000)
    entries = rr.collect(str(tmp_path))
    assert len(entries) == 1


def test_render_markdown_no_failures(tmp_path):
    md = rr.render_markdown(str(tmp_path))
    assert "No failed or errored scenarios" in md


def test_collect_warnings_reads_passed_scenarios_only(tmp_path):
    # NOOD_0021 — a passed scenario whose step logged an ambiguous-locator
    # warning must surface here even though collect() (failures only) skips it.
    _write_result(tmp_path, uuid="u1", status="passed", steps=[{
        "name": "When User clicks 'Preview'",
        "status": "passed",
        "statusDetails": {"warnings": ["Ambiguous locator 'Preview' — matched multiple elements"]},
    }])
    warned = rr.collect_warnings(str(tmp_path))
    assert len(warned) == 1
    assert "Ambiguous locator" in warned[0]["warning"]


def test_collect_warnings_ignores_failed_scenarios(tmp_path):
    _write_result(tmp_path, uuid="u1", status="failed")
    assert rr.collect_warnings(str(tmp_path)) == []


def test_render_markdown_includes_passed_with_warnings_section(tmp_path):
    _write_result(tmp_path, uuid="u1", status="passed", steps=[{
        "name": "When User clicks 'Preview'",
        "status": "passed",
        "statusDetails": {"warnings": ["Ambiguous locator 'Preview' — matched multiple elements"]},
    }])
    md = rr.render_markdown(str(tmp_path))
    assert "Passed with warnings" in md
    assert "Ambiguous locator" in md


def test_render_markdown_includes_heuristic_and_ai_columns(tmp_path):
    _write_result(tmp_path, uuid="u1", labels=[
        {"name": "feature", "value": "F"},
        {"name": "rca_category", "value": "locator-rot"},
        {"name": "rca_confidence", "value": "high"},
        {"name": "rca_reason", "value": "ai reason"},
        {"name": "rca_fix", "value": "ai fix"},
    ])
    md = rr.render_markdown(str(tmp_path))
    assert "locator-rot" in md
    assert "ai reason" in md
    assert "ai fix" in md
