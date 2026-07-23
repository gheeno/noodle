"""Screen region parsing — named regions and raw x,y,w,h coordinates."""


def parse_region(region_str: str, size: tuple | None = None) -> dict:
    """
    Convert a region string to {x, y, width, height} in pixels.
    Named regions: top-left, top-right, bottom-left, bottom-right, center.
    Raw form: "x,y,width,height" (integers, comma-separated).

    `size` (width, height) is the coordinate space — the web agent passes the
    browser viewport; default is the OS screen (desktop agent).
    """
    sw, sh = size if size else _screen_size()
    half_w, half_h = sw // 2, sh // 2

    named = {
        "top-left":     {"x": 0,      "y": 0,      "width": half_w, "height": half_h},
        "top-right":    {"x": half_w,  "y": 0,      "width": half_w, "height": half_h},
        "bottom-left":  {"x": 0,      "y": half_h,  "width": half_w, "height": half_h},
        "bottom-right": {"x": half_w,  "y": half_h,  "width": half_w, "height": half_h},
        "center":       {"x": sw // 4, "y": sh // 4, "width": half_w, "height": half_h},
    }

    key = region_str.strip().lower()
    if key in named:
        return named[key]

    parts = [p.strip() for p in region_str.split(",")]
    if len(parts) == 4:
        try:
            x, y, w, h = (int(p) for p in parts)
            return {"x": x, "y": y, "width": w, "height": h}
        except ValueError:
            pass

    raise ValueError(
        f"Unknown region '{region_str}'. "
        "Use a named region (top-left, top-right, bottom-left, bottom-right, center) "
        "or 'x,y,width,height'."
    )


def _screen_size() -> tuple[int, int]:
    try:
        import mss
        with mss.mss() as sct:
            m = sct.monitors[1]
            return m["width"], m["height"]
    except ImportError:
        pass
    try:
        import pyautogui
        return pyautogui.size()
    except ImportError:
        pass
    return 1920, 1080  # safe fallback
