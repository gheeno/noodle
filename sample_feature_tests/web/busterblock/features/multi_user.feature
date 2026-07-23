@web @multiuser @capability
Feature: Multi-user flows — two isolated browser sessions (Phase J)

  # A single scenario driving two simultaneous browser sessions ("buyer" and
  # "main"). Each named context is a separate Playwright BrowserContext:
  # separate cookies, separate localStorage — logging in as the buyer must not
  # leak into the primary session. Contexts close automatically at scenario end.
  #
  # Vocabulary:
  #   Given a new browser context as "buyer"   — create + name a second session
  #   When acting as "buyer"                   — steps now drive that session
  #   When acting as "main"                    — back to the primary session

  Scenario: Buyer's login does not leak into the main session
    Given User is on "{env:BUSTERBLOCK}"
    And a new browser context as "buyer"
    When acting as "buyer"
    And User is on "{env:BUSTERBLOCK}"
    And User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    Then User should see "VHS Catalog"
    When acting as "main"
    Then User should not see "VHS Catalog"
