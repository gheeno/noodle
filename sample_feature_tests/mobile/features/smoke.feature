# Appium smoke (Phase F) — targets the built-in Android Settings app, a
# known-stable target that needs no app-under-test.
#
# Prerequisites (NOT run in web CI — the @appium tag excludes it from sharding):
#   pip install noodle[mobile]
#   an Appium server (npx appium) and a connected emulator/device
#   NOODLE_APPIUM_CAPS='{"platformName": "Android",
#                        "appium:automationName": "UiAutomator2",
#                        "appium:appPackage": "com.android.settings",
#                        "appium:appActivity": ".Settings"}'
#   NOODLE_APPIUM_URL defaults to http://localhost:4723 ({env:APPIUM_SERVER})
#
# Run:  noodle run sample_feature_tests/mobile/ --tag appium
@appium
Feature: Mobile smoke — Android Settings

  Scenario: Settings opens and scrolls
    Then User should see "Settings"
    When User swipes up
    And User swipes down
    And User presses the back button
