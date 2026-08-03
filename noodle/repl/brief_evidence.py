"""NOOD_0228 (E) — the brief reaches the engine even when the Gherkin is
hand-authored.

The structural gap the audit exposed: evidence derivation from natural
language lived only in goal mode, and `author_test` treats `goal` as mutually
exclusive with `feature_content`. So the instant an agent hand-authored, the
user's words *"take screenshot for evidence"* had **no legal channel into the
engine** — and the step-text marker was the only door left. The agent did not
go around the design; it found the only door the design left open.

`brief` is that channel, and it is deliberately COMPATIBLE with
feature_content. Given the user's verbatim ask plus the compiled Gherkin, this
module answers one question — which scenario steps the user wanted pictured —
and answers it as `@evidence:steps=` / `@evidence:skip=` tag metadata.

Matching is by content, not by line number. A brief line routinely compiles to
several steps, so counting lines would attach the picture to the wrong one and
do it silently. Each evidence-bearing brief unit is scored against every step
of the scenario (quoted literals weigh heaviest, then shared content words);
positional fallback applies only when the two counts agree, which is the one
case where position is evidence rather than coincidence. A request that
matches nothing is REPORTED, never dropped — an unplaceable request is the
caller's decision to make, not ours to discard.
"""
from __future__ import annotations

import re

_SCENARIO_RE = re.compile(r"^\s*(Scenario(?: Outline)?|Example):\s*(.*)$",
                          re.IGNORECASE)
_BLOCK_RE = re.compile(r"^\s*(Feature|Background|Rule|Examples|Scenarios):",
                       re.IGNORECASE)
_STEP_RE = re.compile(r"^(\s*)(Given|When|Then|And|But|\*)\s+(\S.*?)\s*$",
                      re.IGNORECASE)
_TAG_RE = re.compile(r"^\s*@")
_UNIT_RE = re.compile(r"^\s*(?:\d+[.)]\s*|[-*•]\s+)?(.*\S)\s*$")
_QUOTED_RE = re.compile(r"['\"“”‘’]([^'\"“”‘’]{2,})['\"“”‘’]")
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "is",
    "are", "be", "that", "this", "it", "its", "with", "as", "by", "from",
    "then", "when", "given", "user", "users", "should", "must", "can", "will",
    "page", "step", "steps", "i", "we", "my", "our", "verify", "check",
    "ensure", "confirm", "assert", "see", "sees", "seen", "shown", "shows",
    "display", "displays", "displayed", "present", "there", "click", "clicks",
    "clicked", "go", "goes", "navigate", "navigates", "open", "opens",
}
_MATCH_FLOOR = 0.34


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower())
            if w not in _STOP and len(w) > 1}


def scenarios(feature_content: str) -> list[dict]:
    """[{name, header_line, tag_line, steps: [(line_no, text)]}] — the
    scenarios of a feature with their own steps only. Background steps are
    excluded on purpose: `@evidence:steps=` is 1-based over the SCENARIO's
    steps, which is exactly what hooks.after_step indexes."""
    out: list[dict] = []
    cur: dict | None = None
    lines = (feature_content or "").splitlines()
    for i, line in enumerate(lines):
        m = _SCENARIO_RE.match(line)
        if m:
            tag_at = i - 1 if i and _TAG_RE.match(lines[i - 1]) else None
            cur = {"name": m.group(2).strip(), "header_line": i,
                   "tag_line": tag_at, "steps": []}
            out.append(cur)
            continue
        if _BLOCK_RE.match(line):
            cur = None
            continue
        sm = _STEP_RE.match(line)
        if cur and sm:
            cur["steps"].append((i, sm.group(3)))
    return out


def brief_units(brief: str) -> list[str]:
    """The brief split into the units a tester writes: numbered steps, bullets
    or plain lines. Blank lines and headings drop out."""
    units = []
    for raw in (brief or "").splitlines():
        m = _UNIT_RE.match(raw)
        if not m:
            continue
        text = m.group(1)
        if text and not text.startswith("#"):
            units.append(text)
    return units


def _requests(units: list[str]) -> list[dict]:
    """Per unit: does it ask for a picture, refuse one, or say nothing?
    The negative is tested first — "no screenshot needed" contains the word
    the positive looks for (NOOD_0225)."""
    from noodle.repl.prompt_expander import _EVIDENCE, _EVIDENCE_NEG
    out = []
    for idx, unit in enumerate(units):
        neg = _EVIDENCE_NEG.search(unit)
        pos = None if neg else _EVIDENCE.search(unit)
        if not (neg or pos):
            continue
        span = neg or pos
        body = (unit[:span.start()] + unit[span.end():]).strip(" -–—,;.")
        out.append({"unit_index": idx, "unit": unit, "body": body,
                    "kind": "skip" if neg else "want"})
    return out


def _score(body: str, step_text: str) -> float:
    """How strongly a brief unit points at one step. A literal the user quoted
    and the step asserts is near-proof; otherwise shared content words."""
    step_l = step_text.lower()
    body_l = (body or "").lower()
    for lit in _QUOTED_RE.findall(body):
        if lit.lower() in step_l:
            return 1.0
    # ...and the other direction. A compiled step quotes the literal it
    # asserts ('the user sees "Pizza"'); a brief names it in prose ("verify
    # the pizza banner is present"). Scoring only brief→step made the common
    # case fall through to positional guessing.
    for lit in _QUOTED_RE.findall(step_text):
        if lit.lower() in body_l:
            return 0.95
    bt = _tokens(body)
    if not bt:
        return 0.0
    return len(bt & _tokens(step_text)) / len(bt)


def derive(brief: str, feature_content: str,
           explicit: list[int] | None = None,
           explicit_skip: list[int] | None = None) -> dict:
    """{mode, scenarios: [{name, want, skip}], placed, unplaced, requested}.

    `mode` is the run-wide directive the brief carries ('all' / 'assertions' /
    'off'), which becomes a scenario tag rather than a per-step position.
    `unplaced` lists evidence requests no step matched — the caller blocks on
    those instead of shipping a test that quietly proves less than was asked.
    """
    from noodle.repl.prompt_expander import evidence_intent
    scs = scenarios(feature_content)
    reqs = _requests(brief_units(brief))
    mode = evidence_intent(brief or "")
    per_scenario = [{"name": s["name"], "want": set(), "skip": set()}
                    for s in scs]
    for pos in explicit or ():
        for entry in per_scenario:
            entry["want"].add(int(pos))
    for pos in explicit_skip or ():
        for entry in per_scenario:
            entry["skip"].add(int(pos))

    placed, unplaced = [], []
    for req in reqs:
        best = None
        for si, sc in enumerate(scs):
            for pos, (_, text) in enumerate(sc["steps"], start=1):
                score = _score(req["body"], text)
                if score >= _MATCH_FLOOR and (best is None or score > best[2]):
                    best = (si, pos, score, text)
        if best is None and len(scs) == 1 and \
                len(brief_units(brief)) == len(scs[0]["steps"]):
            # Counts agree — position is evidence, not coincidence.
            pos = req["unit_index"] + 1
            best = (0, pos, 0.0, scs[0]["steps"][pos - 1][1])
        if best is None:
            unplaced.append({"request": req["unit"], "kind": req["kind"],
                             "reason": "no step in the feature matches this "
                                       "line closely enough to attach it to"})
            continue
        si, pos, score, text = best
        per_scenario[si][req["kind"]].add(pos)
        placed.append({"request": req["unit"], "kind": req["kind"],
                       "scenario": scs[si]["name"], "step": pos,
                       "step_text": text, "confidence": round(score, 2)})
    return {"mode": mode, "scenarios": per_scenario, "placed": placed,
            "unplaced": unplaced, "requested": len(reqs)}


def _tag_names(want: set[int], skip: set[int], mode: str | None) -> list[str]:
    tags = []
    if mode == "off":
        tags.append("@no_evidence")
    elif mode == "all":
        tags.append("@evidence")
    elif mode == "assertions":
        tags.append("@evidence:assertions")
    if want:
        tags.append("@evidence:steps=" + ",".join(str(n) for n in sorted(want)))
    if skip:
        tags.append("@evidence:skip=" + ",".join(str(n) for n in sorted(skip)))
    return tags


def apply_tags(feature_content: str, derived: dict) -> str:
    """Write the derived directives onto each scenario as tags. Idempotent:
    a scenario that already carries the same tag is left alone, so a re-author
    with the same brief produces byte-identical Gherkin."""
    scs = scenarios(feature_content)
    lines = feature_content.splitlines()
    mode = derived.get("mode")
    # Bottom-up: inserting a tag line shifts every header below it.
    for sc, entry in reversed(list(zip(scs, derived.get("scenarios", [])))):
        tags = _tag_names(entry["want"], entry["skip"], mode)
        if not tags:
            continue
        head = sc["header_line"]
        indent = lines[head][:len(lines[head]) - len(lines[head].lstrip())]
        if sc["tag_line"] is None:
            lines.insert(head, indent + " ".join(tags))
            continue
        have = lines[sc["tag_line"]].split()
        keep = [t for t in have
                if not re.fullmatch(r"@(?:no_)?evidence(?::\S+)?", t)]
        lines[sc["tag_line"]] = indent + " ".join(keep + tags)
    out = "\n".join(lines)
    return out + "\n" if feature_content.endswith("\n") else out


def mentions_evidence(brief: str) -> bool:
    """Does this brief ask for a picture at all? Used for the warn-and-block:
    a brief that says 'screenshot' and produced no tag is the exact hole the
    step-text marker used to fill."""
    from noodle.repl.prompt_expander import _EVIDENCE, _EVIDENCE_NEG
    return any(_EVIDENCE_NEG.search(u) or _EVIDENCE.search(u)
               for u in brief_units(brief)) or \
        evidence_mode(brief) is not None


def evidence_mode(brief: str) -> str | None:
    from noodle.repl.prompt_expander import evidence_intent
    return evidence_intent(brief or "")
