"""Prompt templates for agent test generation (NOOD_0007).

One place for every prompt the agent sends to a model, so wording is tuned
here — not scattered through generate.py. Templates embed STEP_VOCABULARY,
the canonical pattern phrasings, so even a small local model (the primary
target — Ollama-class, per decision #2) writes steps the deterministic
resolver understands instead of inventing its own grammar.
"""
import os
import re

# Canonical phrasings from noodle/resolver/patterns.py — the vocabulary the
# engine resolves without an LLM. Curated, not generated: the point is to show
# the model one good example per action family, not all 100+ regexes.
# ponytail: hand-kept list; extend when a generated suite misses a family.
#
# NOOD_0101 — kept as (section, trigger, text) tuples so relevant_vocabulary()
# can prompt with only the families a request plausibly needs: prompt input is
# the second-biggest latency/token cost of a generation call (after output
# length), and a plain login test never needs the REST or payload sections.
# trigger=None → always sent. A missed trigger only costs one repair pass
# (the repair prompt gets the full vocabulary), never a wrong file.

_VOCAB_SECTIONS: list[tuple[str, str | None, str]] = [
    ("navigation", None, """\
Navigation / setup:
  Given User is on "https://example.com"
  When User reloads the page
  When User goes back
  When User sets the viewport to "1920x1080"
"""),
    ("interaction", None, """\
Interaction:
  When User clicks the login button
  When User double-clicks "Row 3"
  When User enters "value" in the username field
  When User searches for "blue running shoes"
  When User selects the "running shoes" suggestion for "runni"
  When User selects "Blue" from the color dropdown
  When User checks the "Remember me" checkbox
  When User hovers over the "Products" menu
  When User presses "Enter"
  When User submits the login form
  When User scrolls down
(searches for = one step: finds the search box — opening it first when it
hides behind a search icon — fills it and presses Enter. Prefer it over a
hand-rolled click/enter/press chain. selects the "..." suggestion for "..."
= the typeahead flow in one step: types the partial term — any term, kept
exactly as the prompt spells it — waits for the suggestion list, clicks the
matching row. Never hand-roll suggestion clicks with selectors.)
"""),
    ("waiting", None, """\
Waiting:
  When User waits until "Loading" disappears
  When User waits for the page to load
  When User waits for 3 seconds
"""),
    ("conditional",
     r"\bpop-?ups?\b|\bmodal\b|\bdialog\b|\boverlay\b|\bbanner\b|\bconsent\b"
     r"|\bcookies?\b|\bif\b|\bappears?\b|\boptional\b|\bdismiss|\bskip\b"
     r"|\bprompts?\b|\bgeo?location\b|\bpermissions?\b",
     """\
Conditional (only when the flow truly branches — e.g. a popup that only
sometimes shows; the inner step is any other step from this vocabulary):
  When User clicks "Skip" if "Tour popup" appears
  When User clicks "Open menu" if "Sidebar" is not visible
  Given if the page appears to have a pop-up, closes the pop-up
  When User closes the popup if it appears within 10 seconds
(the "within N seconds" form keeps watching for a popup that arrives late.
A browser permission prompt — geolocation/camera/notifications — is browser
chrome, not a page popup; dismiss it with its own step:)
  When User closes the location prompt
"""),
    ("assertions", None, """\
Assertions:
  Then User should see "Welcome"
  Then User should not see "Error"
  Then the search suggestions for "runni" include "running"
  Then the url should contain "/dashboard"
  Then the page title should contain "Home"
  Then the "username" field should contain "Alice"
  Then the "Submit" button should be enabled
"""),
    ("variables",
     r"\bstores?\b|\bcaptur|\bremember|\bextracts?\b|\bsav(?:e|es|ing)\b"
     r"|\breuse|\bcompar|\bgreater\b|\bless\b|\{var:",
     """\
Variables:
  When User stores the text of the "total" element as {var:total}
  Then {var:total} should be greater than "0"
"""),
    ("data-driven",
     r"\boutlines?\b|\bexamples?\b|\bmultiple\b|\bseveral\b|\bvarious\b"
     r"|\beach\b|\bevery\b|\bdata.?driven\b|\bcsv\b|\bcombinations?\b"
     r"|\bdatasets?\b|\busers\b|\bcredentials\b",
     """\
Data-driven (Scenario Outline) — use when the SAME flow repeats with
different data; <placeholders> are unquoted and every Examples column name
must match a <placeholder>:
  Scenario Outline: Login as <username>
    When User enters <username> in the username field
    And User enters <password> in the password field
    And User clicks the login button
    Then User should see <result>

    Examples:
      | username  | password  | result      |
      | reel_ryan | Popcorn1! | VHS Catalog |
      | bad_user  | bad_pass  | Invalid credentials |
"""),
    ("tables",
     r"\btables?\b|\bforms?\b|\bfill(?:s|ed)?\b|\bcolumns?\b|\brows?\b|\bgrid\b",
     """\
Table-driven steps (step ends with ':' and a | … | data table follows;
the first table row is a label header like | field | value |):
  When User fills in the form with:
    | field    | value          |
    | username | {env:USERNAME} |
  Then the row containing "Jaws" should have values:
    | column | value |
    | Year   | 1975  |
  Then the table should have columns:
    | column |
    | Title  |
  Then the response body should contain:
    | Key  | Value |
    | name | Ada   |
"""),
    ("rest",
     r"\bapi\b|\brest\b|\brequests?\b|\bendpoints?\b|\bresponses?\b"
     r"|\bstatus\b|\bheaders?\b|\btokens?\b|\bhttp\b|\bjson\b"
     r"|\bget\b|\bpost\b|\bput\b|\bpatch\b|\bdelete\b",
     """\
REST API:
  When User sets a request header 'Accept' to 'application/json'
  When User sets the bearer token to '{env:API_TOKEN}'
  When User performs a GET request at '/users/1'
  When User performs a POST request at '/users' with body '{"name": "Ada"}'
  Then the response status should be 200
  Then the response body should contain 'Ada'
  When User extracts 'id' from the response and stores it as {var:user_id}
"""),
    ("images",
     r"\bimages?\b|\bcarousels?\b|\bbanners?\b|\bflyers?\b|\blogos?\b"
     r"|\bavatars?\b|\bthumbnails?\b|\bposters?\b|\bpictures?\b|\bphotos?\b"
     r"|\bocr\b|\bpixels?\b",
     """\
Image content (NOOD_0114) — text/objects rendered inside an image's pixels
(carousels, flyers, banners, logos, avatars, profile pictures). OCR-based,
scoped to one element's rendered box; the noun (image/carousel/banner/flyer/
logo/avatar/thumbnail/poster…) picks the pixel path over the DOM path:
  When User focuses on the "product carousel" image
  When User clicks "Dog" in the "product carousel" image
  Then the "sale flyer" image should show "50% off"
  Then the "hero" banner should not show "Sold out"
  When User reads the text from the "sale flyer" image into [FLYER_TEXT]
  When User reads the price from the "product card" image into [PRICE]
  When User reads the screen text into [SCREEN_TEXT]
  (object recognition — requires a vision LLM (NOODLE_MODEL), nondeterministic:
   the scenario is auto-tagged @potential-flake and the step gets a ⚠ comment)
  Then the "hero" image should depict "a golden retriever"
  Then the screen should show a picture of "a red sports car"
"""),
    ("advanced",
     r"\bpayloads?\b|\bfunctions?\b|\bpreconditions?\b|\bseed|\bscripts?\b"
     r"|\bsetup\b|\bteardown\b|\bfixtures?\b",
     """\
Advanced — only when the request needs seeded server-side state or custom
logic, not for ordinary UI steps:
  Given uses this payload 'payloads/seed_cart.json'
  When User calls the function 'resources/functions/helpers.py:make_username' and saves the result as {var:USERNAME}
  (tag a Scenario with @precondition:NAME to run setup/teardown from resources/preconditions.yaml)
"""),
]

# The full vocabulary, unchanged shape — still what the noodle://vocabulary
# MCP resource publishes and what the repair/reflect prompts fall back to.
STEP_VOCABULARY = "\n".join(text for _, _, text in _VOCAB_SECTIONS)


def relevant_vocabulary(description: str) -> str:
    """The vocabulary sections a generation request plausibly needs (NOOD_0101).
    Core families (navigate/interact/wait/assert) always ship; specialised
    ones (REST, tables, variables, data-driven, advanced) only when the
    request's wording triggers them — roughly halving prompt tokens for the
    common login/search/form case. NOODLE_PROMPT_VOCAB=full restores the old
    send-everything behaviour."""
    if os.getenv("NOODLE_PROMPT_VOCAB", "").lower() == "full":
        return STEP_VOCABULARY
    d = description.lower()
    return "\n".join(text for _, trigger, text in _VOCAB_SECTIONS
                     if trigger is None or re.search(trigger, d))

SYSTEM = """\
You are the Noodle test-automation agent. Your only job is turning plain-English
test requests into Behave .feature files (and the JSON describing them) for the
Noodle framework — nothing else.

Boundaries:
- Only use step phrasings from the vocabulary you are given; never invent new
  step grammar.
- Never suggest or output shell commands, code outside a .feature file, or
  actions unrelated to authoring/running Noodle tests.
- If a request names no identifiable test target, say so plainly instead of
  guessing.
"""

GENERATION = """\
You write Behave .feature files for the Noodle test framework.

Rules:
- Output ONLY the .feature file content — no commentary, no markdown fence.
- Start with the wok routing tag line, then "Feature:", then one or more
  scenarios. Pick the tag from the test description: @web (default),
  @api (pure REST, no page), @perf (load test), @android/@ios (native
  mobile app), @windows/@mac (native desktop app), @visual (drive by
  image/OCR). If the description names a tag, use exactly that one.
- Every step MUST use one of the sentence shapes below (change only the
  quoted values, field names, and URLs). Do not invent other phrasings.
- Values that are credentials or environment-specific are written {{env:NAME}},
  e.g. {{env:USERNAME}}, so they resolve from config at run time. Values
  captured by an earlier step are written {{var:NAME}}. To pin one element to
  an exact pom.yaml key, write {{pom:key name}}.
{negative_rule}
Sentence shapes the framework understands:
{vocabulary}

Application under test: {url}
Test description: {description}
"""

REPAIR = """\
You wrote a Behave .feature file, but these steps do not match any sentence
shape the framework understands:

{unmatched}

Rewrite the COMPLETE .feature file, replacing only those steps with the
closest sentence shape from this list (keep everything else unchanged):

{vocabulary}

Output ONLY the corrected .feature content — no commentary, no markdown fence.

Current file:
{feature}
"""


# NOOD_0101 — line-level repair. The old REPAIR template resends the whole
# file and asks for the whole file back; output tokens are decoded serially,
# so regenerating a 30-line file to fix 2 steps is the slowest part of a
# generation call — and gives the model 28 chances to mangle lines that were
# already right. This one sends only the misses and gets only the fixes back;
# generate.py splices them in deterministically. REPAIR stays for the case a
# line-repair can't help with (the draft didn't parse as Gherkin at all).
REPAIR_STEPS = """\
These Behave steps do not match any sentence shape the Noodle framework
understands:

{unmatched}

Rewrite EACH step as the closest sentence shape from this list, keeping its
meaning and its Given/When/Then/And keyword:

{vocabulary}

Reply with ONLY the rewritten steps — one per line, same order as the input,
no numbering, no blank lines, no commentary, no markdown fence.
"""


REFLECT = """\
You wrote this Behave .feature file for the Noodle framework, and it failed
when run against the real application.

Failing scenario: {scenario}
Failing step: {step}
Error: {message}

Rewrite the COMPLETE .feature file with a fix for this failure — e.g. correct
expected text, wrong navigation, or a step that doesn't match what's really
on the page. Keep every other step unchanged. Only use step phrasings from
this vocabulary:

{vocabulary}

Page object selectors defined for this test (may need adjusting too):
{pom}

Output ONLY the corrected .feature content — no commentary, no markdown fence.

Current file:
{feature}
"""


PLAN = """\
Break this request to an automated web-testing agent into an ordered list of
steps. Reply with ONLY a JSON array, no commentary. Each element is one of:

  {{"action": "create", "description": "<what to test, one short phrase>", "url": "<target website>"}}
  {{"action": "scaffold", "kind": "<one of: feature, secrets, environments, pom, preconditions, payload, function, data>", "app": "<app folder name, e.g. busterblock>", "description": "<what the file is for, if given>", "fields": ["<field names mentioned, e.g. username, password>"], "name": "<a short file/precondition/function name if one is implied>"}}
  {{"action": "run"}}
  {{"action": "summary"}}
  {{"action": "open_report"}}

"run" means run the test just created; "summary" means describe the pass/fail
results in text; "open_report" means build and open the Allure HTML report in
the browser. Only include a "run", "summary" or "open_report" step if the
request actually asks for it: "run it" → run; "check the results" / "what
failed" → summary; "show me the report" / "open the report" → open_report.

Use "create" only when the request describes a whole new test AND names a
website/URL. Use "scaffold" when the request asks for ONE supporting file for
an app that likely already exists (a secrets file, an environments.yaml, a
POM/page-object file, a precondition, a payload, a custom function, a data
file) — no URL is required for "scaffold". If the request names no app and no
website anywhere, return an empty array.

Request: {text}
"""


def plan_prompt(text: str) -> str:
    return PLAN.format(text=text)


# NOOD_0030 §2.4 — the vocabulary already expresses negative paths (should
# not see, error text, non-200 status); this rule just tells the model to use
# them. Only injected when the request explicitly asks (generate.py's
# _NEGATIVE_ASK_RE), so ordinary generations stay single-scenario.
NEGATIVE_RULE = """\
- ALSO include one negative-path scenario for the same flow (empty or invalid
  input, wrong credentials, an expected error message, or a failing response
  status), clearly named e.g. "Scenario: <flow> shows an error on invalid input".
"""


def generation_prompt(description: str, url: str, negative: bool = False) -> str:
    return GENERATION.format(vocabulary=relevant_vocabulary(description),
                             url=url, description=description,
                             negative_rule=NEGATIVE_RULE if negative else "")


def repair_prompt(feature_text: str, unmatched: list[str]) -> str:
    lines = "\n".join(f"  - {s}" for s in unmatched)
    return REPAIR.format(unmatched=lines, vocabulary=STEP_VOCABULARY,
                         feature=feature_text)


def repair_steps_prompt(unmatched: list[str]) -> str:
    """NOOD_0101 — fix only the broken lines. Full vocabulary on purpose: the
    generation prompt may have trimmed away exactly the family the model
    needed, and this is the backstop that must not miss twice."""
    lines = "\n".join(f"  - {s}" for s in unmatched)
    return REPAIR_STEPS.format(unmatched=lines, vocabulary=STEP_VOCABULARY)


def reflect_prompt(feature_text: str, pom_text: str, failure: dict) -> str:
    return REFLECT.format(
        scenario=failure.get("scenario", ""),
        step=failure.get("step") or "(unknown step)",
        message=failure.get("message", ""),
        vocabulary=STEP_VOCABULARY,
        pom=pom_text or "(none)",
        feature=feature_text,
    )


# NOOD_0169 — the ONE model-fallback prompt for natural language the
# deterministic prompt compiler could not resolve. The model translates into
# the same typed goal + per-clause coverage; it never writes Gherkin, never
# invents surface controls, and its output passes the same normalize/
# validate/review gates as deterministic output.
PROMPT_TO_GOAL = """\
Translate a user's plain-English test request into ONE JSON object — no
prose, no markdown fence:

  {{"goal": <goal object>, "coverage": {{"<clause-id>": "<what covers it>"}}}}

The goal object's exact vocabulary (keys and allowed values):
{vocabulary}

A minimal valid goal, for shape only:
{example}

Hard rules:
- EVERY clause id below must appear in coverage (use "navigation",
  "dismissal", "metadata", or the action/check that represents it).
- Never invent a click/enter/select target the clauses do not name —
  surface controls come from the page probe, not from you.
- Verifying an added/selected item is item_in_destination with
  expected_from naming the pick's id — never a count or generic text.
- Keep requested URLs in order under navigation.

Clauses:
{clauses}

Original request:
{prompt}

JSON:"""


def prompt_to_goal_prompt(prompt: str, clauses: list[dict],
                          vocabulary: dict, example: dict) -> str:
    import json
    lines = "\n".join(f'  {c["id"]} (line {c["line"]}): {c["text"]}'
                      for c in clauses)
    return PROMPT_TO_GOAL.format(vocabulary=json.dumps(vocabulary, indent=1),
                                 example=json.dumps(example, indent=1),
                                 clauses=lines, prompt=prompt)
