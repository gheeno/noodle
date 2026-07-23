@api @rest @rest_lifecycle @capability
Feature: REST Lifecycle — full CRUD with variable chaining

  # The complete create → read → update → patch → delete cycle.
  # Extracted JSON fields (via 'extracts key from response') chain the
  # created object's ID through every subsequent step.
  #
  # Patterns demonstrated:
  #   performs a POST call at '...' storing the response in {var:VAR}
  #   extracts 'key' from the response storing in {var:VAR}
  #   {var:VAR} used in a URL:  '/objects/{var:DEVICE_ID}'
  #   the response status should be 200
  #   the response body should contain 'X'
  #
  # Run:  noodle run sample_feature_tests/api/features/rest_lifecycle.feature --no-capture

  Background:
    Given sets {var:REST_BASE_URL} to '{env:RESTFULAPI}'
    And sets a request header 'Content-Type' to 'application/json'

  @smoke @crud
  Scenario: Full CRUD lifecycle — create, read, update, patch, delete
    # CREATE
    When performs a POST call at '/objects' with body '{"name":"Noodle Lifecycle Device","data":{"year":2026,"tester":"lifecycle_test"}}' storing the response in {var:CREATE_RESP}
    Then the response status should be 200
    And the response body should contain 'Noodle Lifecycle Device'

    # Extract the new object's ID from the response body for use in all later steps
    When extracts 'id' from the response storing in {var:DEVICE_ID}
    Then {var:DEVICE_ID} should not equal ''

    # READ — confirm the object exists
    When performs a GET call at '/objects/{var:DEVICE_ID}' storing the response in {var:GET_RESP}
    Then the response status should be 200
    And {var:GET_RESP} should contain 'Noodle Lifecycle Device'

    # UPDATE (full replace)
    When performs a PUT call at '/objects/{var:DEVICE_ID}' with body '{"name":"Noodle Updated","data":{"year":2026}}'
    Then the response status should be 200
    And the response body should contain 'Noodle Updated'

    # PATCH (partial)
    When performs a PATCH call at '/objects/{var:DEVICE_ID}' with body '{"name":"Noodle Patched"}'
    Then the response status should be 200
    And the response body should contain 'Noodle Patched'

    # DELETE
    When performs a DELETE call at '/objects/{var:DEVICE_ID}'
    Then the response status should be 200

    # CONFIRM DELETED — GET should return 404
    When performs a GET call at '/objects/{var:DEVICE_ID}'
    Then the response status should be 404
