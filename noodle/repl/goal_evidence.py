"""Goal-mode traversal + validation (NOOD_0227) — carved out of goal.py.

goal.py had grown to 3,700+ lines; the duplicate probe executors, the inert
perform flag and the single-page validator (the 65.9-AIC session's root
causes) were all symptoms of a module past reviewable size (RCA §7). This
module owns the probe-facing half of goal mode:

  * probe_args / perform_do — translate a validated goal into the ONE bounded
    probe transaction it needs (reveal clicks ride the do-chain, NOOD_0227);
  * the reach/runtime/evidence gates — where the probe's page evidence ends;
  * evidence() — match every requested action/check against what the probe
    proved, across every walked page-state, with honest do_failed surfacing.

goal.py re-imports every public and test-pinned name, so `goal.<name>`
remains the stable API; the schema/normalize/validate/compile half stays
there. The one deliberate cross-import — _check_step, lazily, for
runtime-asserted step labels — points the other way and only at call time,
so there is no import cycle.
"""
import difflib
import re


def _check_step(c, captions=None):
    """Lazy bridge to the compile-side step renderer (no import cycle)."""
    from noodle.repl.goal import _check_step as _cs
    return _cs(c, captions)


# NOOD_0156 — Unicode-aware: `[\W_]` keeps letters/digits of ANY script
# (the old [^a-z0-9] erased every non-ASCII caption to "", so goal matching
# and result binding silently failed on non-English sites). casefold() is
# the universal case mapping (ß→ss, İ→i̇) — the engine must work for any
# web app in any language, not just Latin-script ones.
_NORM_RE = re.compile(r"[\W_]+", re.UNICODE)


def _norm(s) -> str:
    return _NORM_RE.sub(" ", str(s or "").casefold()).strip()


def same_url(a: str, b: str) -> bool:
    """Scheme- and trailing-slash-insensitive URL equality — one derivation
    shared by navigation dedup (normalize) and env-key reuse."""
    def _bare(u: str) -> str:
        return u.rstrip("/").removeprefix("https://").removeprefix("http://")
    return _bare(a) == _bare(b)


def navigation_urls(goal: dict) -> list[str]:
    """The goal's ordered requested URLs, normalized to plain strings.
    [] when the goal has no navigation contract (single-URL goals keep the
    caller-supplied base URL)."""
    out = []
    for n in goal.get("navigation") or []:
        u = n.get("url") if isinstance(n, dict) else n
        if isinstance(u, str) and u.strip():
            out.append(u.strip())
    return out


def navigation_env(goal: dict, app: str,
                   base_url: str | None = None,
                   existing: dict | None = None) -> list[tuple[str, str]]:
    """Ordered (ENV_KEY, url) pairs for the goal's navigation contract — the
    compiler emits only {env:KEY} references; the URLs live in the app
    environments.yaml. Keys derive from the app + each URL's last path
    segment (universal — no site-specific names), deduplicated by suffix.
    NOOD_0209 — a navigation URL that IS the app's base URL reuses the app's
    own key instead of minting a second one (<APP>_HOME next to <APP> was
    systematic: two keys, one value, and the extra one never pruned).
    NOOD_0230 — `existing` (the app's current environments.yaml) makes key
    reuse VALUE-AWARE: a key another feature already pinned to a different
    URL is not this authoring's to repurpose. Two features of one app that
    start on different pages used to fight over the app key — the second
    authoring silently re-pointed the first's navigation Given, and the
    package run went red on a feature that was green an hour earlier."""
    from urllib.parse import urlsplit

    ex = {str(k).upper(): str(v) for k, v in (existing or {}).items()
          if isinstance(v, str) and v.strip()}

    def _free(key: str, url: str) -> bool:
        return key not in ex or same_url(ex[key], url)

    taken, out = set(), []
    for u in navigation_urls(goal):
        if base_url and same_url(u, base_url):
            key = app.upper()
            if key not in taken and _free(key, u):
                taken.add(key)
                out.append((key, u))
                continue
        path = urlsplit(u if "://" in u else f"https://{u}").path.strip("/")
        stem = path.rsplit("/", 1)[-1] if path else ""
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        stem = re.sub(r"[\W_]+", "_", stem, flags=re.UNICODE).strip("_").upper() \
            or "HOME"
        key, n = f"{app.upper()}_{stem}", 2
        while key in taken or not _free(key, u):
            key, n = f"{app.upper()}_{stem}{n}", n + 1
        taken.add(key)
        out.append((key, u))
    return out


def probe_args(goal: dict) -> dict:
    """The ONE bounded probe transaction this goal needs — nothing broader.
    Permission prompts, popups, standard search, and requested assertions do
    NOT imply discovery; discover only on goal.probe.discover. Native-control
    enumeration only when a select action needs options.

    Only REVEAL clicks (those before the first enter/select) are executed by
    the probe. A click after data entry is a commit (save/submit) — probing it
    would mutate application state, so it stays a runtime-only action (Risk 1).

    NOOD_0227 — on a searchless goal those reveal clicks ride the probe's own
    transaction executor (`do`, probe._do) instead of the reveal path
    (probe._reveal): `within:` compiles to the row-scoped click grammar
    instead of being dropped, each step diff-snapshots the page it lands on,
    and a failing step HALTS with a structured do_failed instead of being
    swallowed into click_warnings — the 65.9-AIC session's primary defect.
    Search-shaped goals keep the reveal path: their do-chain deliberately
    runs AFTER the search/pick (NOOD_0168), which would reorder a pre-search
    reveal click; so would a performed mutation (NOOD_0208), which runs
    ahead of the do-chain."""
    actions = goal.get("actions") or []
    search = next((a["term"] for a in actions if a["do"] == "search"), None)
    suggest = next((a["term"] for a in actions if a["do"] == "suggest"), None)
    # NOOD_0156 — a pick asks the probe to click the ONE bound result and
    # snapshot the landed page (read-only navigation, never a mutation), so
    # later requested actions resolve against real landed-page evidence.
    pick = next(((a.get("target") or "*") for a in actions
                 if a["do"] == "pick"), None)
    # NOOD_0156 — a requested add_to asks the probe to PROVE the mutation
    # path on the landed page (find the exact mutation control, or one
    # bounded prerequisite reveal) — never to perform the mutation itself.
    mutate = next((a.get("destination") for a in actions
                   if a["do"] == "add_to"), None)
    gate = _runtime_gate(actions)
    reveal_clicks = [a for i, a in enumerate(actions)
                     if a["do"] == "click" and (gate is None or i < gate)]
    perform = bool((goal.get("probe") or {}).get("perform"))
    chain_clicks = (not (search or suggest or pick)
                    and not (perform and mutate))
    clicks = [] if chain_clicks else [a["target"] for a in reveal_clicks]
    do = ([_click_do_string(a) for a in reveal_clicks] if chain_clicks
          else []) + perform_do(goal)
    # NOOD_0195 — FOLLOW the suggestion (NOOD_0142), don't just read the list.
    # Without this the probe stopped at the typeahead and closed it, so a
    # `suggest` goal only ever saw the LANDING page: every check on the page
    # the suggestion navigates to had no evidence source at all. Verified live
    # — a control literally named "a" then "proved" two 41-character product
    # titles through _find_text's reverse containment, and `ready: true` rested
    # on that. Following is read-only navigation, the same contract `pick`
    # already has, and it costs one page load.
    follow = next((a["option"] for a in actions if a["do"] == "suggest"), None)
    # NOOD_0195 — ask the probe to VERIFY the exact strings the checks name
    # (--expect, NOOD_0142: a full-text search of the page it ended on). The
    # structured captures are lossy — result captions truncate at ~60 chars,
    # so a 68-character product title could never be proven whole from them,
    # and the literal upgrade that needs full-render proof could never fire on
    # the flow it was written for. An expect verdict is exact and cheap: one
    # FOUND/NOT-FOUND per string, no extra page load.
    # ponytail: capped at 8 — the strings ride the probe payload, and a goal
    # naming more than 8 literals is asking for a suite, not a scenario.
    expect = [t for c in (goal.get("checks") or []) if isinstance(c, dict)
              for t in ([c["see"]] if isinstance(c.get("see"), str)
                        else [x for x in (c.get("any_of") or [])
                              if isinstance(x, str)])][:8]
    # NOOD_0233 — the goal's own login prelude as a RESCUE-ONLY chain: the
    # probe types it solely when its search/typeahead phase has already
    # failed AND the page visibly shows a password field (login_wall). It is
    # never run in the NOOD_0168 post-search position — on an ungated app the
    # landing search succeeds and the chain is never touched, so no goal that
    # authors today can newly fail. `perform: true` supersedes: its chain
    # already walks the whole tail, prelude included, by explicit opt-in.
    gate_do = (gate_chain(goal) if (search or suggest) and not perform
               else [])
    return {"search": search, "suggest": suggest, "pick": pick,
            "mutate": mutate, "follow": follow, "expect": expect or None,
            "click": clicks or None,
            "do": do or None,
            "gate_do": gate_do or None,
            "open_native_controls": any(a["do"] == "select" for a in actions),
            "discover": bool((goal.get("probe") or {}).get("discover")),
            "perform": perform}


def _click_do_string(a: dict) -> str:
    """One click action as a probe `do` string — the row-scoped grammar when
    the action carries `within:` (NOOD_0227). Emits the canonical spelling
    parse_do accepts, so probe and goal agree on what a row-scoped click is."""
    w = str(a.get("within") or "").strip()
    return (f'click {a["target"]} in the row containing {w}' if w
            else f'click {a["target"]}')


# NOOD_0208 — how many post-gate actions the probe will perform. Bounded
# because each one is a real click on a real app: a runaway chain would drive
# an unbounded transaction against someone's production site.
_PERFORM_CAP = 8


def perform_do(goal: dict) -> list[str]:
    """NOOD_0208 — the goal's post-gate actions as probe `do` strings, or [].

    Opt-in only (`probe: {perform: true}`). The probe deliberately stops at
    the first state-writing action, so every page beyond it — the confirmation
    the test asserts on — was never snapshotted, and the first authored
    wording was a GUESS. That cost one red run per flow even after NOOD_0207
    attached the exact repair to it.

    Turning it on hands those same actions to the existing `--do` transaction
    (NOOD_0144), which performs each one, settles, and diff-snapshots the
    result. Two things fall out for free: the destination page becomes real
    evidence (so the assertion is proven, not guessed), and a click that only
    NAVIGATED instead of mutating is visible as its own delta — the
    navigation-CTA trap.

    This PERFORMS MUTATIONS on the target app. That is why it is opt-in and
    capped, and why nothing turns it on by default.

    NOOD_0227 (RC-2) — the gate WIDENED. It was `_evidence_gate`
    (add_to/press_key/go_back only), so the far commoner pure click/enter
    flow — fill a form, click place order — returned [] and the documented
    opt-in silently no-opped: a full round-trip burned proving a flag does
    nothing. The chain now starts at the first action the probe does not
    already perform natively (search/suggest/pick are probe phases; reveal
    clicks before the runtime gate ride the do-chain via probe_args), which
    is exactly the data-entry-and-commit tail `perform` exists to walk. A
    post-gate click keeps its `within:` scope (B2)."""
    if not (goal.get("probe") or {}).get("perform"):
        return []
    actions = [a for a in (goal.get("actions") or []) if isinstance(a, dict)]
    gate = _runtime_gate(actions)
    start = next((i for i, a in enumerate(actions)
                  if a.get("do") not in _PROBE_FOLLOWS
                  and not (a.get("do") == "click"
                           and (gate is None or i < gate))), None)
    if start is None:
        return []
    out = []
    for a in actions[start:]:
        do, target = a.get("do"), a.get("target")
        if do == "add_to":
            # the mutation control resolves at evidence time, not here — the
            # destination word is what the probe already takes as `mutate`.
            continue
        if do == "click" and target:
            out.append(_click_do_string(a))
        elif do == "enter" and target:
            out.append(f"enter {a.get('value', '')} in {target}")
        elif do == "select" and target:
            out.append(f"select {a.get('option', '')} from {target}")
        else:
            # An action the do-grammar can't express ends the chain: the
            # states after it were never reached, and pretending otherwise
            # would claim evidence for a page the probe never saw.
            break
        if len(out) >= _PERFORM_CAP:
            break
    return out


# NOOD_0233 — a signup-shaped submit CREATES an account: walking it at author
# time is the NOOD_0156 state-mutation hazard, while a login submit only
# establishes a session. Negative filter by write-verb, not a positive
# "login" allowlist — the gate walk must refuse the known account-writing
# shapes in any wording, and everything else the credential shape admits.
_SIGNUP_RE = re.compile(
    r"sign\s?-?\s?up|register|registr|create|join|inscri|cadastr", re.I)


def _gate_prefix(actions: list) -> tuple[list, dict] | None:
    """NOOD_0233 — the goal's login prelude, or None: the run of
    credential-shaped `enter` actions that OPENS the runtime gate, plus the
    submit click that follows them.

    Detection is by SHAPE — the same enter/enter/click the prompt door's
    login verb lowers to — never by the word "login": every enter in the run
    must be credential-shaped (secret_key_for, the compiler's own two-tier
    rule), exactly ONE of them a true secret (two password fields is a
    signup/confirm form, none is not a login), and the submit must not be
    signup-shaped (_SIGNUP_RE). A prefix that fails any test is a form, not a
    gate, and is never walked."""
    from noodle.repl.prompt_expander import secret_key_for
    acts = [a for a in actions if isinstance(a, dict)]
    gate = _runtime_gate(acts)
    if gate is None or acts[gate].get("do") != "enter":
        return None
    i, enters = gate, []
    while i < len(acts) and acts[i].get("do") == "enter" \
            and isinstance(acts[i].get("target"), str) and acts[i]["target"]:
        enters.append(acts[i])
        i += 1
    if not enters or len(enters) > 3 or i >= len(acts):
        return None
    submit = acts[i]
    if submit.get("do") != "click" or not submit.get("target") \
            or _SIGNUP_RE.search(str(submit["target"])):
        return None
    if any(secret_key_for(a["target"], identity_too=True) is None
           for a in enters):
        return None            # a non-credential field: a form, not a gate
    if sum(1 for a in enters
           if secret_key_for(a["target"], identity_too=False)) != 1:
        return None            # no secret = not a login; two = signup/confirm
    return enters, submit


def gate_chain(goal: dict) -> list[str]:
    """NOOD_0233 — the login prelude as probe do-strings, [] when the goal
    has none. The chain STOPS at the gate's submit click: nothing past it is
    ever included, however many actions follow (the NOOD_0156 guardrail — a
    goal continuing into add_to/place-order must not have its tail walked at
    author time). Pure detection; probe_args decides when the probe actually
    receives it (`perform: true` supersedes — its chain walks the whole tail
    by explicit opt-in)."""
    pre = _gate_prefix(goal.get("actions") or [])
    if not pre:
        return []
    enters, submit = pre
    return [f"enter {a.get('value', '')} in {a['target']}" for a in enters] \
        + [_click_do_string(submit)]


def _evidence_gate(actions: list) -> int | None:
    """NOOD_0207 — index of the first action that writes state the probe
    deliberately never performs, so every page BEYOND it was never
    snapshotted. An action there naming an unprobed control is a statement
    about the probe's reach, not about the app: it is deferred to the run,
    exactly as checks in that position already were. Without this, every
    legitimate multi-page flow (mutate → checkout → form → submit) was
    unauthorable, and the reader burned laps probing for a name authoring was
    never going to accept.

    Deliberately NARROWER than _runtime_gate, which also lists `pick`,
    `enter` and `select`: the probe DOES follow a pick's navigation and
    snapshot the landed page, and it does resolve the fields an enter/select
    names — so a control unprobed there is genuinely absent, and blocking is
    right (test_nood_0156 pins that)."""
    for i, a in enumerate(actions):
        if a.get("do") in ("add_to", "press_key", "go_back"):
            return i
    return None


def _reach_gate(actions: list) -> int | None:
    """NOOD_0226 — index of the LAST action whose page the probe snapshotted.
    Every action AFTER it runs on a page the probe never loaded, so an
    unmatched name there is a statement about the probe's reach, not about the
    app — and a name that DOES match came off a page this step never visits.

    Two boundaries, whichever comes first:

    * `_evidence_gate` — the first state-writing verb the probe never performs
      (NOOD_0207); everything past it was already deferred on these terms.
    * the first COMMIT click — a click at or after the runtime gate.
      probe_args forwards only the clicks BEFORE that gate, by its own rule:
      "a click after data entry is a commit (save/submit) — probing it would
      mutate application state". So the page such a click lands on was never
      loaded, and nothing after it has evidence either.

    The login prelude is the everyday case and it had no boundary at all:
    enter / enter / click sign-in gates the runtime at index 0 and names no
    state-writing verb, so `_evidence_gate` was None, no click was forwarded
    to the probe, and every post-login step blocked with the SIGN-IN page's
    controls offered as near misses. Measured on `main` @ d34a902: the goal
    compiled correctly and the engine refused to admit it.

    The boundary action itself stays fully verified — its own control is on a
    page the probe did see (the sign-in button is on the sign-in page), so
    NOOD_0156's "an unprobed control after a pick still blocks" is untouched;
    only what follows it moves to the run."""
    eg = _evidence_gate(actions)
    rg = _runtime_gate(actions)
    commit = None if rg is None else next(
        (i for i, a in enumerate(actions)
         if i > rg and a.get("do") == "click"), None)
    gates = [g for g in (eg, commit) if g is not None]
    return min(gates) if gates else None


def _reach_clause(actions: list, reach: int | None, i: int) -> str:
    """NOOD_0226 — the one sentence a blocker needs when the goal walks past
    the probe's reach: which step the evidence ends on.

    Without it the probed vocabulary a blocker carries reads as "the app's
    controls", and the repair it invites is renaming a LATER step to an
    earlier page's control — the exact wrong move, and the one the login
    repro handed the reader (`username`, `password`, `login` offered as near
    misses for an add-to-cart click). Emitted only where it changes the
    repair: on the boundary step when later steps exist, and on any step past
    it that still blocks (a stated ambiguity/reachability note). A goal that
    ends at the boundary gets nothing — bytes on a payload with nothing to
    say."""
    if reach is None or reach >= len(actions) - 1 or i < reach:
        return ""
    label = _reach_label(actions, reach)
    where = f" ({label})" if label else ""
    if i > reach:
        return (f" — the probe's page evidence ends at step {reach + 1}"
                f"{where}; this step runs on a page it never loaded, so the "
                "controls named above are that earlier page's, not this one's")
    return (" — this is the last step the probe has page evidence for"
            + where
            + f"; the {len(actions) - reach - 1} step(s) after it run on pages "
              "it never loaded and are resolved by the run")


def _reach_label(actions: list, reach: int | None) -> str:
    """The boundary action in the goal's own words, for a blocker that has to
    say where the evidence stops."""
    if reach is None or not (0 <= reach < len(actions)):
        return ""
    a = actions[reach] if isinstance(actions[reach], dict) else {}
    what = a.get("target") or a.get("destination") or a.get("term") or ""
    do = str(a.get("do") or "")
    return f'{do} "{what}"' if what else do


# NOOD_0233 — env-key suffixes that look like login credentials. Matched
# against KEY NAMES from workspace/app files only (never os.environ — the
# shell's own USER/USERNAME would read as workspace credentials).
_CRED_KEY_RE = re.compile(
    r"(USER(NAME)?|E?MAIL|PASS(WORD|PHRASE|WD)?|PWD)$")


def _wall_clause(pg: dict, actions: list, env_keys: list | None) -> str:
    """NOOD_0233 — the one sentence a blocker needs when the probe hit a
    login wall the goal never declared: what the wall is, and whether
    credentials that would walk it already exist in the workspace.

    Empty when there is no wall evidence, or when the goal already carries a
    login prelude — there the walk itself reports its own failure
    (do_failed / near-miss blockers name the exact step), and re-coaching
    "add a login step" over steps that exist would send the repair the wrong
    way. Credentials are NAMED, never used: walking a wall the user never
    asked to log into would author a feature with no login steps — a test
    that cannot reach at run time the very state its evidence came from."""
    wall = pg.get("login_wall")
    if not isinstance(wall, dict) or _gate_prefix(actions):
        return ""
    fields = ", ".join(str(f) for f in (wall.get("fields") or [])[:4]) \
        or "a password field"
    creds = sorted(k for k in (env_keys or [])
                   if _CRED_KEY_RE.search(str(k).upper()))[:4]
    hint = (" — workspace credentials that could walk it exist ("
            + ", ".join(creds) + "; reference as {env:KEY})" if creds
            else " — no workspace credentials found (pass secret_values)")
    return (f" — the page is a login wall ({fields}); this flow never logs "
            "in. Add the login prelude — enter <user>, enter <password>, "
            'click <submit> — or say `log in as "<user>" with password '
            '"<pass>"` in a prompt' + hint)


def _rejection_clause(pg: dict) -> str:
    """NOOD_0233 — when the probe walked the login with the goal's own
    credentials and the page refused, the blocker carries the page's OWN
    words. Without this the reader is sent at the search box ("no search box
    found") or at the login steps (the wall) — both fine; the defect is the
    credential VALUES, and only the page's rejection message says so.
    Empty unless a rejection was captured (undeclared walls are never
    walked, so the two clauses cannot both fire)."""
    wall = pg.get("login_wall")
    if not isinstance(wall, dict) or not wall.get("rejection"):
        return ""
    n = pg.get("gate_attempts") or 1
    return (f" — the login walk ran {n}× with the goal's own credentials "
            f'and the page rejected it: "{wall["rejection"]}" — fix the '
            "credential values, not the search")


def _runtime_gate(actions: list) -> int | None:
    """Index of the first action whose effect the probe does NOT perform:
    enter/select values are never typed, and everything AFTER a pick runs on
    the landed page the probe only snapshots (NOOD_0156 — the add-to-cart
    click itself would mutate state, so the probe never performs it). Every
    check anchored at or after the gate is runtime-asserted (proven by the
    run), never claimed probe-proven.

    NOOD_0195 — `suggest` LEFT this list. It was here because the probe typed
    the term, read the list and closed it without clicking through
    (NOOD_0141); probe_args now passes `follow`, so the probe lands on the
    suggestion's own results page — the same position `search` leaves it in,
    and `search` never gated. While it did gate, every check after a suggest
    was silently routed to runtime-asserted: never proven, never blocked,
    never eligible for the literal upgrade. That is a `ready: true` that
    checked nothing. A follow that finds no matching row sets
    `suggest_warning`, which blocks below before any check is evaluated.

    NOOD_0188 — the new form/navigation verbs join it on the same rule: every
    one of them either writes state (check/uncheck/upload/pick_date), can
    submit (press_key) or moves the page out from under the snapshot
    (go_back). `hover` is deliberately NOT here — like a reveal click it only
    exposes controls, which is exactly what the probe is for."""
    for i, a in enumerate(actions):
        if a.get("do") in ("enter", "select", "pick", "add_to",
                           "check", "uncheck", "upload", "press_key",
                           "pick_date", "go_back"):
            return i
    return None


# NOOD_0156 — evidence-bound result selection. Pure: the probe calls this to
# decide WHICH result to click, and the evidence pass records the same caption
# as the bound target — one rule, no drift.

# NOOD_0200 — actions the probe's own transaction carries the page through, so
# the snapshot it ends on IS that action's page. Everything else at or after the
# runtime gate is performed only by the run.
_PROBE_FOLLOWS = ("search", "suggest", "pick", "hover")


def _beyond_probe_reach(actions: list, performed: bool = False) -> bool:
    """True when the goal's actions carry the page past the last state the
    probe snapshotted — so an END-state check has no probe evidence at all and
    belongs to the run, not to `blocking` and not to `proven`.

    Domain-agnostic by construction: it reads the action grammar only. A goal
    that just searches (or follows a suggestion, or picks a result) keeps its
    pre-run verification untouched — the probe really did land on that page.

    NOOD_0208 — `performed` (probe.perform) makes this false by construction:
    the probe executed those very actions and snapshotted where they landed,
    so there is no "past the probe's reach" left to route around. Without this
    the opt-in would collect the destination evidence and then decline to use
    it, which is the whole lap it exists to remove."""
    if performed:
        return False
    gate = _runtime_gate(actions)
    if gate is None:
        return False
    return any(a.get("do") not in _PROBE_FOLLOWS for a in actions[gate:])


# NOOD_0229 — verbs that can carry the page out from under the landing
# snapshot. `enter`/`select`/`check`/`hover` fill or reveal in place; the rest
# submit, follow or navigate, so a check written for the landing page and left
# unanchored will be asserted somewhere else entirely.
_PAGE_CHANGING = ("click", "pick", "add_to", "search", "suggest",
                  "press_key", "go_back")


def _unanchored_landing_warning(check: dict, actions: list,
                                landing_scope: list,
                                performed_scope: list) -> str | None:
    """Fix 1.1 — the split-the-test trap, named before it costs a lap.

    An unanchored check is evaluated at the END state (NOOD_0158). When its
    text is one the probe read off the LANDING page and the goal's own actions
    navigate away from it, that check is about a page the compiled step will
    never be on. The reviewed session hit exactly this, read it as "a
    landing-page assertion cannot live in this scenario", and authored a
    second feature to host it — two features for one ordered user flow, and
    the logo it verified was never proven to be present in the run that
    ordered the item.

    Silent by design when the check is anchored, when the probe walked the
    flow (probe.perform) and found the same text downstream — there the end
    state really does show it — and when the goal takes fewer than two
    page-changing actions. That last bound is what keeps this a signal rather
    than a lecture: ONE click or mutation may well leave the page where it
    was (and the engine already routes such a check to the run), while a
    CHAIN of them is a multi-page flow by construction, which is the only
    shape where a landing-page text asserted at the end is unambiguously
    wrong.
    """
    if check.get("after") is not None:
        return None
    kind = "see" if "see" in check else ("any_of" if "any_of" in check else None)
    if kind is None:
        return None
    if sum(1 for a in actions
           if isinstance(a, dict) and a.get("do") in _PAGE_CHANGING) < 2:
        return None
    alts = [check["see"]] if kind == "see" else list(check["any_of"])
    on_landing = [t for t in alts if _find_text(t, landing_scope) is not None]
    if not on_landing:
        return None
    if performed_scope and any(_find_text(t, performed_scope) is not None
                               for t in alts):
        return None          # the walked end state shows it too — no trap
    ids = [a["id"] for a in actions
           if isinstance(a, dict) and a.get("id") is not None]
    return (f'check "{on_landing[0]}" is unanchored → asserted at the END '
            "state, but the probe read that text on the LANDING page. Anchor "
            "it to the page it belongs to: `after: start` (landing) or "
            "`after: <action id>` (mid-flow"
            + (f", e.g. {ids[0]!r}" if ids else "; give the action an `id:`")
            + "). One goal can carry checks on several pages — this is not a "
              "reason to author a second feature.")


def bind_result(controls: list[dict], term: str,
                target: str | None = None,
                items: list[dict] | None = None) -> tuple[dict | None, str | None]:
    """(control, why_not) — bind a generic "pick a result" request to ONE
    concrete result control from the post-search collection.

    NOOD_0156 (intent contract v2) — when the probe extracted structured
    `result_items`, binding is STRUCTURAL: membership in the search-result
    region is the provenance, so a valid result (a branded doll, game, or
    truck) binds even when its caption never repeats the generic query word.
    `first_actionable` preserves DOM order and prefers the first item that
    carries a card-scoped action; a named target filters by caption. The
    legacy flat-control path below keeps the lexical term match — without
    region structure it is the only provenance available.

    Deterministic either way. Anything weaker returns (None, reason) — a
    block, never a guess at a non-item control."""
    if items:
        gn = _norm("" if target in (None, "*") else target)
        stable = [it for it in items
                  if _norm(it.get("caption")) and it.get("selector")]
        if gn:
            stable = [it for it in stable
                      if any(t in _norm(it["caption"]) for t in gn.split())]
            if not stable:
                return None, (f"no result item caption matches {target!r} — "
                              "name one of the probed result captions exactly")
        if not stable:
            return None, ("result items were collected but none carries a "
                          "stable caption + selector — cannot bind a "
                          "deterministic result")
        # first_actionable: DOM order, preferring an item whose card already
        # proves an action path; plain first stable item otherwise.
        cand = next((it for it in stable if it.get("actions")), stable[0])
        ctrl = {"name": cand["caption"], "selector": cand["selector"],
                "kind": "link"}
        if cand.get("actions"):
            ctrl["actions"] = cand["actions"]
        return ctrl, None
    tn = _norm(term)
    gn = _norm("" if target in (None, "*") else target)
    counts: dict[str, int] = {}
    for c in controls or []:
        n = _norm(c.get("name"))
        if n:
            counts[n] = counts.get(n, 0) + 1
    matched, ambiguous = [], 0
    for c in controls or []:
        n = _norm(c.get("name"))
        if not n or c.get("kind") not in ("link", "button"):
            continue
        hit = (tn and tn in n) or (gn and any(t in n for t in gn.split()))
        if not hit:
            continue
        if not c.get("selector") or counts[n] > 1:
            ambiguous += 1
            continue
        matched.append(c)
    if matched:
        return matched[0], None
    if ambiguous:
        return None, (f"{ambiguous} matching result caption(s) lack a unique "
                      "stable caption+selector — cannot bind a deterministic "
                      "result; refine the term or name the exact caption")
    return None, ("no probed search-result caption matches the term"
                  + (f" or {target!r}" if gn else "")
                  + " — cannot bind a concrete result to pick")


def _mutating_name(name: str) -> bool:
    """The probe's locale-aware mutating-verb gate, imported lazily so this
    module stays importable without the web stack."""
    try:
        from noodle.agents.web.probe import _is_mutating
    except Exception:                                    # pragma: no cover
        return False
    return _is_mutating(name)


def navigation_shaped(c: dict) -> bool:
    """NOOD_0208 — this control qualified as a mutation ONLY by its name, and
    it is a plain link to somewhere else.

    The trap: a landing page carries a CTA literally named "add to <dest>"
    that merely NAVIGATES to the page where you add it. `mutation_control`
    accepts it (the name matches the mutating-verb gate), the compiler emits
    the click, and the run fails one step later on an item that was never
    added — with the blame landing on the next control.

    Deliberately advisory, never a block on its own: plenty of real
    mutate-controls are anchors that POST server-side, and NOOD_0207's lesson
    is that a heuristic must not turn a working green into a refusal. It
    demotes such a candidate behind any button/submit sibling, and names the
    risk plus the opt-in (`probe: {perform: true}`) that settles it by
    clicking."""
    href = str(c.get("href") or "").strip()
    return (c.get("kind") == "link" and not c.get("submit")
            and bool(href) and not href.startswith(("#", "javascript:")))


# NOOD_0212 — a card's own action button is already proven to act on ONE item,
# so it need not spell the destination the way a flat page control must: retail
# grids label the control "Add" (or "+") and put the word "cart" nowhere near
# it. _MUTATING_RE is the WRONG gate here — it matches "remove", "delete" and
# "save", which are precisely the sibling card actions that must never bind as
# an add. This one is additive-only, with the destructive/deferral siblings
# named explicitly so a locale addition can't quietly let one through.
_ADDITIVE_ITEM_RE = re.compile(
    r"(?:^|\b)(add|\+|buy|purchase|"
    r"hinzuf\w*|kaufen|ajouter|acheter|a[ñn]adir|agregar|comprar|"
    r"aggiungi|acquista|adicionar|toevoegen|kopen)(?:\b|$)", re.I)
_ITEM_ACTION_EXCLUDE_RE = re.compile(
    r"\b(remove|delete|save|wish\s?list|favou?rite|compare|later|share|"
    r"quick\s?view|entfernen|l[öo]schen|supprimer|eliminar|rimuovi)\b", re.I)


def item_actions(blk: dict) -> list[dict]:
    """NOOD_0212 — the probe stores a card's own buttons in
    `result_items[].actions[]`, NEVER in `blk["controls"]`.

    NOOD_0195 fixed exactly this shape for text (captions live in
    `result_items` too, and a `see:` naming a real product hard-blocked while
    the probe held the caption all along). The mutation prover was still
    reading `controls` only, so a results page whose every card carries an
    "Add" button reported "no probed control mutates into 'cart'" — and the
    reader burned laps probing for a name that was in the payload the whole
    time. `--find add` printed it as `[result-item-action]`; this is the same
    list, handed to the prover."""
    out = []
    for it in blk.get("result_items") or []:
        for a in it.get("actions") or []:
            if not a.get("name"):
                continue
            out.append({**a, "item_caption": it.get("caption"),
                        "origin": "result-item-action"})
    return out


def block_mutation_candidates(blk: dict) -> list[dict]:
    """Everything on one probed block that could perform a mutation: the flat
    controls plus each card's own actions. One list, so the prover and the
    "the page offers:" hint can never again disagree about what was seen."""
    return list(blk.get("controls") or []) + item_actions(blk)


def _additive_item_action(c: dict) -> bool:
    n = c.get("name") or ""
    return bool(_ADDITIVE_ITEM_RE.search(n)) \
        and not _ITEM_ACTION_EXCLUDE_RE.search(n)


def mutation_control(controls: list[dict], destination: str,
                     scoped: bool = False) \
        -> tuple[dict | None, str | None]:
    """(control, why_not) — THE probed control that performs the requested
    mutation into `destination` ("add to cart"-shaped). One rule shared by
    the probe's mutation proof and the evidence pass — no drift. A candidate
    must name the destination PLUS more (a bare "cart" control opens the
    destination, it doesn't mutate into it) and be mutation-shaped (button /
    submit / a locale-aware mutating verb). A few same-named visible
    instances are responsive duplicates of one control — first visible
    binds (NOOD_0168); MANY distinct instances are one-per-card — block,
    never pick one."""
    dn = _norm(destination)
    if not dn:
        return None, "no destination named"
    cands = []
    for c in controls or []:
        if c.get("origin") == "result-item-action":
            continue          # NOOD_0212 — considered below, as a fallback
        n = _norm(c.get("name"))
        if not n or dn not in n or n == dn:
            continue
        if c.get("kind") == "button" or c.get("submit") \
                or _mutating_name(c.get("name", "")):
            cands.append(c)
    if not cands:
        # NOOD_0212 — no flat control names the destination; fall back to the
        # cards' own actions. Deliberately a FALLBACK, not a peer: where a
        # page carries a real "Add to cart" control the existing binding must
        # not change, and a card action can never introduce a new ambiguity
        # into a page that already resolved. The destination word is not
        # required here — being an action ON a result item is the proof that
        # a flat control has to earn by naming what it acts on.
        cands = [c for c in controls or []
                 if c.get("origin") == "result-item-action"
                 and _additive_item_action(c)]
    if not cands:
        return None, (f"no probed control mutates into {destination!r} "
                      "(nothing names the destination beyond opening it)")
    # NOOD_0208 — a button/submit sibling outranks a navigation-shaped link
    # naming the same thing. Order only: when the link is the ONLY candidate
    # it still wins, and evidence() warns instead of refusing.
    if any(not navigation_shaped(c) for c in cands):
        cands = [c for c in cands if not navigation_shaped(c)]
    names = {_norm(c.get("name")) for c in cands}
    if len(names) > 1:
        # NOOD_0207 — a label that STARTS with the destination word names it
        # ("cart now" navigates to the cart); one that mentions it after a
        # verb acts on it ("add … to cart"). Position is the discriminator —
        # _is_mutating on the name with the destination stripped returns
        # False for both, so it cannot separate them.
        acting = {n for n in names if not n.startswith(dn)}
        if len(acting) == 1:
            names = acting
            cands = [c for c in cands if _norm(c.get("name")) in acting]
    if len(names) > 1:
        return None, ("ambiguous — several distinct probed controls could "
                      "perform the mutation: "
                      + ", ".join(sorted(names)[:4])
                      + "; name the exact control")
    sels = {c.get("selector") for c in cands if c.get("selector")}
    if scoped and cands:
        # NOOD_0207 — the searchless shape. `within` already says WHICH
        # instance, so a grid of N same-named controls is the expected shape,
        # not a block: the run scopes the click to the anchored card. Bind
        # the first visible instance for its selector/name only; the compiled
        # step never uses that selector (compile_goal skips the POM entry
        # precisely because it matches every card).
        return next((c for c in cands if c.get("visible")), cands[0]), None
    if len(sels) > 1:
        # NOOD_0168 — responsive pages render the same-named buy control
        # more than once (buy box + sticky bar, often BOTH visible). After a
        # pick the landed page holds ONE item, so same-named duplicates
        # perform the SAME mutation — bind the first visible instance. A
        # wrong bind cannot survive: the probe clicks it to prove the
        # before/after delta, and item_in_destination must still find the
        # picked caption in the destination. Many distinct instances is a
        # card GRID (one per item), not a duplicate — that stays a block.
        vis = {c["selector"]: c for c in cands
               if c.get("visible") and c.get("selector")}
        if vis and len(vis) <= 3:
            return next(iter(vis.values())), None
        return None, (f"{len(cands)} probed instances share the mutation "
                      "control name — scope the mutation to one concrete "
                      'item/card: add within: "<text unique to that '
                      'item\'s card/row>"')
    return cands[0], None


# --- evidence ----------------------------------------------------------------

def _block_texts(blk: dict) -> list[str]:
    texts = list(blk.get("headings", []))
    for c in blk.get("controls", []):
        if c.get("name"):
            texts.append(c["name"])
    # NOOD_0195 — search-result captions. A results page keeps its product
    # titles in structured `result_items`, never in headings or control names,
    # so a `see`/`any_of` naming a real product hard-blocked with "no probed
    # heading or control shows that text" while the probe was holding the
    # caption all along. Verified live on a retail site: both requested
    # products were in result_items, neither was reachable from here.
    # (Captions are truncated to ~60 chars by the probe — _find_text's reverse
    # direction still matches longer titles, and since NOOD_0197 an `any_of`
    # compiles to one disjunctive step regardless, so truncation costs nothing.)
    for it in blk.get("result_items") or []:
        if it.get("caption"):
            texts.append(it["caption"])
    return texts


def _page_blocks(pg: dict) -> list[tuple[dict, str, str | None]]:
    """Provenance-tagged blocks of one probed page: (block, phase, trigger).

    phase is 'initial' | 'reveal' | 'discovered' | 'performed' | 'gate' |
    'search'. A revealed block the probe reached by AUTOMATIC discovery
    (auto/discovered) is 'discovered' — its controls are never reachable
    without an explicit goal click that opens them. An explicitly-clicked
    reveal keeps phase 'reveal' and carries the trigger name so the compiler
    can require that click first.

    NOOD_0208 — 'performed' is the state the probe WALKED to by executing the
    goal's own post-gate actions (probe.perform). It carries no trigger: it is
    reachable because those actions are in the test, so requiring a reveal
    click for it would block the very flow that produced it.

    NOOD_0233 — 'gate' is the state the LOGIN-GATE walk drove through
    (rescue-only, no probe.perform). Deliberately its own phase so evidence()
    can exclude it wholesale: the walk exists to carry the browser to the
    search box, not to widen what may be called probe-proven — an
    `after: start` check proving against a post-login snapshot would be the
    NOOD_0195 failure ("a ready: true that checked nothing") wearing a new
    hat."""
    blocks: list[tuple[dict, str, str | None]] = [(pg, "initial", None)]
    for rev in pg.get("revealed", []):
        if rev.get("gate_walk"):
            blocks.append((rev, "gate", None))
            continue
        if rev.get("performed"):
            blocks.append((rev, "performed", None))
            continue
        phase = "discovered" if (rev.get("discovered") or rev.get("auto"))\
            else "reveal"
        blocks.append((rev, phase, rev.get("revealed_by")))
    if pg.get("search"):
        blocks.append((pg["search"], "search", None))
        # NOOD_0156 — the page the probe's bound result-pick landed on:
        # reachable at run time only after the pick action re-clicks it.
        if pg["search"].get("picked"):
            blocks.append((pg["search"]["picked"], "picked", None))
    return blocks


def _reveal_click_before(actions: list, action: dict, trigger: str | None) -> bool:
    """True when an explicit click action targeting `trigger` precedes
    `action` — the prerequisite that legitimately makes a revealed control
    reachable at run time. NOOD_0188 — a `hover` counts too: hover menus are
    the other way a control legitimately becomes reachable, and treating them
    as unreachable blocked the exact flow NOOD_0186 added steps for."""
    tn = _norm(trigger)
    if not tn:
        return False
    for x in actions:
        if x is action:
            return False
        if x.get("do") in ("click", "hover"):
            xn = _norm(x.get("target"))
            if xn and (xn == tn or tn in xn or xn in tn):
                return True
    return False


# NOOD_0145 — a login/submit-shaped intent may fall back to THE unique visible
# submit control. Matched against the normalized target only; deliberately
# narrow (no "continue"/"next" — those name non-submitting controls too often).
_SUBMIT_INTENT_RE = re.compile(r"\b(log ?in|sign ?in|sign ?up|register|submit)\b")


def _auth_synonyms(target: str) -> list[str]:
    """The runtime's own auth-verb synonyms ("login" → "sign in"), so goal
    matching and run-time healing agree on what a login intent may resolve to.
    Imported lazily to keep this module importable without the web stack."""
    try:
        from noodle.agents.web.locator import _synonym_candidates
    except Exception:                                    # pragma: no cover
        return []
    return _synonym_candidates(target)


# NOOD_0207 — how many same-named instances still count as ONE control. A
# responsive page renders its header twice (desktop bar + collapsed menu), and
# NOOD_0168 already settled the rule for the mutation path: a FEW same-named
# visible instances are duplicates of one control, MANY are one-per-card. This
# is that ceiling, shared — without it the ambiguity gate below blocked an
# ordinary two-instance header link that resolves and runs green (the
# benchmark's tc2 caught exactly that).
_DUPLICATE_CEILING = 3


def _iter_controls(blocks: list):
    for blk, phase, trigger in blocks:
        for c in blk.get("controls", []):
            yield c, phase, trigger


def _contains(t: str, cn: str) -> bool:
    """NOOD_0209 — guarded bidirectional containment for substring matching.

    Bare `t in cn or cn in t` let a control named "e" match nearly every
    target and let "edit" match "credit" (a substring of a word is not that
    word): the resulting ambiguity blockers named controls with nothing to do
    with the request, and each one cost the calling agent a rewrite lap. Two
    guards: a 1-2 character name carries no intent, and a containment must
    land on word boundaries — in BOTH directions, so a "cart" target still
    meets an "add to cart" control. Exact-name matching stays ungated
    upstream: a control genuinely named "OK" still matches "OK"."""
    if not cn or len(cn) < 3:
        return False
    return bool(re.search(rf"\b{re.escape(t)}\b", cn)
                or re.search(rf"\b{re.escape(cn)}\b", t))


def _closest_first(target: str, names: list[str]) -> list[str]:
    """NOOD_0209 — rank candidate names by closeness to the target before any
    list is truncated for display; an arbitrary cut used to lead the blocker
    with the least likely candidate."""
    return sorted(names, key=lambda n: difflib.SequenceMatcher(
        None, (target or "").casefold(), (n or "").casefold()).ratio(),
        reverse=True)


def _near_miss(target: str, blocks: list, kind: str = "control") -> str:
    """NOOD_0207 — ' — did you mean "X"? (probed here: …)', or ''.

    Every unmatched-target blocker was holding the probed vocabulary that
    fixes it and shipped only the problem statement, so the repair was a
    re-probe instead of a one-word edit. The `suggest:` branch already did
    exactly this for typeahead options; this is that pattern, everywhere.

    ponytail: difflib over the probed names, cap 8 — the names ride the
    payload, and a shortlist longer than that is a probe dump, not a hint."""
    # accepts either shape a caller holds: provenance tuples (_page_blocks)
    # or plain block dicts (the check scopes).
    plain = [b[0] if isinstance(b, tuple) else b for b in blocks or ()]
    names = list(dict.fromkeys(
        n for blk in plain for n in _block_texts(blk) if n))
    if not names:
        return ""
    ranked = _closest_first(target, names)
    near = difflib.get_close_matches(target or "", names, 1, 0.6)
    shown = ", ".join(f'"{n}"' for n in ranked[:8])
    return ((f' — did you mean "{near[0]}"?' if near else "")
            + f" (probed {kind}s here: {shown}"
            + (f", +{len(ranked) - 8} more" if len(ranked) > 8 else "") + ")")


def _do_label_target(label: str) -> str:
    """The normalized target inside a probe `_do_label` string (NOOD_0227) —
    "do: click X in row containing Y" → norm("X"). The join key between a
    halted do-chain's structured failure and the goal action it belongs to."""
    s = re.sub(r"^do: (?:click|enter|select|switch to) ", "", label or "")
    s = re.sub(r" in row containing .*$", "", s)
    return _norm(s)


def _do_fail_clause(do_failed: dict, pg: dict) -> str:
    """NOOD_0227 (B3) — the halted probe transaction in one clause: the
    error, how much of the chain landed first, and what never ran. Attached
    to the failing action's blocker so "control not found" is never read as
    a naming problem when the real cause is an execution failure."""
    if not do_failed:
        return ""
    err = " ".join(str(do_failed.get("error", "")).split())[:200]
    done, req = pg.get("do_completed", 0), pg.get("do_requested", 0)
    skipped = do_failed.get("skipped") or []
    return (f" — the probe transaction halted at this step ({done} of {req} "
            f"action(s) completed): {err}"
            + (f"; never ran: {', '.join(skipped[:4])}" if skipped else ""))


def _searched_clause(blocks: list) -> str:
    """NOOD_0227 (B5) — WHERE a failed lookup searched. A multi-page
    traversal's vocabulary spans every walked state; without page names a
    blocker reads as "the app doesn't have it" when the truth may be "it is
    on a page the chain never reached"."""
    plain = [b[0] if isinstance(b, tuple) else b for b in blocks or ()]
    urls = list(dict.fromkeys(str(b.get("url")).split("?")[0]
                              for b in plain if b.get("url")))
    if len(plain) <= 1 or not urls:
        return ""
    where = (f", ending on {urls[-1]}" if len(urls) > 1
             else f" of {urls[0]}")
    return f" — searched {len(plain)} probed page-state(s){where}"


# NOOD_0226 — deliberately narrow: an attribute selector, or an id selector
# that cannot be read as a caption. Bare class selectors are NOT here — ".NET
# SDK" and "#1 Best Seller" are control names on real pages, and treating
# either as a selector would block a legitimate target.
_SELECTOR_SHAPED = re.compile(r'^(\[.+\]|#[A-Za-z_][\w-]*)$')


def _locate(target: str, blocks: list, scoped: bool = False,
            pinned: bool = False) \
        -> tuple[dict | None, str | None, str | None, str | None]:
    """(control, phase, trigger, blocking_note) for a goal action target.

    `scoped` — the action carries a `within:` anchor (NOOD_0207), so a
    repeated control is exactly what it means to address; the ambiguity gate
    below is what `within:` is the answer to, and must not fire on it.

    `pinned` — the caller pinned this target in pom_content (NOOD_0212), the
    other way to settle ambiguity; every gate is answered by the selector.

    NOOD_0145 — deterministic match order, replacing first-substring-wins
    (which picked a machine-named lookalike, e.g. "login options toggle btn",
    over the visible "sign in" submit control for a generic "login" target):

      1. exact canonical name — UNLESS the probe captured several distinct
         controls sharing that name (NOOD_0156: repeated per-card controls
         like "Add to cart"), or PROVED one selector matches many nodes
         (NOOD_0207); an unscoped repeated control blocks instead of
         silently acting on whichever instance resolves first
      2. exact runtime auth-synonym name ("login" → "sign in")
      3. login/submit intent: THE unique visible submit control
      4. unique substring match (either direction)
      5. several distinct substring candidates → block as ambiguous (note),
         never guess the first one
    """
    t = _norm(target)
    if not t:
        return None, None, None, None
    # NOOD_0226 — a selector is an exact instruction, so resolve it as one.
    # _norm strips punctuation ('[id="add-to-cart"]' → 'id add to cart'), so
    # the substring pass below matched ANY control merely NAMED "add to cart"
    # and bound ITS selector — a different element, chosen by luck. Measured:
    # a target naming one product page's CTA id resolved to a result card's
    # button, compiled that card's selector into the POM, and ran green for
    # the wrong reason. Exact selector match or nothing.
    if _SELECTOR_SHAPED.match(target.strip()):
        want = target.strip()
        hit = next((x for x in _iter_controls(blocks)
                    if str(x[0].get("selector") or "").strip() == want), None)
        if hit:
            return (*hit, None)
        return None, None, None, (
            "no probed control carries that selector — this is a selector, "
            "not a name, so it is never matched by wording; use the visible "
            "caption the probe reported, or pin the selector in pom_content")
    exact = [(c, phase, trigger) for c, phase, trigger in
             _iter_controls(blocks) if _norm(c.get("name")) == t]
    if exact:
        # Distinct selectors = genuinely different elements sharing one name
        # (one per result card/row). The same control snapshotted twice
        # (identical selector, or no selector captured) stays a unique match.
        sels = {c.get("selector") for c, _, _ in exact if c.get("selector")}
        # NOOD_0207 — the probe already PROVED the ambiguity. A card grid
        # stores ONE representative selector per control family plus
        # `unique: False` / `matches: N` (probe.py:2212), and prints
        # "⚠ selector matches N nodes". N identical nodes collapse to one
        # selector string, so the len(sels) test above scored them a unique
        # match, compiled the POM, reached ready: true — and the run acted on
        # instance 1. Fires only on the probe's own proof, never a heuristic.
        # The ceiling is what keeps this a bug-catcher rather than a tax on
        # every responsive header: 2-3 same-named instances are one control
        # rendered twice and resolve to the same destination; more than that
        # is a per-item family, where instance 1 is a coin flip.
        amb = next((c for c, _, _ in exact if c.get("unique") is False
                    and (c.get("matches") or 0) > _DUPLICATE_CEILING), None)
        if pinned:
            return (*exact[0], None)
        if scoped:
            # NOOD_0222 — the structurally-different gate below exists
            # precisely to say "a within: anchor will not resolve at run
            # time" (NOOD_0209) — but supplying within: skipped it, so the
            # exact goal it warns against authored ready:true and died in
            # the run as "No row containing '<text>' found": a red run per
            # instance of the most expensive ordering there is. Same page,
            # same evidence, decided at author time instead.
            shapes = {re.sub(r"\d+", "N", s) for s in sels}
            if amb is None and len(shapes) > 1:
                return None, None, None, (
                    f"matches {len(exact)} structurally different probed "
                    "controls sharing this exact name (distinct selectors) — "
                    "these are not one control repeated per row/card, so the "
                    "within: anchor will not resolve at run time; drop "
                    "within: and pin the intended instance via pom_content "
                    "(its selector answers \"which one\")")
            return (*exact[0], None)
        if len(sels) > 1 or amb is not None:
            # NOOD_0212 — same NAME and same DESTINATION is not an ambiguity.
            # The ceiling comment above already states the principle ("one
            # control rendered twice ... resolve to the same destination");
            # for links the probe captured the href, so it can be CHECKED
            # rather than assumed from a count. A site whose header renders
            # its nav once for desktop and once for the collapsed menu hit
            # this on every such link, and the advice it printed ("name the
            # instance by nearby unique text") has no answer when both
            # instances sit in chrome with no nearby text of their own.
            # Distinct hrefs stay a block: those really are different pages.
            hrefs = {str(c.get("href") or "").strip() for c, _, _ in exact}
            if (amb is None and len(hrefs) == 1 and "" not in hrefs
                    and all(c.get("kind") == "link" for c, _, _ in exact)):
                c, ph, tr = next((x for x in exact if x[0].get("visible")),
                                 exact[0])
                return ({**c, "collapsed_from": len(exact)}, ph, tr, None)
            n = len(exact) if len(sels) > 1 else amb["matches"]
            # NOOD_0209 — tell the two repeated shapes apart before advising.
            # A per-item FAMILY (one selector shape stamped once per card, or
            # the probe's own unique:False proof) is what `within:` answers.
            # Structurally DIFFERENT controls sharing a label (a page-level
            # control plus per-section ones — distinct selector shapes) are
            # not in rows: `within:` advice there authored ready:true and the
            # run died with "No row containing '<text>' found".
            shapes = {re.sub(r"\d+", "N", s) for s in sels}
            if amb is None and len(shapes) > 1:
                return None, None, None, (
                    f"matches {n} structurally different probed controls "
                    "sharing this exact name (distinct selectors) — these "
                    "are not one control repeated per row/card, so a "
                    "within: anchor will not resolve at run time; instead, "
                    "name the instance by nearby unique text or probe the "
                    "page (--discover) and use the distinguishing control")
            return None, None, None, (
                f"matches {n} probed controls sharing this exact "
                "name — a repeated control; scope the action to one concrete "
                'instance: add within: "<text unique to the intended '
                'row/card>"')
        return (*exact[0], None)
    for alt in _auth_synonyms(t):
        an = _norm(alt)
        for c, phase, trigger in _iter_controls(blocks):
            if _norm(c.get("name")) == an:
                return c, phase, trigger, None
    if _SUBMIT_INTENT_RE.search(t):
        submits = [(c, ph, tr) for c, ph, tr in _iter_controls(blocks)
                   if c.get("submit") and c.get("visible", True)]
        if len({_norm(c.get("name")) for c, _, _ in submits}) == 1:
            return (*submits[0], None)
    subs, names = [], []
    for c, phase, trigger in _iter_controls(blocks):
        cn = _norm(c.get("name"))
        if _contains(t, cn):                     # NOOD_0209 — guarded
            subs.append((c, phase, trigger))
            if cn not in names:
                names.append(cn)
    if len(names) == 1:
        return (*subs[0], None)
    if len(names) > 1:
        # NOOD_0200 — name the candidate it would have picked and why it
        # won't: "ambiguous" alone cost the reviewed session a full
        # round-trip guessing which spelling the engine wanted.
        # NOOD_0209 — likeliest first: the truncated list is what the calling
        # agent acts on, and an arbitrary cut misdirected the repair.
        ranked = _closest_first(t, names)
        return None, None, None, (
            "ambiguous — matches " + ", ".join(f'"{n}"' for n in ranked[:4])
            + ("…" if len(ranked) > 4 else "")
            + f'; not guessing between them — if you meant "{ranked[0]}", '
            "say exactly that, otherwise use the exact probed control name")
    return None, None, None, None


def _observed_count(rsum: dict) -> int | None:
    """The number the results-summary asserts, from its parsed `count` or, as a
    fallback, the first number in its text (NOOD_0141 — US and European
    formats). None when neither yields one."""
    n = rsum.get("count")
    if isinstance(n, int) and not isinstance(n, bool):
        return n
    from noodle.agents.web.probe import parse_number
    parsed = parse_number(str(rsum.get("text") or ""))
    return int(parsed) if parsed is not None else None


def _find_text(needle: str, blocks: list[dict]) -> str | None:
    """The probed page shows the requested text — either in full (`n in tn`),
    or as a shortened rendering of it (`tn in n`, a truncated caption).

    NOOD_0195 — the reverse direction needs a floor. Bare character
    containment let a control literally named "a" satisfy a 41-character
    product title, and evidence() recorded that as proven: a live goal reached
    `ready: true` on a one-letter match. A fragment must be at least two whole
    words of the needle; anything a single word long is noise, and a
    single-word needle is provable by the forward direction anyway."""
    n = _norm(needle)
    if not n:
        return None
    for blk in blocks:
        for t in _block_texts(blk):
            tn = _norm(t)
            if n in tn or (tn in n and len(tn.split()) >= 2):
                return t
    return None


def _shared_phrase(needle: str, blocks: list[dict],
                   floor: int = 2) -> tuple[str, str] | None:
    """NOOD_0199 — (longest run of the needle a probed text contains, that
    text), or None. The decorated-text case: a human asks to verify "Weekly
    Flyer image" and the page renders "Banner 2 of 8 View the Weekly Flyer
    now." Neither contains the other, so `_find_text` blocks — on a page that
    demonstrably shows what was asked about. This is the "assert the smallest
    stable substring" rule the workspace already tells authors to apply, done
    by the engine WITH provenance instead of refusing.

    ponytail: contiguous word runs, longest first, floor of two whole words —
    the same floor `_find_text` uses against one-letter matches. Word-level
    fuzzy matching (stemming, edit distance) is the upgrade path if real
    prompts turn out to need it; nothing measured has."""
    words = (needle or "").split()
    if len(words) <= floor:
        return None                # a one/two-word ask has no shorter form
    texts = [(t, _norm(t)) for blk in blocks for t in _block_texts(blk)]
    for size in range(len(words) - 1, floor - 1, -1):
        for start in range(len(words) - size + 1):
            run = " ".join(words[start:start + size])
            rn = _norm(run)
            if not rn:
                continue
            for t, tn in texts:
                if rn in tn:
                    return run, t
    return None


def _find_control(target: str, blocks: list[dict]) -> dict | None:
    t = _norm(target)
    for blk in blocks:
        for c in blk.get("controls", []):
            cn = _norm(c.get("name"))
            if t and (t == cn or t in cn or cn in t):
                return c
    return None


def _check_scope(check: dict, goal: dict) -> str:
    """'search' when the check observes the page the search/suggest landed on,
    else 'initial'.

    NOOD_0195 — `suggest` counts, not just `search`: probe_args now follows the
    suggestion, so that page is real evidence. And an UNANCHORED check scopes
    there too: NOOD_0158 made it assert the END state and compile_goal emits it
    after every action, but the evidence pass still matched it against the
    landing page — so it was checked against one page and run against another.
    An `after:` anchor before the search still scopes to the landing page,
    which is what `after: start` is for."""
    actions = goal.get("actions") or []
    search_i = next((i for i, a in enumerate(actions)
                     if a["do"] in ("search", "suggest")), None)
    if search_i is None:
        return "initial"
    after = check.get("after")
    if after is None:
        return "search"
    anchor_i = next((i for i, a in enumerate(actions)
                     if a.get("id") == after), -1)
    return "search" if anchor_i >= search_i else "initial"


def evidence(goal: dict, probe_result: dict, pinned=frozenset(),
             env_keys: list | None = None) -> dict:
    """Match every requested action/check against what the probe proved.

    `pinned` — normalized POM keys the caller supplied in pom_content. A
    pinned target has already been disambiguated by hand, so the ambiguity
    gate stands down for it (NOOD_0212).
    `env_keys` (NOOD_0233) — KEY NAMES declared by the workspace/app env
    files (never values, never os.environ), so an undeclared login wall can
    say whether credentials that would walk it already exist.
    Returns {blocking, proven, runtime_asserted, permission_prompts,
    popups_closed, results_summary, controls}. An unproven request BLOCKS — it
    is never dropped or broadened — EXCEPT a check anchored after data the probe
    never entered, which becomes a `runtime_asserted` check the run must pass."""
    def _empty(blocking: list[str]) -> dict:
        return {"blocking": blocking,
                "proven": {}, "runtime_asserted": [], "permission_prompts": [],
                "popups_closed": 0, "results_summary": None, "controls": {},
                "bound_targets": {}, "resolved_controls": {},
                "mutation_plans": {}, "navigation_health": [],
                "revealed_headings": {}, "headings": [], "narrowed": [],
                "warnings": []}

    pages = probe_result.get("pages") or []
    if not pages:
        errs = "; ".join(e.get("error", "?") for e in
                         probe_result.get("errors", [])) or "no pages probed"
        return _empty([f"probe returned no page evidence: {errs}"])
    # NOOD_0156 — ordered navigation contract: every requested URL must have
    # loaded, and the goal's actions/checks are proven against the LAST page
    # (the one the flow acts on). A dropped URL blocks before any authoring.
    nav = navigation_urls(goal)
    nav_health: list[dict] = []
    nav_block = None
    if nav:
        if len(pages) < len(nav):
            errs = "; ".join(f'{e.get("url", "?")}: {e.get("error", "?")}'
                             for e in probe_result.get("errors", []))
            return _empty([
                f"navigation: only {len(pages)} of {len(nav)} requested "
                "URLs loaded" + (f" — {errs}" if errs else "")])
        pg = pages[len(nav) - 1]
        # NOOD_0169 — navigation health: requested setup URLs are preserved
        # even when broken (with a warning — the user asked for them; their
        # controls never join the action page's vocabulary, which is built
        # from the LAST page only), but a broken FINAL action page blocks:
        # actions must never be authored against a 404.
        for i, (u, p) in enumerate(zip(nav, pages)):
            status = p.get("http_status")
            entry = {"url": u,
                     "role": "action" if i == len(nav) - 1 else "setup",
                     "status": status, "title": p.get("title", "")}
            if isinstance(status, int) and status >= 400:
                if entry["role"] == "setup":
                    entry["warning"] = (
                        f"setup URL returned HTTP {status} — preserved as "
                        "requested; setup-page controls never enter the "
                        "action page's vocabulary")
                else:
                    nav_block = (
                        f"navigation: the final action page returned HTTP "
                        f"{status} ({u}) — actions are never authored "
                        "against a broken page; fix the URL or app state")
            nav_health.append(entry)
    else:
        pg = pages[0]
    # NOOD_0233 — gate-walk states are dropped BEFORE any scope is built:
    # the login walk is transport, not evidence (see _page_blocks). With the
    # filter here, every consumer below — controls, scopes, _locate, the
    # reveal-trigger rules — behaves byte-identically to a probe that never
    # walked, except that pg["search"] now holds the real results page.
    blocks = [t for t in _page_blocks(pg) if t[1] != "gate"]
    actions = goal.get("actions") or []
    # NOOD_0227 — `performed` is honest to the CHAIN, not to block presence.
    # Pre-gate reveal clicks now ride the do-chain (probe_args), so a
    # performed-phase block no longer implies probe.perform walked the whole
    # flow: the flag must have been requested AND the transaction must have
    # completed (no do_failed, every requested action landed). Without this,
    # a default-path goal with one walked click would have `gate = None`'d
    # its own runtime deferrals and blocked every post-gate check against
    # pages the probe never loaded.
    perform_on = bool((goal.get("probe") or {}).get("perform"))
    do_failed = pg.get("do_failed") or {}
    chain_done = (not do_failed
                  and pg.get("do_completed", 0) == pg.get("do_requested", 0))
    performed = (perform_on and chain_done
                 and any(ph == "performed" for _, ph, _ in blocks))
    # NOOD_0227 (B3) — a halted probe transaction is first-class evidence:
    # which action failed, and which later actions therefore never ran.
    # _reveal swallowed exactly this into click_warnings and the reviewed
    # session looped on "control not found" wording while the real cause was
    # "step 2 of your chain never executed".
    failed_target = _do_label_target(do_failed.get("action", ""))
    skipped_targets = {t for t in (_do_label_target(s) for s in
                                   do_failed.get("skipped") or []) if t}
    fail_i = next((j for j, x in enumerate(actions)
                   if isinstance(x, dict)
                   and _norm(x.get("target")) == failed_target),
                  None) if do_failed else None
    # Reachable controls for the compiler — initial + explicit reveals + search
    # (+ the picked landed page, NOOD_0156). Discovered/auto blocks are
    # excluded: found by the probe clicking around, not by a requested action,
    # so never authored as reachable.
    controls = {}
    for blk, phase, _ in blocks:
        if phase == "discovered":
            continue
        for c in blk.get("controls", []):
            controls.setdefault(_norm(c.get("name")), c)
    # NOOD_0208 — 'performed' joins the check scope, and that IS the point of
    # the opt-in: those blocks are the pages the flow walks to AFTER the
    # mutation, so a check on the confirmation wording is proven here instead
    # of guessed and paid for with a red run.
    initial_scope = [blk for blk, ph, _ in blocks
                     if ph in ("initial", "reveal", "performed")]
    # NOOD_0229 (fix 1.1) — the two halves of initial_scope, kept apart for
    # the unanchored-check warning below: what the page showed BEFORE the
    # goal's own actions ran, and what it showed after.
    landing_scope = [blk for blk, ph, _ in blocks if ph in ("initial", "reveal")]
    performed_scope = [blk for blk, ph, _ in blocks if ph == "performed"]
    search_scope = [blk for blk, ph, _ in blocks if ph == "search"]
    picked_blk = next((blk for blk, ph, _ in blocks if ph == "picked"), None)
    # NOOD_0195 — the probe's --expect verdicts: an exact full-text search of
    # the page the probe ENDED on, so they only ever answer for a check that
    # observes that page (scope 'search'). Where a structured capture is lossy
    # this is the authoritative source, and a FOUND is full-render proof — the
    # string is in the rendered text, entire.
    expect_found = {_norm(e.get("text")) for e in (pg.get("expect") or [])
                    if e.get("found")}
    searched = any(a["do"] in ("search", "suggest") for a in actions)

    blocking, proven, runtime, bound, resolved = [], {}, [], {}, {}
    warnings: list[str] = []       # NOOD_0208 — real but unproven risks
    # NOOD_0227 (B3) — reveal-path click failures (search-shaped goals keep
    # probe._reveal) surface instead of dying silently in click_warnings:
    # the agent is told `blocking` IS the evidence, so the one line that
    # explains a chain that never opened must at least ride the warnings.
    for w in pg.get("click_warnings") or []:
        warnings.append(f"reveal {w}")
    # NOOD_0233 — the probe walked the goal's own login prelude to reach the
    # search box; say so, or the payload reads as if the landing page had one.
    for k in ("search_after_gate", "suggest_after_gate"):
        if pg.get(k):
            warnings.append(f"probe: {pg[k]}")
    # NOOD_0200 — provenance per proven CHECK: which probe phase's page the
    # match came from (initial / search). Recording it is the structural
    # guard against the end-state false positive: a check whose compiled
    # scope and proven phase drift apart is visible in the payload (and
    # pinned by tests) instead of shipping a green that verified nothing.
    proven_phase: dict = {}
    narrowed: list[dict] = []      # NOOD_0199 — see-checks cut to their
                                   # probe-proven substring, never silently
    mplans: dict[str, dict] = {}
    # NOOD_0226 — the last action index the probe has page evidence for; read
    # once, used by every gate below so they cannot drift apart. NOOD_0227 —
    # a performed (fully-walked) flow has no reach boundary: every page was
    # loaded, so an unmatched name there is genuinely absent and blocks.
    reach = None if performed else _reach_gate(actions)
    if nav_block:
        blocking.append(nav_block)
    for i, a in enumerate(actions):
        if a["do"] == "api":
            # NOOD_0192 — nothing for a page probe to prove: the probe drives
            # a browser, the call is HTTP. Its correctness is proven at run
            # time by the status/body assertion that must accompany it, so it
            # neither blocks authoring nor claims probe evidence.
            continue
        if a["do"] == "add_to":
            # NOOD_0156 — semantic mutation lowering: resolve the requested
            # "add the picked item to <destination>" to the exact probed
            # mutation control on the landed page — plus at most one
            # probe-PROVEN prerequisite reveal (recorded by the probe with a
            # before/after delta). No proven chain = block; a candidate
            # prerequisite is never compiled on a guess.
            aid = a.get("id") or f"add_to:{a.get('destination', '')}"
            src = a.get("item_from", "")
            scope = str(a.get("within") or "").strip()
            if scope and not src:
                # NOOD_0207 — the searchless shape: no pick, so no landed
                # page; the mutation control lives on the CURRENT page,
                # repeated once per card, and `within` says which card.
                # NOOD_0212 — cards' own actions join the flat controls, and
                # the hint below enumerates THE SAME list: it used to print
                # `blk["controls"]` in block order, so a searchless add_to on
                # a results page advertised the landing page's chrome ("skip
                # to main content", "play", "pause") as what the page offers.
                cand = [c for blk, _, _ in blocks
                        for c in block_mutation_candidates(blk)]
                ctrl, why = mutation_control(cand, a["destination"],
                                             scoped=True)
                if ctrl is None:
                    if reach is not None and i > reach:
                        # NOOD_0226 — past the probe's reach there is no
                        # mutation control to find: the page carrying it was
                        # never loaded. Saying "the page offers: username,
                        # password, login" invites a repair onto the SIGN-IN
                        # page, which is never right. `add_to` cannot defer
                        # like a click can — it lowers to a control whose name
                        # only the author knows — so it still blocks, but with
                        # the two repairs that actually work.
                        blocking.append(
                            f'add_to "{a["destination"]}": the mutation '
                            f"control is behind step {reach + 1} "
                            f'({_reach_label(actions, reach)}), which the '
                            "probe does not perform — so it was never probed "
                            "and no candidate here is one. Name the control "
                            'instead: click "<its name>" with within: '
                            f'"{scope}", or opt in with probe: {{perform: '
                            "true}} to let the probe walk the flow first")
                        continue
                    names = list(dict.fromkeys(
                        c["name"] for c in cand if c.get("name")))[:8]
                    blocking.append(
                        f'add_to "{a["destination"]}": {why}'
                        + ("; the page offers: "
                           + ", ".join(repr(n) for n in names) if names else ""))
                    continue
                if not _find_text(scope, [blk for blk, _, _ in blocks]):
                    blocking.append(
                        f'add_to within "{scope}": no probed text on this page '
                        "identifies that item"
                        + _near_miss(scope, blocks, "text"))
                    continue
                # NOOD_0208 — with probe.perform the control was CLICKED, so
                # "does it mutate or merely navigate" is answered instead of
                # assumed. A click that neither changed the page nor produced
                # a delta did nothing: compiling it would fail one step later
                # on an item that was never added, blaming the next control.
                proof = pg.get("mutation_proof") or {}
                if proof and not proof.get("delta") \
                        and not proof.get("navigated"):
                    blocking.append(
                        f'add_to "{a["destination"]}": the probe clicked '
                        f'"{proof.get("control")}" and the page did not '
                        "change at all — that control does not perform the "
                        "mutation; name the control that does, or add the "
                        "step that opens it first")
                    continue
                if not proof and navigation_shaped(ctrl):
                    # unproven and navigation-shaped: say so, never refuse —
                    # plenty of real mutate-controls are anchors that POST.
                    warnings.append(
                        f'add_to "{a["destination"]}": "{ctrl["name"]}" is a '
                        "link to another page, and qualified as the mutation "
                        "control by its NAME alone — if it only navigates, "
                        "this run fails at the next step. Prove it with "
                        "probe: {perform: true}.")
                mplans[aid] = {"prerequisite": None, "control": ctrl,
                               "within": scope,
                               "evidence": "mutation control observed on the "
                                           "current page, scoped by `within`"}
                proven[f"add_to:{aid}"] = ctrl["name"]
                continue
            if src not in bound:
                blocking.append(
                    f'add_to "{a.get("destination", "")}": item_from '
                    f"{src!r} has no bound result — the pick did not bind")
                continue
            picked = (pg.get("search") or {}).get("picked") or {}
            plan, why = picked.get("mutation_path"), None
            if not plan or not plan.get("control"):
                ctrl, why = mutation_control(picked.get("controls") or [],
                                             a["destination"])
                if ctrl is not None:
                    plan = {"prerequisite": None, "control": ctrl,
                            "evidence": "mutation control observed on the "
                                        "landed page"}
            if not plan or not plan.get("control"):
                # NOOD_0212 — the landed page has no add control, but the
                # RESULTS page card the pick came from may carry its own. On
                # most retail grids that card button IS the add-to-cart, and
                # opening the product first is a detour the goal never asked
                # for: the implied pick (NOOD_0168) inserted the navigation,
                # then the prover looked for the button on the page that
                # navigation left. Bind the card's action, scoped to the very
                # caption the pick bound, and compile_goal drops the detour.
                srch = (pg.get("search") or {})
                cap = (bound.get(src) or {}).get("caption")
                # A pick with no `target` means "any matching result" — the
                # implied pick NOOD_0168 inserts is exactly that shape. There,
                # a card the probe happened to click but which offers no add
                # (in-store-only stock is the common one) must not decide the
                # flow: any addable card satisfies the goal equally, so bind
                # one and RE-BIND the caption to it, keeping the assertion and
                # the mutation on the same product. A pick that NAMES its
                # target keeps the strict match — silently adding a different
                # product would answer a question the author didn't ask.
                pick_act = next((x for x in actions if x.get("do") == "pick"
                                 and (x.get("id") or "result") == src), None)
                any_result = not (pick_act or {}).get("target")
                cards = item_actions(srch)
                if not any_result and cap:
                    cards = [c for c in cards
                             if _norm(c.get("item_caption")) == _norm(cap)]
                ctrl2, _why2 = mutation_control(cards, a["destination"])
                if ctrl2 is not None:
                    chosen = ctrl2.get("item_caption") or cap
                    if chosen and chosen != cap and src in bound:
                        # NOOD_0230 (F1-lite) — the identity swap is legal
                        # (an untargeted pick means "any matching result")
                        # but it is never silent: the probe's pick evidence
                        # names a different item than the compiled steps act
                        # on, so the payload says so and intent_trace stops
                        # claiming the pick as fully verified.
                        bound[src]["rebound_from"] = cap
                        bound[src]["caption"] = chosen
                        bound[src]["evidence"] = (
                            "probe:search-results (the card carrying the "
                            "add action)")
                        proven[f"pick:{src}"] = chosen
                        warnings.append(
                            f'pick: bound "{cap}" but only "{chosen}" '
                            "carries an add action into "
                            f'"{a["destination"]}" — the compiled test adds '
                            f'and asserts "{chosen}", not the result the '
                            "probe picked")
                    plan = {"prerequisite": None, "control": ctrl2,
                            "on_results": True, "within": chosen,
                            "evidence": "the picked result's own card action "
                                        "on the results page"}
            if not plan or not plan.get("control"):
                # NOOD_0167 — name what the landed page DOES offer: a
                # reviewed session dead-ended on this generic blocker while
                # the page's tiles carried a differently-named control the
                # whole time. Vocabulary from the probe's own evidence, so
                # the reader's next move is a rename, not a re-probe.
                names = list(dict.fromkeys(
                    c["name"] for c in ((picked.get("controls") or [])
                                        + item_actions(picked))
                    if c.get("name")))[:8]
                blocking.append(
                    f'add_to "{a["destination"]}": no proven mutation path '
                    "on the landed page, and the picked result's own card "
                    "carries no add-shaped action either"
                    + (f" — {why}" if why else "")
                    + " — fix the probe evidence; an unproven intermediate "
                    "step is never guessed"
                    + ("; the landed page offers: "
                       + ", ".join(repr(n) for n in names) if names else ""))
                continue
            mplans[aid] = plan
            proven[f"add_to:{aid}"] = plan["control"]["name"]
            continue
        if a["do"] == "pick":
            # NOOD_0156 — the probe already bound + clicked ONE result
            # (bind_result); this pass records the binding as a bound target
            # with probe provenance, or blocks — never guesses a caption.
            sr = pg.get("search") or {}
            aid = a.get("id") or "result"
            if sr.get("pick_warning"):
                blocking.append(f"pick: {sr['pick_warning']}")
            elif not sr:
                blocking.append("pick: the probe performed no search — "
                                "nothing to pick a result from")
            elif not sr.get("picked"):
                blocking.append("pick: the probe captured no picked-result "
                                "evidence — no landed-page snapshot to bind")
            else:
                cap = sr["picked"].get("picked_caption", "")
                proven[f"pick:{aid}"] = cap
                bound[aid] = {
                    "caption": cap,
                    "selector": sr["picked"].get("picked_selector", ""),
                    "requested_as": a.get("target") or "any matching result",
                    "evidence": "probe:search-results (clicked and landed)"}
            continue
        if a["do"] == "search":
            if pg.get("search_warning"):
                blocking.append(f"search: {pg['search_warning']}"
                                + _wall_clause(pg, actions, env_keys)
                                + _rejection_clause(pg))
            elif pg.get("search"):
                proven["search"] = pg["search"]["term"]
            else:
                blocking.append("search: the probe performed no search — "
                                "no results-page evidence")
            continue
        if a["do"] == "suggest":
            # NOOD_0141 — the requested option must be among the CAPTURED
            # suggestions; the canonical page spelling (exact match first,
            # else substring) is what the compiler emits, so the step clicks
            # the string that actually renders, not the prompt's paraphrase.
            if pg.get("suggest_warning"):
                blocking.append(f"suggest: {pg['suggest_warning']}"
                                + _wall_clause(pg, actions, env_keys)
                                + _rejection_clause(pg))
                continue
            sg = pg.get("suggest")
            if not sg:
                blocking.append("suggest: the probe captured no typeahead — "
                                "no suggestion evidence")
                continue
            want = _norm(a["option"])
            canon = next((s for s in sg["suggestions"]
                          if _norm(s) == want), None) \
                or next((s for s in sg["suggestions"]
                         if want in _norm(s) or _norm(s) in want), None)
            if canon is None:
                # NOOD_0195 — name the nearest captured spelling. A one-edit
                # miss is the commonest cause (a site's typeahead is misspelled
                # and the prompt, or the agent "correcting" it, is not), and
                # without the pointer the repair is a re-derivation from an
                # eight-item list rather than a one-word edit.
                near = difflib.get_close_matches(a["option"],
                                                 sg["suggestions"], 1, 0.6)
                blocking.append(
                    f'suggest: option {a["option"]!r} not among the captured '
                    f'suggestions {sg["suggestions"][:8]}'
                    + (f' — did you mean {near[0]!r}?' if near else ""))
            else:
                proven[f'suggest:{a["term"]}'] = canon
            continue
        # NOOD_0188 — actions that name NO control (press_key, go_back) have
        # nothing for the probe to resolve: they act on the focused element or
        # on history. The generic path below keys everything on a["target"],
        # so without this they raised KeyError mid-author.
        if not a.get("target"):
            continue
        # NOOD_0227 (B3) — actions the halted probe chain never reached get
        # NO near-miss blocker: the vocabulary the blocker would rank comes
        # off pages walked BEFORE the failure, so every candidate in it is a
        # wrong repair (the RC-1 loop). The failure upstream already blocks;
        # these are visibly deferred until it is fixed.
        if fail_i is not None and i > fail_i \
                and _norm(a.get("target")) in skipped_targets:
            proven_phase[f'{a["do"]}:{a["target"]}'] = "unreached"
            continue
        after_pick = any(x.get("do") == "pick" for x in actions[:i])
        # NOOD_0207 — a `within:` anchor says the repeated control IS the
        # intent, so the ambiguity gate (which `within` exists to answer)
        # must not fire on it.
        scope = str(a.get("within") or "").strip()
        # NOOD_0212 — `within:` means "the row/card containing this TEXT", and
        # supplying it SKIPS the ambiguity gate (see _locate's `scoped` arm).
        # An unverified scope therefore authored ready:true and died at run
        # time with "No row containing '<text>' found" — the most expensive
        # ordering there is. Region words (header, nav, footer) are the common
        # miss: they name a part of the page, which `within:` cannot express.
        # Deferred on the same terms as a missing control below: after a pick,
        # or past the probe's reach, the probe never saw this page at all.
        if (scope and not after_pick and (reach is None or i <= reach)
                and not _find_text(scope, [blk for blk, _, _ in blocks])):
            blocking.append(
                f'{a["do"]} "{a["target"]}" within "{scope}": no probed text '
                "on this page identifies that row or card — `within:` scopes "
                "to the TEXT of one repeated row/card and cannot name a "
                "region of the page (header, nav, footer)"
                + _near_miss(scope, blocks, "text"))
            continue
        ctrl = phase = trigger = note = None
        # NOOD_0212 — a caller who pinned this target in pom_content has
        # ALREADY answered "which of the two?", the only question the
        # ambiguity gate asks. Blocking anyway left the gate's own advice —
        # "use the distinguishing control" — with nothing behind it: handing
        # over the selector IS that, and there was no other way to say so.
        # NOOD_0222 — within: and a pom_content pin settle ambiguity on
        # different terms (a pin answers every gate; within: only per-row
        # repetition), so _locate now takes them apart.
        is_pinned = _norm(a.get("target")) in pinned
        if after_pick and picked_blk is not None:
            # NOOD_0156 — an action after the pick happens on the landed page:
            # resolve there FIRST, so the landed page's single "Add to cart"
            # wins over the results page's repeated per-card twins.
            ctrl, phase, trigger, note = _locate(
                a["target"], [(picked_blk, "picked", None)], bool(scope),
                is_pinned)
        if ctrl is None and note is None:
            ctrl, phase, trigger, note = _locate(a["target"], blocks,
                                                 bool(scope), is_pinned)
        if ctrl is None:
            # NOOD_0207 — past the probe's reach it never snapshotted this
            # page at all, so "no probed control matches" is a fact about the
            # probe, not the app: defer to the run, as checks in the same
            # position already are. Only for a plain missing name — a stated
            # ambiguity/reachability `note` is real evidence and still blocks.
            # NOOD_0226 — `reach` replaces the evidence gate here. It was the
            # only boundary, and it recognises three state-writing verbs, so a
            # flow whose page change is an ordinary COMMIT CLICK (sign in,
            # continue, place order) had no boundary at all and every step
            # after it blocked.
            if note is None and reach is not None and i > reach:
                # No probe evidence to record, and none to invent: the step
                # compiles with the author's own wording and the runtime's
                # find() resolves it — proven by the run or red by the run.
                proven_phase[f'{a["do"]}:{a["target"]}'] = "runtime"
                continue
            # NOOD_0226 — after a pick, the near miss comes off the LANDED
            # page, which is the only page this step runs on. Ranking the
            # whole probe meant a blocked add-to-cart on a product page was
            # answered with the RESULTS page's filter names ("electric fans",
            # "sesame street (2)"), measured on a 1000-control listing: an
            # agent told to read `blocking` as probe evidence (NOOD_0214) is
            # then holding a shortlist from a page it never visits, and every
            # candidate in it is a wrong repair. Falls back to the full probe
            # only when the landed page yielded no vocabulary at all.
            scope_blocks = ([(picked_blk, "picked", None)]
                            if after_pick and picked_blk is not None
                            and _block_texts(picked_blk) else blocks)
            blocking.append(f'{a["do"]} "{a["target"]}": '
                            + (note or "no probed control matches that name"
                               + _near_miss(a["target"], scope_blocks))
                            + _searched_clause(scope_blocks)
                            + _reach_clause(actions, reach, i)
                            + (_do_fail_clause(do_failed, pg)
                               if fail_i == i else ""))
            continue
        if ctrl.get("collapsed_from"):
            # NOOD_0212 — resolved, not guessed: every instance links to the
            # same place. Said out loud because the compiled POM pins ONE of
            # them, and a reader comparing the feature to the page should not
            # have to rediscover why the other was safe to ignore.
            warnings.append(
                f'{a["do"]} "{a["target"]}": rendered '
                f'{ctrl["collapsed_from"]} times (responsive header/nav '
                "duplicates), every instance linking to the same "
                "destination — bound the visible one")
        if scope and not _find_text(scope, [blk for blk, _, _ in blocks]):
            blocking.append(
                f'{a["do"]} within "{scope}": no probed text on this page '
                "identifies that row/card" + _near_miss(scope, blocks, "text")
                + (_do_fail_clause(do_failed, pg) if fail_i == i else ""))
            continue
        if phase == "picked" and not after_pick:
            blocking.append(
                f'{a["do"]} "{a["target"]}": only reachable on the page a '
                "result pick lands on — add a pick action before this one")
            continue
        if phase in ("reveal", "discovered") and \
                not _reveal_click_before(actions, a, trigger):
            # Reachable ONLY when an explicit click opens `trigger` first (§2
            # rule 1). Automatic discovery alone never makes a hidden control
            # reachable (rule 3) — say so precisely.
            if phase == "discovered":
                blocking.append(
                    f'{a["do"]} "{a["target"]}": only found via automatic '
                    f'discovery (revealed by "{trigger}"), not by a requested '
                    "action — add an explicit click that opens it before this "
                    "action")
            else:
                blocking.append(
                    f'{a["do"]} "{a["target"]}": hidden until "{trigger}" is '
                    "opened — add a click on it before this action")
            continue
        if fail_i == i:
            # NOOD_0227 (B3) — the control RESOLVED but the probe could not
            # execute the action on it (overlay, timeout, dead hitbox).
            # _reveal swallowed exactly this class of failure; marking the
            # action proven anyway would author a step the probe just watched
            # fail.
            blocking.append(
                f'{a["do"]} "{a["target"]}": the probed control resolved but '
                "the probe transaction could not execute it"
                + _do_fail_clause(do_failed, pg))
            continue
        proven[f'{a["do"]}:{a["target"]}'] = ctrl["name"]
        # NOOD_0156 — the exact control this pass resolved (scoped resolution:
        # a landed-page control wins over a results-page twin sharing its
        # name); the compiler reuses THIS dict so the POM selector can't
        # silently re-resolve to the wrong instance.
        resolved[f'{a["do"]}:{a["target"]}'] = ctrl
        if a["do"] == "select" and ctrl.get("options"):
            if not any(_norm(a["option"]) == _norm(o) for o in ctrl["options"]):
                blocking.append(
                    f'select "{a["target"]}": option {a["option"]!r} not among '
                    f'the enumerated options {ctrl["options"][:8]}')
    rsum = (pg.get("search") or {}).get("results_summary")
    # NOOD_0156 — zero search results block authoring outright: "There are 0
    # results available" is missing evidence, and missing evidence never
    # becomes a guess (the NOOD_0156 session authored a full add-to-cart
    # flow on top of exactly this).
    if rsum is not None and any(a.get("do") == "search" for a in actions):
        obs = _observed_count(rsum)
        if obs == 0:
            term = next((a.get("term") for a in actions
                         if a.get("do") == "search"), "")
            blocking.append(
                f'search "{term}": the probe observed 0 results '
                f'({rsum.get("text")!r}) — authoring against zero search '
                "evidence is blocked; change the term or fix the search flow "
                "first")
    gate = _runtime_gate(actions)
    # NOOD_0208 — when the probe PERFORMED the flow, the pages past the gate
    # are snapshotted, so the checks on them get proven here rather than
    # deferred. `performed` is read off the evidence, not the goal alone
    # (NOOD_0227: the request flag AND a completed chain AND a performed
    # block — computed once, above, where the action loop also needs it).
    beyond_reach = _beyond_probe_reach(actions, performed)
    if performed:
        gate = None
    captions = {k: v["caption"] for k, v in bound.items()}
    for i, c in enumerate(goal.get("checks") or []):
        if "status" in c or "response_contains" in c or "json" in c \
                or "schema" in c:
            # NOOD_0192 — same reason as the api action: runtime-proven by
            # the REST client, never by the page probe (json: NOOD_0201,
            # schema: NOOD_0216).
            runtime.append(_check_step(c)[0])
            continue
        if "item_in_destination" in c:
            # NOOD_0156 — identity in the destination is always runtime-proven
            # (the probe never mutates state), but its INPUTS are validated
            # here: the binding must exist, and a named destination must be a
            # probed control (the observation click has provenance or blocks).
            src = c.get("expected_from", "")
            if src not in captions:
                blocking.append(
                    f"check item_in_destination: expected_from {src!r} has no "
                    "bound result caption — the pick did not bind")
                continue
            dest = c.get("item_in_destination") or ""
            if dest:
                dctrl, dphase, _, dnote = _locate(dest, blocks)
                if dctrl is None:
                    blocking.append(
                        f'check item_in_destination "{dest}": '
                        + (dnote or "no probed control opens that "
                                    "destination — cannot verify there"))
                    continue
                proven[f"destination:{dest}"] = dctrl["name"]
                proven_phase[f"destination:{dest}"] = dphase or "initial"
            runtime.append(_check_step(c, captions)[0])
            continue
        after = c.get("after")
        if w := _unanchored_landing_warning(c, actions, landing_scope,
                                            performed_scope):
            warnings.append(w)
        anchor_i = next((j for j, a in enumerate(actions)
                         if a.get("id") == after), -1) if after is not None else -1
        if gate is not None and (anchor_i >= gate
                                 or (after is None and beyond_reach)):
            # Anchored after data the probe never entered — the probe cannot
            # honestly prove it; the run must. Preserved verbatim, never dropped.
            #
            # NOOD_0200 — an UNANCHORED check is the END state (NOOD_0158
            # compiles it after the last action). When the goal's own actions
            # carry the page past the probe's reach, the probe holds no
            # evidence for that page at ALL, so matching the check against the
            # landing snapshot was wrong in both directions: it blocked text
            # that only exists downstream, and it "proved" text that merely
            # happens to appear on the landing page (footer/nav wording) while
            # the compiled step asserts it somewhere else entirely. Route it
            # to the run, which is the only honest witness. `after: start`
            # still pins a check to the landing page.
            runtime.append(_check_step(c)[0])
            continue
        if "field" in c:
            # NOOD_0156 — a field-shows-value check is always runtime-proven:
            # the probe never types data, so there is nothing to prove it by.
            runtime.append(_check_step(c)[0])
            continue
        if "not_see" in c or "url_contains" in c or "page_status" in c:
            # NOOD_0188 — absence and landing-URL are runtime-only by nature:
            # a probe snapshot cannot prove a thing is ABSENT (it may simply
            # not have rendered yet), and the probe never follows the flow to
            # its landing URL. Blocking on them would be a false negative.
            runtime.append(_check_step(c)[0])
            continue
        at_end = _check_scope(c, goal) == "search"
        scope = search_scope if at_end else initial_scope
        # An expect verdict answers only for the page the probe ended on —
        # the search landing page when the goal searches, else the initial
        # page (NOOD_0196). Only a check anchored BEFORE a search must ignore
        # it; discarding it for every non-search goal blocked body text the
        # probe had literally proven, because the structured capture carries
        # headings and controls, not prose.
        expect = expect_found if at_end or not searched else set()
        if "see" in c:
            hit = c["see"] if _norm(c["see"]) in expect \
                else _find_text(c["see"], scope)
            if hit is None and (nar := _shared_phrase(c["see"], scope)):
                # NOOD_0199 — narrow to the probe-proven part rather than
                # block. The check dict is rewritten in place ON PURPOSE: the
                # compiled step must assert what the evidence supports, and
                # `narrowed` carries the change into the payload's warnings so
                # it is never a silent weakening.
                run, hit = nar
                narrowed.append({"from": c["see"], "to": run, "probed": hit})
                c["see"] = run
            if hit is None:
                # NOOD_0200 — every blocking entry names a legal next input:
                # a bare rejection reads as "your text is wrong" when the
                # real meaning may be "wrong page", and the one legal escape
                # was undiscoverable (the reviewed session dropped four TRUE
                # assertions over exactly this).
                blocking.append(
                    f'check "{c["see"]}": no probed heading or control shows '
                    "that text on the page it is scoped to — fix the text to "
                    "probed evidence, anchor it to the page it belongs to "
                    "(`after: <action id>`, `after: start` for the landing "
                    "page), or leave it unanchored on a flow whose later "
                    "actions only the run performs (the run then proves it)"
                    # NOOD_0207 — three named repairs still cost a lap when
                    # none of them says what the page DOES show.
                    + _near_miss(c["see"], scope, "text")
                    + _searched_clause(scope))
            else:
                # NOOD_0220 — prove and compile must agree. `_find_text` also
                # matches the REVERSE direction (the page renders a shorter
                # form than the ask: "BeanCounter ERP banner" proven by
                # "BeanCounter ERP"), and the runtime asserts by SUBSTRING —
                # so compiling the original wording asserts text the page
                # does not render. The payload said proven, the run went red,
                # and the reader paid a lap to discover the engine's own
                # evidence disagreed with its own Gherkin. Narrow to what was
                # actually proven, with the same NOOD_0199 provenance: never
                # silent, and the invariant is now checkable — every compiled
                # `see` is a substring of the text that proved it.
                if _norm(c["see"]) not in _norm(hit):
                    narrowed.append({"from": c["see"], "to": hit,
                                     "probed": hit})
                    c["see"] = hit
                proven[f"see:{c['see']}"] = hit
                proven_phase[f"see:{c['see']}"] = \
                    "search" if at_end else "initial"
        elif "count" in c:
            want = c.get("min", 1)
            if rsum is None:
                blocking.append(f'check count "{c["count"]}": the probe found '
                                "no results-summary element")
            else:
                obs = _observed_count(rsum)
                if obs is None:
                    blocking.append(
                        f'check count "{c["count"]}": unable to parse an observed '
                        f'count from the summary {rsum.get("text")!r}')
                elif obs < want:
                    blocking.append(
                        f'check count "{c["count"]}": probe observed {obs}, below '
                        f"the requested minimum {want}")
                else:
                    proven[f"count:{c['count']}"] = rsum["text"]
                    proven_phase[f"count:{c['count']}"] = "search"
        else:  # any_of — distinct matching alternatives, not one match ≥ min
            want = c.get("min", 1)
            blks = scope or initial_scope
            texts = set()
            for alt in c["any_of"]:
                hit = alt if _norm(alt) in expect else _find_text(alt, blks)
                if hit is not None:
                    # NOOD_0220 — deliberately NOT narrowed, unlike the `see`
                    # arm above. NOOD_0195 measured the difference: an any_of
                    # runs over RESULT TITLES, whose probe capture truncates,
                    # so a short probed form is usually the capture being
                    # lossy rather than the page rendering less. Narrowing
                    # there would weaken a correct assertion; the disjunction
                    # stays whole and the run records which member rendered.
                    texts.add(_norm(hit))
            if len(texts) >= want:
                # NOOD_0197 — the proven members feed ONE disjunctive step
                # (`sees any of …`); the NOOD_0195 per-member literal expansion
                # is gone — it compiled "A or B" into "A and B".
                proven[f"any_of[{i}]"] = sorted(texts)
                proven_phase[f"any_of[{i}]"] = \
                    "search" if at_end else "initial"
            else:
                blocking.append(
                    "check any_of " + "/".join(c["any_of"])
                    + f": {len(texts)} distinct alternative(s) in the probed "
                    f"evidence, below the requested minimum {want}")
    # NOOD_0156 — heading evidence for postcondition synthesis: what a click
    # provably revealed (keyed by normalized trigger name), and the probed
    # headings overall (suggestion material when synthesis has to block).
    revealed_headings: dict[str, list[str]] = {}
    for blk, phase, trig in blocks:
        # NOOD_0227 — a do-walked click block (probe_args routes reveal
        # clicks through the transaction executor) keys by its click target,
        # so postcondition synthesis sees what the goal's own click revealed
        # exactly as it did on the reveal path.
        if phase == "performed" and not trig \
                and str(blk.get("revealed_by", "")).startswith("do: click"):
            trig = _do_label_target(blk["revealed_by"])
        if phase in ("reveal", "performed") and trig:
            heads = [h for h in blk.get("headings", []) if str(h).strip()]
            if heads:
                revealed_headings.setdefault(_norm(trig), []).extend(heads)
    headings = [h for blk, ph, _ in blocks
                if ph in ("initial", "reveal", "search", "performed")
                for h in blk.get("headings", []) if str(h).strip()]
    return {"blocking": blocking, "proven": proven,
            "proven_phase": proven_phase, "runtime_asserted": runtime,
            "permission_prompts": pg.get("permission_prompts", []),
            "popups_closed": pg.get("popups_closed", 0),
            "results_summary": rsum, "controls": controls,
            "bound_targets": bound, "resolved_controls": resolved,
            "mutation_plans": mplans, "navigation_health": nav_health,
            "revealed_headings": revealed_headings, "headings": headings,
            "narrowed": narrowed,
            # NOOD_0208 — risks that are real but unproven. A block would be a
            # heuristic refusing a working flow (NOOD_0207's lesson); silence
            # would be the diagnose-without-repair defect. This is the third
            # option: say it, ship it, name the opt-in that settles it.
            "warnings": warnings}
