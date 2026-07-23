"""Allure report metadata (NOOD_0022): environment.properties + categories.json.

Both are plain files `allure generate` picks up from allure-results/. Without
them the report's Environment widget ships empty and every failure lands in
the stock "Product defects" bucket — fine for whoever watched the terminal,
useless for anyone reading only the report. Written by hooks.after_all right
before the report build, from data the run already has.
"""
import json
import os
import platform
import sys
from pathlib import Path

# Allure matches messageRegex against the ENTIRE statusDetails message
# (java.util.regex .matches()), and noodle failure messages often carry a
# second "URL: ..." line — hence the (?s) DOTALL prefix and the .* padding.
# Order matters: first match wins, so locator/timeout/resolution problems are
# claimed before the broad "Expected ..." assertion bucket.
CATEGORIES = [
    {"name": "Element not found / ambiguous locator",
     "matchedStatuses": ["failed", "broken"],
     "messageRegex": r"(?s).*(Could not find|Ambiguous locator).*"},
    {"name": "Timeouts & waits",
     "matchedStatuses": ["failed", "broken"],
     "messageRegex": r"(?s).*Timed out.*"},
    {"name": "Step did not resolve",
     "matchedStatuses": ["failed", "broken"],
     "messageRegex": r"(?s).*No pattern matched.*"},
    {"name": "Assertion failures",
     "matchedStatuses": ["failed"],
     "messageRegex": r"(?s).*(Assertion Failed|Expected ).*"},
]


def _base_urls() -> dict:
    """App base URLs, read the same way hooks._load_environments loads them
    (root environments.yaml, then per-app files — first key wins)."""
    try:
        import yaml
    except ImportError:
        return {}
    from noodle import config as config_module
    urls: dict = {}
    tests_dir = config_module.load(".")["tests_dir"]
    candidates = [Path("environments.yaml"),
                  *sorted(Path.cwd().glob(f"{tests_dir}/**/resources/*environments.yaml"))]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception:
            continue
        for key, value in data.items():
            urls.setdefault(str(key), str(value))
    return urls


def write_environment(results_dir: Path) -> Path:
    """environment.properties — browser/headless/timeouts + app base URLs."""
    props = {
        "noodle.browser": os.getenv("NOODLE_BROWSER", "chromium"),
        "noodle.headless": os.getenv("NOODLE_HEADLESS", "false"),
        "noodle.timeout.ms": os.getenv("NOODLE_TIMEOUT", "10000"),
        "noodle.retries": os.getenv("NOODLE_RETRIES", "1"),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    if os.getenv("NOODLE_MODEL"):
        props["noodle.llm.model"] = os.environ["NOODLE_MODEL"]
    for app, url in _base_urls().items():
        props[f"base.url.{app}"] = url
    path = Path(results_dir) / "environment.properties"
    path.write_text("".join(f"{k}={v}\n" for k, v in sorted(props.items())))
    return path


def write_categories(results_dir: Path) -> Path:
    """categories.json — noodle's failure taxonomy for the Categories widget."""
    path = Path(results_dir) / "categories.json"
    path.write_text(json.dumps(CATEGORIES, indent=2) + "\n")
    return path


def write_meta(results_dir) -> None:
    """Write both files. Best-effort: report metadata must never fail a run."""
    try:
        rdir = Path(results_dir)
        rdir.mkdir(parents=True, exist_ok=True)
        write_environment(rdir)
        write_categories(rdir)
    except Exception:  # pragma: no cover — disk-full/permissions edge
        print("  ⚠️  Could not write Allure metadata "
              "(environment.properties / categories.json)", file=sys.stderr)
