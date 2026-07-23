"""Phase B — retry/quarantine, deterministic pixel diff, logging."""
import json

from PIL import Image

from noodle.agents.web.actions import _pixel_diff_ratio
from noodle.cli import _all_failures_quarantined
from noodle.resolver.patterns import match, normalize_subject

# --- deterministic pixel diff ------------------------------------------------

def test_pixel_diff_identical_is_zero():
    img = Image.new("RGB", (10, 10), (255, 255, 255))
    assert _pixel_diff_ratio(img, img.copy()) == 0.0


def test_pixel_diff_all_changed_is_one():
    white = Image.new("RGB", (10, 10), (255, 255, 255))
    black = Image.new("RGB", (10, 10), (0, 0, 0))
    assert _pixel_diff_ratio(white, black) == 1.0


def test_pixel_diff_partial():
    a = Image.new("RGB", (10, 10), (255, 255, 255))
    b = a.copy()
    for x in range(10):          # blacken one row of 10 → 10/100 pixels
        b.putpixel((x, 0), (0, 0, 0))
    assert _pixel_diff_ratio(a, b) == 0.10


def test_pixel_diff_size_mismatch_is_none():
    a = Image.new("RGB", (10, 10))
    b = Image.new("RGB", (20, 20))
    assert _pixel_diff_ratio(a, b) is None


def test_pixel_diff_ignores_subthreshold_noise():
    a = Image.new("RGB", (10, 10), (255, 255, 255))
    b = Image.new("RGB", (10, 10), (250, 250, 250))   # diff 5 < tol 30
    assert _pixel_diff_ratio(a, b) == 0.0


def test_pixel_diff_threshold_boundary_is_exclusive():
    # Luminance diff exactly == tol must NOT count; tol+1 must. Locks the
    # histogram slice (hist[tol+1:]) to the old `p > tol` semantics. Grey at
    # level L has luminance L (ITU-R 601 weights sum to 1), so a solid-grey
    # diff lands in exactly one bucket.
    white = Image.new("RGB", (4, 4), (255, 255, 255))
    at_tol = Image.new("RGB", (4, 4), (255 - 30, 255 - 30, 255 - 30))      # diff 30 == tol
    over_tol = Image.new("RGB", (4, 4), (255 - 31, 255 - 31, 255 - 31))    # diff 31 > tol
    assert _pixel_diff_ratio(white, at_tol) == 0.0
    assert _pixel_diff_ratio(white, over_tol) == 1.0


# --- pattern routing: pixel baseline vs LLM baseline -------------------------

def _resolve(text):
    return match(normalize_subject(text))


def test_pixel_baseline_pattern_default():
    action, params = _resolve("the screen should match the baseline")
    assert action == "pixel_baseline"
    assert params == {"name": "default"}


def test_pixel_baseline_pattern_named():
    action, params = _resolve('the "login" screen should match the baseline')
    assert action == "pixel_baseline"
    assert params["name"] == "login"


def test_llm_baseline_phrase_does_not_hit_pixel_baseline():
    # "look the same as before" must NOT route to the deterministic pixel diff
    # (it stays on the LLM/semantic path — pre-existing behavior).
    action, _ = _resolve("the screen should look the same as before")
    assert action != "pixel_baseline"


# --- quarantine exit-code scan ----------------------------------------------

def _write_result(tmp_path, name, status, tags):
    r = {
        "name": name,
        "status": status,
        "labels": [{"name": "tag", "value": t} for t in tags],
    }
    (tmp_path / f"{name}-result.json").write_text(json.dumps(r))


def test_quarantine_none_when_no_results(tmp_path):
    assert _all_failures_quarantined(str(tmp_path)) is None


def test_quarantine_none_when_all_passed(tmp_path):
    _write_result(tmp_path, "a", "passed", ["web"])
    assert _all_failures_quarantined(str(tmp_path)) is None


def test_quarantine_true_when_all_failures_quarantined(tmp_path):
    _write_result(tmp_path, "a", "passed", ["web"])
    _write_result(tmp_path, "b", "failed", ["web", "quarantine"])
    assert _all_failures_quarantined(str(tmp_path)) is True


def test_quarantine_false_when_a_real_failure_exists(tmp_path):
    _write_result(tmp_path, "b", "failed", ["web", "quarantine"])
    _write_result(tmp_path, "c", "failed", ["web"])
    assert _all_failures_quarantined(str(tmp_path)) is False
