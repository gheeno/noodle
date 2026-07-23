@web
Feature: Dynamic Loading
  Covers: an element hidden (example 1) vs. rendered into the DOM (example 2)
  only after a 5s async delay — a real wait-strategy regression check.

  @smoke
  Scenario: Example 1 — hidden element becomes visible after loading
    Given User is on "{env:THE_INTERNET}/dynamic_loading/1"
    When User clicks "Start"
    Then User waits until "Hello World!" is visible for up to 15 seconds

  Scenario: Example 2 — element is added to the DOM after loading
    Given User is on "{env:THE_INTERNET}/dynamic_loading/2"
    When User clicks "Start"
    Then User waits until "Hello World!" is visible for up to 15 seconds

  Scenario: NOOD_0092 — implicit auto-wait rides out the loading screen
    # No explicit wait step: goto returns at domcontentloaded, the loading
    # spinner covers the page, and the next step's Playwright auto-wait /
    # find() polling must absorb the async delay on its own.
    Given User is on "{env:THE_INTERNET}/dynamic_loading/2"
    When User clicks "Start"
    Then User should see "Hello World!"
