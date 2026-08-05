"""The spec-shape benchmark (NOOD_0232) — `noodle benchmark`.

`noodle benchmark --gate` answers "can the engine still generate a good
test". It cannot answer the question a user asks before adopting Noodle:
**does it still work when I don't phrase my request the way your benchmark
does.** Every one of that gate's cases is a numbered imperative list, so the
shape of the request is pinned at exactly one value and never measured — a
build that generates perfect tests from perfectly-shaped prompts scores PASS
whatever it does with a paragraph, a one-liner or a half-specified ticket.

This benchmark holds the APP constant — the repo's own bundled BusterBlock
site, behind its login gate — and varies only how the request is phrased:
a paragraph, a numbered list, one sentence, a short ambiguous spec, and one
spec whose assertion is deliberately wrong. Because the app is fixed, any
difference between rows is attributable to the phrasing and nothing else.

Five design rules, each of which is the reason a column reads the way it does:

**The specs live in docs/benchmark-specs.md, not here.** They are parsed out
of that file at run time. A benchmark whose prompts are a Python list has two
copies of every prompt the moment it is documented, and the copy a human
pastes stops being the one the engine measures. One file, both audiences: a
human copies a fenced block into their session, an MCP client sends the same
block as `prompt`, and this module reads it byte-for-byte.

**One workspace, five features, one report.** Every spec authors into the
same `noodle init` workspace under its OWN feature file, so the served Allure
report holds all five as one suite — which is what a suite is. Results
accumulate (NOOD_0229), so each spec is run once and only once: no second
authoring pass, and the report is of exactly the runs that were measured.

**A spec that must FAIL.** B5's assertion is wrong on purpose. Nothing else
here can catch the failure mode where the engine reaches green by dropping
an assertion it could not prove — every other spec is written to succeed, so
every other spec would report that build as healthy.

**`expect`, so the gate is not red forever.** A shape the deterministic
compiler cannot take today is recorded (⛔) with its measured rejection, not
scored. A gate that is red on its own backlog is a gate nobody reads. It
still moves: a blocked spec that starts working is announced as a closed gap.

**Corrections are counted, not just outcomes.** A spec that goes green after
the engine reworded two control names, inserted a click and healed a locator
is not the same product as one that went green first time, and both end
`passed`. Every repair the engine makes on the caller's behalf is counted and
named.
"""
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path

# ─── the specs ──────────────────────────────────────────────────────────────

SPEC_DOC = "docs/benchmark-specs.md"

# `<!-- spec: steps | expect: pass -->` immediately above the fenced block it
# describes. An HTML comment because it must be invisible in the rendered
# document — the file's first job is to be pasted from by a human, and a
# machine-readable marker they can see is a machine-readable marker they will
# eventually paste along with the spec.
_MARKER = re.compile(
    r"<!--\s*spec:\s*([a-z_0-9]+)\s*\|\s*expect:\s*(pass|fail|blocked)\s*-->")
_FENCE = re.compile(r"^```[a-zA-Z]*\n(.*?)^```", re.M | re.S)
_HEADING = re.compile(r"^###\s+(.+?)\s*$", re.M)

EXPECTS = ("pass", "fail", "blocked")


class BenchmarkError(RuntimeError):
    """A setup fault that makes the measurement impossible — a missing spec
    document, an app that cannot be hosted. Raised rather than scored: a
    benchmark that reports REGRESSED because nobody ran `npm ci` has blamed
    the engine for the operator's missing step."""


def spec_doc() -> Path:
    """docs/benchmark-specs.md in the clone. The benchmark only runs from a
    checkout (so does the gate — `regression_runs/` lands in the clone), so
    there is no wheel-install path to fall back to."""
    from noodle import install_check
    return (install_check.clone_root() or Path.cwd()) / SPEC_DOC


def load_specs(doc: str | Path | None = None) -> list[dict]:
    """Parse the marked fenced blocks out of the spec document, in file order.

    Returns [{id, ref, label, expect, spec}]. `ref`/`label` come from the
    `### B1 — a paragraph` heading between the marker and the block, so the
    table's shape column is the document's own wording and cannot describe
    a spec differently from the page a reader is looking at.
    """
    path = Path(doc) if doc else spec_doc()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BenchmarkError(
            f"cannot read the benchmark specs at {path} — the specs live in "
            f"{SPEC_DOC} and are parsed from it at run time") from exc
    out = []
    for m in _MARKER.finditer(text):
        fence = _FENCE.search(text, m.end())
        if fence is None:
            raise BenchmarkError(
                f"spec {m.group(1)!r} in {path.name} has no fenced block "
                "after its marker — the block IS the spec")
        head = _HEADING.search(text, m.end(), fence.start())
        title = head.group(1) if head else m.group(1)
        ref, _, label = title.partition("—")
        out.append({"id": m.group(1), "expect": m.group(2),
                    "ref": ref.strip(), "label": (label or ref).strip(),
                    "spec": fence.group(1).strip("\n")})
    if not out:
        raise BenchmarkError(
            f"no `<!-- spec: <id> | expect: <{'|'.join(EXPECTS)}> -->` "
            f"markers found in {path} — nothing to measure")
    return out


# ─── the app under test ─────────────────────────────────────────────────────

# BusterBlock (test-apps/busterblock) is the repo's own bundled full-stack
# test site: a real Express app with a LOGIN GATE in front of its catalogue.
# The gate is what makes it the honest target — every spec has to carry
# credentials and get through it before it reaches the thing being asserted,
# exactly like the prompts users actually send. A static fixture gates
# nothing, so a benchmark on one measures a flow no real request has.
PORT = 3333
BASE_URL = f"http://127.0.0.1:{PORT}"


def _answers(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout):
            return True
    except OSError:
        return False


def host_app() -> tuple:
    """(stop, base_url) for BusterBlock. Raises BenchmarkError when it cannot
    be hosted — never a fallback to a different app.

    The previous design fell back to the static fixture and noted it in the
    verdict. Honest, but the wrong trade: the specs name BusterBlock's own
    controls and credentials, so on the fixture every one of them would be
    measured against a page that has no `username` field, and the table would
    report five engine gaps that were one missing `npm ci`.

    An instance already listening is REUSED and never stopped — a developer
    with the app open for their own work should not have it killed from under
    them, and it is the same app either way.
    """
    if _answers(PORT):
        return (lambda: None), BASE_URL
    from noodle import install_check
    root = (install_check.clone_root() or Path.cwd()) / "test-apps" / "busterblock"
    if not (root / "server.js").is_file():
        raise BenchmarkError(
            f"BusterBlock is not in this checkout ({root}) — the benchmark's "
            "specs are written against it and mean nothing without it")
    if not (root / "node_modules").is_dir():
        raise BenchmarkError(
            "BusterBlock's dependencies are not installed (node_modules/ is "
            f"gitignored). Run:  cd {root} && npm ci")
    try:
        # `node server.js`, NOT `npm start`: npm spawns node as a CHILD, so
        # terminating npm leaves node alive holding port 3333 — and the next
        # run then "reuses" a server this one was supposed to have stopped.
        proc = subprocess.Popen(
            ["node", "server.js"], cwd=str(root),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise BenchmarkError(
            f"could not start BusterBlock — is node installed? ({exc})") from exc
    for _ in range(60):                               # up to ~30s
        if proc.poll() is not None:
            raise BenchmarkError(
                f"BusterBlock exited immediately (code {proc.returncode}) — "
                f"try `node server.js` in {root} to see why")
        if _answers(PORT):
            return proc.terminate, BASE_URL
        time.sleep(0.5)
    proc.terminate()
    raise BenchmarkError(
        f"BusterBlock did not answer on port {PORT} within 30s")


# ─── the agent-driven session ───────────────────────────────────────────────
#
# THE PRIMARY WORKFLOW, and the one the product actually ships in: a human
# pastes a spec into their Claude/Copilot session, and the agent translates
# whatever prose arrived into Noodle calls. There is always an agent in front
# of the engine, so "the deterministic grammar could not parse this phrasing"
# is not a product outcome — it is the engine's floor with nobody driving.
# What matters here is how much WORK the loop took: attempts, engine repairs,
# heals, and wall clock.
#
# The numbers still come from engine artifacts, never from the agent's memory
# of what it did. `core.author_test` appends one ledger line per attempt while
# a session is open, so the table is built from what the engine recorded —
# including the GAPS BETWEEN attempts, which are the agent's own turnaround
# and the honest cost of having it in the loop. An agent self-reporting its
# own benchmark numbers is the failure NOOD_0190 removed from the gate; it is
# not being reintroduced here.

SESSION_FILE = ".noodle/benchmark_session.json"
LEDGER_FILE = ".noodle/benchmark_ledger.jsonl"


def _session_path(workspace) -> Path:
    return Path(workspace) / SESSION_FILE


def active(workspace) -> dict | None:
    """The open session for this workspace, or None. Cheap and exception-free
    — it is called on EVERY author_test, including the millions that have
    nothing to do with a benchmark."""
    try:
        return json.loads(_session_path(workspace).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _spec_of(feature_path: str | None, specs: list) -> str | None:
    """Which spec an authoring call belongs to, from its feature filename.

    The runbook mandates `spec_<id>.feature`, so the mapping is the file name
    and nothing else — never call order, which a session that retries one spec
    or works them out of order would silently scramble.
    """
    stem = Path(str(feature_path or "")).stem
    for s in specs:
        if stem == f"spec_{s['id']}":
            return s["id"]
    return None


def record(workspace, *, feature_path, started: float, seconds: float,
           result: dict) -> None:
    """Append one attempt to the ledger. Never raises — a benchmark ledger is
    an observation, and failing an author call because the observation could
    not be written would corrupt the very thing being measured."""
    try:
        sess = active(workspace)
        if not sess:
            return
        spec = _spec_of(feature_path, sess.get("specs") or [])
        if spec is None:
            return
        author = result.get("author") or {}
        run = result.get("run") or {}
        repairs, _ = _corrections(author, run, result)
        path = Path(workspace) / LEDGER_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "spec": spec, "started": started, "ended": started + seconds,
                "seconds": round(seconds, 2),
                "feature": author.get("feature"),
                "authored": bool(author.get("feature")),
                "ready": bool(author.get("ready")),
                "run_s": run.get("seconds"),
                "passed": run.get("passed") or 0, "failed": run.get("failed") or 0,
                "verified": run.get("verified") is True,
                "engine_repairs": repairs,
                "tokens": _tokens(result, str(workspace)),
                "blocked_by": (result.get("error")
                               or next(iter(author.get("blocking") or []), None)),
            }, default=str) + "\n")
    except Exception:
        pass


def _host_detached() -> tuple[int | None, str]:
    """BusterBlock, left RUNNING after the command exits — a session drives the
    benchmark across many turns, so the app has to outlive the process that
    started it. `--table` stops it again."""
    if _answers(PORT):
        return None, BASE_URL                     # already up; not ours to kill
    from noodle import install_check
    root = (install_check.clone_root() or Path.cwd()) / "test-apps" / "busterblock"
    if not (root / "server.js").is_file():
        raise BenchmarkError(f"BusterBlock is not in this checkout ({root})")
    if not (root / "node_modules").is_dir():
        raise BenchmarkError(
            "BusterBlock's dependencies are not installed (node_modules/ is "
            f"gitignored). Run:  cd {root} && npm ci")
    try:
        proc = subprocess.Popen(
            ["node", "server.js"], cwd=str(root), start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise BenchmarkError(f"could not start BusterBlock: {exc}") from exc
    for _ in range(60):
        if proc.poll() is not None:
            raise BenchmarkError(
                f"BusterBlock exited immediately (code {proc.returncode})")
        if _answers(PORT):
            return proc.pid, BASE_URL
        time.sleep(0.5)
    proc.terminate()
    raise BenchmarkError(f"BusterBlock did not answer on port {PORT} within 30s")


# BusterBlock's own credentials, from its checked-in fixture users.csv. They
# are not a secret in any meaningful sense — they are test data for a test app
# in this repo — but they go through the SECRETS path anyway, because that is
# the path a real login takes and the benchmark is here to exercise the real
# path. Named as the keys a spec references: `{env:BUSTERBLOCK_USER}`.
SECRETS = {"BUSTERBLOCK_USER": "reel_ryan", "BUSTERBLOCK_PASSWORD": "Popcorn1!"}


def redact(text: str) -> str:
    """Mask every known credential value in anything this benchmark writes.

    Its own artifacts leaked one: a blocked spec is reported with the clauses
    the compiler rejected, quoted verbatim, so the password inside the spec
    text rode into `benchmark_results.json`, both verdict files, and the
    `verdict.html` that gets SERVED. Exact-value replacement, not a heuristic
    on field names: the benchmark knows precisely which values it seeded, so
    it can mask those and nothing else — no guessing which token in a sentence
    was the secret, and no mangling a movie title that happens to look like
    one. Scope is exactly `SECRETS` — the values this module seeds. Point
    the benchmark at another app and its credentials are NOT covered until
    they are added there.
    """
    for v in SECRETS.values():
        if v:
            text = text.replace(v, "***")
    return text


def _seed_secrets(workspace) -> Path:
    """Write the app's credentials into the workspace's gitignored
    `secrets.env` at SETUP time, before anything authors.

    Not a convenience — a correctness fix the first session run found. Passing
    them as `secret_values` on the authoring call is too late: the goal's
    `{env:}` values have to RESOLVE for the probe to walk past the login gate,
    and the probe runs inside the same transaction that would have written
    them — which then rolls back on the block it just caused. Credentials are
    workspace setup, the same as they are for a human, who puts them in
    secrets.env before writing the first test.
    """
    p = Path(workspace) / "secrets.env"
    body = "".join(f"{k}={v}\n" for k, v in SECRETS.items())
    existing = p.read_text(encoding="utf-8") if p.is_file() else ""
    if all(k in existing for k in SECRETS):
        return p
    p.write_text(existing + ("" if existing.endswith("\n") or not existing
                             else "\n") + body, encoding="utf-8")
    return p


def session_start(workspace: str) -> dict:
    """Open an agent-driven run: host the app, arm the ledger, hand back the
    runbook. Everything after this is the session's to do."""
    specs = load_specs()
    pid, base = _host_detached()
    _seed_secrets(workspace)
    sess = {"workspace": str(workspace), "base_url": base, "pid": pid,
            "started": time.time(),
            "specs": [{k: s[k] for k in ("id", "ref", "label", "expect")}
                      for s in specs]}
    p = _session_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sess, indent=2), encoding="utf-8")
    return {**sess, "full_specs": specs, "runbook": runbook(workspace, specs)}


def runbook(workspace: str, specs: list) -> str:
    """The instruction the session follows. Written to be executed, not read:
    every step names the exact call, because a runbook an agent has to
    interpret is one more place for the measurement to vary by who ran it."""
    lines = [
        "The app is running and the workspace is scaffolded. Do the rest in "
        "THIS session, in order:", "",
        f"  workspace: {workspace}",
        "", "For each of the 5 specs below:", "",
        "  1. Send the spec to the engine EXACTLY as written — do not reword "
        "it first. That first attempt is what measures the engine on its own.",
        "     author_test(prompt=<the spec, verbatim>,",
        "                 feature_path=\"spec_<id>.feature\",",
        f"                 workspace=\"{workspace}\",",
        "                 run_after_author=True, overwrite=True)",
        "  2. If it comes back blocked, YOU translate it — that is your job in "
        "this loop, and the ledger is counting how much of it you had to do. "
        "Re-author with the same feature_path. Repeat until it authors.",
        "     The app's credentials are ALREADY in this workspace's gitignored "
        "secrets.env as {env:BUSTERBLOCK_USER} / {env:BUSTERBLOCK_PASSWORD} — "
        "use those in the goal, never the literal values, so nothing lands in "
        "the Gherkin that should not be committed.",
        "     A goal whose flow sits behind the login gate needs "
        "`probe: {perform: true}`, so the probe walks past the gate instead of "
        "snapshotting the login page.",
        "  3. Move to the next spec. Do not batch them — one spec at a time, "
        "so the ledger's per-spec timings mean something.", "",
        "Then print the table:  noodle benchmark --table", "",
        "The engine records every attempt itself. Do not tally anything by "
        "hand and do not report a number you did not read off that table.",
        "", "── the 5 specs " + "─" * 50]
    for s in specs:
        lines += ["", f"[{s['ref']}] spec_{s['id']}.feature — {s['label']}",
                  "", *(f"    {ln}" for ln in s["spec"].splitlines())]
    return "\n".join(lines)


def session_table(workspace: str) -> dict:
    """Score the ledger — one row per spec, whatever order the session did
    them in and however many attempts each took."""
    sess = active(workspace)
    if not sess:
        raise BenchmarkError(
            f"no open benchmark session in {workspace} — start one with "
            "`noodle benchmark --session`")
    entries = []
    try:
        for line in (Path(workspace) / LEDGER_FILE).read_text(
                encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        pass
    cases = []
    for spec in sess["specs"]:
        # `expect: blocked` describes the HEADLESS run — the deterministic
        # grammar with nobody driving. In a session the agent IS the
        # interpreter, so there is no such thing as a shape it cannot take:
        # every spec must produce a test, and one that does not is the agent
        # failing at its job, not a tracked engine gap. Leaving the headless
        # expectation in place here would print "🎉 gap closed" three times a
        # run and let a genuinely stuck spec pass as expected.
        spec = dict(spec, expect="pass" if spec["expect"] != "fail" else "fail")
        mine = [e for e in entries if e["spec"] == spec["id"]]
        if not mine:
            cases.append({**_blank(spec), "outcome": "not attempted",
                          "attempts": 0, "why": "the session never authored "
                          "this spec"})
            continue
        last, first = mine[-1], mine[0]
        # DEVELOPMENT TIME with an agent in the loop is first-attempt-start to
        # last-attempt-end: it has to include the agent's own turnaround
        # between attempts, because that is time the user waits. Both ends are
        # engine timestamps, so the agent cannot flatter it.
        dev = round(last["ended"] - first["started"]
                    - (last.get("run_s") or 0), 1)
        engine_s = round(sum(e["seconds"] for e in mine)
                         - (last.get("run_s") or 0), 1)
        # "how many times did we heal the feature" — every correction the loop
        # made: the agent's re-author attempts AND the engine's own repairs.
        heals = (len(mine) - 1) + sum(e.get("engine_repairs") or 0 for e in mine)
        outcome = ("passed" if last.get("passed") and not last.get("failed")
                   else "failed" if last.get("failed")
                   else "blocked")
        cases.append({
            "id": spec["id"], "ref": spec["ref"], "label": spec["label"],
            "expect": spec["expect"], "outcome": outcome,
            "feature": last.get("feature"), "attempts": len(mine),
            "development_s": dev, "engine_s": engine_s,
            "run_s": last.get("run_s"), "elapsed_s": dev,
            "payload_tokens": last.get("tokens"),
            "corrections": heals, "correction_detail":
                ([{"n": len(mine) - 1, "what": "agent re-author attempt(s) — "
                                              "the engine refused the spec as "
                                              "sent"}] if len(mine) > 1 else [])
                + ([{"n": r, "what": "engine repair(s)"}]
                   if (r := sum(e.get("engine_repairs") or 0 for e in mine))
                   else []),
            "lines": None, "passed": last.get("passed") or 0,
            "failed": last.get("failed") or 0,
            "verified": bool(last.get("verified")),
            "intent_verified": None, "unverified_reasons": [],
            "llm_cost": None, "unresolved": [],
            "why": last.get("blocked_by") if outcome == "blocked" else None})
    return {"workspace": workspace, "cases": cases,
            "app_under_test": f"BusterBlock @ {sess['base_url']}",
            "spec_doc": str(spec_doc()), "mode": "session",
            "llm_mode": "agent-driven session (the agent is the interpreter)",
            "report": _report(workspace,
                              sum(1 for c in cases if c.get("feature")))}


def owns_pid(pid) -> bool:
    """Is `pid` STILL the BusterBlock this benchmark started?

    A session spans many turns, so between `--session` and `--table` the app
    can die and the OS can hand its pid to something else entirely — and a
    bare `os.kill(pid)` would then signal an innocent process. Narrow window,
    unbounded blast radius, so it gets checked.

    Unverifiable means DON'T: on a platform without `ps` the answer is False
    and the app is left running with its pid reported. Leaking a dev server
    somebody can stop by hand is a far better failure than killing their
    editor.
    """
    try:
        out = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False
    return "server.js" in out and "busterblock" in out.lower()


def session_stop(workspace: str) -> str | None:
    """Close the session and stop the app it started (never one it found
    already running — that is somebody's own dev server). Returns a note when
    the app was left running, so the caller can say so rather than imply a
    teardown that did not happen."""
    sess = active(workspace) or {}
    note = None
    if pid := sess.get("pid"):
        if owns_pid(pid):
            try:
                os.kill(int(pid), 15)
            except (OSError, ValueError, TypeError):
                note = f"could not stop BusterBlock (pid {pid}) — stop it by hand"
        else:
            note = (f"BusterBlock (pid {pid}) was left running: this process "
                    "could not confirm the pid is still that app, and killing "
                    "an unverified pid is not worth the risk")
    try:
        _session_path(workspace).unlink()
    except OSError:
        pass
    return note


# ─── budget ─────────────────────────────────────────────────────────────────

# **Under two minutes to develop a test case** is the acceptance criterion,
# and it is this benchmark's own number rather than one inherited from the
# gate — the gate's `NOODLE_REG_MAX_ELAPSED_S` can be moved for a slow site
# without anyone intending to move this.
#
# The token ceiling is not invented either: 8 KB is the engine's own agent-door
# payload budget (NOOD_0164), so 2048 tokens is the bound the engine already
# holds itself to, restated in the unit an agent is billed in. It is scored
# only on a spec that went green — see score().
_DEFAULTS = {
    "max_development_s": ("NOODLE_BENCH_MAX_DEV_S", 120),
    "max_payload_tokens": ("NOODLE_BENCH_MAX_TOKENS", 2048),
}


def budget() -> dict:
    """Corrections comes from the gate — a spec here is a test case like any
    other and must not be graded on a softer curve. Time and tokens are this
    benchmark's own."""
    from noodle import regression
    return {"max_corrections": regression.budget()["max_corrections"]} | {
        k: float(os.getenv(env, d)) for k, (env, d) in _DEFAULTS.items()}


# ─── measuring one spec ─────────────────────────────────────────────────────

def _collapse(result: dict, workspace: str) -> dict:
    """The payload an agent door actually returns, so TOKENS is the number a
    caller is billed for rather than the size of an internal dict.

    `collapse_green` is the current name; a branch in flight renames it to
    `collapse_payload`. Asking the build which it has costs three lines and
    keeps this readable across that rename.
    """
    from noodle.repl import core
    fn = getattr(core, "collapse_payload", None) or getattr(
        core, "collapse_green", None)
    return fn(result, workspace=workspace) if fn else result


def _tokens(payload, workspace: str) -> int:
    """Tokens the CALLER actually receives, not the size of an internal dict.

    Collapsed AND bounded, in that order, because that is the pair of
    transforms every agent door applies before the payload crosses the wire
    (NOOD_0217 then NOOD_0164). Measuring the raw envelope would report a
    cost nobody pays, and would report it as growing on builds where only the
    diagnosis the door already trims got bigger.
    """
    from noodle import payload_budget
    payload = payload_budget.bound(_collapse(payload, workspace))
    return round(len(json.dumps(payload, default=str).encode()) / 4)


def _corrections(author: dict, run: dict, result: dict) -> tuple[int, list]:
    """Every repair the engine made on the caller's behalf, counted and named.

    Outcomes alone cannot distinguish a spec that worked from one that was
    made to work: both end `passed`. A dropped check is called out as a
    contract weakening because it is the only one of these that makes the
    test prove LESS than the spec asked for.
    """
    named = []
    for key, src, what in (
            ("rewritten_targets", author,
             "control name(s) reworded to the probed name"),
            ("prerequisite_clicks", author,
             "prerequisite click(s) inserted to reach a hidden control"),
            ("rewritten_checks", author, "check(s) reworded"),
            ("dropped_checks", author,
             "check(s) DROPPED — the test proves less than the spec asked"),
            ("healing_events", run, "locator(s) self-healed at run time"),
            ("flaky", run, "scenario(s) retried, then green")):
        if n := len(src.get(key) or []):
            named.append({"n": n, "what": what})
    if laps := (result.get("auto_fix") or {}).get("laps_used") or 0:
        named.append({"n": laps, "what": "engine re-author lap(s) on a red run"})
    return sum(x["n"] for x in named), named


def _case(spec: dict, workspace: str, log=None) -> dict:
    """Author + run ONE spec, measured from the engine's own payload.

    `run_after_author=True` is the door a real caller uses and the only one
    that gives development time and run time from the same call. Nothing is
    re-authored on a rejection: re-sending an identical prompt returns an
    identical rejection, and counting that lap would report the engine as
    having tried twice.
    """
    from noodle.repl import core
    if log:
        log(f"   · {spec['ref']} {spec['id']} — {spec['label']}")
    t0 = time.monotonic()
    try:
        res = core.author_test(
            prompt=spec["spec"], feature_path=f"spec_{spec['id']}.feature",
            run_after_author=True, overwrite=True, workspace=workspace)
    except Exception as exc:                  # a door that cannot be driven is
        return {**_blank(spec),               # a finding, not a crash
                "elapsed_s": round(time.monotonic() - t0, 1),
                "outcome": "error",
                "why": f"{type(exc).__name__}: {exc}"[:300]}
    elapsed = round(time.monotonic() - t0, 1)
    author, run = res.get("author") or {}, res.get("run") or {}
    compiled = author.get("compiled") or {}
    # `ok: True` is NOT proof anything was written — a blocked author rolls the
    # compiled feature and POM back and still reports ok with a warning. The
    # FILE is the evidence that a test exists.
    authored = bool(author.get("feature"))
    tokens = _tokens(res, workspace)
    if not authored:
        unresolved = res.get("unresolved") or author.get("unresolved") or []
        return {**_blank(spec), "elapsed_s": elapsed, "payload_tokens": tokens,
                "outcome": "blocked",
                "why": (res.get("error")
                        or next(iter(author.get("blocking") or []), None)
                        or "nothing was written"),
                # The rejected clauses ARE the actionable half of a blocked
                # run: which fragment the compiler could not take, and whether
                # it offered a rewrite. A refusal that coaches costs one cheap
                # lap; one that does not costs a human.
                "unresolved": [{"text": (u.get("text") or "")[:120],
                                "suggested": u.get("suggested")}
                               for u in unresolved][:6]}
    corrections, correction_detail = _corrections(author, run, res)
    failed, passed = run.get("failed") or 0, run.get("passed") or 0
    verified = run.get("verified") is True
    outcome = "failed" if failed else ("passed" if passed else "blocked")
    return {
        "id": spec["id"], "ref": spec["ref"], "label": spec["label"],
        "expect": spec["expect"], "outcome": outcome,
        "feature": author.get("feature"),
        "elapsed_s": elapsed, "run_s": run.get("seconds"),
        # DEVELOPMENT time is the number the "under a minute" expectation
        # belongs on: prompt in, .feature written. Total wall clock is
        # dominated by the site, which is not the engine.
        "development_s": round(elapsed - (run.get("seconds") or 0), 1),
        "payload_tokens": tokens,
        "corrections": corrections, "correction_detail": correction_detail,
        "lines": (len((compiled.get("feature") or "").splitlines())
                  + len((compiled.get("pom") or "").splitlines())) or None,
        "passed": passed, "failed": failed, "verified": verified,
        "intent_verified": author.get("intent_verified"),
        "unverified_reasons": (run.get("unverified_reasons") or [])[:3],
        "llm_cost": run.get("llm_cost"),
        # A feature was written and yet no scenario is recorded — the one
        # state where the payload's own counters say nothing useful. The
        # engine knows why (exit code, and the tail of the run's console), and
        # dropping it leaves a reader with "nothing ran" and no next step.
        "why": None if outcome != "blocked" else _why_nothing_ran(run),
        "unresolved": []}


def _why_nothing_ran(run: dict) -> str:
    skipped = run.get("skipped")
    if isinstance(skipped, str) and skipped:
        return skipped
    tail = " | ".join(str(run.get("output") or "").strip().splitlines()[-4:])
    return (f"the feature was written but no scenario ran (exit "
            f"{run.get('exit_code')})" + (f" — {tail[:400]}" if tail else ""))


def _blank(spec: dict) -> dict:
    """The shape every case returns, with every measurement absent.

    None, never 0: a blocked spec generated nothing, so there is nothing that
    could have been corrected and nothing that ran. `0 corrections` on a spec
    that produced no test is a measured-looking lie in the one direction that
    flatters the engine.
    """
    return {"id": spec["id"], "ref": spec["ref"], "label": spec["label"],
            "expect": spec["expect"], "outcome": "blocked", "feature": None,
            "elapsed_s": None, "run_s": None, "development_s": None,
            "payload_tokens": 0, "corrections": None, "correction_detail": [],
            "lines": None, "passed": 0, "failed": 0, "verified": False,
            "intent_verified": None, "unverified_reasons": [],
            "llm_cost": None, "why": None, "unresolved": []}


# ─── the report ─────────────────────────────────────────────────────────────

def _report(workspace: str, expected_features: int) -> dict:
    """ONE Allure report over every spec that ran, built and hosted.

    No re-authoring and no second run: `allure-results/` accumulates
    (NOOD_0229 — a run replaces only what it re-ran), so the five per-spec
    runs above have already deposited five results in one directory. The
    report is therefore of exactly the runs that were measured, which a
    re-authoring second phase could not promise.
    """
    from noodle.repl import core
    t0 = time.monotonic()
    out = {"seconds": None, "urls": [], "ok": False,
           "expected_features": expected_features}
    try:
        built = core.build_report(workspace)
        served = core.serve_report(workspace)
        # The count comes from the ENGINE's own report_scope (NOOD_0229),
        # never from counting files or author calls: it is the one number that
        # describes the SERVED REPORT rather than the intent behind it.
        scope = (core.last_result(workspace) or {}).get("report_scope") or {}
        out |= {"seconds": round(time.monotonic() - t0, 1),
                "urls": served.get("urls") or [],
                "ok": bool(served.get("urls")) and built.get("ok") is not False,
                "features": scope.get("features_in_report"),
                "scenarios": scope.get("scenarios_in_report"),
                "note": scope.get("note"),
                "rca": built.get("rca_html")}
    except Exception as exc:
        out |= {"seconds": round(time.monotonic() - t0, 1),
                "error": f"{type(exc).__name__}: {exc}"[:200]}
    return out


def execute(workspace: str, *, specs: list[dict] | None = None, log=None) -> dict:
    """Host the app, measure every spec into ONE workspace, build ONE report.

    The workspace is the caller's (the CLI scaffolds a build-stamped one with
    the real `noodle init`), so the whole run is filed under the build that
    produced it and two builds can be compared folder against folder.
    """
    specs = specs or load_specs()
    stop, base = host_app()
    if log:
        log(f"   app under test: BusterBlock @ {base}")
    try:
        cases = [_case(s, workspace, log) for s in specs]
        if log:
            log("   · building and serving one report over all of them")
        report = _report(workspace, sum(1 for c in cases if c["feature"]))
    finally:
        stop()
    from noodle import install_check
    return {
        "workspace": workspace, "cases": cases, "report": report,
        "spec_doc": str(spec_doc()), "app_under_test": f"BusterBlock @ {base}",
        # Requirement F, in one string: WHICH BUILD these numbers belong to.
        # The build-stamped folder name says it too, but a verdict that gets
        # pasted into a ticket travels without its folder.
        "build": install_check.build_line(),
        "llm_mode": _llm_mode()}


def _llm_mode() -> str:
    """Which interpretation path was under test.

    Every rejection the deterministic compiler emits ends with "set
    NOODLE_MODEL to allow one bounded interpretation call", so a shape blocked
    with no model configured is a gap in the DETERMINISTIC grammar, not
    necessarily in the engine — two different findings, and the run has to say
    which one it made. Three states, not two: a machine with no model AND no
    `llm` extra cannot even attempt the model path, which is a third finding
    again and shows up in the rejection text as an import error rather than as
    the documented "set NOODLE_MODEL" advice.
    """
    if model := os.getenv("NOODLE_MODEL"):
        return f"NOODLE_MODEL={model}"
    try:
        import litellm  # noqa: F401
    except ImportError:
        return ("deterministic only — NOODLE_MODEL unset AND the llm extra is "
                "not installed, so the model path could not even be attempted "
                "(pip install 'noodle[llm]')")
    return "deterministic only (NOODLE_MODEL unset)"


# ─── scoring ────────────────────────────────────────────────────────────────

def score(results: dict) -> dict:
    """PASS = every spec reached the outcome its `expect` records, inside the
    per-spec budget, and the one report was built and hosted.

    `expect` records what this build MEASURABLY does, not what it ought to.
    That is what keeps the benchmark readable: a shape the compiler has never
    taken is recorded, not scored, so the verdict stays a signal about THIS
    change rather than a standing complaint about the backlog.
    """
    b = budget()
    cases, regressions, blocked, closed = [], [], [], []
    for c in results.get("cases") or []:
        fails, expect, outcome = [], c.get("expect"), c.get("outcome")
        delivered = outcome in ("passed", "failed")
        if expect == "pass":
            if outcome != "passed":
                fails.append(
                    f"expected to pass, {outcome}"
                    + (f" — {str(c.get('why'))[:200]}" if c.get("why") else ""))
            elif not c.get("verified"):
                # A pass reached through fuzzy healing or a lenient match is
                # not a pass — it is the failure mode `verified` exists to
                # name, and the reasons are the whole diagnosis.
                fails.append(
                    "passed but UNVERIFIED (fuzzy healing or a lenient match "
                    "sits behind the green)"
                    + (f" — {'; '.join(c.get('unverified_reasons') or [])[:200]}"
                       if c.get("unverified_reasons") else ""))
        elif expect == "fail":
            if outcome == "passed":
                # The worst outcome the benchmark can produce, and the only
                # one B5 exists to catch: a test whose assertion is false
                # reported green means the engine reached a pass by dropping
                # or weakening the assertion, and every other green in the
                # table is worth less for it.
                fails.append(
                    "went GREEN on an assertion that is false — the engine "
                    "reached a pass without proving what the spec asked "
                    "(check `corrections` for a dropped or reworded check)")
            elif outcome != "failed":
                fails.append(
                    f"expected to author and fail, {outcome}"
                    + (f" — {str(c.get('why'))[:200]}" if c.get("why") else ""))
        elif expect == "blocked":
            if delivered:
                closed.append(
                    f"{c['id']}: this shape now authors and "
                    f"{'passes' if outcome == 'passed' else 'runs'} — promote "
                    f"it to `expect: pass` in {SPEC_DOC} so a later build "
                    "cannot lose it again")
            else:
                blocked.append(c)
        # Budgets grade DELIVERED specs only. A blocked spec's numbers describe
        # a refusal, and grading a refusal on development time rewards refusing
        # faster.
        if delivered:
            if (dev := c.get("development_s")) is not None \
                    and dev > b["max_development_s"]:
                fails.append(
                    f"slow development: {dev:.0f}s > "
                    f"{b['max_development_s']:.0f}s (the generated test's own "
                    "run time is excluded — this is prompt in, feature out)")
            if (corr := c.get("corrections")) is not None \
                    and corr > b["max_corrections"]:
                fails.append(
                    f"inaccurate: {corr} corrections > {b['max_corrections']:.0f}"
                    + (" — " + "; ".join(f"{d['n']} {d['what']}"
                                         for d in c.get("correction_detail") or [])))
            # Cost is scored on the GREEN path only. A red run's payload
            # carries the RCA the caller needs to act on, so charging B5 for
            # carrying a diagnosis would be scoring the engine for doing its
            # job — and the fix a reader would reach for is to return less
            # about the failure. The number is still measured and printed.
            if outcome == "passed" and (tok := c.get("payload_tokens")) \
                    and tok > b["max_payload_tokens"]:
                fails.append(
                    f"expensive: {tok} payload tokens back to the caller > "
                    f"{b['max_payload_tokens']:.0f} (the engine's own 8 KB "
                    "agent-door budget)")
        cases.append({**c, "pass": not fails, "failures": fails,
                      "expected_block": expect == "blocked" and not delivered})
        regressions += [f"{c['id']}: {f}" for f in fails]

    report = results.get("report") or {}
    if not report.get("ok"):
        regressions.append(
            "report: the one Allure report over all five specs was not built "
            "and hosted" + (f" — {report['error']}" if report.get("error") else ""))
    elif (got := report.get("features")) is not None \
            and got < (want := report.get("expected_features") or 0):
        # Requirement D, checked rather than assumed: every spec that produced
        # a feature must be IN the report. A report holding fewer is the
        # "why am I only seeing one test" failure, and it is silent otherwise.
        regressions.append(
            f"report: {got} feature(s) in the served report but {want} spec(s) "
            "authored one — the report does not cover every spec that ran")

    def _avg(key):
        # Delivered specs only, for the same reason budgets are: averaging a
        # 0.0s refusal into development time reports the engine as fast for
        # having declined the work.
        vals = [c[key] for c in cases if c["outcome"] in ("passed", "failed")
                and isinstance(c.get(key), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    tally = {k: sum(1 for c in cases if c["outcome"] == k)
             for k in ("passed", "failed", "blocked", "error")}
    # Two different costs, and conflating them is why the old prompt could not
    # answer "is it cheap". `payload_tokens` is what the ENGINE hands back —
    # model-independent, fully under the engine's control, and therefore the
    # one worth a ceiling. `llm` is what the engine SPENT with the configured
    # model, which by definition moves with the model and so is reported, not
    # scored. Neither is the driving agent's own reasoning: the engine cannot
    # see or change that, and a number nobody can act on is not a measurement.
    spend = [c["llm_cost"] for c in cases if isinstance(c.get("llm_cost"), dict)]
    llm = None
    if spend:
        usd = [s["usd"] for s in spend if s.get("usd") is not None]
        llm = {"calls": sum(s.get("calls") or 0 for s in spend),
               "input_tokens": sum(s.get("input_tokens") or 0 for s in spend),
               "output_tokens": sum(s.get("output_tokens") or 0 for s in spend),
               "usd": round(sum(usd), 6) if usd else None,
               "model": next((s.get("model") for s in spend if s.get("model")),
                             None)}
    return {
        "verdict": "PASS" if not regressions else "REGRESSED",
        "budget": b, "engine": results.get("engine"),
        "build": results.get("build"), "workspace": results.get("workspace"),
        "spec_doc": results.get("spec_doc"),
        "app_under_test": results.get("app_under_test"),
        "llm_mode": results.get("llm_mode"),
        "cases": cases, "report": report, "tally": tally, "llm": llm,
        "average": {k: _avg(k) for k in ("development_s", "engine_s", "run_s",
                                         "corrections", "lines",
                                         "payload_tokens")},
        "regressions": regressions,
        "blocked": [{"id": c["id"], "label": c["label"], "why": c.get("why"),
                     "unresolved": c.get("unresolved") or []} for c in blocked],
        "gaps_closed": closed,
        "next": ("no regression — every spec this build takes still authors, "
                 "runs and reports as it did"
                 if not regressions else
                 "confirm against the last known-good build: `git checkout "
                 "main` + `noodle update` + `noodle benchmark`, then diff the "
                 "two verdict files — docs/benchmark.md "
                 "§ Bisecting a regression")}


# ─── rendering ──────────────────────────────────────────────────────────────

_MARK = {"passed": "✅ passed", "failed": "✅ failed as intended",
         "blocked": "⛔ blocked", "error": "❌ error"}


def _outcome_text(c: dict) -> str:
    """What actually happened, in the reader's terms — never the raw outcome.

    A `failed` row means opposite things depending on what was asked for, and
    a table that prints the same word for "the assertion the spec asked for
    was disproved, as designed" and "the test the spec asked for broke" trains
    its reader to skim past both.
    """
    o = c["outcome"]
    if o == "passed" and not c.get("verified"):
        return "⚠ passed (unverified)"
    if o == "failed" and c.get("expect") != "fail":
        return "❌ failed"
    if o == "passed" and c.get("expect") == "fail":
        return "❌ passed (should have failed)"
    return _MARK.get(o, o)


def render_table(verdict: dict) -> str:
    """The benchmark as a terminal table — requirement C. Plain f-strings, the
    same house style as regression.render_table: no rich, no tabulate, no
    dependency for seven columns."""
    v = verdict

    def _s(x):
        return "—" if x is None else f"{x:g}s"

    def _n(x):
        return "—" if x is None else f"{x:g}"

    # NOOD_0232 — in a SESSION, `DEV` is wall clock and includes the agent's
    # own turnaround between attempts, which is most of it. Reporting that one
    # number alone made the table unreadable in the one way that matters: a
    # spec whose agent stopped to think for two minutes looked identical to a
    # spec the engine was slow at. `ENGINE` is what the engine actually spent,
    # so the two costs can be told apart. Absent headless (there is no agent,
    # so the two are the same number) and the column is dropped rather than
    # printed twice.
    eng = any(c.get("engine_s") is not None for c in v["cases"])
    rows = [f"🥢 noodle benchmark — {v.get('build') or v.get('engine') or 'unknown build'}",
            f"   app under test: {v.get('app_under_test') or 'unknown'}",
            f"   specs:          {v.get('spec_doc') or SPEC_DOC}",
            f"   interpretation: {v.get('llm_mode') or 'unknown'}",
            f"   workspace:      {v.get('workspace') or '—'}", "",
            f"   {'SPEC':<15}{'SHAPE':<26}{'DEV':>7}"
            + (f"{'ENGINE':>8}" if eng else "")
            + f"{'RUN':>7}{'CORR':>6}{'TOKENS':>8}  RESULT"]
    for c in v["cases"]:
        rows.append(
            f"   {c['id']:<15}{c['label'][:25]:<26}"
            f"{_s(c.get('development_s')):>7}"
            + (f"{_s(c.get('engine_s')):>8}" if eng else "")
            + f"{_s(c.get('run_s')):>7}"
            f"{_n(c.get('corrections')):>6}{_n(c.get('payload_tokens')):>8}"
            f"  {_outcome_text(c)}")
    a, t = v["average"], v["tally"]
    rows += ["   " + "─" * (76 + (8 if eng else 0)),
             f"   {'average (delivered specs)':<41}{_s(a['development_s']):>7}"
             + (f"{_s(a['engine_s']):>8}" if eng else "")
             + f"{_s(a['run_s']):>7}{_n(a['corrections']):>6}"
             f"{_n(a['payload_tokens']):>8}",
             "   (averages cover specs that produced a test — a refusal "
             "returns fast and small)",
             f"   budget: ≤{v['budget']['max_development_s']:.0f}s to develop "
             f"a test case · ≤{v['budget']['max_corrections']:.0f} corrections "
             f"· ≤{v['budget']['max_payload_tokens']:.0f} tokens back to the "
             "caller on a green spec"]
    # TOKENS is what the engine returns. What the engine SPENT with a model is
    # a different number that moves with the model, so it is printed rather
    # than scored — and printed as "none" rather than omitted, because a
    # missing cost line reads as "not measured", not as "free".
    if llm := v.get("llm"):
        rows.append(
            f"   LLM     {llm['calls']} call(s), "
            f"{llm['input_tokens']}→{llm['output_tokens']} tokens"
            + (f", ~${llm['usd']:.4f}" if llm.get("usd") is not None else "")
            + (f" ({llm['model']})" if llm.get("model") else ""))
    else:
        rows.append("   LLM     none — the deterministic compiler took every "
                    "spec it took; nothing was spent with a model")
    r = v.get("report") or {}
    rows += ["",
             f"   REPORT  {_s(r.get('seconds'))} "
             + ("✅ hosted — "
                f"{r.get('features')} feature(s) / {r.get('scenarios')} "
                "scenario(s) in ONE report"
                if r.get("ok") else
                "❌ not hosted"
                + (f" — {r['error']}" if r.get("error") else "")),
             "",
             f"   VERDICT: {v['verdict']}",
             f"   {t['passed']} passed · {t['failed']} failed as intended · "
             f"{t['blocked']} blocked · {len(v['cases'])} specs total"]
    rows += [f"   ⚠ {r_}" for r_ in v["regressions"]]
    if blocked := v.get("blocked"):
        rows += ["", f"   ⛔ BLOCKED SHAPES ({len(blocked)}) — recorded, not "
                     "scored; this list is the run's actionable output:"]
        for bl in blocked:
            rows.append(f"      {bl['id']} — {bl['label']}")
            rows += _wrap(bl.get("why") or "", 8)
            for u in bl.get("unresolved") or []:
                rows.append(f"          ✗ {u['text']}")
                if u.get("suggested"):
                    rows.append(f"            → {u['suggested']}")
    rows += [f"   🎉 {c}" for c in v.get("gaps_closed") or []]
    if urls := r.get("urls"):
        rows += [""] + [f"   📊 {u}" for u in urls]
    return "\n".join(rows)


def _wrap(text: str, indent: int, width: int = 78) -> list[str]:
    """Hand-wrapped because the reasons are the useful half of this report and
    a 400-character line in a terminal is not read."""
    pad, out, line = " " * indent, [], " " * indent
    for word in str(text).split():
        if len(line) + len(word) > width:
            out.append(line)
            line = pad
        line += word + " "
    return out + [line.rstrip()] if line.strip() else out


def render_html(verdict: dict) -> str:
    """The scorecard, written into the build folder and served at
    /verdict.html beside /allure-report/index.html."""
    v = verdict
    color = "#1a7f37" if v["verdict"] == "PASS" else "#cf222e"
    rows = ""
    for c in v["cases"]:
        detail = "; ".join(f"{d['n']} {d['what']}"
                           for d in c.get("correction_detail") or [])
        rows += (
            "<tr><td><b>{ref}</b> {id}<br><span style='color:#57606a'>{label}"
            "</span></td><td>{res}</td><td>{dev}</td><td>{run}</td>"
            "<td>{corr}</td><td>{tok}</td><td>{why}</td></tr>".format(
                ref=c.get("ref", ""), id=c["id"], label=c.get("label", ""),
                res=_outcome_text(c),
                dev=f"{c['development_s']}s" if c.get("development_s")
                    is not None else "—",
                run=f"{c['run_s']}s" if c.get("run_s") is not None else "—",
                corr=("—" if c.get("corrections") is None
                      else f"{c['corrections']}"
                           + (f"<br><span style='color:#57606a'>{detail}</span>"
                              if detail else "")),
                tok=c.get("payload_tokens") or "—",
                why=("; ".join(c["failures"]) or c.get("why") or "—")))
    b, r, t = v["budget"], v.get("report") or {}, v["tally"]
    blocked = "".join(
        f"<li><b>{bl['id']}</b> — {bl['label']}<br>{bl.get('why') or ''}"
        + ("<ul>" + "".join(
            f"<li><code>{u['text']}</code>"
            + (f" → <i>{u['suggested']}</i>" if u.get("suggested") else "")
            + "</li>" for u in bl.get("unresolved") or []) + "</ul>"
           if bl.get("unresolved") else "") + "</li>"
        for bl in v.get("blocked") or [])
    return f"""<!doctype html><meta charset="utf-8">
<title>noodle benchmark verdict</title>
<style>body{{font:15px/1.5 system-ui;margin:2rem auto;max-width:70rem;padding:0 1rem}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #d0d7de;padding:.4rem .6rem;text-align:left;vertical-align:top}}
.v{{color:#fff;background:{color};display:inline-block;padding:.2rem .8rem;border-radius:.4rem}}</style>
<h1>Spec-shape benchmark — <span class="v">{v["verdict"]}</span></h1>
<p><b>{v.get("build") or v.get("engine") or "unknown build"}</b> ·
{v.get("app_under_test")} · interpretation: <b>{v.get("llm_mode")}</b>.<br>
Every row sends <b>the same app</b> a request in a different shape, from
<code>{v.get("spec_doc") or SPEC_DOC}</code>, into <b>one</b>
<code>noodle init</code> workspace — so any difference between rows is
attributable to the phrasing and nothing else. <b>DEV</b> is development time
(prompt in, feature written; the generated test's own run time excluded).
<b>CORR</b> counts every repair the engine made on the caller's behalf.
<b>TOKENS</b> is the payload the agent door returns, ÷ 4.
⛔ marks a shape this build is known not to author: recorded with its measured
rejection, not scored, so the benchmark stays a signal about this change
rather than a standing complaint about the backlog.</p>
<table><tr><th>spec</th><th>result</th><th>dev</th><th>run</th><th>corr</th>
<th>tokens</th><th>why not</th></tr>{rows}</table>
<p><b>{t['passed']}</b> passed · <b>{t['failed']}</b> failed as intended ·
<b>{t['blocked']}</b> blocked, of {len(v['cases'])} specs.
<b>Averages</b> (delivered specs only): {v['average']['development_s']}s
development, {v['average']['run_s']}s run,
{v['average']['corrections']} corrections,
{v['average']['payload_tokens']} payload tokens.
<b>Budget:</b> ≤{b['max_development_s']:.0f}s to develop a test case,
≤{b['max_corrections']:.0f} corrections, ≤{b['max_payload_tokens']:.0f} tokens
back to the caller on a green spec.<br>
<b>LLM spend:</b> {(lambda m: f"{m['calls']} call(s), "
                   f"{m['input_tokens']}&rarr;{m['output_tokens']} tokens"
                   + (f", ~${m['usd']:.4f}" if m.get('usd') is not None else "")
                   + (f" ({m['model']})" if m.get('model') else ""))(v['llm'])
                  if v.get('llm') else
                  "none — nothing was spent with a model"} (reported, not
scored: it moves with the model, unlike the payload the engine returns).<br>
<b>Report:</b> {r.get('seconds')}s, {'hosted' if r.get('ok') else 'NOT hosted'}
— {r.get('features')} feature(s) / {r.get('scenarios')} scenario(s) in one
Allure report.</p>
{"<h2>Blocked shapes</h2><ul>" + blocked + "</ul>" if blocked else ""}
{"".join(f"<p>⚠ {x}</p>" for x in v["regressions"])}
{"".join(f'<p style="color:#9a6700">🎉 {c}</p>' for c in v.get("gaps_closed") or [])}
<p><a href="allure-report/index.html">Allure report</a> ·
<a href="rca.html">RCA report</a></p>
<p style="color:#57606a">{v["next"]}</p>"""
