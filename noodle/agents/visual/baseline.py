"""Visual baseline diff (NOOD_0018 Phase 4 / NOOD_0030 §3.1).

The cheap detector for silent visual regressions: a scenario can pass every
text assertion while the page renders visibly wrong (modal behind its
backdrop, color regression, broken layout). With NOODLE_VISUAL_BASELINE=true,
hooks.after_scenario screenshots each PASSING web scenario and diffs it
against a stored baseline — pure Pillow, no vision model.

- First passing run adopts the screenshot as the baseline
  (<NOODLE_BASELINES_DIR, default baselines/>/<scenario>.png). Delete a
  baseline to re-adopt after an intended UI change.
- Later runs warn when more than NOODLE_VISUAL_THRESHOLD (default 1%) of
  pixels differ, writing the thresholded diff image next to the run's other
  screenshots. The warning rides the existing per-step warnings channel, so
  it lands in `noodle rca-report`'s "passed with warnings" table.

ponytail: whole-page pixel ratio, no SSIM/region masking — add masks for
known-dynamic regions (clocks, ads) when a real page needs them.
"""
import os
import shutil
from pathlib import Path

from noodle.reporting import paths as _paths

# Per-channel delta below this is noise (antialiasing, JPEG-ish artifacts).
_PIXEL_TOLERANCE = 25


def enabled() -> bool:
    return os.getenv("NOODLE_VISUAL_BASELINE", "").lower() in ("1", "true", "yes")


def _threshold() -> float:
    try:
        return float(os.getenv("NOODLE_VISUAL_THRESHOLD", "0.01"))
    except ValueError:
        return 0.01


def baselines_dir() -> Path:
    return Path(os.getenv("NOODLE_BASELINES_DIR", "baselines"))


def compare(current: str, baseline: str, diff_out: str | None = None) -> float:
    """Fraction of pixels differing materially between two PNGs. Different
    dimensions return 1.0 — a size change is the strongest "looks different"
    signal there is. Optionally writes a black/white mask of changed pixels."""
    from PIL import Image, ImageChops
    a = Image.open(baseline).convert("RGB")
    b = Image.open(current).convert("RGB")
    if a.size != b.size:
        return 1.0
    diff = ImageChops.difference(a, b).convert("L")
    changed = sum(diff.histogram()[_PIXEL_TOLERANCE + 1:])
    ratio = changed / (a.size[0] * a.size[1])
    if diff_out and ratio:
        diff.point(lambda v: 255 if v > _PIXEL_TOLERANCE else 0).save(diff_out)
    return ratio


def check(page, scenario_name: str) -> str | None:
    """Screenshot `page`; adopt as baseline if none exists, else compare.
    Returns a warning string when the delta exceeds the threshold, None
    otherwise. Raises nothing worth catching upstream beyond best-effort."""
    safe = scenario_name.replace(" ", "_").replace("/", "_")[:80]
    shots_dir = _paths.screenshots_dir()
    shots_dir.mkdir(parents=True, exist_ok=True)
    current = shots_dir / f"BASELINE_CHECK_{safe}.png"
    page.screenshot(path=str(current), full_page=True)

    bdir = baselines_dir()
    baseline = bdir / f"{safe}.png"
    if not baseline.is_file():
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current, baseline)
        return None

    diff_path = shots_dir / f"VISUAL_DIFF_{safe}.png"
    ratio = compare(str(current), str(baseline), str(diff_path))
    if ratio <= _threshold():
        return None
    return (f"Visual diff: {ratio:.1%} of pixels differ from baseline "
            f"{baseline} (diff mask: {diff_path}). If the change is "
            f"intended, delete the baseline to re-adopt.")
