@web
Feature: Checkboxes
  Covers: toggling unlabelled checkbox inputs (no <label>, POM css override required).

  Background:
    Given User is on "{env:THE_INTERNET}/checkboxes"

  @smoke
  Scenario: Checkbox 1 starts unchecked and can be checked
    Then the "checkbox 1" checkbox should be unchecked
    When User checks the "checkbox 1" checkbox
    Then the "checkbox 1" checkbox should be checked

  Scenario: Checkbox 2 starts checked and can be unchecked
    Then the "checkbox 2" checkbox should be checked
    When User unchecks the "checkbox 2" checkbox
    Then the "checkbox 2" checkbox should be unchecked
