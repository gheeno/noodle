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

_MAX_CLAUSE_LEN = 600   # NOOD_0177 — a prompt clause is a sentence; bounds backtracking
_BULLET = re.compile(r"^\s*(?:\d+\s*[.)]\s*|[-*•—–>]\s+)")   # NOOD_0199: — – >
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
# NOOD_0207 — the span now swallows its own punctuation and tail. It used to
# strip only the word "screenshot", so "... is present -  on this step" was
# left behind as the literal to assert: an assertion nothing renders, failing
# with a message that named the wrong thing.
_EVIDENCE = re.compile(
    r"(?:\s*[-–—,]\s*)?(?:\band\s+)?(?:take\s+(?:a\s+)?)?"
    r"(?:\bevidence\b\s*[:-]?\s*)?"
    r"(?:\bscreenshots?\b|\bcaptures?\b(?:\s+(?:the\s+)?(?:screen|page))?)"
    r"(?:\s+for\s+verification)?"
    r"(?:\s+(?:on\s+this\s+step|here|as\s+evidence))?", re.I)
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
_CONJ = re.compile(r"\s+(?:and(?:\s+then)?|then)\s+", re.I)

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
_INSTRUMENT = re.compile(
    r"^(?:uses?|using)\s+(?:the\s+)?[^,\"']{1,40}?\s+to\s+(?=[a-z])"
    r"|^(?:uses?|using|via)\s+(?:the\s+)?[^,\"']{1,40}?\s*,\s*", re.I)

# NOOD_0199 — PROMPT_TEMPLATE.md is a LABELLED brief ("Base URL: [ … ]",
# "User goal: …"), and a human pastes it whole. Every label line was a clause
# outside the grammar, so the template Noodle ships hard-failed its own
# `--prompt` door. Three families: a URL label IS a navigation step, a goal
# label wraps the flow, and the rest is brief metadata (named, never a step).
_URL_LABEL = re.compile(
    r"^(?:base|target|start(?:ing)?|site|app)?\s*url\s*:\s*", re.I)
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
    r"purpose|context)\s*:\s*", re.I)
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
    r"(?:appears?|shows?(?:\s+up)?|opens?|loads?|is\s+(?:shown|displayed))\b",
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
              "close popups / location prompt; take a screenshot")

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
        ln = f"go to {value}"
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
        evidence = bool(_EVIDENCE.search(frag))
        body = _EVIDENCE.sub("", frag).strip().strip("+&,;:- ")
        out.append({"id": f"clause-{len(out) + 1}", "text": body or frag,
                    "line": line_no, "evidence": evidence,
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
            "line": c["line"], "evidence": c["evidence"]}
    if c.get("evidence_only"):
        node["kind"] = "evidence_only"
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
_VALUE_TAIL = re.compile(r"\s*,\s*(?:and\s+)?|\s+and\s+", re.I)
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


def _split_conjuncts(body: str) -> list[str]:
    body = (body or "").strip()
    if re.search(r"\bor\b", body, re.I):
        return []
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
# B3 — a short terminal imperative IS the control's label ("place the order").
_BARE_CLICK = re.compile(
    r"^(?:proceed|continue|submit|confirm|checkout|check\s+out|place|pay|"
    r"finish|complete)\b", re.I)


def _rewrite_asks(clauses: list[dict]) -> list[dict]:
    out: list[dict] = []

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
            # NOOD_0209 — quoted conjunction: one check per member. A claim
            # lead the grammar already reads ('the article shows "A", "B"
            # and "C"') is phrasing; the quoted members are the assertion.
            conj = _split_conjuncts(body)
            if not conj and (h := _HAS.match(body)):
                conj = _split_conjuncts(h.group(2))
            if conj:
                for p in conj:
                    _emit(c, f'verify "{p}"')
                continue
            if pr := _PRESENCE_RESIDUE.match(body):
                _emit(c, f'verify "{pr.group("content")}"')
                continue
            if not _VERIFY_NO_SPLIT.search(body):
                parts = [p.strip() for p in _VALUE_TAIL.split(body)
                         if p.strip()]
                if len(parts) > 1:
                    # as ONE clause this became a single literal no page
                    # renders: green was impossible and the failure named the
                    # wrong thing.
                    for p in parts:
                        _emit(c, f"verify {p}")
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
    clauses = _rewrite_asks(_clauses(text))
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
    pre_action_checks: list[dict] = []  # NOOD_0199 — checks written first
    api_calls: list[dict] = []          # NOOD_0192 — in prompt order
    meta_urls: list[str] = []           # NOOD_0209 — URLs on metadata lines
    counters = {"search": 0, "pick": 0, "add": 0, "api": 0}
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
        if n["kind"] == "observation":
            assumptions.append(
                f"step {no} '{n['raw']}': narration of what the page does, "
                "not an instruction — ignored here")
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
                text = _VERIFY_FILLER.sub("", rest.strip()).strip()
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
                else:
                    # NOOD_0197 — "the Weekly Flyer is shown": the positive
                    # visibility tail and the leading article are phrasing,
                    # not page text; keeping them makes the literal stricter
                    # than the page (substring matching: shorter is safer).
                    text = re.sub(r"\s+(?:is|are)\s+(?:visible|shown|"
                                  r"displayed|present)$", "", text, flags=re.I)
                    text = _ARTICLE.sub("", text)
                    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
                        text = text[1:-1]
                    check = {"see": text}
                    assumptions.append(
                        f"step {no} '{n['raw']}': asserting the literal text "
                        f"'{text}' is visible")
            if n["evidence"] or pending_evidence:
                check["evidence"] = "screenshot"
                pending_evidence = False
            if not actions and "after" not in check:
                # NOOD_0199 — prompt ORDER is the anchor. A check written
                # BEFORE any action observes the landing page; unanchored, it
                # scopes to the post-search page (goal.py `_check_scope`), so
                # the evidence pass looked for it on the wrong page and
                # blocked a fact the probe had proven. Only anchored once an
                # action actually follows — see the post-pass below.
                pre_action_checks.append(check)
            checks.append(check)
            _cover(n, "check")
        if n.get("evidence") and n["kind"] != "verify":
            pending_evidence = True

    # a trailing "take a screenshot" step attaches to the last check
    if pending_evidence and checks and "evidence" not in checks[-1]:
        checks[-1]["evidence"] = "screenshot"
    if actions:
        # NOOD_0199 — anchor the checks the prompt put BEFORE its first
        # action to the landing page. Skipped when the goal has no actions
        # at all: there is no later page for them to be confused with.
        for c in pre_action_checks:
            c["after"] = "start"

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

    host = urlsplit(first_url or base_url).netloc
    app = re.sub(r"[^a-z0-9]+", "_",
                 host.casefold().removeprefix("www.")).strip("_") or "app"
    if app[0].isdigit():
        # NOOD_0201 — an IP host (127.0.0.1:8080) slugs to a leading digit,
        # which is not a legal {env:} key.
        app = "app_" + app
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
        # NOOD_0188 — every TARGETED verb is gated, not just the original
        # three: an ungated one could have its control label invented by the
        # model fallback with no source clause behind it.
        if a.get("do") in goal_mod._TARGETED_ACTIONS and a.get("target"):
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
