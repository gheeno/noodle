# NOOD_0115 — assertion/locator + result-count gaps found generating the retail SPA flyer+search test (plan only)

Source: generating `noodle_tests/example_spa/features/homepage_flyer_and_hotwheels_search.feature`
in the `example_spa` workspace (homepage promo-tile assertions → search →
assert 90+ results). Two scenarios, five assertions, ~15 steps total —
should have been a 10-minute job. It cost ~368 AIC across 2 `probe_page`
calls, 3 full `run` iterations, and roughly 6 ad-hoc Playwright/Python
scripts written just to reverse-engineer why steps that read the dictionary
correctly still failed. None of the 3 failures were app bugs or step-writing
mistakes — every one traces to a specific engine gap below.

## Where the cost actually went

The authoring itself (writing the Gherkin, running `validate`) was cheap —
one pass, clean on the first try. The expensive part was **root-causing
mechanical failures that gave no actionable signal**: each of the 3 failed
runs required reading engine source (`locator.py`, `actions.py`,
`patterns.py`, `pom.py`, `script_runner.py`) and writing throwaway
Playwright scripts to inspect the real DOM, because the RCA report and
failure message didn't say *why* a dictionary-correct step couldn't resolve.
That reverse-engineering loop, not the test-writing, is what needs to
shrink.

## 1. `assert-visible-accessible-name-parity` — give `wait_for`/`assert_visible` the same resolution power as `find()`

**Symptom:** `Then User waits until 'Shop Backyard living' is visible`
timed out at 120s even though the tile is present and rendered on load.
A sibling assertion, `'Weekly Flyer'`, appeared to pass — but only because
it coincidentally matched an unrelated footer newsletter blurb containing
the same words; it wasn't actually asserting on the intended tile.

**Cause:** `assert_visible`/`wait_for` (`noodle/agents/web/locator.py:364-420`,
`noodle/agents/web/actions.py:258-285`) call `page.get_by_text(text,
exact=False)` directly — no POM lookup fallback beyond the earliest
"explicit `{pom:key}`" check, no accessible-name (alt/aria-label/title)
matching, and none of `find()`'s self-heal chain. `find()` (used by
click/hover/fill) has a rich chain: explicit POM → non-explicit POM →
role/name accessibility strategies → visible-narrowing → self-heal (scroll,
POM retry, partial-text, DOM attribute scan, vision). **`wait_for` skips
essentially all of it.** On this page the four promo tiles are raster
images with the caption only in `alt`/`aria-label` — there is no text node
to match, so no timeout length would ever have fixed it.

**Fix:** route `wait_for`/`wait_hidden`/`assert_visible` through the same
resolution entry point `find()` uses (a shared `resolve_locator()` that both
call), instead of maintaining a second, weaker parallel implementation.
Practically: `wait_for` becomes "poll `find()` until it returns a match or
budget expires" rather than "poll `get_by_text` until it appears." This is
the single highest-leverage fix here — it would have fixed the failure
outright, with zero test-author changes (the POM entries I ended up writing
would become optional hardening, not a required workaround).

**Where:** `noodle/agents/web/locator.py` (`find()` ~107-290, `wait_for`
~364-420), `noodle/agents/web/actions.py` (`assert_visible` ~258-285).

## 2. `probe-flag-alt-only-captions` — have `probe_page` call out image-only text up front

**Symptom:** Nothing in the `probe_page` output for the homepage indicated
that "Weekly Flyer" / "Shop Backyard living" / etc. exist *only* as
`alt`/`aria-label` attributes on `<img>` tiles, not as visible text nodes.
The suggested steps it emitted looked identical to ones for real text
content, so there was no signal to write a POM entry before the first run.

**Cause:** `probe_page`'s control dump doesn't currently distinguish "this
label came from a text node" vs. "this label came from an accessible-name
attribute with no backing text node" — a distinction that matters a lot
once you know gap #1 exists.

**Fix:** for every element `probe_page` names by alt/aria-label/title with
no matching visible text node, tag it in the output (e.g. `⚠ caption is
image-only (alt text) — a plain "should see"/"waits until visible" step
needs a POM entry`) and pre-emit the POM `alt_text:` block for it (probe
already does this for some cases — make it unconditional whenever the only
label source is an attribute, not a heuristic). This turns a
run-fail-then-diagnose loop into a write-it-right-the-first-time step,
independent of whether fix #1 ships.

**Where:** `noodle/agents/dev/probe.py` (or wherever probe's control
enumeration/labeling lives) plus its POM-suggestion emitter.

## 3. `count-step-semantic-mismatch` — `assert_count` doesn't count what its own docs say it counts

**Symptom:** `Then User should see at least 90 'product' items` found 8, not
93 (the real number of product tiles on the results page).

**Cause:** `assert_count` (`noodle/agents/web/actions.py:623-634`) is a
literal substring text counter — `page.get_by_text(locator_text,
exact=False).locator("visible=true").count()`. It never consults POM, never
looks at DOM structure/repeated-card patterns, and doesn't call `find()`.
`docs/steps_dictionary.md`'s own example, `Then User should see at least 3
'product' items`, strongly implies "count product cards," but the
implementation counts DOM nodes whose **visible text literally contains the
word "product."** On a real catalog page, no single word appears on all N
product tiles (verified directly: of "Hot Wheels" (52), "Car" (44), "Toy"
(16), "$" (26), none reached 93) — so this step is structurally unable to
verify a realistic "N result cards" assertion, no matter how it's phrased.

**Fix, two parts:**
- **Short-term (docs):** fix the misleading example in
  `docs/steps_dictionary.md` — replace `'product' items` with a case where
  literal-substring counting actually works (e.g. counting rows containing a
  repeated status word), and add a caveat: *"this step counts elements whose
  visible text contains the given word — for structural counts (e.g. 'N
  product cards'), the count won't match a human's notion of 'N items"* on
  arbitrary e-commerce grids.
- **Longer-term (engine):** let `assert_count` optionally resolve its
  locator through POM first, the same way `find()`/`store_text` already do,
  so a test author can define `products: {css: "li[class*='product']"}` in
  a page-scoped POM file and write `Then User should see at least 90
  '{pom:products}' items` for a genuine structural count. This reuses
  existing POM-resolution code rather than inventing a new mechanism.

**Where:** `noodle/agents/web/actions.py` (`assert_count`),
`docs/steps_dictionary.md`.

## 4. `result-count-extraction-step` — no native step for "parse the number out of a results-summary string"

**Symptom:** The only reliable source of the real result count was the
page's own summary text (`<span class="nl-filters__results">93
results</span>`). No dictionary step extracts/parses a number out of
arbitrary page text (`extracts ... from response` is API/JSON-only) — I had
to hand-roll a custom Python function (`helpers.py:parse_int`) plus a
`store_text` → `call_function` → `assert_compare` step chain just to compare
"93" against "90".

**Cause:** this is an extremely common UI pattern (search/results pages,
review counts, cart badges, pagination totals) with no first-class step.
Every test author hitting it has to rediscover the same three-step custom
function workaround.

**Fix:** add a native step, e.g. `Then the number in '<locator>' should be
at least <N>` (and `at most`/`exactly` variants), implemented as: resolve
`<locator>` the same way `store_text` does (POM-aware), regex out the first
integer (handling thousands separators), and numerically compare — i.e.
promote the exact workaround pattern from this session into a first-class
step instead of leaving it to a bespoke custom function every time.

**Where:** new pattern in `noodle/resolver/patterns.py` (near the existing
`assert_compare`/count-comparison entries, ~886-895 / ~629-643), new action
in `noodle/agents/web/actions.py` that composes `get_text` + int-parse +
compare.

## 5. `call-function-string-arg-splitting` — `call_function` silently shlex-splits multi-word string args

**Symptom:** `calls the function 'helpers.py:parse_int' with args
'{var:RESULTS_TEXT}' and saves the result as {var:RESULT_COUNT}` raised
`TypeError: parse_int() takes 1 positional argument but 2 were given` — the
captured variable held `"93 results"`, a two-word string.

**Cause:** `noodle/orchestrator/script_runner.py`'s `call_function` does
`fn(*(shlex.split(args) if args else []))` — any captured page text
containing whitespace becomes multiple positional arguments, not one
string. This is invisible until the custom function is written to accept
exactly one param and the error message doesn't mention shlex or splitting
at all.

**Fix, two parts:**
- **Immediate:** improve the `TypeError` handling around the `fn(...)` call
  in `call_function` to catch arity mismatches and re-raise with a hint:
  *"args were shlex-split into N tokens: (...) — if you intended to pass one
  string, accept `*args` and join it, or quote/escape so it splits as
  intended."*
- **Better:** offer an explicit syntax for "pass this whole value as one
  arg, don't shlex-split it" (e.g. a distinct `with raw arg '{var:X}'`
  phrasing, or detect that the entire `args` string came from a single
  `{var:...}` substitution and skip shlex splitting in that case, only
  splitting when args are literal space-separated tokens in the feature
  file).

**Where:** `noodle/orchestrator/script_runner.py` (`call_function`,
~67-96), matching pattern entry in `patterns.py` (~330-333).

## 6. `dom-inspect-cli-command` — no lightweight way to ask "why does/would this locator resolve to X" outside a browser run

**Symptom:** confirming the real product count (93), that no single literal
word covered all tiles, and that `results` was resolving to the wrong
"Search Results" breadcrumb span all required writing one-off Python +
Playwright scripts by hand, outside Noodle entirely.

**Cause:** there's no CLI/MCP command to ask Noodle itself "resolve this
locator text against this URL and show me every match, which one is
visible, and why" — `probe_page` dumps controls but doesn't accept an
arbitrary phrase to test resolution against, and there's no equivalent of
`find()` exposed as a standalone debug command.

**Fix:** add `noodle inspect <url> "<locator text>"` (and an MCP
`inspect_locator` tool) that runs the exact same resolution path `find()`
uses, headless, and prints every candidate with: source (text node / alt /
aria-label / POM key / self-heal tier), visibility, and a screenshot
highlight. This turns "write a throwaway script" into "run one command" for
the debugging loop that dominated this session's cost.

**Where:** new CLI subcommand wrapping `noodle/agents/web/locator.py`'s
resolution path; MCP tool registration alongside `probe_page`.

## Priority / sequencing

| # | Item | Priority | Why |
|---|---|---|---|
| 1 | assert-visible-accessible-name-parity | P0 | Would have fixed the actual failing run outright; zero test-author workaround needed |
| 2 | probe-flag-alt-only-captions | P0 | Prevents the failure before authoring, independent of #1 shipping |
| 4 | result-count-extraction-step | P1 | Very common pattern (results/review/cart counts); currently forces a custom function every time |
| 3 | count-step-semantic-mismatch | P1 | Docs fix is trivial and should ship regardless; POM-aware count is a natural follow-on once #4's resolution code exists |
| 5 | call-function-string-arg-splitting | P2 | Real gotcha but narrower blast radius; error-message fix alone removes most of the pain |
| 6 | dom-inspect-cli-command | P2 | High value for future debugging loops, but larger scope — best scheduled after the P0/P1 fixes reduce how often it's needed |

## Action items

- [x] Implement `assert-visible-accessible-name-parity`: unify `wait_for`/
      `assert_visible` onto `find()`'s resolution chain (`locator.py`,
      `actions.py`).
- [x] Implement `probe-flag-alt-only-captions`: flag image-only captions and
      pre-emit POM `alt_text` blocks unconditionally (probe module).
- [x] Fix the misleading `docs/steps_dictionary.md` count example; add the
      literal-substring caveat.
- [x] Implement POM-aware `assert_count` (optional `{pom:key}` resolution
      before the substring count).
- [x] Implement a native "number in `<locator>` should be at least N" step
      (`patterns.py` + new action) reusing `store_text`'s POM-aware
      resolution.
- [x] Improve `call_function`'s `TypeError` handling with a shlex-splitting
      hint; consider a raw-single-arg syntax.
- [x] Add `noodle inspect <url> "<text>"` CLI + MCP `inspect_locator` tool.
- [x] Add/extend unit tests for each — `locator.py`/`actions.py` changes
      need browser-free unit coverage matching existing test patterns
      (see `dom_scan.py`'s own no-browser-required test suite as precedent).
