"""The payload budget (NOOD_0164) — one byte bound for every agent-facing
return value, enforced at the boundary rather than tool by tool.

Three sessions in a row (NOOD_0161, NOOD_0162, and the review that opened
this ticket) died the same death: a tool handed back a payload bigger than
the calling harness would inline, the harness spilled it to a temp file, and
the agent burned inferences on `jq`/`grep` to read back what the tool already
knew. Each was fixed where it was found — `probe --json`, `noodle --help`,
`list_tests`, `probe-app`. The next door then leaked. Measured when this
module landed, with the per-tool fixes already in:

    list_tests (this repo)                     15,845 B
    read_docs('manual', section='Setup guide') 25,468 B   (15 sections > 8 KB)
    probe_page compact                         up to 24,000 B (its own budget)

So the fix is not another per-tool cap: it is a bound every tool passes
through, and a guard that fails when a new tool skips it.

`BUDGET_BYTES` is 8 KB — about 2k tokens, comfortably inside what MCP hosts
inline. It is deliberately a knob (`NOODLE_PAYLOAD_BUDGET_BYTES`): the real
threshold belongs to whichever harness is driving, and that is not something
this repo can measure for every host.

Trimming is honest and last-resort. A tool that knows its own content should
shrink it *well* first — probe's cap ladder sheds junk-ranked lists before
author-critical keys, `list_tests` returns an index and takes a query — and
`bound()` only catches what still overflows: it cuts the largest list or
string by what the payload is over, never invents, and always says what it
took and where the rest lives.
"""
from __future__ import annotations

import json
import os

DEFAULT_BUDGET_BYTES = 8_000


def budget_bytes() -> int:
    """The live budget — read per call so a host can raise it for a session
    without a reinstall (`NOODLE_PAYLOAD_BUDGET_BYTES=16000`)."""
    raw = os.environ.get("NOODLE_PAYLOAD_BUDGET_BYTES")
    if raw and raw.strip().isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_BUDGET_BYTES


def size(payload, indent: int | None = None) -> int:
    """Serialized bytes — what the harness actually has to carry. `indent`
    must match how the payload will be RENDERED (NOOD_0165): the CLI prints
    `--json` at indent=2, which inflates a 7,556 B payload to 10,240 B, and a
    budget measured on the compact form never saw those 2.7 KB."""
    return len(json.dumps(payload, default=str, indent=indent))


def bound(payload, budget: int | None = None, hint: str = "",
          indent: int | None = None) -> dict:
    """`payload` trimmed to fit `budget` serialized bytes, with a
    `payload_note` naming what was cut and `hint` saying where the rest is.

    Only dicts are trimmed (every tool returns one); anything else passes
    through untouched. The largest list/string value is cut each pass, so a
    payload dominated by one runaway list loses only that list — the small
    keys an author actually reads (`ready`, `blocking`, `author_ready`,
    verdicts, paths, URLs) are never the largest value and survive whole."""
    budget = budget_bytes() if budget is None else budget
    if not isinstance(payload, dict) or size(payload, indent) <= budget:
        return payload

    # Trim to leave room for the note itself — otherwise the explanation of
    # the trim is what puts the payload back over the line.
    out, trimmed, room = dict(payload), [], max(1, budget - 400)
    while size(out, indent) > room:
        shrinkable = {k: v for k, v in out.items()
                      if (isinstance(v, list) and len(v) > 1)
                      or (isinstance(v, str) and len(v) > 200)}
        if not shrinkable:
            break
        key = max(shrinkable, key=lambda k: size(out[k], indent))
        # Cut what the payload is actually over by, priced at this value's own
        # bytes-per-element — halving would throw away a 60-feature index down
        # to 15, and a fixed ratio collapses a 200 KB string to nothing.
        # The -1 guarantees progress: a lap that cuts nothing loops forever,
        # and every lap re-serializes the whole payload.
        value = out[key]
        per = max(1.0, size(value, indent) / len(value))
        keep = len(value) - int((size(out, indent) - room) / per) - 1
        out[key] = value[:max(1, min(keep, len(value) - 1))]
        if key not in trimmed:
            trimmed.append(key)

    if not trimmed:
        # Nothing left to halve and still over: hand it back whole rather than
        # drop a key the caller may need, and say so.
        out["payload_note"] = (
            f"over the {budget // 1000} KB payload budget ({size(out, indent)} B) and "
            f"nothing is trimmable — every value is already small. {hint}".strip())
        return out
    out["payload_note"] = (
        f"trimmed {', '.join(trimmed)} to fit the {budget // 1000} KB payload "
        f"budget — the harness spills anything larger to a temp file. "
        f"{hint}".strip())
    return out
