# Native & Mobile App Testing — @android / @ios / @windows / @mac
<!-- Branch: NOOD_0032 -->

> **For:** testers targeting native/mobile apps.

One agent, one protocol, four platforms. Noodle drives native apps through
**Appium 2** — the same W3C WebDriver protocol everywhere, with an official
driver per platform. No SikuliX, no image-coordinate scripting: elements are
located by accessibility metadata, exactly like the web agent locates by
role/label.

| Platform          | Appium driver           | Underneath                    |
|-------------------|-------------------------|-------------------------------|
| Android (emulator/device) | UiAutomator2     | Google's UiAutomator          |
| iOS (simulator/device)    | XCUITest         | Apple's XCUITest              |
| **Windows 11 native apps**| appium-windows-driver | Microsoft's WinAppDriver |
| macOS native apps         | appium-mac2-driver    | XCUITest for macOS       |

## How it works

Tag a scenario with a platform. Noodle starts an Appium session with default
capabilities for that platform; you name the app in one env var. All the
normal step vocabulary (click, fill, should see, wait…) plus the gestures in
`docs/steps_dictionary.md` ("Mobile & Native Apps") just work — steps route
to the Appium driver instead of Playwright.

```gherkin
@windows
Feature: Calculator
  Scenario: Add two numbers
    When User clicks the One button
    And User clicks the Plus button
    And User clicks the Two button
    And User clicks the Equals button
    Then User should see "3"
```

Element lookup tries, in order: accessibility id (= `AutomationId` on
Windows) → resource-id → content-desc → visible text → iOS `label`/`name` →
Windows `Name`/`AutomationId` → macOS `title` → `pom.yaml` → OCR (opt-in, see
below). Strategies for other platforms simply don't match, so one chain
serves all four.

Fine-grained control when you need it: `NOODLE_APPIUM_CAPS` (JSON string or
path to a .json file) — explicit entries override the tag defaults.
`NOODLE_APPIUM_URL` points at a non-default Appium server (default
`http://localhost:4723`).

## When nothing has an accessible name — OCR fallback

Some controls genuinely expose nothing to UI Automation/accessibility APIs:
unlabeled legacy Win32/MFC controls (an old C++ app that never set
`AutomationId`), canvas-drawn UI, games rendering their own widgets. No
selector reaches those — accessibility id, xpath, none of it — on any
platform.

Add `@ocr_fallback` to the scenario (or set `NOODLE_OCR_FALLBACK=true`) —
the same tag the web agent already uses for closed shadow roots. When every
accessibility strategy misses, the locator screenshots the native screen,
runs Tesseract OCR to find the text, and taps that coordinate directly
instead of an element. No new vocabulary — `clicks`, `fills`, `should see`,
`long-presses` all keep working once OCR finds the label.

```gherkin
@windows @ocr_fallback
Scenario: A legacy dialog with unlabeled buttons
  When User clicks the OK button
  Then User should see "Saved"
```

Requires the `[visual]` extra (Tesseract via `pytesseract` + the tesseract
binary itself):

```bash
pip install noodle[visual]
# macOS: brew install tesseract
# Windows: choco install tesseract, or the UB-Mannheim installer
# Linux: apt install tesseract-ocr
```

It's a fallback, not a first resort — every accessibility strategy is tried
first and is far cheaper (no screenshot + OCR decode per lookup). Reach for
`@ocr_fallback` only on the specific scenarios/apps that need it.

## Probe the app first — `noodle probe-app` (NOOD_0136)

The web probe's contract for native apps: before authoring any native step,
snapshot the accessibility tree once and author from what it actually
exposes.

```bash
noodle probe-app windows            # android | ios | windows | mac
noodle probe-app android --json     # raw payload for an agent
```

One Appium session (same env contract as tagged runs — `NOODLE_<PLATFORM>_APP`,
`NOODLE_APPIUM_CAPS`, `NOODLE_APPIUM_URL`), one `page_source` dump, session
closed. Every interactive node comes back normalized: kind
(button/field/toggle/dropdown/link), accessible name, the lookup strategy the
runtime chain will actually use (`accessibility_id` → `id` → text XPath),
visibility/enabled state, and a vocabulary-shaped suggested step (`taps
"login"`, `enters "<value>" in the "user name" field`). Nodes with **no
accessible name** are flagged `needs_pom` with a paste-ready mobile POM entry;
Flutter apps' native Semantics surface through UiAutomator2/XCUITest like any
other accessibility tree — no Flutter-specific driver or vocabulary.

Snapshot-only by design: the probe taps **nothing**. To see a deeper screen,
run an explicit scenario that navigates there, then probe again. A tree that
exposes fewer than three named controls returns `coverage: visual_only` — the
honest verdict that the app has no usable semantics — and points at the
`@ocr_fallback` path above instead of fabricating selectors. MCP callers get
the same via the `probe_app(platform)` tool.

## Common setup (all platforms)

```bash
pip install noodle[mobile]      # Appium Python client
npm install -g appium           # Appium 2 server
appium                          # start the server
```

## Windows 11 native apps (.exe, Store apps)

Run this ON the Windows 11 machine (the framework's unit tests run anywhere;
driving a real app needs the app's OS):

```powershell
# once
npm install -g appium
appium driver install --source=npm appium-windows-driver
# Settings > Privacy & security > For developers > Developer Mode: ON
# (the windows driver installs/uses WinAppDriver, which requires it)

# each session
appium

# what to test — one of:
$env:NOODLE_WINDOWS_APP = "C:\Program Files\MyApp\myapp.exe"                    # classic .exe
$env:NOODLE_WINDOWS_APP = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"       # Store app (AUMID)
$env:NOODLE_WINDOWS_APP = "Root"                                                # whole desktop (attach to anything)

noodle run sample_feature_tests/desktop/ --tag windows
```

Find a Store app's AUMID with `Get-StartApps | Select-String Calculator`.
Inspect an app's `AutomationId`/`Name` attributes with **Accessibility
Insights for Windows** (or the Windows SDK's `inspect.exe`).

Smoke example: `sample_feature_tests/desktop/features/windows_calculator.feature`.

## Android (emulator or device)

```bash
appium driver install uiautomator2
# an emulator (Android Studio > Device Manager) or a USB device with
# debugging on — `adb devices` must list it
export NOODLE_ANDROID_APP=app.apk                          # or:
export NOODLE_ANDROID_APP=com.android.settings/.Settings   # package/Activity
noodle run sample_feature_tests/mobile/ --tag android
```

## iOS (simulator; needs a macOS host with Xcode)

```bash
appium driver install xcuitest
xcrun simctl boot "iPhone 15"          # or start one from Xcode
export NOODLE_IOS_APP=com.apple.Preferences   # bundle id, or a .app/.ipa path
noodle run sample_feature_tests/mobile/ --tag ios
```

## macOS native apps

```bash
appium driver install mac2
# System Settings > Privacy & Security > Accessibility: allow the terminal/Appium
export NOODLE_MAC_APP=com.apple.calculator    # bundle id, or a path to the .app
noodle run sample_feature_tests/mobile/ --tag mac
```

## CI

Platform-tagged features are automatically excluded from web CI sharding
(`scripts/list_features.py` — they need a device/OS per shard, not a
stateless agent). To run them in CI, add a dedicated job on an agent that
has the platform: a `windows-latest` pool with the app installed for
`@windows`, a macOS pool with simulators for `@ios`/`@mac`, an
emulator-enabled Linux/macOS agent for `@android`. The job is just the
setup block above plus `noodle run <dir> --tag <platform>`.

## Unit tests without any of this installed

`unit_tests/test_nood_0032.py` exercises the capability builder, tag
detection, locator chain, gesture patterns and runner routing with fake
drivers — no Appium client, server, device or Windows box required. That's
why the framework can be developed on macOS and executed on Windows 11
unchanged.
