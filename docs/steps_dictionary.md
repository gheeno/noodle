# Noodle Test Framework Steps Dictionary
<!-- Branch: NOOD_0062 -->

> **For:** testers and AI coding agents — the full built-in step reference.

Steps are written in plain English and prefixed with `Given`, `When`, `Then`, or `And`.
The subject (`User`, `I`, `The user`) is stripped before matching, so all three forms are equivalent:

```gherkin
When User clicks the login button
When I click the login button
When The user clicks the login button
```

Variable substitution happens before matching. Every reference is
`{source:name}` — one delimiter, the prefix names the source:

| Syntax | Source | Example |
|---|---|---|
| `{env:NAME}` | config: OS env → `.env` → `secrets.env` → `environments.yaml` | `When User enters {env:BB_USER} in the username field` |
| `{var:NAME}` | value captured during the run (stored from a previous step); also the write target in capture steps | `…saves the result as {var:SUM}` … `Then {var:SUM} should equal "5"` |
| `{pom:name}` | pin one element straight to its `pom.yaml` entry (skips accessibility/self-heal/vision) | `When User clicks the {pom:burger menu}` |

Literals need no wrapper: quote strings (`"hello world"`), write numbers and
URLs bare, use Gherkin's own `<name>` in Scenario Outlines. JSON bodies are
never touched — only `{env:…}`/`{var:…}` refs *inside* them are expanded.
The legacy delimiters `[NAME]`, `` `NAME` `` and bare `{name}` still resolve
but log a deprecation warning.

### Wording drift (NOOD_0009)

A small set of common synonyms is canonicalized before matching, so you don't
have to phrase things exactly like the examples below:

```gherkin
Then PRICE is equal to '9.99'      # → canonicalized to "should equal"
Then PRICE equals '9.99'           # → canonicalized to "should equal"
Then PRICE is not equal to '0.00'  # → canonicalized to "should not equal"
```

See `_PHRASE_ALIASES` in `noodle/resolver/patterns.py` to add more — each
entry must be a phrase that can't plausibly belong to another action's
wording (e.g. `should match` is never aliased, since pixel/visual baseline
steps already use it).

### Grammar tolerance (NOOD_0062)

The engine normalizes sloppy authoring before matching, so a step written in
a hurry still resolves:

- **Smart quotes** (`‘ ’ “ ”` pasted from Word/Jira) become straight quotes.
- **Doubled whitespace** collapses; a **trailing `.` or `!`** is stripped
  (punctuation inside quotes is untouched).
- **Past tense and bare infinitives** are normalized: `the user clicked …`,
  `click the login button` and `User clicks the login button` all match the
  same pattern.
- **Verify wrappers** unwrap: `verifies that …`, `makes sure …`,
  `ensures that …`, `expects that …`, `checks that …` are stripped and the
  inner step is matched (`checks` needs an explicit `that/whether/if` so
  checkbox steps stay untouched).
- **Verb synonyms**: `chooses`/`picks` → `selects`, `inputs` → `enters`.

```gherkin
When the user clicked the login button
When click the login button
Then verify that 'Welcome' is visible
Then makes sure the 'spinner' is gone
When the user chooses 'Ontario' from the province dropdown
```

If a step still doesn't match anything, the failure includes the closest
documented example(s):

```
No pattern matched: "User clicsk the log in button"
  Did you mean:
    User clicks the login button
```

---

## Navigation

```gherkin
Given User navigates to '{env:BASE_URL}/path'
Given User is on '{env:BASE_URL}/path'
Given User opens '{env:BASE_URL}/path'
Given User goes to '{env:BASE_URL}/path'
Given User visits '{env:BASE_URL}/path'
Given User browses to '{env:BASE_URL}/path'
Given User opens the page '{env:BASE_URL}/path'
Given the login page with the url value of '{env:BASE_URL}/login'
Given User is on the checkout page whose url is '{env:BASE_URL}/checkout'
```

In the page-with-url forms the page name is descriptive only — the quoted
url is what gets navigated to. A scheme-less `www.…` url gets `https://`
prepended automatically (NOOD_0062).

### Viewport / screen size (NOOD_0007, NOOD_0035)

```gherkin
When User sets the viewport to "1920x1080"
Given browser resolution is set to 1920x1080
Given screen resolution is set to 1366x768
```

Both phrasings resolve to the same `set_viewport` action — the second is the
declarative form from the target-architecture sketch ("system resolution is
set to 1920x1080"; `browser`/`screen`/`system` are all accepted). `{env:X}`/
`{var:X}` also work (e.g. `browser resolution is set to {var:RESOLUTION}`) —
they're substituted before the sentence is matched, same as everywhere else.

Or per scenario with a tag (`@viewport:1366x768`), or run-wide with
`NOODLE_VIEWPORT=1920x1080`. An explicit viewport wins over `@mobile`
device presets.

---

## Clicking

```gherkin
When User clicks the 'Login' button
When User clicks the 'Sign in' link
When User clicks 'Add to cart'
When User clicks the submit button
When User double-clicks 'Item Name'
When User right-clicks 'Context Menu'
When User taps 'Continue'
```

Qualifier phrasing (NOOD_0062) — name the element by role word + attribute;
`on`/`upon` is tolerated in every click step:
```gherkin
When User clicks the button with a label 'stonemountain'
When User clicks the link whose text is 'Sign up'
When User clicks on the element containing 'Add to cart'
When User taps the button labelled 'Save'
When User clicks the menu item with the name 'Export'
When User clicks on the login button
```

Scoped to a row or section:
```gherkin
When User clicks 'Edit' in the row containing 'Order #123'
When User clicks 'Delete' in the 'Actions' section
```

By screen position (pixel/OCR bridge — canvas & terminal UIs, no DOM):
```gherkin
When User clicks at (120, 340)
When User clicks on the screen text 'Start'
```

---

## Image content (NOOD_0114)

Text or objects rendered **inside an image's pixels** — product carousels,
flyers, banners, logos, avatars, profile pictures, thumbnails, posters. The
image noun in the step routes it through the OCR/vision bridge scoped to that
element's rendered box, instead of the DOM text engine. Needs
`pip install 'noodle[visual]'` + the tesseract binary.

```gherkin
# focus later screen/OCR steps on one element's box
When User focuses on the "product carousel" image

# OCR inside one element (deterministic — no LLM)
When User clicks "Dog" in the "product carousel" image
Then the "sale flyer" image should show "50% off"
Then the "hero" banner should not show "Sold out"

# OCR extraction into variables
When User reads the text from the "sale flyer" image into [FLYER_TEXT]
When User reads the price from the "product card" image into [PRICE]
When User reads the screen text into [SCREEN_TEXT]
Then [PRICE] should equal "1299.99"
```

Object recognition (the *picture*, not its text) needs a vision LLM
(`NOODLE_MODEL`) and is **nondeterministic** — `write_feature` auto-inserts a
`# ⚠ requires a vision LLM (image recognition)` comment above the step and
tags the scenario `@potential-flake`; at run time the step fails with a
stderr warning when no model is configured:

```gherkin
@potential-flake
Scenario: hero shows the mascot
  # ⚠ requires a vision LLM (image recognition) — nondeterministic, may flake
  Then the "hero" image should depict "a golden retriever"
  Then the screen should show a picture of "a red sports car"
```

---

## Drag & Drop (NOOD_0009)

```gherkin
When User drags 'Card A' onto 'Done column'
When User drags 'file.png' to the 'upload area'
```

---

## Keyboard

```gherkin
When User presses 'Enter'
When User presses 'Tab'
When User presses 'Escape'
When User presses 'ArrowDown'
```

Supported keys: `Enter`, `Return`, `Tab`, `Escape`, `Space`, `Backspace`, `Delete`, `ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight`, `Home`, `End`, `PageUp`, `PageDown`

Chords (NOOD_0009) — combine `Control`/`Ctrl`, `Alt`/`Option`, `Shift`, `Meta`/`Cmd`/`Command` with any key:

```gherkin
When User presses 'Control+A'
When User presses 'Shift+Tab'
When User presses 'Ctrl+Shift+K'
```

Type into whatever has focus (no target element — terminal/canvas UIs):
```gherkin
When User types 'ls -la'
```

---

## Forms

```gherkin
When User enters 'john@example.com' in the email field
When User types 'hunter2' into the password field
When User fills in the username with 'admin'
When User clears the search field
When User selects 'Canada' from the country dropdown
When User selects 'Red', 'Green' and 'Blue' from the tags dropdown
When User selects the 'Express shipping' radio button
When User checks the 'Remember me' checkbox
When User unchecks the 'Subscribe' checkbox
When User submits the login form
```

More phrasings (NOOD_0062):
```gherkin
When User enters 'john' as the username
When User provides 'j@x.test' for the email field
When User types 'abc' into the field with the label 'Username'
When User inputs 'abc' in the search field
When User chooses 'Ontario' from the province dropdown
When User picks 'Red' from the colour dropdown
When User selects 'Express' option from the shipping dropdown
```

Scoped to a row or section (NOOD_0009):
```gherkin
When User enters '5' in the 'Qty' field in the row containing 'Widget'
When User enters 'Alice' in the 'Name' field in the 'Billing' section
```

### File upload

```gherkin
When User uploads 'fixtures/report.pdf' to the attachment field
Then a file should be downloaded
Then a file 'export.csv' should be downloaded
```

---

## Hovering & Scrolling

```gherkin
When User hovers over the profile menu
When User scrolls down
When User scrolls up
When User scrolls to 'Footer'
When User scrolls to the bottom of the page
When User scrolls to the top of the page
```

`scrolls down/up` nudge one half-viewport; the `bottom/top of the page` forms
(NOOD_0143) jump to the document edge — what lazy-load footers need.

---

## Browser History & Tabs

```gherkin
When User goes back
When User goes forward
When User reloads the page
When User refreshes the page
Then a new tab should open
When User switches to the new tab
When User switches to the original tab
When User closes the current tab
```

---

## Cookies & Storage (NOOD_0009)

Test isolation and auth-state seeding. Set cookies **after** navigating — they attach to the current page's URL.

```gherkin
When User clears all cookies
When User clears the local storage
When User clears the session storage
When User sets the cookie 'session' to '{env:AUTH_TOKEN}'
```

Value seeding and asserts (NOOD_0143) — set state before a scenario, assert
what the app persisted after. Exact match or substring, like attribute asserts:

```gherkin
When User sets the local storage 'feature_flag' to 'on'
When User sets the session storage 'draft' to 'saved'
Then the local storage 'cart_count' should be '3'
Then the session storage 'draft' should contain 'saved'
Then the cookie 'session' should exist
Then the cookie 'consent' should be 'accepted'
```

---

## Waits

```gherkin
When User waits for the page to load
When User waits for the page to fully load
When User waits for the network to be idle
When User waits until 'Welcome' is visible
When User waits until 'Spinner' disappears
When User waits 3 seconds
When User waits until the URL contains 'checkout'
When User waits for the URL to be 'https://app.example.com/done'
When User waits for the response from '/api/cart'
When User waits for the 'cart' request to complete
When User waits until the 'cart badge' updates
```

The response/request waits are the correct settle after a state-changing
click (add to cart, save, submit) and BEFORE navigating away — navigating
the instant the click returns aborts the in-flight request
(net::ERR_ABORTED) and the asserted state never lands. They wait for the
NEXT matching response, so they must follow the triggering step.

The URL waits (NOOD_0143) block until an async SPA navigation lands (up to
`NOODLE_TIMEOUT`) — the waiting twin of the instant `the URL should contain`
assertion.

Generic element waits (NOOD_0062) — a bare `waits for X` waits for it to be
visible; the `to disappear/be gone` forms wait it out:
```gherkin
When User waits for the loading icon
When User waits for the 'loading icon'
When User waits for the loading icon to disappear
When User waits for the 'spinner' to be gone
When User waits for the toast to appear
When User waits for the results to finish loading
```

Hard sleeps (NOOD_0044) — a real thread sleep, so prefer the condition-based
waits above; use these only when nothing observable changes to wait on.
`wait/sleep/pause`, optional `for`, and seconds/minutes/hours/ms all work:
```gherkin
When User waits for 3 seconds
When User sleeps 2 seconds
When User pauses for 1 minute
When User waits for 500 ms
When User waits for the page to load : 20 seconds
When User waits for the page to load for 20 seconds
```
The `: N seconds` form is also a hard sleep — the duration can come from a
variable, e.g. `waits for the page to load : {var:LOAD_WAIT} seconds`.

Per-step timeout (NOOD_0009) — overrides `NOODLE_TIMEOUT` for that one wait:
```gherkin
When User waits until 'Report ready' is visible for up to 30 seconds
When User waits until 'Spinner' disappears within 45 seconds
```

A wait you don't have to author explicitly (NOOD_0088/0089): ordinary steps
that locate an element (`clicks`, `enters ... in`, `selects`, `checks`,
etc.) already poll for up to `NOODLE_FIND_TIMEOUT` (default 2 minutes — a
ceiling, not a wait: the step proceeds the instant the element appears) if
the element isn't there yet — no separate `waits for X` step needed for
content that renders asynchronously (a spinner, an API-backed row). While
waiting, the engine re-scans the DOM for attribute matches (wrong-selector
self-heal; hidden dev-panel elements included) and, at the deadline, grants
one bounded extension (`NOODLE_WAIT_EXTENSION`) when network traffic shows
the page is genuinely still loading. This is on top of Playwright's own
actionability auto-wait, which still applies once the element is resolved
— Playwright can only auto-wait on a locator it already has; the poll is
for *finding* it. Navigation (`is on "..."`, back/forward/reload) and the
actions themselves respect `NOODLE_TIMEOUT` (default 10s) — raise that in
`.env` if your app's pages routinely load slowly, rather than adding a wait
step after every navigate.

---

## Conditional Steps (NOOD_0044)

Run any other step only when an element is (or is not) visible — for flows
that genuinely branch, like a promo popup that only sometimes shows. The
condition is probed without failing; when it doesn't hold, the inner step is
skipped and logged, never an error. Conditions accept `appears / is visible /
is present / is displayed / is shown / exists` and their negations
(`does not appear / is not visible / …`).

```gherkin
When User clicks 'Skip' if 'Tour popup' appears
When User clicks 'Retry' when 'Error banner' is visible
When User clicks 'Open menu' if 'Sidebar' is not visible
When User enters 'admin' in the username field if 'Login form' appears
Given if 'Cookie banner' appears, clicks 'Accept all'
Given if 'Welcome tour' does not appear, clicks 'Start tour'
When a 'Promo modal' appears on the page, performs clicks 'Close'
```

Popup-specific conditionals route to the best-effort popup closer (see
[Popups / Modals](#popups--modals)) — no condition probe needed, it never
fails:

```gherkin
Given if the page appears to have a pop-up, closes the pop-up
When User closes the popups if any appear
```

The inner step ("then" part) goes through the full resolver again, so any
step in this dictionary works there — including another conditional.

---

## Assertions — Visibility

```gherkin
Then User should see 'Welcome back'
Then User should not see 'Error'
Then User should see 3 'Shipped' items
Then User should be on the dashboard page
Then User should have url containing '/checkout'
Then the page title should contain 'My App'
```

Count comparisons (NOOD_0009) — exact counts make list tests brittle:
```gherkin
Then User should see at least 3 'Shipped' items
Then User should see at most 10 'Out of stock' results
Then User should see more than 0 rows
Then User should see fewer than 5 errors
```

> **What this counts (NOOD_0115):** elements whose **visible text contains the
> given word** — 3 'Shipped' items means three elements showing the word
> "Shipped". It is NOT a structural count: on an e-commerce grid no single
> word appears on all N product cards, so "at least 90 'product' items" will
> never equal a human's "90 results". For a structural count, give the card
> selector a POM entry — `products: {css: "li[class*='product']"}` — and
> count that:
> ```gherkin
> Then User should see at least 90 '{pom:products}' items
> ```
> Or compare against the page's own results-summary number (see the
> number-extraction step under Assertions — Values).

Scoped to a row or section (NOOD_0009):
```gherkin
Then User should see 'Shipped' in the row containing 'Order #123'
Then User should not see 'Error' in the 'Summary' section
```

Natural visibility phrasing (NOOD_0062) — all of these resolve to the same
visible/hidden assertions as `should see` / `should not see`:
```gherkin
Then the user can see 'Welcome back'
Then the user sees 'Welcome back'
Then the user cannot see 'Error'
Then the page contains the text 'Welcome back'
Then the page does not contain the text 'Error'
Then the element with the text value of '1234xyz' is seen
Then the element containing 'Error' is not seen
Then 'Welcome back' is visible
Then the 'Welcome' banner is visible on the page
Then the 'loading icon' is gone
Then the loading icon is no longer seen
Then the 'spinner' is dismissed
Then the spinner is not visible
```

Landing / redirect phrasing (NOOD_0062) — URL assertions:
```gherkin
Then the user lands on the checkout page
Then the user is redirected to '/orders'
Then the url contains '/checkout'
```

URL exact / ends-with (NOOD_0009):
```gherkin
Then User should have url ending with '/checkout'
Then the url should be 'https://example.com/done'
```

---

## Assertions — Element State

```gherkin
Then the 'Submit' button should be disabled
Then the 'Email' field should contain 'user@example.com'
Then the 'Email' field should have value 'user@example.com'
Then the 'username' should have attribute 'placeholder' equal to 'Enter name'
```

Negated, scoped to one element (NOOD_0021) — unlike page-wide `should not see`,
this checks a *specific* field/cell never shows a value. Useful for pinning
down exactly where a leftover `undefined`/`null`/`NaN` from an unguarded JS
assignment shows up, instead of just asserting it's absent from the whole page:

```gherkin
Then the 'runtime' field should not contain 'undefined'
```

Supported states: `enabled`, `disabled`, `checked`, `unchecked`, `selected`, `editable`, `read-only`

State synonyms (NOOD_0062) — `is` works without the modal verb, and
`clickable`/`greyed out` map to enabled/disabled:
```gherkin
Then the save button is disabled
Then the 'Save' button should be clickable
Then the submit button is greyed out
```

Focus and computed style (NOOD_0143) — `focused` pairs with `presses Tab` for
keyboard/tab-order tests; the CSS assert checks a computed property (exact or
substring) without a pixel baseline:

```gherkin
Then the 'Email' field should be focused
Then the 'error banner' should have css 'color' of 'rgb(220, 38, 38)'
```

---

## Assertions — Table / Grid

Works on HTML tables **and** ARIA grids (Dynamics 365, AG Grid, … — anything
exposing `role=grid`/`gridcell`). Rows are identified by text they contain,
columns by their header name.

```gherkin
Then the table should have 5 rows
Then the cell in row 'Alice' column 'Role' should be 'Admin'
Then the cell under 'Director' in the row containing 'Jaws' should be 'Steven Spielberg'
Then the row containing 'Jaws' should have values '1975', 'Thriller' and 'Steven Spielberg'
Then the table should have columns 'Title', 'Year' and 'Genre'
Then the 'Genre' column should contain 'Thriller'
Then the 'Year' column should be sorted ascending
Then the 'Price' column should be sorted descending
When User clicks the cell under 'Actions' in the row containing 'Jaws'
```

`sorted` (NOOD_0143) compares numerically when every cell parses as a number
(`$1,234.56` tolerated), else as case-insensitive text; bare `sorted` means
ascending.

Table-driven forms (end the step with `:` and indent a Gherkin table):

```gherkin
Then the row containing 'Jaws' should have values:
  | column   | value            |
  | Year     | 1975             |
  | Director | Steven Spielberg |
Then the table should have columns:
  | column |
  | Title  |
  | Year   |
Then the 'Genre' column should contain:
  | value    |
  | Thriller |
Then the grid should contain rows:
  | Title | Year | Director         |
  | Jaws  | 1975 | Steven Spielberg |
```

For `the grid should contain rows:` the Gherkin headings are the grid's column
names and each row's **first cell identifies the row**.

Scroll a grid's own scrollbars (`bottom`/`top` jump to the edge;
`right`/`left`/`down`/`up` move about a page — virtualised grids render rows
as you scroll):

```gherkin
When User scrolls the table to the bottom
When User scrolls the grid right
When User scrolls the 'Movie catalog table' table to the top
```

---

## Table-Driven Form Fill

One step fills many fields — headings are labels, cells are `field | value`:

```gherkin
When User fills in the form with:
  | field    | value     |
  | username | reel_ryan |
  | password | Popcorn1! |
```

---

## Scenario Outlines & Data Tables (NOOD_0062)

### Scenario Outlines

Behave expands each `Examples` row into its own scenario run **before** the
step reaches the engine, so `<placeholders>` work in any step — quoted or
unquoted positions, several per step, reused across steps:

```
Scenario Outline: Login as <username>
  When User enters '<username>' as the username
  And User provides '<password>' for the password field
  And User clicks the button with a label 'Login'
  Then the cell under '<column>' in the row containing '<movie>' should be '<expected>'
  And User waits <n> seconds

  Examples: Valid users
    | username  | password  | column | movie | expected | n |
    | reel_ryan | Popcorn1! | Year   | Jaws  | 1975     | 1 |

  Examples: Invalid credentials
    | username | password | column | movie | expected | n |
    | bad_user | bad_pass | Year   | Jaws  | 1975     | 1 |
```

Rules the engine relies on:

- Every `Examples` column name must match a `<placeholder>` exactly
  (case-sensitive, no quotes in the header row).
- Quote the placeholder (`'<username>'`) when the step shape expects a quoted
  literal; leave it bare for numbers (`waits <n> seconds`).
- `{env:X}` / `{var:X}` refs work inside Examples cells — substituted at run
  time, after outline expansion.
- Multiple `Examples:` blocks all run; label them to group happy/sad paths.
- `noodle validate --resolve` substitutes the **first** Examples row into
  outline steps before dry-running, so a step that only matches with real
  data (`waits <n> seconds`) validates correctly.

### Data tables

A step ending in `:` with an indented `| … |` table receives the table as
data. Gherkin always treats the **first row as headings**; Noodle handles
both styles:

- **Labelled** — headings are generic labels (`field`, `value`, `column`,
  `key`, `header`, `payload`, …): they're skipped, every other row is data.
- **Headerless** — headings that *aren't* generic labels are treated as the
  first data row, so a table written without a label row doesn't silently
  lose its first entry:

```gherkin
When User fills in the form with:
  | username | reel_ryan |
  | password | Popcorn1! |
```

Header-keyed tables (`| Key | Value |` for REST body asserts, `| Header |
Value |` for header asserts, `| payload |` for resource loads) match their
headings **case-insensitively**, and fall back to positional cells when the
heading row is missing.

Exception: `the grid should contain rows:` — its headings ARE meaningful
(they name the grid's columns) and are never treated as data:

```gherkin
Then the grid should contain rows:
  | Title    | Year | Genre    |
  | Jaws     | 1975 | Thriller |
```

Cell values go through the same `{env:X}` / `{var:X}` substitution as step
text. Working examples: `sample_feature_tests/web/busterblock/features/scenario_outline.feature`
and `tables_and_grids.feature`.

---

## Browser Session Persistence

Save cookies + localStorage after a login; start every later scenario (or CI
run) already authenticated by pointing `NOODLE_STORAGE_STATE` at the file.
The standard answer to SSO/MFA login walls (Microsoft 365, Google, …).

```gherkin
When User saves the browser session as 'artifacts/reports/session.json'
```

```bash
NOODLE_STORAGE_STATE=artifacts/reports/session.json noodle run noodle_tests/web/myapp --headless
```

---

## Assertions — Value Comparison

Used to compare stored variables against expected values.

```gherkin
Then {var:PRICE} should equal '9.99'
Then {var:PRICE} should not equal '0.00'
Then {var:COUNT} should be greater than '0'
Then {var:COUNT} should be less than '100'
Then {var:COUNT} should be at least '1'
Then {var:RESPONSE} should contain 'success'
```

Number extraction (NOOD_0115) — read the first number out of an element's
text and compare it, in one step. The results-summary / review-count /
cart-badge / pagination-total pattern: a `<span>93 results</span>` verifies
"at least 90" without a store → custom-function → compare chain. The locator
is POM-aware (same resolution as `stores ... in {var:}`); thousands
separators are handled ('1,234 items' → 1234).

```gherkin
Then the number in 'results summary' should be at least 90
Then the number in 'cart badge' should be at most 10
Then the number in '{pom:review_count}' should be exactly 12
Then the number in 'stock level' should be more than 0
Then the number in 'errors shown' should be less than 5
```

> **Counting results? Summary count beats rendered count (NOOD_0117).** When
> a page shows its own "NN results" summary, assert that number (`the number
> in 'results summary' should be at least N`) instead of counting rendered
> cards (`should see at least N 'product' items`): grids lazy-render, so the
> rendered count varies between headless and headed (52 vs 92 observed for
> the same query). `noodle probe <url> --search "term"` emits the summary
> element's POM entry and this assertion ready-made — with a stable `>= 1`
> floor to raise to your intent (`more than 1 item` → `at least 2`), never
> today's live count baked in (NOOD_0125), so the test can't rot when
> inventory shifts.

---

## Variables

Seed a literal value:
```gherkin
Given sets {var:BASE_PRICE} to '29.99'
```

Capture element text:
```gherkin
When User stores the total price in {var:TOTAL}
When User grabs the order number in {var:ORDER_ID}
```

Capture an element attribute:
```gherkin
When User stores attribute 'href' of the download link in {var:LINK_URL}
```

---

## Screenshots & Visual

```gherkin
When User takes a screenshot
When User takes a screenshot 'checkout-complete'
Then the screen should match the pixel baseline
Then the 'header' screen should match the pixel baseline
Then the screen should look the same as before
Then the screen should look the same as before ignoring the banner
```

### Evidence screenshots (NOOD_0153)

Proof a green step really did what it claims. By default
(`NOODLE_EVIDENCE=last`) the engine captures ONE evidence screenshot per
passing web scenario — at its final step — as a **viewport-only JPEG**
(what the tester needs to see, not the full-page scroll), with a **green
box** drawn around the element that step resolved. So
`Then the "toy" is seen in the cart` ships a picture of the cart with the
toy outlined. Evidence lands on the step in the Allure report and in the
Evidence section of `rca.md` / `rca.html`.

Ask for evidence of any specific step with a trailing marker — it is
stripped before the step resolves, so every step form accepts it:

```gherkin
When User clicks the 'Login' button ( take a screenshot )
Then User should see 'Welcome back' ( take a screenshot )
```

Gates — the engine never takes random screenshots:

| Control | Effect |
|---|---|
| `NOODLE_EVIDENCE=last` | default — final step of each passing web scenario |
| `NOODLE_EVIDENCE=all` | every passed step (heavier — debugging/demo runs) |
| `NOODLE_EVIDENCE=off` | none (markers and `@evidence` still respected: off kills only the automatic shot) |
| `( take a screenshot )` marker | always capture that step |
| `@evidence` tag | every passed step in that scenario |
| `@no_evidence` tag | no evidence for that scenario, overriding everything |

Web area only: `@api`, `@appium`/platform and `@visual` scenarios have no
Playwright page, so the gate skips them. Failure screenshots are unchanged
(full-page PNG with expected/matched markers). The explicit
`takes a screenshot` step above is also attached to the reports now, not
just written to disk. Related: in **headed** runs the engine scrolls each
matched element into view so the visible viewport follows what Playwright
is doing (`NOODLE_FOLLOW=true|false` overrides; headless runs skip it).

---

## Search

```gherkin
When User searches for 'blue running shoes'
```

One step does the whole job: the engine resolves the search box
editable-first (`searchbox` POM key → `search` label → searchbox role), so a
search *button* whose accessible name also says "search" can't steal the
fill. If the best match still isn't an input — retail sites often show only
a search icon until it's clicked — the engine clicks it open, finds the
revealed box, fills it, and presses Enter (NOOD_0106). Prefer this over a
hand-rolled click/enter/press chain.

### Search suggestions / typeahead (NOOD_0141)

```gherkin
When User selects the 'running shoes' suggestion for 'runni'
When User selects the 'running shoes' suggestion
When User clicks the 'running shoes' suggestion
Then the search suggestions for 'runni' include 'running'
Then the search suggestions include 'shoes'
Then a suggestion bar appears below the search bar
```

Quoted values are slot-filled like every other step — any term, any site's
typeahead (Google, retail, docs search). `selects the "..." suggestion for
"..."` is the whole typeahead flow in one deterministic step: resolve the
visible search box (opening its trigger when hidden), type the partial term
**per-character** (typeaheads listening on keydown never see a single fill),
wait for the suggestion list to populate, and click the row whose text
matches — the *navigating* row element, never a decorative icon inside it.
The typed term is kept exactly as written — a deliberately partial or
misspelled term is the test, don't "correct" it. The bare form (no `for`)
picks from a list an earlier step already opened. The assertions are
intent-level and DOM-free — "a partial term still yields suggestions" with
no selector; the `for '...'` form types the term itself first. Discover the
exact suggestion strings up front with `noodle probe <url> --suggest "runni"`
(MCP: `probe_page(suggest=...)`).

---

## Popups / Modals

```gherkin
When User closes all popups
When User closes the modal
When User closes the banner
When User closes the popup if it appears within 10 seconds
When User closes any and all popups including the geolocation prompt
```

`close all popups` sweeps **DOM overlays only** (cookie banners, modals, promo
overlays) — it never touches browser permission state. To also decide a browser
permission bubble in the same step, name it: `...including the geolocation
prompt` runs the DOM sweep **and** denies that one permission for the current
origin (NOOD_0122). Named permissions: `location`/`geolocation`,
`notifications`, `camera`, `microphone`.

The `within N seconds` form (NOOD_0106) keeps sweeping for up to N seconds
and returns as soon as a popup is closed — for overlays that arrive a few
seconds after load. Don't pad tests with `waits for N seconds` to catch a
late popup.

---

## JS Dialogs (alert / confirm / prompt)

Arm the handler **before** the step that triggers the dialog — Playwright auto-dismisses unhandled dialogs.

```gherkin
When User accepts the next confirm
When User dismisses the next alert
When User types 'Alice' into the next prompt and accepts it
Then the alert should say 'Are you sure?'
```

---

## iFrames

```gherkin
When User switches to the 'payment-frame' iframe
When User switches back to the main frame
```

---

## REST API Testing

Tag a scenario `@api` to run it **without a browser** — REST steps talk
straight to the HTTP client, no Playwright launch, no browser binaries needed
in CI. Web steps inside an `@api` scenario fail with a clear error.

### Setup

```gherkin
Given sets {var:REST_BASE_URL} to '{env:API_BASE_URL}'
Given sets request header 'Authorization' to 'Bearer token123'
Given sets request header 'X-Api-Key' to '{env:API_KEY}'
```

### Authentication (NOOD_0007)

Credentials belong in `{env:VARS}` (secrets.env) — never literal in the feature.
`Authorization` headers are never written to logs or reports. A value later
pulled out of a response via `extract`/`sets`/`stores` steps is also redacted
in logs if its variable name looks sensitive (`token`, `auth`, `secret`,
`password`, `key`, `jwt`, `bearer`).

```gherkin
Given sets the bearer token to '{env:API_TOKEN}'
Given uses basic auth with '{env:API_USER}' and '{env:API_PASS}'
Given sets the api key header 'X-Api-Key' to '{env:API_KEY}'
Given fetches an oauth2 token from '{env:AUTH_URL}' with client '{env:CLIENT_ID}' and secret '{env:CLIENT_SECRET}'
```

The OAuth2 step performs a client-credentials grant and sets the bearer token.
If a later call returns 401, the token is refreshed once and the call retried
once — never a loop.

### Requests

Paths starting with `/` are appended to `REST_BASE_URL`. Absolute `https://` paths are used as-is.

```gherkin
When performs a GET call at '/objects'
When performs a GET call at '/objects/1'
When performs a GET call at '/objects' storing response in {var:LIST_RESP}

When performs a POST call at '/objects' with body '{"name": "My Item"}'
When performs a POST call at '/objects' with body '{"name": "My Item"}' storing response in {var:CREATED}

When performs a PUT call at '/objects/{var:OBJ_ID}' with body '{"name": "Updated"}'
When performs a PATCH call at '/objects/{var:OBJ_ID}' with body '{"name": "Patched"}'
When performs a DELETE call at '/objects/{var:OBJ_ID}'
When performs a DELETE call at '/objects/{var:OBJ_ID}' storing response in {var:DEL_RESP}
```

### Response Assertions

```gherkin
Then the response status should be 200
Then the response status code should be 404

Then the response body should contain 'id'
Then the response body should contain 'error'

Then the response body should contain:
  | Key       | Value      |
  | id        |            |
  | name      | My Item    |
  | createdAt |            |
```

Empty `Value` = key-exists check only. Non-empty `Value` = key and value both checked.

```gherkin
Then the response header 'Content-Type' should contain 'application/json'
Then the response header 'X-Auth' should be 'token123'

Then the response headers should contain:
  | Header       | Value            |
  | Content-Type | application/json |
```

### Extracting values from the response

```gherkin
Then extracts 'id' from response storing in {var:OBJ_ID}
Then extracts json key 'token' from response body storing in {var:AUTH_TOKEN}
Then extracts 'data.items[0].id' from the response and stores it as {var:FIRST_ID}
```

Keys may be a dotted path with `[n]` indexes for nested JSON; a plain key
keeps the original behaviour (first item when the response is a list).

After extraction, the value is available as a runtime variable for later steps:
```gherkin
When performs a DELETE call at '/objects/{var:OBJ_ID}'
```

### Full pattern example

```gherkin
Given sets {var:REST_BASE_URL} to '{env:API_BASE_URL}'
When performs a POST call at '/users' with body '{"name": "Alice"}' storing response in {var:USER_RESP}
Then the response status should be 200
And extracts 'id' from response storing in {var:USER_ID}
When performs a GET call at '/users/{var:USER_ID}'
Then the response status should be 200
And the response body should contain 'Alice'
When performs a DELETE call at '/users/{var:USER_ID}'
Then the response status should be 200
```

---

## API Setup / Teardown (Playwright-backed)

These use Playwright's request context (shares browser cookies) and only assert 2xx — no body access.

```gherkin
When User calls GET '{env:API_URL}/reset'
When User calls POST '{env:API_URL}/seed' with body '{"id": 1}'
When User calls DELETE '{env:API_URL}/items/1'
```

They also send the REST auth headers, so a token set with the REST auth steps
guards these calls too (Microsoft Graph / Dynamics OData-style setup):

```gherkin
Given sets the bearer token to '{var:TOKEN}'
When User calls POST '{env:API_URL}/cart' with body '{"movieId": 1}'
```

---

## Network Mocking

```gherkin
When User mocks '/api/products' with status 200 and body '[{"id":1}]'
When User mocks '/api/auth' with status 401
When User blocks requests to '**/analytics/**'
```

---

## Test Data

Load a YAML or JSON fixture file into the variable store:
```gherkin
Given User loads test data from 'fixtures/user.yaml'
```

Each top-level key becomes a `{env:KEY}` variable (uppercased, spaces → underscores).

---

## Scripts & Shell Commands

```gherkin
When User runs the script 'scripts/seed_db.py'
When User runs the script 'scripts/seed_db.py' with args '--env staging'
When User runs the script 'scripts/seed_db.py' storing output in {var:SEED_OUTPUT}

When User runs the command 'curl -s https://example.com/status'
When User runs the command 'npm run build' storing output in {var:BUILD_LOG}
```

stdout is always stored in `SCRIPT_OUTPUT`. Named `storing output in` stores it additionally under that name.

> **Windows note:** `.sh` scripts run via `bash`, which isn't on a vanilla
> Windows PATH (Git Bash or WSL provides it). For cross-platform suites prefer
> `.py` scripts — they run with the same Python interpreter as Noodle itself.

### Custom Python functions (NOOD_0009)

Scripts only give you stdout. To call a Python **function** in-process and use its
real return value — the Noodle equivalent of a Java/Cucumber step class method
(JDBC setup, token minting, data prep):

```gherkin
When User calls the function 'sample_feature_tests/web/busterblock/resources/functions/helpers.py:add' with args '2 3'
When User calls the function 'myproject.helpers:make_token' and saves the result as {var:TOKEN}
When User calls the function 'os.path:basename' with args 'a/b.pdf' and saves the result as {var:NAME}
```

- Spec is `path/to/file.py:function` or `importable.module:function`.
- Args are strings, split shell-style and passed positionally. **Watch out:**
  a captured value containing spaces (`'93 results'`) splits into TWO args.
  To pass the whole value as one argument, say `with raw arg` (NOOD_0115):
  ```gherkin
  When User calls the function 'helpers.py:parse_int' with raw arg '{var:RESULTS_TEXT}'
  ```
- The return value is always stored in `` `FUNCTION_RESULT` ``; `saves the result as`
  also stores it under the named variable. dict/list returns are JSON-encoded.
- A raised exception fails the step.

### Dependency injection between steps

Any stored variable feeds later steps — one step generates, the next consumes:

```gherkin
Given User calls the function 'scripts/helpers.py:make_username' and saves the result as {var:USERNAME}
When User calls the function 'scripts/helpers.py:greet' with args '{var:USERNAME}'
Then {var:FUNCTION_RESULT} should contain 'Hello'
```

`` `USERNAME` `` is substituted before matching, so the second function receives the
value the first one returned. The same works with `store text`, `SCRIPT_OUTPUT`,
REST-extracted values, etc. See `sample_feature_tests/web/busterblock/features/custom_functions.feature`.

---

## Assertions — Console & Network Health (Phase M)

Listeners record JS console errors, uncaught exceptions and failed requests
passively for every scenario — nothing asserts unless a step asks, so existing
scenarios are unaffected.

```gherkin
Then no console errors should be logged
Then no uncaught JS errors should occur
Then no network requests should fail
Then a request to '**/api/cart*' should have been made
```

The last step matches observed request URLs by substring, or as a glob when
the text contains `*`/`?`.

---

## Soft Assertions (Phase L)

Tag a scenario `@soft` and assertion failures are collected instead of
stopping the scenario; the scenario fails at the end listing all of them.
Add the explicit step to fail at a chosen point instead:

```gherkin
Then all soft assertions should pass
```

---

## Browser Context Emulation (Phase N)

Geolocation, permissions, locale, timezone and color scheme — as scenario tags
(`@geo:51.5,-0.12 @permissions:geolocation @locale:fr-FR
@timezone:America/New_York @color_scheme:dark`) or run-wide env vars
(`NOODLE_GEOLOCATION`, `NOODLE_PERMISSIONS`, `NOODLE_LOCALE`,
`NOODLE_TIMEZONE`, `NOODLE_COLOR_SCHEME`). Locale/timezone/color-scheme apply
at context creation only (Playwright has no runtime setter); geolocation and
permissions also have runtime steps:

```gherkin
When User grants permission 'geolocation'
When User sets geolocation to '51.5,-0.12'
```

Sites like example.com pop the browser's own permission bubble
("wants to know your location") at the top-left of a headed Chromium window.
That bubble is browser chrome, not DOM, so `close popups` can't reach it —
close it by denying the permission for the current origin:

```gherkin
And the user closes the location prompt
```

Also accepts `dismisses`, `pop-up`/`notification`/`bubble`/`request`, and the
other promptable permissions: `geolocation`, `notifications`, `camera`,
`microphone` (e.g. `When User dismisses the notifications prompt`). On
Chromium the visible prompt is resolved via CDP; firefox/webkit never render
a native permission prompt under Playwright (undecided requests are
auto-denied), so there the step is a logged no-op and still passes. If the
test *needs* the permission, grant it instead (step above or
`@permissions:geolocation` tag).

To **allow** rather than deny, `accept`/`allow` the prompt — it grants the
named permission for the current origin (NOOD_0122):

```gherkin
And the user accepts the location prompt
And the user allows the notifications prompt
```

Permission prompts take **no text** — they're allow/deny only. `types 'Toronto'
into the location prompt` is rejected at resolution with a message pointing you
to a JavaScript prompt (`types 'X' into the prompt`) or a page field instead, so
it can't silently become a DOM fill hunting for a nonexistent element.

### Which step for which system surface

Browser "popups" are several different things; pick the right family so you
don't ask a permission bubble to hold text or a JS dialog to be closed as DOM.

| Surface | Examples | Step family |
|---|---|---|
| Page DOM | cookie banner, modal, promo overlay | `closes all popups`, click, fill |
| JavaScript dialog | alert, confirm, prompt, beforeunload | `accepts/dismisses the alert`, `types 'X' into the prompt` (arm before trigger) |
| Permission prompt | location, notifications, camera, microphone | `accepts/dismisses the <perm> prompt` — allow/deny only, no text |
| New tab/window | popup window | switch/close via browser-context steps |
| File chooser | upload picker | `uploads 'file' to ...` |
| Download | download shelf/prompt | download-assert steps |
| HTTP auth | browser credential challenge | `@http_credentials` tag (NOOD_0143) + `NOODLE_HTTP_USER`/`NOODLE_HTTP_PASSWORD` in the secrets file — applied to the browser context **before** navigation |
| Chrome product UI | sign-in/sync, save-password, translate, autofill | **launch-profile config, not a page step** — bundled automation Chromium normally suppresses these; add a disable flag only if a run reproduces one |

Chrome sign-in/password/translate/autofill UI is browser configuration, not
something these popup steps handle — don't write a step expecting to close it.

---

## Offline & Network Throttling (Phase O)

`@offline` (or `NOODLE_OFFLINE=true`) starts the context offline. Runtime:

```gherkin
When User goes offline
When User goes back online
When User throttles the network to 'slow-3g'
```

Throttling presets: `slow-3g`, `fast-3g`, `4g` (standard Lighthouse values).
Chromium-only — it rides a CDP session; firefox/webkit fail with a clear error.

---

## Accessibility Auditing (Phase P)

Runs the vendored axe-core (no network fetch, no new dependency) inside the
page and fails listing rule ids, impact and element counts:

```gherkin
Then the page should have no accessibility violations
Then the page should have no critical accessibility violations
Then User should see at most 5 accessibility violations
```

The impact form (`minor` / `moderate` / `serious` / `critical`) counts
violations at or above that level.

---

## Clipboard (Phase Q)

Chromium-only (clipboard permissions aren't exposed to automation on
firefox/webkit). Permissions are granted automatically by the steps.

```gherkin
When User copies 'https://example.com/share/42' to the clipboard
Then the clipboard should contain 'share/42'
```

---

## WebSockets (Phase R)

Every frame on every socket is recorded passively ({url, direction, payload});
the asserts wait up to `NOODLE_TIMEOUT` since socket traffic is async:

```gherkin
Then a websocket message containing 'order_filled' should be received
Then a websocket message containing 'subscribe' should be sent
Then a websocket message containing 'heartbeat' should be observed
```

---

## Print Media & PDF (Phase S)

```gherkin
When User emulates print media
When User saves the page as pdf 'artifacts/reports/out.pdf'
```

Print-layout *visual* verification composes with the existing pixel baseline —
no new comparison engine:

```gherkin
When User emulates print media
Then the 'invoice-print' screen should match the pixel baseline
```

`save as pdf` is Chromium-only.

---

## Multi-User Browser Contexts (Phase J)

Two (or more) simultaneous, fully isolated sessions in one scenario — separate
cookies, separate storage. `'main'` always names the primary session. All named
contexts close automatically at scenario end.

```gherkin
Given a new browser context as 'buyer'
When acting as 'buyer'
When acting as 'main'
When User switches to the 'buyer' context
```

Demo: `sample_feature_tests/web/busterblock/features/multi_user.feature`.

---

## App Lifecycle (Phase G4)

Start a local process (a dev server, a desktop app) and health-check it; every
launched process is killed at scenario end even when the scenario failed.

```gherkin
Given User launches the app 'python -m http.server 8000'
Then the app should be running on port 8000
When User stops the app
```

---

## Visual / Desktop Steps — @visual (NOOD_0067)

For UIs with no accessible DOM (desktop apps, Electron, Citrix, legacy web).
Needs `pip install noodle[visual]` and the `tesseract` binary. Targets are found
by OpenCV template match (with DPI-scale variants) → Tesseract OCR → optional
vision LLM (only when `NOODLE_VISION_MODEL` or `NOODLE_MODEL` is set).

**`@visual` routes the whole scenario to the desktop agent.** These steps resolve
against a **separate pattern table** (`noodle/resolver/visual_patterns.py`), so
none of the web machinery applies: no POM lookups, no self-healing locators, no
accessibility strategies. You cannot mix web and visual steps in one scenario —
split them into a `@visual` scenario and a plain `@web` one. Subject stripping
(`User`/`I`/`The user`) and `{env:}`/`{var:}` substitution work exactly as they do
for web steps.

Image paths are relative to the run directory (e.g. an `assets/` folder).

```gherkin
@visual
Scenario: Upload via file picker
  When User clicks image "upload_button.png"
  Then User should see text "File picker" on screen
  And User types "{env:FILE_PATH}"
  And User presses key "enter"
```

| Step | Action |
|---|---|
| `clicks image "btn.png"` | Click the matched image |
| `clicks image "btn.png" with confidence 0.75` | Same, with an explicit match threshold (default `0.85`) |
| `right-clicks image "icon.png"` | Right-click |
| `double-clicks image "icon.png"` | Double-click |
| `scrolls to image "footer.png"` | Scroll until the image is on screen |
| `should see image "logo.png" on screen` | Assert present |
| `should not see image "err.png" on screen` | Assert absent |
| `waits until image "dialog.png" appears` | Poll until present (10s timeout) |
| `waits until image "loader.png" disappears` | Poll until absent |
| `clicks text "OK" on screen` | OCR the screen, click the text |
| `should see text "Saved" on screen` | OCR assert |
| `waits until text "Done" appears on screen` | OCR poll |
| `types "hello"` | Type into whatever has focus (quotes required) |
| `presses key "enter"` | Press a single key |
| `scrolls down 3 times` / `scrolls up 2 times` | Scroll wheel |
| `drags "a.png" to "b.png"` | Drag one matched image onto another |
| `focuses on screen region "top-left"` | Restrict later matching to a region |
| `focuses the window "Calculator"` | Bring an OS window to the front |

---

## Mobile & Native Apps — @appium / @android / @ios / @windows / @mac (Phase F, NOOD_0032)

Tag a scenario with a platform and steps drive the app through Appium
(`pip install noodle[mobile]` + an Appium server — full per-platform setup,
including Windows 11 native .exe apps, in `docs/native-apps.md`). `@appium`
needs explicit `NOODLE_APPIUM_CAPS`; a platform tag builds default
capabilities from one env var naming the app:

| Tag        | Env var              | Value examples                                            |
|------------|----------------------|-----------------------------------------------------------|
| `@android` | `NOODLE_ANDROID_APP` | `app.apk`, `com.foo.bar`, `com.foo.bar/.MainActivity`     |
| `@ios`     | `NOODLE_IOS_APP`     | `MyApp.app`, `com.apple.Preferences`                      |
| `@windows` | `NOODLE_WINDOWS_APP` | `C:\path\app.exe`, a Store AUMID, `Root` (whole desktop)  |
| `@mac`     | `NOODLE_MAC_APP`     | `com.apple.calculator`, `/Applications/Foo.app`           |

Clicks, fills and visibility asserts reuse the normal vocabulary; gestures:

```gherkin
When User swipes left
When User swipes up
When User presses the back button
When User presses the home button
When User long-presses the Archive button
When User presses and holds 'Chat item'
When User hides the keyboard
When User sends the app to the background for 5 seconds
When User takes a screenshot 'calculator'
```

The back/home device keys are mobile-only (Android keycodes, iOS
pressButton); on `@windows`/`@mac` they fail with a clear message. On web
(no platform tag), "presses the back/home button" clicks the element with
that name; gestures fail with a pointer to the tags.
Examples: `sample_feature_tests/mobile/features/` (Android/iOS),
`sample_feature_tests/desktop/features/windows_calculator.feature` (Windows 11).

**No accessible name at all?** Some controls — unlabeled legacy Win32/MFC
elements, canvas-drawn UI, games — expose nothing an accessibility strategy
can match, on any of the four platforms. Add `@ocr_fallback` (or
`NOODLE_OCR_FALLBACK=true`) — the same tag the web agent uses — and the
locator's last resort is Tesseract OCR over a native screenshot, tapping the
recognised text's coordinates directly. Needs `pip install noodle[visual]`
(and the tesseract binary). Every step above (click, fill, should see,
long-press…) works unchanged once OCR finds it — no separate vocabulary.

---

## Performance / Load Testing — @perf (NOOD_0155)

The performance wok ([woks.md](woks.md)): plain-Gherkin load tests via the
built-in stdlib generator — no browser, no extra install. `@perf` scenarios
are browserless like `@api`. These steps resolve against
`noodle/resolver/perf_patterns.py`, consulted only after the web table
misses, so they also compose into any other scenario. Assertions grade the
**most recent** load test in the scenario. Phrasing note: `should exceed`
works everywhere; the natural `should be at least` also works *inside a
`@perf` scenario* (tag-aware grammar — the perf table outranks the generic
compare assertion there, see [woks.md § Tag-aware step grammar](woks.md#tag-aware-step-grammar)).

```gherkin
@perf
Scenario: Home page latency gate
  When User runs a load test on "{env:APP}" with 10 users for 30 seconds
  Then the p95 response time should be under 800 ms
  And the average response time should be under 400 ms
  And the error rate should be under 1 %
  And the throughput should exceed 20 requests per second
  And User saves the load test report as "home baseline"
  And User stores the p95 response time into "HOME_P95"
```

```gherkin
@perf
Scenario: Fixed request budget
  When User runs a load test on "{env:APP}" with 100 requests using 10 users
  Then the p99 response time should be under 2000 ms
```

- `runs a load test on "<url>" with N users for S seconds` — duration mode.
- `runs a load test on "<url>" with N requests [using M users]` — budget mode (default 5 users).
- Latency assertions accept `p50`–`p99`, `average`/`mean`, `max`/`maximum`/`slowest`.
- `saves the load test report as "<name>"` renders a latency-over-time chart
  PNG into the screenshots dir — the wok's "screenshot", attached to
  Allure/RCA like any other.
- `stores the <metric> [response time] into "<VAR>"` exposes a metric to
  `{var:VAR}` for later steps (cross-wok).

---

## Desktop Spreadsheet Steps (NOOD_0155)

The desktop wok's browserless file helpers
(`noodle/resolver/desktop_patterns.py` → `agents/desktop/spreadsheet.py`):
read saved `.xlsx` values — including a formula's last-calculated result —
with zero extra dependencies, on any OS. Workbook paths resolve against the
app package's `resources/` folder (same rule as `load_data`). Because these
steps never touch the browser they compose into any scenario: read a cell,
then use `{var:...}` in web/mobile steps. Driving the Excel *application*
UI is the `@visual`/`@windows`/`@mac` side of the desktop wok. Phrasing
note: `expects ... to equal` works everywhere; the natural `should equal`
also works *inside a desktop-wok scenario* (`@windows`/`@mac` — tag-aware
grammar, see [woks.md § Tag-aware step grammar](woks.md#tag-aware-step-grammar)).

```gherkin
@web
Scenario: An Excel value drives a web test
  Given User reads cell "B2" from sheet "Catalog" of spreadsheet "inventory.xlsx" into "TITLE"
  And User is on "{env:APP}"
  When User searches for "{var:TITLE}"
  Then User should see "{var:TITLE}"
```

```gherkin
Scenario: Assert the workbook directly
  Then User expects cell "A1" of spreadsheet "inventory.xlsx" to equal "Movie"
  And User expects cell "B2" of sheet "Catalog" of spreadsheet "inventory.xlsx" to equal "Blade Runner"
```

- `reads cell "<A1>" from [sheet "<name>" of] spreadsheet "<file.xlsx>" into "<VAR>"`
  (`workbook` also accepted; omitting the sheet uses the first one).
- `expects cell "<A1>" of [sheet "<name>" of] spreadsheet "<file.xlsx>" to equal "<value>"`.
- Values come back as displayed text: `42.0` reads as `42`, booleans as
  `TRUE`/`FALSE`, empty/missing cells as `""`.

---

## Window, Complex Interaction & Wait Vocabulary (NOOD_0152)

Closes the gaps found by the 2026-07-21 coverage audit. Every step here is
deterministic — no LLM, no vision.

### Browser window & responsive layout

| Step | Notes |
|---|---|
| `resizes the browser to tablet width` | Named breakpoints: `mobile`/`phone`/`small` (390×844), `tablet`/`ipad`/`medium` (768×1024), `laptop` (1280×800), `desktop`/`large` (1440×900), `wide`/`full hd` (1920×1080) |
| `switches to desktop view` | Same table, no size noun |
| `resizes the browser window to 800x600` | Alias of `sets the viewport to "800x600"` |
| `rotates the device to landscape` / `to portrait` | Reads the **live** viewport and transposes it, so it composes with whatever size is already set. Idempotent |
| `rotates the device` | Always swaps width/height |
| `the viewport should be 1280 pixels wide` | Verifies a resize actually landed |
| `the viewport should be 800x600` | |

**Not supported, by design:** maximise, minimise, restore and window-move are
absent from the Playwright API entirely — they need Chromium-only CDP
(`Browser.setWindowBounds`) or a `--window-size` launch arg. Use viewport
sizing instead; it is what responsive tests actually need.

### Complex interactions (real mouse events)

`drags 'A' onto 'B'` uses `Locator.drag_to`, which only synthesises HTML5 drag
events. Everything below issues genuine press→move→release, which is what
split panes, sliders, canvases and most JS sortables listen for.

| Step | Notes |
|---|---|
| `drags 'Card' by 100, 50` | Pixel offset from the element's centre |
| `drags 'split pane' 100 pixels right` | Direction words: right/left/up/down |
| `resizes the 'sidebar' panel 120 pixels right` | Grabs the **border**, not the centre — the resize handle. Horizontal drags grab the right edge, vertical the bottom |
| `drags the 'Price' divider 80 pixels left` | Same |
| `drags 'Task A' to the 'Done' column` | Kanban drop via mouse events |
| `drags 'Item 1' above 'Item 3'` | Sortable-list reorder |
| `drags 'Card' onto 'Bin' using the mouse` | Forces the mouse path when `drag_to` is ignored |
| `drags the 'volume' slider to 75` | Native `<input type=range>` is set through the **native value setter** plus bubbling `input`/`change`, because assigning `.value` alone leaves React/Vue state stale. A custom slider is dragged proportionally using its `aria-valuemin`/`aria-valuemax` |
| `ctrl-clicks 'Row 2'` / `shift-clicks 'Row 5'` | Multi-select |
| `clicks 'Row 2' while holding Shift` | Same; `Ctrl+Shift` chords accepted |
| `right-clicks 'Row 1' and selects 'Delete'` | In-page menus only — a native OS context menu is invisible to the browser, and the step says so |

### Waits that replace `waits N seconds`

A hard sleep is both the slowest and the flakiest option. These poll and
re-raise the **last real failure**, so the message says why it never became
ready.

| Step | Notes |
|---|---|
| `waits until the 'Save' button is enabled` | Any state: enabled/disabled/checked/unchecked/selected/editable/readonly |
| `waits until 'Submit' is no longer disabled` | Negated form |
| `waits until there are 10 'rows'` | Count wait — the correct "results loaded" check |
| `waits until the 'total' changes` | Snapshots the text when the step starts |
| `waits until the 'total' changes from '10'` | Explicit prior value |
| `waits for the response from '/api/orders'` | **Waits for the NEXT matching response**, so it must follow the triggering step. If the call already finished, assert history instead: `a request to '/api/orders' should have been made` |
| `scrolls until 'Item 100' is visible` | Infinite scroll / lazy load — `scrolls to 'X'` only reaches elements already in the DOM and never drives the loader |
| `loads all results by scrolling` | Scrolls until the page stops growing |
| `scrolls the 'sidebar' panel to the bottom` | Scrolls **inside** a named container (bottom/top/up/down/left/right) |

### Assertions

| Step | Notes |
|---|---|
| `the 'Email' field should be empty` | The post-reset check. Previously only `should have value ''` |
| `the 'Email' field should not be empty` | |
| `the 'total' should match the pattern '^\$[0-9,]+\.[0-9]{2}$'` | Regex / format |
| `the 'total' should be formatted as currency` | Optional symbol, grouped or plain digits, optional 2 decimals |
| `the number in 'balance' should be 100.00 within 0.01` | Tolerance. The boundary is **inclusive** — binary floats make `abs(99.99-100.0)` slightly exceed `0.01`, so the comparison carries scaled slack |
| `the number in 'total' should be approximately 99.99` | Default tolerance 0.01 |
| `the number in 'amount' should be between 10 and 20` | Inclusive range |
| `the page should have at most 3 serious accessibility violations` | Impact + budget together — the shape a real a11y gate takes. Impacts: minor/moderate/serious/critical |
| `the page should make fewer than 50 requests` | Page-weight budget |
| `the downloaded file should contain 'Invoice #123'` | Reads the file off disk. Binary formats (xlsx/pdf) are refused with an explanation, not a confusing red |
| `the downloaded csv should have 10 rows` | Non-empty lines minus the header |

### Other

| Step | Notes |
|---|---|
| `enters today's date in the 'Start date' field` | Also `tomorrow`/`yesterday`, and `enters the date 3 days from now in 'X'` / `7 days ago`. `<input type=date>` gets ISO regardless of display locale; otherwise `NOODLE_DATE_FORMAT` (default `%Y-%m-%d`) |
| `switches to the 'inner' frame inside the 'outer' frame` | Nested iframes — a payment iframe inside a vendor iframe |
| `stores the clipboard as \`CLIP\`` | `read_clipboard` existed but was reachable only from inside `assert_clipboard` |
| `focuses on the 'order summary' panel` | Scopes OCR/screen reads to any layout container, not just images |
| `long-presses 'Row 1' for 2 seconds` | Explicit hold duration (Appium) |

### Steps that now refuse instead of guessing

Noodle has no mail/SMS adapter. `the email should contain 'X'` used to fall
through to `assert_compare` and string-compare the literal words *"the
email"* — a red nobody could diagnose. It now fails at resolution with the
workaround (`calls the function 'mailbox:latest' …`). Refusing honestly beats
pretending.

---

## Finding a step / suggesting a new one

Before writing anything by hand, ask whether Noodle already has a step for
what you want:

```bash
noodle step-search "store a return param and use it to another step"
noodle repl
noodle> find a step for clicking the checkout button
```

This searches *this file* — `example_index()` in
`noodle/resolver/step_resolver.py` parses every ```gherkin``` example above
into the same corpus that already powers `noodle steps <keyword>` and the
VS Code hover. Nothing else needs to be kept in sync.

Ranking is deterministic first (token-overlap + string similarity over the
dictionary, pure Python stdlib — no database, see "Do we need a database?"
below) and always runs. A local LLM (Ollama, via `NOODLE_MODEL`) is only
consulted as a tie-breaker when the deterministic ranking is genuinely
ambiguous, and only if one is configured — it's never required and never
the primary mechanism.

If nothing matches well, the engine drafts a new step — a phrasing, a regex,
and the closest existing action type — and asks:

```
Suggested new step:
  When frobnicates the sprocket widget
  action_type: click  (Nearest existing step: "..." (click).)
Add this step? [y/N]
```

Accepting (`y` at the prompt, or `noodle step-search "..." --accept` for
CI/scripting) stages it in `docs/agent_patterns.yaml` — **not** spliced into
`noodle/resolver/patterns.py` directly (see that file's own comment for why:
`PATTERNS` is hand-curated and order-sensitive, and automatically inserting
a tuple into the *right* spot in existing source is fragile). The staged
pattern is checked as a second tier, only after every curated pattern has
already failed, so it can never shadow one — and it resolves immediately,
same process, no restart. It also lands as an example in the
**Agent-Suggested Steps (staging)** section at the end of this file. A human
reviews staged entries periodically and promotes a good one into
`patterns.py` proper (see "How to add a new pattern" below) — treat the
staging file as a to-do list, not a silent parallel resolver.

The engine will only ever draft a new *phrasing* for a capability that
already exists at runtime. If nothing in the existing action types
(`step_resolver.VALID_TYPES`) fits, it says so plainly and writes nothing —
that case needs real new runtime logic (an action handler + a runner
dispatch), which is not something to auto-generate; see "How to add a new
pattern" below for the manual workflow.

**Do we need a database for this?** No. The whole corpus is this one file —
a few hundred lines, parsed once and cached in memory. Ranking is a linear
scan with `re`/`difflib`, sub-millisecond at this scale, no concurrent
writers (the only writer is a human-gated accept step appending a couple of
lines to two flat files). If the dictionary ever grows into the thousands
of examples, `sqlite3` (stdlib, still zero new dependency) is the next step
up — not a client-server database.

---

## Adding a new step

Three tiers, lightest first:

0. **Search first** — `noodle step-search "<description>"` or
   `noodle repl`'s `find a step for ...` (above). If it finds a good match,
   you're done; if it drafts a new one that fits, accepting it does most of
   tier 3 below for you.
1. **Call a function** — no framework changes at all. Put the logic in any Python
   file and drive it with `calls the function '...'` (see above).
2. **Project-local step definition** — a behave `@when`/`@then` in
   `noodle_tests/steps/` (e.g. `custom_hooks.py`); loaded before the catch-all, so it
   wins over pattern matching. See encyclopedia §15 and `custom_steps.feature`.
3. **Built-in pattern** — extend Noodle itself, as below. Right when the step is
   generic enough that every project would want it.

### What the editor warns about

Two different editor integrations exist; don't confuse them.

`cucumberautocomplete` (`.vscode/settings.json`) points at
`.cucumber_stubs.py`, which registers three wildcard `.*` patterns — one each
for `Given`, `When`, `Then`. Every step matches, so *it* never reports
"undefined step".

The **Noodle VS Code extension** (`vscode-extension/`) is the one that warns.
It runs `noodle/lsp/server.py`, which calls the real `match_step()` on every
step and emits

```
No built-in pattern matched — LLM will resolve at runtime.   noodle(llm-fallback)
```

for anything `PATTERNS` doesn't cover. Severity is configurable via
`noodle.unknownStepSeverity` (`warning` by default; `off` disables it).

> **Gotcha — a stale warning after `patterns.py` changes.** The language
> server is a long-lived process that imports `patterns.py` **once, at
> startup**. Python does not reload modules, so a pattern added since the
> server booted — by you, or by a `git pull`/branch switch — is invisible to
> it: the editor keeps flagging `llm-fallback` on a step that now matches
> perfectly. **Reload the VS Code window** (`Cmd/Ctrl+Shift+P` →
> *Developer: Reload Window*) after any change to `patterns.py`.
>
> To tell a stale warning from a real gap, ask the code directly — this is
> the same call the server makes:
>
> ```bash
> python -c "from noodle.resolver import match_step; print(match_step('<step text>'))"
> ```
>
> A tuple back means the pattern exists and the editor is out of date;
> `None` means the gap is real.

The extension picks its interpreter in this order: `noodle.pythonPath` →
the workspace's `.venv` → bare `python3`. A workspace with no `.venv`, whose
system `python3` has no Noodle, gets **no diagnostics at all** (the server
fails to start). Point `noodle.pythonPath` at a Python that has Noodle
installed if warnings never appear.

### What actually validates a step

The check happens at **runtime**. `noodle/steps/catch_all.py` intercepts every step and hands it to `noodle/resolver/patterns.py`. If no regex there matches, the scenario fails (see *What happens when a step is not found* below).

### How to add a new pattern

**0. Check it's actually missing.** Cheapest first — a "missing" step is
usually a stale editor (above) or phrasing that already resolves:

```bash
python -c "from noodle.resolver import match_step; print(match_step('<step text>'))"
noodle steps <keyword>          # or: noodle step-search "<description>"
```

**1. Write the step** in your `.feature` file — the editor accepts it immediately.

**2. Add a regex** to `noodle/resolver/patterns.py` → `PATTERNS` list:

```python
(r'^your pattern here (.+)$',  'your_action',  lambda m: {'param': m.group(1)}),
```

Patterns are tried top-to-bottom; **first match wins** — so position is part
of the change, not an afterthought. A specific pattern must sit *above* any
general one that would also swallow it (the `assert_compare` catch-all
`"X should contain Y"` is the usual culprit — the suggestion assertions are
deliberately placed above it for exactly this reason). Match against the
*normalized* text: the subject is already stripped and the verb forced to 3rd
person, so write `^clicks?` and never `^(?:the user|I) clicks`.

**3. Add an action handler** in `noodle/agents/web/actions.py`:

```python
def your_action(page: Page, param: str):
    ...
```

Skip this step when an existing action already does the job — a new *phrasing*
for an existing capability is pattern-only, and that is the common case.

**4. Wire the dispatch** in `noodle/orchestrator/runner.py` → `execute_step()`:

```python
elif t == 'your_action':
    actions.your_action(page, **params)
```

**5. Add a unit test** in `unit_tests/test_patterns_*.py`. Assert against the
same pipeline `resolve()` uses — normalize, then match — so subject-stripping
and phrasing aliases are covered too:

```python
from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject

def _resolve(text):
    return match(normalize_phrasing(normalize_subject(text)))

def test_your_action():
    assert _resolve("User does the thing to 'x'") == ("your_action", {"param": "x"})
```

Test at least one phrasing variant and one *negative* case — a neighbouring
step that must **not** be captured by the new regex. Run with
`pytest unit_tests/test_patterns_*.py`.

**6. Document it** — add the phrasing to this file and to
`docs/encyclopedia.md` in the matching section. The dictionary is what
`noodle step-search`, the REPL, and the agent playbook read, so an
undocumented pattern exists but is undiscoverable.

**7. Reload the VS Code window** so the language server re-imports
`patterns.py`, or it will keep warning on the step you just added.

No LSP or stub changes are needed beyond that restart — the server derives
everything from `PATTERNS`.

---

## What happens when a step is not found

When a step doesn't match any known pattern, the framework tries two things in order:

### 1. Pattern match fails

The step text is normalised (subject stripped, verb normalised to 3rd person) and compared against every regex in `patterns.py`. If nothing matches, you get:

```
AssertionError: No pattern matched: "User frobnicates the widget"
  Normalized to: "frobnicates the widget"
  → Add a pattern to noodle/resolver/patterns.py
  → OR set NOODLE_MODEL in .env to enable LLM fallback
```

The scenario stops at that step and is marked **FAILED**.

### 2. LLM fallback (optional)

If `NOODLE_MODEL` is set in `.env`, the framework sends the unmatched step to the configured LLM and asks it to infer the action. The model picks from the known action types (click, fill, assert_visible, etc.) and constructs the parameter dict. This is a best-effort recovery — it works well for standard UI interactions phrased unusually, but won't invent new action types.

To enable:
```env
NOODLE_MODEL=claude-sonnet-4-6   # or any litellm-compatible model id
```

Requires the llm extra:
```
pip install noodle[llm]
```

If the LLM returns something unparseable or an unknown action type, the step still fails with a clear error.

### Common reasons a step doesn't match

| Symptom | Likely cause |
|---|---|
| Step uses `{env:VAR}` but value is empty | Variable not defined in `.env` or `environments.yaml` |
| Step with `{var:VAR}` not substituted | Variable not yet set by a prior step |
| REST step not matching | Body contains single quotes — use double quotes inside JSON |
| "performs a X call at Y" not matching | Path must be in single quotes: `'/objects'` |
| Table step not matching | Step must end with `:` — e.g. `the response body should contain:` |

---

## Agent-Suggested Steps (staging)

Steps accepted via `noodle step-search --accept` or the `noodle repl` y/N
prompt land here first, alongside `docs/agent_patterns.yaml`. Review
periodically and promote a good one into `noodle/resolver/patterns.py`
(see "How to add a new pattern" above), then delete its entry here and from
`docs/agent_patterns.yaml`.

<!-- agent-suggestions-anchor -->
