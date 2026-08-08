"""NOOD_0241 — the parity manifest: every pipeline operation's tool argument,
its CLI flag, and (for author) its spec key, as one bounded payload.

The audited shell-leak session began with an agent guessing CLI flags off the
documented tool surface (`author_test(run_after_author=True)` → `--run`,
`run_and_report(headless=True)` → `author --headless`), eating four exit-2
laps, and improvising `--help | head` to recover. Two rules fall out of that:

1. Every documented tool argument must have an exact CLI twin (flag or spec
   key) — in a no-MCP estate the CLI is not a fallback, it IS the surface.
2. Discovery must have a sanctioned, machine-readable door. The --help ban
   only holds while `noodle capabilities --json` answers the same question
   bounded, complete, and pipe-free.

Nothing here is hand-listed twice: tool arguments come from the live core
signatures (the same ones the MCP tools delegate to) and flags come from the
live click commands, so the manifest cannot drift from either surface. The
unit tests additionally pin the MCP tool signatures (ast-parsed, no mcp
import) against this manifest, which is the CI tripwire the audit asked for.
"""
from __future__ import annotations

import inspect

# tool-argument name → click parameter name, where the two differ. Everything
# else maps by identity (snake_case flag spelling is derived by click).
_RENAMES = {
    "author": {"run_after_author": "run", "evidence_requests": "evidence_step"},
    "run": {"target": "path", "serve_reports": "serve",
            "preflight_check": "preflight"},
    "probe": {"do": "do_", "open_native_controls": "open_native",
              "timeout_ms": "timeout"},
}

_MCP_TOOLS = {"author": "author_test", "run": "run_and_report",
              "probe": "probe_page"}

# Tool arguments that live on the MCP wrapper rather than the core function
# (payload shaping applied at the door). Named here so the manifest covers
# the full documented tool surface without importing the optional mcp
# package; the unit test pins them against the ast-parsed signature.
_DOOR_ONLY_ARGS = {"probe": ("compact", "brief", "find")}

NOTE = ("The CLI is a full surface: every tool argument here is reachable as "
        "a flag or spec key. No MCP server registered? Use these flags — "
        "never --help, never a pipe; a command line is ONE noodle invocation.")


def _flags_of(group, cmd_name: str) -> dict:
    """click param name → rendered flag spelling for one command."""
    cmd = group.get_command(None, cmd_name)
    out = {}
    for p in cmd.params:
        names = [*p.opts, *p.secondary_opts]
        if any(n.startswith("-") for n in names):
            out[p.name] = " / ".join(names)
        else:
            out[p.name] = f"<{p.name} argument>"
    return out


def _tool_args(funcs) -> list[str]:
    seen = []
    for f in funcs:
        for name in inspect.signature(f).parameters:
            if name not in ("kw", "kwargs") and name not in seen:
                seen.append(name)
    return seen


def manifest() -> dict:
    import typer.main

    from noodle import cli
    from noodle.repl import core
    group = typer.main.get_command(cli.app)
    sources = {
        "author": (core._author_test_door, core._author_test_impl),
        "run": (core.run_and_report,),
        "probe": (core.probe_page,),
    }
    ops = {}
    for op, funcs in sources.items():
        cmd_name = op
        flags = _flags_of(group, cmd_name)
        renames = _RENAMES.get(op, {})
        args, mapped = {}, set()
        for name in (*_tool_args(funcs), *_DOOR_ONLY_ARGS.get(op, ())):
            click_name = renames.get(name, name)
            entry = {"flag": flags.get(click_name)}
            mapped.add(click_name)
            if op == "author":
                if name in cli._SPEC_KEYS:
                    entry["spec_key"] = name
                if name == "evidence_requests":
                    # two explicit flags carry the one list argument
                    entry["flag"] = (f"{flags.get('evidence_step')} / "
                                     f"{flags.get('evidence_skip')}")
                    mapped.add("evidence_skip")
            if entry["flag"] is None and not entry.get("spec_key"):
                # a core-internal knob on neither agent door — said out loud,
                # so a reader never mistakes absence for an undocumented flag
                entry["internal"] = True
            args[name] = entry
        cli_only = sorted(flags[n] for n in flags
                          if n not in mapped and n not in ("as_json",
                                                           "json_out"))
        ops[op] = {"cli": f"noodle {cmd_name}", "mcp_tool": _MCP_TOOLS[op],
                   "arguments": args, "cli_only": cli_only}
    return {"ok": True, "operations": ops,
            "spec_keys": list(cli._SPEC_KEYS), "note": NOTE}
