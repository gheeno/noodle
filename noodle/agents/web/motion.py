"""Is this element actually moving, and which way? (NOOD_0210)

Marketing pages animate logos, banners and carousel strips constantly, and a
test could only ever assert such an element was *present* — a bouncing logo and
a frozen one are the same assertion. The give-away that a CSS check can't see:
on thebrief.ai the logo strip drifts 13.7px left over 1.25s while the <img>
itself reports `animationName: none` and no transform, because the motion is on
an ancestor.

So this samples `getBoundingClientRect()` over time instead of reading style.
That is mechanism-agnostic by construction — CSS keyframes, a transform on any
ancestor, a JS ticker, a marquee, scroll-linked motion all move the box, and
the box is what a human sees move.

Deterministic: no vision model, no OCR. The cost is wall-clock (one short
sampling window), not tokens.
"""
from __future__ import annotations

# Sub-pixel jitter happens on real pages (font loading, scrollbar reflow,
# subpixel layout). Below this a "move" is noise, not animation.
_NOISE_PX = 1.0
# An axis only counts as the direction of travel if it dominates the other —
# otherwise a diagonal drift would report as whichever axis won by 0.1px.
_DOMINANCE = 2.0

def _reversals(values: list[float]) -> int:
    """How many times the direction of travel flips along one axis.

    A steady drift has 0; a bounce/oscillation has 1+. Deltas under the noise
    floor are dropped first so jitter can't manufacture a reversal.
    """
    deltas = [b - a for a, b in zip(values, values[1:])
              if abs(b - a) > _NOISE_PX / 2]
    return sum(1 for a, b in zip(deltas, deltas[1:]) if a * b < 0)


def classify(xs: list[float], ys: list[float]) -> dict:
    """Turn two coordinate series into a verdict.

    Returns `direction` as one of: left to right / right to left /
    top to bottom / bottom to top / side to side / up and down / moving /
    (empty when still). `oscillating` marks bounce-style motion, which is the
    difference between "drifting left" and "bouncing side to side".
    """
    if len(xs) < 2:
        return {"moving": False, "direction": "", "dx": 0.0, "dy": 0.0,
                "oscillating": False, "travel_x": 0.0, "travel_y": 0.0}
    dx, dy = xs[-1] - xs[0], ys[-1] - ys[0]
    # Total path length, not net displacement: a full bounce cycle returns to
    # where it started (dx ~ 0) while having moved the whole time.
    travel_x = sum(abs(b - a) for a, b in zip(xs, xs[1:]))
    travel_y = sum(abs(b - a) for a, b in zip(ys, ys[1:]))
    osc_x, osc_y = _reversals(xs), _reversals(ys)
    moving = max(travel_x, travel_y) > _NOISE_PX
    out = {"moving": moving, "dx": round(dx, 1), "dy": round(dy, 1),
           "travel_x": round(travel_x, 1), "travel_y": round(travel_y, 1),
           "oscillating": bool(osc_x or osc_y), "direction": ""}
    if not moving:
        return out
    horizontal = travel_x > travel_y * _DOMINANCE
    vertical = travel_y > travel_x * _DOMINANCE
    if horizontal:
        out["direction"] = ("side to side" if osc_x else
                            "left to right" if dx > 0 else "right to left")
    elif vertical:
        out["direction"] = ("up and down" if osc_y else
                            "top to bottom" if dy > 0 else "bottom to top")
    else:
        out["direction"] = "moving"
    return out


def observe(page, target, samples: int = 8,
            interval_ms: int = 120) -> dict | None:
    """Watch one element's box for `samples` frames and classify its motion.

    `target` is a resolved Playwright Locator, so this rides on the engine's
    whole resolution stack — POM entries, accessible names, the NOOD_0210 image
    cue tier — instead of needing a hand-written CSS selector.

    Returns None when the element cannot be measured at all (never rendered,
    detached): the caller turns that into the "no such element" failure, which
    is a different report from "found it, but it is still".

    Default window is ~0.85s: long enough for a slow drift to clear the noise
    floor (thebrief.ai's strip moves ~2.7px per 250ms), short enough not to pad
    every run that uses the step.
    """
    xs: list[float] = []
    ys: list[float] = []
    for n in range(samples):
        if n:
            page.wait_for_timeout(interval_ms)
        try:
            box = target.bounding_box()
        except Exception:
            box = None
        if box is None:
            # One missed frame mid-animation (an element that briefly wraps or
            # is swapped) shouldn't void the whole observation — only a total
            # absence does.
            continue
        xs.append(box["x"])
        ys.append(box["y"])
    if len(xs) < 2:
        return None
    verdict = classify(xs, ys)
    verdict["samples"] = len(xs)
    verdict["window_ms"] = (samples - 1) * interval_ms
    return verdict


# Phrases a user writes → the axis/sense they mean. Matching is intentionally
# forgiving on wording ("bouncing up and down", "moving left to right") but
# NEVER on the axis: asserting vertical bounce must fail on a horizontal
# drift, or the step proves nothing beyond "something moved".
_WANTED = {
    "left to right":  {"axis": "x", "sign": +1},
    "right to left":  {"axis": "x", "sign": -1},
    "top to bottom":  {"axis": "y", "sign": +1},
    "bottom to top":  {"axis": "y", "sign": -1},
    "up and down":    {"axis": "y", "sign": 0},
    "down and up":    {"axis": "y", "sign": 0},
    "side to side":   {"axis": "x", "sign": 0},
    "sideways":       {"axis": "x", "sign": 0},
    "horizontally":   {"axis": "x", "sign": 0},
    "vertically":     {"axis": "y", "sign": 0},
}


def matches(verdict: dict, wanted: str) -> tuple[bool, str]:
    """Does an observed verdict satisfy the direction the step asked for?

    Returns (ok, why-not). An empty/None `wanted` means "any motion".
    """
    if not verdict.get("moving"):
        return False, "it is not moving at all"
    want = _WANTED.get((wanted or "").strip().lower())
    if want is None:
        return True, ""
    axis = want["axis"]
    travel = verdict[f"travel_{axis}"]
    other = verdict["travel_y" if axis == "x" else "travel_x"]
    if travel <= _NOISE_PX or travel <= other:
        moved = verdict["direction"] or "barely at all"
        return False, f"it moves {moved}, not {wanted}"
    if want["sign"]:
        net = verdict[f"d{axis}"]
        if net * want["sign"] <= 0:
            return False, f"it moves {verdict['direction']}, not {wanted}"
    return True, ""
