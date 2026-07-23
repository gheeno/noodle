@web @nood_0062 @tables
Feature: NOOD_0062 — Data tables, with and without label headers
  Table-driven steps: labelled | field | value | tables, headerless tables
  (first row is data, not labels — NOOD_0062 keeps it), column-name-headed
  row tables, and {env:X} substitution inside cells.

  Background:
    Given the login page with the url value of '{env:BUSTERBLOCK}'

  Scenario: Labelled form-fill table with env refs in cells
    When the user fills in the form with:
      | field    | value         |
      | username | {env:BB_USER} |
      | password | {env:BB_PASS} |
    And the user clicks on the Login button
    Then the user sees 'VHS Catalog'

  Scenario: Headerless form-fill table — first row must not be dropped
    When the user fills in the form with:
      | username | {env:BB_USER} |
      | password | {env:BB_PASS} |
    And the user clicks on the Login button
    Then the user sees 'VHS Catalog'

  Scenario: Column-aware row values and column lists
    When the user fills in the form with:
      | field    | value         |
      | username | {env:BB_USER} |
      | password | {env:BB_PASS} |
    And the user clicks on the Login button
    And the user waits for the 'VHS Catalog'
    Then the row containing 'Jaws' should have values:
      | column   | value            |
      | Year     | 1975             |
      | Genre    | Thriller         |
      | Director | Steven Spielberg |
    And the table should have columns:
      | column |
      | Title  |
      | Year   |
      | Genre  |
    And the grid should contain rows:
      | Title    | Year | Genre    |
      | Jaws     | 1975 | Thriller |
      | Die Hard | 1988 | Action   |

  Scenario: Headerless single-column table — column names as data
    When the user fills in the form with:
      | username | {env:BB_USER} |
      | password | {env:BB_PASS} |
    And the user clicks on the Login button
    And the user waits for the 'VHS Catalog'
    Then the table should have columns:
      | Director |
      | Cast     |
      | Price    |
