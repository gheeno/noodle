@web
Feature: Drag and Drop
  Covers: HTML5 native drag events. Known industry-wide flaky case for
  automation tools — kept in the suite deliberately to see how this engine
  handles it.

  Background:
    Given User is on "{env:THE_INTERNET}/drag_and_drop"

  @smoke
  Scenario: Dragging column A onto column B swaps their headers
    When User drags "column a" onto "column b"
    Then User should see "B" in the "column a" section
    And User should see "A" in the "column b" section
