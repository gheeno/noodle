@web
Feature: Form Authentication
  Covers: valid login, invalid username, invalid password, logout.

  Background:
    Given User is on "{env:THE_INTERNET}/login"

  @smoke
  Scenario: Valid credentials log the user into the secure area
    When User enters {env:THE_INTERNET_USERNAME} in the username field
    And User enters {env:THE_INTERNET_PASSWORD} in the password field
    And User clicks the login button
    Then User should see "You logged into a secure area!"
    And User should have url containing "/secure"

  Scenario: Invalid username shows an error
    When User enters "not_a_real_user" in the username field
    And User enters {env:THE_INTERNET_PASSWORD} in the password field
    And User clicks the login button
    Then User should see "Your username is invalid!"

  Scenario: Invalid password shows an error
    When User enters {env:THE_INTERNET_USERNAME} in the username field
    And User enters "wrong_password" in the password field
    And User clicks the login button
    Then User should see "Your password is invalid!"

  Scenario: Logout returns the user to the login page
    When User enters {env:THE_INTERNET_USERNAME} in the username field
    And User enters {env:THE_INTERNET_PASSWORD} in the password field
    And User clicks the login button
    And User clicks the "Logout" link
    Then User should see "You logged out of the secure area!"
    And User should have url containing "/login"
