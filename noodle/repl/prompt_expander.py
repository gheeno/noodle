"""NOOD_0169 — deterministic prompt compiler: plain-English steps → a goal
object through three pure passes, no LLM on the happy path.

The definitive-plan review of the first `--prompt` cut found two gaps: the
single-pass regex parser was narrower than its public contract (backticked
URLs, `Then url`, parenthetical compounds and `Verify:` labels all refused),
and every context inference hung off ONE global search id, so multi-flow
prompts could never bind independently. This version is the bounded flow
planner from that plan:

  Pass A (_clauses)    — normalize + classify: bullets/backticks stripped,
                         URLs extracted, evidence suffixes separated,
                         parenthetical/conjunction compounds split only when
                         both halves carry a recognizable verb. Every clause
                         gets an id and source line. No intent inference.
  Pass B (_translate)  — self-contained clauses become typed nodes with
                         stable ids minted in source order (search1, pick1,
                         add1). A prompt whose every clause is complete skips
                         Pass C entirely: the deterministic fast path.
  Pass C (in _assemble)— typed dataflow for the incomplete clauses only:
                         search(term)→result_set, pick→selected_item,
                         add_to(selected_item, destination)→mutation,
                         item_in_destination←mutation. Producers bind within
                         a two-flow-sibling window, nearest first; a forward
                         sibling may confirm or CONFLICT (a conflict blocks,
                         never guesses). Every inference carries provenance
                         + supporting clause ids.

Semantic actions may be inferred from context; surface controls may not — a
click enters the goal only when the prompt names it (probe-proven
prerequisites are the compiler's job, downstream). Anything outside the
grammar is refused BY NAME; `model_fallback` (one temperature-zero
`ask()` call, opt-in via NOODLE_MODEL) translates only what the
deterministic passes could not, and its output passes the same
review_contract gate. Pure text → dict; unit-testable without a browser.
"""
import json
import re
from urllib.parse import urlsplit

_BULLET = re.compile(r"^\s*(?:\d+\s*[.)]\s*|[-*•]\s+)")
_INLINE_NUM = re.compile(r"\s+\d+\s*[.)]\s+")
_URLISH = re.compile(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?$", re.I)
_ARTICLE = re.compile(r"^(?:a|an|the|any|some)\s+", re.I)
_ANAPHORA = {"it", "that", "this", "them", "one", "item", "product",
             "the item", "the product", "the result"}
_EVIDENCE = re.compile(
    r"(?:\band\s+)?(?:take\s+(?:a\s+)?)?\bscreenshots?\b"
    r"(?:\s+for\s+verification)?|(?:\band\s+)?\bcaptures?\b"
    r"(?:\s+(?:the\s+)?(?:screen|page))?(?:\s+for\s+verification)?", re.I)
_RUN_MODE = re.compile(r"\brun\b.*\b(headed|headless)\b|"
                       r"\b(headed|headless)\b.*\bmode\b|"
                       r"^\s*(headed|headless)\s*$", re.I)
_WEBSITE_REF = re.compile(
    r"^(?:the\s+)?(?:web\s?site|site|page|app(?:lication)?|url|"
    r"home\s?page|browser)$", re.I)
_PAREN = re.compile(r"\(([^()]{3,})\)")
_CONJ = re.compile(r"\s+(?:and(?:\s+then)?|then)\s+", re.I)

VERBS_HELP = ("go to / open url / then url <url>; search for <term>; "
              "click <name>; enter <value> in <field>; "
              "select <option> from <list>; add [<item>] to <destination>; "
              "verify[:] <destination> has <item> | verify <text>; "
              "close popups / location prompt; take a screenshot")

# (kind, compiled regex) — first match wins, order matters: nav-url before
# nav (an "open url X" clause must never become a click on "url X"),
# dismiss before click, verify before click.
_VERBS = [
    ("nav_url", re.compile(
        r"^(?:open(?:s)?|then|go(?:es)?\s+to|visit(?:s)?|"
        r"navigate(?:s)?\s+to|launch(?:es)?)?\s*(?:the\s+)?url\s+(\S+)$",
        re.I)),
    ("dismiss", re.compile(
        r"^(?:close|dismiss|accept|handle)s?\b.*\b"
        r"(pop\s*-?\s*ups?|cookies?|banners?|modals?|overlays?|"
        r"location|geolocation|notifications?)", re.I)),
    ("nav", re.compile(
        r"^(?:go(?:es)?\s+to|open(?:s)?|visit(?:s)?|navigate(?:s)?\s+to|"
        r"launch(?:es)?|then)\s+(.+)$", re.I)),
    ("search", re.compile(r"^search(?:es)?(?:\s+for)?\s+(.+)$", re.I)),
    ("enter", re.compile(
        r"^(?:enter|type|fill)s?\s+(?:in\s+)?[\"']?(.+?)[\"']?\s+"
        r"(?:in|into)\s+(?:the\s+)?(.+)$", re.I)),
    ("select", re.compile(
        r"^selects?\s+(.+?)\s+from\s+(?:the\s+)?(.+)$", re.I)),
    ("add_to", re.compile(
        r"^adds?\s*(.*?)\s*to\s+(?:the\s+)?([\w ]+?)$", re.I)),
    # (?!\s*out) — "check out"/"checkout" is a mutation flow, not an
    # assertion; it must fall through to a named refusal, never become a
    # bogus verify. The optional colon accepts the "Verify:" label form.
    ("verify", re.compile(
        r"^(?:verif(?:y|ies)|checks?(?!\s*out\b)|confirms?|ensures?|"
        r"make\s+sure|asserts?|(?:should\s+)?sees?)\b\s*:?\s*"
        r"(?:that\s+|if\s+|whether\s+)?(.*)$", re.I)),
    ("click", re.compile(
        r"^(?:click|press|tap)s?\s+(?:on\s+)?(.+)$", re.I)),
]
_HAS = re.compile(
    r"^(?:the\s+)?(.+?)\s+(?:has|have|contains?|shows?|includes?|lists?)\s+"
    r"(?:a\s+|an\s+|the\s+)?(.+)$", re.I)
_IS_IN = re.compile(
    r"^(?:the\s+)?(.+?)\s+(?:is|are|was|got)?\s*"
    r"(?:added\s+)?(?:in|to)\s+(?:the\s+)?(.+)$", re.I)

# Flow-node kinds that count as siblings for the dataflow window; navigation,
# dismissals and run-mode notes are setup metadata, never flow context.
_FLOW_KINDS = ("search", "click", "enter", "select", "add_to", "verify")
_WINDOW = 2


def _clean(term: str) -> str:
    return _ARTICLE.sub("", term.strip().strip("\"'")).strip()


def _tokens(s: str) -> set:
    """Casefolded word set; a naive plural fold (trailing s on 4+ letter
    words) so 'toys' meets 'toy' — both sides get the same transform, so
    it can never make two different words collide asymmetrically."""
    toks = re.sub(r"[\W_]+", " ", (s or "").casefold()).split()
    return {t[:-1] if len(t) > 3 and t.endswith("s") else t for t in toks}


def _overlaps(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb and (ta <= tb or tb <= ta or ta & tb))


def _is_anaphoric(item: str) -> bool:
    return not item or _clean(item).casefold() in _ANAPHORA


def _normalize_url(u: str) -> str:
    u = u.strip().strip("\"'").rstrip("/")
    return u if "://" in u else f"https://{u}"


# --- Pass A: normalize + classify into clauses --------------------------------

def _recognizable(text: str) -> bool:
    """Does any grammar verb (or a URL / run-mode note) anchor this text?
    The compound-split gate: split only when BOTH halves are recognizable."""
    t = text.strip()
    if not t:
        return False
    if _URLISH.match(t) or _RUN_MODE.search(t):
        return True
    return any(rx.match(t) for _, rx in _VERBS)


def _split_compound(text: str) -> list[str]:
    """Split on and/then connectors ONLY where both halves carry a
    recognizable verb — 'search for cat and dog toys' stays whole."""
    out, rest = [], text
    while True:
        cut = None
        for m in _CONJ.finditer(rest):
            if _recognizable(rest[:m.start()]) and _recognizable(rest[m.end():]):
                cut = m
                break
        if cut is None:
            out.append(rest)
            return out
        out.append(rest[:cut.start()])
        rest = rest[cut.end():]


def _clauses(text: str) -> list[dict]:
    """Source clauses: [{id, text, line, evidence}] — normalized syntax only,
    intent untouched. Markdown bullets/numbering/backticks stripped,
    evidence suffixes separated onto the clause's `evidence` flag,
    parentheticals and verb-verb conjunctions split into their own clauses."""
    lines = [(i + 1, ln) for i, ln in enumerate((text or "").splitlines())
             if ln.strip()]
    if len(lines) == 1 and _INLINE_NUM.search(lines[0][1]):
        lines = [(lines[0][0], part)
                 for part in re.split(r"\d+\s*[.)]\s+", lines[0][1])]
    frags: list[tuple[int, str]] = []
    for line_no, ln in lines:
        ln = _BULLET.sub("", ln).replace("`", "").strip().rstrip(".;,")
        ln = re.sub(r"^(?:the\s+)?users?\s+", "", ln, flags=re.I)
        # leading connectors are ordering words, not verbs: "Then url <u>"
        # means "next, navigate", never a click on "url <u>"
        ln = re.sub(r"^(?:and\s+then|then|next|and)\s+", "", ln,
                    flags=re.I)
        if not ln:
            continue
        # parenthetical compounds: '(and close all pop ups)' becomes its own
        # clause when it carries a verb; decorative parens stay in place.
        extras = []
        def _pull(m):
            inner = re.sub(r"^and\s+", "", m.group(1).strip(), flags=re.I)
            if _recognizable(inner):
                extras.append(inner)
                return ""
            return m.group(0)
        ln = _PAREN.sub(_pull, ln).strip().rstrip(".;,")
        for piece in ([ln] if ln else []) + extras:
            frags.extend((line_no, p.strip().rstrip(".;,"))
                         for p in _split_compound(piece) if p.strip())
    out = []
    for line_no, frag in frags:
        evidence = bool(_EVIDENCE.search(frag))
        body = _EVIDENCE.sub("", frag).strip().strip("+&,;:- ")
        out.append({"id": f"clause-{len(out) + 1}", "text": body or frag,
                    "line": line_no, "evidence": evidence,
                    "evidence_only": evidence and not body})
    return out


# kept for callers/tests that only need the flat step texts
def split_steps(text: str) -> list[str]:
    return [c["text"] for c in _clauses(text)]


# --- Pass B: translate self-contained clauses into typed nodes ----------------

def _parse_clause(c: dict) -> dict:
    """One clause → a typed node; kind 'unknown' when no verb matches."""
    text = c["text"]
    node = {"kind": "unknown", "raw": text, "clause": c["id"],
            "line": c["line"], "evidence": c["evidence"]}
    if c.get("evidence_only"):
        node["kind"] = "evidence_only"
        return node
    if _RUN_MODE.search(text):
        node.update(kind="run_mode", mode=_RUN_MODE.search(text).group(0))
        return node
    if _URLISH.match(text):            # a naked URL clause is navigation
        node.update(kind="nav", url=_normalize_url(text))
        return node
    for kind, rx in _VERBS:
        m = rx.match(text)
        if not m:
            continue
        node["kind"] = kind
        if kind == "nav_url":
            target = _clean(m.group(1)).rstrip(".")
            if _URLISH.match(target):
                node.update(kind="nav", url=_normalize_url(target))
            else:
                node["kind"] = "unknown"
                continue
        elif kind == "dismiss":
            what = m.group(1).casefold()
            node["dismissal"] = (
                "location_prompt" if "location" in what or "geo" in what
                else "notifications_prompt" if "notification" in what
                else "popups")
        elif kind == "nav":
            target = _clean(m.group(1)).rstrip(".")
            if _URLISH.match(target):
                node["url"] = _normalize_url(target)
            elif _WEBSITE_REF.match(target):
                # contextual navigation reference — covered by the prompt's
                # (or caller's) URL, never a click target
                node["kind"] = "nav_ref"
            else:                    # "go to the cart" — navigation by click
                node.update(kind="click", target=target)
        elif kind == "search":
            node["term"] = _clean(m.group(1))
        elif kind == "enter":
            node["value"], node["target"] = m.group(1), _clean(m.group(2))
        elif kind == "select":
            node["option"], node["target"] = \
                _clean(m.group(1)), _clean(m.group(2))
        elif kind == "add_to":
            node["item"], node["destination"] = \
                _clean(m.group(1)), _clean(m.group(2))
        elif kind == "verify":
            node["rest"] = m.group(1).strip()
        elif kind == "click":
            node["target"] = _clean(m.group(1))
        return node
    return node


# --- Pass C helpers: typed dataflow over flow siblings -------------------------

def _flow_index(nodes: list[dict]) -> dict[int, int]:
    """node position → flow position (metadata nodes excluded), the distance
    metric of the two-sibling context window."""
    fi, k = {}, 0
    for i, n in enumerate(nodes):
        if n["kind"] in _FLOW_KINDS:
            fi[i] = k
            k += 1
    return fi


def _verify_shape(rest: str) -> tuple[str, str] | None:
    """(destination_word, item_word) when the text claims item-in-destination
    ('cart has toy' / 'toy is added to cart'); None for plain prose."""
    m = _HAS.match(rest)
    if m:
        return m.group(1), m.group(2)
    m = _IS_IN.match(rest)
    if m:
        return m.group(2), m.group(1)
    return None


# --- assembly ------------------------------------------------------------------

def expand(text: str, base_url: str | None = None) -> dict:
    """Plain-English steps → {ok, goal, base_url, app_name, feature_path,
    translation_mode, clauses, coverage, inferences, unresolved, conflicts,
    assumptions, unrecognized}. Deterministic and pure: identical input
    yields byte-identical output. `unresolved` names clauses outside the
    grammar (model_fallback may translate those); `conflicts` names typed
    contradictions the flow itself contains (no model may guess past them)."""
    clauses = _clauses(text)
    if not clauses:
        return {"ok": False, "error": "empty prompt", "assumptions": [],
                "unrecognized": [], "unresolved": [], "conflicts": [],
                "goal": None}
    nodes = [_parse_clause(c) for c in clauses]
    fi = _flow_index(nodes)
    assumptions, unrecognized = [], []
    unresolved, conflicts, inferences, coverage = [], [], [], []
    urls, dismissals, actions, checks = [], [], [], []
    searches: dict[int, dict] = {}      # node index → search action
    picks: dict[int, dict] = {}         # node index (of minting clause) → pick
    adds: dict[int, dict] = {}          # node index → add_to action
    consumed: set[str] = set()          # search ids already feeding a pick
    counters = {"search": 0, "pick": 0, "add": 0}
    pending_evidence = False

    def _cover(n, status, node_ids=()):
        coverage.append({"clause": n["clause"], "status": status,
                         **({"nodes": list(node_ids)} if node_ids else {})})

    def _step_no(n) -> int:
        return int(n["clause"].split("-")[1])

    def _refuse(n, reason, *, conflict=False):
        entry = f"step {_step_no(n)} '{n['raw']}': {reason}" \
            if reason else f"step {_step_no(n)} '{n['raw']}'"
        unrecognized.append(entry)
        (conflicts if conflict else unresolved).append(
            {"clause": n["clause"], "text": n["raw"], "reason":
                reason or "outside the supported grammar"})
        _cover(n, "conflict" if conflict else "unresolved")

    def _mint(kind: str) -> str:
        counters[kind] += 1
        return f"{kind}{counters[kind]}"

    def _nearest_pick(i: int) -> tuple[int, dict] | None:
        """(distance, pick) of the closest earlier selected_item producer
        within the window — an already-picked entity is reusable (the same
        item can mutate into a second destination)."""
        best = None
        for j, p in picks.items():
            if j < i and fi[i] - fi[j] <= _WINDOW:
                d = fi[i] - fi[j]
                if best is None or d < best[0]:
                    best = (d, p)
        return best

    def _forward_verify_item(i: int) -> tuple[dict, str, str] | None:
        """(node, dest_word, item_word) of the nearest following verify
        within the window that claims item-in-destination."""
        for j in range(i + 1, len(nodes)):
            n2 = nodes[j]
            if n2["kind"] not in _FLOW_KINDS:
                continue
            if fi[j] - fi[i] > _WINDOW:
                return None
            if n2["kind"] == "verify":
                shape = _verify_shape(n2.get("rest") or "")
                if shape:
                    return n2, shape[0], shape[1]
        return None

    for i, n in enumerate(nodes):
        no = _step_no(n)
        if n["kind"] == "unknown":
            _refuse(n, "")
            continue
        if n["kind"] == "run_mode":
            assumptions.append(
                f"step {no} '{n['raw']}': run mode is a runner flag "
                "(--headed/--headless), not a test step — ignored here")
            _cover(n, "metadata")
            continue
        if n["kind"] == "evidence_only":
            pending_evidence = True
            _cover(n, "metadata")
            continue
        if n["kind"] == "nav_ref":
            if not urls and not base_url and not any(
                    x.get("url") for x in nodes):
                _refuse(n, "no URL in the prompt to open — add an "
                            "'open url <url>' step or pass base_url")
            else:
                _cover(n, "navigation")
            continue
        if n["kind"] == "dismiss":
            if n["dismissal"] not in dismissals:
                dismissals.append(n["dismissal"])
            _cover(n, "dismissal")
            continue
        if n["kind"] == "nav":
            urls.append(n["url"])
            _cover(n, "navigation")
            continue
        if n["kind"] == "search":
            act = {"do": "search", "id": _mint("search"), "term": n["term"]}
            searches[i] = act
            actions.append(act)
            _cover(n, "action", [act["id"]])
        elif n["kind"] == "click":
            actions.append({"do": "click", "target": n["target"]})
            _cover(n, "action")
        elif n["kind"] == "enter":
            actions.append({"do": "enter", "target": n["target"],
                            "value": n["value"]})
            _cover(n, "action")
        elif n["kind"] == "select":
            actions.append({"do": "select", "target": n["target"],
                            "option": n["option"]})
            _cover(n, "action")
        elif n["kind"] == "add_to":
            item, dest = n["item"], n["destination"]
            back = [(j, s) for j, s in searches.items() if j < i]
            informative = not _is_anaphoric(item) and not (
                back and _tokens(_clean(item)) <= _tokens(back[-1][1]["term"]))
            if informative:
                if not back:
                    _refuse(n, f"adding '{item}' needs a search step first "
                                "(results are what picks bind to)",
                            conflict=True)
                    continue
                # explicit item: literal pick from the nearest earlier search
                j, src = back[-1]
                pick = {"do": "pick", "id": _mint("pick"), "target": item,
                        "from": src["id"]}
                add = {"do": "add_to", "id": _mint("add"),
                       "item_from": pick["id"], "destination": dest}
                consumed.add(src["id"])
                picks[i], adds[i] = pick, add
                actions.extend([pick, add])
                _cover(n, "action", [pick["id"], add["id"]])
                continue
            # --- Pass C: uninformative item — typed dataflow resolution ---
            window = [(j, s) for j, s in back
                      if s["id"] not in consumed
                      and fi[i] - fi[j] <= _WINDOW]
            sdist = min((fi[i] - fi[j] for j, s in window), default=None)
            pick_cand = _nearest_pick(i)
            if pick_cand and (sdist is None or pick_cand[0] < sdist):
                # the nearest producer is an already-picked entity — the
                # same item mutates into a second destination
                add = {"do": "add_to", "id": _mint("add"),
                       "item_from": pick_cand[1]["id"], "destination": dest}
                adds[i] = add
                actions.append(add)
                inferences.append({
                    "node": add["id"], "provenance": "context-inferred",
                    "consumer": f"add_to {dest}",
                    "source_clauses": [n["clause"]],
                    "note": f"step {no} '{n['raw']}': reuses the already "
                            f"picked item ({pick_cand[1]['id']})"})
                assumptions.append(inferences[-1]["note"])
                _cover(n, "action", [add["id"]])
                continue
            if not window:
                if back:
                    _refuse(n, "the nearest search "
                            f"('{back[-1][1]['term']}') is outside the "
                            f"{_WINDOW}-step context window — name the item "
                            "explicitly or move the search nearer",
                            conflict=True)
                else:
                    _refuse(n, "nothing to add — no earlier search step "
                                "and no explicit item", conflict=True)
                continue
            fwd = _forward_verify_item(i)
            if len(window) > 1:
                # a forward verify's item word may disambiguate; a tie blocks
                if fwd and not _is_anaphoric(fwd[2]):
                    match = [(j, s) for j, s in window
                             if _overlaps(fwd[2], s["term"])]
                    if len(match) == 1:
                        window = match
                if len(window) > 1:
                    terms = ", ".join(repr(s["term"]) for _, s in window)
                    _refuse(n, f"two equally compatible searches ({terms}) "
                                "could feed this add — name the item "
                                "explicitly", conflict=True)
                    continue
            j, src = window[0]
            if fwd and not _is_anaphoric(fwd[2]) \
                    and not _overlaps(fwd[2], src["term"]):
                _refuse(n, f"the earlier search says '{src['term']}' but "
                        f"step {_step_no(fwd[0])} verifies '{fwd[2]}' — "
                        "conflicting context is never guessed past",
                        conflict=True)
                continue
            pick = {"do": "pick", "id": _mint("pick"), "from": src["id"],
                    "strategy": "first_actionable"}
            add = {"do": "add_to", "id": _mint("add"),
                   "item_from": pick["id"], "destination": dest}
            consumed.add(src["id"])
            picks[i], adds[i] = pick, add
            actions.extend([pick, add])
            support = [nodes[j]["clause"], n["clause"]] + (
                [fwd[0]["clause"]] if fwd else [])
            inferences.append({
                "node": pick["id"], "provenance": "context-inferred",
                "consumer": f"add_to {dest}", "source_clauses": support,
                "note": f"step {no} '{n['raw']}': item is "
                        f"{'implicit' if _is_anaphoric(item) else 'the search subject'}"
                        f" — adding the first actionable result of step "
                        f"{_step_no(nodes[j])}'s search '{src['term']}' "
                        f"to {dest}"})
            assumptions.append(inferences[-1]["note"])
            _cover(n, "action", [pick["id"], add["id"]])
        elif n["kind"] == "verify":
            rest = n["rest"]
            if not rest:
                _refuse(n, "nothing to verify")
                continue
            all_adds = sorted(adds.items())
            shape = _verify_shape(rest)
            check = None
            if shape:
                dest_word, item_word = shape
                hits = [(j, a) for j, a in all_adds
                        if _overlaps(dest_word, a["destination"])]
                if hits:
                    j, add = hits[-1]
                    subjects = [searches[k]["term"] for k in searches] + \
                        [p.get("target") or "" for p in picks.values()]
                    if not _is_anaphoric(item_word) and not any(
                            _overlaps(item_word, s) for s in subjects if s):
                        _refuse(n, f"verifies '{item_word}' but the flow's "
                                "item comes from "
                                f"'{next(iter(searches.values()))['term']}'"
                                if searches else
                                f"verifies '{item_word}' — no flow item "
                                "matches", conflict=True)
                        continue
                    check = {"item_in_destination": add["destination"],
                             "expected_from": add["item_from"],
                             "after": add["id"]}
                elif all_adds:
                    dests = ", ".join(repr(a["destination"])
                                      for _, a in all_adds)
                    _refuse(n, f"verifies an item in '{dest_word}' but the "
                            f"flow mutates {dests} — conflicting "
                            "destination", conflict=True)
                    continue
            if check is None and all_adds and len(_tokens(rest)) <= 3 \
                    and any(_overlaps(rest, a["destination"])
                            for _, a in all_adds):
                j, add = next((j, a) for j, a in all_adds
                              if _overlaps(rest, a["destination"]))
                check = {"item_in_destination": add["destination"],
                         "expected_from": add["item_from"],
                         "after": add["id"]}
                assumptions.append(
                    f"step {no} '{n['raw']}': bare destination — verifying "
                    f"the added item is shown in {add['destination']}")
                inferences.append({
                    "node": f"check:{len(checks)}",
                    "provenance": "context-inferred",
                    "consumer": f"observe {add['destination']}",
                    "source_clauses": [n["clause"]],
                    "note": assumptions[-1]})
            if check is None:
                check = {"any_of": [rest]}
                assumptions.append(
                    f"step {no} '{n['raw']}': asserting the literal text "
                    f"'{rest}' is visible")
            if n["evidence"] or pending_evidence:
                check["evidence"] = "screenshot"
                pending_evidence = False
            checks.append(check)
            _cover(n, "check")
        if n.get("evidence") and n["kind"] != "verify":
            pending_evidence = True

    # a trailing "take a screenshot" step attaches to the last check
    if pending_evidence and checks and "evidence" not in checks[-1]:
        checks[-1]["evidence"] = "screenshot"

    if unrecognized:
        return {"ok": False,
                "error": "prompt step(s) not understood — rewrite them or "
                         "author with goal. Supported: " + VERBS_HELP,
                "unrecognized": unrecognized, "unresolved": unresolved,
                "conflicts": conflicts, "assumptions": assumptions,
                "clauses": clauses, "coverage": coverage, "goal": None}
    first_url = urls[0] if urls else None
    if not first_url and not base_url:
        return {"ok": False,
                "error": "no URL in the prompt and no base_url given — "
                         "add a 'go to <url>' step or pass base_url",
                "unrecognized": [], "unresolved": [], "conflicts": [],
                "assumptions": assumptions, "clauses": clauses,
                "coverage": coverage, "goal": None}
    if not dismissals:
        dismissals = ["location_prompt", "popups"]
        assumptions.append(
            "dismissals defaulted to location_prompt + popups (both are "
            "conditional no-ops when the page shows neither)")
    elif dismissals == ["popups"]:
        # "close all pop ups" includes the browser's permission bubble in
        # user language; the close step is a conditional no-op when absent.
        dismissals.append("location_prompt")
        assumptions.append(
            "'close popups' also dismisses the browser location prompt "
            "(a conditional no-op when the page never asks)")

    labels = []
    for a in actions:
        if a["do"] == "search":
            labels.append(f"search '{a['term']}'")
        elif a["do"] == "add_to":
            labels.append(f"add to {a['destination']}")
        elif a["do"] in ("click", "enter", "select"):
            labels.append(f"{a['do']} {a.get('target', '')}".strip())
    for c in checks:
        if "item_in_destination" in c:
            labels.append(f"verify {c['item_in_destination']}")
    scenario = ", ".join(labels)[:80] or "prompt flow"

    goal = {"scenario": scenario, "dismissals": dismissals,
            "actions": actions, "checks": checks}
    if urls:
        goal["navigation"] = urls

    host = urlsplit(first_url or base_url).netloc
    app = re.sub(r"[^a-z0-9]+", "_",
                 host.casefold().removeprefix("www.")).strip("_") or "app"
    slug = re.sub(r"[^a-z0-9]+", "_", scenario.casefold()).strip("_")[:40] \
        or "prompt_flow"
    return {"ok": True, "goal": goal, "base_url": first_url or base_url,
            "app_name": app,
            "feature_path": f"noodle_tests/{app}/features/{slug}.feature",
            "translation_mode": ("contextual" if inferences
                                 else "deterministic-fast-path"),
            "clauses": clauses, "coverage": coverage,
            "inferences": inferences, "unresolved": [], "conflicts": [],
            "assumptions": assumptions, "unrecognized": []}


# --- intent-contract review (pure, no model, no browser) -----------------------

def review_contract(exp: dict) -> dict:
    """{ok, problems} — the deterministic 'does this contract hold together'
    gate every translation mode passes BEFORE any browser work: every clause
    represented, the goal schema-valid, every inference carrying supporting
    clauses and a typed consumer, no surface action without a source clause,
    requested evidence attached to a check."""
    if not exp.get("ok"):
        return {"ok": False, "problems": [exp.get("error", "expansion failed")]}
    from noodle.repl import goal as goal_mod
    problems = []
    norm, _ = goal_mod.normalize(exp["goal"])
    problems += goal_mod.validate(norm)
    covered = {c["clause"] for c in exp.get("coverage") or []
               if c.get("status") not in ("unresolved", "conflict")}
    for c in exp.get("clauses") or []:
        if c["id"] not in covered:
            problems.append(
                f"clause {c['id']} ({c['text']!r}) is not represented "
                "in the goal")
    for inf in exp.get("inferences") or []:
        if not inf.get("source_clauses"):
            problems.append(f"inferred node {inf.get('node')!r} names no "
                            "supporting source clauses")
        if not inf.get("consumer"):
            problems.append(f"inferred node {inf.get('node')!r} has no typed "
                            "consumer — an orphan is never compiled")
    # surface actions must trace to prompt text — a model (or any later
    # editor) may not invent a click/enter/select label
    text = " ".join(c["text"] for c in exp.get("clauses") or [])
    for a in norm.get("actions") or []:
        if a.get("do") in ("click", "enter", "select") and a.get("target"):
            if not _overlaps(a["target"], text):
                problems.append(
                    f'{a["do"]} "{a["target"]}" has no source clause — '
                    "surface controls come from the prompt or probe "
                    "evidence, never from translation")
    if any(c.get("evidence") or c.get("evidence_only")
           for c in exp.get("clauses") or []):
        if not any(ch.get("evidence") == "screenshot"
                   for ch in norm.get("checks") or []):
            problems.append("the prompt requested screenshot evidence but "
                            "no check carries it")
    return {"ok": not problems, "problems": problems}


# --- optional one-call model fallback -------------------------------------------

def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def model_fallback(text: str, base_url: str | None = None) -> dict:
    """ONE temperature-zero ask() translating the whole prompt into the same
    typed goal + clause coverage, used only when the deterministic passes
    left clauses unresolved. Model output is never authoritative: it passes
    goal.normalize/validate and review_contract exactly like deterministic
    output, and any miss returns ok:False with no browser launched."""
    from noodle.llm.client import ask
    from noodle.repl import goal as goal_mod
    from noodle.repl import prompts
    clauses = _clauses(text)
    raw = ask(prompts.prompt_to_goal_prompt(
        text, clauses, goal_mod.vocabulary(), goal_mod.EXAMPLE))
    fail = {"ok": False, "goal": None, "clauses": clauses,
            "translation_mode": "model-fallback", "assumptions": [],
            "unrecognized": [], "unresolved": [], "conflicts": [],
            "coverage": [], "inferences": []}
    try:
        data = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, TypeError):
        return {**fail, "error": "model fallback returned invalid JSON — "
                                 "refused, no browser launched"}
    goal = data.get("goal") if isinstance(data, dict) else None
    cov = data.get("coverage") if isinstance(data, dict) else None
    if not isinstance(goal, dict) or not isinstance(cov, dict):
        return {**fail, "error": "model fallback must return "
                                 '{"goal": {...}, "coverage": {...}} — refused'}
    coverage = [{"clause": c["id"],
                 "status": str(cov.get(c["id"], "unresolved"))}
                for c in clauses]
    urls = goal_mod.navigation_urls(goal)
    first_url = urls[0] if urls else None
    if not first_url and not base_url:
        return {**fail, "error": "model fallback produced no navigation URL "
                                 "and no base_url was given — refused"}
    host = urlsplit(first_url or base_url).netloc
    app = re.sub(r"[^a-z0-9]+", "_",
                 host.casefold().removeprefix("www.")).strip("_") or "app"
    scenario = str(goal.get("scenario") or "prompt flow")
    slug = re.sub(r"[^a-z0-9]+", "_", scenario.casefold()).strip("_")[:40] \
        or "prompt_flow"
    exp = {"ok": True, "goal": goal, "base_url": first_url or base_url,
           "app_name": app,
           "feature_path": f"noodle_tests/{app}/features/{slug}.feature",
           "translation_mode": "model-fallback", "clauses": clauses,
           "coverage": coverage,
           "inferences": [{"node": a.get("id") or a.get("do", "?"),
                           "provenance": "model-interpreted",
                           "consumer": "goal",
                           "source_clauses": [c["id"] for c in clauses]}
                          for a in (goal.get("actions") or [])
                          if isinstance(a, dict)],
           "unresolved": [], "conflicts": [],
           "assumptions": ["translated by the configured model "
                           "(one call); deterministic review + probe "
                           "evidence still gate everything"],
           "unrecognized": []}
    review = review_contract(exp)
    if not review["ok"]:
        return {**fail, "coverage": coverage,
                "error": "model fallback failed the intent-contract review — "
                         "refused, no browser launched: "
                         + "; ".join(review["problems"]),
                "unresolved": [{"clause": c["clause"], "reason": "uncovered"}
                               for c in coverage
                               if c["status"] == "unresolved"]}
    return exp
