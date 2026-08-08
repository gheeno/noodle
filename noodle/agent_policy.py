"""NOOD_0241 — machine-enforced agent tool policy, generated per workspace.

The shell-leak audit's root cause finding: the "noodle commands only, no
pipes" rule existed only as prose in AGENTS.md and the skill cards, and a
rule a model can decide to bypass under friction is a rule it will bypass
under friction. Prose is a preference; a host tool allow/deny list is a
control. In a no-MCP estate (the audited one — corporate policy bans MCP
servers) the shell tool cannot be disabled, because it is the only way to
invoke noodle at all: enforcement must therefore allow the `noodle` binary
while denying shell COMPOSITION around it.

The composition patterns are the load-bearing entries, not the executable
list: `noodle author --help | head -50` still starts with `noodle`, so a
bare `Bash(noodle:*)` allow-list waves it through — that is exactly how the
audited leak would slip a naive prefix rule, and the regression test pins
it. The executable denials are belt-and-braces for hosts that match
per-command rather than per-line.

`noodle init` writes these files into every workspace (refresh with
`--force`, skip with `--no-agent-policy`); `noodle doctor --policy`
verifies they exist, carry every required entry, and actually deny the
canned probe strings. Where a host cannot hard-enforce (VS Code's terminal
auto-approve map gates approval, it does not block), the file is written
anyway and doctor says "advisory" out loud rather than pretending.

Domain-agnostic and OS-agnostic by construction: no app names, no machine
paths, and the deny set carries PowerShell spellings alongside the POSIX
ones so a Windows workspace is not left unprotected.
"""
from __future__ import annotations

import json
from pathlib import Path

# What an agent session in a workspace may run in a shell: noodle itself and
# git (version control is workspace management, not observability of a
# system under test — the one sanctioned exception, same as AGENTS.md).
ALLOW = ["Bash(noodle:*)", "Bash(git:*)"]

# Shell-composition metacharacters, POSIX + PowerShell. Any line containing
# one is not "exactly one noodle invocation and its flags".
DENY_COMPOSITION = [
    "Bash(*|*)", "Bash(*>*)", "Bash(*<*)", "Bash(*&&*)", "Bash(*;*)",
    "Bash(*`*)", "Bash(*$(*)",
    "Bash(*Out-File*)", "Bash(*Tee-Object*)", "Bash(*Set-Content*)",
]

# Text-wrangling / re-hosting executables the audited sessions reached for.
DENY_EXECUTABLES = [
    "Bash(cd:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(tee:*)",
    "Bash(cat:*)", "Bash(grep:*)", "Bash(rg:*)", "Bash(sed:*)",
    "Bash(awk:*)", "Bash(jq:*)", "Bash(find:*)", "Bash(ls:*)",
    "Bash(xargs:*)", "Bash(curl:*)", "Bash(wget:*)",
    "Bash(python:*)", "Bash(python3:*)", "Bash(node:*)", "Bash(allure:*)",
]

DENY = DENY_COMPOSITION + DENY_EXECUTABLES

# The audit's own leak lines, verbatim shapes (nothing app- or
# machine-specific — and deliberately no <angle-bracket> placeholders, which
# would themselves match the redirection patterns and prove nothing).
# verify() proves the policy denies every one and still allows the clean
# invocation; the unit fixture pins the first probe against a bare
# allow-list to prove the composition entries are what work.
PROBE_LEAKS = (
    "noodle author --help | head -50",
    "cd workspace-root && noodle probe https://app.example --compact",
    "noodle author --spec-text 'spec' --json 2>&1 | tee /tmp/out.json",
)
PROBE_CLEAN = "noodle run noodle_tests/sample_app --headless --retries 0 --json"


def _matches(pattern: str, line: str) -> bool:
    """Conservative mirror of a host's Bash() rule matching — substring for
    Bash(*X*) composition patterns, first-word for Bash(tool:*) prefixes.
    Deliberately simple: doctor uses it to prove the WRITTEN policy covers
    the canned probes, not to reimplement any host byte-for-byte."""
    if not (pattern.startswith("Bash(") and pattern.endswith(")")):
        return False
    body = pattern[5:-1]
    if body.startswith("*") and body.endswith("*"):
        return body.strip("*") in line
    if body.endswith(":*"):
        head = line.strip().split()[0] if line.strip() else ""
        return head == body[:-2] or head.startswith(body[:-2] + ".")
    return line.strip() == body


def denies(line: str, deny: list[str] | None = None) -> str | None:
    """The first deny pattern matching `line`, or None."""
    for pattern in (DENY if deny is None else deny):
        if _matches(pattern, line):
            return pattern
    return None


def allows(line: str, allow: list[str] | None = None) -> bool:
    return any(_matches(p, line) for p in (ALLOW if allow is None else allow))


# ---------------------------------------------------------------- files ---

def _claude_settings(existing: dict) -> dict:
    perms = existing.setdefault("permissions", {})
    for key, wanted in (("allow", ALLOW), ("deny", DENY)):
        have = perms.setdefault(key, [])
        have.extend(w for w in wanted if w not in have)
    return existing

def _vscode_settings(existing: dict) -> dict:
    # VS Code / Copilot Chat terminal auto-approve: true = run without
    # asking, false = always ask. It gates approval rather than blocking —
    # advisory containment, and doctor reports it as exactly that.
    approve = existing.setdefault("chat.tools.terminal.autoApprove", {})
    approve.setdefault("noodle", True)
    approve.setdefault("git", True)
    approve.setdefault("/[|<>;`]|&&|\\$\\(/", False)
    for tool in ("cd", "head", "tail", "tee", "cat", "grep", "sed", "awk",
                 "jq", "find", "xargs", "curl", "wget", "python", "python3",
                 "node", "allure", "Out-File", "Tee-Object", "Set-Content"):
        approve.setdefault(tool, False)
    return existing

def _canonical_policy(existing: dict) -> dict:
    existing.update({
        "policy": "noodle-agent-shell",
        "rule": ("a command line is exactly ONE noodle invocation and its "
                 "flags — any other executable, or any of | > < && ; ` $( "
                 "(PowerShell: Out-File, Tee-Object, Set-Content), is "
                 "forbidden regardless of what it is"),
        "allow": ALLOW,
        "deny": DENY,
        "note": ("canonical, host-neutral copy. Hosts without native "
                 "file-based tool permissions translate `allow`/`deny` into "
                 "their own mechanism (session flags, org policy); "
                 "`noodle doctor --policy` verifies the set stays current."),
    })
    return existing


FILES = {
    Path(".claude") / "settings.json": _claude_settings,
    Path(".vscode") / "settings.json": _vscode_settings,
    Path(".copilot") / "agent-policy.json": _canonical_policy,
}


def install(root: str | Path) -> dict:
    """Write/merge every host policy file under `root`. Merge, never
    clobber: existing unrelated keys (a team's own settings) are preserved,
    wanted entries are added when missing. Returns {relpath: created|
    updated|kept}. Idempotent — a second run reports every file kept."""
    out = {}
    for rel, builder in FILES.items():
        path = Path(root) / rel
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, ValueError):
            existing = {}
        before = json.dumps(existing, sort_keys=True)
        merged = builder(existing)
        after = json.dumps(merged, sort_keys=True)
        if path.exists() and before == after:
            out[str(rel)] = "kept"
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged, indent=2, sort_keys=False) + "\n",
                        encoding="utf-8")
        out[str(rel)] = "updated" if before != "{}" else "created"
    return out


def verify(root: str | Path) -> list[dict]:
    """Findings for doctor: per file — present? every required entry
    present? and for the enforceable set, do the canned probe leaks match a
    deny while the clean invocation stays allowed? Each finding: {file, ok,
    enforcement, detail}."""
    findings = []
    for rel in FILES:
        path = Path(root) / rel
        f = {"file": str(rel), "ok": False,
             "enforcement": ("advisory (approval gate only)"
                             if ".vscode" in str(rel) else "enforcing")}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            f["detail"] = "missing — `noodle init --force` writes it"
            findings.append(f)
            continue
        except ValueError:
            f["detail"] = "not valid JSON"
            findings.append(f)
            continue
        if rel.name == "settings.json" and ".claude" in str(rel):
            perms = (data.get("permissions") or {})
            missing = [p for p in (*ALLOW, *DENY)
                       if p not in (perms.get("allow") or [])
                       and p not in (perms.get("deny") or [])]
            deny = perms.get("deny") or []
            leak = next((p for p in PROBE_LEAKS if not denies(p, deny)), None)
            if missing:
                f["detail"] = f"stale — missing entries: {', '.join(missing[:4])}…" \
                    if len(missing) > 4 else f"stale — missing: {', '.join(missing)}"
            elif leak:
                f["detail"] = f"ineffective — probe not denied: {leak!r}"
            elif denies(PROBE_CLEAN, deny) or not allows(
                    PROBE_CLEAN, perms.get("allow") or []):
                f["detail"] = "over-broad — the clean noodle invocation is blocked"
            else:
                f["ok"] = True
                f["detail"] = "every probe leak denied; bare noodle allowed"
        elif ".vscode" in str(rel):
            approve = data.get("chat.tools.terminal.autoApprove") or {}
            f["ok"] = approve.get("noodle") is True and any(
                v is False for v in approve.values())
            f["detail"] = ("auto-approve map present" if f["ok"]
                           else "stale — auto-approve map incomplete")
        else:
            f["ok"] = data.get("deny") == DENY and data.get("allow") == ALLOW
            f["detail"] = ("canonical copy current" if f["ok"]
                           else "stale — re-run `noodle init --force`")
        findings.append(f)
    return findings
