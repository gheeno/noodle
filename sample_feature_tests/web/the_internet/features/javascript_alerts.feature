@web
Feature: JavaScript Alerts
  Covers: native alert, confirm and prompt dialogs. Handler must be armed
  before the click that triggers each dialog (Playwright auto-dismisses
  unhandled dialogs).

  Background:
    Given User is on "{env:THE_INTERNET}/javascript_alerts"

  @smoke
  Scenario: Accepting a plain alert
    When User accepts the next alert
    And User clicks "Click for JS Alert"
    Then User should see "You successfully clicked an alert"

  Scenario: Accepting a confirm dialog
    When User accepts the next confirm
    And User clicks "Click for JS Confirm"
    Then User should see "You clicked: Ok"

  Scenario: Dismissing a confirm dialog
    When User dismisses the next confirm
    And User clicks "Click for JS Confirm"
    Then User should see "You clicked: Cancel"

  Scenario: Entering text into a prompt
    When User types "Ponytail QA" into the next prompt and accepts it
    And User clicks "Click for JS Prompt"
    Then User should see "You entered: Ponytail QA"
