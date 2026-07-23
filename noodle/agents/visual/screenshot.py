"""Full-screen capture. Prefers mss (fast, headless-friendly); falls back to PIL ImageGrab."""
import tempfile


def capture() -> tuple:
    """Return (PIL.Image, temp_path_str). Caller owns the temp file."""
    try:
        import mss
        import mss.tools
        from PIL import Image
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    except ImportError:
        from PIL import ImageGrab
        img = ImageGrab.grab()

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name)
    return img, tmp.name
