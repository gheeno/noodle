@web
Feature: QA Playground — Input Fields
  Covers: fill, keyboard, clear, value assertions, disabled/readonly state.
  POM keys use hyphenated aliases: the page prose repeats natural widget names
  (e.g. "movie name" appears in tutorial accordions), which the accessibility
  locator would match first — aliases force deterministic POM resolution.

  Background:
    Given User is on "{env:QAPLAYGROUND}/practice/input-fields"

  @smoke
  Scenario: Enter a movie name
    When User enters "Avengers Endgame" in the movie-input field
    Then the "movie-input" field should have value "Avengers Endgame"

  Scenario: Type text and tab away
    When User enters "I am good, thanks!" in the append-input field
    And User presses "Tab"
    Then the "append-input" field should have value "I am good, thanks!"

  Scenario: Verify text already present inside an input field
    Then the "verify-input" field should have value "QA PlayGround"

  Scenario: Clear the prefilled text and refill
    When User clears the clear-input field
    And User enters "cleared and refilled" in the clear-input field
    Then the "clear-input" field should have value "cleared and refilled"

  Scenario: Disabled and readonly fields report their state
    Then the "locked-input" field should be disabled
    And the "readonly-input" field should be read-only
