@web
Feature: QA Playground — Dynamic Waits
  Covers: wait-until-text-visible after delayed render, spinner completion,
  disabled-until-enabled state.

  Background:
    Given User is on "{env:QAPLAYGROUND}/practice/dynamic-waits"

  @smoke
  Scenario: Element appears after a delay
    When User clicks the "show-btn" button
    And User waits until "Element is now visible!" appears
    And User takes a screenshot "element-appeared"

  Scenario: Spinner completes after loading
    When User clicks the "spin-btn" button
    And User waits until "Done! Spinner gone." is visible
    And User takes a screenshot "spinner-done"

  Scenario: Delayed-enable button starts disabled
    Then the "delay-btn" button should be disabled
