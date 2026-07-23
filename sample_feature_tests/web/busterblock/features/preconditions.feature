@web @precondition @capability
Feature: Preconditions — seed and tear down app state via YAML declarations

  # Instead of clicking the UI into the state you need, seed it directly via the
  # app's test API. Noodle reads preconditions.yaml (in this app's resources/
  # folder, sibling of this features/ folder) and fires HTTP calls
  # before/after the scenario — like a JDBC @Before/@After.
  #
  # Tag a scenario with @precondition:NAME to activate a named block from
  # preconditions.yaml. The block's setup: calls run before the scenario starts;
  # teardown: calls run after — even if the scenario fails.
  #
  # See resources/preconditions.yaml for the full definitions.
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/preconditions.feature --headless

  Background:
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    And User waits until "VHS Catalog" is visible

  @smoke @precondition:jaws_out_of_stock
  Scenario: A movie seeded out of stock shows "Out" in the catalog
    # The @precondition:jaws_out_of_stock tag caused setup: to run before this
    # scenario. Jaws (id 1) was forced to stock 0 via the API — no UI clicks.
    Then the cell in row "Jaws" column "Stock" should be "Out"
    # teardown: resets the data after this scenario completes.

  @smoke @precondition:cart_preseeded
  Scenario: A pre-seeded cart shows its item without clicking Add to Cart
    # Precondition placed Star Wars in reel_ryan's cart server-side.
    # The UI just needs to navigate to the cart — no clicking required.
    When User clicks "View cart"
    And User waits until "Your Cart" appears
    Then User should see "Your Cart"
    And User should see "Star Wars"
