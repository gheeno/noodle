"""Workspace config — noodle.yaml lives in the user's test directory, not here.

A workspace is any directory the user owns holding their tests/ (feature files
+ resources/), .env and noodle.yaml. The engine reads this to know where
things live; CI passes --workspace, the agent passes it too. Missing file →
defaults (current layout).
"""
import os
from pathlib import Path

import yaml

# NOOD_0177 — os.chmod appeared nowhere in the package, so every credential and
# session file landed at 0o666 & ~umask (typically 0644, world-readable). These
# two helpers are the single place that decides "this file holds a credential".
# A saved browser session is the sharpest case: it is pre-authenticated and
# bypasses MFA, which is exactly what the docs recommend it for.
_SECRET_FILE_HINTS = ("secrets.env", "_secrets.env", "session", "storage_state")


def is_secret_path(path) -> bool:
    name = Path(path).name.lower()
    return any(h in name for h in _SECRET_FILE_HINTS)


def write_private(path, text: str) -> Path:
    """Write text at 0600, private from creation — never world-readable, not
    even for the instant between write and chmod."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:      # fdopen owns fd and closes it
        fh.write(text)
    os.chmod(str(path), 0o600)          # an existing file keeps its old mode otherwise
    return path


# NOOD_0179 — ONE alias table. The run engine (hooks.py) and the probe both
# turn a user-facing browser name into the Playwright attribute + channel pair;
# two copies drift, and a probe that hardcoded chromium ignored the choice
# entirely. `safari` and `edge` are not Playwright engines — they are webkit
# and a chromium channel.
ENGINE_ALIASES = {"safari": ("webkit", None), "edge": ("chromium", "msedge")}
VALID_BROWSERS = {"chromium", "firefox", "webkit", "safari", "edge"}


def resolve_engine(name: str | None = None) -> tuple[str, str | None, str | None]:
    """(engine, channel, warning) for a browser name — NOODLE_BROWSER when None.

    Never raises: the probe is advisory, and a typo'd engine there must degrade
    to chromium with a note rather than kill a discovery run (the run engine
    keeps its hard validation — a mistyped browser in CI should fail loudly).
    """
    picked = (name or os.getenv("NOODLE_BROWSER") or "chromium").strip().lower()
    if picked not in VALID_BROWSERS:
        return "chromium", None, (
            f"unknown browser '{picked}' — using chromium "
            f"(valid: {', '.join(sorted(VALID_BROWSERS))})")
    engine, channel = ENGINE_ALIASES.get(picked, (picked, None))
    return engine, channel, None


DEFAULTS = {
    "tests_dir": "tests",
    "env_file": ".env",
    "reports_dir": "artifacts/reports",
    "browser": "chromium",
    "headless": True,
}
# No pageobjects_dir: page objects live at <tests_dir>/<type>/<app>/resources/
# pageobjects/, one folder per app-under-test — see docs/feature-packages.md.

# --llm name -> default model string litellm understands. Shared between
# `noodle init --llm` (persists NOODLE_MODEL into .env) and `noodle repl
# --llm` (session override) so there's one place to add a preset.
LLM_PRESETS = {
    # NOOD_0151 — cloud-first: Claude Sonnet is the recommended default;
    # ollama stays as the restricted-network / zero-cost fallback.
    "claude": "anthropic/claude-sonnet-5",
    "gemini": "gemini/gemini-1.5-flash",
    "ollama": "ollama/llama3.2",
}


def dev_fix_attempts() -> int:
    """NOOD_0094 — while an agent is *developing* a test, how many times it may
    auto-fix a mechanical failure (element not found, ambiguous locator,
    find-timeout) and re-run before it must stop and report the test as flaky.
    A token-cost ceiling on the generate -> validate -> run -> fix loop, NOT a
    green-forcing retry: a genuine app/assertion failure is root-caused, never
    looped on (see docs/agent-playbook.md §5). Default 10, floor 1.

    (Working title in the spec was MAX_RUN_TIME_DURING_TEST_DEVELOPMENT.)"""
    try:
        return max(1, int(os.getenv("NOODLE_DEV_FIX_ATTEMPTS", "10")))
    except ValueError:
        return 10


def rest_timeout(override: float | None = None) -> float:
    """NOOD_0182 — seconds a single HTTP call may take before the step fails.
    One budget for every wok: the stdlib REST client (@api, and any scenario —
    mobile/desktop included — since rest_ steps need no browser), Playwright's
    `api_call`, the network-response wait, and the perf load generator.

    Deliberately separate from NOODLE_TIMEOUT (Playwright per-action / element
    budget): report endpoints, cold serverless and batch APIs answer in minutes,
    and a UI timeout has no business capping them. A step's own
    "within N seconds" wins over the env var.

    ponytail: seconds (not ms) because every REST sentence says seconds."""
    if override:
        return float(override)
    try:
        return max(1.0, float(os.getenv("NOODLE_REST_TIMEOUT", "30")))
    except ValueError:
        return 30.0


def load(workspace: str = ".") -> dict:
    """Merge noodle.yaml (if present) over the defaults.

    Unknown keys still merge (forward-compat) but warn — a typo'd key
    (`broswer:`) otherwise silently falls back to the default."""
    cfg = dict(DEFAULTS)
    f = Path(workspace) / "noodle.yaml"
    if f.exists():
        loaded = yaml.safe_load(f.read_text()) or {}
        unknown = sorted(set(loaded) - set(DEFAULTS))
        if unknown:
            import sys
            print(f"noodle: warning: unknown key(s) in {f}: {', '.join(unknown)} "
                  f"— known keys: {', '.join(sorted(DEFAULTS))}", file=sys.stderr)
        cfg.update(loaded)
    return cfg
