@web
Feature: Key Presses
  Covers: a keydown listener that echoes the key name.
  NB: Enter is deliberately NOT asserted against the echo — the #target input
  is the lone field in a bare <form>, so Enter fires the browser's implicit
  single-input form submit and reloads the page before any assertion can see
  the echoed text (NOOD_0022 RCA finding). Enter's real behaviour on this
  page is the redirect, and that's what we assert.

  Background:
    Given User is on "{env:THE_INTERNET}/key_presses"

  @smoke
  Scenario: Pressing Tab is echoed back
    When User clicks "target"
    And User presses "Tab"
    Then User should see "You entered: TAB"

  Scenario: Pressing Escape is echoed back
    When User clicks "target"
    And User presses "Escape"
    Then User should see "You entered: ESCAPE"

  Scenario: Pressing Enter submits the bare form and reloads the page
    When User clicks "target"
    And User presses "Enter"
    Then User should have url containing "key_presses?"
