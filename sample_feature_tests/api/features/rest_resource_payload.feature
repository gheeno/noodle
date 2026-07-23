@api @rest @rest_resource_payload @resource @payload @capability
Feature: REST Resource Payload — load request bodies from files

  # For large or shared request bodies, store them in resources/payloads/ and
  # reference them by relative path. The file content is stored in {var:PAYLOAD}
  # (and {var:PAYLOAD_<STEM>} for the named-variable form) and can be used in
  # any subsequent REST call step.
  #
  # Patterns demonstrated:
  #   uses this payload 'payloads/file.json'         — single file → {var:PAYLOAD}
  #   uses these payloads: [table]                   — multiple files → named vars
  #   extracts 'key' from the response storing in {var:V} — JSON key extraction
  #
  # Run:  noodle run sample_feature_tests/api/features/rest_resource_payload.feature --no-capture

  Background:
    Given sets {var:REST_BASE_URL} to '{env:RESTFULAPI}'
    And sets a request header 'Content-Type' to 'application/json'

  @smoke @single_payload
  Scenario: POST with a payload loaded from a JSON file
    # uses this payload loads resources/payloads/create_device.json.
    # Content is stored in {var:PAYLOAD}. The POST body references {var:PAYLOAD}.
    Given uses this payload 'payloads/create_device.json'
    When performs a POST call at '/objects' with body '{var:PAYLOAD}'
    Then the response status should be 200
    And the response body should contain 'Noodle Resource Device'
    And the response body should contain 'id'

  @multi_payload @chained
  Scenario: POST then PUT using two payload files chained by extracted ID
    # Load both files up front into named variables, then chain them through
    # a create → update flow using the extracted ID.
    Given uses these payloads:
      | payload                      |
      | payloads/create_device.json  |
      | payloads/update_device.json  |

    # CREATE with the first payload
    When performs a POST call at '/objects' with body '{var:PAYLOAD_CREATE_DEVICE}'
    Then the response status should be 200
    When extracts 'id' from the response storing in {var:DEV_ID}

    # UPDATE with the second payload — the extracted ID is injected into the URL
    When performs a PUT call at '/objects/{var:DEV_ID}' with body '{var:PAYLOAD_UPDATE_DEVICE}'
    Then the response status should be 200
    And the response body should contain 'Noodle Updated Device'
