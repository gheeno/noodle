@web @nood_0062 @outline
Feature: NOOD_0062 — Scenario Outlines with complex multi-parameter steps
  Placeholders in quoted and unquoted positions, several parameters in one
  step, a parameter reused twice, and multiple Examples blocks.

  Background:
    Given the login page with the url value of '{env:BUSTERBLOCK}'

  Scenario Outline: Login as <username> — placeholders in fills, clicks and asserts
    When the user enters '<username>' as the username
    And the user provides '<password>' for the password field
    And the user clicked the button with a label 'Login'
    Then the page contains the text '<result>'

    Examples: Valid users
      | username   | password  | result      |
      | reel_ryan  | Popcorn1! | VHS Catalog |
      | tape_tanya | Rewind2#  | VHS Catalog |

    Examples: Invalid credentials
      | username | password | result              |
      | bad_user | bad_pass | Invalid credentials |

  Scenario Outline: Multi-parameter cell assertion — three placeholders in one step
    When the user fills in the form with:
      | field    | value         |
      | username | {env:BB_USER} |
      | password | {env:BB_PASS} |
    And the user clicks on the Login button
    And the user waits for the 'VHS Catalog'
    Then the cell under '<column>' in the row containing '<movie>' should be '<expected>'
    And the element with the text value of '<movie>' is seen

    Examples:
      | movie              | column   | expected         |
      | Jaws               | Director | Steven Spielberg |
      | Die Hard           | Year     | 1988             |
      | Back to the Future | Genre    | Sci-Fi           |

  Scenario Outline: Numeric placeholder in a timed wait
    When the user waits <n> seconds
    Then the 'Login' button is visible on the page

    Examples:
      | n |
      | 1 |
