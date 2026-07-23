@web @assertions @capability
Feature: Assertions — every assertion pattern Noodle supports

  # Patterns demonstrated:
  #   should see 'X' / should not see 'X'     — element/text visible or hidden
  #   should see N 'X' items                  — count assertion
  #   should have url containing 'X'           — URL fragment assertion
  #   page title should contain 'X'            — title assertion
  #   the 'X' field should contain 'Y'         — input value assertion
  #   the 'X' should be enabled/disabled       — element state assertion
  #   the 'X' should have attribute 'Y' of 'Z' — attribute assertion
  #   {var:VAR} should contain / equal             — stored variable assertion
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/assertions.feature --headless

  Background:
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User waits until "VHS Catalog" is visible

  @smoke @visible
  Scenario: Assert element / text is visible
    # "should see X" passes when the element or text is in the DOM and visible.
    Then User should see "VHS Catalog"
    And User should see "Die Hard"
    And User should see "Back to the Future"

  @smoke @hidden
  Scenario: Assert element / text is not visible
    # "should not see X" passes when the element is absent or hidden.
    Then User should not see "Invalid credentials"
    And User should not see "Your Cart"

  @smoke @url_assert
  Scenario: Assert the current URL
    Then User should have url containing "catalog"

  @title_assert
  Scenario: Assert the page title
    Then the page title should contain "BusterBlock"

  @smoke @count
  Scenario: Assert a count of visible elements
    # "should see N 'X' items" counts all visible elements matching 'X'.
    # The catalog loads 50 movies; each has an "Add to Cart" button.
    Then User should see 50 "Add to Cart" items

  @state @enabled @precondition:reset_state
  Scenario: Assert element is enabled / disabled
    # Genre filter is a <select> — enabled by default.
    Then the "genre filter" should be enabled
    # Checkout button on an empty cart is disabled.
    When User navigates to "{env:BUSTERBLOCK}/cart.html"
    Then the "Checkout" should be disabled

  @value_assert
  Scenario: Assert a text input field's current value
    # After typing, verify what is in the field.
    When User enters "searchterm" in the search movies field
    Then the "search movies" field should contain "searchterm"

  @smoke @variable_assert
  Scenario: Store element text, then assert the stored value
    # Store → {var:VAR} — then assert the variable against a literal.
    When User stores the "VHS Catalog" heading as {var:HEADING}
    Then {var:HEADING} should contain "VHS Catalog"

  @compare
  Scenario: Variable comparison operators
    # All comparison operators work on stored or set variables.
    Given User sets {var:PRICE} to '3.99'
    Then {var:PRICE} should equal '3.99'
    And {var:PRICE} should not equal '0.00'
    And {var:PRICE} should be greater than '1.00'
    And {var:PRICE} should be less than '10.00'
