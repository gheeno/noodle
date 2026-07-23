# iOS platform-tag smoke (NOOD_0032) — the built-in Settings app on a
# simulator. Needs a macOS host with Xcode.
#
# Prerequisites:
#   pip install noodle[mobile]
#   npm i -g appium && appium driver install xcuitest
#   a booted simulator (xcrun simctl list), appium server running
#   export NOODLE_IOS_APP=com.apple.Preferences
#
# Run:  noodle run sample_feature_tests/mobile/ --tag ios
@ios
Feature: iOS smoke — Settings via platform tag

  Scenario: Settings opens and scrolls
    Then User should see "Settings"
    When User swipes up
    And User swipes down
