# Windows 11 native-app smoke (NOOD_0032) — the built-in Calculator, a
# known-stable target present on every Windows 11 machine.
#
# Prerequisites (run ON a Windows 11 box — see docs/native-apps.md):
#   pip install noodle[mobile]
#   npm i -g appium && appium driver install --source=npm appium-windows-driver
#   Settings > Privacy & security > For developers > Developer Mode: ON
#   appium        (server on http://localhost:4723)
#   set NOODLE_WINDOWS_APP=Microsoft.WindowsCalculator_8wekyb3d8bbwe!App
#
# Run:  noodle run sample_feature_tests/desktop/ --tag windows
@windows
Feature: Windows 11 native smoke — Calculator

  Scenario: Add two numbers
    When User clicks the One button
    And User clicks the Plus button
    And User clicks the Two button
    And User clicks the Equals button
    Then User should see "3"
