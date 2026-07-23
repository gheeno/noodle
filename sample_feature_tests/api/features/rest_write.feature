@api @rest @rest_write @capability
Feature: REST Write — POST, PUT, PATCH, DELETE with request bodies

  # Patterns demonstrated:
  #   performs a POST call at 'URL' with body '...'
  #   performs a PUT call at 'URL' with body '...'
  #   performs a PATCH call at 'URL' with body '...'
  #   performs a DELETE call at 'URL'
  #   sets a request header 'X' to 'Y'            — header before the call
  #   the response status should be 200 / 201 / 204
  #
  # For large or reusable bodies, load from a file — see rest_resource_payload.feature.
  # For full CRUD chain with variable extraction — see rest_lifecycle.feature.
  #
  # Run:  noodle run sample_feature_tests/api/features/rest_write.feature --no-capture

  # NOTE: the sandbox's pre-seeded demo objects (low numeric ids like 7) are
  # read-only — PUT/PATCH/DELETE on them now returns 405. Each scenario below
  # creates its own throwaway object in the Background and writes to that,
  # same pattern as rest_lifecycle.feature.
  Background:
    Given sets {var:REST_BASE_URL} to '{env:RESTFULAPI}'
    And sets a request header 'Content-Type' to 'application/json'
    When performs a POST call at '/objects' with body '{"name":"Noodle Test Device","data":{"year":2026}}'
    And extracts 'id' from the response storing in {var:DEVICE_ID}

  @smoke @post
  Scenario: POST — create a new object
    Then the response status should be 200
    And the response body should contain 'Noodle Test Device'
    And the response body should contain 'id'

  @put
  Scenario: PUT — full replacement of a freshly created object
    When performs a PUT call at '/objects/{var:DEVICE_ID}' with body '{"name":"Noodle Updated","data":{"year":2026}}'
    Then the response status should be 200
    And the response body should contain 'Noodle Updated'

  @patch
  Scenario: PATCH — partial update of a freshly created object
    When performs a PATCH call at '/objects/{var:DEVICE_ID}' with body '{"name":"Noodle Patched"}'
    Then the response status should be 200
    And the response body should contain 'Noodle Patched'

  @delete
  Scenario: DELETE — remove the freshly created object
    When performs a DELETE call at '/objects/{var:DEVICE_ID}'
    Then the response status should be 200

  @run_command @post_curl
  Scenario: POST via curl — full control over Content-Type and response parsing
    # When you need strict Content-Type or want to parse a field from the JSON
    # response in a later step, drop to run_command with curl.
    When User runs the command 'curl -s -X POST "{env:RESTFULAPI}/objects" -H "Content-Type: application/json" -d "{\"name\":\"curl-test\"}"' and storing the output in {var:POST_OUT}
    Then {var:POST_OUT} should contain 'curl-test'
    And {var:POST_OUT} should contain 'id'
