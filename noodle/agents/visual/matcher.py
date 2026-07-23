"""OpenCV template matching with scale variants and annotated failure screenshots."""
import os
from pathlib import Path

from noodle.reporting import paths as _paths

from .screenshot import capture

_SCALES = (1.0, 0.8, 0.9, 1.1, 1.2)

# Phase G4 — the scale that last matched this session. One DPI/resolution
# combo rarely changes mid-run, so trying the winner first makes every
# subsequent lookup a single-pass match.
_last_scale: float | None = None


def _scale_order() -> tuple:
    if _last_scale is None or _last_scale not in _SCALES:
        return _SCALES
    return (_last_scale, *(s for s in _SCALES if s != _last_scale))


def _cv2():
    try:
        import cv2
        return cv2
    except ImportError:
        raise ImportError("Visual agent requires OpenCV: pip install noodle[visual]")


def _np():
    try:
        import numpy as np
        return np
    except ImportError:
        raise ImportError("Visual agent requires numpy: pip install noodle[visual]")


def find_on_screen(template_path: str, confidence: float = 0.85) -> tuple[int, int] | None:
    """
    Return (x, y) center of best match on screen, or None.
    Tries 5 scales; saves an annotated failure screenshot if nothing exceeds confidence.
    """
    cv2 = _cv2()
    np = _np()

    template_img = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template_img is None:
        raise FileNotFoundError(f"Template not found: {template_path}")

    screen_pil, screen_path = capture()
    screen_img = cv2.cvtColor(np.array(screen_pil), cv2.COLOR_RGB2BGR)

    best_score = -1.0
    best_loc = None
    best_tw, best_th = template_img.shape[1], template_img.shape[0]

    for scale in _scale_order():
        if scale == 1.0:
            tmpl = template_img
        else:
            w = max(1, int(template_img.shape[1] * scale))
            h = max(1, int(template_img.shape[0] * scale))
            tmpl = cv2.resize(template_img, (w, h))

        if tmpl.shape[0] > screen_img.shape[0] or tmpl.shape[1] > screen_img.shape[1]:
            continue

        result = cv2.matchTemplate(screen_img, tmpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val > best_score:
            best_score = max_val
            best_loc = max_loc
            best_tw, best_th = tmpl.shape[1], tmpl.shape[0]

        if max_val >= confidence:
            global _last_scale
            _last_scale = scale            # G4 — try the winner first next time
            cx = max_loc[0] + tmpl.shape[1] // 2
            cy = max_loc[1] + tmpl.shape[0] // 2
            try:
                os.unlink(screen_path)
            except OSError:
                pass
            return cx, cy

    # No match — save annotated screenshot
    _save_annotated_failure(screen_pil, best_loc, best_tw, best_th, best_score, template_path)
    try:
        os.unlink(screen_path)
    except OSError:
        pass
    return None


def _save_annotated_failure(screen_pil, loc, tw, th, score, template_path):
    from PIL import ImageDraw
    img = screen_pil.copy()
    draw = ImageDraw.Draw(img)
    if loc:
        x, y = loc
        draw.rectangle([x, y, x + tw, y + th], outline="red", width=3)
        draw.text((x, max(0, y - 20)), f"best={score:.2f} ({Path(template_path).name})", fill="red")
    shots_dir = _paths.screenshots_dir()
    os.makedirs(shots_dir, exist_ok=True)
    out = str(shots_dir / f"NOMATCH_{Path(template_path).stem}.png")
    img.save(out)
