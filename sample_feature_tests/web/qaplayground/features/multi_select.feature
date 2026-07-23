@web
Feature: QA Playground — Multi Select
  Covers: native multi-select, select-all buttons, checkbox groups, tag pickers.

  Background:
    Given User is on "{env:QAPLAYGROUND}/practice/multi-select"

  @smoke
  Scenario: Select one option in the fruits multi-select
    When User selects "Apple" from the fruit multi select
    And User takes a screenshot "fruit-selected"

  Scenario: Select all countries via the button
    When User clicks "Select All"
    And User takes a screenshot "countries-all-selected"

  Scenario: Pick technologies from a checkbox group
    When User checks the "React" checkbox
    And User checks the "Svelte" checkbox
    Then the "React" checkbox should be checked
    And the "Svelte" checkbox should be checked

  Scenario: Pick tags from a tag picker
    When User clicks "automation"
    And User clicks "selenium"
    Then User should see "Total tags selected: 2"

  # Quarantined (NOOD_0018): confirmed by direct reproduction that Playwright's
  # select_option() correctly selects both DOM options (el.selectedOptions has
  # both), but qaplayground.com's own "Selected count" React state stays at 0 —
  # the site's counter appears to be driven by per-option click handlers, not
  # the <select>'s change event. Site quirk, not a Noodle bug.
  @quarantine
  Scenario: Select several options at once
    When User selects "Banana" and "Mango" from the fruit multi select
    Then User should see "Selected count: 2"
