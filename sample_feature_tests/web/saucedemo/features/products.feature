
Feature: Sauce Demo Products Page

  @web @smoke
  Scenario: Products page shows correct content after login
    Given User is on "{env:SAUCEDEMO}"
    When User enters {env:SAUCE_USERNAME} in the username field
    And User enters {env:SAUCE_PASSWORD} in the password field
    And User clicks the login button
    Then User should see "Products"
    And User should have url containing "inventory"
    And User should not see "Epic sadface"
    And the page title should contain "Swag Labs"

  @web @smoke
  Scenario: Products page works in headless mode
    Given User is on "{env:SAUCEDEMO}"
    When User enters {env:SAUCE_USERNAME} in the username field
    And User enters {env:SAUCE_PASSWORD} in the password field
    And User clicks the login button
    Then User should see "Products"
    And User should have url containing "inventory"

  @web @slow
  Scenario: Add item to cart
    Given User is on "{env:SAUCEDEMO}"
    When User enters {env:SAUCE_USERNAME} in the username field
    And User enters {env:SAUCE_PASSWORD} in the password field
    And User clicks the login button
    And User clicks "Add to cart"
    Then User should see "Remove"
    And User should see "1"
