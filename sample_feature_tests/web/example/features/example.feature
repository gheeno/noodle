@web
Feature: Example Product Search

  # Guest user, three real pages: home -> search results -> product detail.
  # The search box / submit / first-result have no usable accessible label,
  # so they resolve via the per-page files in pageobjects/ (home_pom.yaml,
  # results_pom.yaml), scoped by URL.
  # Single-word locator keys (searchbox, searchbutton, firstresult) are used
  # on purpose: a multi-word label triggers the partial-text self-heal, which
  # would grab the wrong button before the POM is consulted.

  @web @smoke
  Scenario: Guest searches for a product and opens the first result
    Given User is on "https://www.example.com"
    And User waits 5 seconds
    When User enters "office chair" in the searchbox field
    And User clicks the searchbutton
    And User waits until "Office Chair" is visible
    And User clicks the firstresult
    And User waits until "Office Chair" is visible
    Then User should see "Office Chair"
    And User should have url containing "pdp"

  @web
  Scenario: Search results page shows relevant products
    Given User is on "https://www.example.com"
    And User waits 5 seconds
    When User enters "office chair" in the searchbox field
    And User clicks the searchbutton
    And User waits until "Office Chair" is visible
    Then User should have url containing "search-results"
    And User should see "Office Chair"

  # @page:<name> demo — this scenario deep-links straight to a
  # results URL, so results_pom.yaml's {var:firstresult} key must resolve without
  # ever having navigated through the home page. Its `match: { url_contains:
  # "search-results" }` block would already cover this URL — the tag makes
  # that independent of the exact query string / match pattern, so a future
  # redirect or URL restructure on Example's side can't silently break
  # {var:firstresult} the way it broke {var:searchbox}/{var:searchbutton} would if home's
  # URL ever stopped containing "en.html".
  @web @page:results
  Scenario: Deep-linked results page resolves via the pinned page, not the URL
    Given User is on "https://www.example.com/en/search-results.html?q=office%20chair"
    And User waits 5 seconds
    Then User should see "Office Chair"
    When User clicks the firstresult
    And User waits until "Office Chair" is visible
    Then User should have url containing "pdp"
