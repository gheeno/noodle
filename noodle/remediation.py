"""NOOD_0228 — a remediation is a COMMAND, never a file edit.

The audited failure: `locator._on_ambiguous` told an agent to open
`resources/pom.yaml` and paste a selector into it. No `noodle pom` command
existed, so the only executable reading of that advice was a heredoc — and
the agent, correctly, obeyed the engine over the skill card. The engine
prohibited shell improvisation in prose and then prescribed it in an error.

The fix is structural, not editorial. Every remediation the engine emits is
a record, not a sentence:

    Remediation(action_id="locator.ambiguous", command="pom resolve",
                args={"ambiguity_id": "A1f3c9", "candidate_id": "C1"},
                explanation="'add to order' matched 4 elements …")

Rendering is DERIVED from the record, so a remediation cannot contain a
filesystem-edit imperative — there is nowhere to put one. Two invariants are
enforced mechanically by unit_tests/test_nood_0228_affordance_contract.py:

  1. `command` resolves to a registered Typer command (CLI) — a dangling
     command id fails CI. This is what stops a future ticket writing
     "edit pom.yaml" again: there is no way to express it.
  2. The rendered text contains no filesystem-edit imperative
     (`filesystem_imperative()` below is the same detector CI runs).

The registry is also the enumeration F4 walks: `REMEDIATIONS` holds every
action_id the engine can emit, so "every remediation names a real command"
is checkable without grepping call sites.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Remediation:
    """One typed next action. `command` is a space-separated registered
    noodle command path ('pom resolve'); `args` are rendered as CLI options
    in declaration order. `explanation` says WHY, never HOW — the how is the
    command."""
    action_id: str
    command: str
    args: dict = field(default_factory=dict)
    explanation: str = ""
    note: str = ""
    positional: tuple[str, ...] = ()

    def argv(self) -> list[str]:
        """The rendered command as argv. `positional` names the args the CLI
        takes as Arguments rather than Options — rendering one as `--flag`
        produced a line that read fine and would not run, which is the same
        class of defect as prescribing a file edit."""
        out = list(self.command.split())
        for k in self.positional:
            v = self.args.get(k)
            if v is not None:
                out.append(str(v))
        for k, v in self.args.items():
            if k in self.positional or v is None or v is False:
                continue
            out.append("--" + k.replace("_", "-"))
            if v is not True:
                out.append(str(v))
        return out

    def command_line(self) -> str:
        parts = ["noodle"]
        for token in self.argv():
            parts.append(f'"{token}"' if " " in token else token)
        return " ".join(parts)

    def render(self) -> str:
        """Human/agent-facing text. Explanation, then the one command."""
        lines = []
        if self.explanation:
            lines.append(self.explanation)
        lines.append(f"→ {self.command_line()}")
        if self.note:
            lines.append(f"  ({self.note})")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {"action_id": self.action_id, "command": self.command,
                "args": dict(self.args), "explanation": self.explanation,
                "command_line": self.command_line()}


# ── the registry ────────────────────────────────────────────────────────────
# action_id → (command, required arg names, explanation template). Anything
# the engine may recommend is declared HERE, so the contract test can walk
# the set instead of hunting call sites. Adding a call site without a
# registry entry raises at build() time — loudly, at author/run time, not
# silently in a log line nobody reads.
REMEDIATIONS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "locator.ambiguous": (
        "pom resolve", ("ambiguity_id", "choose"),
        "{phrase!r} matched {count} elements — pin the one you meant."),
    "locator.no_pom_entry": (
        "pom set", ("phrase", "css"),
        "{phrase!r} has no pin and accessibility could not settle it."),
    # (continued below; POSITIONAL declares which of the above are Arguments)
    "evidence.marker_in_step": (
        "author", ("brief",),
        "Evidence requests are not accepted inside step text. Pass the "
        "user's own words as the brief and the engine derives which step "
        "gets a picture, or name the step positions directly."),
    "evidence.marker_in_workspace": (
        "migrate evidence-markers", ("write",),
        "This feature carries evidence requests inside step text, which the "
        "engine no longer accepts. The migration rewrites them as scenario "
        "tag metadata through the normal author transaction."),
    "workspace.discover_resources": (
        "workspace inspect", ("app",),
        "Environment keys, POM pins and feature paths are reported by the "
        "engine."),
    "workspace.drift": (
        "author", ("overwrite",),
        "Engine-authored files changed outside the engine; re-establish "
        "ownership by re-authoring the change."),
    "spec.multiline": (
        "author", ("spec_text",),
        "A multi-line spec is one argument — no file and no redirection."),
}

# Which args each command takes POSITIONALLY. Kept beside the registry so the
# two can never drift apart silently; the contract test executes every
# rendered command line against the CLI parser, which is what catches a drift.
POSITIONAL: dict[str, tuple[str, ...]] = {
    "locator.ambiguous": ("ambiguity_id",),
    "locator.no_pom_entry": ("phrase",),
}

# Args that are boolean CLI flags: rendered bare, never with a value. Same
# reason as POSITIONAL — `--write xwrite` is a line that cannot run.
FLAGS: dict[str, tuple[str, ...]] = {
    "evidence.marker_in_workspace": ("write",),
    "workspace.drift": ("overwrite",),
}


class UnknownRemediation(KeyError):
    """An action_id no registry entry declares. Raised at build time so a
    dangling remediation can never reach a payload."""


def build(action_id: str, *, explanation: str | None = None,
          note: str = "", **args) -> Remediation:
    """Mint a remediation from the registry. `explanation` overrides the
    template; otherwise the template is formatted with **args (missing keys
    leave the template's own wording)."""
    try:
        command, required, template = REMEDIATIONS[action_id]
    except KeyError:
        raise UnknownRemediation(
            f"{action_id!r} is not a declared remediation — add it to "
            "noodle.remediation.REMEDIATIONS (its `command` must be a "
            "registered noodle command)") from None
    if explanation is None:
        try:
            explanation = template.format(**args)
        except (KeyError, IndexError):
            explanation = template
    flags = FLAGS.get(action_id, ())
    rendered_args = {k: (True if k in flags else args[k])
                     for k in required if args.get(k) is not None}
    return Remediation(action_id=action_id, command=command,
                       args=rendered_args, explanation=explanation, note=note,
                       positional=POSITIONAL.get(action_id, ()))


# ── invariant 1: the command exists ─────────────────────────────────────────
def registered_commands() -> set[str]:
    """Every command path the CLI registers, space-separated
    ('pom resolve', 'run', 'report serve'). Walked from the live Typer app —
    never a hardcoded list, or a command added tomorrow escapes the
    contract."""
    from noodle import cli

    def walk(typer_app, prefix: str) -> set[str]:
        out: set[str] = set()
        for cmd in typer_app.registered_commands:
            name = cmd.name or (cmd.callback.__name__ if cmd.callback else "")
            name = name.replace("_", "-")
            if name:
                out.add(f"{prefix}{name}".strip())
        for grp in typer_app.registered_groups:
            gname = grp.name or (
                grp.typer_instance.info.name if grp.typer_instance else "")
            if not gname or grp.typer_instance is None:
                continue
            out |= walk(grp.typer_instance, f"{prefix}{gname} ")
        return out

    return walk(cli.app, "")


# ── invariant 2: no filesystem-edit imperative ──────────────────────────────
# Matched against RENDERED remediation text and, in the contract test, against
# every diagnostic the engine emits. The shapes are the ones the audit found:
# a verb aimed at a file, a "put X in <file>" instruction, and a pasteable
# YAML selector snippet.
_FILE = r"(?:[\w./<>\[\]-]*\.(?:ya?ml|feature|env|json|py|txt|md))"
_FS_IMPERATIVES = (
    re.compile(rf"\b(?:edit|hand-edit|open|create|write|modify|amend|patch|"
               rf"append\s+to|paste\s+into)\b[^.\n]{{0,80}}{_FILE}",
               re.IGNORECASE),
    re.compile(rf"\b(?:put|add|pin|place|drop|copy)\b[^.\n]{{0,80}}?\b"
               rf"(?:in|into|to|inside)\b[^.\n]{{0,40}}{_FILE}", re.IGNORECASE),
    # a copy-ready selector snippet — "css: '…'" under an indented key
    re.compile(r"^\s+(?:css|xpath|id|testid)\s*:\s*['\"<]", re.MULTILINE),
)


def filesystem_imperative(text: str) -> str | None:
    """The matched phrase when `text` tells the reader to touch a file,
    else None. Deliberately blunt: a remediation has one legal shape (a
    noodle command), so a false positive costs a rewording and a false
    negative costs another NOOD_0228."""
    for rx in _FS_IMPERATIVES:
        m = rx.search(text or "")
        if m:
            return m.group(0).strip()
    return None
