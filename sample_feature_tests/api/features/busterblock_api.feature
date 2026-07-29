@api @rest @busterblock_api @capability
Feature: BusterBlock Reviews API — the api wok against a local service

  # NOOD_0201 — the api wok's worked example on a LOCAL target. The public
  # sample API used by the other rest_*.feature files is fine for read-only
  # demos but cannot show the shapes that matter most: a 201-create, a
  # 400-rejection that persists nothing, a paginated list, a bearer-protected
  # delete, or a response-shape contract.
  #
  # Capabilities shown (NOOD_0201 unless noted):
  #   @readiness   waits until '<path>' returns status <code>  — polling, not a sleep
  #   @typed       the response json '<path>' should equal/have N items — typed, not substring
  #   @schema      the response should match the schema '<file>' — the whole shape in one step
  #   @docstring   performs a POST call at '<path>' with this body: + """ … """
  #   @payload     uses this payload '<file>'                     (NOOD_0016)
  #   @auth        bearer token extracted from a login response   (NOOD_0007)
  #   @negative    a rejected request persists nothing
  #
  # Needs BusterBlock running:  cd test-apps/busterblock && npm start
  # The @auth scenario also needs credentials: copy
  # ../../web/busterblock/resources/busterblock_secrets.env.example to
  # busterblock_secrets.env (gitignored) — {env:BB_USER}/{env:BB_PASS}.
  # Run:  noodle run sample_feature_tests/api/features/busterblock_api.feature --headless

  Background:
    Given sets {var:REST_BASE_URL} to '{env:BUSTERBLOCK_API}'
    And sets a request header 'Content-Type' to 'application/json'
    # Readiness as a poll, not a hard sleep: it returns the instant the service
    # answers, and fails with the budget named if it never does.
    And waits until '/api/health' returns status 200 within 20 seconds
    # Universal teardown seam — every scenario starts from zero reviews.
    When performs a POST call at '/api/test/reset'
    Then the response status should be 200

  @smoke @typed @schema
  Scenario: Submitting a review returns the stored record
    When performs a POST call at '/api/reviews/new' with body '{"name":"Reel Ryan","rating":5,"comment":"Rewound and ready."}'
    Then the response status should be 201
    # Typed assertions: 5 is the number 5, not the substring "5" — a response
    # holding "rating":50 would pass `body should contain '5'` and fail here.
    And the response json 'name' should equal 'Reel Ryan'
    And the response json 'rating' should equal '5'
    And the response json 'response' should contain 'Thanks for the review'
    # The whole shape in one step, including the fields nobody asserted above.
    And the response should match the schema 'schemas/review.json'

  @negative
  Scenario: An empty name is rejected and nothing is persisted
    When performs a POST call at '/api/reviews/new' with body '{"name":""}'
    Then the response status should be 400
    And the response json 'error' should contain 'name is required'
    # The half a negative test usually forgets: the store did not move.
    When performs a GET call at '/api/reviews'
    Then the response status should be 200
    And the response json 'total' should equal '0'
    And the response json 'items' should have 0 items

  @negative
  Scenario: A rating outside 1-5 is rejected at the boundary
    When performs a POST call at '/api/reviews/new' with body '{"name":"Out Of Range","rating":9}'
    Then the response status should be 400
    And the response json 'error' should contain 'rating must be an integer'

  @docstring
  Scenario: A multi-line payload reads as a docstring
    When performs a POST call at '/api/reviews/new' with this body:
      """
      {
        "name": "Docstring Dana",
        "rating": 3,
        "comment": "A payload that does not fit on one line."
      }
      """
    Then the response status should be 201
    And the response json 'name' should equal 'Docstring Dana'
    And the response should match the schema 'schemas/review.json'

  @payload
  Scenario: A payload file supplies the body
    When uses this payload 'payloads/new_review.json'
    And performs a POST call at '/api/reviews/new' with body '{var:PAYLOAD}'
    Then the response status should be 201
    And the response json 'name' should equal 'Payload Pete'
    And the response json 'rating' should equal '4'

  @crud @auth
  Scenario: Full lifecycle — create, read, delete with a bearer token
    # CREATE, then chain the generated id through every later step.
    When performs a POST call at '/api/reviews/new' with body '{"name":"Lifecycle Lou","rating":4}'
    Then the response status should be 201
    When extracts 'id' from the response storing in {var:REVIEW_ID}
    Then {var:REVIEW_ID} should not equal ''

    # READ — the record is retrievable by its own id.
    When performs a GET call at '/api/reviews/{var:REVIEW_ID}'
    Then the response status should be 200
    And the response json 'name' should equal 'Lifecycle Lou'

    # DELETE is protected: without a token it must refuse.
    When performs a DELETE call at '/api/reviews/{var:REVIEW_ID}'
    Then the response status should be 401

    # Log in, extract the JWT, and retry as an authenticated caller.
    # Credentials NEVER appear in a feature file, not even the test app's:
    # these resolve from busterblock_secrets.env at run time (copy the
    # .example beside it — see docs/feature-packages.md).
    When performs a POST call at '/api/auth/login' with body '{"username":"{env:BB_USER}","password":"{env:BB_PASS}"}'
    Then the response status should be 200
    When extracts 'token' from the response storing in {var:BB_TOKEN}
    Given sets the bearer token to '{var:BB_TOKEN}'
    When performs a DELETE call at '/api/reviews/{var:REVIEW_ID}'
    Then the response status should be 200

    # CONFIRM DELETED — the id is gone.
    When performs a GET call at '/api/reviews/{var:REVIEW_ID}'
    Then the response status should be 404

  @readiness
  Scenario: Polling waits for a record to become readable
    When performs a POST call at '/api/reviews/new' with body '{"name":"Polling Pat"}'
    Then the response status should be 201
    When extracts 'id' from the response storing in {var:REVIEW_ID}
    # On an eventually-consistent service the read after a write is a race.
    # This is a ceiling, not a wait: it returns on the first 200.
    Then waits until a GET call at '/api/reviews/{var:REVIEW_ID}' returns status 200 and the body contains 'Polling Pat' within 15 seconds
