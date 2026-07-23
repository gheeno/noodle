# Building a Suite Against a Real External Site (Worked Example)
<!-- Branch: NOOD_0028 -->

**Who this is for:** an AI coding agent — or a new tester comfortable in a
terminal — asked to build Noodle test coverage for a site with no local
server, no existing suite, and no docs of its own. This walks through one
real session end to end: scaffold → write → validate → run → debug real
failures → generate supporting files as they turned out to be needed →
reports. Every command, failure, and fix below actually happened, against
[academybugs.com](https://academybugs.com) — a public QA-training site, not
a fixture.

**This is the direct-CLI path, not `noodle repl`.** The README's ["Your own
test workspace"](manual.md#your-own-test-workspace--a-manual-testers-guide)
walkthrough documents two ways to write tests: hand-write Gherkin, or chat
with `noodle repl`'s REPL. This is a third path — an agent (or a tester
scripting their own workflow) drives the plain `noodle` CLI directly, the
same commands a human would type, no chat loop involved. The decision
procedure for that path is
[agent-playbook.md](agent-playbook.md); this doc is a
worked example of following it against a real, uncontrolled site — not a
fixture that behaves.

---

## 1. Scaffold the workspace outside the repo

Per the playbook: pick a directory next to (not inside) the `noodle` repo
and scaffold it. `noodle init` already names the tests root `noodle_tests/`,
so it can never collide with a host project's own `tests/`.

```bash
noodle init /Users/you/Projects/academybugs_regression
cd /Users/you/Projects/academybugs_regression
```

Delete the scaffolded `sample_app/` package — it's a template, not
something to keep once you have a real app to test. Create the real one:

```bash
mkdir -p noodle_tests/academybugs/features
mkdir -p noodle_tests/academybugs/resources/pageobjects
mkdir -p noodle_tests/academybugs/resources/payloads
```

## 2. Recon before writing a single step

A `.feature` file is only as good as the selectors and page behavior behind
it — for a real site, that means looking at the actual HTML before guessing.
`curl` with a real browser `User-Agent` header beats an AI web-summarization
tool here: a summarizer paraphrases button text ("Add to Cart") when the
actual DOM says something else entirely ("ADD TO CART", all-caps) — exactly
what happened in this session, costing a full debug cycle before the mismatch
was caught by grepping the raw HTML directly.

```bash
curl -sL "https://academybugs.com/find-bugs/" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36" \
  -o /tmp/site.html
grep -oE '<a[^>]*ec_product_addtocart[^>]*>[^<]*' /tmp/site.html   # real button text, real classes
```

What this surfaced before writing anything: the product grid's real CSS
classes, that "Add to Cart" is actually rendered `ADD TO CART`, and — caught
only by re-fetching later in the session — that the site's internal product
ID (`model_number`) is **not stable between requests** (see §4, bug 3).

## 3. Write the feature package

One app package, four `.feature` files, each demonstrating a different part
of Noodle's file surface (this was the actual brief — see
[docs/feature-packages.md](feature-packages.md) for the full package
contract):

| File | Demonstrates |
|---|---|
| `smoke.feature` | Navigation, visibility asserts, `no network requests should fail` |
| `shop_cart.feature` | Page-object YAML (an unlabeled `<select>`, an ambiguous button), a positive network assert |
| `data_driven_cart.feature` | A JSON fixture driving `{env:X}`/`{var:X}`-substituted assertions, explicit waits on AJAX UI |
| `api_response_check.feature` | `@api` — raw HTTP status/body/header asserts, no browser |

Supporting files, added only once a scenario actually needed them (never
scaffold a folder a test doesn't reference):

```
resources/academybugs_environments.yaml       # academybugs: https://academybugs.com
resources/pageobjects/find-bugs_pom.yaml      # 2 entries — see §4, bugs 3 & the section-scoping gap
resources/payloads/product.json               # {"product_name": ..., "expected_price": ...}
```

A page-object filename auto-scopes to any URL containing its stem — this bit
a real naming choice in this session: `find_bugs_pom.yaml` (underscore) does
**not** match a URL containing `find-bugs` (hyphen); the fix was renaming the
file to `find-bugs_pom.yaml`. String equality, not fuzzy matching — see
[feature-packages.md § Per-page POM files need `match:`](feature-packages.md#per-page-pom-files-need-match).

## 4. Validate, then run — and debug like it's a real site, because it is

```bash
noodle validate noodle_tests/academybugs/features --resolve   # no browser — catch unmatched steps first
```

All 9 scenarios resolved deterministically (no LLM fallback needed) before a
browser ever opened. That's the point of `validate`: it catches phrasing
mistakes for free. It does **not** catch behavioral mistakes — those only
show up at `noodle run`, and against a live site, several did:

1. **Wrong assumption: clicking "add to cart" navigates.** It doesn't — the
   site's add-to-cart link has `onclick="...; return false"`, which cancels
   the default navigation and fires an AJAX call instead. The fix wasn't a
   framework issue at all: re-read the actual response (`a request to
   '**/admin-ajax.php*' should have been made`, not `/my-cart*`), and added
   `waits until '...' is visible` before asserting on AJAX-rendered UI.
2. **A `pageobjects/*_pom.yaml` selector matched a hidden duplicate.** WP
   Easy Cart (the site's cart plugin) renders two copies of each add-to-cart
   link — one hidden quick-view duplicate. `.first` on an unscoped selector
   silently clicked the invisible one and timed out. Fixed with `:visible` in
   the CSS selector, same fix already documented in this repo's
   `sample_feature_tests/web/example/resources/pageobjects/home_pom.yaml`.
3. **The site's own product IDs aren't stable.** A selector pinned to
   `model_number=3181370` (captured during recon) silently clicked a
   *different* product on the next run — the site reassigns that ID between
   requests. Confirmed by re-fetching the page twice and diffing which
   product each ID pointed to. Fixed by scoping the selector to the stable
   container (`<li class="ec_product_li">`) plus the product's title text
   instead of the volatile ID — the general lesson: never anchor a selector
   to a value you haven't confirmed is stable across requests, not just
   within one page load.
4. **A third-party widget blocked a click and wasn't worth fixing.** A tour
   overlay (`#TourTipDisabledArea`) intercepted pointer events on one page
   and didn't yield to `Escape`. Routed around by navigating directly instead
   of clicking through — not every real-site quirk is worth automating
   around; this one was out of scope (a marketing widget, not the site under
   test) and documented as a comment in the `.feature` file instead.

None of the four above were framework bugs — they were the ordinary cost of
testing a live, uncontrolled site, exactly why `noodle validate` catching
phrasing mistakes for free matters: it leaves the debugging budget for the
things only a real run can surface.

**Two genuine framework bugs did surface** in the process — both were
blockers baked into the outside-repo layout this doc just walked through
(`hooks.py`/`allure_meta.py` hardcoded `tests/**` instead of reading the
configured `tests_dir`; `loads test data from` didn't resolve into
`resources/` like its sibling payload step). Both fixed the same session;
see `docs/design-history.md` for the entry once folded in, or `git log
--grep NOOD_0028`.

## 5. Reports — Allure, RCA, and per-scenario network logs

```bash
noodle run --workspace . noodle_tests/academybugs/features --headless
noodle summary    --workspace .                                   # plain pass/fail, no browser
noodle rca-report --workspace . -o artifacts/reports/rca-report.md # why each failure happened

cd .                          # report generate/open read cwd, not --workspace (yet)
noodle report generate
noodle report open            # serves over localhost, picks a free port automatically
```

Every `@web` scenario's Allure result now carries its own **network log**
attachment (console errors, failed requests, every request URL, WebSocket
frames) — added this same session, on by default, not gated on
pass/fail, because a passed scenario's clean network trace is worth having
next to a failed one's dirty one. No tag or flag needed; it's wired into
`hooks.after_scenario` for every scenario that had a real browser page. See
`noodle/reporting/writer.py`'s `ScenarioResult.add_attachment()` for the
mechanism — a test-case-level attachment, distinct from the existing
per-step failure-screenshot attachment.

## 6. Result

7 features, 9 scenarios, 6 passing. The one failure
(`smoke.feature` → home page network-health check) is a confirmed, real site
defect — `cdn.polyfill.io` is a dead dependency (the domain was taken down
in 2024) — kept in deliberately rather than relaxed away, because that's
exactly the class of regression a network assertion exists to catch.

## See also

- [agent-playbook.md](agent-playbook.md) — the decision
  procedure this walkthrough follows.
- [feature-packages.md](feature-packages.md) — the package contract
  (`features/`, `resources/`) referenced throughout §3.
- [steps_dictionary.md](steps_dictionary.md) — full step vocabulary; every
  step used in this walkthrough is a documented example, not invented
  phrasing.
- docs/manual.md's ["Your own test workspace"](manual.md#your-own-test-workspace--a-manual-testers-guide)
  — the same workflow from a manual tester's point of view (options A/B);
  this doc is the option-C, agent-driven equivalent.
