"""Workspace config — noodle.yaml lives in the user's test directory, not here.

A workspace is any directory the user owns holding their tests/ (feature files
+ resources/), .env and noodle.yaml. The engine reads this to know where
things live; CI passes --workspace, the agent passes it too. Missing file →
defaults (current layout).
"""
import os
from pathlib import Path

import yaml

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
