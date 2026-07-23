@web @waits @capability
Feature: Waits — event-driven and time-based wait patterns

  # Patterns demonstrated:
  #   waits until 'X' is visible / appears    — element-driven (MutationObserver)
  #   waits until 'X' disappears / is hidden  — disappearance wait
  #   waits for the page to load              — DOMContentLoaded
  #   waits for the network to be idle        — no in-flight requests
  #   waits N seconds                         — fixed sleep (avoid in production)
  #
  # Prefer event-driven waits (visible/hidden/network) over fixed sleeps.
  # Fixed sleeps are slow, flaky, and never the right default for CI.
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/waits.feature --headless

  @smoke @wait_visible
  Scenario: Wait for dynamic content to appear after login
    # After navigation, the VHS Catalog heading renders after JS loads the data.
    # "waits until X is visible" uses Playwright's MutationObserver — no polling.
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User waits until "VHS Catalog" is visible
    And User should see "Die Hard"

  @wait_hidden
  Scenario: Wait for an element to disappear before asserting
    # After login the error banner should not be visible.
    # Useful when a loading spinner or error message must clear before proceeding.
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User waits until "Invalid credentials" disappears

  @wait_load
  Scenario: Wait for the page to load (DOMContentLoaded)
    Given User is on "{env:BUSTERBLOCK}"
    And User waits for the page to load
    Then User should see "BusterBlock"

  @wait_network
  Scenario: Wait for network to be idle before asserting catalog rows
    # "waits for the network to be idle" blocks until all XHR/fetch calls settle.
    # Use after navigating to a page that makes API calls to load content.
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    And User waits for the network to be idle
    Then User should see "Die Hard"
    And User should see "Jaws"

  @wait_seconds @no_retry
  Scenario: Fixed-time wait (last resort — prefer event-driven waits above)
    # "waits N seconds" is a dumb sleep. Only use it when you have no better
    # signal — e.g. waiting for a rate-limited external system.
    # @no_retry prevents behave autoretry from doubling the wait on this scenario.
    Given User is on "{env:BUSTERBLOCK}"
    When User waits 2 seconds
    # Also valid: "waits 1 minute", "waits 2 hours" (converts to seconds internally)
    Then User should see "BusterBlock"
