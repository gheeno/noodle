@web @resource @payload @capability
Feature: Resource Files — load JSON payloads and fixtures from disk

  # Test data that is too large or too structured to inline in a step lives in
  # the app's resources/ folder (sibling of this features/ folder) and is
  # loaded by a step. The content is stored in {var:PAYLOAD} (last loaded) and
  # {var:PAYLOAD_<STEM>} (by filename stem) so later steps can reference either
  # variable.
  #
  # Patterns demonstrated:
  #   uses this payload 'path/to/file.json'   — single file → {var:PAYLOAD}
  #   uses these payloads: [table]             — multiple files → named vars
  #
  # The path is relative to the app's resources/ folder.
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/resource_files.feature --no-capture

  @smoke @single_payload
  Scenario: Load a single JSON payload and POST it to the BusterBlock API
    # 1. Load seed_cart.json from resources/payloads/ → stored in {var:PAYLOAD}.
    # 2. POST to the BusterBlock seed-cart endpoint with that body.
    # 3. Assert the API accepted it (status 200).
    Given sets {var:REST_BASE_URL} to '{env:BUSTERBLOCK}'
    And uses this payload 'payloads/seed_cart.json'
    When performs a POST call at '/api/test/seed-cart' with body '{var:PAYLOAD}'
    Then the response status should be 200

  @multi_payload @table_step
  Scenario: Load multiple payloads from a table — each stored by filename stem
    # Two files loaded: PAYLOAD_SEED_CART and PAYLOAD_SEED_CART (last wins for PAYLOAD).
    # In a real scenario you'd load files with different stems so each gets its own var.
    # This shows the table-driven syntax.
    Given uses these payloads:
      | payload                   |
      | payloads/seed_cart.json   |
    And {var:PAYLOAD} should contain "reel_ryan"
