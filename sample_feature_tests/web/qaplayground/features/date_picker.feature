@web
Feature: QA Playground — Date Picker
  Covers: native date inputs, date range, value assertion.

  Background:
    Given User is on "{env:QAPLAYGROUND}/practice/date-picker"

  @smoke
  Scenario: Enter today's date
    When User enters "2026-07-01" in the today-input field
    Then the "today-input" field should have value "2026-07-01"

  Scenario: Enter a birthday
    When User enters "1990-05-15" in the bday-input field
    Then the "bday-input" field should have value "1990-05-15"

  Scenario: Enter a date range
    When User enters "2026-07-01" in the range-start-input field
    And User enters "2026-07-15" in the range-end-input field
    Then the "range-start-input" field should have value "2026-07-01"
    And the "range-end-input" field should have value "2026-07-15"
