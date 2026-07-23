@web
Feature: Dropdown
  Covers: native <select> option selection and value assertion.

  Background:
    Given User is on "{env:THE_INTERNET}/dropdown"

  @smoke
  Scenario: Selecting Option 1 sets the dropdown value
    When User selects "Option 1" from the dropdown
    Then the "dropdown" field should have value "1"

  Scenario: Selecting Option 2 sets the dropdown value
    When User selects "Option 2" from the dropdown
    Then the "dropdown" field should have value "2"
