"""NOOD_0024 — web pixel/OCR bridge: routing + coordinate correctness.

Pure logic runs everywhere (no tesseract binary). The one real-engine test
skips cleanly when tesseract isn't installed or the synthetic font defeats OCR.
"""
import pytest

from noodle.agents.visual import ocr, regions
from noodle.agents.web import screen
from noodle.resolver.patterns import match, normalize_subject


def _resolve(text):
    return match(normalize_subject(text))


# --- pattern routing --------------------------------------------------------

def test_type_text_no_locator():
    assert _resolve('User types "ls -la"') == ('type_text', {'text': 'ls -la'})
    assert _resolve('I enter "login admin"') == ('type_text', {'text': 'login admin'})


def test_type_into_field_still_fills():  # regression: must not shadow fill
    action, p = _resolve('User types "admin" into "username"')
    assert action == 'fill' and p == {'value': 'admin', 'locator': 'username'}


def test_click_at_coords():
    assert _resolve('User clicks at 640, 360') == ('click_at', {'x': 640, 'y': 360})
    assert _resolve('clicks at (10,20)') == ('click_at', {'x': 10, 'y': 20})


def test_click_on_text():
    assert _resolve('User clicks on the text "logout"') == ('click_text', {'text': 'logout'})


def test_screen_assertions():
    assert _resolve('the screen shows "access granted"') == ('assert_screen_text', {'text': 'access granted'})
    assert _resolve('the terminal displays "READY"') == ('assert_screen_text', {'text': 'READY'})
    assert _resolve('the screen should not show "error"') == ('assert_screen_text_hidden', {'text': 'error'})


def test_wait_screen_text():
    assert _resolve('User waits until the screen shows "done"') == ('wait_screen_text', {'text': 'done'})


def test_terminal_buffer_assert():
    assert _resolve('the terminal buffer contains "$ whoami"') == ('assert_buffer', {'text': '$ whoami'})


def test_focus_region():
    assert _resolve('User focuses on the "top-left" region') == ('focus_region', {'region': 'top-left'})


# --- coordinate correctness (pure) ------------------------------------------

class _FakePage:
    def __init__(self, dpr):
        self._dpr = dpr

    def evaluate(self, _):
        return self._dpr


def test_to_css_scaling():
    assert screen._to_css(200, 100, 2.0) == (100, 50)
    assert screen._to_css(50, 50, 1.0) == (50, 50)


def test_device_region_scales_css_to_device():
    screen.set_region({"x": 10, "y": 20, "width": 100, "height": 50})
    try:
        assert screen._device_region(_FakePage(2.0)) == {"x": 20, "y": 40, "width": 200, "height": 100}
        assert screen._device_region(_FakePage(1.0)) == {"x": 10, "y": 20, "width": 100, "height": 50}
    finally:
        screen.set_region(None)


def test_device_region_none_when_unset():
    screen.set_region(None)
    assert screen._device_region(_FakePage(2.0)) is None


# --- OCR pure logic (no tesseract binary) -----------------------------------

def test_pick_word_centroid():
    data = {"text": ["", "ACCESS", "GRANTED"], "conf": [-1, 95, 90],
            "left": [0, 100, 200], "top": [0, 50, 50],
            "width": [0, 80, 90], "height": [0, 20, 20]}
    assert ocr._pick_word(data, "access") == (140, 60)   # 100+80//2, 50+20//2
    assert ocr._pick_word(data, "missing") is None


def test_crop_offset():
    from PIL import Image
    img = Image.new("RGB", (200, 200))
    cropped, offset = ocr._crop(img, {"x": 10, "y": 20, "width": 50, "height": 60})
    assert cropped.size == (50, 60) and offset == (10, 20)
    assert ocr._crop(img, None)[1] == (0, 0)


def test_find_text_in_image_adds_region_offset(monkeypatch):
    # find_text_in_image must add the crop offset back so coords are full-image.
    from PIL import Image
    monkeypatch.setattr(ocr, "_tesseract", lambda: type("T", (), {
        "image_to_data": staticmethod(lambda img, output_type=None: {
            "text": ["HIT"], "conf": [90], "left": [4], "top": [6],
            "width": [10], "height": [10]}),
        "Output": type("O", (), {"DICT": None}),
    }))
    img = Image.new("RGB", (200, 200))
    # crop offset (50,60) + centroid (4+5, 6+5) = (59, 71)
    assert ocr.find_text_in_image(img, "HIT", {"x": 50, "y": 60, "width": 80, "height": 80}) == (59, 71)


# --- region parser with explicit size ---------------------------------------

def test_parse_region_with_viewport_size():
    assert regions.parse_region("top-left", (1280, 720)) == {"x": 0, "y": 0, "width": 640, "height": 360}
    assert regions.parse_region("10,20,30,40", (1280, 720)) == {"x": 10, "y": 20, "width": 30, "height": 40}


# --- OCR engine (needs tesseract; skips cleanly otherwise) -------------------

def test_find_text_in_image_real_engine():
    pytesseract = pytest.importorskip("pytesseract")
    from PIL import Image, ImageDraw, ImageFont
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        pytest.skip("tesseract binary not installed")

    img = Image.new("RGB", (480, 140), "black")
    try:
        font = ImageFont.load_default(size=48)
    except TypeError:
        font = ImageFont.load_default()
    ImageDraw.Draw(img).text((20, 40), "ACCESS GRANTED", fill="#00ff00", font=font)

    text = ocr.find_all_text_in_image(img).upper()
    if "ACCESS" not in text:
        pytest.skip("synthetic font not OCR-legible in this environment")
    assert "GRANTED" in text
    assert ocr.find_text_in_image(img, "ACCESS") is not None


# --- navigate: portable local-fixture resolution ----------------------------

class _GotoPage:
    def __init__(self): self.url = None
    def goto(self, url, **kw): self.url = url


def test_navigate_resolves_local_html_to_file_uri(tmp_path):
    from noodle.agents.web import actions
    f = tmp_path / "app.html"
    f.write_text("<html></html>")
    page = _GotoPage()
    actions.navigate(page, str(f))
    assert page.url.startswith("file://") and page.url.endswith("/app.html")


def test_navigate_leaves_http_urls_untouched():
    from noodle.agents.web import actions
    page = _GotoPage()
    actions.navigate(page, "https://example.com")
    assert page.url == "https://example.com"
