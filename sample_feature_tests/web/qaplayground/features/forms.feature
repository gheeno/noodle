@web
Feature: QA Playground — Registration Form
  Covers: text/email/tel/date/password fields, textarea, radio, custom dropdown,
  checkbox group, submit, success state.

  Background:
    Given User is on "{env:QAPLAYGROUND}/practice/forms"

  @smoke
  Scenario: Fill and submit the registration form end to end
    When User enters "Jane" in the first-name-input field
    And User enters "Doe" in the last-name-input field
    And User enters "jane.doe@example.com" in the email-input field
    And User enters "9876543210" in the phone-input field
    And User enters "1992-03-04" in the dob-input field
    And User checks the "gender-female-radio" checkbox
    And User clicks the "country-select" button
    And User clicks "india-option"
    And User enters "Mumbai" in the city-input field
    And User enters "Staff QE evaluating the noodle framework" in the bio-input field
    And User checks the "interest-selenium-box" checkbox
    And User checks the "interest-playwright-box" checkbox
    And User enters "Password123" in the password-input field
    And User enters "Password123" in the confirm-password-input field
    And User checks the "terms-box" checkbox
    And User clicks the "submit-btn" button
    And User waits until "success-msg" is visible
    And User takes a screenshot "form-submitted"

  Scenario: Reset clears the form
    When User enters "Throwaway" in the first-name-input field
    And User clicks the "reset-btn" button
    Then the "first-name-input" should have attribute "placeholder" equal to "John"

  Scenario: Custom dropdown via the select step
    When User selects "India" from the country-select
