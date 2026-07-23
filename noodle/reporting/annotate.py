from pathlib import Path

from PIL import Image, ImageDraw


def _annotated_path(img_path: str) -> str:
    p = Path(img_path)
    return str(p.with_stem(p.stem + "_annotated"))


def draw_not_found(img_path: str, label: str) -> str:
    """Red dashed border around the full image with label text at top-left."""
    img = Image.open(img_path).convert("RGBA")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    # Dashed red border — draw in segments
    dash, gap, thickness = 12, 6, 3
    for side in ["top", "bottom", "left", "right"]:
        if side == "top":
            coords = [(x, 0, x + dash, thickness) for x in range(0, w, dash + gap)]
        elif side == "bottom":
            coords = [(x, h - thickness, x + dash, h) for x in range(0, w, dash + gap)]
        elif side == "left":
            coords = [(0, y, thickness, y + dash) for y in range(0, h, dash + gap)]
        else:
            coords = [(w - thickness, y, w, y + dash) for y in range(0, h, dash + gap)]
        for box in coords:
            draw.rectangle(box, fill="red")
    draw.text((8, 8), f"NOT FOUND: {label}", fill="red")
    out = _annotated_path(img_path)
    img.save(out, optimize=True)
    return out


def draw_failure_markers(img_path: str, label: str, marked: dict) -> str:
    """Corner legend for the in-page outlines drawn by locator.mark_failure —
    colour swatches only (red = matched, green = expected per pom.yaml)."""
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    x, y = 8, 8
    draw.text((x, y), f"FAILED: {label}", fill="red")
    y += 18
    if marked.get("matched"):
        draw.rectangle([x, y, x + 12, y + 12], outline="red", width=3)
        draw.text((x + 20, y), "matched element", fill="red")
        y += 18
    if marked.get("expected"):
        draw.rectangle([x, y, x + 12, y + 12], outline="green", width=3)
        draw.text((x + 20, y), "expected (pom.yaml)", fill="green")
    out = _annotated_path(img_path)
    img.save(out, optimize=True)
    return out


def draw_evidence(img_path: str, label: str, box: dict,
                  viewport: dict | None = None) -> str:
    """NOOD_0153 — green box around the element the passed step resolved, on
    the viewport evidence shot, with the step text top-left. Edits in place
    (evidence has no raw/annotated pair — the annotated file IS the evidence).
    `box` is Playwright's viewport-relative CSS-pixel bounding box; `viewport`
    lets us rescale when the image isn't 1:1 CSS pixels (device presets)."""
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    scale = 1.0
    if viewport and viewport.get("width"):
        scale = img.width / viewport["width"]
    x0 = box["x"] * scale
    y0 = box["y"] * scale
    x1 = (box["x"] + box["width"]) * scale
    y1 = (box["y"] + box["height"]) * scale
    # Clamp to the image — a half-scrolled element still gets its visible part
    # outlined instead of PIL erroring on out-of-bounds coordinates.
    x0, y0 = max(0, min(x0, img.width - 1)), max(0, min(y0, img.height - 1))
    x1, y1 = max(x0 + 1, min(x1, img.width - 1)), max(y0 + 1, min(y1, img.height - 1))
    draw.rectangle([x0, y0, x1, y1], outline="#1a7f37", width=4)
    draw.text((8, 8), f"EVIDENCE: {label}", fill="#1a7f37")
    img.save(img_path, quality=80, optimize=True)
    return img_path


def draw_assertion_failure(img_path: str, label: str) -> str:
    """Semi-transparent yellow overlay with red ✗ and label text."""
    img = Image.open(img_path).convert("RGBA")
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (255, 255, 0, 60))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    draw.text((8, 8), f"✗ ASSERTION FAILED: {label}", fill="red")
    out = _annotated_path(img_path)
    img.convert("RGB").save(out, optimize=True)
    return out


def draw_timeout(img_path: str, label: str) -> str:
    """Orange TIMEOUT text overlay."""
    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.text((8, 8), f"TIMEOUT: {label}", fill="orange")
    out = _annotated_path(img_path)
    img.save(out, optimize=True)
    return out
