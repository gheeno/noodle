@web
Feature: Hovers
  Covers: CSS :hover-revealed captions — three identical avatars disambiguated
  via POM nth-of-type overrides.

  Background:
    Given User is on "{env:THE_INTERNET}/hovers"

  @smoke
  Scenario: Hovering the first avatar reveals its caption
    When User hovers over "user 1 avatar"
    Then User should see "name: user1"
    And User should see "View profile"

  Scenario: Hovering the third avatar reveals its caption
    When User hovers over "user 3 avatar"
    Then User should see "name: user3"
