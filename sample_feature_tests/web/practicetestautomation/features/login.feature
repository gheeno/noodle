@web
Feature: Login
  Covers: practicetestautomation.com's login practice page — valid login,
  invalid username, invalid password.

  Background:
    Given User is on "{env:PRACTICETESTAUTOMATION}/practice-test-login/"

  @smoke
  Scenario: Valid user logs in successfully
    When User enters {env:PTA_USERNAME} in the username field
    And User enters {env:PTA_PASSWORD} in the password field
    And User clicks the login button
    Then User should see "Logged In Successfully"
    And User should see "Log out"

  Scenario: Invalid username shows an error
    When User enters "wrong" in the username field
    And User enters {env:PTA_PASSWORD} in the password field
    And User clicks the login button
    Then User should see "Your username is invalid!"

  Scenario: Invalid password shows an error
    When User enters {env:PTA_USERNAME} in the username field
    And User enters "wrong" in the password field
    And User clicks the login button
    Then User should see "Your password is invalid!"
