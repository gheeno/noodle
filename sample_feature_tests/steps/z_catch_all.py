"""behave step-definitions entry point.

behave auto-imports every ``tests/steps/*.py`` at startup to discover step
definitions. Noodle registers exactly ONE step matcher — a regex catch-all
that routes each Gherkin line to the web or visual agent (see
``noodle.steps.catch_all``). This module just re-exports it so behave picks it
up; there are no hand-written step functions to add here.

The ``z_`` filename prefix keeps it last in behave's alphabetical load order, so
any project-local step files added later register before the catch-all.
"""
from noodle.steps.catch_all import *  # noqa: F401,F403,E402
