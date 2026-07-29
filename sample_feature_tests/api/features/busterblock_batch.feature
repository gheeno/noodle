@api @rest @busterblock_batch @capability
Feature: Seeding many records — one step per batch, not one step per call

  # NOOD_0201 — the file this whole ticket exists to make possible.
  #
  # What a generated seeding test used to look like: 21 pasted
  # `performs a POST call ...` lines, then ONE trailing status assertion — which
  # only ever checked the LAST call. Rows 1-20 could all have failed silently,
  # and no pro test developer would sign their name to it.
  #
  # Three ways to say "do it N times", and when to reach for each:
  #   @repeat    same call, generated data      → repeated N times   ({i} = call number)
  #   @table     same call, heterogeneous data  → for each row       (headings name {placeholders})
  #   @outline   DIFFERENT assertions per row   → Scenario Outline + Examples
  # In all three, `expecting status` asserts EVERY call and names the row that
  # failed. Batches cap at 1000 calls — volume belongs to the @perf wok.
  #
  # Needs BusterBlock running:  cd test-apps/busterblock && npm start
  # Run:  noodle run sample_feature_tests/api/features/busterblock_batch.feature --headless

  Background:
    Given sets {var:REST_BASE_URL} to '{env:BUSTERBLOCK_API}'
    And sets a request header 'Content-Type' to 'application/json'
    And waits until '/api/health' returns status 200 within 20 seconds
    When performs a POST call at '/api/test/reset'
    Then the response status should be 200

  @smoke @repeat
  Scenario: Seed 20 reviews with one step, then verify the page count
    # ONE step. Twenty calls. Every one of them asserted 201.
    When performs a POST call at '/api/reviews/new' with body '{"name":"Row {i}","rating":5}' repeated 20 times expecting status 201
    # Typed assertions on the paginated envelope: 20 is the number, and the
    # default page really holds 10 of them.
    When performs a GET call at '/api/reviews'
    Then the response status should be 200
    And the response json 'total' should equal '20'
    And the response json 'items' should have 10 items
    And the response json 'totalPages' should equal '2'
    And the response should match the schema 'schemas/review_page.json'

  @table
  Scenario: Seed named reviewers from a data table
    # Headings name the {placeholder} tokens substituted into the body per row —
    # the shape to reach for when the rows are not interchangeable.
    When performs a POST call at '/api/reviews/new' with body '{"name":"{name}","rating":{rating}}' for each row expecting status 201:
      | name          | rating |
      | Ada Lovelace  | 5      |
      | Grace Hopper  | 5      |
      | Alan Turing   | 4      |
      | Katherine J   | 3      |
    When performs a GET call at '/api/reviews?page=1&size=10'
    Then the response status should be 200
    And the response json 'total' should equal '4'
    And the response json 'items' should have 4 items
    # Newest-first ordering: the last row seeded is the first one listed.
    And the response json 'items[0].name' should equal 'Katherine J'
    And the response body should contain 'Ada Lovelace'

  @table @negative
  Scenario: A batch of invalid payloads is rejected row by row
    # `expecting status 400` holds the whole batch to the SAME contract — one
    # row slipping through to a 201 fails the step and names the row.
    When performs a POST call at '/api/reviews/new' with body '{"name":"{name}","rating":{rating}}' for each row expecting status 400:
      | name    | rating |
      |         | 5      |
      | Zero    | 0      |
      | Six     | 6      |
    When performs a GET call at '/api/reviews'
    Then the response json 'total' should equal '0'

  @outline
  Scenario Outline: Pagination boundaries — <label>
    # Per-row ASSERTIONS differ, so this is Scenario Outline territory, not a
    # batch step: each row becomes its own scenario in the report.
    Given performs a POST call at '/api/reviews/new' with body '{"name":"Page Probe {i}"}' repeated 12 times expecting status 201
    When performs a GET call at '/api/reviews?page=<page>&size=<size>'
    Then the response status should be 200
    And the response json 'items' should have <expected> items
    And the response json 'page' should equal '<page>'

    Examples:
      | label            | page | size | expected |
      | first full page  | 1    | 5    | 5        |
      | last short page  | 3    | 5    | 2        |
      | beyond the end   | 4    | 5    | 0        |
