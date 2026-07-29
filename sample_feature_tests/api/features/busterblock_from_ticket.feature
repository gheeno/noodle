@api @rest @from_ticket @capability
Feature: Authored from a ticket payload alone — BB-42

  # NOOD_0201 — the "ambiguous payload, little to no context" case, and the
  # exact failure that opened this ticket.
  #
  # INPUT: one raw JIRA issue payload and nothing else —
  #   sample_feature_tests/api/resources/tickets/busterblock_reviews_story.json
  # It carries Atlassian Document Format trees, SPEC_LINK/sha256 boilerplate, a
  # parent epic, five acceptance criteria as loose Given/When/Then prose — and
  # NO base URL. Its criteria say POST /api/review. The service serves
  # POST /api/reviews/new. Authoring the ticket's own wording gives a 404 that
  # looks like a product bug.
  #
  # HOW THESE SCENARIOS GOT HERE (no LLM anywhere in the chain):
  #   1. `noodle ticket <payload.json>`        — ADF walked, boilerplate stripped,
  #                                              ACs split into Given/When/Then
  #   2. localhost swept, /openapi.json read   — base URL + the REAL endpoints
  #   3. endpoint matcher                      — /api/review → /api/reviews/new,
  #                                              recorded as an assumption
  #   4. one goal per API-testable criterion → author_test → ready: true
  #
  # WHAT IT REFUSED TO INVENT, and said so instead:
  #   AC-1 ("follows Node/Express standards")  → not automatable: names no
  #        endpoint; that is a code-review criterion
  #   AC-4 ("exactly one record is stored")    → not automatable: asserts
  #        database state, which needs a DB step family Noodle does not have
  #   the request body for AC-2                → asked as a question; the
  #        <value> placeholder below was filled by the author, not guessed
  #
  # Reproduce:  noodle ticket sample_feature_tests/api/resources/tickets/busterblock_reviews_story.json
  # Needs BusterBlock running:  cd test-apps/busterblock && npm start
  # Run:  noodle run sample_feature_tests/api/features/busterblock_from_ticket.feature --headless

  Background:
    Given sets {var:REST_BASE_URL} to '{env:BUSTERBLOCK_API}'
    And waits until '/api/health' returns status 200 within 20 seconds
    When performs a POST call at '/api/test/reset'
    Then the response status should be 200

  # AC-2 — "the API validates the input, persists the review, and returns a JSON
  # response containing id, name, date, and response". The four field names the
  # criterion lists became the four assertions; the body is the author's.
  @smoke @ac2
  Scenario: BB-42 AC-2 — a valid submission returns the stored record
    When performs a POST call at '/api/reviews/new' with body '{"name":"Ticket Tina","rating":5}'
    Then the response status should be 201
    And the response body should contain 'id'
    And the response body should contain 'name'
    And the response body should contain 'date'
    And the response body should contain 'response'

  # AC-3 — "rejects the request with a client error response and does not
  # persist a review record". "Client error" with no code named compiled to 400,
  # which the plan reported as an assumption rather than a fact.
  @ac3 @negative
  Scenario: BB-42 AC-3 — malformed input is rejected
    When performs a POST call at '/api/reviews/new' with body '{}'
    Then the response status should be 400

  # AC-5 — "returns a paginated JSON list of reviews ordered by date descending".
  # The engine authored the call and the status; the pagination and ordering
  # assertions below are the follow-up its own `questions` asked for, once the
  # page/size defaults were known. This is the honest division of labour:
  # deterministic authoring gets it running, a human states what "correct" means.
  @ac5
  Scenario: BB-42 AC-5 — the list is paginated, newest first
    When performs a POST call at '/api/reviews/new' with body '{"name":"Older Olive"}'
    Then the response status should be 201
    When performs a POST call at '/api/reviews/new' with body '{"name":"Newer Nadia"}'
    Then the response status should be 201
    When performs a GET call at '/api/reviews?page=1&size=1'
    Then the response status should be 200
    And the response json 'total' should equal '2'
    And the response json 'totalPages' should equal '2'
    And the response json 'items' should have 1 items
    And the response json 'items[0].name' should equal 'Newer Nadia'
