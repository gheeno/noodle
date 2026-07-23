@web
Feature: QA Playground — Tabs and Windows
  Covers: new tab detection, switching between tabs.

  Background:
    Given User is on "{env:QAPLAYGROUND}/practice/tabs-windows"

  @smoke
  Scenario: Open home page in a new tab and switch back
    When User clicks "Open Home Page"
    Then a new tab should open
    When User switches to the new tab
    Then User should have url containing "qaplayground.com"
    When User switches to the original tab
    Then User should have url containing "tabs-windows"
