# NOOD_0131 — agent-pipeline benchmark

The pinned baseline for the faster/lower-cost agent pipeline work, the
replay protocol that reproduces it, and the deterministic counters each
phase's regressions read. Live AIC replay is a **reported benchmark, not a
CI gate** — absolute AIC is not portable across host models; the CI gates
are the model-independent work-shape targets below.

## Baseline (nood_0130, preproduction login flow, GPT-5.3-Codex)

| Metric | Baseline |
|---|---:|
| Reported AIC | 29.3 |
| Locally recorded AIC | 28.328 |
| Host model calls | 24 |
| Input tokens | 835,940 |
| Output tokens | 6,809 |
| Input:output ratio | 122.8:1 |
| Model time | 70.6 s |
| Context per call | 21,190 → 42,387 tokens |
| Probe browser launches | 5 |
| Full test browser launches | 2 |
| Full test runtime | 85 s |
| Engine-side model calls | 0 |

The AIC discrepancy (29.3 vs 28.328) is an accounting-boundary difference:
the reported 29.3 is the benchmark headline; the local counters stay for
diagnosis.

## Targets

| Metric | Green path | One-repair path |
|---|---:|---:|
| Host-visible pipeline operations | ≤ 3 | ≤ 5 |
| Browser launches | ≤ 2 | ≤ 3 |
| Engine-side model calls | 0 | 0 |
| Separate validation after ready author result | 0 | 0 |
| Separate RCA/report/serve calls | 0 | 0 |
| Compact probe payload | < 4 KB | < 4 KB each |
| Final machine payload | < 2 KB | < 3 KB |
| Same-host GPT-5.3-Codex AIC | ≤ 15 | ≤ 20 |
| Green-path wall time | ≥ 50% below baseline | ≥ 30% below baseline |

## Replay fixture

`test-apps/replay-spa/index.html` — one self-contained SPA page matching
the baseline shape: hidden `.trigger-dev-panel` hitbox → Development Panel
(Asset Tag input + **custom** Device Type combobox) → Save
Configuration transition → username/password login (`demo` / `demo123`,
gated on the config being saved) → post-login `Branch #12 — Dashboard`
heading + `Search inventory` control. No server: probe and run it via
`file://<repo>/test-apps/replay-spa/index.html`.

### Replay protocol

1. Fresh workspace (`noodle init`), MCP or CLI, host model of record.
2. Prompt from PROMPT_TEMPLATE.md: goal = configure the device via the
   hidden dev panel, sign in, verify the dashboard heading and the search
   control; credentials `RSPA_USER=demo RSPA_PASS=demo123`.
3. Record per run: host model calls + AIC, input/output tokens, tool calls
   and payload bytes, browser launches, wall time per phase (probe /
   author / execute / report), engine-side model calls, repair loops and
   their cause.
4. Green path is probe → author → execute+report: 3 host-visible pipeline
   operations, ≤ 2 browser launches (1 probe + 1 run).

## Deterministic counters (`noodle/counters.py`)

Unit-test observability for the work shape — bumped in-process, reset per
test, asserted by `unit_tests/test_nood_0131.py`:

| Counter | Bumped by |
|---|---|
| `browser_launch` | `probe.probe()` (one browser per call) and every `run` engine invocation from `repl/core` |
| `target_resolution` | `core.resolve_target()` |
| `result_scan` | `reporting/summary.collect()` |
| `report_generation` | `reporting/builder.generate()` |
| `freshness_check` | `reporting/builder.ensure_fresh_reports()` |

## Per-phase results (implemented 2026-07-18, `feature/nood_0131`)

Model-independent gates, measured; enforced by `unit_tests/test_nood_0131.py`.

| Phase | Metric moved | Before → after |
|---|---|---|
| 2 — honest authoring readiness | ready:true on an unresolved `{env:}` ref | possible (the baseline `BASE_URL` miss) → impossible; result now returns `base_url_key` |
| 3 — one probe sufficient | probe browser launches on the fixture flow | 5 → **1** (hyphen/space reveal-name parity + compact copy-ready steps + auto-opened combobox options; compact render of the fixture = 2,175 B < 4 KB) |
| 4 — three-operation path | canonical sequence on every always-on surface | conflicting author-first / validate-always / serve-separately texts → one probe→author→execute sequence, content-tested |
| 5 — execution/report dedup | per-run work shape (counters) | rebuild-always → 1 target resolution, 1 result scan, 1 freshness check, 0 extra report builds; serve reuses the verified root; `--json` = exactly one object |
| 6 — always-loaded context | instruction-surface bytes | AGENTS.md 4,514→**4,094** (≤4,096); skill cards 8,430/8,713→**5,106/5,119** (≤5,120); MCP instructions 1,997→**1,585** (≤2,048); hot-path tool descriptions 5,885→**3,745** (≤6,144); prompt 917 (≤1,024) |
| 7 — replay | AIC (reported, per model) | live replay is manual (a host model must drive) — run the protocol above on GPT-5.3-Codex plus one second model and record here; release gate ≤ 15 AIC |

## NOOD_0135 — URL preservation + probe settle (measured 2026-07-19)

The reviewed login session (69 host calls, 3.08M input tokens, 88.3 AIU,
26 browser launches, 9 runs) traced to one authoring bug — `author_test`
stored only `scheme://netloc`, so the first run opened the host root and
the failure read as locator rot — amplified by repeated browser debugging.
Fixes: full-URL preservation + readiness URL fidelity (`ready`/`validated`
now verify the resolved app URL byte-for-byte), `[navigation-mismatch]`
failure verdict classified before any locator rule, and DOM-mutation probe
settling (plus dispatch-direct clicks for known-hidden triggers).

Probe wall time on `test-apps/replay-spa` (3-run medians, M-series macOS,
http.server on loopback; CI asserts call shape only — exact numbers live
here, not in unit tests):

| Probe shape | Before | Target | After |
|---|---:|---:|---:|
| Initial page | 1.193 s | ≤ 1.5 s | **0.850 s** |
| One reveal | 4.180 s | ≤ 2.0 s | **0.999 s** |
| Reveal + options | 4.267 s | ≤ 2.5 s | **1.205 s** |

Deterministic gates (all enforced by `unit_tests/test_nood_0135.py`):
full-URL round-trip through author/CLI/generate (origin, path, query,
fragment, trailing slash), origin-only inputs unchanged, host/port package
reuse intact, overwrite recovery corrects an old origin-only file without
touching unrelated keys, `ready:false` on any resolved-URL mismatch, and
navigation-mismatch beats locator-rot in RCA. Live replay proof: a
nested-route copy of replay-spa authored + executed green in ONE run with
2 browser launches total (1 probe + 1 run); a 302-sabotaged route
classified `navigation-mismatch` (high) on the first failure. Host-model
AIU on the pinned benchmark (≤ 15 green / ≤ 20 one-repair) remains a
manual replay per the protocol above.
