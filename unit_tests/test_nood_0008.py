"""NOOD_0008 — gap-closure tests (report correctness, POM-first locator,
summary de-dup, dialog/upload/download/multi-select patterns, POM scoping,
failure markers). No browser, no allure binary."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from noodle.resolver.patterns import match, normalize_subject


def _resolve(text):
    return match(normalize_subject(text))


# ---------------------------------------------------------------------------
# Phase 0 — writer: historyId, suite labels, attachment copy
# ---------------------------------------------------------------------------

def _scenario(name="Login test", feature="Auth Feature", filename=None):
    s = MagicMock()
    s.name = name
    s.tags = []
    s.feature = MagicMock()
    s.feature.name = feature
    s.feature.filename = filename
    return s


class TestWriterLabels:
    def test_history_id_stable_across_attempts(self):
        from noodle.reporting.writer import ScenarioResult
        a = ScenarioResult(_scenario())
        b = ScenarioResult(_scenario())
        assert a.result["historyId"] == b.result["historyId"]
        assert a.uuid != b.uuid

    def test_suite_labels_present(self):
        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_scenario(filename="tests/web/qaplayground/features/x.feature"))
        labels = {lab["name"]: lab["value"] for lab in sr.result["labels"]}
        assert labels["suite"] == "Auth Feature"
        assert labels["parentSuite"] == "qaplayground"

    def test_parent_suite_falls_back_when_not_nested_under_features(self):
        """A .feature file not inside a features/ subfolder (non-standard
        layout) still gets a parentSuite — the immediate parent folder name."""
        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_scenario(filename="somewhere/qaplayground/x.feature"))
        labels = {lab["name"]: lab["value"] for lab in sr.result["labels"]}
        assert labels["parentSuite"] == "qaplayground"

    def test_no_parent_suite_without_filename(self):
        from noodle.reporting.writer import ScenarioResult
        sr = ScenarioResult(_scenario(filename=None))
        names = [lab["name"] for lab in sr.result["labels"]]
        assert "parentSuite" not in names

    def test_attachment_copied_into_results_dir(self, tmp_path, monkeypatch):
        from noodle.reporting.writer import ScenarioResult
        monkeypatch.setenv("NOODLE_RESULTS_DIR", str(tmp_path / "res"))
        shot = tmp_path / "FAILED_x_annotated.png"
        Image.new("RGB", (10, 10)).save(str(shot))

        sr = ScenarioResult(_scenario())
        step = MagicMock()
        step.keyword, step.name, step.exception, step.error_message = "When", "x", None, ""
        sr.add_step(step, "failed", attachment_path=str(shot))

        source = sr.result["steps"][0]["attachments"][0]["source"]
        assert source.endswith("-attachment.png")
        assert (tmp_path / "res" / source).is_file()


# ---------------------------------------------------------------------------
# Phase 1 — POM entry beats the accessibility scan in find()
# ---------------------------------------------------------------------------

class TestPomFirst:
    def test_pom_wins_over_unique_accessibility_match(self, monkeypatch):
        from noodle.agents.web import locator, pom
        sentinel = object()
        monkeypatch.setattr(pom, "locate", lambda page, text: sentinel)
        page = MagicMock()
        assert locator.find(page, "movie name") is sentinel
        page.get_by_role.assert_not_called()   # scan never consulted

    def test_scoped_lookup_skips_pom(self, monkeypatch):
        from noodle.agents.web import locator, pom
        monkeypatch.setattr(
            pom, "locate",
            lambda page, text: pytest.fail("POM must not be used for scoped lookups"))
        scope = MagicMock()
        unique = MagicMock()
        unique.count.return_value = 1
        scope.get_by_role.return_value = unique
        loc = locator.find(MagicMock(), "delete", scope=scope)
        assert loc is unique.first


# ---------------------------------------------------------------------------
# Phase 2 — summary de-dups auto-retry attempts
# ---------------------------------------------------------------------------

def test_summary_dedupes_retries_keeping_last_attempt(tmp_path):
    from noodle.reporting import summary
    d = tmp_path / "allure-results"
    d.mkdir()
    common = {"historyId": "abc", "fullName": "F: retry me",
              "name": "retry me", "labels": [], "steps": []}
    (d / "a-result.json").write_text(json.dumps(
        {**common, "status": "failed", "start": 1000, "stop": 2000}))
    (d / "b-result.json").write_text(json.dumps(
        {**common, "status": "passed", "start": 2000, "stop": 3000}))
    s = summary.collect(str(d))
    assert s["passed"] == 1 and s["failed"] == 0


# ---------------------------------------------------------------------------
# Phases 3–5 + 7 — new step patterns
# ---------------------------------------------------------------------------

class TestNewPatterns:
    def test_accept_alert_arms_handler(self):
        assert _resolve("User accepts the next alert") == (
            "arm_dialog", {"response": "accept", "answer": None})
        assert _resolve("User accepts the alert") == (
            "arm_dialog", {"response": "accept", "answer": None})

    def test_dismiss_confirm(self):
        assert _resolve("User dismisses the next confirm") == (
            "arm_dialog", {"response": "dismiss", "answer": None})

    def test_answer_prompt_does_not_route_to_fill(self):
        assert _resolve('User types "noodle" into the prompt and accepts it') == (
            "arm_dialog", {"response": "accept", "answer": "noodle"})
        assert _resolve('User answers "noodle" into the next prompt') == (
            "arm_dialog", {"response": "accept", "answer": "noodle"})

    def test_dialog_text_assert(self):
        assert _resolve('the alert should say "Hello"') == (
            "assert_dialog_text", {"text": "Hello"})

    def test_upload(self):
        assert _resolve('User uploads "test-data/sample.txt" to the file upload input') == (
            "upload", {"path": "test-data/sample.txt", "locator": "file upload input"})

    def test_download_assert(self):
        assert _resolve("a file should be downloaded") == ("assert_download", {"name": None})
        assert _resolve('a file "report.pdf" should be downloaded') == (
            "assert_download", {"name": "report.pdf"})

    def test_multi_select_list_parsed(self):
        assert _resolve('User selects "Banana" and "Mango" from the fruit multi select') == (
            "select_multi", {"values": ["Banana", "Mango"], "locator": "fruit multi select"})
        assert _resolve('User selects "A", "B" and "C" in the list') == (
            "select_multi", {"values": ["A", "B", "C"], "locator": "list"})

    def test_single_select_still_works(self):
        assert _resolve('User selects "India" from the country-select') == (
            "select", {"value": "India", "locator": "country-select"})

    def test_radio_alias(self):
        assert _resolve('User selects the "yes-radio" radio') == (
            "check", {"locator": "yes-radio"})

    def test_empty_value_assert(self):
        assert _resolve('the "first-name-input" should have value ""') == (
            "assert_value", {"locator": "first-name-input", "value": ""})


def test_assert_value_empty_requires_empty(monkeypatch):
    from noodle.agents.web import actions
    monkeypatch.setattr(actions, "get_text", lambda page, t: "leftover")
    page = MagicMock()
    page.url = "http://x"
    with pytest.raises(AssertionError, match="to be empty"):
        actions.assert_value(page, "field", "")
    monkeypatch.setattr(actions, "get_text", lambda page, t: "")
    actions.assert_value(page, "field", "")   # must not raise


def test_assert_value_not(monkeypatch):
    # NOOD_0021 — negated mirror: raises when the value IS present, passes otherwise.
    from noodle.agents.web import actions
    page = MagicMock()
    page.url = "http://x"
    monkeypatch.setattr(actions, "get_text", lambda page, t: "undefined min")
    with pytest.raises(AssertionError, match="to NOT contain"):
        actions.assert_value_not(page, "trailer runtime", "undefined")
    monkeypatch.setattr(actions, "get_text", lambda page, t: "107 min")
    actions.assert_value_not(page, "trailer runtime", "undefined")   # must not raise


# ---------------------------------------------------------------------------
# Phase 6 — POM per-page files default to filename scope; shadowing warns
# ---------------------------------------------------------------------------

class TestPomScoping:
    def test_matchless_file_defaults_to_filename_scope(self):
        from noodle.agents.web.pom import _wrap_page
        wrapped = _wrap_page("forms", {"terms-box": {"id": "terms"}})
        block = wrapped["pages"]["forms"]
        assert block["match"] == {"url_contains": "forms"}

    def test_explicit_empty_match_stays_folder_global(self):
        from noodle.agents.web.pom import _wrap_page
        wrapped = _wrap_page("shared_stuff", {"match": {}, "logo": {"id": "logo"}})
        assert wrapped == {"logo": {"id": "logo"}}

    def test_shadowed_key_warns(self, tmp_path, capsys):
        from noodle.agents.web import pom
        # pageobjects/ lives under the app's resources/, sibling of the
        # features/ folder the .feature file (and _feature_dir) is in.
        pod = tmp_path / "resources" / "pageobjects"
        pod.mkdir(parents=True)
        # folder-global (explicit match:{}) defines the same key as a sibling
        (pod / "a_pom.yaml").write_text("match: {}\nterms-box: {id: a}\n")
        (pod / "b_pom.yaml").write_text(
            "match: {url_contains: /b}\nterms-box: {id: b}\n")
        pom.set_context(str(tmp_path / "features"))
        pom._load_yaml.cache_clear()
        pom._warned_dirs.clear()
        try:
            pom._load_pom_chain()
        finally:
            pom.set_context(None)
        assert "shadow" in capsys.readouterr().out.lower()


# ---------------------------------------------------------------------------
# Phase 8 — failure markers
# ---------------------------------------------------------------------------

class TestFailureMarkers:
    def test_mark_failure_outlines_matched_and_pom_expected(self, monkeypatch):
        from noodle.agents.web import locator, pom
        matched = MagicMock()
        expected = MagicMock()
        expected.evaluate.side_effect = [False, None]   # not same element → outline
        monkeypatch.setattr(locator, "_last_match", ("movie name", matched))
        monkeypatch.setattr(pom, "locate", lambda page, text: expected)
        marked = locator.mark_failure(MagicMock())
        assert marked == {"matched": True, "expected": True}
        assert "red" in matched.evaluate.call_args[0][0]
        assert "green" in expected.evaluate.call_args[0][0]

    def test_mark_failure_without_find_is_noop(self, monkeypatch):
        from noodle.agents.web import locator
        monkeypatch.setattr(locator, "_last_match", None)
        assert locator.mark_failure(MagicMock()) == {"matched": False, "expected": False}

    def test_draw_failure_markers_writes_annotated_file(self, tmp_path):
        from noodle.reporting.annotate import draw_failure_markers
        src = tmp_path / "shot.png"
        Image.new("RGB", (200, 120), (240, 240, 240)).save(str(src))
        out = draw_failure_markers(str(src), "fill movie name",
                                   {"matched": True, "expected": True})
        assert Path(out).exists() and "_annotated" in out


# ---------------------------------------------------------------------------
# Phase 0 — junit stays out of allure-results
# ---------------------------------------------------------------------------

def test_junit_default_path_is_reports(tmp_path, monkeypatch):
    from noodle.reporting import junit
    monkeypatch.chdir(tmp_path)
    assert str(junit.write_junit([])) == "artifacts/reports/junit.xml"
    assert str(junit.merge_junits([])) == "artifacts/reports/junit.xml"
