"""Agentic RCA — classify a failed step's root cause with the vision LLM.

The Healenium-beating move isn't a better healer, it's *diagnosis*: when a step
fails, send the failure screenshot + step text + error to a vision model and get
back a structured root-cause verdict (app regression / locator rot / env flap /
test data / test script), logged and attached to the Allure result so the report
can be filtered by category.

Opt-in: NOODLE_RCA truthy AND NOODLE_MODEL set. Best-effort — every entry
point swallows its own errors so RCA never changes a test's pass/fail outcome or
slows a green run (it only fires on failure).
"""
import base64
import json
import os
import re
from pathlib import Path

from noodle.log import logger

# Single-letter categories keep the model's reply terse and easy to validate;
# the human-readable label is what lands in the report.
CATEGORY_LABELS = {
    "A": "app-regression",
    "B": "locator-rot",
    "C": "environment-flap",
    "D": "test-data",
    "E": "test-script",
}

_TRUTHY = {"1", "true", "yes", "on"}

_PROMPT = """A BDD test step failed. Look at the screenshot and classify the root cause.

Step: "{step}"
Error: "{error}"

Root cause categories:
  A) App regression — the UI changed or a feature is broken
  B) Locator rot — the targeted element's label or structure changed
  C) Environment flap — network, timeout, or infrastructure issue
  D) Test data issue — missing, stale, or wrong seed data
  E) Test script issue — the step or assertion itself is wrong

Reply with JSON only, no other text:
{{"category": "A", "confidence": "high|medium|low", "reason": "one sentence", "suggested_fix": "one sentence"}}
"""


def enabled() -> bool:
    """RCA runs only when explicitly opted in AND a model is configured."""
    return (
        os.getenv("NOODLE_RCA", "false").strip().lower() in _TRUTHY
        and bool(os.getenv("NOODLE_MODEL"))
    )


def parse(raw: str) -> dict | None:
    """Parse the model's JSON verdict into a dict, or None if unusable. Pure —
    tolerates a ```json fence and prose around the object, and rejects an
    unknown category. Unit-testable without a model."""
    if not isinstance(raw, str):
        return None
    text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", raw.strip()).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("category") not in CATEGORY_LABELS:
        return None
    obj["label"] = CATEGORY_LABELS[obj["category"]]
    obj.setdefault("confidence", "unknown")
    obj.setdefault("reason", "")
    obj.setdefault("suggested_fix", "")
    return obj


def review(step_name: str, error_message: str, screenshot_path: str) -> dict | None:
    """Classify one failed step. Returns the verdict dict (with 'label') or None.
    Never raises — RCA is advisory and must not affect the run."""
    if not enabled():
        return None
    try:
        from noodle.llm.client import ask_vision

        b64 = base64.b64encode(Path(screenshot_path).read_bytes()).decode()
        # Phase I — RCA spends from its own pool (NOODLE_RCA_MAX_CALLS) so a
        # cap on step-fallback calls doesn't also silence diagnosis.
        raw = ask_vision(
            prompt=_PROMPT.format(step=step_name, error=error_message or ""),
            image_b64=b64,
            cap_var="NOODLE_RCA_MAX_CALLS",
        )
        verdict = parse(raw)
        if verdict is None:
            return None
        logger.info(
            f"\n  🔍 RCA [{verdict['label']}] ({verdict['confidence']}): "
            f"{verdict['reason']}\n  💡 Suggested fix: {verdict['suggested_fix']}"
        )
        return verdict
    except Exception:
        return None
