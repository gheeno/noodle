import os

from behave import step, use_step_matcher
from playwright.sync_api import TimeoutError as PWTimeoutError

from noodle import healing
from noodle.log import logger
from noodle.orchestrator.runner import SoftAssertionReport, execute_step
from noodle.orchestrator.visual_runner import execute_visual_step

# Regex matcher so [variable] brackets in step text don't confuse the parser
use_step_matcher("re")


def _step_retries(tags) -> int:
    """Phase K (F7) — NOODLE_STEP_RETRIES extra in-place attempts per step;
    @retry_step opts one flaky scenario in without enabling it globally."""
    retries = int(os.getenv("NOODLE_STEP_RETRIES", "0") or "0")
    if "retry_step" in tags:
        retries = max(retries, 1)
    return retries


def _run_with_retries(step_text: str, context, retries: int):
    """Retry only assertion/timeout failures (a flaky SSO redirect, a slow CDN
    asset); unexpected exceptions propagate immediately. Retries land in the
    healing report alongside locator heals (strategy: step-retry)."""
    attempts = retries + 1
    for attempt in range(1, attempts + 1):
        try:
            execute_step(step_text, context)
            return
        except SoftAssertionReport:
            raise                              # a report, not a flaky step
        except (AssertionError, PWTimeoutError):
            if attempt >= attempts:
                raise
            healing.record(step_text, "step-retry", f"attempt {attempt} failed, retrying")
            logger.warning(f"\n  🔁 Step failed (attempt {attempt}/{attempts}) — retrying: {step_text}")


@step(r"(?P<anything>.*)")
def catch_all(context, anything):
    # Single catch-all for the whole suite: web and visual cannot both register
    # the same regex (behave raises AmbiguousStep), so we route by tag here.
    # @visual → desktop/OpenCV agent; everything else → web (Playwright) agent.
    tags = set(getattr(context.scenario, "effective_tags", None) or [])
    if "visual" in tags:
        execute_visual_step(anything, context)
        return
    try:
        _run_with_retries(anything, context, _step_retries(tags))
    except SoftAssertionReport:
        raise
    except AssertionError as e:
        # Phase L — @soft scenarios collect assertion failures and keep going;
        # hooks.after_scenario (or 'all soft assertions should pass') reports.
        if "soft" in tags:
            context._soft_failures.append(f"{anything}: {e}")
            logger.warning(f"\n  🟡 Soft assertion failed (continuing): {anything}")
            return
        raise
