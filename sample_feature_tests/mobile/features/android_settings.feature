# Android platform-tag smoke (NOOD_0032) — same target as smoke.feature but
# using @android + NOODLE_ANDROID_APP instead of hand-written caps JSON.
#
# Prerequisites:
#   pip install noodle[mobile]
#   npm i -g appium && appium driver install uiautomator2
#   an Android emulator/device (adb devices shows it), appium server running
#   export NOODLE_ANDROID_APP=com.android.settings/.Settings
#
# Run:  noodle run sample_feature_tests/mobile/ --tag android
@android
Feature: Android smoke — Settings via platform tag

  Scenario: Settings opens, scrolls and navigates back
    Then User should see "Settings"
    When User swipes up
    And User swipes down
    And User presses the back button
