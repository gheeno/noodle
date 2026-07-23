"""Vision LLM fallback: describe an element, get back (x, y) coords."""
import json
import os

from .screenshot import capture


def _model_configured() -> bool:
    return bool(os.getenv("NOODLE_VISION_MODEL") or os.getenv("NOODLE_MODEL"))


def _png_b64(image) -> str:
    import base64
    import io
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def locate_by_description(description: str, image=None) -> tuple[int, int] | None:
    """Return (x, y) from vision LLM, or None if no model is configured or the
    parse fails. `image` (PIL) is the frame to search — the web agent passes a
    browser screenshot; default captures the OS screen (desktop agent)."""
    if not _model_configured():
        return None

    from noodle.llm.client import ask_vision

    if image is None:
        image, _ = capture()

    prompt = (
        f'In this screenshot, where is: "{description}"? '
        'Reply with {"x": <int>, "y": <int>} only — the pixel coordinates of the center.'
    )
    raw = ask_vision(prompt=prompt, image_b64=_png_b64(image))

    try:
        data = json.loads(raw.strip())
        return int(data["x"]), int(data["y"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def image_matches(description: str, image=None) -> bool | None:
    """NOOD_0114 — YES/NO object check: do the pixels show `description`?
    None when no vision model is configured (caller decides how loud to be).
    Nondeterministic by nature — feature files using it carry @potential-flake."""
    if not _model_configured():
        return None

    from noodle.llm.client import ask_vision

    if image is None:
        image, _ = capture()

    prompt = (
        f'Does this image show: "{description}"? '
        'Answer with exactly YES or NO.'
    )
    raw = ask_vision(prompt=prompt, image_b64=_png_b64(image))
    return raw.strip().upper().startswith("YES")
