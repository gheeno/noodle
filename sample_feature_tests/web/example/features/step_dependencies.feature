# ============================================================================
# STEP DEPENDENCIES DEMO (Phase 12) — live site (example.com).
#
# Flow: go to the site -> close popups -> search "office chair toolbox" ->
#       GRAB the second result's title (a value only known at runtime) ->
#       ASSERT it equals the known expected title.
#
# The dependency is on the grab step: {var:result} holds the live second-result
# title, and the next step asserts that captured value against a literal.
#
# Variable syntax:
#   "literal"  → a fixed string you type (the URL, the search, the expected title)
#   {var:name}     → a value CAPTURED during this run (scenario-scoped store)
#   {env:name}     → a value from .env / config (e.g. {env:SAUCE_USERNAME})
#
# Selectors (searchbox/secondresulttitle) come from pageobjects/home_pom.yaml
# and pageobjects/results_pom.yaml in this folder.
#
# Run it:   behave features/example/step_dependencies.feature --no-capture
# Live site: needs network + the settle waits; ordering can change over time, so
# the expected title may need updating if Example reorders results.
# ============================================================================
@web @step_dependencies
Feature: Step Dependencies and Shared State

  @web @smoke
  Scenario: Grab the second result's title and assert its value
    Given User is on "https://www.example.com"
    And User waits 5 seconds
    And User closes all popups

    When User searches for "office chair toolbox"
    And User waits until "Office Chair" is visible
    And User waits 5 seconds

    # grab the second result's title into the shared store (the dependency)
    And User grabs the secondresulttitle as {var:result}

    # assert the captured value against the stable part of the title only —
    # the color/variant suffix rotates with stock and merchandising (e.g.
    # "Pink" vs "Lilac"), so pinning the full string makes this brittle
    # against site content it doesn't actually need to verify.
    Then {var:result} should contain "Office Chair Mini Toolbox with 2 Drawers"
