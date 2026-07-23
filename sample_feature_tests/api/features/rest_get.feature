@api @rest @rest_get @capability
Feature: REST GET — read requests and status assertions

  # Noodle's REST client is separate from the browser. It sends real HTTP
  # requests (not Playwright browser fetches) with full header control.
  #
  # Patterns demonstrated:
  #   performs a GET call at 'URL'                              — simple GET
  #   performs a GET call at 'URL' storing the response in {var:R} — capture body
  #   the response status should be 200                         — status assertion
  #   the response body should contain 'X'                      — body substring
  #   {var:VAR} should contain 'X'                                  — variable assertion
  #
  # Also shown: run_command with curl for full output capture (SCRIPT_OUTPUT).
  #
  # Run:  noodle run sample_feature_tests/api/features/rest_get.feature --no-capture
  # Needs network: hits https://api.restful-api.dev (public sandbox, no auth)

  Background:
    Given sets {var:REST_BASE_URL} to '{env:RESTFULAPI}'

  @smoke @get_status
  Scenario: GET a known object — assert status 200
    When performs a GET call at '/objects/1'
    Then the response status should be 200

  @smoke @get_body
  Scenario: GET a known object — assert response body content
    When performs a GET call at '/objects/1' storing the response in {var:OBJ}
    Then the response status should be 200
    And the response body should contain 'Google Pixel 6 Pro'

  @get_var
  Scenario: Capture the response body and assert the stored variable
    When performs a GET call at '/objects/2' storing the response in {var:OBJ2}
    Then {var:OBJ2} should contain 'Apple iPhone'

  @run_command @get_curl
  Scenario: GET via curl — full stdout captured in SCRIPT_OUTPUT
    # run_command gives you raw stdout, useful when you need to chain the result
    # into a later step without a dedicated assertion step.
    When User runs the command 'curl -s "{env:RESTFULAPI}/objects/1"' and storing the output in {var:CURL_OUT}
    Then {var:CURL_OUT} should contain 'Google Pixel 6 Pro'
    And {var:CURL_OUT} should contain 'id'

  @get_list
  Scenario: GET all objects — assert the list is not empty
    When performs a GET call at '/objects' storing the response in {var:ALL_OBJECTS}
    Then the response status should be 200
    And {var:ALL_OBJECTS} should contain 'id'
