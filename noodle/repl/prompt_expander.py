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

from noodle.resolver.patterns import CONTROL_NOUNS

_MAX_CLAUSE_LEN = 600   # NOOD_0177 — a prompt clause is a sentence; bounds backtracking
_BULLET = re.compile(r"^\s*(?:\d+\s*[.)]\s*|[-*•—–>]\s+)")   # NOOD_0199: — – >
# NOOD_0217 — digit bullets only: numbered lines are steps by convention,
# while dash/star bullets routinely carry brief metadata ("- base url: …"),
# so only the numbered kind may demote a goal label to a title.
_NUMBERED = re.compile(r"\s*\d+\s*[.)]\s")
_INLINE_NUM = re.compile(r"\s+\d+\s*[.)]\s+")
# NOOD_0188 — an explicit scheme accepts ANY host (dotless + port), so a local
# dev server works: `go to the url http://localhost:3333`. Without a scheme a
# dot is still required, which is what keeps "go to the cart" a CLICK rather
# than a navigation. Ports were rejected outright before, so every
# localhost:PORT / 127.0.0.1:PORT prompt failed with "no URL in the prompt" —
# i.e. the cheap authoring path couldn't target the app you're developing.
_URLISH = re.compile(
    r"^(?:https?://[\w-]+(?:\.[\w-]+)*(?::\d+)?(?:/\S*)?"
    r"|[\w-]+(?:\.[\w-]+)+(?::\d+)?(?:/\S*)?)$", re.I)
# NOOD_0201 — a rooted URL path (/api/greeting), valid ONLY where an api verb
# already said "this is an HTTP call": it joins {var:REST_BASE_URL} at run
# time. Never accepted as a navigation target.
_PATHISH = re.compile(r"^/[\w\-./{}%?=&]*$")
_ARTICLE = re.compile(r"^(?:a|an|the|any|some)\s+", re.I)
_ANAPHORA = {"it", "that", "this", "them", "one", "item", "product",
             "the item", "the product", "the result"}
# NOOD_0225 — one vocabulary for "evidence", shared by the per-step marker and
# the run-wide directive below. A tester writes "attach", "take", "capture" or
# "grab" interchangeably, and the noun is "screenshot", "screen cap", "capture"
# or plain "evidence"; before this, only `take` + `screenshot|capture` was
# understood, so "take evidence screenshot on this step" worked and
# "attach evidence" — the phrasing the same brief used one line later —
# silently produced no shot at all.
_EV_TAKE = (r"(?:takes?|taking|captures?|capturing|attach(?:es|ing)?|"
            r"includ(?:e|es|ing)|adds?|adding|grabs?|grabbing|"
            r"saves?|saving|collects?|collecting|provid(?:e|es|ing))")
# The photographic nouns. Deliberately NOT "image"/"photo": a brief that says
# "verify no images are broken" must not read as an evidence directive.
_EV_SHOT = r"(?:screen\s*shots?|screen\s*caps?|snapshots?|captures?)"
# NOOD_0207 — the span now swallows its own punctuation and tail. It used to
# strip only the word "screenshot", so "... is present -  on this step" was
# left behind as the literal to assert: an assertion nothing renders, failing
# with a message that named the wrong thing.
_EVIDENCE = re.compile(
    # NOOD_0212 — "…, with a screenshot as evidence" left "with a" behind once
    # the noun was lifted out, and that residue compiled into an assertion on
    # the literal text "with a". The lead-in has to go with the phrase.
    r"(?:\s*[-–—,]\s*)?(?:\band\s+)?(?:\bplease\s+)?"
    r"(?:"
    # "with a screenshot" / "take AN evidence screenshot" (NOOD_0218 — the
    # article left "- take an" behind) / a bare "screenshot".
    r"(?:\bwith\s+(?:an?\s+)?)?"
    rf"(?:\b{_EV_TAKE}\s+(?:an?\s+|the\s+)?)?"
    r"(?:\bevidence\b\s*[:-]?\s*)?"
    rf"{_EV_SHOT}(?:\s+(?:the\s+)?(?:screen|page))?"
    # NOOD_0225 — "evidence" standing alone as the noun ("attach evidence",
    # "with evidence"). Anchored to the clause end because, unlike
    # "screenshot", the bare word appears in ordinary prose ("verify the
    # page for evidence of the order") and this span is STRIPPED, not just
    # flagged — an unanchored match would delete words the tester meant.
    rf"|(?:\b{_EV_TAKE}\s+(?:an?\s+|the\s+)?|\b(?:with|as|for)\s+(?:an?\s+)?)"
    r"\bevidence\b(?=\s*[.;,]?\s*$)"
    r")"
    r"(?:\s+for\s+verification)?"
    r"(?:\s+(?:on\s+this\s+step|for\s+this\s+step|here|as\s+evidence))?", re.I)
# NOOD_0225 — the per-step NEGATIVE ("… - no screenshot needed"). Two bugs
# lived where this regex now sits. The clause splitter classified any line
# carrying such a phrase as a run-wide directive and DROPPED it, so
# "Verify the banner is present - no screenshot needed" lost its assertion
# entirely; and had it survived, _EVIDENCE matched the word "screenshot"
# inside the negation and turned the shot ON. Matched before _EVIDENCE.
_EVIDENCE_NEG = re.compile(
    r"(?:\s*[-–—,(\[]\s*)?(?:\band\s+|\bbut\s+)?"
    r"(?:"
    rf"(?:do\s+not|don'?t|no\s+need\s+to|never)\s+{_EV_TAKE}\s+"
    rf"(?:an?\s+|the\s+|any\s+)?(?:evidence\s+)?(?:{_EV_SHOT}|evidence)"
    rf"|(?:no|without|skip|omit)\s+(?:{_EV_TAKE}\s+)?"
    rf"(?:any\s+|the\s+)?(?:evidence\s+)?(?:{_EV_SHOT}|evidence)"
    rf"|(?:{_EV_SHOT}|evidence)\s+(?:is\s+|are\s+)?not\s+"
    r"(?:required|needed|necessary|wanted)"
    r")"
    r"(?:\s+(?:needed|required|necessary|here|on\s+this\s+step|"
    r"for\s+this\s+step))?\s*[)\]]?", re.I)
_RUN_MODE = re.compile(r"\brun\b.*\b(headed|headless)\b|"
                       r"\b(headed|headless)\b.*\bmode\b|"
                       r"^\s*(headed|headless)\s*$", re.I)
# NOOD_0199 — the same words as an ASIDE inside a flow sentence ("once on the
# page on headed mode or even headless the location prompt appears"). _RUN_MODE
# classifies a WHOLE clause, so one incidental mention dropped every step in
# that sentence; this is the span to lift out instead.
_RUN_MODE_PHRASE = re.compile(
    r"\b(?:on|in|under)?\s*(?:headed|headless)(?:\s+mode)?"
    r"(?:\s*(?:,|or(?:\s+even)?)\s*(?:headed|headless)(?:\s+mode)?)*", re.I)
_WEBSITE_REF = re.compile(
    r"^(?:the\s+)?(?:base\s+|start(?:ing)?\s+|target\s+)?"
    r"(?:web\s?site|site|page|app(?:lication)?|url|"
    r"home\s?page|browser|ui|front\s?-?end|web\s?app)$", re.I)
_PAREN = re.compile(r"\(([^()]{3,})\)")
# NOOD_0226 — a COMMA joins actions too, and it was not a connector here.
# "enter <user> in username, enter <pass> in password, and click Login" split
# at the ` and ` only: the first enter's greedy target swallowed the second
# clause whole ("username, enter <pass> in password") and authoring blocked
# as ambiguous. Multi-action steps are how briefs are written; NOOD_0224
# settled the same split for the probe's own `--do` chain, and this is that
# rule one layer up. Safe because _split_compound cuts ONLY where BOTH halves
# carry a grammar verb: "search for cat, dog toys", "verify A, subtotal is
# $5" and a comma inside a URL or a quoted literal all keep their clause.
_CONJ = re.compile(
    r"\s*,\s*(?:(?:and(?:\s+then)?|then)\s+)?|\s+(?:and(?:\s+then)?|then)\s+",
    re.I)

# NOOD_0197 — phrasing families normalized BEFORE verb matching. The verb
# table is ^-anchored, so an unstripped preamble hides a perfectly parseable
# clause; the session this fixes lost 3 of 6 ordinary-English steps to these:
#   "As a user, I would like to search…"       → "search…"
#   "On the results page, verify…"             → "verify…"
#   "If the location prompt appears, close it" → "close location prompt"
#   "Use the search bar to search for X"       → "search for X"
_NARRATIVE = re.compile(
    r"^as\s+an?\s+[^,]{1,40},\s*(?:i\s+(?:want|would\s+like|need|'d\s+like)"
    r"\s+to\s+)?|^i\s+(?:want|would\s+like|need|'d\s+like)\s+to\s+", re.I)
_PAGE_PREAMBLE = re.compile(
    r"^on\s+the\s+.{1,40}?\s+(?:page|screen|tab|view),?\s+", re.I)
_CONDITIONAL = re.compile(
    r"^(?:if|when)\s+(?:the\s+)?(?P<thing>.{1,80}?)\s+"
    r"(?:appears?|shows?(?:\s+up)?|pops?\s+up|opens?|"
    r"is\s+(?:shown|displayed|present)|comes?\s+up)\s*[,:]?\s*(?:then\s+)?"
    r"(?P<verb>close|dismiss|accept|handle)\s*(?:it|them|the\s+prompt)?$",
    re.I)
# NOOD_0199 — the span is comma- and quote-free: with a bare `.{1,40}?` the
# lazy match crossed both, so 'Use the search bar , search for "Vaccu" ( needs
# to be incomplete )' stripped everything up to that far-away " to " and left
# "be incomplete )". The comma arm now also accepts `use`, the form humans
# actually write ("Use the search bar , search for X").
# NOOD_0211 — the third form, which has neither "to" nor a comma:
# "Using search bar search for Steve Jobs". The other two arms lean on those
# separators to know where the preamble ends; with a bare `[^,"']{1,40}?` and
# no separator the lazy match has nothing to stop it eating the verb too. So
# this arm names the instrument instead — a closed set of UI-control nouns —
# which bounds the preamble by vocabulary rather than punctuation.
_INSTRUMENT_NOUN = (
    r"(?:search\s*(?:bar|box|field|input|widget)?|searchbar|searchbox|"
    r"nav(?:igation)?(?:\s+(?:bar|menu))?|menu|sidebar|side\s+bar|header|"
    r"footer|toolbar|tool\s+bar|top\s+bar|filter|dropdown|drop\s+down|"
    r"form|page|site|website|app|ui)")
_INSTRUMENT = re.compile(
    r"^(?:uses?|using)\s+(?:the\s+)?[^,\"']{1,40}?\s+to\s+(?=[a-z])"
    r"|^(?:uses?|using|via)\s+(?:the\s+)?[^,\"']{1,40}?\s*,\s*"
    rf"|^(?:uses?|using|via|through|with|from)\s+(?:the\s+)?{_INSTRUMENT_NOUN}"
    r"\s*,?\s+(?=[a-z])", re.I)

# NOOD_0199 — PROMPT_TEMPLATE.md is a LABELLED brief ("Base URL: [ … ]",
# "User goal: …"), and a human pastes it whole. Every label line was a clause
# outside the grammar, so the template Noodle ships hard-failed its own
# `--prompt` door. Three families: a URL label IS a navigation step, a goal
# label wraps the flow, and the rest is brief metadata (named, never a step).
# NOOD_0212 — `UI: <url>` / `Site: <url>` label a URL without ever saying the
# word "url", and a brief that opens that way then refuses its own "go to UI"
# back-reference for want of a URL.
_URL_LABEL = re.compile(
    r"^(?:(?:base|target|start(?:ing)?|site|app)?\s*url|ui|uri|site|website)"
    r"\s*:\s*", re.I)
# NOOD_0214 — the same rule, whatever the label happens to read. A brief that
# opens "Web Test UI : https://…" is naming its target exactly as "Base URL:"
# does, but _URL_LABEL is ^-anchored and could not see past the section header
# in front of it — so the URL was lost, the clause refused, and the brief's own
# "go to UI" back-reference then refused too (drill TC3: two clause-1 refusals
# from one missing prefix). Guarded so it can never eat a step: the value must
# be a bare URL end to end (prose wrapped around one stays with _URL_LABEL's
# handling above), and the label must carry no verb of its own — "click the
# link : https://x" is a click, not a navigation.
_LABELLED_URL = re.compile(r"^(?P<label>[^:\n]{1,40}):\s*(?P<url>\S+)\s*$")
_LABEL_VERB = re.compile(
    r"\b(?:go|open|visit|navigate|search|look|click|tap|enter|type|fill|"
    r"select|choose|verify|check|confirm|ensure|assert|close|dismiss|"
    r"accept|add|press|hover|upload|scroll|wait)\b", re.I)
# NB: `Verify:` is NOT here — it is grammar (the verify verb reads its own
# label), and stripping it would turn an assertion into an unknown clause.
_GOAL_LABEL = re.compile(
    r"^(?:user\s+)?(?:goal|story|steps?|flow|task|prompt|scenario)\s*:\s*",
    re.I)
# NOOD_0207 — `ac`/`acceptance criteria`/`objective`/… joined the family. An
# AC preamble restates the whole flow in one sentence; parsed as a step it
# produced BOTH a bogus literal assertion and a "not understood" refusal on
# clause 1 — the most expensive position a refusal can occupy, because every
# later lap re-pays for the whole transcript.
_META_LABEL = re.compile(
    r"^(?P<label>app(?:lication)?(?:\s+under\s+test)?|credentials?(?:/config)?"
    r"|config|shell\s+commands[^:]*|environments?|browsers?|device|"
    r"test\s+name|title|tags?|acceptance\s+criteria|ac|objective|summary|"
    r"purpose|context|agent\s+rules?)\s*:\s*", re.I)
# NOOD_0211 — trailing prose that CONFIGURES the run rather than describing a
# step. "Note : each assertion must contain an evidence screenshot" is the
# canonical one: every tester writes it, and parsing it as a step produced
# both a bogus literal assertion ("Note : each assertion must contain an") and
# a hard NEEDS_INTERPRETATION refusal on an otherwise complete brief.
_DIRECTIVE_LABEL = re.compile(
    r"^(?:note|notes|nb|n\.b\.|remark|reminder|important|caveat|"
    r"requirements?|constraints?)\s*[:\-]\s*", re.I)

# NOOD_0212 — brief scaffolding that carries no step. Three families, all of
# them clause-1 refusals in the wild (the most expensive position there is:
# every later lap re-pays the whole transcript):
#   1. a bare section header — "Web Test", "AC :" (the clause splitter strips
#      the trailing colon, so _META_LABEL, which requires one, never sees it);
#   2. an instruction addressed to the AGENT, not the browser — "Generate a
#      Noodle test in this workspace";
#   3. a note about how the test itself should be written or reported —
#      "follow AGENTS.md", "finish with the Allure + RCA report links".
# These are matched ONLY as the last stop before a refusal (see _parse_clause),
# so a real step always wins the classification and the blast radius is exactly
# the set of clauses that would otherwise have been rejected.
_BARE_HEADER = re.compile(
    r"^(?:ac|acceptance\s+criteria|objective|summary|purpose|context|"
    r"notes?|steps?|flow|scenario|background|preconditions?|pre-?requisites?|"
    r"test\s+(?:case|name|type)|"
    r"(?:web|api|ui|mobile|desktop|e2e|integration)\s+test|test)\s*:?\s*$",
    re.I)
_AGENT_DIRECTIVE = re.compile(
    r"^(?:please\s+)?(?:generate|create|write|author|build|produce|make)\s+"
    r"(?:a|an|the|one)?\s*(?:new\s+)?(?:noodle\s+)?(?:bdd\s+|automated\s+)?"
    r"(?:test|scenario|feature)s?(?:\s+(?:case|suite)s?)?"
    # the object has to BE the test, not merely start with the word: "create a
    # test account" is a step about an account, and swallowing that as an
    # instruction to the agent would silently drop a real one.
    r"\s*(?:$|[.,;]|\b(?:in|for|that|which|to|from|using|with|covering)\b)",
    re.I)
_PROCESS_NOTE = re.compile(
    r"\b(?:agents?\.md|step[-\s]dictionary|step-writing|gherkin|allure|rca|"
    r"report\s+links?|token\s+economy|background\s*:)", re.I)
# NOOD_0212 — deliberately NOT here: a rule that drops a "Verify:" line which
# merely restates the goal ("Verify: picking a suggestion runs the search").
# Tried and reverted — no wording test separates it from a real assertion, and
# the one that looked safe also ate "verify order is placed successfully".
# Silently dropping an asked-for verify produces a test that proves less than
# it claims; blocking against probe evidence, which is what happens today,
# costs a lap but names the fix and stays honest.


def _is_brief_noise(text: str) -> bool:
    """NOOD_0212 — scaffolding rather than a step; see the regexes above."""
    return bool(_BARE_HEADER.match(text) or _AGENT_DIRECTIVE.match(text)
                or _PROCESS_NOTE.search(text))

# Evidence intent, recognised anywhere in the brief (labelled or standing on
# its own line). Order matters: the negative is checked first, because "no
# screenshots" contains "screenshot".
# NOOD_0225 — the negation vocabulary a tester actually types. It used to
# accept only take/capture/include, so "DO NOT ADD SCREENSHOTS" and
# "don't attach any evidence" — the two most natural spellings — read as no
# directive at all, and the default still shot the last step. An explicit
# refusal that the engine ignores is worse than no feature: the brief said
# one thing and the report showed another.
_EVIDENCE_OFF = re.compile(
    rf"\b(?:no|without|skip|omit|avoid)\s+(?:{_EV_TAKE}\s+)?"
    rf"(?:any\s+|the\s+)?(?:evidence\s+)?(?:{_EV_SHOT}|evidence)\b"
    rf"|\b(?:do\s+not|don'?t|should\s+not|shouldn'?t|must\s+not|mustn'?t|"
    rf"never|no\s+need\s+to)\s+{_EV_TAKE}\s+"
    rf"(?:any\s+|an?\s+|the\s+)?(?:evidence\s+)?(?:{_EV_SHOT}|evidence)\b"
    rf"|\b(?:{_EV_SHOT}|evidence)\s+(?:are\s+|is\s+)?not\s+"
    r"(?:required|needed|necessary|wanted)\b"
    rf"|\bno\s+need\s+for\s+(?:any\s+|an?\s+|the\s+)?(?:{_EV_SHOT}|evidence)\b",
    re.I)
# NOOD_0225 — "a screenshot on EVERY STEP" is not "on every assertion". The
# runtime has had the @evidence tag (every passed step) since NOOD_0153, but
# `steps?` sat in the per-assertion pattern, so the brief that asked for the
# most evidence quietly got the least — action steps shipped none. Matched
# before the per-assertion pattern.
_EVIDENCE_PER_STEP = re.compile(
    r"\b(?:each|every|all|per)\b[^.]{0,40}?\b(?:single\s+)?steps?\b[^.]{0,40}?"
    rf"\b(?:{_EV_SHOT}|evidence)\b"
    rf"|\b(?:{_EV_SHOT}|evidence)\b[^.]{{0,40}}?\b(?:each|every|all|per)\b"
    r"[^.]{0,40}?\b(?:single\s+)?steps?\b",
    re.I)
_EVIDENCE_PER_ASSERTION = re.compile(
    r"\b(?:each|every|all|per)\b[^.]{0,40}?\b(?:assert(?:ion)?s?|checks?|"
    r"verif(?:y|ication)s?)\b[^.]{0,40}?"
    rf"\b(?:{_EV_SHOT}|evidence)\b"
    rf"|\b(?:{_EV_SHOT}|evidence)\b[^.]{{0,40}}?\b(?:each|every|all|per)\b"
    r"[^.]{0,40}?\b(?:assert(?:ion)?s?|checks?|verif(?:y|ication)s?)\b",
    re.I)


def evidence_intent(text: str) -> str | None:
    """NOOD_0211 — the run-wide evidence mode a brief asks for, or None.

    'off' beats the positives: an explicit "no screenshots" is a decision, and
    the always-capture-the-last default must not quietly overrule it.
    NOOD_0225 — 'all' (every step) beats 'assertions', because a brief that
    names STEPS asked for more than one that names assertions.
    """
    text = text or ""
    if _EVIDENCE_OFF.search(text):
        return "off"
    if _EVIDENCE_PER_STEP.search(text):
        return "all"
    if _EVIDENCE_PER_ASSERTION.search(text):
        return "assertions"
    return None


def _directive_span(text: str):
    """NOOD_0225 — (mode, match) when `text` carries a run-wide evidence
    directive, else None. evidence_intent() answers WHAT was asked; this
    answers WHERE, which is what tells a directive line ("no screenshots")
    apart from a step that merely carries an evidence note ("Verify the
    banner is present - no screenshot needed"). Conflating the two dropped
    the step and its assertion with it."""
    for mode, rx in (("off", _EVIDENCE_OFF), ("all", _EVIDENCE_PER_STEP),
                     ("assertions", _EVIDENCE_PER_ASSERTION)):
        if m := rx.search(text or ""):
            return mode, m
    return None


def _run_directive(text: str) -> str | None:
    """NOOD_0225 — the run-wide evidence mode, read only off the units of the
    brief that ARE the directive. A unit keeps its step when something
    recognizable survives the directive span; only a unit that is nothing but
    the directive configures the whole run."""
    best = None
    for unit in _directive_units(text):
        found = _directive_span(unit)
        if not found:
            continue
        mode, m = found
        rest = (unit[:m.start()] + " " + unit[m.end():])
        rest = _DIRECTIVE_LABEL.sub("", rest).strip(" -–—,;:.")
        if _recognizable(rest):
            continue                   # a step that mentions evidence
        if mode == "off":
            return "off"               # an explicit refusal beats everything
        best = best or mode
    return best


def _directive_units(text: str) -> list[str]:
    """The brief cut the same way _clauses cuts it — wrapped lines rejoined,
    then split into sentences — so a directive is judged against the unit it
    was written in, whether the brief is a numbered list or one paragraph."""
    lines = [(i + 1, ln) for i, ln in enumerate((text or "").splitlines())
             if ln.strip()]
    return [s for _, ln in _join_wrapped(lines)
            for raw in _SENTENCE.split(re.sub(r"\s+", " ", ln))
            if (s := _BULLET.sub("", raw).strip())]


# NOOD_0209 — the only URL in a brief often lives on its metadata line
# ("App: Demo · UI `https://demo.example`"). _META_LABEL keeps that line out
# of the step flow (NOOD_0207), but discarding it whole took the URL with it,
# and authoring then refused with "no URL in the prompt". Harvested here,
# spent as the base_url fallback BEFORE that refusal, stated in assumptions.
# Scheme'd (or www.) only — a schemeless dotted token in prose ("e.g.") is
# not evidence of a URL.
_URL_IN_TEXT = re.compile(r"(?:https?://|www\.)[^\s`'\"()<>]+")


def _harvest_urls(raw: str) -> list[str]:
    return [_normalize_url(u.rstrip(".,;:")) for u in
            _URL_IN_TEXT.findall(raw or "")]


# a bracketed template placeholder is punctuation around the value
_BRACKETED = re.compile(r"^\[\s*(.*?)\s*\]$")
# NOOD_0199 — ordering words that open a sentence in prose ("After that, …").
_LEAD_CONNECTOR = re.compile(
    r"^(?:and\s+then|then|next|and|after\s+that|afterwards?|after\s+this|"
    r"finally|lastly|first(?:ly)?|second(?:ly)?|now|subsequently|so)"
    r"\s*,?\s+", re.I)
# NOOD_0199 — a bare section header is punctuation between steps.
_SECTION_HEADER = re.compile(
    r"^(?:steps?(?:\s+(?:a\s+)?(?:human|user)\s+would\s+take)?|"
    r"expected(?:\s+results?)?|acceptance(?:\s+criteria)?|notes?|context|"
    r"pre-?conditions?|background)\s*:?\s*$", re.I)
# NOOD_0199 — a markdown table row of expected values ("| Product A |"): the
# way people paste a list of what the page must show.
_TABLE_ROW = re.compile(r"^\|(.+)\|$")
_TABLE_RULE = re.compile(r"^[\s|:-]+$")
# NOOD_0199 — scene-setting between two real steps ("then a suggestion bar
# appears below the search bar"). Deliberately narrow — a NAMED subject
# ("the cart badge shows 1") is still an unknown clause the author must fix,
# because silently dropping it would drop an assertion the user asked for.
_NARRATION = re.compile(
    r"^(?:a|an|the)?\s*(?:search\s+)?(?:suggestions?|autocomplete|typeahead|"
    r"drop\s?-?down|results?|page|screen|window|tab|listings?)"
    r"[\w\s]{0,30}?\s+(?:will\s+|would\s+|should\s+|may\s+|might\s+)?"
    # NOOD_0212 — "the results page LISTS these products" is the same
    # scene-setting as "…appears": narration introducing the assertions that
    # follow it, not an instruction of its own.
    r"(?:appears?|shows?(?:\s+up)?|opens?|loads?|lists?|displays?|"
    r"contains?|is\s+(?:shown|displayed))\b",
    re.I)
# NOOD_0199 — sentence boundary: prose prompts are paragraphs, not one step.
# A capital starts one; so does a lowercase grammar verb, because humans type
# their steps in lower case ("go to x.com. search for kettles"). Both arms
# need whitespace after the stop, so a URL path never splits.
_SENTENCE = re.compile(
    r"(?<=[.!?])\s+(?=[\"'(\[]?(?:[A-Z0-9]|(?i:go|open|visit|navigate|"
    r"search|look|click|tap|enter|type|fill|select|choose|verify|check|"
    r"confirm|ensure|assert|close|dismiss|accept|add|press|hover|upload|"
    r"scroll|wait|then|next|after|finally|lastly|use|using|on|the|a|an)\b))")

VERBS_HELP = ("go to / open url / then url <url>; "
              # NOOD_0192 — the api wok reads the same as the web one.
              "GET|POST|PUT|PATCH|DELETE <url> | call the api at <url> | "
              "go to <url> via rest; verify the response status is <code>; "
              "verify the response body contains <text>; "
              "search for <term>; "
              "click <name>; click the suggestion <option> (after a search); "
              "enter <value> in <field>; "
              "select <option> from <list>; add [<item>] to <destination>; "
              "check/uncheck <name> checkbox; hover over <name>; "
              "upload <file> to <field>; press Enter; go back; "
              "select <date> from the <name> calendar; "
              "verify[:] <destination> has <item> | verify <text> | "
              "verify <A> or <B> | verify at least <N> results with title "
              "<A> or <B> | "
              "verify <text> is not visible | verify the url contains <part>; "
              "close popups / location prompt; capture evidence")

# (kind, compiled regex) — first match wins, order matters: nav-url before
# nav (an "open url X" clause must never become a click on "url X"),
# dismiss before click, verify before click.
_VERBS = [
    # NOOD_0192 — the api verbs come FIRST: every one of them names a URL, and
    # `nav`/`click` would otherwise swallow "go to <url> via rest" as a browser
    # navigation. Each carries named groups (method/url) because three shapes
    # share one kind. A match whose url isn't URL-shaped falls through to the
    # rest of the table — "gets the coffee" is still a click.
    ("api", re.compile(
        r"^(?:(?:sends?|makes?|performs?|issues?|submits?|runs?|does)\s+)?"
        r"(?:a\s+|an\s+)?(?P<method>GET|POST|PUT|PATCH|DELETE)\b"
        r"(?:\s+(?:api|rest|http)?\s*(?:call|request))?"
        r"(?:\s+(?:to|at|on|against|for))?\s+(?P<url>\S+)"
        # NOOD_0201 — optional batch tail: body template, repeat count,
        # per-call status. Restricted to these three openers so a stray
        # sentence never rides along.
        r"(?P<tail>\s+(?:with|repeated|expecting)\s.*)?$", re.I)),
    # NOOD_0201 — the batch shape by name: "create 20 rows using POST <url>
    # with body '{...}'". Count + one call spec = ONE repeat step, never N
    # pasted calls (the screenshot failure this ticket opened with).
    ("api_batch", re.compile(
        r"^(?:generates?|creates?|seeds?|inserts?|adds?|submits?)\s+"
        r"(?P<count>\d+)\s+[\w\s-]*?"
        r"(?:using|via|over|through)\s+(?:a\s+|the\s+)?"
        r"(?:(?P<method>GET|POST|PUT|PATCH|DELETE)\s+)?"
        # each filler word must stand alone ("rest api call to") — a bare
        # `(?:http)?` here would eat the "http" of "https://…"
        r"(?:(?:rest|api|http)\s+)?(?:(?:call|request)s?\s+)?"
        r"(?:(?:to|at|on|against)\s+)?(?P<url>\S+)?"
        r"(?P<tail>\s+(?:with|expecting)\s.*)?$", re.I)),
    ("api", re.compile(
        r"^(?:calls?|hits?|queries|requests?|fetch(?:es)?|gets?)\s+"
        r"(?:the\s+)?(?:rest\s+)?(?:api|endpoint|service|url|resource)?\s*"
        r"(?:at|on|to|from)?\s+(?P<url>\S+)"
        r"(?:\s+(?:via|over|using|through)\s+(?:the\s+)?"
        r"(?:rest|api|http))?$", re.I)),
    # the user's own phrasing: "go to <url> via rest"
    ("api", re.compile(
        r"^(?:go(?:es)?\s+to|open(?:s)?|visit(?:s)?|navigate(?:s)?\s+to)\s+"
        r"(?:the\s+)?(?:url\s+)?(?P<url>\S+)\s+"
        r"(?:via|over|using|through)\s+(?:the\s+)?(?:rest|api|http)"
        r"(?:\s+call)?$", re.I)),
    # a bare status claim with no verify verb in front of it
    ("api_status", re.compile(
        r"^(?:the\s+)?(?:(?:response|api|call|request|it|url)\s*)?"
        r"(?:status(?:\s+code)?\s*)?"
        r"(?:should\s+(?:be|equal|return)|is|was|returns?|=)\s*"
        r"(?P<code>\d{3})$", re.I)),
    ("nav_url", re.compile(
        r"^(?:open(?:s)?|then|go(?:es)?\s+to|visit(?:s)?|"
        r"navigate(?:s)?\s+to|launch(?:es)?)?\s*(?:the\s+)?url\s+(\S+)$",
        re.I)),
    ("dismiss", re.compile(
        # NOOD_0197 — consent joins the family (goal-side canon already maps
        # it to popups); "use location" carries "location" and lands right.
        r"^(?:close|dismiss|accept|handle)s?\b.*\b"
        r"(pop\s*-?\s*ups?|cookies?|banners?|modals?|overlays?|consent|"
        r"location|geolocation|notifications?)", re.I)),
    # NOOD_0209 — result selection is a first-class action: `pick` existed
    # in the goal schema (NOOD_0156) but NO prompt phrasing reached it, so
    # 'open the "X" result' authored a click on a target no probed control
    # carries. Two shapes: a quoted title + result noun (or "from the
    # results"), and the ordinal "first result". MUST precede `nav` and
    # `click`, both of which would swallow these. "link"/"button" nouns are
    # deliberately absent — 'click the "Contact" link' is a plain click.
    ("pick_result", re.compile(
        r"^(?:pick|open|select|choose|click|tap|press)s?\s+(?:on\s+)?"
        r"(?:the\s+)?"
        r"(?:first\s+(?:search\s+)?(?:result|item|entry|match|article)"
        r"|([\"'])(?P<item>(?:(?!\1).)+?)\1\s*"
        r"(?:(?:search\s+)?(?:result|article|item|entry|match)"
        r"|\s+from\s+(?:the\s+)?(?:search\s+)?results?(?:\s+list)?))"
        r"$", re.I)),
    ("nav", re.compile(
        r"^(?:go(?:es)?\s+to|open(?:s)?|visit(?:s)?|navigate(?:s)?\s+to|"
        r"launch(?:es)?|then)\s+(.+)$", re.I)),
    # NOOD_0212 — the inverted phrasing a brief actually uses: "In the search
    # bar, type 'Vaccu'". The `enter` verb below wants "<value> into
    # <target>", so word-order-first refused. Routed to `search` rather than
    # `enter` deliberately: step 3 of a suggestion flow IS a search whatever
    # the word order, and this way it feeds the same typeahead pairing that
    # "search for 'Vaccu'" already does. A trailing aside ("— deliberately
    # incomplete") is a parenthetical about the term, never part of it.
    ("search", re.compile(
        r"^(?:in|into|on|using)\s+(?:the\s+)?(?:search|find)\s*"
        r"(?:bar|box|field|input)?\s*[,:]?\s*"
        r"(?:enter|type|fill|search(?:\s+for)?)s?\s+"
        r"[\"']?([^\"'—–]+?)[\"']?\s*(?:[—–].*)?$", re.I)),
    ("search", re.compile(r"^search(?:es)?(?:\s+for)?\s+(.+)$", re.I)),
    # NOOD_0188 — go_back before nav so "goes back" is never read as a click.
    ("go_back", re.compile(
        r"^(?:go(?:es)?|navigates?)\s+back$", re.I)),
    ("enter", re.compile(
        r"^(?:enter|type|fill)s?\s+(?:in\s+)?[\"']?(.+?)[\"']?\s+"
        r"(?:in|into)\s+(?:the\s+)?(.+)$", re.I)),
    # NOOD_0188 — checkbox verbs MUST precede `verify`, which already owns the
    # word "check". Both require the trailing checkbox/box noun so an ordinary
    # "check the total is 5" stays an assertion.
    ("uncheck", re.compile(
        r"^(?:uncheck|untick|clear)s?\s+(?:the\s+)?[\"']?(.+?)[\"']?\s+"
        r"(?:checkbox|check\s*box|box)$", re.I)),
    ("check", re.compile(
        r"^(?:check|tick|ticks)s?\s+(?:the\s+)?[\"']?(.+?)[\"']?\s+"
        r"(?:checkbox|check\s*box|box)$", re.I)),
    # NOOD_0188 — pick_date before `select`, whose generic "X from Y" would
    # otherwise swallow the calendar and target "Departure calendar".
    ("pick_date", re.compile(
        r"^(?:select|pick|choose)s?\s+[\"']?(.+?)[\"']?\s+(?:from|in)\s+"
        r"(?:the\s+)?[\"']?(.+?)[\"']?\s+(?:calendar|date\s*picker)$", re.I)),
    ("select", re.compile(
        r"^selects?\s+(.+?)\s+from\s+(?:the\s+)?(.+)$", re.I)),
    # NOOD_0188 — upload before add_to (different leading verb, but keep the
    # file→field shape adjacent to the other two-argument verbs).
    ("upload", re.compile(
        r"^uploads?\s+[\"']?(.+?)[\"']?\s+(?:to|into)\s+(?:the\s+)?(.+)$", re.I)),
    ("hover", re.compile(
        r"^hovers?\s+(?:over|on)?\s*(?:the\s+)?(.+)$", re.I)),
    ("add_to", re.compile(
        r"^adds?\s*(.*?)\s*to\s+(?:the\s+)?([\w ]+?)$", re.I)),
    # (?!\s*out) — "check out"/"checkout" is a mutation flow, not an
    # assertion; it must fall through to a named refusal, never become a
    # bogus verify. The optional colon accepts the "Verify:" label form.
    ("verify", re.compile(
        r"^(?:verif(?:y|ies)|checks?(?!\s*out\b)|confirms?|ensures?|"
        r"make\s+sure|asserts?|(?:should\s+)?sees?)\b\s*:?\s*"
        r"(?:that\s+|if\s+|whether\s+)?(.*)$", re.I)),
    # NOOD_0188 — press_key before `click`, which owns "press". Restricted to
    # real key names so "press the Submit button" stays a click.
    ("press_key", re.compile(
        r"^(?:press(?:es)?|hits?|types?)\s+(?:the\s+)?"
        r"(Enter|Return|Tab|Escape|Esc|Space|Backspace|Delete|"
        r"Arrow\s*Up|Arrow\s*Down|Arrow\s*Left|Arrow\s*Right)"
        r"(?:\s+key)?$", re.I)),
    # NOOD_0226 — the ROW-SCOPED click, which only the prompt grammar lacked:
    # the goal schema has `within:` and the step dictionary has `User clicks
    # "X" in the row containing "Y"` (NOOD_0222), so a brief that named a row
    # was the one door that could not reach either. The generic click below
    # swallowed the whole phrase as the target and the run died on a control
    # named "Add to Cart in the row containing <title>". Must precede it.
    ("click_within", re.compile(
        r"^(?:click|press|tap)s?\s+(?:on\s+)?[\"']?(.+?)[\"']?\s+"
        r"(?:in|within|on|from)\s+(?:the\s+)?"
        r"(?:row|card|line|item|entry|tile)\s+"
        r"(?:containing|with|for|labell?ed(?:\s+as)?|named|showing|"
        r"that\s+(?:says|shows|contains))\s+"
        r"[\"']?(.+?)[\"']?$", re.I)),
    ("click", re.compile(
        r"^(?:click|press|tap)s?\s+(?:on\s+)?(.+)$", re.I)),
]

# NOOD_0188 — canonical key spellings for press_key (the runtime step wants
# Playwright's names).
_KEY_CANON = {"return": "Enter", "esc": "Escape", "arrowup": "ArrowUp",
              "arrowdown": "ArrowDown", "arrowleft": "ArrowLeft",
              "arrowright": "ArrowRight"}
# NOOD_0192 — the two api assertions as a `verify <...>` tail ("see if url is
# 200", "verify the response body contains 'Apple'").
_STATUS_CLAIM = re.compile(
    r"^(?:the\s+)?(?:(?:response|api|call|request|it|url)\s*)?"
    r"(?:status(?:\s+code)?\s*)?"
    r"(?:should\s+(?:be|equal|return)|is|was|returns?|=)\s*"
    r"(?P<code>\d{3})$", re.I)
_BODY_CLAIM = re.compile(
    r"^(?:the\s+)?(?:response|api|payload)\s+(?:body\s+)?"
    r"(?:should\s+)?(?:contains?|includes?|has)\s+(?P<needle>.+)$", re.I)
_HAS = re.compile(
    r"^(?:the\s+)?(.+?)\s+(?:has|have|contains?|shows?|includes?|lists?)\s+"
    r"(?:a\s+|an\s+|the\s+)?(.+)$", re.I)
_IS_IN = re.compile(
    r"^(?:the\s+)?(.+?)\s+(?:is|are|was|got)?\s*"
    r"(?:added\s+)?(?:in|to)\s+(?:the\s+)?(.+)$", re.I)

# Flow-node kinds that count as siblings for the dataflow window; navigation,
# dismissals and run-mode notes are setup metadata, never flow context.
_FLOW_KINDS = ("search", "click", "enter", "select", "add_to", "verify",
               "pick_result")
_WINDOW = 2

# NOOD_0201 — the optional tail of an api clause: a quoted body template
# ({i} = 1-based call number in a batch), a repeat count, a per-call status.
_API_BODY = re.compile(
    r"with\s+(?:request\s+)?body\s+(?P<q>['\"])(?P<body>.+?)(?P=q)", re.I)
_API_REPEAT = re.compile(r"(?:repeated\s+)?(\d+)\s+times", re.I)
_API_EXPECT = re.compile(r"expect(?:ing)?\s+(?:status\s+)?(\d{3})", re.I)


def _api_tail(node: dict, tail: str) -> bool:
    """True when the tail is empty or contributed at least one field. A tail
    nothing here recognized means the clause says more than the grammar reads
    ("with headers ..."), and the caller must refuse it by name — silently
    dropping half a sentence is how a test stops testing what was asked."""
    if not tail.strip():
        return True
    hit = False
    if m := _API_BODY.search(tail):
        node["body"], hit = m.group("body"), True
    if m := _API_REPEAT.search(tail):
        node["repeat"], hit = int(m.group(1)), True
    if m := _API_EXPECT.search(tail):
        node["expect_status"], hit = int(m.group(1)), True
    return hit


def _clean(term: str) -> str:
    return _ARTICLE.sub("", term.strip().strip("\"'")).strip()


# NOOD_0209 — quote characters intended as DELIMITERS survived into action
# targets as literal content ('click the "Sign in" button' → target
# '"Sign in" button'), which no probed control name can ever carry; the
# check side of the same bug is the verify-split fix above. Reduced only
# when the target is
# exactly ONE quoted member plus an optional trailing control noun — 'click
# "Save" then confirm' and '"A" next to "B"' stay whole, and quoted CONTENT
# is data whose spelling is never touched.
_TARGET_NOUN = (
    r"button|link|tab|field|input|box|result|article|item|option|icon|menu|"
    r"page|entry|control|checkbox|drop-?down|row|card")
_QUOTED_TARGET = re.compile(
    rf"^([\"'])(?P<content>(?:(?!\1).)+?)\1(?:\s+(?:{_TARGET_NOUN}))?$", re.I)


def _target_clean(term: str) -> str:
    # article-strip only, BEFORE the quote test: _clean's edge-quote strip
    # would take the leading delimiter and hide the pair from the pattern.
    t = _ARTICLE.sub("", (term or "").strip()).strip()
    if m := _QUOTED_TARGET.match(t):
        return m.group("content").strip()
    return _clean(term)


def _tokens(s: str) -> set:
    """Casefolded word set; a naive plural fold (trailing s on 4+ letter
    words) so 'toys' meets 'toy' — both sides get the same transform, so
    it can never make two different words collide asymmetrically."""
    toks = re.sub(r"[\W_]+", " ", (s or "").casefold()).split()
    return {t[:-1] if len(t) > 3 and t.endswith("s") else t for t in toks}


def _overlaps(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb and (ta <= tb or tb <= ta or ta & tb))


def _quoted_or_whole(raw: str) -> str:
    """NOOD_0199 — a quoted value IS the value. 'search for "Vaccu" ( needs
    to be incomplete )' searches for Vaccu; the aside after it is the human
    explaining themselves, and it used to ride into the search box."""
    if q := re.match(r"\s*([\"'])(.+?)\1", raw or ""):
        return _clean(q.group(2))
    return _clean(raw)


def _is_anaphoric(item: str) -> bool:
    return not item or _clean(item).casefold() in _ANAPHORA


def _normalize_url(u: str) -> str:
    u = u.strip().strip("\"'").rstrip("/")
    return u if "://" in u else f"https://{u}"


# --- Pass A: normalize + classify into clauses --------------------------------

def _depreamble(ln: str) -> str:
    """NOOD_0199 — every syntactic wrapper stripped, intent untouched: labels,
    bullets, leading connectors, narrative/page/instrument preambles,
    conditionals. Factored out of `_clauses` because `_recognizable` has to
    apply the SAME normalization: the verb table is ^-anchored, so the second
    half of 'verify X and then use the search bar to search for Y' looked
    verbless to the compound-split gate and the sentence never split."""
    ln = _BULLET.sub("", ln).replace("`", "").strip().rstrip(".;,")
    if m := _BRACKETED.match(ln):
        ln = m.group(1)
    if _META_LABEL.match(ln):
        return ""                      # brief metadata — handled by the caller
    if m := _URL_LABEL.match(ln):
        value = ln[m.end():].strip()
        if b := _BRACKETED.match(value):    # "Base URL: [ https://… ]"
            value = b.group(1)
        # NOOD_0212 — the label's value is routinely prose WRAPPED around the
        # URL ("base url : run this on www.example.com"). Taking the sentence
        # whole produced a non-URL nav target, which fell through to a click —
        # so the brief ended up with no URL at all and its own "open the
        # website" line refused for want of one. Take the URL, not the prose.
        if not _URLISH.match(value) and (u := _URL_IN_TEXT.search(value)):
            value = u.group(0)
        ln = f"go to {value}"
    elif (m := _LABELLED_URL.match(ln)) and _URLISH.match(m.group("url")) \
            and not _LABEL_VERB.search(m.group("label")):
        ln = f"go to {m.group('url')}"      # NOOD_0214 — any label, bare URL
    else:
        ln = _GOAL_LABEL.sub("", ln)
        if b := _BRACKETED.match(ln):
            ln = b.group(1)
    ln = re.sub(r"^(?:the\s+)?users?\s+", "", ln, flags=re.I)
    # leading connectors are ordering words, not verbs: "Then url <u>"
    # means "next, navigate", never a click on "url <u>"
    ln = _LEAD_CONNECTOR.sub("", ln)
    # the preamble/conditional families (NOOD_0197, defined above).
    ln = _NARRATIVE.sub("", ln)
    ln = _PAGE_PREAMBLE.sub("", ln)
    if cond := _CONDITIONAL.match(ln):
        ln = f"{cond.group('verb')} {cond.group('thing')}"
    ln = _INSTRUMENT.sub("", ln)
    return ln.strip().rstrip(".;,")


# NOOD_0199 — the narrative dismissal, the commonest shape in a human test ask:
# "the location prompt would appear, close it, and then a few popups would
# appear, close those too". _CONDITIONAL (NOOD_0197) only covers the if/when
# form of ONE clause; in prose the thing and the close land in different
# comma segments, so the whole sentence used to compile into a click on a
# 200-character target.
_APPEARS = re.compile(r"\b(?:appears?|shows?(?:\s+up)?|pops?\s+up|"
                      r"comes?\s+up|is\s+(?:shown|displayed|present))\b", re.I)
_CLOSE_CMD = re.compile(
    r"^(?:so\s+|then\s+|please\s+|just\s+)*"
    r"(?:close|dismiss|accept|handle|get\s+rid\s+of)\s+"
    r"(?:it|them|those|these|that|all(?:\s+of\s+(?:them|those))?)"
    r"(?:\s+(?:too|as\s+well|also))?$", re.I)
_DISMISS_THING = re.compile(
    r"\b(?P<thing>location|geo\w*|notifications?|pop\s?-?ups?|cookies?|"
    r"consent|banners?|modals?|dialogs?|prompts?|overlays?)\b", re.I)


def _pull_narrative_dismissals(ln: str) -> tuple[str, list[str]]:
    """(remaining text, dismissal clauses) — a comma segment that is only
    'close it' is paired with the nearest earlier segment naming something
    that appears, and both leave the sentence as one grammar clause."""
    segs = [s for s in ln.split(",")]
    if len(segs) < 2:
        return ln, []
    used, extras = set(), []
    for i, seg in enumerate(segs):
        if i in used or not _CLOSE_CMD.match(seg.strip().rstrip(".;")):
            continue
        for j in range(i - 1, -1, -1):
            thing = _DISMISS_THING.search(segs[j])
            if j in used or not thing or not _APPEARS.search(segs[j]):
                continue
            extras.append(f"close {thing.group('thing').lower()}")
            used |= {i, j}
            break
    if not extras:
        return ln, []
    rest = ", ".join(s for k, s in enumerate(segs) if k not in used)
    return re.sub(r"\s{2,}", " ", rest).strip(" ,;"), extras


def _has_verb(text: str) -> bool:
    t = _depreamble(text.strip())
    return bool(t) and (bool(_URLISH.match(t))
                        or any(rx.match(t) for _, rx in _VERBS))


def _recognizable(text: str) -> bool:
    """Does any grammar verb (or a URL / run-mode note) anchor this text?
    The compound-split gate: split only when BOTH halves are recognizable."""
    t = text.strip()
    if not t:
        return False
    return bool(_RUN_MODE.search(t)) or _has_verb(t)


def _strip_run_mode(ln: str) -> str:
    """NOOD_0199 — lift an incidental run-mode aside out of a flow sentence.
    A clause that IS only a run note keeps its text (nothing parseable is left
    once the phrase goes), so `_parse_clause` still files it as metadata."""
    if not _RUN_MODE.search(ln):
        return ln
    rest = re.sub(r"\s{2,}", " ", _RUN_MODE_PHRASE.sub(" ", ln)).strip(" ,;")
    return rest if _has_verb(rest) else ln


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


def _join_wrapped(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """NOOD_0212 — rejoin a numbered step that wrapped onto the next line.

    Briefs are written to a column width, so a long step arrives as::

        4. On the next page, verify you see the two textboxes
           'Please enter your email address' and 'Please enter an order number'

    Split line-by-line, the tail is a verbless fragment that refuses on its
    own while the head asserts nothing — one clause-4 refusal for a step the
    author wrote correctly.

    A continuation is INDENTED and starts no list marker of its own. Table
    rows (`| … |`) are excluded: they are already parsed one check per row,
    and folding them into the narration above would lose every assertion.
    """
    out: list[tuple[int, str]] = []
    for no, ln in lines:
        cont = (out and ln[:1].isspace() and not ln.lstrip().startswith("|")
                and not _BULLET.match(ln.strip()))
        if cont:
            out[-1] = (out[-1][0], out[-1][1].rstrip() + " " + ln.strip())
        else:
            out.append((no, ln))
    return out


def _clauses(text: str) -> list[dict]:
    """Source clauses: [{id, text, line, evidence}] — normalized syntax only,
    intent untouched. Markdown bullets/numbering/backticks stripped,
    evidence suffixes separated onto the clause's `evidence` flag,
    parentheticals and verb-verb conjunctions split into their own clauses."""
    lines = [(i + 1, ln) for i, ln in enumerate((text or "").splitlines())
             if ln.strip()]
    lines = _join_wrapped(lines)
    if len(lines) == 1 and _INLINE_NUM.search(lines[0][1]):
        lines = [(lines[0][0], part)
                 for part in re.split(r"\d+\s*[.)]\s+", lines[0][1])]
    frags: list[tuple[int, str]] = []
    # NOOD_0199 — a prompt written as prose is a paragraph of sentences, not
    # one step. Without this split the whole paragraph was ONE clause: it
    # matched no verb (or matched one and swallowed the rest as its target),
    # and the flow silently vanished. Sentences first, connectors after.
    sentences = [(line_no, s)
                 for line_no, ln in lines
                 for s in _SENTENCE.split(re.sub(r"\s+", " ", ln))]
    # NOOD_0199 — "User goal: <summary>" followed by a "Steps:" section is a
    # scenario TITLE, not the flow; without this the summary parsed as a
    # second, bogus search ahead of the numbered steps that follow it.
    titled = any(_SECTION_HEADER.match(_BULLET.sub("", s).strip())
                 for _, s in sentences)
    # NOOD_0217 — numbered steps ARE the flow even when no "Steps:" header
    # announces them. The 0217 benchmark fed the PROMPT_TEMPLATE shape minus
    # its "Steps a human would take:" line, and the goal summary minted a
    # second search action: one CONTRACT_BLOCKED lap, then a probe lap where
    # the summary's term swallowed the suggest binding. A goal label beside
    # numbered steps is a title; only a brief with no numbered steps still
    # reads its flow off the goal line (the one-line `Goal: search for
    # shoes` door, which must keep working).
    # (checked on LINES, not sentences — the sentence splitter severs the
    # "1." marker from its step, so the numbering is only visible here)
    if not titled:
        titled = any(_NUMBERED.match(ln)
                     and not _GOAL_LABEL.match(_BULLET.sub("", ln).strip())
                     for _, ln in lines)
    for line_no, ln in sentences:
        # NOOD_0177 — collapse whitespace runs BEFORE any clause regex sees the
        # line. The clause patterns use (.+?) straddling two independent \s+
        # boundaries, which backtracks at ~n^2.9 in the number of spaces: a
        # 1200-space prompt ran past 120s in expand(). One space per gap makes
        # the split unambiguous. The cap bounds the remaining polynomial.
        ln = ln.strip()[:_MAX_CLAUSE_LEN]
        bare = _BULLET.sub("", ln).strip()
        if _META_LABEL.match(bare):
            frags.append((line_no, bare))    # kept whole; parsed as metadata
            continue
        # NOOD_0211 — a run directive is decided on the RAW line and dropped
        # here. The evidence-marker stripper downstream eats the very words
        # that identify one ("NO SCREENSHOT" → "NO"), leaving a residue that
        # then refused as an unknown step — the directive would configure the
        # run correctly and still fail the brief it came from.
        # NOOD_0225 — but only when the line IS the directive. The old test
        # dropped any line an evidence pattern touched, so
        # "Verify the banner is present - no screenshot needed" lost its
        # assertion outright AND flipped the whole run's evidence off: the
        # tester asked for one step without a picture and got neither the
        # picture nor the check.
        if _DIRECTIVE_LABEL.match(bare):
            continue
        if found := _directive_span(bare):
            rest = (bare[:found[1].start()] + " " + bare[found[1].end():])
            if not _recognizable(rest.strip(" -–—,;:.")):
                continue
        if _SECTION_HEADER.match(bare) or (titled and _GOAL_LABEL.match(bare)):
            continue                         # header / title — not a step
        if row := _TABLE_ROW.match(bare):
            if not _TABLE_RULE.match(bare):    # the |---| rule is not a value
                # every cell is one expected value → one check each
                frags.extend((line_no, f"verify {cell.strip()}")
                             for cell in row.group(1).split("|")
                             if cell.strip())
            continue
        ln = _depreamble(_strip_run_mode(ln))
        if not ln:
            continue
        ln, extras = _pull_narrative_dismissals(ln)
        # parenthetical compounds: '(and close all pop ups)' becomes its own
        # clause when it carries a verb; decorative parens stay in place.
        def _pull(m):
            inner = re.sub(r"^and\s+", "", m.group(1).strip(), flags=re.I)
            if _recognizable(inner):
                extras.append(inner)
                return ""
            return m.group(0)
        ln = _PAREN.sub(_pull, ln).strip().rstrip(".;,")
        for piece in ([ln] if ln else []) + extras:
            # re-normalize each piece: the preamble families are ^-anchored,
            # so the RIGHT half of a compound ("… and then use the search bar
            # to search for X") only sheds its preamble once it leads.
            frags.extend((line_no, q)
                         for p in _split_compound(piece)
                         if (q := _depreamble(p).strip().rstrip(".;,")))
    out = []
    for line_no, frag in frags:
        # NOOD_0225 — the negative is read FIRST. Left to _EVIDENCE, the word
        # "screenshot" inside "no screenshot needed" matched and requested the
        # very shot the tester declined.
        if _EVIDENCE_NEG.search(frag):
            evidence, evidence_off = False, True
            body = _EVIDENCE_NEG.sub("", frag).strip().strip("+&,;:- ")
        else:
            evidence, evidence_off = bool(_EVIDENCE.search(frag)), False
            body = _EVIDENCE.sub("", frag).strip().strip("+&,;:- ")
        out.append({"id": f"clause-{len(out) + 1}", "text": body or frag,
                    "line": line_no, "evidence": evidence,
                    "evidence_off": evidence_off,
                    "evidence_only": evidence and not body})
    return out


# kept for callers/tests that only need the flat step texts
def split_steps(text: str) -> list[str]:
    return [c["text"] for c in _clauses(text)]


def _split_alternatives(raw: str) -> list[str]:
    """NOOD_0197 — 'Hot Wheels or Die Cast' / '"A", "B", or "C"' → members.
    Only an explicit top-level ' or ' makes a disjunction, and a fully quoted
    text is ONE literal even when it contains ' or '."""
    raw = (raw or "").strip()
    if not raw or not re.search(r"\s+or\s+", raw, re.I):
        return []
    if (len(raw) > 1 and raw[0] == raw[-1] and raw[0] in "\"'"
            and raw.count(raw[0]) == 2):
        return []
    parts = re.split(r"\s*,\s*(?:or\s+)?|\s+or\s+", raw, flags=re.I)
    out = [_clean(p).strip("\"'") for p in parts if p.strip()]
    return out if len(out) > 1 else []


# NOOD_0199 — filler between "verify" and the claim itself. Every assertion
# grammar below is ^-anchored, so "verify that there is at least 1 result …"
# missed the results-count arm and leaked the whole sentence into the any_of
# members ('there is at least 1 result found with the title "Hot Wheels').
_VERIFY_FILLER = re.compile(
    r"^(?:that\s+|if\s+|whether\s+)*"
    r"(?:(?:you|we|i|the\s+user)\s+(?:can|could|should)?\s*"
    r"(?:see|sees|view|find)\s+)?"
    r"(?:that\s+)?(?:there\s+(?:is|are)\s+)?", re.I)

# NOOD_0218 — "X should be <one of these>" is a presence claim on X itself,
# never on the tail word.
_PRESENCE_TAILS = frozenset(
    ("visible", "present", "shown", "displayed", "there", "correct",
     "working", "ok"))

# NOOD_0220 — display-element nouns a human appends when naming WHAT they are
# looking at ("the BeanCounter ERP banner", "the Acme logo"). Deliberately
# separate from resolver.patterns.CONTROL_NOUNS: that list drives locator
# resolution, and widening it would change how steps match elements. This one
# only trims an assertion's trailing word, where the worst case is a shorter
# (still-passing) literal.
_TRAILING_NOUN = re.compile(
    r"\s+(?:banner|logo|image|img|icon|header|heading|section|label|"
    r"title|message|text|button|link|field|panel|badge|tile|card)s?$", re.I)

# NOOD_0221 — the summary-line tail. Anchored to the END of the clause and
# kept to the "does the feature function" family only: these describe the
# TEST, never text a page renders. Everything adjacent was deliberately left
# out — "is successful", "is correct", "as expected" are all strings real
# pages print ("Payment is successful"), and skipping one of those would drop
# a genuine assertion. The caller's escape hatch is quoting.
# Longest alternatives first so "works properly" is read whole rather than
# leaving "properly" stranded past an early `works` match.
_SUMMARY_CLAIM = re.compile(
    r"\b(?:works?\s+(?:fine|well|correctly|properly|"
    r"as\s+(?:expected|intended))|"
    r"functions?\s+(?:correctly|properly|as\s+(?:expected|intended))|"
    r"functional|working|works?)"
    r"\s*[.!]?\s*$", re.I)


# NOOD_0197 — one concrete rewrite per still-unresolved clause, so a partial
# rejection teaches the fix instead of dumping the whole grammar and walking
# away. Keyword → the grammar template it most likely wanted; first hit wins.
_SUGGEST_RULES = (
    # NOOD_0209 — a result-SELECTION clause must be offered the action, not a
    # verify: the old advice silently replaced navigation with an assertion
    # and the rest of the flow ran on the wrong page.
    (re.compile(r"^(?:pick|select|choose|open|click|tap)\b.{0,60}?"
                r"\b(?:results?|articles?)\b", re.I),
     'pick the "<title>" result (after a search step)'),
    (re.compile(r"result|found with|titled?", re.I),
     'verify at least 1 result with title "<A>" or "<B>"'),
    (re.compile(r"search", re.I), 'search for "<term>"'),
    (re.compile(r"pop.?up|cookie|banner|location|notification|consent"
                r"|close|dismiss", re.I),
     '"close popups" / "close location prompt"'),
    (re.compile(r"enter|type|fill|input", re.I), 'enter "<value>" in <field>'),
    (re.compile(r"select|choose|pick", re.I),
     'select "<option>" from <control>'),
    (re.compile(r"click|press|tap|button|link", re.I), "click <control name>"),
    (re.compile(r"go to|open|navigate|visit|url", re.I), "go to <url>"),
    (re.compile(r"verify|see|shown|displayed|contain", re.I),
     'verify "<text>"'),
)


def _suggest(text: str) -> str | None:
    for rx, template in _SUGGEST_RULES:
        if rx.search(text):
            return f"rewrite as: {template}"
    return None


# --- Pass B: translate self-contained clauses into typed nodes ----------------

def _parse_clause(c: dict) -> dict:
    """One clause → a typed node; kind 'unknown' when no verb matches."""
    text = c["text"]
    node = {"kind": "unknown", "raw": text, "clause": c["id"],
            "line": c["line"], "evidence": c["evidence"],
            "evidence_off": bool(c.get("evidence_off"))}
    if c.get("evidence_only"):
        node["kind"] = "evidence_only"
        return node
    # NOOD_0211 — a directive line configures the run; it is never a step.
    # Checked before the verb table because "each assertion must CONTAIN an
    # evidence screenshot" trips the verify verb and compiles to an assertion
    # on the literal text.
    # NOOD_0225 — same "is it the WHOLE clause?" test as the splitter: a
    # compound whose right half carries an evidence note is still a step.
    if _DIRECTIVE_LABEL.match(text):
        node.update(kind="directive",
                    directive=_DIRECTIVE_LABEL.sub("", text).strip(),
                    evidence_mode=evidence_intent(text))
        return node
    if found := _directive_span(text):
        rest = (text[:found[1].start()] + " " + text[found[1].end():])
        if not _recognizable(rest.strip(" -–—,;:.")):
            node.update(kind="directive", directive=text.strip(),
                        evidence_mode=found[0])
            return node
    if m := _META_LABEL.match(text):   # NOOD_0199 — a brief field, not a step
        node.update(kind="metadata", label=m.group("label").strip().lower())
        return node
    if _RUN_MODE.search(text):
        node.update(kind="run_mode", mode=_RUN_MODE.search(text).group(0))
        return node
    if _URLISH.match(text):            # a naked URL clause is navigation
        node.update(kind="nav", url=_normalize_url(text))
        return node
    if not any(rx.match(text) for _, rx in _VERBS) and _NARRATION.match(text):
        node["kind"] = "observation"       # NOOD_0199 — scene-setting prose
        return node
    for kind, rx in _VERBS:
        m = rx.match(text)
        if not m:
            continue
        node["kind"] = kind
        if kind == "api":
            # NOOD_0192 — url-shaped or it isn't an API call; anything else
            # keeps walking the table (so "gets the coffee" stays a click).
            # NOOD_0201 — a rooted path (/api/greeting) is also url-shaped
            # HERE: it joins {var:REST_BASE_URL} at run time, which authoring
            # fills from the caller or localhost discovery.
            url = _clean(m.group("url")).rstrip(".")
            if not (_URLISH.match(url) or _PATHISH.match(url)):
                node["kind"] = "unknown"
                continue
            node["url"] = _normalize_url(url) if _URLISH.match(url) else url
            node["method"] = (m.groupdict().get("method") or "GET").upper()
            if not _api_tail(node, m.groupdict().get("tail") or ""):
                node["kind"] = "unknown"
                continue
        elif kind == "api_batch":
            # NOOD_0201 — count + call spec. A missing url/body is refused BY
            # NAME in assembly (with the one-shot phrasing), never guessed —
            # except the method, where the verb IS the answer: create → POST.
            node["repeat"] = int(m.group("count"))
            url = _clean(m.group("url") or "").rstrip(".")
            node["url"] = (_normalize_url(url) if _URLISH.match(url)
                           else url if _PATHISH.match(url) else None) \
                if url else None
            node["method"] = (m.groupdict().get("method") or "POST").upper()
            if not _api_tail(node, m.groupdict().get("tail") or ""):
                node["kind"] = "unknown"
                continue
        elif kind == "api_status":
            node["status"] = int(m.group("code"))
        elif kind == "nav_url":
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
                node.update(kind="click", target=_target_clean(m.group(1)))
        elif kind == "search":
            node["term"] = _quoted_or_whole(m.group(1))
        elif kind == "pick_result":
            item = m.groupdict().get("item")
            node["item"] = _clean(item) if item else None
        elif kind == "enter":
            node["value"], node["target"] = \
                m.group(1), _target_clean(m.group(2))
        elif kind == "select":
            node["option"], node["target"] = \
                _clean(m.group(1)), _target_clean(m.group(2))
        elif kind == "add_to":
            node["item"], node["destination"] = \
                _clean(m.group(1)), _clean(m.group(2))
        # NOOD_0188 — the form/navigation verbs.
        elif kind in ("check", "uncheck", "hover"):
            node["target"] = _target_clean(m.group(1))
        elif kind == "upload":
            node["file"], node["target"] = \
                _clean(m.group(1)), _target_clean(m.group(2))
        elif kind == "pick_date":
            node["date"], node["target"] = \
                _clean(m.group(1)), _target_clean(m.group(2))
        elif kind == "press_key":
            raw = re.sub(r"\s+", "", m.group(1)).casefold()
            node["key"] = _KEY_CANON.get(raw, m.group(1).strip().title())
        elif kind == "go_back":
            pass                      # no payload — the verb IS the action
        elif kind == "verify":
            node["rest"] = m.group(1).strip()
        elif kind == "click":
            node["target"] = _target_clean(m.group(1))
        elif kind == "click_within":
            # NOOD_0226 — a click that names its row. Kept a distinct kind so
            # the suggestion-flow rewrite below (which reads `target` alone)
            # can never mistake a row anchor for a typeahead option.
            node["target"] = _target_clean(m.group(1))
            node["within"] = _clean(m.group(2)).strip("\"'")
        return node
    # NOOD_0212 — last stop before a refusal. No verb matched, so the only
    # remaining question is whether this is a step nobody can parse or brief
    # scaffolding nobody should have parsed. Rescued HERE, after every verb has
    # had its turn, so a real step is never reclassified as noise.
    if node.get("kind") == "unknown" and _is_brief_noise(text):
        node.update(kind="metadata", label="brief")
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


# NOOD_0211 — "<subject> contains <text>" names WHERE to look, then what to
# find; only the second half is the assertion. Kept as the whole sentence it
# compiled to `the user sees "Main Page contains : From today's feature
# article"` — a literal no page renders, so a correct AC failed on its own
# phrasing. The subject must be a page-ish noun run (≤4 words, no quotes) so
# "the error message contains 'timeout'" — where the subject is the element
# being asserted about — is left alone, as are REST body assertions.
_CONTAINMENT = re.compile(
    r"^(?!.*\b(?:response|body|payload|json|api|endpoint|header)s?\b)"
    r"(?:the\s+)?(?:\w+[\s-]+){0,2}?"
    r"\b(?:page|screen|site|website|ui|view|browser)\b\s+"
    r"(?:contains?|shows?|displays?|has|includes?)\b\s*:?\s+"
    r"(?P<rest>\S.*)$", re.I)


def _strip_containment(rest: str) -> str:
    """Reduce '<page-ish subject> contains <text>' to '<text>'. Best-effort
    and conservative — anything it does not recognise passes through whole."""
    m = _CONTAINMENT.match(rest or "")
    if not m:
        return rest
    inner = m.group("rest").strip().strip(":").strip()
    # Never strip down to nothing, and never past a quote (a quoted value is
    # handled by _CONTAINS_QUOTED, which keeps the quotes as delimiters).
    return inner or rest


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


# NOOD_0207 — three clause SHAPES people always type, normalized into grammar
# the parser already owns. Pure clause→clause: no new node kinds, no new
# assembly branches, and anything unrecognized passes straight through to the
# ordinary refusal. Refusing these was pure cost — each one a lap, and a lap
# re-pays for the whole growing transcript.

# B1 — "fill the customer info: Name - A, Email: B" → one enter per pair.
_LABEL_LIST = re.compile(
    r"^(?:fill|enter|complete|populate)s?\s+(?:in\s+)?(?:the\s+)?"
    r"(?P<what>[\w\s]{0,40}?)\s*(?:details|info(?:rmation)?|fields?|form)?"
    r"\s*(?:with|using)?\s*:\s*(?P<pairs>.+)$", re.I)
_LABEL_PAIR = re.compile(r"^(?P<label>[^:,-][^:]*?)\s*(?::|\s-\s)\s*"
                         r"(?P<value>.+)$")
# B2 — "verify A, B and C" → one verify per part. The guard words are
# load-bearing: the grammar already reads those shapes as ONE assertion
# (NOOD_0197 disjunctions, NOOD_0125 count floors), and splitting them breaks
# tests that pin exactly that.
_VERIFY_LEAD = re.compile(
    r"^(?:verif(?:y|ies)|confirms?|ensures?|asserts?|checks?(?!\s*out\b))"
    r"\b\s*:?\s*(?:that\s+)?(?P<body>.+)$", re.I)
_VERIFY_NO_SPLIT = re.compile(
    r"[\"']|\bor\b|\bany of\b|\bat least\b|\bresults? with\b", re.I)
# NOOD_0212 — the same guard minus the quote clause, for the top-level split
# below: quotes no longer veto splitting, they only bind the separators they
# enclose. The disjunction/quantifier members are unchanged — those really do
# have to stay one step.
_VERIFY_NO_SPLIT_HARD = re.compile(
    r"\bor\b|\bany of\b|\bat least\b|\bresults? with\b", re.I)
_VALUE_TAIL = re.compile(r"\s*,\s*(?:and\s+)?|\s+and\s+", re.I)
_POSSESSIVE = re.compile(r"^(?:his|her|their|its|your|my|our)\s+", re.I)

# NOOD_0217 — the claim grammar lacked the control-noun stripping the step
# grammar has had since NOOD_0109: `verify you see the textbox "Please enter
# your email address"` compiled to an assertion on the LITERAL 'textbox
# "Please enter..."' — text no page renders — and cost a full red-run +
# re-author lap on phrasing a human considers normal. The noun names the
# instrument; the quoted string is the claim. Same noun list as patterns.py
# (imported — one definition, or the two grammars drift), plus text-
# compounds, plurals, an optional count word and an optional linking word.
_NOUN_RUN = (rf"(?:(?:the|a|an|one|two|three|both|all)\s+){{0,2}}"
             rf"(?:(?:text\s?)?(?:{CONTROL_NOUNS})(?:e?s)?\s+)+"
             rf"(?:(?:with|labell?ed(?:\s+as)?|named|showing|reading|"
             rf"saying|containing|for)\s+)?")
# The tight shape: optional see-lead, noun run, ONE quoted member, at most a
# presence tail. Anchored both ends so a state assertion ('the checkbox "I
# agree" is checked') never matches — its tail is not a presence verb, and
# rewriting it would turn a clean refusal into a wrong literal.
_NOUN_CLAIM = re.compile(
    rf"^(?:(?:you|we|i|the\s+user)\s+(?:can\s+|should\s+|will\s+)?sees?\s+)?"
    rf"{_NOUN_RUN}"
    rf"([\"'])(?P<content>(?:(?!\1).)+?)\1"
    rf"(?:\s+(?:is|are)\s+(?:present|visible|shown|displayed)|\s+appears?)?"
    rf"\s*$", re.I)
# The loose helper for retrying the OTHER claim shapes (conjunctions,
# containment verbs) with the noun run lifted out. Substitutes only OUTSIDE
# quotes — quoted content is data, never rewritten — and its result is used
# only when one of the existing tightly-shaped handlers matches it.
_NOUN_BEFORE_QUOTE = re.compile(rf"\b{_NOUN_RUN}(?=[\"'])", re.I)


def _strip_control_nouns(body: str) -> str:
    def repl(m):
        outside = (body.count('"', 0, m.start()) % 2 == 0
                   and body.count("'", 0, m.start()) % 2 == 0)
        return "" if outside else m.group(0)
    return _NOUN_BEFORE_QUOTE.sub(repl, body)


def _split_top_level(body: str) -> list[str]:
    """NOOD_0212 — split on `,` / ` and ` that sit OUTSIDE any quoted run.

    A quoted member is data: a comma inside it belongs to the page text, not
    to the list. Walking the string is the only way to tell the two apart —
    the regex could not, which is why any quote used to veto the split whole.
    """
    parts, buf, quote, i = [], [], None, 0
    while i < len(body):
        ch = body[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote, _ = ch, buf.append(ch)
            i += 1
            continue
        if m := _VALUE_TAIL.match(body, i):
            parts.append("".join(buf).strip())
            buf, i = [], m.end()
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf).strip())
    return [p for p in parts if p]
# NOOD_0209 — the QUOTED half of B2. The verify verb's greedy capture kept
# quote DELIMITERS as literal content, so 'verify "A", "B" and "C"' compiled
# to ONE literal no page renders ('A", "B" and "C'): the flow ran perfectly
# and green was impossible. _split_conjuncts returns the members only when
# the text is ENTIRELY quoted members joined by comma/and separators — prose
# between members, a single member, or any top-level "or" (a disjunction is
# ONE any_of step, never narrowed) leaves the clause untouched. Quoted
# content is DATA: delimiters are stripped, spelling never is.
_QUOTED_MEMBER = re.compile(r"([\"'])(?P<content>(?:(?!\1).)+?)\1")
_CONJ_SEP = re.compile(r"\s*(?:,\s*(?:and\s+)?|\s+and\s+)\s*", re.I)
# presence phrasing AROUND one quoted member: '"Acme" logo is present'
# asserted the literal '"Acme" logo'. Deliberately narrow: exactly one
# quoted member, at most a short noun run, and a presence verb at the end —
# a state assertion ('"Add to cart" button is disabled') keeps its whole
# text, and prose after the verb ('"A" appears next to "B"') never matches.
_PRESENCE_RESIDUE = re.compile(
    r"^(?:the\s+|a\s+|an\s+)?([\"'])(?P<content>(?:(?!\1).)+?)\1"
    # the noun run must never eat a copula or a NEGATION — '"Error" is not
    # visible' must stay a not_see, never invert into a presence check
    r"(?:\s+(?!(?:not|no|never|is|are|isn|aren)\b)[\w-]+){0,3}?"
    r"(?:\s+(?:is|are))?\s+(?:present|visible|shown|displayed|appears?)$",
    re.I)


def _quoted_conjuncts(body: str) -> list[str]:
    members, pos = [], 0
    while pos < len(body):
        m = _QUOTED_MEMBER.match(body, pos)
        if not m:
            return []
        members.append(m.group("content"))
        pos = m.end()
        if pos >= len(body):
            break
        sep = _CONJ_SEP.match(body, pos)
        if not sep:
            return []
        pos = sep.end()
    return members if len(members) > 1 else []


def _split_conjuncts(body: str) -> list[str]:
    body = (body or "").strip()
    if re.search(r"\bor\b", body, re.I):
        return []
    if members := _quoted_conjuncts(body):
        return members
    # NOOD_0212 — "you see the two textboxes 'A' and 'B'". The author names
    # WHICH controls before quoting the text in them; the quoted members are
    # the assertion, the noun phrase introducing them is not. Collapsed whole,
    # it became ONE literal `see` on a sentence no page renders — two field
    # assertions silently turned into one unprovable one. Retried without the
    # preamble ONLY when everything after the first quote is a pure quoted
    # list, so an ordinary sentence carrying a single quote never splits here.
    cut = min((i for i in (body.find('"'), body.find("'")) if i > 0),
              default=-1)
    return _quoted_conjuncts(body[cut:]) if cut > 0 else []
# NOOD_0211 — "assert that UI page returns 200". A status claim is not a text
# claim, but the verify verb had only literal text to compile to, so this
# produced `the user sees "UI page returns 200"` and blocked on evidence no
# page could ever show. Requires a page-ish subject so "the API returns 200"
# (a REST assertion) is left alone.
_PAGE_STATUS = re.compile(
    r"^(?:the\s+)?(?:\w+\s+){0,2}?(?:page|url|site|website|ui|screen)\b"
    r"[^.\d]{0,40}?\b(?:returns?|responds?\s+with|gives?|is|status(?:\s+code)?"
    r"|should\s+(?:be|return))\b[^.\d]{0,20}?(?P<code>[1-5]\d\d)\b", re.I)

# NOOD_0211 — "UI contains \"Welcome to Wikipedia\" banner". _PRESENCE_RESIDUE
# already handles a quoted member with a trailing noun ('"Acme" logo is
# present'); this is the same idea with the prose BEFORE it, which is how an
# AC is actually written. The quoted string IS the assertion — the sentence
# around it is the human explaining which thing they mean. Exactly one quoted
# member, and a containment verb, so a disjunction or a multi-member list
# (handled by _split_conjuncts) never reaches here.
# The subject guard is load-bearing: "the response body contains 'total'" is a
# REST assertion, and rewriting it to a bare `verify "total"` turned a
# pure-API goal into one that launches a browser.
_CONTAINS_QUOTED = re.compile(
    r"^(?![^\"']*\b(?:response|body|payload|json|api|endpoint|header)s?\b)"
    r"[^\"']{0,60}?\b(?:contains?|contain|shows?|displays?|has|have|with|"
    r"including|includes?)\s+(?:the\s+|a\s+|an\s+)?"
    r"([\"'])(?P<content>(?:(?!\1).)+?)\1"
    r"(?:\s+[\w-]+){0,3}\s*$", re.I)

# B3 — a short terminal imperative IS the control's label ("place the order").
_BARE_CLICK = re.compile(
    r"^(?:proceed|continue|submit|confirm|checkout|check\s+out|place|pay|"
    r"finish|complete)\b", re.I)


# NOOD_0211 — a category noun the LAST member of a conjunction carries on
# behalf of all of them: "Born and Died dates" is the Born date and the Died
# date, so splitting on "and" left "Born" (real page text) beside "Died dates"
# (text no page renders) and blocked the whole brief on the second half of a
# phrase the first half had already proven. Closed set, plural only, and it
# fires ONLY when stripping the noun leaves something — never on a member that
# IS the noun ("the dates and times").
_BARE_DETERMINERS = {"the", "a", "an", "its", "his", "her", "their", "our",
                     "this", "that", "these", "those", "all", "both", "any"}
_SHARED_NOUN = re.compile(
    r"^(?P<head>.+?)\s+(?:dates|times|names|values|fields|numbers|prices|"
    r"amounts|labels|columns|headings|totals|counts|ids|links|buttons|"
    r"sections|entries|rows)$", re.I)


def _drop_shared_noun(parts: list[str]) -> list[str]:
    """Strip a trailing shared category noun from any conjunct that carries it
    on behalf of a bare peer. Unquoted PROSE only — the caller never reaches
    here for quoted members (_VERIFY_NO_SPLIT bails on a quote), so this can
    never touch a string the user marked as data.

    The noun-bearing member is rarely last: "Steve Jobs, Born and Died dates,
    and we can see his signature" splits four ways with "Died dates" in the
    middle, so scanning only the tail missed the real case.
    """
    if len(parts) < 2:
        return parts

    def bare_peer(other: str) -> bool:
        """A short co-ordinate item that does NOT itself end in a category
        noun — the thing the shared noun is being shared WITH. Guards
        'the dates and times', where every member is a category noun."""
        return len(other.split()) <= 3 and not _SHARED_NOUN.match(other)

    out = []
    for i, p in enumerate(parts):
        m = _SHARED_NOUN.match(p)
        head = m.group("head").strip() if m else ""
        peers = [o for j, o in enumerate(parts) if j != i]
        # The head must be a real subject, not a stranded determiner: "the
        # dates and times" must not become "the" and "times".
        contentful = head.lower() not in _BARE_DETERMINERS
        if head and contentful and len(head.split()) <= 3 \
                and any(bare_peer(o) for o in peers):
            out.append(head)
        else:
            out.append(p)
    return out


def _rewrite_asks(clauses: list[dict],
                  assumptions: list[str] | None = None) -> list[dict]:
    out: list[dict] = []
    assumptions = assumptions if assumptions is not None else []

    def _emit(src: dict, text: str):
        out.append({**src, "id": f"clause-{len(out) + 1}", "text": text})

    for c in clauses:
        t = (c["text"] or "").strip()
        if m := _LABEL_LIST.match(t):
            pairs = [_LABEL_PAIR.match(p.strip())
                     for p in m.group("pairs").split(",")]
            # all-or-nothing: a partial match means the shape was something
            # else, and guessing half a form is worse than refusing whole.
            if pairs and all(pairs):
                for p in pairs:
                    _emit(c, f'enter "{p.group("value").strip()}" in '
                             f'{p.group("label").strip()}')
                continue
        if m := _VERIFY_LEAD.match(t):
            body = m.group("body").strip()
            # NOOD_0217 — the same body with any control-noun run before a
            # quote lifted out; each handler below retries on it when the
            # noun-carrying original refuses. Used only via those
            # tightly-shaped handlers, so a state assertion never rewrites.
            bare = _strip_control_nouns(body)

            def _noun_note():
                assumptions.append(
                    f"read '{body}' as a claim on the quoted text — the "
                    "control noun names the instrument, not page text")
            # NOOD_0209 — quoted conjunction: one check per member. A claim
            # lead the grammar already reads ('the article shows "A", "B"
            # and "C"') is phrasing; the quoted members are the assertion.
            conj = _split_conjuncts(body)
            if not conj and bare != body and (conj := _split_conjuncts(bare)):
                _noun_note()
            if not conj and (h := _HAS.match(body)):
                conj = _split_conjuncts(h.group(2))
            if conj:
                for p in conj:
                    _emit(c, f'verify "{p}"')
                continue
            # NOOD_0211 — a status claim, before any text handling: the code
            # is the assertion, and every text path below would turn the
            # sentence into a literal nothing renders.
            if ps := _PAGE_STATUS.match(body):
                _emit(c, f'verify page status {ps.group("code")}')
                continue
            if nc := _NOUN_CLAIM.match(body):
                _noun_note()
                _emit(c, f'verify "{nc.group("content")}"')
                continue
            if pr := _PRESENCE_RESIDUE.match(body):
                _emit(c, f'verify "{pr.group("content")}"')
                continue
            cq = _CONTAINS_QUOTED.match(body)
            if not cq and bare != body and (cq := _CONTAINS_QUOTED.match(bare)):
                _noun_note()
            if cq:
                _emit(c, f'verify "{cq.group("content")}"')
                continue
            # NOOD_0212 — a MIXED list ('the page contains "Steve Jobs", his
            # born and died dates, and that his signature is visible') used to
            # be refused wholesale by _VERIFY_NO_SPLIT, because any quote at
            # all blocked the comma split — a guard meant to stop a quoted
            # string being chopped mid-content. Splitting at top level only
            # keeps that guarantee (a separator inside quotes is never a
            # separator) while letting the unquoted members through, which is
            # how an acceptance criterion is actually written. A disjunction is
            # still ONE any_of step and never splits here.
            if not _VERIFY_NO_SPLIT_HARD.search(body):
                parts = _split_top_level(body)
                if len(parts) > 1:
                    # as ONE clause this became a single literal no page
                    # renders: green was impossible and the failure named the
                    # wrong thing.
                    # NOOD_0212 — "his born and died dates" splits to "his
                    # born" / "died"; the possessive belongs to the sentence,
                    # never to the page, and carrying it made the first member
                    # unmatchable while its twin matched fine.
                    for p in _drop_shared_noun(parts):
                        _emit(c, f"verify {_POSSESSIVE.sub('', p)}")
                    continue
        # NOOD_0223 — narration that CARRIES its own expected values.
        # _NARRATION (NOOD_0199) drops scene-setting prose, which is right
        # until the sentence quotes the very strings the page must show: "the
        # results page appears with these products: 'A' and 'B'" dropped both
        # and authored a green test with ZERO assertions. The table form of
        # the same brief (one value per `| cell |`) has compiled one check per
        # value since NOOD_0199 — two notations for one intent disagreed, and
        # the silent one won.
        #
        # Quoting is the escape hatch, exactly as NOOD_0221 settled it for the
        # summary rule, and the guard is what keeps NOOD_0199 intact: only
        # spans AFTER the narration verb count, because those are the values
        # the narration introduces. A quote inside the lead names the
        # instrument ('the "search" dropdown appears'), not page text, so that
        # clause stays narration and is still dropped.
        if (nar := _NARRATION.match(t)) \
                and not any(rx.match(t) for _, rx in _VERBS):
            members = [m.group("content")
                       for m in _QUOTED_MEMBER.finditer(t[nar.end():])]
            if members:
                assumptions.append(
                    f"step {c.get('line', '?')} '{t}': narration introducing "
                    f"{len(members)} quoted value(s) — the framing is dropped, "
                    "the quoted text is asserted")
                for p in members:
                    _emit(c, f'verify "{p}"')
                continue
        if _BARE_CLICK.match(t) and len(t.split()) <= 6 \
                and not any(rx.match(t) for _, rx in _VERBS):
            _emit(c, f"click {t}")
            continue
        _emit(c, t)
    return out


# --- assembly ------------------------------------------------------------------

def expand(text: str, base_url: str | None = None) -> dict:
    """Plain-English steps → {ok, goal, base_url, app_name, feature_path,
    translation_mode, clauses, coverage, inferences, unresolved, conflicts,
    assumptions, unrecognized}. Deterministic and pure: identical input
    yields byte-identical output. `unresolved` names clauses outside the
    grammar (model_fallback may translate those); `conflicts` names typed
    contradictions the flow itself contains (no model may guess past them)."""
    assumptions: list[str] = []
    clauses = _rewrite_asks(_clauses(text), assumptions)
    if not clauses:
        return {"ok": False, "error": "empty prompt", "assumptions": [],
                "unrecognized": [], "unresolved": [], "conflicts": [],
                "goal": None}
    nodes = [_parse_clause(c) for c in clauses]
    fi = _flow_index(nodes)
    unrecognized: list[str] = []
    unresolved, conflicts, inferences, coverage = [], [], [], []
    urls, dismissals, actions, checks = [], [], [], []
    searches: dict[int, dict] = {}      # node index → search action
    picks: dict[int, dict] = {}         # node index (of minting clause) → pick
    adds: dict[int, dict] = {}          # node index → add_to action
    consumed: set[str] = set()          # search ids already feeding a pick
    pre_action_checks: list[dict] = []  # NOOD_0199 — checks written first
    # NOOD_0226 — (check, actions-so-far) for every check written BETWEEN two
    # actions; anchored in the post-pass once we know another action followed.
    mid_flow_checks: list[tuple[dict, int]] = []
    api_calls: list[dict] = []          # NOOD_0192 — in prompt order
    meta_urls: list[str] = []           # NOOD_0209 — URLs on metadata lines
    counters = {"search": 0, "pick": 0, "add": 0, "api": 0}
    pending_evidence = False
    pending_evidence_off = False     # NOOD_0225 — the same seam, negated
    # NOOD_0211 — scanned off the RAW brief, not off a clause. The clause
    # splitter treats a trailing "evidence screenshot" as a per-step evidence
    # MARKER and strips it, so by parse time the directive had been shortened
    # to "Note : each assertion must contain an" — the exact string the old
    # refusal quoted back. Reading the whole text also lets the directive sit
    # anywhere in the brief, labelled or not.
    # NOOD_0225 — scanned per UNIT, not over the whole blob: a single step
    # that declined a picture ("… - no screenshot needed") used to switch the
    # entire run off, which is the opposite of what one line about one step
    # can mean. _run_directive only honours a unit that is nothing but the
    # directive.
    evidence_mode = _run_directive(text)

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
            # NOOD_0212 — a brief's trailing directive paragraph WRAPS across
            # lines ("…finish with the Allure + RCA report links and" /
            # "nothing else"), and the clause splitter works line by line. The
            # head matched as scaffolding and the tail — a verbless fragment
            # with nothing to resolve — refused on its own, which cost a whole
            # lap to fix by deleting words nobody meant as a step. A fragment
            # directly after brief scaffolding is the rest of that sentence.
            prev = nodes[i - 1] if i else None
            if (prev is not None and prev.get("kind") in ("metadata", "directive")
                    and not _has_verb(n["raw"]) and not _URLISH.match(n["raw"])):
                _cover(n, "metadata")
                continue
            _refuse(n, "")
            continue
        if n["kind"] == "run_mode":
            assumptions.append(
                f"step {no} '{n['raw']}': run mode is a runner flag "
                "(--headed/--headless), not a test step — ignored here")
            _cover(n, "metadata")
            continue
        if n["kind"] == "observation":
            assumptions.append(
                f"step {no} '{n['raw']}': narration of what the page does, "
                "not an instruction — ignored here")
            _cover(n, "metadata")
            continue
        if n["kind"] == "directive":
            # NOOD_0211 — run configuration, not a step. An evidence directive
            # is APPLIED (it becomes a scenario tag); anything else is named in
            # assumptions so nothing is silently dropped.
            if n.get("evidence_mode"):
                evidence_mode = n["evidence_mode"]
                # NOOD_0228 — this used to reassure the reader by NAMING the
                # step-text spelling it replaced. An assumption line is an
                # emitted surface: printing the forbidden form here taught it
                # to every agent that read a successful authoring payload.
                how = {"off": "no evidence screenshots for this scenario",
                       "all": "an evidence screenshot on every step, carried "
                              "as a scenario tag",
                       }.get(evidence_mode,
                             "an evidence screenshot on every assertion, "
                             "carried as a scenario tag")
                assumptions.append(
                    f"step {no} '{n['raw']}': read as a directive — {how}")
            else:
                assumptions.append(
                    f"step {no} '{n['raw']}': a note about the test, not a "
                    "step — ignored here")
            _cover(n, "metadata")
            continue
        if n["kind"] == "metadata":
            # NOOD_0209 — harvest, never discard: the brief's "App: … · UI
            # `<url>`" line may carry the only URL in the whole prompt.
            meta_urls.extend(u for u in _harvest_urls(n["raw"])
                             if u not in meta_urls)
            # NOOD_0199 — a labelled brief field. Named, never silent, and
            # never a blocker: refusing them made PROMPT_TEMPLATE.md, the
            # thing users paste, unusable at the `--prompt` door.
            note = ("credentials belong in `--spec` secret_values (written to "
                    "the app's gitignored secrets.env), not in a step"
                    if n["label"].startswith("credential")
                    else "brief metadata, not a test step")
            assumptions.append(f"step {no} '{n['raw']}': {note} — ignored here")
            _cover(n, "metadata")
            continue
        if n["kind"] == "evidence_only":
            pending_evidence = True
            _cover(n, "metadata")
            continue
        if n["kind"] == "nav_ref":
            if not urls and not base_url and not meta_urls and not any(
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
        # NOOD_0192 — the api wok, straight through: the call is an action,
        # the status claim is its check. No probe, no page, no inference.
        if n["kind"] in ("api", "api_batch"):
            # NOOD_0201 — a batch missing its URL or (for a write verb) its
            # body template is refused with the one-shot phrasing, so the
            # repair costs zero docs hunts.
            if n["kind"] == "api_batch" and not n.get("url"):
                _refuse(n, "a batch call needs a URL — phrase it: \"create "
                        "20 rows using POST http://<host>/<path> with body "
                        "'{\"name\":\"Row {i}\"}' expecting status 201\"")
                continue
            if n["kind"] == "api_batch" and n["method"] in \
                    ("POST", "PUT", "PATCH") and not n.get("body"):
                _refuse(n, f"a {n['method']} batch needs a body template — "
                        "append: with body '{\"name\":\"Row {i}\"}' "
                        "({i} = call number)")
                continue
            act = {"do": "api", "id": _mint("api"), "method": n["method"],
                   "url": n["url"]}
            for k in ("body", "repeat", "expect_status"):
                if n.get(k) is not None:
                    act[k] = n[k]
            api_calls.append(act)
            actions.append(act)
            _cover(n, "action", [act["id"]])
            continue
        if n["kind"] == "api_status":
            if not api_calls:
                _refuse(n, "a status assertion needs an api call before it",
                        conflict=True)
                continue
            checks.append({"status": n["status"],
                           "after": api_calls[-1]["id"]})
            _cover(n, "check")
            continue
        if n["kind"] == "search":
            act = {"do": "search", "id": _mint("search"), "term": n["term"]}
            searches[i] = act
            actions.append(act)
            _cover(n, "action", [act["id"]])
        elif n["kind"] == "pick_result":
            # NOOD_0209 — 'open the "X" result' binds to the nearest earlier
            # search as a first-class pick; with no search to bind to, a
            # NAMED title is still a control the page may carry (a click),
            # while a bare "first result" has nothing to be first OF.
            back = [(j, s) for j, s in sorted(searches.items())
                    if j < i and s["do"] == "search"
                    and s["id"] not in consumed]
            if back:
                j, src = back[-1]
                pick = {"do": "pick", "id": _mint("pick"), "from": src["id"]}
                if n.get("item"):
                    pick["target"] = n["item"]
                else:
                    pick["strategy"] = "first_actionable"
                consumed.add(src["id"])
                picks[i] = pick
                actions.append(pick)
                assumptions.append(
                    f"step {no} '{n['raw']}': picking "
                    + (f"'{n['item']}'" if n.get("item")
                       else "the first actionable result")
                    + f" from step {_step_no(nodes[j])}'s search "
                      f"'{src['term']}' results")
                _cover(n, "action", [pick["id"]])
            elif n.get("item"):
                actions.append({"do": "click", "target": n["item"]})
                _cover(n, "action")
            else:
                _refuse(n, "picking the first result needs a search step "
                            "before it", conflict=True)
                continue
        elif n["kind"] == "click":
            # NOOD_0185 — "click the suggestion X" after a search is the
            # typeahead flow, benchmark-proven unreachable by prompt before
            # this: the search action itself becomes a suggest (type the
            # term, pick option X from the dropdown, never submit).
            # NOOD_0199 — 'the Suggestion : "X"': humans space the colon and
            # quote the option; both used to leave a click on the label.
            sug = re.match(r"^(?:the\s+)?suggestions?\s*:?\s+(.+)$",
                           n["target"], re.I)
            if not sug:
                # NOOD_0212 — the other word order, just as common in briefs:
                # "click the vacuum-cleaner suggestion". A trailing aside
                # ("…, worded exactly as the site renders it") says HOW to
                # find the option and is never part of its text; without this
                # the whole descriptive phrase became a click target and
                # blocked against every control the probe had seen.
                sug = re.match(r"^(?:the\s+)?(.+?)\s+suggestions?\b\s*(?:,.*)?$",
                               n["target"], re.I)
            back = [s for j, s in sorted(searches.items())
                    if j < i and s["do"] == "search"]
            if sug and back:
                src = back[-1]
                src.update(do="suggest",
                           option=_clean(sug.group(1)).strip("\"'"))
                _cover(n, "action", [src["id"]])
                assumptions.append(
                    f"step {no} '{n['raw']}': picking '{src['option']}' from "
                    f"the search suggestions for '{src['term']}' (typeahead — "
                    "the search is never submitted)")
            elif sug:
                _refuse(n, "clicking a suggestion needs a search step first",
                        conflict=True)
                continue
            else:
                actions.append({"do": "click", "target": n["target"]})
                _cover(n, "action")
        elif n["kind"] == "click_within":
            # NOOD_0226 — one action, scoped: `within:` is what makes a
            # repeated per-row control addressable (NOOD_0207/0222).
            actions.append({"do": "click", "target": n["target"],
                            "within": n["within"]})
            _cover(n, "action")
        elif n["kind"] == "enter":
            actions.append({"do": "enter", "target": n["target"],
                            "value": n["value"]})
            _cover(n, "action")
        elif n["kind"] == "select":
            actions.append({"do": "select", "target": n["target"],
                            "option": n["option"]})
            _cover(n, "action")
        # NOOD_0188 — literal translation, same as the verbs above: the clause
        # names the action and its target, nothing is inferred.
        elif n["kind"] in ("check", "uncheck", "hover"):
            actions.append({"do": n["kind"], "target": n["target"]})
            _cover(n, "action")
        elif n["kind"] == "upload":
            actions.append({"do": "upload", "target": n["target"],
                            "file": n["file"]})
            _cover(n, "action")
        elif n["kind"] == "pick_date":
            actions.append({"do": "pick_date", "target": n["target"],
                            "date": n["date"]})
            _cover(n, "action")
        elif n["kind"] == "press_key":
            actions.append({"do": "press_key", "key": n["key"]})
            _cover(n, "action")
        elif n["kind"] == "go_back":
            actions.append({"do": "go_back"})
            _cover(n, "action")
        elif n["kind"] == "add_to":
            item, dest = n["item"], n["destination"]
            back = [(j, s) for j, s in searches.items() if j < i]
            informative = not _is_anaphoric(item) and not (
                back and _tokens(_clean(item)) <= _tokens(back[-1][1]["term"]))
            if informative:
                if not back:
                    # NOOD_0207 — the single biggest cost sink in the reviewed
                    # sessions. On a catalogue or card grid there IS no search
                    # box, so "needs a search step first" demanded a control
                    # that does not exist: the reader burned laps inventing
                    # search/pick/add_to chains, and one session gave up and
                    # shipped a green test for the WRONG item. The item names
                    # itself — `within` scopes the mutation to its card.
                    add = {"do": "add_to", "id": _mint("add"),
                           "within": item, "destination": dest}
                    adds[i] = add
                    actions.append(add)
                    _cover(n, "action", [add["id"]])
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
            # NOOD_0221 — a COVERAGE SUMMARY, not an assertion. Briefs end
            # with "Verify: Suggestion search works" — the author naming what
            # the test is about, in the same register as the `AC:` and `Note:`
            # lines already classified as metadata. No page renders that
            # sentence, so it compiled to a see-check that could only be
            # dropped later (0218 benchmark TC1: the one row whose
            # intent_verified read false, on a test that proved every real
            # assertion asked of it).
            #
            # Deliberately NARROW, because NOOD_0212's lesson is that a
            # wording rule which guesses eats real assertions: the clause must
            # END in the summary verb and carry NO quoted text (quoted content
            # is a claim about the page, always). "verify the totals are
            # correct" keeps its check; "verify checkout works" does not.
            if _SUMMARY_CLAIM.search(rest) and not _QUOTED_MEMBER.search(rest):
                assumptions.append(
                    f"step {no} '{n['raw']}': reads as a summary of what the "
                    "test covers, not text to find on the page — no assertion "
                    "compiled for it (quote the exact wording to assert it)")
                _cover(n, "metadata")
                continue
            # NOOD_0211 — the status claim _rewrite_asks normalized. Anchored
            # to `start`: it is the landing navigation's own response, not
            # whatever page the flow ends on.
            if st := re.match(r"^page status (\d{3})$", rest, re.I):
                checks.append({"page_status": int(st.group(1)),
                               "after": "start"})
                _cover(n, "check")
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
                    # NOOD_0207 — a searchless add names its item with
                    # `within`, so that text is a flow subject too.
                    subjects = [searches[k]["term"] for k in searches] + \
                        [p.get("target") or "" for p in picks.values()] + \
                        [a.get("within") or "" for _, a in all_adds]
                    if not _is_anaphoric(item_word) and not any(
                            _overlaps(item_word, s) for s in subjects if s):
                        _refuse(n, f"verifies '{item_word}' but the flow's "
                                "item comes from "
                                f"'{next(iter(searches.values()))['term']}'"
                                if searches else
                                f"verifies '{item_word}' — no flow item "
                                "matches", conflict=True)
                        continue
                    check = ({"any_of": [add["within"]], "after": add["id"]}
                             # NOOD_0207 — no pick, so no bound caption to
                             # point expected_from at: the `within` text IS
                             # the identity. Same rule as the pick path —
                             # identity, never a count.
                             if add.get("within") and not add.get("item_from")
                             else {"item_in_destination": add["destination"],
                                   "expected_from": add["item_from"],
                                   "after": add["id"]})
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
                check = ({"any_of": [add["within"]], "after": add["id"]}
                         if add.get("within") and not add.get("item_from")
                         else {"item_in_destination": add["destination"],
                               "expected_from": add["item_from"],
                               "after": add["id"]})
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
                # NOOD_0185 — the benchmark caught this: a plain `verify
                # <text>` used to emit an any_of check, which compiles to a
                # link/title/alt-scoped "result titles" count locator — it can
                # never match a <label> or other plain page text. The stated
                # intent ("literal text is visible") maps to `see`, which
                # compiles to `the user sees "<text>"`. Wrapping quotes from
                # the prompt are part of the quoting, not the text.
                # NOOD_0211 — only here, after every item-in-destination
                # shape has had its chance: "<page-ish subject> contains
                # <text>" names where to look, then what to find, and only
                # the second half is the assertion. Stripping it earlier
                # would eat "cart has toy", whose subject IS the destination.
                text = _VERIFY_FILLER.sub(
                    "", _strip_containment(rest.strip())).strip()
                # NOOD_0188 — two shapes the `see` fallback cannot express,
                # both read straight off the clause (no inference):
                #   "<text> is not visible" / "no <text>"  → not_see
                #   "the url contains <part>"              → url_contains
                neg = re.match(
                    r"^(?:there\s+(?:is|are)\s+)?(?:no|not)\s+(.+)$|"
                    r"^(.+?)\s+(?:is|are)\s+(?:not|no\s+longer)\s+"
                    r"(?:visible|shown|displayed|present|there)$",
                    text, re.I)
                url_m = re.match(
                    r"^(?:the\s+)?(?:url|address|link)\s+"
                    r"(?:contains?|includes?|has|is|ends?\s+with)\s+(.+)$",
                    text, re.I)
                # NOOD_0192 — "see if url is 200" is an HTTP status claim, and
                # it must be read BEFORE url_contains, whose "url is <x>" arm
                # would compile it into a browser assertion that the address
                # bar contains "200".
                status_m = _STATUS_CLAIM.match(text)
                body_m = _BODY_CLAIM.match(text)
                # NOOD_0197 — result-set assertions: "at least 1 result is
                # found with title Hot Wheels or Die Cast".
                res_m = re.match(
                    r"^at\s+least\s+(?P<min>\d+)\s+"
                    r"(?:results?|items?|matches?|entries?|products?|rows?)"
                    r"(?:\s+(?:is|are|were|was))?"
                    r"(?:\s+(?:found|shown|displayed|listed|returned|"
                    r"present|visible))?"
                    r"(?:\s+(?:with|having|containing)"
                    r"(?:\s+(?:the\s+)?(?:titles?|names?|texts?|labels?))?"
                    r"\s+(?P<terms>.+))?$", text, re.I)
                if status_m or body_m:
                    if not api_calls:
                        _refuse(n, "an api assertion needs an api call "
                                   "before it", conflict=True)
                        continue
                    if status_m:
                        check = {"status": int(status_m.group("code")),
                                 "after": api_calls[-1]["id"]}
                        assumptions.append(
                            f"step {no} '{n['raw']}': asserting the response "
                            f"status is {status_m.group('code')}")
                    else:
                        needle = _clean(body_m.group("needle")).strip("\"'")
                        check = {"response_contains": needle,
                                 "after": api_calls[-1]["id"]}
                        assumptions.append(
                            f"step {no} '{n['raw']}': asserting the response "
                            f"body contains '{needle}'")
                elif url_m:
                    part = _clean(url_m.group(1)).strip("\"'")
                    check = {"url_contains": part}
                    assumptions.append(
                        f"step {no} '{n['raw']}': asserting the URL contains "
                        f"'{part}'")
                elif neg:
                    gone = _clean(neg.group(1) or neg.group(2)).strip("\"'")
                    check = {"not_see": gone}
                    assumptions.append(
                        f"step {no} '{n['raw']}': asserting '{gone}' is NOT "
                        "visible")
                elif res_m:
                    # NOOD_0197 — "at least N results [with title A or B]".
                    # Titled → any_of (compiles to ONE disjunctive step);
                    # untitled → a plain results-count check.
                    raw_terms = res_m.group("terms") or ""
                    want = int(res_m.group("min") or 1)
                    terms = _split_alternatives(raw_terms) or (
                        [_clean(raw_terms).strip("\"'")]
                        if raw_terms.strip() else [])
                    if terms:
                        check = {"any_of": terms, "min": want}
                        assumptions.append(
                            f"step {no} '{n['raw']}': asserting at least "
                            f"{want} of " + " / ".join(terms) + " is shown")
                    else:
                        check = {"count": "results", "min": want}
                        assumptions.append(
                            f"step {no} '{n['raw']}': asserting the results "
                            f"count is at least {want}")
                elif len(alts := _split_alternatives(text)) > 1:
                    # NOOD_0197 — "verify A or B" is a disjunction. (Plain
                    # "verify <text>" stays `see` — NOOD_0185's rule — this
                    # branch needs an explicit unquoted ' or '.)
                    check = {"any_of": alts}
                    assumptions.append(
                        f"step {no} '{n['raw']}': asserting any of "
                        + " / ".join(alts) + " is visible")
                elif sb := re.match(
                        r"^(?P<subj>.{1,60}?)\s+should\s+"
                        r"(?:be|show|display|read|say|equal|contain)s?\s+"
                        r"(?P<val>.+)$", text, re.I):
                    # NOOD_0218 — "<label> should be <value>" is a label+value
                    # claim; as ONE literal it asserts a sentence no page
                    # renders ("subtotal should be $18.99" — the page says
                    # 'Subtotal' and '$18.99'). The label and the value are
                    # each real page text: two see-checks, said out loud.
                    subj = _ARTICLE.sub(
                        "", _clean(sb.group("subj")).strip("\"'"))
                    val = _clean(sb.group("val")).strip("\"'").rstrip(".")
                    if val.casefold() in _PRESENCE_TAILS:
                        # "X should be visible" is a presence claim on X, not
                        # on the word "visible"
                        check = {"see": subj}
                        assumptions.append(
                            f"step {no} '{n['raw']}': asserting the literal "
                            f"text '{subj}' is visible")
                    elif re.match(r"^(?:the\s+)?(?:url|address|link)$",
                                  subj, re.I):
                        check = {"url_contains": val}
                        assumptions.append(
                            f"step {no} '{n['raw']}': asserting the URL "
                            f"contains '{val}'")
                    elif _WEBSITE_REF.match(subj):
                        # "the page should contain X" names WHERE to look;
                        # only X is the assertion — a `see "page"` check
                        # would assert a word no page renders as content
                        # (checked AFTER the url arm: _WEBSITE_REF matches
                        # "url" too, and that one is an address claim)
                        check = {"see": val}
                        assumptions.append(
                            f"step {no} '{n['raw']}': asserting the literal "
                            f"text '{val}' is visible")
                    else:
                        extra = {"see": subj}
                        if not actions:
                            pre_action_checks.append(extra)
                        checks.append(extra)
                        check = {"see": val}
                        assumptions.append(
                            f"step {no} '{n['raw']}': split into two "
                            f"visibility checks — '{subj}' (label) and "
                            f"'{val}' (value); no page renders the sentence "
                            "itself")
                else:
                    # NOOD_0197 — "the Weekly Flyer is shown": the positive
                    # visibility tail and the leading article are phrasing,
                    # not page text; keeping them makes the literal stricter
                    # than the page (substring matching: shorter is safer).
                    text = re.sub(r"\s+(?:is|are)\s+(?:visible|shown|"
                                  r"displayed|present)$", "", text, flags=re.I)
                    text = _ARTICLE.sub("", text)
                    quoted = (len(text) > 1 and text[0] == text[-1]
                              and text[0] in "\"'")
                    if quoted:
                        text = text[1:-1]
                    # NOOD_0220 — the UNQUOTED twin of _PRESENCE_RESIDUE.
                    # '"Acme" logo is present' already lost its noun; "verify
                    # BeanCounter ERP banner is present" kept it and asserted
                    # 'BeanCounter ERP banner' — text no page renders, and a
                    # measured red run (0218 benchmark TC5) on a page that
                    # plainly showed the thing. The trailing noun names the
                    # ELEMENT; what precedes it is the page text. Quoted
                    # content is DATA and is never rewritten (the engine's
                    # standing rule), and the strip is safe by construction:
                    # it can only shorten, the runtime matches by substring,
                    # so an assertion that would have passed still passes.
                    if not quoted and (
                            stripped := _TRAILING_NOUN.sub("", text).strip()) \
                            and stripped != text:
                        assumptions.append(
                            f"step {no} '{n['raw']}': the trailing noun names "
                            f"the element, not page text — asserting "
                            f"'{stripped}', not '{text}'")
                        text = stripped
                    check = {"see": text}
                    assumptions.append(
                        f"step {no} '{n['raw']}': asserting the literal text "
                        f"'{text}' is visible")
            if n["evidence"] or pending_evidence:
                check["evidence"] = "screenshot"
                pending_evidence = False
            elif n.get("evidence_off") or pending_evidence_off:
                # NOOD_0225 — the tester declined a picture for THIS step. Say
                # so on the step itself, or the run-wide default ('last')
                # would still shoot it when it happens to end the scenario.
                check["evidence"] = "none"
                pending_evidence_off = False
            if not actions and "after" not in check:
                # NOOD_0199 — prompt ORDER is the anchor. A check written
                # BEFORE any action observes the landing page; unanchored, it
                # scopes to the post-search page (goal.py `_check_scope`), so
                # the evidence pass looked for it on the wrong page and
                # blocked a fact the probe had proven. Only anchored once an
                # action actually follows — see the post-pass below.
                pre_action_checks.append(check)
            elif actions and "after" not in check:
                # NOOD_0226 — the same rule, MID-flow. An unanchored check
                # compiles after the LAST action (NOOD_0158), so a brief that
                # says "5. open the cart 6. verify the item and total 7. check
                # out" asserted the cart's contents on the page checkout left
                # behind — the cart it had just emptied. Measured live: three
                # correct assertions, all run one page too late, and the run
                # is red for a flow that worked. Recorded now, anchored in the
                # post-pass only if another action actually follows: a
                # TRAILING check is already in the right place, and anchoring
                # it would change compiled order for every existing flow.
                mid_flow_checks.append((check, len(actions)))
            checks.append(check)
            _cover(n, "check")
        if n.get("evidence") and n["kind"] != "verify":
            pending_evidence = True
        elif n.get("evidence_off") and n["kind"] != "verify":
            pending_evidence_off = True

    # a trailing evidence request attaches to the last check
    if pending_evidence and checks and "evidence" not in checks[-1]:
        checks[-1]["evidence"] = "screenshot"
    elif pending_evidence_off and checks and "evidence" not in checks[-1]:
        checks[-1]["evidence"] = "none"
    if actions:
        # NOOD_0199 — anchor the checks the prompt put BEFORE its first
        # action to the landing page. Skipped when the goal has no actions
        # at all: there is no later page for them to be confused with.
        for c in pre_action_checks:
            c["after"] = "start"
        # NOOD_0226 — anchor a check to the action it was written after, but
        # only when the brief goes on to DO something else: that is the whole
        # class of "assert, then act again" flows, and the only one whose
        # compiled order is wrong today.
        for check, n_before in mid_flow_checks:
            if n_before >= len(actions):
                continue                    # nothing followed — already last
            anchor = actions[n_before - 1]
            if not anchor.get("id"):
                anchor["id"] = f"step{n_before}"
            check["after"] = anchor["id"]

    if unrecognized:
        # NOOD_0197 — a partial parse is returned, never discarded: the goal
        # built from the clauses that DID parse plus a concrete rewrite per
        # unresolved clause. One unknown step out of six no longer throws the
        # other five away.
        for u in unresolved:
            s = _suggest(u.get("text") or "")
            if s:
                u["suggested"] = s
        partial = None
        if actions or checks or urls:
            partial = {"scenario": "partial prompt flow",
                       "dismissals": dismissals,
                       "actions": actions, "checks": checks}
            if urls:
                partial["navigation"] = urls
        return {"ok": False,
                # NOOD_0207 — the ~0.9 KB grammar dump became a pointer: each
                # unresolved clause already carries its own `suggested`
                # rewrite, which is the part a reader acts on, and the dump
                # was then re-sent on every later call in the turn.
                "error": "prompt step(s) not understood — rewrite them (each "
                         "unresolved clause carries a `suggested` rewrite) or "
                         "author with goal; full grammar: "
                         "noodle author --vocabulary",
                "unrecognized": unrecognized, "unresolved": unresolved,
                "conflicts": conflicts, "assumptions": assumptions,
                "clauses": clauses, "coverage": coverage, "goal": None,
                "goal_partial": partial}
    # NOOD_0192 — a pure-API prompt has no page to open, so its package is
    # named after the endpoint it calls. Without this the api wok could never
    # be reached from a prompt at all: "no URL in the prompt" for a prompt
    # made entirely of URLs.
    first_url = urls[0] if urls else None
    api_only = bool(api_calls) and not urls and not any(
        a["do"] != "api" for a in actions)
    if not first_url and not base_url and api_only:
        # NOOD_0201 — only an ABSOLUTE call can name the package; a relative
        # path (/api/greeting) has no host and needs base_url (given, or
        # filled by author_test's localhost discovery before this runs).
        base_url = next((a["url"] for a in api_calls
                         if str(a["url"]).startswith("http")), None)
    if not first_url and not base_url and meta_urls:
        # NOOD_0209 — the brief's metadata line held the only URL; spend it
        # rather than refusing a prompt that plainly named its app.
        base_url = meta_urls[0]
        assumptions.append(
            f"base URL taken from the brief's metadata line: {base_url}")
    if not first_url and not base_url:
        return {"ok": False,
                "error": "no URL in the prompt and no base_url given — "
                         "add a 'go to <url>' step or pass base_url "
                         "(api steps may use relative paths once base_url "
                         "is known; a running localhost app is discovered "
                         "automatically)",
                "unrecognized": [], "unresolved": [], "conflicts": [],
                "assumptions": assumptions, "clauses": clauses,
                "coverage": coverage, "goal": None}
    if not actions and not checks:
        # NOOD_0199 — the silent-empty class. Dismissals, navigation and
        # metadata all parse without producing a step, so a prompt whose flow
        # was swallowed came back ok:true with an EMPTY goal — the engine
        # claiming success for a test that does nothing. It is a blocker, and
        # it says what to write instead.
        return {"ok": False,
                "error": "the prompt parsed to setup only — no action and no "
                         "check, so there is nothing to test. Write the flow "
                         "as numbered steps: 1. go to <url> 2. search for "
                         "\"<term>\" 3. verify \"<text>\" — full grammar: "
                         "noodle author --vocabulary",
                "unrecognized": [], "conflicts": [],
                "unresolved": [{"clause": c["id"], "text": c["text"],
                                "reason": "parsed, but contributes no action "
                                          "or check",
                                "suggested": _suggest(c["text"])
                                or "rewrite as: search for \"<term>\""}
                               for c in clauses],
                "assumptions": assumptions, "clauses": clauses,
                "coverage": coverage, "goal": None, "goal_partial": None}
    if api_only:
        # no browser, so no popups to dismiss — an empty list keeps the goal
        # honest about what it does.
        dismissals = []
    elif not dismissals:
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
        elif a["do"] == "suggest":
            labels.append(f"suggestion '{a['option']}' for '{a['term']}'")
        elif a["do"] == "add_to":
            labels.append(f"add to {a['destination']}")
        elif a["do"] == "press_key":
            labels.append(f"press {a['key']}")
        elif a["do"] == "go_back":
            labels.append("go back")
        elif a["do"] == "api":
            labels.append(f"{a['method']} {urlsplit(a['url']).path or '/'}")
        elif a["do"] in ("click", "enter", "select", "check", "uncheck",
                         "hover", "upload", "pick_date"):
            # NOOD_0188 — every targeted verb reaches the scenario title; a
            # missing branch silently dropped the action from the name.
            labels.append(f"{a['do'].replace('_', ' ')} "
                          f"{a.get('target', '')}".strip())
    for c in checks:
        if "item_in_destination" in c:
            labels.append(f"verify {c['item_in_destination']}")
        elif "status" in c:
            labels.append(f"status {c['status']}")
    # NOOD_0209 — a hard [:80] cut mid-label left Feature: titles truncated
    # mid-quote and unbalanced, and the filename slugged from that fragment.
    # Truncate at a label boundary, and never leave a dangling quote.
    scenario = ", ".join(labels)
    if len(scenario) > 80:
        cut = scenario[:80]
        scenario = cut.rsplit(", ", 1)[0] if ", " in cut else cut
    if scenario.count("'") % 2:
        scenario = re.sub(r"\s*'[^']*$", "", scenario).strip(" ,")
    scenario = scenario or "prompt flow"

    goal = {"scenario": scenario, "dismissals": dismissals,
            "actions": actions, "checks": checks}
    if urls:
        goal["navigation"] = urls
    if evidence_mode:
        # NOOD_0211 — compiles to a scenario tag, so the feature file stays
        # free of per-step evidence-request noise.
        goal["evidence"] = evidence_mode

    app = app_from_url(first_url or base_url)
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


def app_from_url(url: str) -> str:
    """App-name slug from a URL's host — the ONE derivation both the prompt
    path and goal-mode (NOOD_0213) use, so they can't drift."""
    host = urlsplit(url or "").netloc
    app = re.sub(r"[^a-z0-9]+", "_",
                 host.casefold().removeprefix("www.")).strip("_") or "app"
    if app[0].isdigit():
        # NOOD_0201 — an IP host (127.0.0.1:8080) slugs to a leading digit,
        # which is not a legal {env:} key.
        app = "app_" + app
    return app


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
        # NOOD_0188 — every TARGETED verb is gated, not just the original
        # three: an ungated one could have its control label invented by the
        # model fallback with no source clause behind it.
        if a.get("do") in goal_mod._TARGETED_ACTIONS and a.get("target"):
            if not _overlaps(a["target"], text):
                problems.append(
                    f'{a["do"]} "{a["target"]}" has no source clause — '
                    "surface controls come from the prompt or probe "
                    "evidence, never from translation")
    # NOOD_0211 — a run-wide evidence directive ("each assertion must contain
    # an evidence screenshot") is satisfied by the scenario TAG, not by a
    # marker on one check. Demanding a per-check marker here failed the
    # contract on exactly the brief that asked for the most evidence.
    if any(c.get("evidence") or c.get("evidence_only")
           for c in exp.get("clauses") or []) \
            and norm.get("evidence") not in ("assertions", "all"):
        if not any(ch.get("evidence") == "screenshot"
                   for ch in norm.get("checks") or []):
            problems.append("the prompt requested screenshot evidence but "
                            "no check carries it")
    # NOOD_0225 — and the same contract in the negative. A brief that declines
    # evidence is making a decision, so a compiled goal that still shoots is
    # as much a contract break as one that silently drops a requested shot.
    if norm.get("evidence") == "off" \
            and any(ch.get("evidence") == "screenshot"
                    for ch in norm.get("checks") or []):
        problems.append("the prompt declined screenshot evidence but a check "
                        "still requests one")
    if any(c.get("evidence_off") for c in exp.get("clauses") or []) \
            and norm.get("evidence") != "off" \
            and not any(ch.get("evidence") == "none"
                        for ch in norm.get("checks") or []):
        problems.append("the prompt declined evidence on a step but no check "
                        "carries the opt-out")
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
