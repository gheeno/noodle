@web @scroll @hover @capability
Feature: Scroll and Hover — move the viewport, trigger hover states

  # Patterns demonstrated:
  #   scrolls down / scrolls up
  #   scrolls to 'X'       — scroll an element into view
  #   hovers over X        — trigger :hover CSS and mouseover events
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/scroll_hover.feature --headless

  Background:
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User waits until "VHS Catalog" is visible

  @smoke @scroll_down
  Scenario: Scroll the catalog down to reveal more movies
    # The catalog table extends below the fold. Scroll down to see later entries.
    When User scrolls down
    Then User should see "Reservoir Dogs"

  @scroll_up
  Scenario: Scroll back up after scrolling down
    When User scrolls down
    And User scrolls up
    Then User should see "VHS Catalog"

  @smoke @scroll_to
  Scenario: Scroll a specific element into view
    # "scrolls to 'X'" calls Playwright scroll_into_view_if_needed() on the
    # element matching X — the safest way to interact with off-screen elements.
    When User scrolls to "Speed"
    Then User should see "Speed"

  @hover
  Scenario: Hover over a movie title to reveal any tooltip or hover state
    # "hovers over X" sends pointer-enter + mouseover events to the element.
    # BusterBlock's catalog rows may highlight on hover; this exercises the path.
    When User hovers over "Die Hard"
    Then User should see "Die Hard"

  @scroll_to @click_after_scroll
  Scenario: Scroll to an element then interact with it
    # Elements below the fold require a scroll before they can be clicked.
    When User scrolls to "Reservoir Dogs"
    And User clicks "Add to Cart" in the row containing "Reservoir Dogs"
    Then User should see "1"
