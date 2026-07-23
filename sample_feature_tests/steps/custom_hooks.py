"""Custom hook registrations and project-local step definitions.

Two things live here:
  1. Hook registrations — fire around every scenario (before/after) without
     touching .feature files. See hooks.feature for usage.
  2. Custom step definitions — steps NOT in Noodle's built-in dictionary.
     Behave discovers all *.py in tests/steps/ at startup. The z_ prefix
     on z_catch_all.py keeps it last in load order, so custom steps registered
     here are tried before the catch-all. See custom_steps.feature for usage.
"""
import csv
import os
import time
import uuid

from behave import then, when

from noodle.hooks import hook
from noodle.log import logger
from noodle.orchestrator.runner import ctx_get


@hook("before_scenario")
def assign_session(context, scenario):
    """Inject a short session ID and start a timer before each scenario."""
    context.session_id = str(uuid.uuid4())[:8]
    context._hook_start = time.monotonic()


@hook("after_scenario")
def log_timing(context, scenario):
    """Log elapsed time + session ID; extra audit line when @audit is present."""
    # ctx_get, not getattr: skipped scenarios (e.g. @live, @terminal without
    # OCR, @appium without the client) return before before_scenario's own
    # assign_session runs, so _hook_start/session_id are never set — and
    # behave's Context raises KeyError (not AttributeError) for unset
    # underscore-prefixed attributes, which plain getattr(..., default)
    # doesn't catch.
    elapsed = time.monotonic() - ctx_get(context, "_hook_start", time.monotonic())
    status_str = str(scenario.status)
    if "passed" in status_str:
        status = "PASSED"
    elif "skipped" in status_str:
        status = "SKIPPED"
    else:
        status = "FAILED"
    session_id = ctx_get(context, "session_id", "no-session")
    logger.info(
        f"\n  🪝 [{session_id}] {scenario.name} — {status} ({elapsed:.1f}s)"
    )
    if "audit" in scenario.effective_tags:
        logger.info(f"\n  📋 AUDIT: {scenario.feature.name} / {scenario.name}")


# ---------------------------------------------------------------------------
# Custom step: CSV login
# Usage:  When a user from this list "data/users.csv" logs in
# The CSV file must live in the app's resources/ (sibling of the features/
# folder the current .feature file lives in) — write the path relative to
# resources/, e.g. "data/users.csv".
# Row columns: username, password. Logs in as the first row only.
# See login.feature (@csv) and custom_steps.feature (@custom_step) for usage.
# ---------------------------------------------------------------------------
@when('a user from this list "{csv_path}" logs in')
def step_csv_login(context, csv_path):
    feature_dir = os.path.dirname(os.path.abspath(context.feature.filename))
    app_dir = os.path.dirname(feature_dir)
    full_path = os.path.join(app_dir, "resources", csv_path)
    with open(full_path, newline="") as fh:
        row = next(csv.DictReader(fh))
    context.execute_steps(f"""
        When User enters "{row['username']}" in the username field
        And User enters "{row['password']}" in the password field
        And User clicks the login button
    """)


# ---------------------------------------------------------------------------
# Custom assertion: catalog movie count
# Usage:  Then the catalog should have at least 10 movies
# Reads the #movie-count badge text (e.g. "15 movies") and asserts >= N.
# See custom_steps.feature (@custom_assert) for usage.
# ---------------------------------------------------------------------------
@then("the catalog should have at least {n:d} movies")
def step_catalog_min_count(context, n):
    badge = context.page.locator("#movie-count").inner_text()
    digits = "".join(ch for ch in badge if ch.isdigit())
    count = int(digits) if digits else 0
    assert count >= n, f"Expected at least {n} movies but badge shows: {badge!r}"
