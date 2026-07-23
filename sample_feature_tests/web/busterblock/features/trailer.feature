@web @trailer @capability
Feature: Trailer / Preview page — deep-linking, tab lifecycle, error state

  # trailer.html was previously only touched by one shallow assertion inside
  # navigation.feature ("a click opens a new tab... User should see 'Director'").
  # This file targets the page itself:
  #   - deep-linking straight to trailer.html?id=N renders that exact movie's data
  #   - "closes the current tab" (documented, never exercised anywhere else)
  #   - an unknown id's error handling (found while writing this file — see below)
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/trailer.feature --headless

  @smoke @deeplink
  Scenario: Deep link straight to a movie's trailer page renders that movie's real data
    # initTrailer() has no requireAuth() gate (unlike catalog/cart) — no login needed.
    Given User navigates to "{env:BUSTERBLOCK}/trailer.html?id=14"
    Then the page title should contain "The Terminator"
    And User should see "The Terminator"
    And User should see "1984"
    And User should see "James Cameron"
    And User should see "107 min"
    And User should see "Sci-Fi"

  @smoke @new_tab @close_tab
  Scenario: Preview opens a new tab; closing it returns focus to the catalog tab
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"
    When User clicks "Preview"
    Then a new tab should open
    And User should see "Director"
    When User closes the current tab
    Then User should see "VHS Catalog"

  @regression @deeplink
  Scenario: An unknown movie id shows a clean "not found" state, no leftover placeholders
    # Regression lock for a real bug found while building this file: GET
    # /api/movies/9999 returns 404 with a *valid* JSON body ({"error":"Not found"}),
    # so fetch() never threw — initTrailer() kept assigning fields from the error
    # body, leaving "undefined min" on screen next to "Movie not found". Fixed in
    # test-app/assets/app.js (initTrailer bails out before any assignment when
    # the response has no movie). "trailer runtime" is a scoped POM key (busterblock_pom.yaml)
    # so this checks the exact value field, not just page-wide absence of the text.
    Given User navigates to "{env:BUSTERBLOCK}/trailer.html?id=9999"
    Then User should see "Movie not found"
    And the "trailer runtime" should not contain "undefined"
