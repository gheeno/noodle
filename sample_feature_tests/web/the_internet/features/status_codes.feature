@web
Feature: Status Codes
  Covers: pages that render a given HTTP status code in their body text.

  @smoke
  Scenario Outline: Visiting a status code page shows that code
    Given User is on "{env:THE_INTERNET}/status_codes/<code>"
    Then User should see "This page returned a <code> status code"

    Examples:
      | code |
      | 200  |
      | 301  |
      | 404  |
      | 500  |
