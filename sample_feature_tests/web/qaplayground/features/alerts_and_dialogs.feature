@web
Feature: QA Playground — Alerts and Dialogs
  Covers: DOM modals and native JS alert/confirm/prompt handling.
  Dialog handlers are armed BEFORE the click that opens the dialog —
  Playwright auto-dismisses unhandled dialogs, so afterwards is too late.

  Background:
    Given User is on "{env:QAPLAYGROUND}/practice/alerts-dialogs"

  Scenario: Sweet Alert modal opens and can be closed
    When User clicks "Sweet Alert"
    And User takes a screenshot "sweet-alert-open"
    And User closes the modal

  Scenario: Accept a simple JS alert
    When User accepts the next alert
    And User clicks "Simple Alert"

  Scenario: Answer a JS prompt
    When User answers "noodle" into the next prompt
    And User clicks "Prompt Alert"
