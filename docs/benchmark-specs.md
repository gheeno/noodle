# The benchmark specs (NOOD_0232)

The five test specs `noodle benchmark` runs, in the exact wording it sends
them. **This file is the source of truth** — the engine parses the fenced
blocks below at run time, so the spec a human copies is byte-for-byte the
spec the benchmark measures. There is no second copy inside the Python to
drift away from this one.

Each spec is written the way a request actually arrives, because that is
what is being measured. A benchmark whose every case is a numbered
imperative list measures how well the engine handles numbered imperative
lists, and reports it as *generation quality*.

| | |
|---|---|
| **App under test** | BusterBlock — the repo's own bundled VHS-rental site (`test-apps/busterblock`), Express on `127.0.0.1:3333`, with a **login gate** in front of its catalogue |
| **Credentials** | `reel_ryan` / `Popcorn1!` |
| **What is measured** | development time, run time, token cost, corrections, and did the test pass at the end |
| **Where it runs** | a fresh build-stamped `noodle init` workspace under `regression_runs/` |

The login gate is the point. Every spec has to carry credentials and get
through a gate before it reaches the thing being asserted — exactly like the
prompts users send, and unlike a fixture that gates nothing.

## Running it

Two modes, and the difference is **whether an agent is in the loop**. That is
not a detail — it decides what a "blocked" spec means.

### The session run — what the product ships as

The workflow a user actually has: they paste a spec into their Claude or
Copilot session, and the agent translates whatever prose arrived into Noodle
calls. There is always an agent in front of the engine, so no spec is blocked
by phrasing — what is worth measuring is **how much work the loop took.**

```bash
noodle update                # 1. the install must match this checkout
noodle benchmark --session   # 2. starts BusterBlock, scaffolds the workspace,
                             #    seeds secrets.env, prints the runbook
#  ... the session then authors and runs all 5, one at a time ...
noodle benchmark --table     # 3. the table, and the app is stopped
```

`--session` prints a runbook the agent executes: send each spec **verbatim**
first (that first attempt is what measures the engine on its own), and when
the engine refuses, translate it and re-author against the same
`feature_path`. Every attempt is what the ledger counts.

**The numbers are the engine's, not the agent's.** `author_test` appends one
line per attempt to `.noodle/benchmark_ledger.jsonl` while a session is open,
so the table is built from what the engine recorded — including the *gaps
between* attempts, which are the agent's own turnaround and the honest cost of
having it in the loop. An agent tallying its own benchmark is the failure
[NOOD_0190](../CHANGELOG.md) removed from the gate; it is not coming back.

The app's credentials are seeded into the workspace's gitignored `secrets.env`
at setup, as `{env:BUSTERBLOCK_USER}` / `{env:BUSTERBLOCK_PASSWORD}` — so a
spec logs in the way a real test does, and nothing lands in the Gherkin that
should not be committed. They have to be there *before* anything authors: the
probe needs those values to resolve to walk past the login gate, and it runs
inside the same transaction that would otherwise be writing them.

### The headless run — the engine's floor

```bash
noodle benchmark             # no agent, no model: the deterministic compiler alone
```

Same five specs, same app, same one report — but nothing translates for the
engine. Reproducible and free, which makes it the right instrument for
comparing two builds; and a spec it cannot take is a real finding about the
grammar, not about the product a user experiences. `--json` gives the same
verdict as one bounded payload. Exit 0 = PASS, 1 = REGRESSED.

BusterBlock's `node_modules/` is gitignored. If it is missing, the benchmark
**stops and says so** rather than quietly measuring a different app:

```bash
cd test-apps/busterblock && npm ci
```

## Sending a spec by hand

The same five blocks work outside the benchmark, which is the reason they
live in a document rather than in a Python list.

- **Human, in an LLM session** — copy one fenced block and paste it. Nothing
  else: no preamble, no "please write a Noodle test for". The block *is* the
  message. The engine's own numbers come back in the payload.
- **MCP / A2A client** — send the block verbatim as the `prompt` argument of
  `author_test`, with `run_after_author: true`. Same string, same result.
- **CLI** — `noodle author --spec-text "<block>" --run`, or put the block in
  a file and use `--prompt <file>`.

Whichever door you use, do **not** reword the block to help it through. A
reworded spec measures your rewording.

---

## The five specs

<!-- spec: paragraph | expect: blocked -->
### B1 — a paragraph

Prose, hard-wrapped the way a paste out of a ticket or an email arrives.
Multi-sentence, anaphoric ("the Alien listing"), no quoted control names,
credentials carried inline in the sentence rather than in fields.

```text
Please sign in to BusterBlock at http://127.0.0.1:3333/ using the account
reel_ryan with the password Popcorn1!. Once the catalogue loads, search it
for Alien. Confirm that the Alien listing is what you are left looking at.
```

<!-- spec: steps | expect: pass -->
### B2 — step by step

The numbered imperative list: one action per line, control names quoted, the
assertion on its own line. The shape every other benchmark in this repo is
written in, kept here as the **control** — it is what the other four are read
against.

```text
1. Go to the URL http://127.0.0.1:3333/
2. Enter "reel_ryan" in the "username" field
3. Enter "Popcorn1!" in the "password" field
4. Click "login"
5. Enter "Jaws" in the "search movies" field
6. Verify: Jaws
```

<!-- spec: one_liner | expect: blocked -->
### B3 — a single sentence

The minimum anyone will ever send. One line, several actions joined by
"then" and "and", everything else left to inference.

```text
On http://127.0.0.1:3333/ log in as reel_ryan with password Popcorn1! then search movies for The Shining and verify The Shining is shown.
```

<!-- spec: ambiguous | expect: blocked -->
### B4 — a short spec with ambiguous steps

Short, and deliberately underspecified. Every fact needed is present, but
none of it is spelled out: `log in` names no control and no field, the
credentials sit in a header line rather than in a step, and `it should be
there` is an anaphor pointing three lines back.

```text
url: http://127.0.0.1:3333/
user: reel_ryan / Popcorn1!

1. log in
2. search for Ghostbusters
3. it should be there
```

<!-- spec: expected_fail | expect: fail -->
### B5 — a spec that must fail

**The assertion is deliberately wrong.** `Casablanca` is not in BusterBlock's
catalogue, and searching for `Halloween` cannot put it there. This spec is
here to measure what the other four cannot: that a test which *should* fail
**does** fail, visibly, with a diagnosis.

A green run here is the worst result the benchmark can produce — it would
mean the engine reached a pass by dropping the assertion it could not prove,
and every other green in the table would be worth less for it.

```text
1. Go to the URL http://127.0.0.1:3333/
2. Enter "reel_ryan" in the "username" field
3. Enter "Popcorn1!" in the "password" field
4. Click "login"
5. Enter "Halloween" in the "search movies" field
6. Verify: Casablanca
```

---

## Reading the table

```
   SPEC            SHAPE                DEV     RUN   CORR   TOKENS  RESULT
   steps           step by step        2.2s     11s      0      976  ✅ passed
   expected_fail   must fail           2.6s     29s      0     1.2k  ✅ failed as intended
   paragraph       paragraph           0.0s       —      —      413  ⛔ blocked
```

| column | what it is | why it is that and not something else |
|---|---|---|
| `DEV` | **development time** — prompt in, `.feature` written: wall clock minus the generated test's own run time | the number the "under a minute" expectation belongs on. Total wall clock is dominated by the *site*, which is not Noodle |
| `RUN` | the generated test executing | the app's speed, reported separately so it cannot flatter or damn the engine |
| `CORR` | **corrections** — every repair the engine made on your behalf: a reworded control name, an inserted prerequisite click, a dropped or rewritten check, a self-healed locator, a retried-then-green scenario | a spec that only works after five corrections is not the same product as one that works first time, and both end green |
| `TOKENS` | the payload the engine hands back to the driving agent, ÷ 4 | the part of an agent's bill the engine controls. The host's preamble and the model's own reasoning are not in it and cannot be — see [benchmark.md § No cost column](benchmark.md#no-cost-column) |
| `RESULT` | did the feature pass at the end — and for B5, did it fail at the end | `verified` is folded in: a pass reached through fuzzy healing is reported as `passed (unverified)`, never as a plain pass |

`CORR` and `RUN` read `—` on a blocked spec. Nothing was generated, so there
is nothing that could have been corrected and nothing that ran; a `0` there
would be a measured-looking lie in the direction that flatters the engine.

## Expected outcomes, and what a change to one means

Each spec declares an `expect` in its HTML comment above. It records what
**this build actually does**, measured — not what it ought to do.

| `expect` | meaning | a different outcome means |
|---|---|---|
| `pass` | authors, runs, goes green and verified | **REGRESSED** — this build lost something the last one had |
| `fail` | authors and runs **red**, with a diagnosis | **REGRESSED** — either it stopped authoring, or worse, it went green |
| `blocked` | the deterministic compiler rejects it today; the rejection is recorded with the clauses it could not parse | **a gap closed** — promote the spec to `expect: pass` in this file so a later build cannot lose it again |

**`blocked` applies to the headless run only.** A session has an agent
interpreting, so there is no such thing as a shape it cannot take: every spec
must produce a test, and one that does not is the agent failing at its job,
not an engine gap to record. `--table` therefore holds every spec to `pass`
(except B5, which must still fail).

`blocked` is not a pass and is not scored as one. It is carried so the
benchmark is not permanently REGRESSED on shapes that have never worked —
a gate that is always red is a gate nobody reads. The blocked specs are
printed in full, with the engine's own rejection text and every rewrite it
suggested, because that list is the actionable output of the run.

**The blocked shapes are measured with `NOODLE_MODEL` unset**, i.e. against
the deterministic compiler alone. Every rejection it emits ends with *"set
`NOODLE_MODEL` to allow one bounded interpretation call"*, so a build with a
model configured may well take shapes this one refuses. The run records which
interpretation path it measured (`interpretation:` in the table header) —
"this build cannot parse a paragraph" and "this build was not given a model"
are different findings and must not be confused for one another.

## What it has found

The point of the benchmark is to name weaknesses, so they are recorded here
as it finds them — each one measured, not inferred from reading the code.

**Round 1 (1.0.0a46).** Three of five specs blocked. The label said "prompt
step(s) not understood", and the earlier reading of that — "clause splitting
is line-based, so a hard-wrapped paragraph breaks" — was **wrong**: the same
paragraph on one line blocks identically. Splitting each spec into the clauses
the compiler produced showed one cause common to all three: **the grammar had
no login verb.** B3 refused on exactly its `log in` clause while `search movies
for The Shining` and `verify The Shining is shown`, in the same sentence, both
parsed; spelled out as enter/enter/click, the identical sentence compiled.

Fixed in a46 — `log in as <user> with password <pass>` is now a verb, and
three smaller gaps it uncovered went with it:

| gap | what it did |
|---|---|
| no login verb | the one action every gated app needs was outside the grammar |
| a login clause swallowed its URL | `On <url> log in as …` refused with "no URL in the prompt" — for a prompt that contained one |
| `search <box> for <term>` | `search movies for The Shining` hunted for a search box named "movies for The Shining" |
| a gated search never reached its box | the probe runs the do-chain *after* the search ([NOOD_0168](../CHANGELOG.md)) — right for "search then act", backwards behind a login gate, where that chain is how you reach the box |

**Round 2 — the first agent-driven session.** All five specs produced a test
and landed in one report: 4 passed, 1 failed as intended, **0 blocked**. Two
more defects fell out of driving it the way a user does:

| gap | what it did |
|---|---|
| the authoring probe never passed its workspace | `{env:}` in a goal's actions resolved against the **current directory**, so a credentialed login goal refused with "set in the workspace env files first" — naming files it had never looked at. Unreachable from a CLI standing in the workspace, and unavoidable for every agent, which always passes `workspace=` |
| credentials had nowhere to be *before* authoring | `secret_values` on the author call is too late — the probe needs `{env:}` to resolve to get past the login gate, and it runs inside the transaction that would have written them, which then rolls back on the block it caused. `--session` seeds `secrets.env` at setup |

**Round 3 — read the generated file, not just the table.** B2's `.feature`
had `User enters "Popcorn1!" in the "password" field` sitting in it: a
credential typed into a prompt was landing in a **committed** test. The engine
has had the right mechanism all along (`secret_values` → gitignored
`<app>_secrets.env` → `{env:KEY}`) and the goal path used it, but the prompt
path — the one a pasted spec goes through — could not reach it. Now the
compiler lifts credential values out before anything is written, so the test
carries `{env:PASSWORD}` and the literal exists in exactly one gitignored
file. The lift is two-tiered on purpose: a `password` field is a secret
anywhere, an `email` field only when a secret field appears in the same flow —
otherwise a checkout form's customer email would be pushed into a secrets file
and the test would ask for a value nobody set.

The benchmark was leaking one too: a blocked spec is reported with the clauses
the compiler rejected, quoted verbatim, so the password inside a spec rode
into `benchmark_results.json`, both verdict files and the served
`verdict.html`. Every artifact is now masked at the write.

**Still open in the headless run** — named defects now, not "not understood":

- **The probe will not type a login.** `enter` values are never typed unless
  `probe: {perform: true}` — deliberately, because performing writes state on
  the app. A login is data entry that is also the only way to *see* the page,
  so a `search` behind one has no evidence to resolve against. In a session
  the agent opts in and it is a non-issue; headless, it is why B3 stops. This
  is a policy boundary the framework drew on purpose, not a missing pattern.
- **A hard-wrapped paragraph splits its credentials across lines** (B1), so
  the login clause loses its password. Now a *coached* refusal.
- **A subordinate clause blocks the verb match** — `Once the catalogue loads,
  search it for Alien` (B1).
- **Credentials in a header line** (`user: a / b`) are not a step, so a bare
  `log in` has nothing to borrow (B4). Now coached with the exact rewrite.
- **An anaphor two steps back** — `it should be there` (B4).

## Where this sits next to `noodle benchmark --gate`

Two benchmarks, deliberately:

| | `noodle benchmark --gate` | `noodle benchmark` |
|---|---|---|
| Question | can the engine still generate a good test | can it still take a request phrased the way people phrase them |
| Varies | the **flow** (5 flows, one shape) | the **shape** (5 shapes, one app) |
| Target | Wikipedia + a static fixture | BusterBlock, behind its login gate |
| Role | **required** before any engine-code PR | on demand, and before a release |
| Cost | ~90s | ~2-3 min |

The gate holds the phrasing still so the flow can be measured. This holds the
app still so the phrasing can be. A build can pass one and fail the other,
and which one it fails tells you something different each time.
