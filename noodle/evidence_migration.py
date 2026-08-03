"""NOOD_0228 (A) — `noodle migrate evidence-markers`: the upgrade path for a
workspace whose features still carry an evidence request inside step text.

Rejecting the marker at parse time is a breaking change for existing
hand-authored features, and a breaking change without a migration is just
breakage. A grace period was the alternative and it is the exact failure mode
this ticket exists to end: an accepted, rewarded, still-taught affordance is
not deprecated, whatever the release notes say.

The conversion is mechanical and lossless. A step whose text ends in an
evidence request becomes a plain step, and its 1-based position within its own
scenario joins that scenario's `@evidence:steps=` tag (or `@evidence:skip=`
for the negative form). Positions are computable because the marker is
per-step by definition — the information does not move, only the channel it
rides on.

Rewrites go through workspace_policy.record so a migrated file stays
engine-owned: a migration that turned every feature into drift would trade one
unregenerable-file problem for another.
"""
from __future__ import annotations

import re
from pathlib import Path

_STEP_RE = re.compile(
    r"^(\s*)(Given|When|Then|And|But|\*)\s+(.*?)\s*$", re.IGNORECASE)
_SCENARIO_RE = re.compile(r"^\s*(Scenario(?: Outline)?|Example):", re.IGNORECASE)
_BLOCK_RE = re.compile(
    r"^\s*(Feature|Background|Rule|Examples|Scenarios):", re.IGNORECASE)
_TAG_RE = re.compile(r"^\s*@")


def _tag_line(indent: str, existing: list[str], want: set[int],
              skip: set[int]) -> str:
    """Merge the derived positions into whatever @evidence:* tags the
    scenario already carried, so a partially-migrated file converges."""
    keep, have_want, have_skip = [], set(), set()
    for t in existing:
        m = re.fullmatch(r"@evidence:(steps|skip)=([\d,]+)", t)
        if not m:
            keep.append(t)
            continue
        nums = {int(n) for n in m.group(2).split(",") if n}
        (have_want if m.group(1) == "steps" else have_skip).update(nums)
    want |= have_want
    skip |= have_skip
    if want:
        keep.append("@evidence:steps=" + ",".join(str(n) for n in sorted(want)))
    if skip:
        keep.append("@evidence:skip=" + ",".join(str(n) for n in sorted(skip)))
    return indent + " ".join(keep)


def convert(text: str) -> tuple[str, list[dict]]:
    """(rewritten feature text, [{scenario, tag, steps}]). Pure — the unit
    tests exercise it without a workspace."""
    from noodle.resolver.patterns import (
        EVIDENCE_MARKER_RE,
        EVIDENCE_SUPPRESS_RE,
    )
    lines = text.splitlines()
    # Pass 1: per scenario, strip the request off each step and remember its
    # 1-based position. Tag lines are patched in pass 2 (they precede the
    # scenario, so the positions are not known when they are read).
    scenarios: list[dict] = []
    cur: dict | None = None
    pos = 0
    for i, line in enumerate(lines):
        if _SCENARIO_RE.match(line):
            cur = {"header": i, "name": line.split(":", 1)[1].strip(),
                   "want": set(), "skip": set(), "tag_lines": []}
            scenarios.append(cur)
            pos = 0
            continue
        if _BLOCK_RE.match(line):
            cur = None
            continue
        m = _STEP_RE.match(line)
        if not (cur and m):
            continue
        indent, keyword, body = m.groups()
        pos += 1
        neg = EVIDENCE_SUPPRESS_RE.search(body)
        if neg:
            cur["skip"].add(pos)
            body = body[:neg.start()].rstrip()
        else:
            hit = EVIDENCE_MARKER_RE.search(body)
            if not hit:
                continue
            cur["want"].add(pos)
            body = body[:hit.start()].rstrip()
        lines[i] = f"{indent}{keyword} {body}"

    # Pass 2: write the tag line for each scenario that gained positions.
    changed: list[dict] = []
    for sc in scenarios:
        if not (sc["want"] or sc["skip"]):
            continue
        head = sc["header"]
        indent = lines[head][:len(lines[head]) - len(lines[head].lstrip())]
        tag_at, existing = None, []
        j = head - 1
        while j >= 0 and _TAG_RE.match(lines[j]):
            existing = lines[j].split() + existing
            tag_at = j
            j -= 1
        new_line = _tag_line(indent, existing, sc["want"], sc["skip"])
        if tag_at is None:
            lines.insert(head, new_line)
            for later in scenarios:
                if later["header"] > head:
                    later["header"] += 1
        else:
            lines[tag_at] = new_line
            for k in range(tag_at + 1, head):
                lines[k] = ""
            lines[tag_at + 1:head] = [ln for ln in lines[tag_at + 1:head] if ln]
    for sc in scenarios:
        if sc["want"] or sc["skip"]:
            changed.append({
                "scenario": sc["name"],
                "tag": _tag_line("", [], set(sc["want"]), set(sc["skip"])).strip(),
                "steps": sorted(sc["want"] | sc["skip"]),
            })
    out = "\n".join(lines)
    return (out + "\n" if text.endswith("\n") and not out.endswith("\n")
            else out), changed


def migrate(workspace: str = ".", write: bool = False) -> dict:
    """Convert every .feature under tests_dir. Dry by default: a migration
    that rewrites a tester's suite before they have read what it would do is
    the same trust problem as a silent hand edit."""
    from noodle import config, workspace_policy
    root = Path(workspace) / config.load(workspace).get("tests_dir",
                                                        "noodle_tests")
    features, touched = [], []
    if not root.is_dir():
        return {"ok": True, "features": [], "written": False,
                "note": f"no {root} in this workspace"}
    for f in sorted(root.rglob("*.feature")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        new, changed = convert(text)
        if not changed:
            continue
        rel = f.relative_to(Path(workspace)).as_posix()
        features.append({"path": rel, "scenarios": changed})
        if write:
            try:
                f.write_text(new, encoding="utf-8")
                touched.append(f)
            except OSError as e:
                return {"ok": False, "error": f"cannot write {rel}: {e}",
                        "features": features}
    if touched:
        # Only re-claim files the engine ALREADY owned: claiming a
        # hand-authored feature here would silently widen strict mode's
        # scope to files the tester never asked the engine to manage.
        owned = workspace_policy.load(workspace)
        workspace_policy.record(workspace, [
            p for p in touched
            if p.relative_to(Path(workspace)).as_posix() in owned])
    return {"ok": True, "written": bool(write), "features": features,
            "note": ("evidence requests now ride as @evidence:steps= / "
                     "@evidence:skip= scenario tags")}
