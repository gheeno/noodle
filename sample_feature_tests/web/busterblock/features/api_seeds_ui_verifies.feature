@web @busterblock @combo @capability
Feature: Cross-wok — REST seeds the data, the browser proves the customer sees it

  # NOOD_0201 — the combo shape, end to end against one local app.
  #
  # Tagged @web, NEVER @api: `@api` means "start no browser", which would kill
  # the UI half of the scenario. REST steps need no tag at all — they are plain
  # I/O available in every scenario, which is what makes one file able to seed
  # over HTTP and then assert in the DOM.
  #
  # Both directions are covered:
  #   @seed    API creates → the page renders it   (fast setup, no UI clicking)
  #   @verify  the UI submits → the API confirms it (the write really persisted)
  #
  # Needs BusterBlock running:  cd test-apps/busterblock && npm start
  # Run:  noodle run sample_feature_tests/web/busterblock/features/api_seeds_ui_verifies.feature --headless

  Background:
    Given sets {var:REST_BASE_URL} to '{env:BUSTERBLOCK}'
    And sets a request header 'Content-Type' to 'application/json'
    And waits until '/api/health' returns status 200 within 20 seconds
    When performs a POST call at '/api/test/reset'
    Then the response status should be 200

  @smoke @seed
  Scenario: Reviews seeded over REST appear on the reviews page
    # Setup at HTTP speed: four reviewers in one step, every call asserted.
    # Doing this through the form would be four page loads and twelve clicks.
    When performs a POST call at '/api/reviews/new' with body '{"name":"{name}","rating":{rating}}' for each row expecting status 201:
      | name          | rating |
      | Ada Lovelace  | 5      |
      | Grace Hopper  | 5      |
      | Alan Turing   | 4      |

    # Now the part only a browser can answer: did the customer actually see it?
    Given User is on "{env:BUSTERBLOCK}/reviews.html"
    Then User should see "Showing 3 of 3 reviews"
    And User should see "Ada Lovelace"
    And User should see "Grace Hopper"
    And User should see "Alan Turing"
    # The app's generated reply is rendered too, not just the submitted name.
    And User should see "Thanks for the review, Ada Lovelace!"

  @verify
  Scenario: A review submitted in the browser really persists
    Given User is on "{env:BUSTERBLOCK}/reviews.html"
    Then User should see "No reviews yet."

    When User enters "Vera Rubin" in the "Your name" field
    And User enters "Galaxies rotate; so do these reels." in the "Comment" field
    And User clicks "Submit review"
    Then User should see "Thanks for the review, Vera Rubin!"
    And User should see "Showing 1 of 1 reviews"

    # The UI said it saved. The API is the witness that it did — a rendered
    # confirmation is not proof of persistence.
    When performs a GET call at '/api/reviews?page=1&size=10'
    Then the response status should be 200
    And the response json 'total' should equal '1'
    And the response json 'items[0].name' should equal 'Vera Rubin'
    And the response json 'items[0].comment' should contain 'Galaxies rotate'

  @seed @negative
  Scenario: A rejected submission leaves the page empty
    When User is on "{env:BUSTERBLOCK}/reviews.html"
    And User clicks "Submit review"
    Then User should see "Could not save your review: name is required"
    And User should see "No reviews yet."
    # And the store never moved.
    When performs a GET call at '/api/reviews'
    Then the response json 'total' should equal '0'
