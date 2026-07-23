"""Unit tests for visual matcher — mocks cv2 and screen capture, no real display."""
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build minimal numpy-like mocks without importing numpy
# ---------------------------------------------------------------------------

def _make_np_array(shape, value=0.0):
    """Return a mock that behaves like a tiny numpy array for our purposes."""
    arr = MagicMock()
    arr.shape = shape
    return arr


def _make_cv2_mock(max_val=0.95, max_loc=(10, 20)):
    cv2 = MagicMock()
    cv2.IMREAD_COLOR = 1
    cv2.TM_CCOEFF_NORMED = 5
    cv2.COLOR_RGB2BGR = 4

    # imread returns a mock image array
    img = _make_np_array((50, 80, 3))
    cv2.imread.return_value = img

    # resize returns a similarly-shaped mock
    cv2.resize.return_value = img

    # matchTemplate result: minMaxLoc returns (min, max, min_loc, max_loc)
    cv2.matchTemplate.return_value = MagicMock()
    cv2.minMaxLoc.return_value = (0.0, max_val, (0, 0), max_loc)

    # cvtColor returns a screen mock
    screen = _make_np_array((768, 1366, 3))
    cv2.cvtColor.return_value = screen

    return cv2


def _make_pil_image():
    from PIL import Image
    return Image.new("RGB", (1366, 768), color=(200, 200, 200))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFindOnScreen:
    def _run(self, cv2_mock, max_val=0.95, template_path="tests/assets/btn.png"):
        """Patch dependencies and call find_on_screen."""
        import numpy as np_real
        pil_img = _make_pil_image()

        with patch.dict("sys.modules", {"cv2": cv2_mock}):
            with patch("noodle.agents.visual.screenshot.capture",
                       return_value=(pil_img, "/tmp/screen.png")):
                with patch("noodle.agents.visual.matcher._np", return_value=np_real):
                    with patch("os.unlink"):
                        from importlib import reload

                        import noodle.agents.visual.matcher as mod
                        reload(mod)
                        return mod.find_on_screen(template_path, confidence=0.85)

    def test_returns_coords_when_match_exceeds_confidence(self):
        cv2 = _make_cv2_mock(max_val=0.95, max_loc=(10, 20))
        pil_img = _make_pil_image()

        with patch.dict("sys.modules", {"cv2": cv2}):
            with patch("noodle.agents.visual.screenshot.capture",
                       return_value=(pil_img, "/tmp/s.png")):
                with patch("os.unlink"):
                    from importlib import reload

                    import noodle.agents.visual.matcher as mod
                    reload(mod)
                    result = mod.find_on_screen("tests/assets/btn.png", confidence=0.85)

        assert result is not None
        assert isinstance(result[0], int)
        assert isinstance(result[1], int)

    def test_returns_none_when_no_match(self):
        cv2 = _make_cv2_mock(max_val=0.10, max_loc=(0, 0))
        pil_img = _make_pil_image()

        with patch.dict("sys.modules", {"cv2": cv2}):
            with patch("noodle.agents.visual.screenshot.capture",
                       return_value=(pil_img, "/tmp/s.png")):
                with patch("os.unlink"):
                    with patch("os.makedirs"):
                        from importlib import reload

                        import noodle.agents.visual.matcher as mod
                        reload(mod)
                        result = mod.find_on_screen("tests/assets/btn.png", confidence=0.85)

        assert result is None

    def test_scale_variants_attempted(self):
        cv2 = _make_cv2_mock(max_val=0.10)
        pil_img = _make_pil_image()

        with patch.dict("sys.modules", {"cv2": cv2}):
            with patch("noodle.agents.visual.screenshot.capture",
                       return_value=(pil_img, "/tmp/s.png")):
                with patch("os.unlink"):
                    with patch("os.makedirs"):
                        from importlib import reload

                        import noodle.agents.visual.matcher as mod
                        reload(mod)
                        mod.find_on_screen("tests/assets/btn.png", confidence=0.85)

        # resize called for the 4 non-1.0 scales
        assert cv2.resize.call_count >= 4

    def test_annotated_screenshot_saved_on_failure(self, tmp_path):
        cv2 = _make_cv2_mock(max_val=0.10, max_loc=(5, 5))
        pil_img = _make_pil_image()

        # Reload the module first so it picks up the cv2 mock, then patch
        # _save_annotated_failure on the already-loaded module object.
        from importlib import reload

        import noodle.agents.visual.matcher as mod

        with patch.dict("sys.modules", {"cv2": cv2}):
            with patch("noodle.agents.visual.screenshot.capture",
                       return_value=(pil_img, "/tmp/s.png")):
                with patch("os.unlink"):
                    with patch("os.makedirs"):
                        reload(mod)
                        with patch.object(mod, "_save_annotated_failure") as save_mock:
                            mod.find_on_screen("tests/assets/btn.png", confidence=0.85)

        save_mock.assert_called_once()

    def test_missing_template_raises(self):
        cv2 = MagicMock()
        cv2.imread.return_value = None  # file not found
        pil_img = _make_pil_image()

        with patch.dict("sys.modules", {"cv2": cv2}):
            with patch("noodle.agents.visual.screenshot.capture",
                       return_value=(pil_img, "/tmp/s.png")):
                from importlib import reload

                import noodle.agents.visual.matcher as mod
                reload(mod)
                with pytest.raises(FileNotFoundError):
                    mod.find_on_screen("nonexistent.png")
