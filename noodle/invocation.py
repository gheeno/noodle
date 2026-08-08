"""NOOD_0241 — invocation hygiene: did a shell COMPOSE this noodle call?

The audited session's leaks went entirely unflagged — the run went green and
the operator learned about `--help | head` and `| tee /tmp/...` only by
re-reading the transcript by hand. Undetected policy breaches recur, so the
engine now looks at how it was invoked and says so in the payload the same
way `verified: false` is said: `invocation_clean: false` plus a note naming
the composition it saw.

Best-effort by design, and honest about it: the parent process's command
line is only readable cheaply on Linux (/proc) and macOS (ps); Windows and
anything unexpected return None and stamp nothing. Detection triggers only
when the parent is a shell running `-c` — an interactive shell or a host
that exec()s noodle directly never matches. Metacharacter detection is
quote-aware where the stdlib allows (shlex punctuation tokens for | ; & < >)
and substring-based for `$(`/backticks, so a quoted prompt legitimately
containing punctuation stays clean. This stamp is INFORMATIONAL — nothing
is refused; the enforcement layer is the host policy noodle init writes
(agent_policy.py). Sightings append to <workspace>/.noodle/invocation_log
.jsonl so leaks aggregate across sessions instead of evaporating with the
transcript.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path

_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}


def parent_command() -> str | None:
    """The parent process's command line, or None where it can't be read
    cheaply and safely (Windows, sandboxes, a vanished parent)."""
    ppid = os.getppid()
    try:
        if sys.platform.startswith("linux"):
            raw = Path(f"/proc/{ppid}/cmdline").read_bytes()
            return raw.replace(b"\x00", b" ").decode(errors="replace").strip() or None
        if sys.platform == "darwin":
            out = subprocess.run(["ps", "-o", "command=", "-p", str(ppid)],
                                 capture_output=True, text=True, timeout=2)
            return out.stdout.strip() or None
    except Exception:
        return None
    return None


def _segments(tokens: list[str]) -> list[list[str]]:
    """Token runs between chain separators (&&, ||, ;, &)."""
    out = [[]]
    for t in tokens:
        if t in ("&&", "||", ";", "&"):
            out.append([])
        else:
            out[-1].append(t)
    return [s for s in out if s]


def composition(cmd: str | None) -> str | None:
    """Composition evidence AROUND the noodle invocation in a parent shell
    -c line, or None. Precision over recall, deliberately: agent harnesses
    wrap every command in their own `-c` plumbing (env sourcing, cwd
    bookkeeping) that is full of operators the agent never wrote, so
    flagging any operator anywhere would cry wolf on every clean call.
    What is flagged is only what touches the noodle command itself:
    - the noodle segment is part of a pipeline (`noodle … | head`),
    - its output/stderr is redirected (`… 2>&1`, `> file`, `| tee`),
    - a command substitution mark sits inside the segment,
    - or the whole body is exactly `cd <dir> && noodle …` — the audit's
      six-of-six cd prefix, distinguishable from harness plumbing by there
      being nothing else on the line."""
    if not cmd:
        return None
    parts = cmd.split()
    if not parts:
        return None
    head = parts[0].rsplit("/", 1)[-1].lstrip("-")
    if head not in _SHELLS or not any(p.startswith("-") and "c" in p.lstrip("-")
                                      for p in parts[1:3]):
        return None
    body = cmd.split(" -c ", 1)[-1] if " -c " in cmd else " ".join(parts[2:])
    try:
        lex = shlex.shlex(body, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:            # unbalanced quotes — best effort on words
        tokens = body.split()
    segments = _segments(tokens)
    noodle_segs = [s for s in segments
                   if s and s[0].rsplit("/", 1)[-1] == "noodle"
                   or any(t.rsplit("/", 1)[-1] == "noodle" for t in s[:2])]
    found = []
    for seg in noodle_segs:
        after = seg[next(i for i, t in enumerate(seg)
                         if t.rsplit("/", 1)[-1] == "noodle"):]
        if "|" in after:
            nxt = after[after.index("|") + 1:]
            found.append("noodle piped into "
                         + (nxt[0] if nxt else "another command"))
        redirects = [t for t in after
                     if t and all(c in "<>&" for c in t) and t != "&"]
        if redirects:
            found.append("noodle output redirected ("
                         + " ".join(sorted(set(redirects))) + ")")
        if any("$(" in t or "`" in t for t in after):
            found.append("command substitution inside the noodle segment")
    if len(segments) == 2 and segments[0][:1] == ["cd"] and noodle_segs:
        found.append("cd prefix (pass -w instead)")
    return "; ".join(found) or None


@lru_cache(maxsize=1)
def report() -> dict:
    """{} when clean/unknowable; the payload stamp when composed. Cached —
    one ps/proc read per process, and the parent doesn't change."""
    note = composition(parent_command())
    if not note:
        return {}
    return {"invocation_clean": False,
            "invocation_note": (
                f"this command line was shell-composed ({note}) — a noodle "
                "command line is ONE noodle invocation and its flags; "
                "payloads are complete as returned (payload_complete), so "
                "nothing here needs a pipe, a redirect, or a cd")}


def log_sighting(workspace: str = ".") -> None:
    """Append the sighting to the workspace's invocation log — best-effort,
    never raises, no-op when clean."""
    stamp = report()
    if not stamp:
        return
    try:
        path = Path(workspace) / ".noodle" / "invocation_log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(),
                                "argv": sys.argv[1:6],
                                "note": stamp["invocation_note"]}) + "\n")
    except OSError:
        pass
