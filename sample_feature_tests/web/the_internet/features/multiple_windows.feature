@web
Feature: Multiple Windows
  Covers: a link that opens a new browser tab.

  Background:
    Given User is on "{env:THE_INTERNET}/windows"

  @smoke
  Scenario: Clicking the link opens a new tab with the expected content
    When User clicks "Click Here"
    Then a new tab should open
    When User switches to the new tab
    Then User should see "New Window"
    When User closes the current tab
    And User switches to the original tab
    Then User should see "Opening a new window"
