# Real public canvas terminals — for trying the X/Y coordinate + OCR bridge
# against something live. NETWORK-DEPENDENT and not run in CI (the @live tag is
# excluded by the web sharding discovery). Real sites drift: if a demo moves,
# adjust the click coordinates and expected text.
#
# Good targets to point this at (all canvas/black-and-green, keyboard-driven):
#   - https://xtermjs.org        live xterm.js demo (local echo) — used below
#   - https://hackertyper.net    iconic green-on-black, echoes as you type
#   - https://copy.sh/v86/       a full OS booted in a <canvas>
#
# Opt-in only — skipped unless NOODLE_RUN_LIVE=1 (so CI/casual runs never make
# surprise network calls). Run it:
#   NOODLE_RUN_LIVE=1 noodle run sample_feature_tests/terminal/features/live_xterm.feature --headed
@web @terminal @live
Feature: Real canvas terminal — xterm.js live demo

  Scenario: focus the live terminal by coordinate, type, and read it back
    Given User is on "https://xtermjs.org"
    When User clicks at 600, 400
    And User types "echo hello-noodle"
    Then the screen shows "hello-noodle"
