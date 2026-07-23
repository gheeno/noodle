@api @rest @rest_assertions @capability
Feature: REST Assertions — assert status, body, and headers

  # Noodle's REST layer provides three assertion families after a call:
  #
  #   Status:   the response status should be N
  #   Body:     the response body should contain 'X'
  #             the response body should contain: [table]   ← key/value rows
  #   Headers:  the response header 'X' should be 'Y'
  #             the response headers should contain: [table]
  #
  # All assertions operate on the LAST completed REST call in the scenario.
  #
  # Run:  noodle run sample_feature_tests/api/features/rest_assertions.feature --no-capture

  Background:
    Given sets {var:REST_BASE_URL} to '{env:RESTFULAPI}'

  @smoke @assert_status
  Scenario: Assert response status code
    When performs a GET call at '/objects/1'
    Then the response status should be 200

  @smoke @assert_body_single
  Scenario: Assert a single substring in the response body
    When performs a GET call at '/objects/1'
    Then the response body should contain 'Google Pixel 6 Pro'

  @smoke @assert_body_table @table_step
  Scenario: Assert multiple fields with a table — body contains key/value pairs
    # Table rows: first column = JSON key, second column = expected value.
    # An empty Value cell asserts that the key EXISTS anywhere in the body text.
    When performs a GET call at '/objects/1'
    Then the response body should contain:
      | Key    | Value            |
      | name   | Google Pixel 6   |
      | id     |                  |

  @assert_header
  Scenario: Assert a response header value
    When performs a GET call at '/objects/1'
    Then the response header 'Content-Type' should contain 'application/json'

  @smoke @assert_body_var
  Scenario: Capture body into variable and assert the variable
    # The stored variable can be used with any comparison operator.
    When performs a GET call at '/objects/1' storing the response in {var:BODY}
    Then {var:BODY} should contain 'Google Pixel 6 Pro'
    And {var:BODY} should contain 'id'
    And {var:BODY} should not equal ''
