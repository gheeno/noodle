@web
Feature: Inputs
  Covers: a bare <input type=number> with no label/placeholder.

  Background:
    Given User is on "{env:THE_INTERNET}/inputs"

  @smoke
  Scenario: Entering a number sets the field value
    When User enters "42" in the "number input" field
    Then the "number input" field should have value "42"
