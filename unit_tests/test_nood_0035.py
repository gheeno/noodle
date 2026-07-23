"""NOOD_0035 — declarative "browser resolution is set to ..." step, and the
target-architecture gap fixes (compressed failure screenshots, `noodle report
serve`)."""
import threading
import urllib.request
from types import SimpleNamespace
from unittest.mock import MagicMock

from noodle.orchestrator import runner
from noodle.reporting import builder
from noodle.resolver import patterns

# --- resolution step -------------------------------------------------------

def test_resolution_pattern():
    assert patterns.match("browser resolution is set to 1920x1080") == \
        ("set_viewport", {"width": 1920, "height": 1080})
    assert patterns.match('the browser resolution is set to "800x600"') == \
        ("set_viewport", {"width": 800, "height": 600})
    assert patterns.match("screen resolution is set to 1366x768") == \
        ("set_viewport", {"width": 1366, "height": 768})
    assert patterns.match("system resolution is set to 640 x 480") == \
        ("set_viewport", {"width": 640, "height": 480})


def test_resolution_step_dispatch():
    # behave strips the Given/When/Then keyword before the step text ever
    # reaches execute_step — same convention as test_set_viewport_dispatch.
    sizes = []
    page = SimpleNamespace(set_viewport_size=lambda s: sizes.append(s))
    ctx = SimpleNamespace(page=page, _vars={})
    runner.execute_step('browser resolution is set to "1920x1080"', ctx)
    assert sizes == [{"width": 1920, "height": 1080}]


def test_resolution_step_with_var_placeholder():
    # {var:X} is substituted before resolve() ever sees the sentence — same
    # path as the existing {env:X}/{var:X} viewport step.
    sizes = []
    page = SimpleNamespace(set_viewport_size=lambda s: sizes.append(s))
    ctx = SimpleNamespace(page=page, _vars={"RES": "1920x1080"})
    runner.execute_step('browser resolution is set to {var:RES}', ctx)
    assert sizes == [{"width": 1920, "height": 1080}]


# --- compressed / deduped failure screenshots -------------------------------

def test_failed_screenshot_raw_copy_is_removed_once_annotated(monkeypatch, tmp_path):
    """hooks.after_step used to leave both the raw PNG and the annotated PNG
    on disk even though only the annotated one is ever attached to the
    report — doubling storage for every failure. The raw copy should be
    removed once annotation succeeds."""
    from noodle import hooks
    from noodle.reporting import writer

    monkeypatch.setenv("NOODLE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(hooks, "_REPORTING", True)
    monkeypatch.setattr(
        hooks.locator_module, "mark_failure", lambda page: {}
    )

    def _fake_screenshot(path, full_page=True):
        from PIL import Image
        Image.new("RGB", (4, 4)).save(path)

    scenario = MagicMock()
    scenario.name = "S"
    scenario.feature.name = "F"
    scenario.tags = []
    context = MagicMock()
    context._allure_result = writer.ScenarioResult(scenario)
    context.page = SimpleNamespace(screenshot=_fake_screenshot)

    step = MagicMock()
    step.status = "failed"
    step.name = "clicks the missing button"
    step.error_message = "not found"

    hooks.after_step(context, step)

    shots_dir = tmp_path / "artifacts" / "screenshots"
    raw = shots_dir / "FAILED_clicks_the_missing_button.png"
    annotated = shots_dir / "FAILED_clicks_the_missing_button_annotated.png"
    assert not raw.exists()
    assert annotated.exists()


# --- noodle report serve -----------------------------------------------------

def test_report_serve_serves_the_built_html(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hi from the report</h1>")
    httpd = builder._make_server(str(tmp_path), host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=2)
        assert b"hi from the report" in resp.read()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
