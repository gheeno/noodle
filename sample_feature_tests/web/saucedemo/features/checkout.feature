
Feature: Sauce Demo Checkout

  @web @smoke
  Scenario: User completes a purchase end to end

    Given User is on "{env:SAUCEDEMO}"
    When User enters {env:SAUCE_USERNAME} in the username field
    And User enters {env:SAUCE_PASSWORD} in the password field
    And User clicks the login button
    Then User should see "Products"

    When User clicks "Add to cart"
    Then User should see "1"

    When User clicks the shopping cart
    Then User should have url containing "cart"
    And User should see "Your Cart"

    When User clicks "Checkout"
    Then User should have url containing "checkout-step-one"

    When User enters "Jane" in the first name field
    And User enters "Doe" in the last name field
    And User enters "12345" in the zip code field
    And User clicks "Continue"
    Then User should have url containing "checkout-step-two"
    And User should see "Payment Information"

    When User clicks "Finish"
    Then User should see "Thank you for your order!"
    And User should have url containing "checkout-complete"

  @web @smoke
  Scenario: User cancels checkout and returns to products

    Given User is on "{env:SAUCEDEMO}"
    When User enters {env:SAUCE_USERNAME} in the username field
    And User enters {env:SAUCE_PASSWORD} in the password field
    And User clicks the login button
    And User clicks "Add to cart"
    And User clicks the shopping cart
    And User clicks "Checkout"
    When User clicks "Cancel"
    Then User should have url containing "cart"
