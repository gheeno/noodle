@web @login @capability
Feature: Login — all the ways Noodle can authenticate

  # Noodle has no login keyword. Login is composed from the same vocabulary
  # as every other flow: navigate → fill → click → assert. This file shows every
  # pattern for supplying credentials so you can search @login and see them all.
  #
  # Capabilities shown:
  #   @env_var      {env:BB_USER} / {env:BB_PASS} — secrets resolved at runtime, never in git
  #   @literal      "username" / "password" — hardcoded (local dev only)
  #   @multi_param  two captured groups in a single step phrase
  #   @outline      Scenario Outline + Examples table
  #   @csv          custom step reads resources/data/users.csv (NOT a built-in step)
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/login.feature --headless
  # Tags: noodle run sample_feature_tests/web/busterblock/features/login.feature --tag @smoke

  @smoke @env_var
  Scenario: Log in with env var credentials
    # {env:BB_USER} and {env:BB_PASS} resolve from .env / secrets.env at runtime.
    # The actual values never appear in the feature file or in git.
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"

  @literal
  Scenario: Log in with hardcoded credentials (literals)
    # Literal strings — fine for local exploration; use env vars in CI.
    Given User is on "{env:BUSTERBLOCK}"
    When User enters "reel_ryan" in the username field
    And User enters "Popcorn1!" in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"

  @smoke @multi_param
  Scenario: Steps with two parameters — value AND field name in one step
    # "enters X in the Y field" captures two groups: X = value, Y = field name.
    # Both resolve independently. Pattern: enters (.+?) in(?:to)? (?:the )?(.+?) (?:field|...)
    # Shown here on the error path so the two-param shape is clear.
    Given User is on "{env:BUSTERBLOCK}"
    When User enters "not_a_user" in the username field
    And User enters "wrong_pass" in the password field
    And User clicks the login button
    Then User should see "Invalid credentials"

  @smoke @outline @table
  Scenario Outline: Scenario Outline — one definition, many users from a table
    # Behave expands each Examples row into a separate scenario run.
    # <username> and <password> are substituted per row at execution time.
    Given User is on "{env:BUSTERBLOCK}"
    When User enters <username> in the username field
    And User enters <password> in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"

    Examples:
      | username      | password   |
      | reel_ryan     | Popcorn1!  |
      | tape_tanya    | Rewind2#   |
      | vhs_victor    | VCR_3way   |

  @csv @custom_step
  Scenario: Log in with the first user from a CSV resource file
    # "a user from this list" is NOT a built-in Noodle step.
    # It is defined in tests/steps/custom_hooks.py — search @custom_step or
    # see custom_steps.feature for the pattern. The step reads resources/data/users.csv
    # and logs in as the first row.
    Given User is on "{env:BUSTERBLOCK}"
    # llm-ok: custom @when in tests/steps/custom_hooks.py, not a built-in pattern
    When a user from this list "data/users.csv" logs in
    Then User should see "VHS Catalog"
