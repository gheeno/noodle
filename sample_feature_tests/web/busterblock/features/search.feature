@web @search @capability
Feature: Catalog search — live-filtering the movie list

  # The search box has no submit button — typing live-filters the table
  # (debounced ~280ms client-side, server-backed). No "clicks the search
  # button" step needed, unlike a typical search-then-submit form.
  #
  # Capabilities shown:
  #   @tag           a caller-supplied tag (e.g. from a generation prompt
  #                   like "add gherkin tags @catalog_search") lands on every
  #                   scenario generated from that request
  #   multi-scenario  two scenarios in ONE feature file — generate_test's
  #                   append_to lets a later "also test <other case>" prompt
  #                   add a scenario here instead of writing a new file
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/search.feature --headless
  # Tags: noodle run sample_feature_tests/web/busterblock/features/search.feature --tag @catalog_search

  Background:
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"

  @smoke @catalog_search
  Scenario: Searching for a movie by title filters the catalog to it
    When User enters "Jaws" in the search field
    Then User should see "Jaws"

  @catalog_search
  Scenario: Searching for a different movie filters to that one instead
    When User enters "Alien" in the search field
    Then User should see "Alien"
