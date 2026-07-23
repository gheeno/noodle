@web
Feature: Add/Remove Elements
  Covers: dynamically added/removed DOM nodes.

  Background:
    Given User is on "{env:THE_INTERNET}/add_remove_elements/"

  @smoke
  Scenario: Adding an element creates a Delete button
    When User clicks "Add Element"
    Then User should see 1 "Delete" items

  Scenario: Adding three elements creates three Delete buttons
    When User clicks "Add Element"
    And User clicks "Add Element"
    And User clicks "Add Element"
    Then User should see 3 "Delete" items

  Scenario: Deleting an element removes its Delete button
    When User clicks "Add Element"
    And User clicks "Delete"
    Then User should see 0 "Delete" items
