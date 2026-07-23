"""Tesseract OCR — find text in a PIL image or on the OS screen.

Source-agnostic: the web agent passes a browser screenshot, the desktop agent
passes an mss screen grab. All coordinates returned are image-pixel space.
"""
from .screenshot import capture


def _tesseract():
    try:
        import pytesseract
        return pytesseract
    except ImportError:
        raise ImportError(
            "OCR requires pytesseract + the tesseract binary: "
            "pip install 'noodle[visual]' (and install the tesseract engine)"
        )


def _preprocess(img):
    """Grayscale + contrast boost — improves OCR on UI/terminal text."""
    from PIL import ImageEnhance
    gray = img.convert("L")
    return ImageEnhance.Contrast(gray).enhance(2.0)


def _crop(img, region):
    """Crop to region {x,y,width,height}; return (cropped, (offset_x, offset_y))."""
    if not region:
        return img, (0, 0)
    x, y = region["x"], region["y"]
    return img.crop((x, y, x + region["width"], y + region["height"])), (x, y)


def _pick_word(data, needle):
    """Pure: from a tesseract image_to_data dict, return (x,y) centroid of the
    first word matching `needle`, or None. Image-pixel coords. Unit-testable
    with a hand-built dict — no tesseract binary needed."""
    needle = needle.strip().lower()
    for i in range(len(data["text"])):
        word = (data["text"][i] or "").strip().lower()
        if not word or int(data["conf"][i]) < 0:
            continue
        if needle in word or word in needle:
            x = data["left"][i] + data["width"][i] // 2
            y = data["top"][i] + data["height"][i] // 2
            return x, y
    return None


def _pick_phrase(data, needle):
    """Pure (Phase G2): multi-word phrase matching. Tesseract returns 'Save'
    and 'As' as separate boxes on one line — group words by
    (block_num, par_num, line_num), join each line with spaces, search the
    needle case-insensitively, and return the (x,y) centroid of the union
    bounding box of the matched word range. None when no line contains it.
    Unit-testable with a hand-built dict — no tesseract binary needed."""
    needle = " ".join(needle.strip().lower().split())
    if not needle:
        return None
    # line key -> list of (word, left, top, width, height), in reading order
    lines: dict = {}
    for i in range(len(data["text"])):
        word = (data["text"][i] or "").strip()
        if not word or int(data["conf"][i]) < 0:
            continue
        key = (data.get("block_num", [0] * len(data["text"]))[i],
               data.get("par_num", [0] * len(data["text"]))[i],
               data.get("line_num", [0] * len(data["text"]))[i])
        lines.setdefault(key, []).append(
            (word, data["left"][i], data["top"][i], data["width"][i], data["height"][i]))
    for words in lines.values():
        joined = " ".join(w[0].lower() for w in words)
        pos = joined.find(needle)
        if pos < 0:
            continue
        # Map the character span back to word indexes within the line.
        start_idx = joined[:pos].count(" ")
        end_idx = joined[:pos + len(needle)].count(" ")
        span = words[start_idx:end_idx + 1]
        x1 = min(w[1] for w in span)
        y1 = min(w[2] for w in span)
        x2 = max(w[1] + w[3] for w in span)
        y2 = max(w[2] + w[4] for w in span)
        return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def find_text_in_image(img, text, region=None):
    """Return (x,y) centroid of `text` in `img` (full-image pixel coords), or
    None. `region` ({x,y,width,height}) narrows the search first. Phrases
    spanning several OCR word boxes match via line grouping (G2); the fuzzy
    single-word match stays as a fallback for partial OCR reads."""
    pytesseract = _tesseract()
    cropped, (ox, oy) = _crop(img, region)
    data = pytesseract.image_to_data(_preprocess(cropped), output_type=pytesseract.Output.DICT)
    hit = _pick_phrase(data, text) or _pick_word(data, text)
    return (hit[0] + ox, hit[1] + oy) if hit else None


def find_all_text_in_image(img, region=None):
    """All recognised text in `img` as one string — for phrase/buffer asserts
    that span multiple word tokens."""
    pytesseract = _tesseract()
    cropped, _ = _crop(img, region)
    return pytesseract.image_to_string(_preprocess(cropped))


def find_text_on_screen(text):
    """Desktop path: OCR the OS screen. Returns screen-pixel (x,y) or None."""
    screen_pil, _ = capture()
    return find_text_in_image(screen_pil, text)
