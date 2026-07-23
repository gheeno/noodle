@web @navigation @capability
Feature: Navigation — move between pages, history, tabs

  # Patterns demonstrated:
  #   navigates to '...' / is on '...' / opens '...' / goes to '...'
  #   goes back / goes forward / reloads page
  #   URL assertion / title assertion
  #   a new tab should open / switches to tab / closes tab
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/navigation.feature --headless

  Background:
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"

  @smoke @navigate
  Scenario: Four equivalent navigate phrasings
    # All four resolve to the same 'navigate' action — use whichever reads most naturally.
    When User navigates to "{env:BUSTERBLOCK}/catalog.html"
    Then User should see "VHS Catalog"
    When User opens "{env:BUSTERBLOCK}/catalog.html"
    Then User should see "VHS Catalog"
    When User goes to "{env:BUSTERBLOCK}/catalog.html"
    Then User should see "VHS Catalog"
    When User is on "{env:BUSTERBLOCK}/catalog.html"
    Then User should see "VHS Catalog"

  @smoke @url_assert
  Scenario: Assert the current URL contains a fragment
    When User navigates to "{env:BUSTERBLOCK}/catalog.html"
    Then User should have url containing "catalog"

  @title_assert
  Scenario: Assert the page title
    When User navigates to "{env:BUSTERBLOCK}/catalog.html"
    Then the page title should contain "BusterBlock"

  @smoke @back_forward @precondition:reset_state
  Scenario: Browser history — back and forward
    When User clicks "Add to Cart"
    And User clicks "View cart"
    Then User should see "Your Cart"
    When User goes back
    Then User should see "VHS Catalog"
    When User goes forward
    Then User should see "Your Cart"

  @reload
  Scenario: Reload the page
    When User reloads the page
    Then User should see "VHS Catalog"

  @smoke @new_tab @tab_switch
  Scenario: A click opens a new tab — assert content, then switch back
    When User clicks "Preview"
    Then a new tab should open
    And User should see "Director"
    When User switches to the previous tab
    Then User should see "VHS Catalog"

  @new_tab @close_tab @precondition:reset_state
  Scenario: Checkout receipt opens in a new tab, close it, return to cart
    When User clicks "Add to Cart"
    And User clicks "View cart"
    And User clicks "Checkout"
    Then a new tab should open
    And User should see "Thank you for renting"
    When User switches to the previous tab
    Then User should see "Your Cart"
