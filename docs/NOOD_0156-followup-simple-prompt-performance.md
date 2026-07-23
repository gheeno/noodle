# NOOD_0156 follow-up — why a 4-line prompt cost 10+ minutes, and the fixes

**Source session:** 2026-07-21, workspace `noodle_tests/nood_0156`, Sonnet 5
via the noodle MCP server. Prompt under investigation:

```
base url : Noodle run on www.<retail-site>.ca
1. Open the website (and close all pop ups on home page)
2. Search for a toy
3. Add to cart
Verify: Toy is added to cart and take screenshot for verification
```

Cost observed: **9 probe calls + 5 author/run calls**, three of them
oversized payloads (68 KB / 604 KB / 67 KB) that forced file-persist +
parse round trips. Root-caused below, it should be **1 author(goal) call**.

## The key finding: prompt understanding was NOT the bottleneck

The agent translated the prompt into the goal schema correctly on the first
try — dismissals, search, pick, add_to, item_in_destination + screenshot
evidence. That mapping took seconds. "Make the engine more intent-aware" is
therefore the wrong lever for THIS failure; the goal contract already
carries the intent faithfully. Everything slow happened **after** the
intent arrived, in four engine gaps:

| # | Gap | Cost it caused |
|---|-----|----------------|
| 1 | `build_result_items` accepted the header promo/nav strip as a result-card group (shared class + distinct hrefs = "card shape"), so the generic pick bound **"Support"** — a banner — and `add_to cart` correctly blocked: no mutation path on a customer-service page | The whole cascade: blocked goal → manual feature_content → intent-contract refusal → `allow_unverified_intent` override → hand-built POM. ~half the session |
| 2 | Compact probe payloads dropped the two things the flow needed: `result_items` was not in the compact passthrough at all, and "add to cart" (a plain button, not type=submit) was capped out of `suggested_steps` by 40 header/footer chrome entries | Two `compact=False` re-probes (604 KB and 67 KB) plus the parsing round trips |
| 3 | A **working** add-to-cart click read as a no-op: `_settle`'s 1 s first-change cap expired before the server round trip rendered the drawer, and a no-delta click left **no record at all** in `revealed` | 4 probes chasing ghost theories (fulfillment tabs, headed vs headless, ThreatMetrix bot-blocking) |
| 4 | The first goal call was rejected on phrasing: free-text dismissals ("closes the popup if it appears…") vs the `popups` enum, boolean `item_in_destination` vs the destination string | One full author round trip; hits non-native-English authors hardest |

Plus one latent engine bug the manual path exposed: the **compiled**
`add_to → click destination` sequence navigates the instant the mutation
click returns, aborting the cart POST in flight (`net::ERR_ABORTED`,
reproduced headed AND headless — it was never bot detection). The goal
path would have shipped the same race.

## Fixes (this branch)

1. **Chrome exclusion in `build_result_items`** (`agents/web/probe.py`) —
   a card-signature group whose members are **majority-persisted** from the
   pre-search page (same selector or same caption) is chrome, dropped as a
   group. Per-item selector diffing stays out (it previously collapsed 891
   real results to zero). Minority overlap (a product also in a homepage
   carousel) keeps the group. `_results_block` now passes the previous
   page's control names alongside its selectors.
2. **Compact payloads keep the goods** — `result_items` added to the
   compact passthrough (already capped at 24); `_rank_ready` now tiers
   visible submits first, visible **mutating-named** controls second
   (add to cart / buy / checkout in 7 languages via `_MUTATING_RE`), chrome
   last — the cap eats junk, never the buy box.
3. **Honest do/settle** — `_settle(mutating=True)` gives state-changing
   clicks a 5 s first-change window (plain reveals keep 1 s); a no-delta
   **click** now always leaves a `revealed` entry with
   `note: "…no observable delta"` (fills/selects stay silent — no-delta is
   their normal case). "Did nothing" and "rendered late" are now
   distinguishable from one payload.
4. **Lenient goal input** (`repl/goal.py` `normalize()`, wired in
   `repl/core.py`) — deterministic keyword canon, no LLM: free-text
   dismissals map to the enum ("closes the popup if it appears…" →
   `popups`, "location" → `location_prompt`), `item_in_destination: true`
   resolves to the sole add_to destination, any evidence phrase containing
   "screenshot" canonicalizes. Every rewrite is echoed back as
   `goal_normalized`. Unmappable input still fails validate() exactly as
   before.
5. **The compiled mutation→observation race** (`repl/goal.py`
   `compile_goal`) — one `User waits for the network to be idle` step is
   emitted before every destination-observation click. Network-quiet is
   ~free when nothing is in flight.
6. **Consistent endings** (`mcp/server.py`) — `run_and_report`
   `serve_reports` now defaults **True** over MCP: the documented workflow
   always ends on served Allure+RCA URLs; a caller omitting the flag no
   longer gets bare file paths.
7. **Discoverable post-action waits** (`docs/steps_dictionary.md`) — the
   `wait_response` / request-complete / element-updates steps are now
   indexed gherkin examples (they existed only in a caveat table, invisible
   to `search_step`). The session's query "wait for cart count to update"
   now ranks a condition wait first instead of `waits for 3 seconds`.

Regression tests: `unit_tests/test_nood_0156.py` ("add-to-cart session
review" section, R1–R5) — chrome-group exclusion by name and by selector,
legacy no-context behavior, minority-overlap survival, bind-never-chrome,
compact result_items, mutating-rank, no-op click record vs silent fill,
mutating settle budget, all normalize forms, settle-before-destination,
serve default. Two existing pinned step-sequences updated for the emitted
network-idle wait.

## Acceptance (re-run the original prompt)

The exact goal from the session, on a fresh `noodle init` workspace:

```json
{"scenario": "Search for a toy and add it to cart",
 "dismissals": ["location_prompt", "popups"],
 "actions": [{"do": "search", "id": "s", "term": "toy"},
             {"do": "pick", "id": "p"},
             {"do": "add_to", "id": "a", "item_from": "p", "destination": "cart"}],
 "checks": [{"item_in_destination": "cart", "expected_from": "p",
             "after": "a", "evidence": "screenshot"}]}
```

Pass bar:
- `p` binds a real product (never header chrome) — zero manual correction,
  zero `allow_unverified_intent`.
- ONE `author_test(goal, run_after_author=true)` call reaches a
  `verified: true` green; no `ERR_ABORTED` from the engine's own step
  sequencing.
- The run payload ends with served report URLs with no flags passed.
- Free-text dismissals in the goal are accepted and echoed in
  `goal_normalized`.

## Deliberately not done (and why)

- **Prompt→goal translation in the engine** — that's the driving agent's
  job and it worked; adding an engine-side NL parser would duplicate the
  LLM above it. The engine's contribution is a forgiving goal schema
  (fix 4) and evidence that's right the first time (fixes 1–3).
- **Network-capture timestamps + a `mutation-aborted-by-navigation` RCA
  verdict** — the right next step for making the RCA name the navigation
  race directly instead of "overlay, consent gate, anti-automation", but it
  needs step-boundary markers in the capture writer; fix 5 removes the
  race at the source, so this is diagnostic polish, not a blocker.
- **Enforcing the human-only `allow_unverified_intent`** — still
  honor-system; with fix 1 the legitimate need for it collapses. Tracked
  as an open question, since an MCP parameter cannot verify a human.
