@web @outline @table @capability
Feature: Scenario Outline and Data Tables — parameterised and table-driven tests

  # Patterns demonstrated:
  #   Scenario Outline + Examples table  — one definition, N test runs
  #   Multiple Examples blocks           — group cases by label (happy/sad paths)
  #   Doc String in a step               — multi-line string parameter
  #   Inline data table in a step        — table-driven step body
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/scenario_outline.feature --headless

  @smoke @outline_basic
  Scenario Outline: Genre filter — one outline, multiple genre/movie combos
    # Behave expands each row in Examples into a separate scenario run.
    # <genre> and <expected_movie> are substituted per row.
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    And User waits until "VHS Catalog" is visible
    When User selects "<genre>" from the genre filter
    Then User should see "<expected_movie>"

    Examples:
      | genre  | expected_movie     |
      | Action  | Die Hard           |
      | Thriller| Jaws               |
      | Sci-Fi  | Back to the Future |

  @outline_multi_examples
  Scenario Outline: Login — happy and sad paths grouped in separate Examples blocks
    # Multiple Examples: blocks let you label groups of test data.
    # Behave runs ALL rows from ALL Examples blocks for this outline.
    Given User is on "{env:BUSTERBLOCK}"
    When User enters <username> in the username field
    And User enters <password> in the password field
    And User clicks the login button
    Then User should see <result>

    Examples: Valid users
      | username    | password  | result       |
      | reel_ryan   | Popcorn1! | VHS Catalog  |
      | tape_tanya  | Rewind2#  | VHS Catalog  |

    Examples: Invalid credentials
      | username    | password  | result               |
      | bad_user    | bad_pass  | Invalid credentials  |

  @table_step
  Scenario: Table-driven step body — load multiple payloads from a table
    # When a step ends in ':' and is followed by a table, Behave passes the table
    # to the step as context.table. Noodle's load_resource step uses this to
    # load multiple files in one step. Each row is processed in order.
    # See also: resource_files.feature for the single-file variant.
    Given sets {var:REST_BASE_URL} to '{env:BUSTERBLOCK}'
    And uses these payloads:
      | payload                   |
      | payloads/seed_cart.json   |
