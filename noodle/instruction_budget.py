"""The instruction budget ledger (NOOD_0159) — one place for every byte
ceiling on an always-on instruction surface.

An always-on surface is text an agent pays for on EVERY model call (or every
session): the scaffolded AGENTS.md, the skill cards, the MCP connect-time
instructions, the hot tool docstrings. Their ceilings used to live as asserts
scattered across seven per-ticket test files, with duplicate pins at
different values; unit_tests/test_nood_0159.py now enforces this ledger and
nothing else does.

The rule the ledger serves (docs/llm-performance.md §8): **surfaces route,
docs carry.** A surface earns bytes only for the workflow contract, triggers,
and pointers; the substance lands as a docs/ section, retrievable through
read_docs (index → query → section). New guidance therefore costs ~0 surface
bytes — a doc section is free, a pointer line is the most a surface should
pay. Prior art: GoogleChrome/modern-web-guidance ships a ~230-token always-on
card whose only content is "search first, retrieve on demand".

Bytes are the ONLY unit here. Line-count ceilings (70/96/120/165 in the old
tests) were retired by NOOD_0159 — tokens track bytes, not lines, and two
units meant two fights per edit.

Raising a cap is allowed but never silent: edit it here, in a branch whose
CHANGELOG entry states the before/after bytes and why the content cannot be a
doc section instead (the §7 acceptance rule).

Ceiling accounting (moved verbatim from the retired test asserts):
- _AGENTS_MD 5632: NOOD_0128 diet baseline; +384 NOOD_0147 (one always-on
  session-diagnostics rule — detection itself is engine-side); +256 each
  NOOD_0155 (engine/workspace/wok routing nouns; definitions live in
  docs/glossary.md + docs/woks.md); +896 NOOD_0156 (false-positive gates —
  STOP on blocked authoring, full-flow probes, never-invent-assertion-text,
  green = failed==0 AND verified:true).
- _INSTRUCTIONS 2432: +384 NOOD_0156 — the STOP rule and the verified-run
  success contract must reach every MCP host at connect time; the rest stays
  in AGENTS.md / the playbook. log_diagnostic is deliberately NOT here
  (NOOD_0147) — the run result's diagnostic_due nudge carries it.
- skill cards 5376 → 5888 (NOOD_0161): one card, two hosts (.claude +
  .copilot) — the shingle guard in test_nood_0131 exempts exactly this pair
  from anti-duplication. +266 bytes buy the minimal valid goal OBJECT inline
  (goal.EXAMPLE); it cannot be a doc section, because the round trip to read
  that section IS the cost being removed — the reviewed session spent 25
  inferences rediscovering the shape. The remaining +246 restores headroom on
  the copilot card, which sat at 6 bytes (the NOOD_0160 floor lesson).
- copilot digest 7168: first pinned by NOOD_0159 (it was the one always-on
  surface with no cap; 7058 bytes at pin time).
- NOOD_0191: claude skill card 5888 → 6144, copilot 6144 → 6304 (+256/+160;
  6037 and 6200 B used at the bump). A developer added the skill to their
  work repo, asked for API tests, and the agent REFUSED — "Noodle is a web UI
  testing framework, use pytest or Postman". Flatly wrong: Noodle is a
  UNIVERSAL BDD framework and has shipped a browserless REST capability
  (verbs, oauth2, status/body/header asserts, JSON extraction, five sample
  features) since NOOD_0007 — promoted to its own `api` wok here. But the
  agent reasoned correctly from what the card told it — the card opened
  "Playwright+behave BDD", its `description` listed POM files and --headed,
  and `@api` appeared exactly once inside a ten-tag list. What the +bytes buy
  is the scope correction in the `description` (the string that decides
  whether the skill applies AT ALL) plus one line naming the API wok and
  saying "never refuse a non-web ask".
  This is the clearest case yet of the §7 exception, and it is stronger than
  NOOD_0179's flag-existence argument: an agent has to know a capability
  exists before it will spend a call looking it up, and this one never made a
  second call — it declined the whole request and recommended a competitor.
  A doc section cannot fix a surface that talks the reader out of reading.
  First attempt paid for it by deleting the fastest-path-first probe sentence
  instead; test_nood_0101 and test_nood_0136 correctly refused that — those
  are the narrow-exemption triggers from the 131-AIC session, not spare
  bytes. Cutting a hard-won rule to fund a different one is not a saving.
  _AGENTS_MD took the same scope line inside its cap, and the NOOD_0160
  headroom FLOOR did its job — it refused the spend and forced three real
  moves instead: `after:` check anchoring and `inspect_locator` to the
  playbook (both already there), and the `--spec -` heredoc tip to
  playbook §2 (a move, so it was ADDED there first). Folding the scope
  sentence into the existing Nouns paragraph paid the rest. 5067 B used,
  565 headroom — above the floor it started at.
- hot docstrings 6144: probe_page + author_test + run_and_report + run_test,
  summed — the four schemas every MCP session carries.
- NOOD_0160: −1039 bytes off _AGENTS_MD (5628 → 4589) — the first
  router-rule content move: probe flag catalog, author spec keys,
  result-pick binding, evidence-screenshot marker, and the failure
  taxonomy now live only in the playbook (they already did — the surface
  text was duplication); AGENTS.md keeps the contract, triggers, and
  pointers, plus the new `noodle docs` CLI route to the docs.
  test_nood_0160 pins a 512-byte headroom FLOOR on _AGENTS_MD so the
  file can never silently return to zero.
- NOOD_0162: `noodle probe --help` 6144 and `noodle run --help` 5120 join the
  ledger — an agent pulls a whole help screen in one call, and these two were
  12.5 KB and 8.4 KB of option help carrying their NOOD_XXXX rationale. The
  rationale moved to docs/cli-reference.md (a move, not a delete) and each
  option help keeps the one line saying what the flag does; probe fell to
  ~6.0 KB, run to ~4.5 KB. Note that ~4 KB of each is rich's frame — box
  characters and lines padded to the terminal width — so the caps are near
  the floor for a command with this many flags, and the next real cut is a
  flag leaving, not a sentence.
- NOOD_0179: hot docstrings 6144 → 6400 and `noodle probe --help` 6144 → 6400
  (+256 each). Both sat at 3 and 62 bytes of headroom, so the `brief` flag
  could not be named on either surface without one. A doc section cannot
  carry it: the §7 rule is that substance moves to docs, but a PARAMETER'S
  EXISTENCE is routing — an agent that never learns `brief` is a flag cannot
  read the doc that explains it. The rationale (what brief keeps, what it
  drops, when to use it) did move: docs/llm-performance.md §4. What each
  surface buys is one clause naming the flag.
- NOOD_0187: `noodle run --help` 5120 → 5504 (+384). Three flags joined run
  — --shard i/N, --timeout, --fail-fast (the scale batch: split across
  machines, wall-clock budget, stop-on-first-red). Same NOOD_0179 rule: a
  flag's existence is routing, and each carries a one-line help; the
  rationale lives in docs/cli-reference.md. 5321 B used at the bump.
- NOOD_0190: copilot skill card 5888 → 6144 and copilot digest 7168 → 7424
  (+256 each; the Claude card absorbed the same line inside its cap). All
  three learn that `noodle feature-regression` RUNS the benchmark in one
  call. Same NOOD_0179 rule at command scale: a command an agent never
  learns exists is a command it improvises around, and that improvisation
  — reading docs, guessing flags, hand-writing results.json, digging
  through session telemetry for a cost number — was measured at ~28 AIC
  per benchmark run, against an engine that spent none. 189 B buys it back.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

CEILINGS: dict[str, int] = {
    "agents-md (cli._AGENTS_MD)": 5632,
    "prompt-template (cli._PROMPT_TEMPLATE)": 1024,
    "mcp-instructions (server._INSTRUCTIONS)": 2432,
    "claude-skill-card (.claude/skills/noodle/SKILL.md)": 6144,
    "copilot-skill-card (.copilot/skills/noodle/SKILL.md)": 6304,
    "copilot-digest (.github/copilot-instructions.md)": 7424,
    "hot-tool-docstrings (probe/author/run_and_report/run)": 6400,
    "cli-help (noodle probe --help)": 6400,
    "cli-help (noodle run --help)": 5504,
}


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _cli_help(command: str) -> bytes:
    """Rendered `noodle <command> --help` at a pinned 80 columns, colour
    stripped — an agent pulls this in one call, so it is an always-on surface
    too (NOOD_0162). Width is pinned because rich pads every line to the
    terminal width; NOOD_0163 strips the ANSI escapes because GitHub Actions
    renders help in colour and a laptop doesn't — 396 escapes, ~2 KB, enough
    to fail the ceiling in CI and pass it locally. Colour is paint, not
    content: what the agent reads is the same either way."""
    import os

    from typer.testing import CliRunner

    from noodle import cli
    prev = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = "80"
    try:
        out = CliRunner().invoke(cli.app, [command, "--help"]).output
    finally:
        if prev is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = prev
    return _ANSI_RE.sub("", out).encode()


def _sources() -> dict[str, bytes | None]:
    """Current bytes of each surface; None for repo files a wheel install
    doesn't ship (skill cards, digest)."""
    from noodle import cli
    from noodle.mcp import server

    def _file(rel: str) -> bytes | None:
        f = REPO / rel
        return f.read_bytes() if f.is_file() else None

    hot = (server.probe_page, server.author_test, server.run_and_report,
           server.run_test)
    return {
        "cli-help (noodle probe --help)": _cli_help("probe"),
        "cli-help (noodle run --help)": _cli_help("run"),
        "agents-md (cli._AGENTS_MD)": cli._AGENTS_MD.encode(),
        "prompt-template (cli._PROMPT_TEMPLATE)": cli._PROMPT_TEMPLATE.encode(),
        "mcp-instructions (server._INSTRUCTIONS)": server._INSTRUCTIONS.encode(),
        "claude-skill-card (.claude/skills/noodle/SKILL.md)":
            _file(".claude/skills/noodle/SKILL.md"),
        "copilot-skill-card (.copilot/skills/noodle/SKILL.md)":
            _file(".copilot/skills/noodle/SKILL.md"),
        "copilot-digest (.github/copilot-instructions.md)":
            _file(".github/copilot-instructions.md"),
        "hot-tool-docstrings (probe/author/run_and_report/run)":
            b"".join((f.__doc__ or "").encode() for f in hot),
    }


def ledger() -> list[dict]:
    """One row per surface: used/cap/headroom bytes (used=None if the file
    isn't present in this install)."""
    rows = []
    src = _sources()
    for name, cap in CEILINGS.items():
        data = src[name]
        used = None if data is None else len(data)
        rows.append({"surface": name, "used": used, "cap": cap,
                     "headroom": None if used is None else cap - used})
    return rows


def format_ledger() -> str:
    """The ledger as an aligned table — this is what a failing ceiling test
    prints, so the editor sees where the bytes went without archaeology."""
    rows = ledger()
    w = max(len(r["surface"]) for r in rows)
    lines = [f"{'surface':<{w}}  {'used':>6} {'cap':>6} {'headroom':>8}"]
    for r in rows:
        u = "-" if r["used"] is None else r["used"]
        h = "-" if r["headroom"] is None else r["headroom"]
        lines.append(f"{r['surface']:<{w}}  {u:>6} {r['cap']:>6} {h:>8}")
    return "\n".join(lines)
