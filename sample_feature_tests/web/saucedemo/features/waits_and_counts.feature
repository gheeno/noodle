
Feature: Native waits & visible counts (NOOD_0018)

  # Exercises two NOOD_0018 changes end-to-end against the live Sauce Demo:
  #   - 0018-4: "waits until ... is visible" now uses Playwright's native wait
  #     (MutationObserver, no polling race) for dynamic/post-login content.
  #   - 0018-6: "should see N ..." counts only VISIBLE occurrences, so the count
  #     reflects what a user actually sees and tracks DOM changes live.

  @web @smoke
  Scenario: Wait for the inventory, then count the Add to cart buttons

    Given User is on "{env:SAUCEDEMO}"
    When User enters {env:SAUCE_USERNAME} in the username field
    And User enters {env:SAUCE_PASSWORD} in the password field
    And User clicks the login button
    Then User waits until "Products" is visible
    And User should see 6 "Add to cart" items

  @web
  Scenario: Adding an item drops the visible Add to cart count

    Given User is on "{env:SAUCEDEMO}"
    When User enters {env:SAUCE_USERNAME} in the username field
    And User enters {env:SAUCE_PASSWORD} in the password field
    And User clicks the login button
    Then User waits until "Products" is visible
    When User clicks the "Add to cart" button
    Then User should see 5 "Add to cart" items
    And User should see 1 "Remove" items
