@web @tables @capability
Feature: Tables & Grids — cells, headers, rows, columns and scrollbars

  # Patterns demonstrated (NOOD_0011):
  #   the cell under '<header>' in the row containing '<row>' should be 'X'
  #   the row containing 'X' should have values …        (inline + table-driven)
  #   the table should have columns …                    (inline + table-driven)
  #   the 'X' column should contain …                    (inline + table-driven)
  #   the grid should contain rows:                      (headings = column names)
  #   clicks the cell under 'X' in the row containing 'Y'
  #   scrolls the table right / left / to the bottom / to the top
  #   fills in the form with:                            (| field | value | table)
  #   saves the browser session as 'X'                   (reuse via NOODLE_STORAGE_STATE)
  #
  # The same steps work on ARIA grids (role=grid / role=gridcell) — Dynamics 365,
  # AG Grid, Power Apps — not just HTML <table>s: rows resolve via role=row and
  # cells fall back from role=cell to role=gridcell automatically.
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/tables_and_grids.feature --headless

  Background:
    # Table-driven form fill — one step, many fields. Cell values go through
    # the same {env:X}/{var:X} substitution as step text.
    Given User is on "{env:BUSTERBLOCK}"
    When User fills in the form with:
      | field    | value     |
      | username | {env:BB_USER} |
      | password | {env:BB_PASS} |
    And User clicks the login button
    Then User waits until "VHS Catalog" is visible

  @smoke @cell_under_header
  Scenario: Assert a cell under a named header
    Then the cell under 'Director' in the row containing 'Jaws' should be 'Steven Spielberg'
    And the cell under 'Year' in the row containing 'Die Hard' should be '1988'
    And the cell under 'Genre' in the row containing 'Back to the Future' should be 'Sci-Fi'

  @smoke @row_values
  Scenario: Row values — order-free inline list
    # Values just have to appear somewhere in the row — no column mapping.
    Then the row containing 'Jaws' should have values '1975', 'Thriller' and 'Steven Spielberg'

  @row_values_table
  Scenario: Row values — column-aware, table-driven
    # Each value is checked in the cell under the named header of that row.
    Then the row containing 'Jaws' should have values:
      | column   | value            |
      | Year     | 1975             |
      | Genre    | Thriller         |
      | Director | Steven Spielberg |
      | Price    | $3.99            |

  @headers
  Scenario: The table has the expected column headers
    Then the table should have columns 'Title', 'Year' and 'Genre'
    And the table should have columns:
      | column   |
      | Director |
      | Cast     |
      | Runtime  |
      | Format   |
      | Stock    |
      | Price    |
      | Rating   |
      | Actions  |

  @column_values
  Scenario: Column contents — a value appears somewhere in a named column
    Then the 'Genre' column should contain 'Thriller'
    And the 'Director' column should contain:
      | value            |
      | Steven Spielberg |
      | John McTiernan   |
      | Robert Zemeckis  |

  @whole_rows
  Scenario: The grid contains whole rows — headings are column names
    # The first cell of each row identifies the row in the grid.
    Then the grid should contain rows:
      | Title              | Year | Director         |
      | Jaws               | 1975 | Steven Spielberg |
      | Die Hard           | 1988 | John McTiernan   |
      | Back to the Future | 1985 | Robert Zemeckis  |

  @scrollbars
  Scenario: Drive the grid's own scrollbars, not the page's
    # bottom/top jump to the edge; right/left/down/up move about a page.
    # Virtualised grids (Dynamics 365) render rows as you scroll — scroll,
    # then assert.
    # Row keys are contains-matches — 'Heat' would hit Poltergeist first
    # (cast: Heather O'Rourke), so use a title no other row's text contains.
    When User scrolls the table to the bottom
    Then the cell under 'Year' in the row containing 'Shawshank' should be '1994'
    When User scrolls the table right
    And User scrolls the table to the top
    Then the cell under 'Year' in the row containing 'Jaws' should be '1975'

  @cell_click
  Scenario: Click a cell under a named header
    When User clicks the cell under 'Title' in the row containing 'Die Hard'
    Then User should see "Die Hard"

  @outline @cell_outline
  Scenario Outline: One outline asserting cells across many rows
    Then the cell under '<column>' in the row containing '<movie>' should be '<value>'

    Examples:
      | movie              | column   | value            |
      | Jaws               | Director | Steven Spielberg |
      | Die Hard           | Year     | 1988             |
      | Back to the Future | Genre    | Sci-Fi           |

  @session
  Scenario: Save the logged-in session for reuse across runs
    # Reuse it: NOODLE_STORAGE_STATE=reports/busterblock-session.json noodle run …
    # Every scenario then starts already authenticated — the standard answer
    # to SSO/MFA login walls.
    When User saves the browser session as 'reports/busterblock-session.json'

  @api_auth
  Scenario: Token-guarded api_call — bearer token applies to setup calls
    # Fetch a real JWT from the login API, then hit a protected endpoint with
    # the quick api_call step — the bearer token set for REST guards it too.
    Given sets {var:REST_BASE_URL} to '{env:BUSTERBLOCK}'
    When performs a POST call at '/api/auth/login' with body '{"username": "{env:BB_USER}", "password": "{env:BB_PASS}"}'
    Then the response status should be 200
    When extracts 'token' from the response storing in {var:TOKEN}
    And sets the bearer token to '{var:TOKEN}'
    And User calls POST '{env:BUSTERBLOCK}/api/cart' with body '{"movieId": 1, "qty": 1}'
