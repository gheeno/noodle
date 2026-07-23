@web @custom_step @capability
Feature: Custom Steps — steps not in the built-in dictionary

  # Noodle's built-in patterns cover the common automation vocabulary.
  # When you need something outside that vocabulary, you have two options:
  #
  #   Option A — LLM fallback (zero code)
  #     Use a verb the regex layer doesn't know. The step falls through to the LLM.
  #     See llm_fallback.feature for examples.
  #
  #   Option B — Write a custom step in Python (deterministic, no LLM)
  #     Add a @given / @when / @then function to tests/steps/custom_hooks.py.
  #     Behave discovers all *.py files in tests/steps/ automatically.
  #     Custom steps run BEFORE z_catch_all.py (because z_ sorts last), so they
  #     take priority over the built-in catch-all.
  #
  # This file demonstrates Option B using two custom steps defined in
  # tests/steps/custom_hooks.py:
  #
  #   @when('a user from this list "{csv_path}" logs in')
  #     → reads resources/data/users.csv and logs in as the first row
  #
  #   @then('the catalog should have at least {n:d} movies')
  #     → asserts the movie-count badge shows >= N
  #
  # Search for @custom_step across all feature files to find every usage.
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/custom_steps.feature --headless

  @smoke @csv @custom_step
  Scenario: Custom step — log in as the first user from a CSV file
    # "a user from this list" is defined in custom_hooks.py.
    # It reads resources/data/users.csv (relative to THIS feature file) and executes
    # the fill + click steps for the first row. See custom_hooks.py for the code.
    Given User is on "{env:BUSTERBLOCK}"
    # llm-ok: custom @when in custom_hooks.py, not a built-in pattern
    When a user from this list "data/users.csv" logs in
    Then User should see "VHS Catalog"

  @smoke @custom_step @custom_assert
  Scenario: Custom assertion step — catalog must have at least N movies
    # "the catalog should have at least N movies" is a custom @then step.
    # It reads the movie-count badge and asserts the number is >= N.
    # This kind of assertion is too specific to generalise into the built-in
    # vocabulary — it belongs in a project-local step file.
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    And User waits until "VHS Catalog" is visible
    Then the catalog should have at least 10 movies
