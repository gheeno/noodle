@web @click @capability
Feature: Click — every click pattern Noodle supports

  # Patterns demonstrated:
  #   clicks 'X' / clicks the X button / clicks the X link
  #   double-clicks X / right-clicks X
  #   clicks in a table row: clicks 'X' in the row containing 'Y'
  #   clicks in a section: clicks 'X' in the 'Y' section
  #   clicks at X, Y  (coordinate/OCR click, no DOM lookup)
  #   clicks on the screen text 'X'  (OCR text click)
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/click.feature --headless

  Background:
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"

  @smoke @click_text
  Scenario: Click by visible text (quoted)
    # Finds any element whose accessible name or text content matches exactly.
    When User clicks "Add to Cart"
    Then User should see "1"

  @smoke @click_button
  Scenario: Click by button role ("the X button" / "presses the X button")
    # Targets role=button with the given accessible name. Same resolution,
    # both phrasings work — "clicks the X button" and "presses the X button".
    When User clicks the "Log out" button
    Then User should see "BusterBlock"

  @click_link
  Scenario: Click by link role ("the X link")
    # Targets role=link with the given accessible name.
    When User clicks the "View cart" link
    Then User should see "Your Cart"

  @double_click
  Scenario: Double-click on an element
    # double-clicks X → Playwright fires mousedown/mouseup/click x2.
    # BusterBlock does not expose a double-click target, so this scenario
    # is intentionally skipped if no element matches — it documents the step shape.
    # Replace 'some element' with a real target in your own app.
    When User double-clicks on "VHS Catalog"

  @click_in_row
  Scenario: Click a cell action inside a specific table row
    # "clicks 'X' in the row containing 'Y'" scopes the click to the row
    # whose text includes Y — avoids ambiguity when the same button appears on every row.
    When User clicks "Add to Cart" in the row containing "Die Hard"
    Then User should see "1"

  @xy_click @ocr
  Scenario: Coordinate click — no DOM, pure X/Y pixel position
    # "clicks at X, Y" fires a pointer event at page coordinates (X, Y).
    # No element lookup. Useful for canvas apps or pixel-mapped UIs.
    # Coordinates below target the catalog area; adjust if the layout changes.
    When User clicks at 640, 400
    # No assertion — this demonstrates the step shape. The point may land on
    # a movie row; combine with OCR or DOM assertions for a real test.
