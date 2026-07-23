@web @text_input @fill @capability
Feature: Text Input — fill, clear, type, keyboard keys

  # Patterns demonstrated:
  #   enters X in the Y field          — fill by field name (two parameters)
  #   fills in X with Y                — fill (locator first, value second)
  #   types 'X' into Y                 — fill via 'type' verb
  #   clears the X field               — clear a field before filling
  #   types 'X'                        — raw keyboard input (no locator, goes to focused element)
  #   presses 'Tab' / 'Enter' / 'Escape'  — keyboard key
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/text_input.feature --headless

  Background:
    Given User is on "{env:BUSTERBLOCK}"

  @smoke @fill_two_param
  Scenario: Fill a field — "enters X in the Y field" (two parameters)
    # Two captured groups: X = value, Y = field name.
    # Field name resolves via aria-label, placeholder, or visible label text.
    When User enters "reel_ryan" in the username field
    And User enters "Popcorn1!" in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"

  @fill_reversed
  Scenario: Fill a field — "fills in Y with X" (locator-first phrasing)
    # Equivalent to "enters X in Y" but the field name comes first.
    # Pattern: fills? (?:in )?(?:the )?(.+?) with (.+)
    When User fills in the username field with "reel_ryan"
    And User fills in the password field with "Popcorn1!"
    And User clicks the login button
    Then User should see "VHS Catalog"

  @clear @fill_after_clear
  Scenario: Clear a field before typing a new value
    # "clears the X field" erases existing content first — prevents
    # accidental appending when the field already has a value.
    When User enters "wrong_user" in the username field
    And User clears the username field
    And User enters "reel_ryan" in the username field
    And User enters "Popcorn1!" in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"

  @raw_type @keyboard
  Scenario: Raw keyboard type + Tab + Enter (no locator)
    # "types 'X'" sends keystrokes to whatever has focus — no element lookup.
    # "presses 'Tab'" moves focus. "presses 'Enter'" submits.
    When User clicks the username field
    And User types "reel_ryan"
    And User presses "Tab"
    And User types "Popcorn1!"
    And User presses "Enter"
    Then User should see "VHS Catalog"

  @keyboard @escape
  Scenario: Press Escape clears focus
    # Pressing Escape on the login form should clear the active input focus.
    When User clicks the username field
    And User types "test"
    And User presses "Escape"
    Then User should see "BusterBlock"

  @smoke @fill_search
  Scenario: Fill the catalog search field
    # After login, filter the catalog with a search term.
    When User enters "reel_ryan" in the username field
    And User enters "Popcorn1!" in the password field
    And User clicks the login button
    And User waits until "VHS Catalog" is visible
    And User enters "Terminator" in the search movies field
    Then User should see "The Terminator"
    And User should not see "Die Hard"
