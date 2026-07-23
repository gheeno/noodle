"""
Phase 5 — Reporting unit tests.
No browser, no allure binary, no subprocess.
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_step(keyword="When", name="user clicks Login", status="passed", exception=None, error_message=""):
    step = MagicMock()
    step.keyword = keyword
    step.name = name
    step.status = status
    step.exception = exception
    step.error_message = error_message
    return step


def _make_scenario(name="Test scenario", feature_name="My Feature", tags=None):
    scenario = MagicMock()
    scenario.name = name
    scenario.tags = list(tags or [])
    scenario.feature = MagicMock()
    scenario.feature.name = feature_name
    return scenario


def _tiny_png(path: Path) -> Path:
    img = Image.new("RGB", (100, 80), color=(200, 200, 200))
    img.save(str(path))
    return path


# ---------------------------------------------------------------------------
# writer.ScenarioResult
# ---------------------------------------------------------------------------

class TestScenarioResult:
    def test_init_sets_name_and_feature(self):
        from noodle.reporting.writer import ScenarioResult
        scenario = _make_scenario("Login test", "Auth Feature")
        sr = ScenarioResult(scenario)
        assert sr.result["name"] == "Login test"
        assert sr.result["fullName"] == "Auth Feature: Login test"
        assert sr.result["status"] == "passed"

    def test_add_step_passed_appends_entry(self):
        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_make_scenario())
        step = _make_step(status="passed")
        sr.add_step(step, "passed")
        assert len(sr.result["steps"]) == 1
        assert sr.result["steps"][0]["status"] == "passed"
        assert "When user clicks Login" in sr.result["steps"][0]["name"]

    def test_add_step_passed_with_warnings_still_recorded(self):
        # NOOD_0021 — a passed step can log a ⚠️ warning (ambiguous locator);
        # it must reach statusDetails.warnings, not be dropped just because
        # the step itself passed.
        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_make_scenario())
        step = _make_step(status="passed")
        sr.add_step(step, "passed", warnings=["Ambiguous locator 'X' — matched multiple elements"])
        assert sr.result["steps"][0]["statusDetails"]["warnings"] == \
            ["Ambiguous locator 'X' — matched multiple elements"]

    def test_add_step_failed_includes_status_details(self):
        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_make_scenario())
        step = _make_step(status="failed", exception=AssertionError("Element not found"), error_message="traceback here")
        sr.add_step(step, "failed")
        s = sr.result["steps"][0]
        assert s["status"] == "failed"
        assert "Element not found" in s["statusDetails"]["message"]

    def test_add_step_failed_with_attachment(self):
        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_make_scenario())
        step = _make_step(status="failed")
        sr.add_step(step, "failed", attachment_path="/tmp/FAILED_foo_annotated.png")
        s = sr.result["steps"][0]
        assert s["attachments"][0]["name"] == "failure_screenshot"
        assert s["attachments"][0]["source"] == "FAILED_foo_annotated.png"

    def test_finish_sets_status_passed_when_all_steps_pass(self):
        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_make_scenario())
        sr.add_step(_make_step(status="passed"), "passed")
        sr.finish(_make_scenario())
        assert sr.result["status"] == "passed"

    def test_finish_sets_status_failed_when_any_step_fails(self):
        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_make_scenario())
        sr.add_step(_make_step(status="passed"), "passed")
        sr.add_step(_make_step(status="failed", exception=AssertionError("oops")), "failed")
        sr.finish(_make_scenario())
        assert sr.result["status"] == "failed"

    def test_finish_propagates_failure_to_status_details(self):
        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_make_scenario())
        sr.add_step(_make_step(status="failed", exception=AssertionError("boom")), "failed")
        sr.finish(_make_scenario())
        assert "boom" in sr.result["statusDetails"]["message"]

    def test_finish_sets_stop_timestamp(self):
        import time

        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_make_scenario())
        before = int(time.time() * 1000)
        sr.finish(_make_scenario())
        assert sr.result["stop"] >= before


class TestWriteResult:
    def test_writes_valid_json_file(self, tmp_path, monkeypatch):
        from noodle.reporting import writer
        monkeypatch.setenv("NOODLE_RESULTS_DIR", str(tmp_path))

        sr = writer.ScenarioResult(_make_scenario("Demo", "Demo Feature"))
        sr.add_step(_make_step(), "passed")
        sr.finish(_make_scenario())
        written = writer.write_result(sr)

        assert written.exists()
        data = json.loads(written.read_text())
        assert data["name"] == "Demo"
        assert data["status"] == "passed"
        assert isinstance(data["steps"], list)

    def test_creates_allure_dir_if_missing(self, tmp_path, monkeypatch):
        from noodle.reporting import writer
        target = tmp_path / "new-allure-results"
        monkeypatch.setenv("NOODLE_RESULTS_DIR", str(target))

        sr = writer.ScenarioResult(_make_scenario())
        sr.finish(_make_scenario())
        writer.write_result(sr)

        assert target.is_dir()


# ---------------------------------------------------------------------------
# junit.write_junit
# ---------------------------------------------------------------------------

class TestWriteJunit:
    def _make_sr(self, name, feature, status, error_msg=None):
        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_make_scenario(name, feature))
        if status == "failed":
            sr.add_step(_make_step(status="failed", exception=AssertionError(error_msg or "fail")), "failed")
        else:
            sr.add_step(_make_step(status="passed"), "passed")
        sr.finish(_make_scenario(name, feature))
        return sr

    def test_writes_xml_file(self, tmp_path):
        from noodle.reporting import junit
        out = tmp_path / "junit.xml"
        sr = self._make_sr("Scenario A", "Feature X", "passed")
        junit.write_junit([sr], path=str(out))
        assert out.exists()

    def test_testsuite_counts(self, tmp_path):
        from noodle.reporting import junit
        out = tmp_path / "junit.xml"
        results = [
            self._make_sr("A", "F", "passed"),
            self._make_sr("B", "F", "failed", "not found"),
            self._make_sr("C", "F", "passed"),
        ]
        junit.write_junit(results, path=str(out))
        root = ET.parse(str(out)).getroot()
        assert root.attrib["tests"] == "3"
        assert root.attrib["failures"] == "1"

    def test_failed_testcase_has_failure_element(self, tmp_path):
        from noodle.reporting import junit
        out = tmp_path / "junit.xml"
        sr = self._make_sr("Bad test", "Bad Feature", "failed", "Element not found")
        junit.write_junit([sr], path=str(out))
        root = ET.parse(str(out)).getroot()
        tc = root.find("testcase")
        failure = tc.find("failure")
        assert failure is not None
        assert "Element not found" in failure.attrib["message"]

    def test_passed_testcase_has_no_failure_element(self, tmp_path):
        from noodle.reporting import junit
        out = tmp_path / "junit.xml"
        sr = self._make_sr("Good test", "Good Feature", "passed")
        junit.write_junit([sr], path=str(out))
        root = ET.parse(str(out)).getroot()
        tc = root.find("testcase")
        assert tc.find("failure") is None

    def test_classname_matches_feature(self, tmp_path):
        from noodle.reporting import junit
        out = tmp_path / "junit.xml"
        sr = self._make_sr("My test", "Checkout Feature", "passed")
        junit.write_junit([sr], path=str(out))
        root = ET.parse(str(out)).getroot()
        tc = root.find("testcase")
        assert tc.attrib["classname"] == "Checkout Feature"


# ---------------------------------------------------------------------------
# annotate
# ---------------------------------------------------------------------------

class TestAnnotate:
    def test_draw_not_found_creates_output(self, tmp_path):
        from noodle.reporting.annotate import draw_not_found
        src = tmp_path / "screenshot.png"
        _tiny_png(src)
        out = draw_not_found(str(src), "Login button")
        assert Path(out).exists()
        assert "_annotated" in out

    def test_draw_assertion_failure_creates_output(self, tmp_path):
        from noodle.reporting.annotate import draw_assertion_failure
        src = tmp_path / "screenshot.png"
        _tiny_png(src)
        out = draw_assertion_failure(str(src), "Expected visible")
        assert Path(out).exists()
        assert "_annotated" in out

    def test_draw_timeout_creates_output(self, tmp_path):
        from noodle.reporting.annotate import draw_timeout
        src = tmp_path / "screenshot.png"
        _tiny_png(src)
        out = draw_timeout(str(src), "Login button")
        assert Path(out).exists()
        assert "_annotated" in out

    def test_original_file_not_overwritten(self, tmp_path):
        from noodle.reporting.annotate import draw_not_found
        src = tmp_path / "screenshot.png"
        _tiny_png(src)
        original_size = src.stat().st_size
        draw_not_found(str(src), "label")
        assert src.stat().st_size == original_size
