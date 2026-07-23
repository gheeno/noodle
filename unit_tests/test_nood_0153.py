"""NOOD_0153 — evidence screenshots: proof a green step did what it claims.

Covers the four layers, browser-free:
  * marker parsing — the trailing "( take a screenshot )" is stripped by
    _pre_clean (so every resolution path tolerates it) and flagged by
    runner.execute_step;
  * gating — evidence.wanted() (mode env / tags / marker / page presence);
  * capture — viewport JPEG + green box via a fake page/locator;
  * reporting — writer attachments on passed steps, rca_report's Evidence
    section (paths-only markdown, inlined-thumbnail html).
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from noodle.resolver.patterns import (
    EVIDENCE_MARKER_RE,
    match,
    normalize_phrasing,
    normalize_subject,
)


def _resolve(text):
    return match(normalize_phrasing(normalize_subject(text)))


class _Ctx:
    """Plain attribute bag — behave's Context stand-in for runner/hooks."""


def _make_step(keyword="When", name="user clicks Login", status="passed",
               exception=None, error_message=""):
    step = MagicMock()
    step.keyword = keyword
    step.name = name
    step.status = status
    step.exception = exception
    step.error_message = error_message
    return step


class _FakeLoc:
    def __init__(self, box=None):
        self.box = box or {"x": 10, "y": 10, "width": 40, "height": 20}
        self.scrolled = 0

    def scroll_into_view_if_needed(self, timeout=None):
        self.scrolled += 1

    def bounding_box(self):
        return self.box


class _FakePage:
    viewport_size = {"width": 100, "height": 80}

    def __init__(self):
        self.shot_kwargs = None

    def screenshot(self, path=None, full_page=False, **kwargs):
        self.shot_kwargs = {"full_page": full_page, **kwargs}
        Image.new("RGB", (100, 80), color=(220, 220, 220)).save(path)


@pytest.fixture(autouse=True)
def _reset_locator_state():
    yield
    from noodle.agents.web import locator
    locator.clear_last_match()
    locator.set_follow(False)


# ---------------------------------------------------------------------------
# Marker parsing
# ---------------------------------------------------------------------------

class TestMarker:
    def test_marker_stripped_before_matching(self):
        assert _resolve("User clicks the 'Login' button ( take a screenshot )") == \
            _resolve("User clicks the 'Login' button")

    @pytest.mark.parametrize("suffix", [
        "( take a screenshot )", "(take a screenshot)", "( Take a Screenshot )",
        "( capture a screenshot )", "(screenshot)", "( takes an evidence screenshot )",
        "( please take a screenshot here )",
    ])
    def test_marker_variants(self, suffix):
        assert EVIDENCE_MARKER_RE.search(f"clicks the 'Save' button {suffix}")

    def test_ordinary_parentheses_survive(self):
        assert EVIDENCE_MARKER_RE.search("clicks the 'Save (draft)' button") is None

    def test_runner_sets_flag_and_strips(self):
        from noodle.orchestrator.runner import execute_step
        ctx = _Ctx()
        ctx.page = None
        ctx._vars = {}
        execute_step("sets {var:GREETING} to 'hi' ( take a screenshot )", ctx)
        assert ctx._vars["GREETING"] == "hi"
        assert ctx._evidence_request is True
        assert isinstance(ctx._match_seq_at_step_start, int)

    def test_runner_no_flag_without_marker(self):
        from noodle.orchestrator.runner import ctx_get, execute_step
        ctx = _Ctx()
        ctx.page = None
        ctx._vars = {}
        execute_step("sets {var:GREETING} to 'hi'", ctx)
        assert ctx_get(ctx, "_evidence_request") is None


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------

class TestWanted:
    def test_default_mode_is_last_step_only(self, monkeypatch):
        from noodle.reporting import evidence
        monkeypatch.delenv("NOODLE_EVIDENCE", raising=False)
        assert evidence.wanted([], False, True, True) is True
        assert evidence.wanted([], False, False, True) is False

    def test_no_page_never_captures(self):
        from noodle.reporting import evidence
        assert evidence.wanted(["evidence"], True, True, False) is False

    def test_no_evidence_tag_beats_everything(self, monkeypatch):
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_EVIDENCE", "all")
        assert evidence.wanted(["no_evidence"], True, True, True) is False

    def test_marker_and_tag_survive_mode_off(self, monkeypatch):
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_EVIDENCE", "off")
        assert evidence.wanted([], True, False, True) is True
        assert evidence.wanted(["evidence"], False, False, True) is True
        assert evidence.wanted([], False, True, True) is False

    def test_mode_all(self, monkeypatch):
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_EVIDENCE", "all")
        assert evidence.wanted([], False, False, True) is True

    def test_unknown_mode_degrades_to_last(self, monkeypatch):
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_EVIDENCE", "bananas")
        assert evidence.mode() == "last"


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

class TestCapture:
    def test_viewport_jpeg_with_box(self, tmp_path, monkeypatch):
        from noodle.agents.web import locator
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))
        loc = _FakeLoc()
        locator._last_match = ("toy", loc)
        page = _FakePage()
        path = evidence.capture(page, "the toy is seen in the cart", fresh_match=True)
        assert path and Path(path).is_file() and path.endswith(".jpg")
        assert page.shot_kwargs["full_page"] is False
        assert loc.scrolled == 1
        # The green outline (#1a7f37) sits on the box's top border (y=10..13).
        r, g, b = Image.open(path).convert("RGB").getpixel((30, 11))
        assert g > 90 and g > r and g > b

    def test_stale_match_gets_no_box(self, tmp_path, monkeypatch):
        from noodle.agents.web import locator
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))
        loc = _FakeLoc()
        locator._last_match = ("toy", loc)
        path = evidence.capture(_FakePage(), "goes back to the homepage",
                                fresh_match=False)
        assert path and Path(path).is_file()
        assert loc.scrolled == 0
        r, g, b = Image.open(path).convert("RGB").getpixel((30, 11))
        assert abs(r - g) < 30 and abs(g - b) < 30   # untouched grey, no box

    def test_no_page_returns_none(self):
        from noodle.reporting import evidence
        assert evidence.capture(None, "anything") is None

    # --- NOOD_0157: the shot is FOCUSED on the target element ---------------

    def test_capture_centers_element_and_flags_in_view(self, tmp_path, monkeypatch):
        """The target is JS-centered (scrollIntoView block:center — no
        actionability gate to time out) and meta carries element_in_view, so
        an agent can trust the shot without re-opening the image."""
        from noodle.agents.web import locator
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))

        class _CenteringLoc(_FakeLoc):
            def __init__(self):
                super().__init__()
                self.scripts = []

            def evaluate(self, script):
                self.scripts.append(script)

        class _WaitPage(_FakePage):
            def wait_for_timeout(self, ms):
                pass

        loc = _CenteringLoc()
        locator._last_match = ("toy", loc)
        path = evidence.capture(_WaitPage(), "the toy is seen in the cart",
                                fresh_match=True)
        assert path and Path(path).is_file()
        assert loc.scripts and "block: 'center'" in loc.scripts[0]
        assert loc.scrolled == 0        # JS centering used, not minimal-scroll
        meta = evidence.last_meta()
        assert meta["element_in_view"] is True

    def test_capture_flags_element_out_of_view(self, tmp_path, monkeypatch):
        """Centering failed (element center lands outside the viewport) →
        element_in_view: false, which summary surfaces as an unverified
        reason — a shot that can't show the element must not count as proof."""
        from noodle.agents.web import locator
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))
        loc = _FakeLoc(box={"x": 10, "y": 1000, "width": 40, "height": 20})
        locator._last_match = ("toy", loc)
        path = evidence.capture(_FakePage(), "the toy is seen in the cart",
                                fresh_match=True)
        assert path
        meta = evidence.last_meta()
        assert meta["element_in_view"] is False

    def test_capture_falls_back_to_minimal_scroll(self, tmp_path, monkeypatch):
        """A driver whose locator can't evaluate JS keeps the old
        scroll_into_view_if_needed path instead of losing the scroll."""
        from noodle.agents.web import locator
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))
        loc = _FakeLoc()                 # no .evaluate → JS path raises
        locator._last_match = ("toy", loc)
        assert evidence.capture(_FakePage(), "the toy is seen in the cart",
                                fresh_match=True)
        assert loc.scrolled == 1

    # --- NOOD_0157: refocus fallback — elementless final step ---------------

    def test_elementless_step_refocuses_previous_element(self, tmp_path, monkeypatch):
        """Final step resolved nothing (popup sweep, wait) but the scenario's
        last element is on the SAME page and still visible → box it, flag
        refocused (hooks then counts the evidence valid)."""
        from noodle.agents.web import locator
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))

        class _VisibleLoc(_FakeLoc):
            def is_visible(self):
                return True

        class _UrlPage(_FakePage):
            url = "https://x/results"

        loc = _VisibleLoc()
        locator._last_match = ("toy", loc)
        locator._last_match_url = "https://x/results"
        path = evidence.capture(_UrlPage(), "closes the popup if it appears",
                                fresh_match=False)
        assert path
        meta = evidence.last_meta()
        assert meta["refocused"] is True
        assert meta["locator"] == "toy"
        r, g, b = Image.open(path).convert("RGB").getpixel((30, 11))
        assert g > 90 and g > r and g > b    # green box on the old element

    def test_refocus_refused_after_navigation(self, tmp_path, monkeypatch):
        """The elementless final step landed on a DIFFERENT url than the one
        the element was matched on → no box (a stale outline would lie)."""
        from noodle.agents.web import locator
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))

        class _VisibleLoc(_FakeLoc):
            def is_visible(self):
                return True

        class _UrlPage(_FakePage):
            url = "https://x/checkout"

        loc = _VisibleLoc()
        locator._last_match = ("toy", loc)
        locator._last_match_url = "https://x/results"
        path = evidence.capture(_UrlPage(), "goes back to the homepage",
                                fresh_match=False)
        assert path
        meta = evidence.last_meta()
        assert "refocused" not in meta
        r, g, b = Image.open(path).convert("RGB").getpixel((30, 11))
        assert abs(r - g) < 30 and abs(g - b) < 30   # untouched grey

    def test_refocus_refused_when_element_gone(self, tmp_path, monkeypatch):
        """Same page but the element no longer reports visible → no box."""
        from noodle.agents.web import locator
        from noodle.reporting import evidence
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))

        class _GoneLoc(_FakeLoc):
            def is_visible(self):
                return False

        class _UrlPage(_FakePage):
            url = "https://x/results"

        locator._last_match = ("toy", _GoneLoc())
        locator._last_match_url = "https://x/results"
        evidence.capture(_UrlPage(), "closes the popup if it appears",
                         fresh_match=False)
        meta = evidence.last_meta()
        assert "refocused" not in meta


# ---------------------------------------------------------------------------
# Writer attachments on passed steps
# ---------------------------------------------------------------------------

class TestWriterAttachments:
    def _sr(self):
        from noodle.reporting.writer import ScenarioResult
        scenario = MagicMock()
        scenario.name = "Scenario"
        scenario.tags = []
        scenario.feature = MagicMock()
        scenario.feature.name = "Feature"
        return ScenarioResult(scenario)

    def test_passed_step_evidence_attachment(self):
        sr = self._sr()
        sr.add_step(_make_step(), "passed",
                    attachment_path="/nowhere/EVIDENCE_cart.jpg")
        att = sr.result["steps"][0]["attachments"][0]
        assert att["name"] == "evidence"
        assert att["type"] == "image/jpeg"
        assert att["source"] == "EVIDENCE_cart.jpg"

    def test_passed_step_custom_name(self):
        sr = self._sr()
        sr.add_step(_make_step(), "passed",
                    attachment_path="/nowhere/checkout.png",
                    attachment_name="screenshot")
        att = sr.result["steps"][0]["attachments"][0]
        assert att["name"] == "screenshot"
        assert att["type"] == "image/png"

    def test_failed_step_keeps_historic_name(self):
        sr = self._sr()
        sr.add_step(_make_step(status="failed", exception=AssertionError("x")),
                    "failed", attachment_path="/nowhere/FAILED_x_annotated.png")
        att = sr.result["steps"][0]["attachments"][0]
        assert att["name"] == "failure_screenshot"
        assert att["type"] == "image/png"

    def test_passed_step_without_attachment_unchanged(self):
        sr = self._sr()
        sr.add_step(_make_step(), "passed")
        assert "attachments" not in sr.result["steps"][0]


# ---------------------------------------------------------------------------
# hooks.after_step — attach on pass
# ---------------------------------------------------------------------------

class TestAfterStep:
    def _ctx(self, sr, page=None):
        ctx = _Ctx()
        ctx.page = page
        ctx._allure_result = sr
        ctx._scenario_failed = False
        ctx._manual_screenshot = None
        ctx._evidence_request = False
        return ctx

    def test_manual_screenshot_attached(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))
        from noodle import hooks
        sr = TestWriterAttachments()._sr()
        shot = tmp_path / "checkout.png"
        Image.new("RGB", (10, 10)).save(shot)
        ctx = self._ctx(sr)
        ctx._manual_screenshot = str(shot)
        hooks.after_step(ctx, _make_step(name="takes a screenshot 'checkout'"))
        att = sr.result["steps"][0]["attachments"][0]
        assert att["name"] == "screenshot"
        assert ctx._manual_screenshot is None

    def test_last_step_gets_evidence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))
        monkeypatch.delenv("NOODLE_EVIDENCE", raising=False)
        from noodle import hooks
        from noodle.agents.web import locator
        locator.clear_last_match()
        sr = TestWriterAttachments()._sr()
        step = _make_step(name="the 'toy' is seen in the cart")
        ctx = self._ctx(sr, page=_FakePage())
        ctx.scenario = MagicMock()
        ctx.scenario.effective_tags = []
        ctx.scenario.steps = [step]
        ctx._match_seq_at_step_start = locator.match_seq()
        hooks.after_step(ctx, step)
        att = sr.result["steps"][0]["attachments"][0]
        assert att["name"] == "evidence"
        assert att["type"] == "image/jpeg"

    def test_non_last_step_no_evidence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))
        monkeypatch.delenv("NOODLE_EVIDENCE", raising=False)
        from noodle import hooks
        sr = TestWriterAttachments()._sr()
        step = _make_step(name="user clicks 'Add to cart'")
        ctx = self._ctx(sr, page=_FakePage())
        ctx.scenario = MagicMock()
        ctx.scenario.effective_tags = []
        ctx.scenario.steps = [step, _make_step(name="later step")]
        hooks.after_step(ctx, step)
        assert "attachments" not in sr.result["steps"][0]

    # --- NOOD_0157: page-killing tail — evidence fires one step earlier -----

    def test_tab_closing_tail_shifts_evidence_back(self, tmp_path, monkeypatch):
        """Scenario ends with 'closes the current tab': nothing to shoot
        there, so _evidence_last_step aims 'last' mode at the step before it
        and that step gets the evidence attachment."""
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))
        monkeypatch.delenv("NOODLE_EVIDENCE", raising=False)
        from noodle import hooks
        from noodle.agents.web import locator
        locator.clear_last_match()
        sr = TestWriterAttachments()._sr()
        see = _make_step(name="the 'toy' is seen in the cart")
        close = _make_step(name="User closes the current tab")
        scenario = MagicMock()
        scenario.effective_tags = []
        scenario.steps = [see, close]
        assert hooks._evidence_last_step(scenario) is see
        ctx = self._ctx(sr, page=_FakePage())
        ctx.scenario = scenario
        ctx._evidence_last_step = see
        ctx._match_seq_at_step_start = locator.match_seq()
        hooks.after_step(ctx, see)
        att = sr.result["steps"][0]["attachments"][0]
        assert att["name"] == "evidence"

    def test_evidence_last_step_defaults_to_final_step(self):
        """No page-killing tail → None, meaning after_step's steps[-1]."""
        from noodle import hooks
        scenario = MagicMock()
        scenario.steps = [_make_step(name="user clicks 'Add to cart'"),
                          _make_step(name="the 'toy' is seen in the cart")]
        assert hooks._evidence_last_step(scenario) is scenario.steps[-1]

    def test_refocused_evidence_counts_valid(self, tmp_path, monkeypatch):
        """Elementless final step + successful refocus → evidence_meta.valid
        True (summary keeps the run verified)."""
        monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path))
        monkeypatch.delenv("NOODLE_EVIDENCE", raising=False)
        from noodle import hooks
        from noodle.agents.web import locator

        class _VisibleLoc(_FakeLoc):
            def is_visible(self):
                return True

        class _UrlPage(_FakePage):
            url = "https://x/results"

        locator.clear_last_match()
        locator._last_match = ("toy", _VisibleLoc())
        locator._last_match_url = "https://x/results"
        sr = TestWriterAttachments()._sr()
        step = _make_step(name="closes the popup if it appears within 2 seconds")
        ctx = self._ctx(sr, page=_UrlPage())
        ctx.scenario = MagicMock()
        ctx.scenario.effective_tags = []
        ctx.scenario.steps = [step]
        ctx._match_seq_at_step_start = locator.match_seq()   # no move → not fresh
        hooks.after_step(ctx, step)
        ev = sr.result["steps"][0]["statusDetails"]["evidence"]
        assert ev["refocused"] is True
        assert ev["valid"] is True


# ---------------------------------------------------------------------------
# Follow mode (headed-run viewport tracking)
# ---------------------------------------------------------------------------

class TestFollowMode:
    def test_find_scrolls_match_into_view_when_following(self, monkeypatch):
        from noodle.agents.web import locator
        loc = _FakeLoc()
        monkeypatch.setattr(locator, "_find", lambda *a, **k: loc)
        monkeypatch.delenv("NOODLE_FOLLOW", raising=False)
        locator.set_follow(True)
        seq0 = locator.match_seq()
        assert locator.find(MagicMock(), "toy") is loc
        assert loc.scrolled == 1
        assert locator.match_seq() == seq0 + 1
        assert locator.last_match()[0] == "toy"

    def test_follow_off_by_default_and_env_override(self, monkeypatch):
        from noodle.agents.web import locator
        loc = _FakeLoc()
        monkeypatch.setattr(locator, "_find", lambda *a, **k: loc)
        monkeypatch.setenv("NOODLE_FOLLOW", "false")
        locator.set_follow(True)                  # env wins over headed default
        locator.find(MagicMock(), "toy")
        assert loc.scrolled == 0


# ---------------------------------------------------------------------------
# RCA report — Evidence section
# ---------------------------------------------------------------------------

def _write_result(d: Path, name: str, status: str, steps: list[dict]):
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "uuid": name, "historyId": name, "name": name,
        "fullName": f"Feature: {name}", "status": status,
        "stop": 1000,
        "labels": [{"name": "feature", "value": "Feature"},
                   {"name": "parentSuite", "value": "shop"},
                   {"name": "featureFile", "value": "features/shop.feature"}],
        "steps": steps,
    }
    if status == "failed":
        payload["statusDetails"] = {"message": "boom", "trace": ""}
    (d / f"{name}-result.json").write_text(json.dumps(payload))


class TestRcaEvidence:
    def _results(self, tmp_path) -> Path:
        d = tmp_path / "allure-results"
        d.mkdir()
        ev = d / "aaaa-attachment.jpg"
        Image.new("RGB", (60, 40), color=(0, 128, 0)).save(ev)
        fail = d / "bbbb-attachment.png"
        Image.new("RGB", (60, 40), color=(128, 0, 0)).save(fail)
        _write_result(d, "green", "passed", [
            {"name": "Then the toy is seen in the cart", "status": "passed",
             "attachments": [{"name": "evidence", "source": ev.name,
                              "type": "image/jpeg"}]},
        ])
        _write_result(d, "red", "failed", [
            {"name": "Then checkout works", "status": "failed",
             "statusDetails": {"message": "boom", "trace": ""},
             "attachments": [{"name": "failure_screenshot", "source": fail.name,
                              "type": "image/png"}]},
        ])
        return d

    def test_collect_evidence(self, tmp_path):
        from noodle.reporting import rca_report
        d = self._results(tmp_path)
        shots = rca_report.collect_evidence(str(d))
        assert {s["kind"] for s in shots} == {"evidence", "failure_screenshot"}
        assert all(s["app"] == "shop" for s in shots)

    def test_markdown_lists_paths_never_pixels(self, tmp_path):
        from noodle.reporting import rca_report
        d = self._results(tmp_path)
        md = rca_report.render_markdown(str(d))
        assert "Evidence screenshots (2)" in md
        assert "aaaa-attachment.jpg" in md
        assert "base64" not in md

    def test_html_inlines_thumbnails(self, tmp_path):
        from noodle.reporting import rca_report
        d = self._results(tmp_path)
        html_out = rca_report.render_html(str(d))
        assert "Evidence screenshots (2)" in html_out
        assert html_out.count("data:image/jpeg;base64,") == 2

    def test_green_run_still_shows_evidence(self, tmp_path):
        from noodle.reporting import rca_report
        d = tmp_path / "allure-results"
        d.mkdir()
        ev = d / "cccc-attachment.jpg"
        Image.new("RGB", (60, 40)).save(ev)
        _write_result(d, "green", "passed", [
            {"name": "Then all good", "status": "passed",
             "attachments": [{"name": "evidence", "source": ev.name,
                              "type": "image/jpeg"}]},
        ])
        md = rca_report.render_markdown(str(d))
        assert "No failed or errored scenarios" in md
        assert "Evidence screenshots (1)" in md
        assert "data:image/jpeg;base64," in rca_report.render_html(str(d))

    def test_non_image_attachments_skipped(self, tmp_path):
        from noodle.reporting import rca_report
        d = tmp_path / "allure-results"
        d.mkdir()
        _write_result(d, "green", "passed", [
            {"name": "Then all good", "status": "passed",
             "attachments": [{"name": "network log", "source": "x.json",
                              "type": "application/json"}]},
        ])
        assert rca_report.collect_evidence(str(d)) == []
