@web
Feature: Context Menu
  Covers: a right-click handler that fires a native JS alert.

  Background:
    Given User is on "{env:THE_INTERNET}/context_menu"

  @smoke
  Scenario: Right-clicking the hot spot triggers an alert
    When User accepts the next alert
    And User right-clicks "hot spot"
    Then the alert should say "You selected a context menu"
