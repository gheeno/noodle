# ============================================================================
# FALLBACK DEMO — shows the two resolution paths side by side.
#
#   ACCESSIBILITY PATH  → element found by role/label/placeholder/text.
#                         No pom.yaml needed. Zero config. (steps marked [A11Y])
#
#   POM FALLBACK PATH   → element has NO accessible name (icon-only button).
#                         Accessibility returns 0 matches, so the framework
#                         falls back to this app's resources/pageobjects/
#                         pom.yaml. (step marked [POM])
#
# Run it:   noodle run sample_feature_tests/web/fallback-demo/features/fallback_demo.feature --no-capture
#
# Watch for this line in the output, which proves the fallback fired:
#   📋 POM: resolved 'burger menu' via pom.yaml
#
# NOTE: this runs against the CURRENT framework. Phase 9 (page-scoped POM +
# ambiguity detection) builds ON TOP of this same fallback mechanism.
# ============================================================================
@web @fallback_demo
Feature: POM Fallback Demonstration

  Scenario: Resolve elements via accessibility, then fall back to POM

    # [A11Y] navigate — no element lookup at all
    Given User is on "https://www.saucedemo.com"

    # [A11Y] "username" → matched by input placeholder "Username"
    When User enters {env:SAUCE_USERNAME} in the username field

    # [A11Y] "password" → matched by input placeholder "Password"
    And User enters {env:SAUCE_PASSWORD} in the password field

    # [A11Y] "login" → matched by button accessible name "Login"
    And User clicks the login button

    # [A11Y] plain DOM text assertion — never touches POM
    Then User should see "Products"

    # [POM] "burger menu" → accessibility finds 0 matches (the button's only
    #       text is the hidden "Open Menu"), so the framework falls back to
    #       pom.yaml → id: react-burger-menu-btn
    When User clicks the burger menu

    # [A11Y] the menu slid open — assert the POM click actually worked
    Then User should see "Logout"
