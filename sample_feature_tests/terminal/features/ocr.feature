# Canvas terminal — the case Selenium/Healenium and plain DOM steps can't touch.
# Everything is painted to <canvas>, so there is no DOM text to locate. These
# steps drive it with the NOOD_0024 bridge: coordinate clicks, raw keyboard
# typing, and deterministic OCR over the rendered pixels.
#
# Needs the OCR engine:  pip install -e ".[visual]"  + the tesseract binary
# (macOS: brew install tesseract).  Runs headless and in --parallel like any
# other web feature.
@web @terminal
Feature: Canvas terminal (no DOM) — OCR + coordinate bridge

  Background:
    Given User is on "sample_feature_tests/terminal/resources/terminal_app.html"

  Scenario: type a command and read the rendered output
    When User clicks at 400, 200
    And User types "login admin"
    And User presses Enter
    Then the screen shows "ACCESS GRANTED"
    And the screen should not show "unknown command"

  Scenario: an unknown command is echoed back
    When User clicks at 400, 200
    And User types "deploy please"
    And User presses Enter
    Then the screen shows "unknown command"
