@web
Feature: QA Playground — Radio buttons and Checkboxes
  Covers: radio select, checkbox toggle (incl. ARIA role=checkbox buttons),
  checked/unchecked/disabled state.
  Site quirk found while testing: in the "disabled radio group" only
  radio-maybe is actually disabled; radio-going is enabled.

  Background:
    Given User is on "{env:QAPLAYGROUND}/practice/radio-checkbox"

  @smoke
  Scenario: Select a radio option
    When User checks the "yes-radio" checkbox
    Then the "yes-radio" checkbox should be checked
    And the "no-radio" checkbox should be unchecked

  Scenario: Uncheck a pre-checked ARIA checkbox
    When User unchecks the "rememberme-box" checkbox
    Then the "rememberme-box" checkbox should be unchecked

  Scenario: Check the terms ARIA checkbox
    # Explicit wait: POM lookup does not wait for hydration (see gap report)
    When User waits until "terms-box" is visible
    And User checks the "terms-box" checkbox
    Then the "terms-box" checkbox should be checked

  Scenario: Disabled state in the disabled radio group
    Then the "maybe-radio" should be disabled
    And the "going-radio" should be enabled
