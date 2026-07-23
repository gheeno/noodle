"""NOOD_0155 — the performance wok's screenshot capability.

Other woks screenshot the system under test; a load test has no screen, so
its evidence image is a rendered latency-over-time chart (Pillow — already a
core dependency). Saved into the run's screenshots dir, it flows through the
same failure/evidence attachment pipeline into Allure and RCA as any page
screenshot would.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

from noodle.agents.perf.loadgen import LoadResult

_W, _H = 900, 420
_MARGIN = 60
_BG, _FG, _GRID = (255, 255, 255), (30, 30, 30), (225, 225, 225)
_OK, _ERR, _P95 = (46, 125, 50), (198, 40, 40), (21, 101, 192)


def render(result: LoadResult, path: str) -> str:
    """Render `result` to a PNG at `path` (parent dirs created). Returns path."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    img = Image.new("RGB", (_W, _H), _BG)
    d = ImageDraw.Draw(img)

    plot_w, plot_h = _W - 2 * _MARGIN, _H - 2 * _MARGIN
    x0, y0 = _MARGIN, _H - _MARGIN                      # axes origin (bottom-left)
    max_ms = max(result.max_ms, 1.0)
    max_s = max(result.duration_s, max((s.offset_s for s in result.samples), default=0.0), 0.001)

    # ASCII only — Pillow's built-in bitmap font draws em-dashes as boxes.
    d.text((_MARGIN, 12), f"Noodle load test - {result.url}", fill=_FG)
    d.text((_MARGIN, 28), result.summary().replace("—", "-"), fill=_FG)

    # Frame + horizontal gridlines with ms labels
    d.rectangle([x0, _MARGIN, x0 + plot_w, y0], outline=_FG)
    for i in range(1, 5):
        gy = y0 - plot_h * i // 5
        d.line([x0, gy, x0 + plot_w, gy], fill=_GRID)
        d.text((6, gy - 6), f"{max_ms * i / 5:.0f}ms", fill=_FG)

    # p95 reference line
    p95 = result.percentile_ms(95)
    py = y0 - int(min(p95 / max_ms, 1.0) * plot_h)
    d.line([x0, py, x0 + plot_w, py], fill=_P95, width=2)
    d.text((x0 + plot_w - 90, max(py - 14, _MARGIN)), f"p95 {p95:.0f}ms", fill=_P95)

    # Samples: green dot = ok, red = error
    for s in result.samples:
        sx = x0 + int(min(s.offset_s / max_s, 1.0) * plot_w)
        sy = y0 - int(min(s.ms / max_ms, 1.0) * plot_h)
        color = _OK if s.ok else _ERR
        d.ellipse([sx - 2, sy - 2, sx + 2, sy + 2], fill=color)

    d.text((x0, y0 + 8), "0s", fill=_FG)
    d.text((x0 + plot_w - 30, y0 + 8), f"{max_s:.1f}s", fill=_FG)

    img.save(path, "PNG")
    return path
