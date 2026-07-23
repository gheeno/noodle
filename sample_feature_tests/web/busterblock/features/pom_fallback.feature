@web @pom @fallback @capability
Feature: POM Fallback — resolve elements via page-object aliases

  # When the accessibility tree finds no match for a locator, Noodle falls
  # back to pageobjects/busterblock_pom.yaml in this folder and looks up the
  # step text as a key.
  #
  # Resolution order:
  #   1. Accessibility tree (role + name, placeholder, text, aria-label)  ← free, zero config
  #   2. POM fallback  →  pom.yaml key → CSS / ID / XPath selector        ← when a11y fails
  #
  # Look for this line in output to confirm the fallback fired:
  #   📋 POM: resolved 'genre dropdown' via pom.yaml
  #
  # POM entries in pageobjects/busterblock_pom.yaml:
  #   genre dropdown → id: genre-filter      (readable alias for the <select>)
  #   movie count    → id: movie-count       (a <span> with no aria-label)
  #   catalog heading → css: main h1         (heading element)
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/pom_fallback.feature --headless --no-capture

  Background:
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User waits until "VHS Catalog" is visible

  @smoke @pom_alias
  Scenario: Use a POM alias to select the genre dropdown
    # "genre dropdown" is NOT the element's aria-label ("Filter by genre").
    # The a11y tree finds nothing. POM resolves it to id: genre-filter.
    # Watch output for: 📋 POM: resolved 'genre dropdown' via pom.yaml
    When User selects "Action" from the "genre dropdown"
    Then User should see "Die Hard"

  @smoke @pom_store
  Scenario: Capture text from an element that has no accessible name
    # The movie count <span id="movie-count"> has no aria-label.
    # Direct accessibility lookup returns nothing. POM maps "movie count" → id: movie-count.
    When User stores the "movie count" text as {var:COUNT_TEXT}
    Then {var:COUNT_TEXT} should contain "movies"

  @pom_heading
  Scenario: Grab the catalog heading text via POM alias
    # "catalog heading" maps to css: main h1. The heading includes the movie
    # count badge, so the full text may be "15 movies VHS Catalog".
    When User stores the "catalog heading" as {var:HEADING}
    Then {var:HEADING} should contain "VHS Catalog"
