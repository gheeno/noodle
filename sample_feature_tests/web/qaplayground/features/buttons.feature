@web
Feature: QA Playground — Buttons
  Covers: click, double-click, right-click, disabled state, navigation buttons.

  Background:
    Given User is on "{env:QAPLAYGROUND}/practice/buttons"

  @smoke
  Scenario: Double click reveals the confirmation message
    When User double-clicks "Double Click Me"
    Then User should see "Double clicked!"

  Scenario: Disabled button reports its state
    Then the "locked-btn" button should be disabled

  Scenario: Go To Home navigates away from the practice page
    When User clicks "Go To Home"
    Then User should not see "Double Click Me"

  Scenario: Right click on the right-click button
    When User right-clicks "Right Click Me"
    And User takes a screenshot "after-right-click"
