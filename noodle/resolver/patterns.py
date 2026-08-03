import re
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # ponytail: only fail at lookup time, not import time


def _q(s: str) -> str:
    """Strip surrounding single or double quotes from a captured group."""
    s = s.strip()
    if len(s) >= 2 and s[0] in ('"', "'") and s[-1] == s[0]:
        return s[1:-1]
    return s


def _alternatives(raw: str) -> list[str]:
    """NOOD_0197 — the quoted alternatives of a disjunctive assertion:
    '"A", "B" or "C"' → ['A', 'B', 'C']."""
    return re.findall(r'["\']([^"\']+)["\']', raw)


# NOOD_0217 — THE control-noun list, single definition. The step grammar
# below strips these trailing nouns from a locator ('the "Save" button' → the
# element named Save); prompt_expander imports this same list to strip a
# LEADING noun before a quoted claim ('the textbox "Email"' → "Email"). A
# unit test guards that every inline alternation in this file matches this
# constant — edit them together or the two grammars drift.
CONTROL_NOUNS = "button|field|input|box|link|checkbox|element|icon|dropdown|menu"

# NOOD_0182 — per-step REST budget: "... within 90 seconds" on any step that
# waits on a backend (REST call, API call, response wait). Optional; without it
# the run-wide NOODLE_REST_TIMEOUT applies.
_WITHIN = r'(?:(?: and)? (?:with)?in (?:up to )?(\d+(?:\.\d+)?) seconds?)?'


def _secs(raw: str | None) -> float | None:
    return float(raw) if raw else None


def _ms(raw: str | None) -> int | None:
    return int(float(raw) * 1000) if raw else None


def _ignore_list(raw: str | None) -> list[str]:
    """NOOD_0188 — "the clock, the avatar and the ad banner" → three locator
    phrases to mask before a pixel diff. Quotes are optional; `and`/commas both
    separate, which is how a tester writes the tail without thinking about it."""
    if not raw:
        return []
    parts = re.split(r'\s*,\s*|\s+and\s+', raw.strip())
    # Each part sheds its own leading article — the pattern only strips the
    # first one, so "the clock and the avatar" would keep "the avatar".
    return [_q(re.sub(r'^the\s+', '', p.strip())) for p in parts if p.strip()]


# NOOD_0152 — direction words → pixel deltas, for the mouse-level drags.
_DIRECTION_DELTA = {'right': (1, 0), 'left': (-1, 0), 'down': (0, 1), 'up': (0, -1)}


def _offset(locator: str, amount: str, direction: str) -> dict:
    """"drags 'X' 100 pixels right" → a dx/dy offset drag."""
    dx, dy = _DIRECTION_DELTA[direction.lower()]
    n = int(amount)
    return {'locator': _q(locator), 'dx': dx * n, 'dy': dy * n}


def _edge_offset(locator: str, amount: str, direction: str) -> dict:
    """Same, but grabbing a border instead of the centre. The resize handle
    sits on the trailing edge — a horizontal drag grabs the right border and
    pulls either way from there, a vertical one the bottom."""
    d = direction.lower()
    dx, dy = _DIRECTION_DELTA[d]
    n = int(amount)
    return {'locator': _q(locator), 'dx': dx * n, 'dy': dy * n,
            'edge': 'right' if d in ('right', 'left') else 'bottom'}


def _no_mail_adapter(noun: str):
    """NOOD_0152 — general message-content assertions have no adapter, so
    these steps must fail HERE, at resolution: they otherwise fall through to
    assert_compare, which string-compares the literal words "the email" and
    produces a red nobody can diagnose. Refusing honestly beats pretending.
    NOOD_0184 — the email arm now points at the one thing Noodle DOES read
    email for: pulling an MFA/verification code out of an IMAP inbox."""
    if noun == 'email':
        raise AssertionError(
            "Noodle reads email only to extract one-time codes, not to "
            "assert on message content. For an MFA/verification code:\n"
            "  When User waits for an email code and stores it in {var:OTP}\n"
            "(needs NOODLE_IMAP_HOST/USER/PASSWORD in secrets.env). For "
            "anything else, assert the on-page confirmation instead."
        )
    raise AssertionError(
        f"Noodle has no {noun} adapter, so it can't read {noun} messages. "
        f"Assert the on-page confirmation instead, or fetch the message "
        f"yourself and assert on that:\n"
        f"  When User calls the function 'mailbox:latest' and stores the "
        f"result as `BODY`\n"
        f"  Then `BODY` should contain 'Verify your account'"
    )


def _no_native_dialog(kind: str):
    """NOOD_0186 — the OS print dialog and the OS file picker render outside
    the page: no DOM, no OCR bridge, no Playwright handle. A step that clicks
    'inside' them can only ever time out hunting the DOM, so it fails HERE,
    at resolution, with the step that covers the underlying intent (same
    ethos as the email/SMS refusals)."""
    if kind == 'print':
        raise AssertionError(
            "The browser's print dialog is OS chrome — it never opens under "
            "automation and can't be asserted on. Cover the intent instead:\n"
            "  When User prints the page as 'invoice.pdf'   (page.pdf export)\n"
            "  When User emulates print media               (assert the "
            "@media print styles)"
        )
    raise AssertionError(
        "The OS file picker never opens under automation — Playwright sets "
        "files directly on the input. Cover the intent instead:\n"
        "  When User uploads 'a.png' to the 'Photos' input\n"
        "  When User uploads 'a.png' and 'b.png' to the 'Photos' input\n"
        "Downloads are captured automatically: `a file should be downloaded`."
    )


def _no_text_into_permission_prompt(name: str):
    """NOOD_0122 — browser permission prompts (location/notifications/camera/
    microphone) have no text field; only JavaScript prompts accept typed input.
    Raise here so the step is rejected at resolution instead of falling through
    to a DOM fill that hunts for a nonexistent '<name> prompt' element."""
    raise AssertionError(
        f"Can't type into the {name} prompt: browser permission prompts take "
        "no text (only allow or deny). Type into a JavaScript prompt with "
        "\"types 'X' into the prompt\", or into a page field by its label."
    )


# NOOD_0153 — trailing evidence marker: `clicks "Login" ( take a screenshot )`
# asks for an evidence screenshot of THAT step. Detected (and the request flag
# set) in runner.execute_step; ALSO stripped here in _pre_clean so every other
# resolution path — step-search, the LSP, the docs example corpus — tolerates
# the marker instead of failing to match the inner step.
#
# NOOD_0225 — the bracket and dash spellings are accepted too. A reviewed
# session watched a model write `[evidence: screenshot]` onto a step, argue
# with itself about whether that was "step decorator syntax", and ship a step
# that resolved green with no picture attached — the marker was simply not
# recognised, and nothing said so. Every shape a model reaches for now means
# the same thing: `( take a screenshot )`, `[take a screenshot]`,
# `[evidence: screenshot]`, `- take a screenshot`, `(screenshot)`.
# The tail must still be a marker, not step text: an explicit separator is
# required, the phrase is anchored to the end of the line, and — since the
# span is STRIPPED, not merely flagged — the bare word "evidence" needs a
# verb or a photographic noun beside it before it counts.
_EV_VERB = (r'(?:take|takes|capture|captures|grab|grabs|attach|attaches|'
            r'include|includes|add|adds|save|saves)')
_EV_NOUN = r'(?:screen\s*shots?|screen\s*caps?|snapshots?|captures?)'
_EV_OPEN = r'\s*(?:\(\s*|\[\s*|[-–—,]\s*)'
_EV_CLOSE = r'\s*[)\]]?\s*$'
EVIDENCE_MARKER_RE = re.compile(
    _EV_OPEN
    + r'(?:'
    + rf'(?:please\s+)?{_EV_VERB}\s+(?:an?\s+|the\s+)?(?:evidence\s*[:=-]?\s*)?'
    + rf'(?:{_EV_NOUN}|evidence)'
    + rf'|(?:evidence\s*[:=-]\s*)?{_EV_NOUN}'
    + r')'
    + r'(?:\s+(?:here|as\s+evidence|on\s+this\s+step|for\s+this\s+step))?'
    + _EV_CLOSE, re.IGNORECASE)
# NOOD_0225 — the opposite instruction, same shapes: `( no screenshot )`,
# `[no evidence]`, `- do not attach a screenshot`. Without it a step could ask
# for a shot but never decline one, so the default (one shot on whichever step
# ends the scenario) overrode a tester who had said not to. Matched BEFORE the
# positive marker everywhere, because "no screenshot" contains "screenshot".
EVIDENCE_SUPPRESS_RE = re.compile(
    _EV_OPEN
    + r'(?:(?:do\s+not|don\'?t|never|no\s+need\s+to)\s+' + _EV_VERB
    + r'\s+(?:an?\s+|the\s+|any\s+)?(?:evidence\s+)?'
    + rf'(?:{_EV_NOUN}|evidence)'
    + rf'|(?:no|without|skip|omit)\s+(?:{_EV_VERB}\s+)?'
    + rf'(?:any\s+|the\s+)?(?:evidence\s+)?(?:{_EV_NOUN}|evidence)'
    + rf'|(?:{_EV_NOUN}|evidence)\s+(?:is\s+|are\s+)?not\s+'
    + r'(?:required|needed|necessary|wanted)'
    + r')'
    + r'(?:\s+(?:needed|required|necessary|here|on\s+this\s+step|'
    + r'for\s+this\s+step))?' + _EV_CLOSE, re.IGNORECASE)


def _pre_clean(text: str) -> str:
    """NOOD_0062 — tolerate sloppy authoring before any matching happens:
    smart quotes (pasted from Word/Jira), doubled/odd whitespace, and a
    trailing full stop or bang. Quoted values are safe: the strip only
    touches punctuation AFTER the last character, and quotes are normalised
    (not removed)."""
    text = (text.replace('‘', "'").replace('’', "'")
                .replace('“', '"').replace('”', '"'))
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[.!]+$', '', text).strip()
    text = EVIDENCE_SUPPRESS_RE.sub('', text).strip()
    return EVIDENCE_MARKER_RE.sub('', text).strip()


def normalize_subject(text: str) -> str:
    """
    Strip the grammatical subject so patterns only describe the action.
    Accepts: User, The user, A user, As a user, As an end user, I
    e.g. "User clicks the login button" → "clicks the login button"
         "I click the login button"     → "clicks the login button"  (normalised to 3rd person)
    """
    text = _pre_clean(text)
    m = re.match(
        r'^(?:As an?\s+(?:end\s+)?user,?\s+|(?:[Tt]he\s+|[Aa]n?\s+)?[Uu]ser\s+|I\s+)',
        text
    )
    if not m:
        # NOOD_0062 — no subject, but the first word may still be a bare
        # infinitive or past-tense verb ("click the button", "clicked the
        # button"): normalise it so the 3rd-person patterns still match.
        return _to_third_person(text)
    remainder = text[m.end():]
    # normalise 1st-person verb to 3rd-person so a single verb pattern covers both
    # "am" → "is",  "click" → "clicks", "enter" → "enters", etc.
    # Only touches the very first word.
    remainder = _to_third_person(remainder)
    return remainder


_FIRST_TO_THIRD = {
    'am': 'is',
    'use': 'uses',
    'enter': 'enters',
    'type': 'types',
    'fill': 'fills',
    'click': 'clicks',
    'press': 'presses',
    'tap': 'taps',
    'select': 'selects',
    'check': 'checks',
    'uncheck': 'unchecks',
    'wait': 'waits',
    'scroll': 'scrolls',
    'take': 'takes',
    'clear': 'clears',
    'open': 'opens',
    'go': 'goes',
    'navigate': 'navigates',
    'should': 'should',  # modal — no change
    'hover': 'hovers',
    'store': 'stores',
    'switch': 'switches',
    'set': 'sets',
    'search': 'searches',
    'close': 'closes',
    'grab': 'grabs',
    'focus': 'focuses',
    'submit': 'submits',
    'reload': 'reloads',
    'refresh': 'refreshes',
    'double-click': 'double-clicks',
    'right-click': 'right-clicks',
    'perform': 'performs',
    'drag': 'drags',
    # Phases M–U + F (2026-07)
    'copy': 'copies',
    'emulate': 'emulates',
    # NOOD_0044 — hard-sleep verbs
    'sleep': 'sleeps',
    'pause': 'pauses',
    'save': 'saves',
    'grant': 'grants',
    'throttle': 'throttles',
    'launch': 'launches',
    'stop': 'stops',
    'swipe': 'swipes',
    'act': 'acts',
    # NOOD_0062 — extra present-tense verbs
    'see': 'sees',
    'choose': 'chooses',
    'pick': 'picks',
    'visit': 'visits',
    'input': 'inputs',
    'provide': 'provides',
    'verify': 'verifies',
    'validate': 'validates',
    'confirm': 'confirms',
    'ensure': 'ensures',
    'assert': 'asserts',
    'expect': 'expects',
    'land': 'lands',
    'browse': 'browses',
    'upload': 'uploads',
    'accept': 'accepts',
    'dismiss': 'dismisses',
    # NOOD_0062 — past tense → 3rd-person present ("the user clicked ...")
    # NOOD_0186 — media, calendar, hover-menu, print verbs
    'play': 'plays',
    'mute': 'mutes',
    'unmute': 'unmutes',
    'seek': 'seeks',
    'resume': 'resumes',
    'skip': 'skips',
    'jump': 'jumps',
    'start': 'starts',
    'print': 'prints',
    'played': 'plays',
    'clicked': 'clicks',
    'entered': 'enters',
    'typed': 'types',
    'filled': 'fills',
    'selected': 'selects',
    'checked': 'checks',
    'unchecked': 'unchecks',
    'opened': 'opens',
    'navigated': 'navigates',
    'went': 'goes',
    'pressed': 'presses',
    'tapped': 'taps',
    'hovered': 'hovers',
    'scrolled': 'scrolls',
    'waited': 'waits',
    'searched': 'searches',
    'submitted': 'submits',
    'cleared': 'clears',
    'saw': 'sees',
    'chose': 'chooses',
    'picked': 'picks',
    'visited': 'visits',
    'uploaded': 'uploads',
    'dragged': 'drags',
    'refreshed': 'refreshes',
    'reloaded': 'reloads',
    'closed': 'closes',
    'switched': 'switches',
    'stored': 'stores',
    'saved': 'saves',
    'accepted': 'accepts',
    'dismissed': 'dismisses',
}


def _to_third_person(text: str) -> str:
    first_word = text.split()[0].lower() if text.split() else ''
    third = _FIRST_TO_THIRD.get(first_word)
    if third and first_word != third:
        return third + text[len(first_word):]
    return text


# ---------------------------------------------------------------------------
# Phrase aliases (NOOD_0009) — canonicalize wording drift for ANY step in the
# dictionary, not one action at a time. One line per synonym here beats a
# duplicate full pattern + lambda in PATTERNS for every variant someone writes.
#
# Rule for adding an entry: a phrase only belongs here if it CANNOT plausibly
# appear in another action family's own wording. E.g. we do NOT alias
# "should match" -> "should equal", because "the screen should match the
# baseline" (pixel_baseline) already owns that phrase — aliasing it would
# silently reroute a screenshot assertion into a value comparison.
# test_phrase_aliases.py runs every example in docs/steps_dictionary.md
# through match() so a colliding alias fails that test loudly, not silently
# at runtime.
# ---------------------------------------------------------------------------
_PHRASE_ALIASES = [
    (r'\bis (?:now )?not equal to\b', 'should not equal'),
    (r'\bdoes not equal\b',           'should not equal'),
    (r'\bis (?:now )?equal to\b',     'should equal'),
    (r'\bequals\b',                   'should equal'),
    # NOOD_0062 — verb synonyms, anchored to the start (post-subject-strip the
    # text begins with the verb) so a noun use elsewhere in the step is safe.
    (r'^chooses\b',                   'selects'),
    (r'^picks\b',                     'selects'),
    (r'^inputs\b',                    'enters'),
]


# NOOD_0062 — "verify that X" / "makes sure X" wrap another step: strip the
# wrapper and re-normalize the inner text (which may carry its own subject:
# "verifies that the user sees 'X'"). "checks" is the one ambiguous verb —
# it owns the checkbox family — so it only unwraps with an explicit
# that/whether/if connective.
_WRAPPER_RE = re.compile(
    r'^(?:verify|verifies|verifying|validates?|asserts?|ensures?|expects?|'
    r'confirms?|makes? sure)\s+(?:that\s+|whether\s+|if\s+)?',
    re.IGNORECASE)
_CHECKS_WRAPPER_RE = re.compile(r'^checks?\s+(?:that|whether|if)\s+', re.IGNORECASE)


def normalize_phrasing(text: str) -> str:
    """Rewrite known synonym phrasing to the canonical wording PATTERNS
    expects. Runs after normalize_subject, before pattern matching."""
    stripped = _WRAPPER_RE.sub('', text, count=1)
    if stripped == text:
        stripped = _CHECKS_WRAPPER_RE.sub('', text, count=1)
    if stripped != text:
        text = normalize_subject(stripped)
    for phrase, canonical in _PHRASE_ALIASES:
        text = re.sub(phrase, canonical, text, flags=re.IGNORECASE)
    # NOOD_0033 — a {var:X}/{env:X} ref still present here survived
    # substitution, i.e. it's a write target ("saves the result as {var:SUM}")
    # or an unknown ref. Canonicalize to the legacy delimiters every PATTERNS
    # entry already accepts, so no pattern needs its groups renumbered.
    # {pom:...} is untouched — it's locator syntax, handled in agents/web/pom.py.
    text = re.sub(r'\{var:([^}]+)\}', r'`\1`', text)
    text = re.sub(r'\{env:([^}]+)\}', r'[\1]', text)
    return text


# NOOD_0044 — conditional-step vocabulary. Kept as fragments so the leading-if,
# trailing-if and bare forms below stay in sync on what counts as "appears".
# The negated fragment is matched first: "is not visible" would otherwise be
# left for the positive branch to reject one word at a time.
_APPEARS = r'(?:appears?|is visible|is present|is displayed|is shown|exists?)'
_NOT_APPEARS = (r'(?:does not appear|is not visible|is not present|is not displayed|'
                r'is not shown|does not exist|is absent|is missing)')

# NOOD_0114 — nouns that mean "a rendered image": carousels, flyers, banners,
# logos, avatars… Text inside these lives in pixels, so steps using this
# fragment route through the OCR/vision bridge, not the DOM text engine.
_IMG = (r'(?:image|img|picture|photo|carousel(?: item| tile| slide| card)?|'
        r'banner|flyer|logo|avatar|profile (?:picture|photo|image)|'
        r'thumbnail|tile|badge|poster|graphic|illustration|'
        r'hero(?: image| banner)?|ad|advert(?:isement)?)')

# NOOD_0184 — every way authors name an MFA code. No capturing groups: the
# fragment is spliced into patterns that number their own groups.
_OTP = (r'(?:one-?time|totp|otp|mfa|2fa|authenticator|verification|security) '
        r'(?:code|password|passcode|token|pin)')
# Optional secret source: quoted (may contain the spaces authenticator apps
# display) or a bare token. After runner.substitute an {env:X} that resolved
# arrives as the raw base32 value; one that DIDN'T resolve arrives as [X] and
# mfa.totp_code turns that into a "secret not set" error naming the variable.
_OTP_FOR = r'(?: (?:for|from|using) (?:["\']([^"\']+)["\']|(\S+)))?'


def _otp_secret(quoted: str | None, bare: str | None) -> str | None:
    return quoted or bare or None


# NOOD_0152 — nouns that mean "a bounded box on the page" for focus_element.
# Sibling of _IMG: same action, but these are ordinary layout containers whose
# text lives in the DOM, not pixels. Without this, focusing OCR/screen reads
# onto a non-image container ('order summary' panel, a terminal pane) missed.
_BOX = (r'(?:panel|card|section|container|pane|widget|box|element|'
        r'region|area|block|column|sidebar|header|footer|modal|dialog)')

# NOOD_0186 — nouns that mean "an HTML5 media element". No capturing groups:
# spliced into patterns that number their own groups. "player" covers the
# wrapper widgets testers actually name ("pauses the 'promo' player").
_MEDIA = (r'(?:video|audio|media(?: player)?|player|clip|movie|song|track|'
          r'podcast|recording)')

# NOOD_0152 — named responsive breakpoints. The runtime (set_viewport) already
# existed; only the name→WxH table was missing, so this is pattern-only.
# Widths follow the common device tiers Playwright/Chrome DevTools present.
_BREAKPOINTS = {
    'mobile': (390, 844),          # iPhone 14/15 class
    'phone': (390, 844),
    'small': (390, 844),
    'tablet': (768, 1024),         # iPad portrait
    'ipad': (768, 1024),
    'medium': (768, 1024),
    'laptop': (1280, 800),
    'desktop': (1440, 900),
    'large': (1440, 900),
    'wide': (1920, 1080),
    'full hd': (1920, 1080),
    'widescreen': (1920, 1080),
}

# NOOD_0044 — one duration table for every hard-sleep phrasing (wait_seconds).
_UNIT_SECONDS = {
    'second': 1, 'seconds': 1, 'sec': 1, 'secs': 1, 's': 1,
    'minute': 60, 'minutes': 60, 'min': 60, 'mins': 60, 'm': 60,
    'hour': 3600, 'hours': 3600, 'h': 3600,
    'millisecond': 0.001, 'milliseconds': 0.001, 'ms': 0.001,
}


# ---------------------------------------------------------------------------
# Patterns — written in 3rd-person (after normalize_subject).
# Each entry: (regex, action_type, param_extractor)
# Patterns tried top-to-bottom; first match wins.
# ---------------------------------------------------------------------------
PATTERNS = [
    # --- NOOD_0044: conditional steps -------------------------------------
    # run_if wraps ANY other step: the runner probes the condition's
    # visibility (non-fatal) and only then re-enters execute_step with the
    # inner step text. MUST be the very first entries — the inner step is a
    # free-form capture that every catch-all below would otherwise swallow.
    #
    # Popup conditional — routes straight to close_popups, which is already
    # best-effort and never fails, so no visibility probe is needed.
    (r'^if (?:the )?page (?:appears to have|has|shows?|displays?) (?:a |an )?pop-?up,?\s*(?:[-–—:]\s*)?(?:then )?(?:closes?|dismiss(?:es)?)(?: (?:the|it|all))?(?: pop-?ups?)?$',
                                                   'close_popups',   lambda m: {}),
    # Timed variant (NOOD_0106) — "closes the popup if it appears within 10
    # seconds": keeps sweeping until the deadline, for overlays that arrive
    # seconds after load. Must precede the untimed form (longer match first).
    (rf'^(?:closes?|dismiss(?:es)?) (?:the |any |all )?pop-?ups? if (?:one |any |it )?(?:{_APPEARS}|present) (?:with)?in (?:the (?:next|first) )?(\d+) seconds?$',
                                                   'close_popups',   lambda m: {'within': int(m.group(1))}),
    (rf'^(?:closes?|dismiss(?:es)?) (?:the |any |all )?pop-?ups? if (?:one |any |it )?(?:{_APPEARS}|present)$',
                                                   'close_popups',   lambda m: {}),
    # Leading-if: "if 'Cookie banner' appears, clicks 'Accept all'".
    (rf'^(?:if|when) (?:a |an |the )?["\'](.+?)["\'] {_NOT_APPEARS}(?: on the (?:page|screen))?,?\s*(?:[-–—:]\s*)?(?:then )?(?:performs? )?(.+)$',
                                                   'run_if',         lambda m: {'condition': _q(m.group(1)), 'negate': True, 'then': m.group(2)}),
    (rf'^(?:if|when) (?:a |an |the )?["\'](.+?)["\'] {_APPEARS}(?: on the (?:page|screen))?,?\s*(?:[-–—:]\s*)?(?:then )?(?:performs? )?(.+)$',
                                                   'run_if',         lambda m: {'condition': _q(m.group(1)), 'negate': False, 'then': m.group(2)}),
    # Bare form: "a 'Promo modal' appears on the page perform clicks 'Close'".
    # The "on the page/screen" anchor is required here — it's what separates
    # this from a plain assertion phrasing.
    (rf'^(?:a|an|the) ["\'](.+?)["\'] {_APPEARS} on the (?:page|screen),?\s*(?:[-–—:]\s*)?(?:then )?(?:performs? )?(.+)$',
                                                   'run_if',         lambda m: {'condition': _q(m.group(1)), 'negate': False, 'then': m.group(2)}),
    # Trailing-if: "clicks 'Skip' if 'Tour popup' appears".
    (rf'^(.+?),? (?:if|when|in case) ["\'](.+?)["\'] {_NOT_APPEARS}(?: on the (?:page|screen))?$',
                                                   'run_if',         lambda m: {'then': m.group(1), 'condition': _q(m.group(2)), 'negate': True}),
    (rf'^(.+?),? (?:if|when|in case) ["\'](.+?)["\'] {_APPEARS}(?: on the (?:page|screen))?$',
                                                   'run_if',         lambda m: {'then': m.group(1), 'condition': _q(m.group(2)), 'negate': False}),

    # Page pin (9.3) — set the active POM page for SPAs where the URL is static.
    # Must precede the navigate patterns; ends in " page" so it can't be a URL.
    (r'^is on (?:the )?["\'](.+?)["\'] page$',     'set_page',       lambda m: {'name': _q(m.group(1))}),

    # NOOD_0122 — composite close: DOM sweep AND deny the browser permission
    # prompt named explicitly after "including". Only the named permission is
    # decided; a bare "close all popups" (below) never touches permission state.
    # Sits above the generic close so the "including ..." tail routes here.
    # ponytail: one named permission covers the ask; widen the capture to a list
    # when a real test names two prompts in one breath.
    (r'^closes? (?:any and all|all|any) (?:the )?(?:pop-?ups?|modals?|dialogs?|banners?|overlays?)(?: windows?)?(?:,)? (?:and )?(?:also )?includ\w+ (?:the )?(location|geolocation|notifications?|camera|microphone)(?: permission)?(?: (?:prompt|pop-?up|notification|bubble|request))?$',
                                                   'close_popups',   lambda m: {'deny_permissions': [m.group(1).lower()]}),
    # Close popups / cookie banners / modals (best-effort, never fails).
    # Timed form first — same NOOD_0106 late-overlay sweep as above.
    (r'^closes? (?:all )?(?:the )?(?:pop-?ups?|modals?|dialogs?|banners?)(?: windows?)? (?:with)?in (?:the (?:next|first) )?(\d+) seconds?$',
                                                   'close_popups',   lambda m: {'within': int(m.group(1))}),
    (r'^closes? (?:all )?(?:the )?(?:pop-?ups?|modals?|dialogs?|banners?)(?: windows?)?$',
                                                   'close_popups',   lambda m: {}),

    # Search — fill the search box and submit, in one step
    (r'^searches? for ["\'](.+?)["\']$',           'search',         lambda m: {'query': _q(m.group(1))}),

    # NOOD_0141 — typeahead composite, sibling grammar to `searches for`:
    # resolve the visible search box (opening its trigger if hidden), type the
    # partial term per-character, wait for the suggestion list to populate,
    # click the NAVIGATING row (never a no-op icon sub-element). The bare form
    # (no `for "..."`) picks from an already-open list; "clicks the ...
    # suggestion" is the phrasing weaker models actually emit — same action.
    (r'^(?:selects?|clicks?(?: on| upon)?) (?:the )?["\'](.+?)["\'] suggestion(?: for ["\'](.+?)["\'])?$',
                                                   'select_suggestion', lambda m: {'option': _q(m.group(1)), 'term': _q(m.group(2)) if m.group(2) else None}),
    # NOOD_0141 — intent-level typeahead assertions ("misspelling still
    # yields suggestions"), DOM-free for the author. The `for "term"` form is
    # self-contained: it types the term itself first. MUST live up here — the
    # assert_compare catch-all ("X should contain Y") would swallow them.
    (r'^the search suggestions? for ["\'](.+?)["\'] (?:should )?(?:includes?|contains?|shows?|lists?) ["\'](.+?)["\']$',
                                                   'assert_suggestion', lambda m: {'term': _q(m.group(1)), 'text': _q(m.group(2))}),
    (r'^the search suggestions? (?:should )?(?:includes?|contains?|shows?|lists?) ["\'](.+?)["\']$',
                                                   'assert_suggestion', lambda m: {'term': None, 'text': _q(m.group(1))}),
    # "a suggestion bar appears below the search bar" — prompts say this
    # verbatim; without this it routes to assert_visible hunting for literal
    # "suggestion bar" text and rots.
    (r'^(?:the |a |an )?(?:search )?suggestions?(?: (?:bar|box|list|dropdown|panel))?(?: should)? (?:appears?|shows? up|is (?:visible|shown|displayed)|are (?:visible|shown|displayed))(?: below the search (?:bar|box|field))?$',
                                                   'assert_suggestion', lambda m: {'term': None, 'text': None}),

    # --- NOOD_0186: the five audited web-coverage gaps ------------------------
    # HTML5 media, multi-file upload, calendar pickers, hover-menu composites,
    # native-dialog refusals. The whole block sits ABOVE navigate/upload/
    # select/click/fill and the assertion families: "opens the 'X' menu and
    # clicks 'Y'" would otherwise be swallowed by the navigate `opens? "..."`
    # catch-all, "selects '…' from the 'X' calendar" by the dropdown select,
    # "uploads 'a' and 'b' to …" by the single upload, and every media assert
    # by assert_state / assert_compare / assert_semantic.
    #
    # Hover menus — hover the trigger, wait for the revealed item, click it
    # (falls back to clicking the trigger for click-to-open menus).
    (r'^opens? (?:the )?["\'](.+?)["\'] (?:menu|dropdown|nav(?:igation)?(?: menu)?|submenu) and (?:clicks?|selects?|chooses?|picks?) (?:on )?["\'](.+?)["\']$',
                                                   'menu_select',    lambda m: {'trigger': _q(m.group(1)), 'item': _q(m.group(2))}),
    (r'^hovers? (?:over|on) (?:the )?["\'](.+?)["\'](?: (?:menu|nav(?:igation)?))? and (?:clicks?|selects?|chooses?|picks?) (?:on )?["\'](.+?)["\']$',
                                                   'menu_select',    lambda m: {'trigger': _q(m.group(1)), 'item': _q(m.group(2))}),
    (r'^(?:clicks?|selects?) (?:on )?["\'](.+?)["\'] (?:in|from|under) (?:the )?["\'](.+?)["\'] (?:menu|nav(?:igation)?(?: menu)?|dropdown menu|submenu)$',
                                                   'menu_select',    lambda m: {'item': _q(m.group(1)), 'trigger': _q(m.group(2))}),
    # Calendar pickers — click-driven popup calendars (react-datepicker,
    # flatpickr, MUI, jQuery UI, ARIA grids). fill_date TYPES a date; this
    # DRIVES the widget: open, navigate months, click the day cell.
    (r'^(?:selects?|clicks?) (today|tomorrow|yesterday)(?:\'s date)? (?:from|in|on) (?:the )?["\']?(.+?)["\']? (?:calendar|date ?picker)$',
                                                   'pick_date',      lambda m: {'locator': _q(m.group(2)), 'offset_days': {'today': 0, 'tomorrow': 1, 'yesterday': -1}[m.group(1)]}),
    (r'^(?:selects?|clicks?) ["\'](.+?)["\'] (?:from|in|on) (?:the )?["\']?(.+?)["\']? (?:calendar|date ?picker)$',
                                                   'pick_date',      lambda m: {'date': _q(m.group(1)), 'locator': _q(m.group(2))}),
    # Multi-file upload — one set_input_files call with the whole list;
    # repeated single uploads REPLACE each other on an <input multiple>.
    (r'^uploads? ((?:["\'][^"\']+["\'])(?:\s*(?:,|and)\s*["\'][^"\']+["\'])+) (?:to|into) (?:the )?(.+)$',
                                                   'upload_multi',   lambda m: {'paths': re.findall(r'["\']([^"\']+)["\']', m.group(1)), 'locator': _q(m.group(2))}),
    # HTML5 media — play/pause/mute/seek/volume + state/time/duration asserts.
    (rf'^(?:plays?|starts?|resumes?)(?: playing)? (?:the |a |an )?(?:["\'](.+?)["\'] )?{_MEDIA}$',
                                                   'media_play',     lambda m: {'locator': _q(m.group(1)) if m.group(1) else None}),
    (rf'^pauses? (?:the )?(?:["\'](.+?)["\'] )?{_MEDIA}$',
                                                   'media_pause',    lambda m: {'locator': _q(m.group(1)) if m.group(1) else None}),
    (rf'^(mutes?|unmutes?) (?:the )?(?:["\'](.+?)["\'] )?{_MEDIA}$',
                                                   'media_mute',     lambda m: {'locator': _q(m.group(2)) if m.group(2) else None, 'muted': not m.group(1).lower().startswith('un')}),
    (rf'^(?:seeks?|skips?|jumps?)(?: (?:the )?(?:["\'](.+?)["\'] )?{_MEDIA})?(?: (?:forward|ahead))? to (\d+):(\d{{2}})$',
                                                   'media_seek',     lambda m: {'locator': _q(m.group(1)) if m.group(1) else None, 'seconds': int(m.group(2)) * 60 + int(m.group(3))}),
    (rf'^(?:seeks?|skips?|jumps?)(?: (?:the )?(?:["\'](.+?)["\'] )?{_MEDIA})?(?: (?:forward|ahead))? to (\d+(?:\.\d+)?) ?(?:seconds?|s)$',
                                                   'media_seek',     lambda m: {'locator': _q(m.group(1)) if m.group(1) else None, 'seconds': float(m.group(2))}),
    (rf'^sets? the (?:["\'](.+?)["\'] )?(?:{_MEDIA} )?volume to (\d+(?:\.\d+)?) ?%?$',
                                                   'media_volume',   lambda m: {'locator': _q(m.group(1)) if m.group(1) else None, 'percent': float(m.group(2))}),
    # Negated state first — the positive branch would eat "is not playing"
    # one word at a time (same ordering rule as _NOT_APPEARS).
    (rf'^(?:the )?(?:["\'](.+?)["\'] )?{_MEDIA} (?:should not be|is not|isn\'?t|is no longer) (playing|paused|muted|unmuted|ended|finished|stopped)$',
                                                   'assert_media_state', lambda m: {'locator': _q(m.group(1)) if m.group(1) else None, 'state': m.group(2), 'negate': True}),
    (rf'^(?:the )?(?:["\'](.+?)["\'] )?{_MEDIA} (?:should (?:be|have)|is|has) (playing|paused|muted|unmuted|ended|finished|stopped)$',
                                                   'assert_media_state', lambda m: {'locator': _q(m.group(1)) if m.group(1) else None, 'state': m.group(2), 'negate': False}),
    (rf'^(?:the )?(?:["\'](.+?)["\'] )?{_MEDIA} should have played (?:at least )?(\d+(?:\.\d+)?) ?seconds?$',
                                                   'assert_media_time', lambda m: {'locator': _q(m.group(1)) if m.group(1) else None, 'seconds': float(m.group(2)), 'op': '>='}),
    (rf'^(?:the )?(?:["\'](.+?)["\'] )?{_MEDIA} should be (\d+(?:\.\d+)?) ?seconds? long$',
                                                   'assert_media_duration', lambda m: {'locator': _q(m.group(1)) if m.group(1) else None, 'seconds': float(m.group(2))}),
    (rf'^(?:the )?(?:["\'](.+?)["\'] )?{_MEDIA} duration should be (\d+(?:\.\d+)?) ?seconds?$',
                                                   'assert_media_duration', lambda m: {'locator': _q(m.group(1)) if m.group(1) else None, 'seconds': float(m.group(2))}),
    # Printing — "prints the page" maps to the intent automation CAN cover
    # (page.pdf export); the native print dialog itself is refused below.
    (r'^prints? the page(?: (?:to|as) ["\'](.+?)["\'])?$',
                                                   'save_pdf',       lambda m: {'path': _q(m.group(1)) if m.group(1) else 'printed-page.pdf'}),
    (r'^the print (?:dialog|preview|window) should (?:open|appear|be (?:visible|shown|displayed))$',
                                                   '_reject',        lambda m: _no_native_dialog('print')),
    (r'^(?:clicks?|presses?|selects?) .+? (?:in|from|on) the print (?:dialog|preview|window)$',
                                                   '_reject',        lambda m: _no_native_dialog('print')),
    (r'^(?:closes?|dismiss(?:es)?|cancels?|accepts?) the print (?:dialog|preview|window)$',
                                                   '_reject',        lambda m: _no_native_dialog('print')),
    (r'^(?:clicks?|presses?|selects?) .+? (?:in|from|on) the (?:(?:os |system |native )?file (?:picker|chooser|browser|explorer)|(?:open|save) file dialog)$',
                                                   '_reject',        lambda m: _no_native_dialog('file')),
    (r'^(?:closes?|dismiss(?:es)?|cancels?|accepts?) the (?:(?:os |system |native )?file (?:picker|chooser|browser|explorer)|(?:open|save) file dialog)$',
                                                   '_reject',        lambda m: _no_native_dialog('file')),

    # --- Phase D: network mocking, API setup/teardown, test data -------------
    # NOOD_0188 — richer mocks. status+body alone couldn't express the daily
    # cases: a body too big for a Gherkin string (fixture file), mocking ONE
    # verb while the others pass through (method), or injected latency to
    # exercise a spinner/timeout. Response headers ride an optional table.
    # Order: the more specific forms MUST precede the bare status form.
    (r'^mocks? (?:(GET|POST|PUT|DELETE|PATCH) )?["\'](.+?)["\'] with (?:the )?(?:fixture|payload|file) ["\'](.+?)["\'](?: and status (\d+))?$',
                                                   'mock_route',     lambda m: {'method': (m.group(1) or '').upper() or None, 'url': _q(m.group(2)), 'fixture': _q(m.group(3)), 'status': int(m.group(4) or 200)}),
    (r'^mocks? (GET|POST|PUT|DELETE|PATCH) ["\'](.+?)["\'] with status (\d+)(?: and body ["\'](.+?)["\'])?$',
                                                   'mock_route',     lambda m: {'method': m.group(1).upper(), 'url': _q(m.group(2)), 'status': int(m.group(3)), 'body': m.group(4)}),
    (r'^mocks? ["\'](.+?)["\'] with status (\d+)(?: and body ["\'](.+?)["\'])? after ([\d.]+) seconds?$',
                                                   'mock_route',     lambda m: {'url': _q(m.group(1)), 'status': int(m.group(2)), 'body': m.group(3), 'delay_ms': _ms(m.group(4))}),
    # Mock a network response (Playwright route.fulfill).
    (r'^mocks? ["\'](.+?)["\'] with status (\d+)(?: and body ["\'](.+?)["\'])?$',
                                                   'mock_route',     lambda m: {'url': _q(m.group(1)), 'status': int(m.group(2)), 'body': m.group(3)}),
    # NOOD_0188 — HAR replay: pin a whole third-party-dependent session with
    # one line instead of a mock per request. The recording form writes the
    # file from a live run, so the same step captures then replays forever.
    (r'^(?:replays?|serves?) (?:the )?(?:network |traffic )?(?:from )?["\'](.+?\.har)["\'](?: for ["\'](.+?)["\'])?$',
                                                   'route_from_har', lambda m: {'har': _q(m.group(1)), 'url': _q(m.group(2)) if m.group(2) else None, 'update': False}),
    (r'^records? (?:the )?(?:network |traffic )?(?:to )?["\'](.+?\.har)["\'](?: for ["\'](.+?)["\'])?$',
                                                   'route_from_har', lambda m: {'har': _q(m.group(1)), 'url': _q(m.group(2)) if m.group(2) else None, 'update': True}),
    # Block requests to a URL glob (route.abort) — e.g. analytics/ads.
    (r'^blocks? requests? to ["\'](.+?)["\']$',    'block_route',    lambda m: {'url': _q(m.group(1))}),
    # API setup/teardown — call an endpoint directly (no browser nav).
    (r'^calls? (GET|POST|PUT|DELETE|PATCH) ["\'](.+?)["\'](?: with body ["\'](.+?)["\'])?' + _WITHIN + r'$',
                                                   'api_call',       lambda m: {'method': m.group(1).upper(), 'url': _q(m.group(2)), 'body': m.group(3), 'timeout': _secs(m.group(4))}),
    # Load a resource file (payload, fixture, …) from the feature's resources/ folder.
    # Single file → stored in PAYLOAD (and PAYLOAD_<STEM> for consistency).
    (r"^uses? (?:this )?(?:payload|resource|fixture) ['\"](.+?)['\"]$",
                                                   'load_resource',  lambda m: {'path': _q(m.group(1))}),
    # Table form — | payload | column, one file per row.
    (r"^uses? (?:these )?(?:payloads?|resources?|fixtures?):?$",
                                                   'load_resource',  lambda m: {'path': None}),

    # Load a YAML/JSON fixture file into the run-scoped variable store.
    (r'^loads? (?:test )?data from ["\'](.+?)["\']$',
                                                   'load_data',      lambda m: {'file': _q(m.group(1))}),

    # --- Run an external script / command as a step (NOOD_0016) -------------
    # Execute a user script (py/js/jar/sh/...) or a shell command — e.g. seed a
    # database before the UI test. stdout is captured into `SCRIPT_OUTPUT` (and an
    # optional named var), so a later step can assert on the result.
    (r'^runs? (?:the )?script ["\'](.+?)["\'](?: with (?:args? )?["\'](.+?)["\'])?(?: (?:and )?stor(?:e|ing) (?:the )?output (?:as|in) [\[`]([^\]`]+)[\]`])?$',
                                                   'run_script',     lambda m: {'path': _q(m.group(1)), 'args': m.group(2), 'var': m.group(3)}),
    (r'^(?:the )?script ["\'](.+?)["\'] (?:runs?|executes?|is executed|completes? successfully)$',
                                                   'run_script',     lambda m: {'path': _q(m.group(1)), 'args': None, 'var': None}),
    (r'^runs? (?:the )?command ["\'](.+?)["\'](?: (?:and )?stor(?:e|ing) (?:the )?output (?:as|in) [\[`]([^\]`]+)[\]`])?$',
                                                   'run_command',    lambda m: {'command': _q(m.group(1)), 'var': m.group(2)}),
    (r'^(?:the )?command ["\'](.+?)["\'] (?:runs?|executes?|is executed)$',
                                                   'run_command',    lambda m: {'command': _q(m.group(1)), 'var': None}),
    # Call an in-process Python function (NOOD_0009) — unlike run_script this
    # imports and calls the function directly, so its *return value* (not stdout)
    # lands in `FUNCTION_RESULT` (and an optional named var) for later steps (D.I.).
    # "with raw arg 'X'" (NOOD_0115) — pass the WHOLE value as one argument;
    # plain "with args" shlex-splits, which shreds captured page text like
    # '93 results' into two positional args.
    (r'^calls? (?:the )?function ["\'](.+?)["\'](?: with (raw )?(?:args? )?["\'](.+?)["\'])?(?: (?:and )?(?:sav|stor)(?:es?|ing) (?:the )?(?:result|return(?: value)?|output) (?:as|in) [\[`]([^\]`]+)[\]`])?$',
                                                   'call_function',  lambda m: {'spec': _q(m.group(1)), 'raw': bool(m.group(2)), 'args': m.group(3), 'var': m.group(4)}),

    # Navigate
    (r'^is on ["\'](.+)["\']$',                   'navigate',       lambda m: {'url': _q(m.group(1))}),
    (r'^navigates? to ["\'](.+)["\']$',            'navigate',       lambda m: {'url': _q(m.group(1))}),
    (r'^opens? ["\'](.+)["\']$',                   'navigate',       lambda m: {'url': _q(m.group(1))}),
    (r'^goes? to ["\'](.+)["\']$',                 'navigate',       lambda m: {'url': _q(m.group(1))}),
    # NOOD_0062 — more navigate phrasings testers actually write.
    (r'^(?:visits?|browses? to) ["\'](.+)["\']$',  'navigate',       lambda m: {'url': _q(m.group(1))}),
    (r'^opens? (?:the )?(?:page|url|site|website) ["\'](.+)["\']$',
                                                   'navigate',       lambda m: {'url': _q(m.group(1))}),
    # "the login page with the url value of 'www.stone.com'" — the page name is
    # descriptive only; the quoted url is what we navigate to.
    (r'^(?:is on )?(?:the )?.+? page (?:with|at|has|whose) (?:the )?url(?: value)?(?: of| is| set to)? ["\'](.+?)["\']$',
                                                   'navigate',       lambda m: {'url': _q(m.group(1))}),

    # Viewport (NOOD_0007) — responsive testing at any size, mid-scenario.
    (r'^sets? the viewport(?: size)? to ["\']?(\d+)\s*[xX]\s*(\d+)["\']?$',
                                                   'set_viewport',   lambda m: {'width': int(m.group(1)), 'height': int(m.group(2))}),
    # NOOD_0152 — "resizes the browser window to 800x600". The window/browser
    # noun is what testers reach for; set_viewport is the same runtime call.
    (r'^(?:resizes?|sets?) the (?:browser|window|browser window|screen)(?: size| window)? to ["\']?(\d+)\s*[xX]\s*(\d+)["\']?$',
                                                   'set_viewport',   lambda m: {'width': int(m.group(1)), 'height': int(m.group(2))}),
    # NOOD_0152 — named breakpoints ("resizes the browser to tablet width").
    # Responsive suites write this constantly; the WxH table is _BREAKPOINTS.
    # MUST precede the numeric forms' siblings only in the sense that it is
    # mutually exclusive — a name never parses as digits, so order is free.
    (rf'^(?:resizes?|sets?|switches?)(?: the)?(?: browser| window| viewport| screen| layout)?'
     rf'(?: size)? (?:to|into) (?:the )?({"|".join(_BREAKPOINTS)})'
     rf'(?: width| size| view| viewport| breakpoint| layout| mode)?$',
                                                   'set_viewport',   lambda m: {'width': _BREAKPOINTS[m.group(1).lower()][0],
                                                                                'height': _BREAKPOINTS[m.group(1).lower()][1]}),
    # "switches to desktop view" / "switches to mobile" — no size noun at all.
    (rf'^switches? to (?:the )?({"|".join(_BREAKPOINTS)})(?: width| size| view| viewport| breakpoint| layout| mode)?$',
                                                   'set_viewport',   lambda m: {'width': _BREAKPOINTS[m.group(1).lower()][0],
                                                                                'height': _BREAKPOINTS[m.group(1).lower()][1]}),
    # NOOD_0152 — orientation swap. Reads the live viewport and transposes it,
    # so it composes with whatever size is already set (resolved in the runner).
    (r'^rotates? (?:the )?(?:device|screen|browser|page|viewport) to (landscape|portrait)$',
                                                   'rotate_viewport', lambda m: {'orientation': m.group(1).lower()}),
    (r'^rotates? (?:the )?(?:device|screen|browser|page|viewport)$',
                                                   'rotate_viewport', lambda m: {'orientation': None}),
    # NOOD_0152 — viewport assertion. There was no way to verify a resize took.
    (r'^(?:the )?(?:viewport|browser|window|screen) (?:width )?should be (\d+) (?:pixels? )?wide$',
                                                   'assert_viewport', lambda m: {'width': int(m.group(1)), 'height': None}),
    (r'^(?:the )?(?:viewport|browser|window|screen) (?:size )?should be ["\']?(\d+)\s*[xX]\s*(\d+)["\']?$',
                                                   'assert_viewport', lambda m: {'width': int(m.group(1)), 'height': int(m.group(2))}),

    # NOOD_0035 — declarative resolution phrasing from the target-architecture
    # sketch ("Given browser resolution is set to 1920x1080"). {var:X}/{env:X}
    # are substituted before resolve() runs, so this only ever sees the WxH literal.
    (r'^(?:the )?(?:browser|screen|system) resolution is set to ["\']?(\d+)\s*[xX]\s*(\d+)["\']?$',
                                                   'set_viewport',   lambda m: {'width': int(m.group(1)), 'height': int(m.group(2))}),

    # Browser history (NOOD_0025) — back / forward / reload.
    (r'^goes? back$',                              'go_back',        lambda m: {}),
    (r'^goes? forward$',                           'go_forward',     lambda m: {}),
    (r'^(?:reloads?|refreshes?) (?:the )?page$',   'reload',         lambda m: {}),

    # Browser state (NOOD_0009) — cookie/storage reset for test isolation and
    # cookie seeding for auth state. "clears the X field" needs a field/box/input
    # suffix, so these can't collide with the form-clear pattern.
    (r'^clears? (?:all )?(?:the )?cookies$',       'clear_cookies',  lambda m: {}),
    (r'^clears? (?:the )?(local|session) storage$',
                                                   'clear_storage',  lambda m: {'kind': m.group(1).lower()}),
    (r'^sets? (?:the )?cookie ["\'](.+?)["\'] to ["\'](.+?)["\']$',
                                                   'set_cookie',     lambda m: {'name': _q(m.group(1)), 'value': _q(m.group(2))}),
    # NOOD_0143 — storage/cookie VALUE steps (audit gap): seed state before a
    # scenario, assert what the app persisted after. The assertions sit here,
    # well before the assert_compare catch-all that would swallow
    # "storage 'x' should be 'y'".
    (r'^sets? (?:the )?(local|session) storage ["\'](.+?)["\'] to ["\'](.+?)["\']$',
                                                   'set_storage',    lambda m: {'kind': m.group(1).lower(), 'key': _q(m.group(2)), 'value': _q(m.group(3))}),
    (r'^(?:the )?(local|session) storage ["\'](.+?)["\'] should (?:be|equal|contain|include) ["\'](.+?)["\']$',
                                                   'assert_storage', lambda m: {'kind': m.group(1).lower(), 'key': _q(m.group(2)), 'value': _q(m.group(3))}),
    (r'^(?:the )?cookie ["\'](.+?)["\'] should (?:be|equal|contain|include|have (?:the )?value) ["\'](.+?)["\']$',
                                                   'assert_cookie',  lambda m: {'name': _q(m.group(1)), 'value': _q(m.group(2))}),
    (r'^(?:the )?cookie ["\'](.+?)["\'] should (?:exist|be set|be present)$',
                                                   'assert_cookie',  lambda m: {'name': _q(m.group(1)), 'value': None}),
    # Session persistence (NOOD_0011) — save cookies + localStorage after a
    # login; reuse across runs/scenarios via NOODLE_STORAGE_STATE=<file>.
    (r'^saves? the (?:browser |login )?session (?:as|to|in) ["\'](.+?)["\']$',
                                                   'save_session',   lambda m: {'path': _q(m.group(1))}),

    # Tab / window management (NOOD_0025) — handled in execute_step (it owns
    # the browser context that holds every page). "a new tab should open" both
    # asserts and focuses the newest page so following steps act on it.
    (r'^a new (?:tab|window) should open$',        'assert_new_tab', lambda m: {}),
    (r'^switches? to (?:the )?(new|last|previous|original|first|main) (?:tab|window)$',
                                                   'switch_tab',     lambda m: {'target': m.group(1).lower()}),
    # NOOD_0181 — address a window by name. The positional words above are a
    # two-page model; with a shop, a support chat and a payment window open at
    # once, "previous" stops meaning anything. Matches title OR url, because a
    # popup often opens with an empty or generic title. No overlap with the
    # forms above (they need the literal new|last|… keywords) or with
    # `switches to the 'inner' frame` (a different trailing noun).
    (r'^switches? to (?:the )?(?:tab|window) (?:titled|named|called|with (?:the )?title) ["\'](.+?)["\']$',
                                                   'switch_tab_named', lambda m: {'name': _q(m.group(1))}),
    (r'^switches? to (?:the )?["\'](.+?)["\'] (?:tab|window)$',
                                                   'switch_tab_named', lambda m: {'name': _q(m.group(1))}),
    (r'^closes? (?:the )?(?:new |current )?(?:tab|window)$',
                                                   'close_tab',      lambda m: {}),

    # JS dialogs (NOOD_0008 gap #2) — arm-before-trigger: Playwright auto-
    # dismisses unhandled dialogs, so the handler must be armed BEFORE the
    # click that opens one. MUST precede the fill patterns ("types X into the
    # prompt ..." would otherwise route to fill and hunt for an element).
    (r'^(?:answers?|types?) ["\'](.+?)["\'] (?:into|in|to) the (?:next )?prompt(?: and accepts? it)?$',
                                                   'arm_dialog',     lambda m: {'response': 'accept', 'answer': _q(m.group(1))}),
    (r'^accepts? the (?:next )?(?:alert|confirm|dialog|prompt)$',
                                                   'arm_dialog',     lambda m: {'response': 'accept', 'answer': None}),
    (r'^dismiss(?:es)? the (?:next )?(?:alert|confirm|dialog|prompt)$',
                                                   'arm_dialog',     lambda m: {'response': 'dismiss', 'answer': None}),
    (r'^the (?:alert|confirm|dialog|prompt) should (?:say|show|contain) ["\'](.+?)["\']$',
                                                   'assert_dialog_text', lambda m: {'text': _q(m.group(1))}),
    # NOOD_0122 — reject typing into a browser permission prompt. MUST precede
    # the generic fill patterns, or "types 'Toronto' into the location prompt"
    # silently becomes a DOM fill hunting for a 'location prompt' element.
    (r'^(?:enters?|types?|answers?|provides?) .+? (?:in|into|to) (?:the )?(location|geolocation|notifications?|camera|microphone)(?: permission)? (?:prompt|pop-?up|bubble|request)$',
                                                   '_reject',        lambda m: _no_text_into_permission_prompt(m.group(1).lower())),

    # NOOD_0184 — MFA one-time codes. All three MUST precede the generic fill
    # and store_text patterns ("enters the one-time code…" is otherwise a DOM
    # fill of the literal words; "stores the one-time code…" hunts the page
    # for an element called "one-time code").
    (rf'^(?:enters?|types?|fills? in|inputs?) (?:the )?{_OTP}{_OTP_FOR} '
     rf'(?:in|into) (?:the )?["\'](.+?)["\'](?: field| box| input)?$',
                                                   'totp_fill',      lambda m: {'secret': _otp_secret(m.group(1), m.group(2)), 'locator': _q(m.group(3))}),
    (rf'^(?:stores?|saves?|generates?|gets?) (?:the )?{_OTP}{_OTP_FOR} '
     rf'(?:as|into|in) [\[`]([^\]`]+)[\]`]$',
                                                   'totp_store',     lambda m: {'secret': _otp_secret(m.group(1), m.group(2)), 'var': m.group(3)}),
    # Emailed codes — poll the NOODLE_IMAP_* inbox until the code arrives.
    (rf'^(?:waits? for|reads?|gets?|fetch(?:es)?) (?:an |the )?(?:latest |newest )?e-?mail(?:ed)? '
     rf'(?:{_OTP}|code)(?: containing ["\'](.+?)["\'])? '
     rf'and stor(?:es?|ing) (?:it )?(?:as|into|in) [\[`]([^\]`]+)[\]`]{_WITHIN}$',
                                                   'email_code',     lambda m: {'match': _q(m.group(1)) if m.group(1) else None, 'var': m.group(2), 'timeout': _secs(m.group(3))}),

    # NOOD_0152 — email/SMS steps fail HERE rather than falling through to
    # assert_compare, which would literally compare the words "the email".
    (r'^(?:the )?(email|e-mail|sms|text message)(?: to ["\'].+?["\'])? should (?:contain|include|say|show|have) ["\'].+?["\']$',
                                                   '_reject',        lambda m: _no_mail_adapter('email' if 'mail' in m.group(1) else 'SMS')),
    (r'^(?:opens?|reads?|checks?) (?:the )?(?:latest |last |newest )?(email|e-mail|sms|text message)(?: to ["\'].+?["\'])?$',
                                                   '_reject',        lambda m: _no_mail_adapter('email' if 'mail' in m.group(1) else 'SMS')),

    # File upload / download assert (NOOD_0008 gaps #3, #4)
    (r'^uploads? ["\'](.+?)["\'] (?:to|into) (?:the )?(.+)$',
                                                   'upload',          lambda m: {'path': _q(m.group(1)), 'locator': _q(m.group(2))}),
    (r'^a file (?:["\'](.+?)["\'] )?should (?:be|have been) downloaded$',
                                                   'assert_download', lambda m: {'name': _q(m.group(1)) if m.group(1) else None}),
    # NOOD_0152 — assert on the downloaded file's CONTENT. Previously
    # "the downloaded file should contain 'X'" fell through to assert_compare,
    # which literally string-compared the words "the downloaded file" — a
    # guaranteed, baffling red rather than a clean unknown-step.
    (r'^the downloaded (?:file|csv|report|export)? ?should (?:contain|include) ["\'](.+?)["\']$',
                                                   'assert_download_content', lambda m: {'needle': _q(m.group(1)), 'rows': None}),
    (r'^the downloaded (?:file|csv|report|export)? ?should have (\d+) rows?$',
                                                   'assert_download_content', lambda m: {'needle': None, 'rows': int(m.group(1))}),

    # NOOD_0152 — mouse-level drags. ALL of these MUST precede the drag_to form
    # below, whose "(?:to|onto|into|over)" would swallow "by 100, 50" and the
    # edge/slider phrasings. Real press→move→release events: the drag_to form
    # only synthesises HTML5 drag events, which JS-driven widgets ignore.
    (r'^drags? ["\'](.+?)["\'] by \(?(-?\d+)\s*,\s*(-?\d+)\)?(?: pixels?| px)?$',
                                                   'mouse_drag',      lambda m: {'locator': _q(m.group(1)), 'dx': int(m.group(2)), 'dy': int(m.group(3))}),
    (r'^drags? ["\'](.+?)["\'] (-?\d+) (?:pixels?|px) (right|left|up|down)$',
                                                   'mouse_drag',      lambda m: _offset(m.group(1), m.group(2), m.group(3))),
    # Split panes / column dividers / drawers — the handle IS the border, so the
    # grab point is an edge, not the centre.
    (r'^(?:drags?|resizes?) (?:the )?["\'](.+?)["\'] (?:divider|handle|splitter|edge|border|gripper) (-?\d+) (?:pixels?|px) (right|left|up|down)$',
                                                   'drag_edge',       lambda m: _edge_offset(m.group(1), m.group(2), m.group(3))),
    (r'^(?:drags?|resizes?) (?:the )?["\'](.+?)["\'] (?:column|panel|pane|sidebar|drawer) (-?\d+) (?:pixels?|px) (right|left|up|down)$',
                                                   'drag_edge',       lambda m: _edge_offset(m.group(1), m.group(2), m.group(3))),
    # Kanban / sortable — same words as drag_to, but forced onto real mouse
    # events via an explicit noun ("column"/"list"/"above"/"below").
    (r'^drags? ["\'](.+?)["\'] (?:to|onto|into) (?:the )?["\'](.+?)["\'] (?:column|lane|list|bucket|swimlane|zone|area)$',
                                                   'mouse_drag_to',   lambda m: {'source': _q(m.group(1)), 'target': _q(m.group(2))}),
    (r'^drags? ["\'](.+?)["\'] (?:above|below|before|after) ["\'](.+?)["\']$',
                                                   'mouse_drag_to',   lambda m: {'source': _q(m.group(1)), 'target': _q(m.group(2))}),
    (r'^drags? ["\'](.+?)["\'] (?:to|onto|into|over) (?:the )?["\'](.+?)["\'] using the mouse$',
                                                   'mouse_drag_to',   lambda m: {'source': _q(m.group(1)), 'target': _q(m.group(2))}),
    # Sliders / range inputs — price filters, volume, ratings.
    (r'^(?:drags?|sets?|moves?) (?:the )?["\'](.+?)["\'] (?:slider|range|handle|thumb) to (-?\d+(?:\.\d+)?)$',
                                                   'set_slider',      lambda m: {'locator': _q(m.group(1)), 'value': float(m.group(2))}),
    (r'^(?:drags?|sets?|moves?) (?:the )?slider ["\'](.+?)["\'] to (-?\d+(?:\.\d+)?)$',
                                                   'set_slider',      lambda m: {'locator': _q(m.group(1)), 'value': float(m.group(2))}),

    # Drag and drop (NOOD_0009) — both ends quoted; Playwright drag_to.
    (r'^drags? ["\'](.+?)["\'] (?:to|onto|into|over) (?:the )?["\'](.+?)["\']$',
                                                   'drag',            lambda m: {'source': _q(m.group(1)), 'target': _q(m.group(2))}),

    # NOOD_0152 — relative dates. MUST precede every fill pattern below:
    # "enters today's date in the 'Start date' field" MATCHED the generic fill
    # and typed the literal string "today's date" into the box. A silent wrong
    # answer, and a classic unexplainable red in booking/HR/trial suites.
    (r"^(?:enters?|types?|picks?|selects?|sets?) (?:the )?(today|tomorrow|yesterday)'?s? date (?:in|into|for|as) (?:the )?[\"']?(.+?)[\"']?(?: field| box| input| picker)?$",
                                                   'fill_date',       lambda m: {'locator': _q(m.group(2)),
                                                                                 'offset_days': {'today': 0, 'tomorrow': 1, 'yesterday': -1}[m.group(1)]}),
    (r"^(?:enters?|types?|picks?|selects?|sets?) (?:the )?date (\d+) days? (from now|ago|in the future|in the past) (?:in|into|for|as) (?:the )?[\"']?(.+?)[\"']?(?: field| box| input| picker)?$",
                                                   'fill_date',       lambda m: {'locator': _q(m.group(3)),
                                                                                 'offset_days': int(m.group(1)) * (-1 if m.group(2) in ('ago', 'in the past') else 1)}),

    # Scoped fills (NOOD_0009) — MUST precede the generic fill patterns, whose
    # greedy "types X into Y" would swallow the row/section suffix as locator.
    (r'^(?:enters?|types?) (.+?) in(?:to)? (?:the )?(.+?) (?:field|box|input) in (?:the )?row (?:containing|with) ["\'](.+?)["\']$',
                                                   'fill_in_row',     lambda m: {'value': _q(m.group(1)), 'locator': _q(m.group(2)), 'row': _q(m.group(3))}),
    (r'^(?:enters?|types?) (.+?) in(?:to)? (?:the )?(.+?) (?:field|box|input) in (?:the )?["\'](.+?)["\'] (?:section|panel|dialog|region|area)$',
                                                   'fill_in_section', lambda m: {'value': _q(m.group(1)), 'locator': _q(m.group(2)), 'section': _q(m.group(3))}),

    # Table-driven form fill (NOOD_0011) — "fills in the form with:" + a
    # | field | value | table. MUST precede the generic fill below, whose
    # "fills X with Y" would otherwise capture locator='form', value=':'.
    (r'^fills? (?:in )?the form with:?$',          'fill_form_table', lambda m: {}),

    # NOOD_0062 — qualified-field fills. MUST precede the generic fill
    # patterns, whose greedy captures would swallow the qualifier as locator.
    # "types 'abc' into the field with the label 'Username'"
    (r'^(?:enters?|types?|provides?) (.+?) (?:in|into|to|on) (?:the |a |an )?(?:field|input|box|text ?box|text ?area) (?:with|having|named|labell?ed(?: as)?|whose (?:label|name|placeholder|title) is) (?:the |a |an )?(?:label|name|placeholder|title)?(?: value)?(?: of| is)?\s*["\'](.+?)["\']$',
                                                   'fill',           lambda m: {'value': _q(m.group(1)), 'locator': _q(m.group(2))}),
    # "enters 'john' as the username" / "provides 'x' for the email field"
    (r'^(?:enters?|types?|provides?) ["\'](.+?)["\'] (?:as|for) (?:the )?(.+?)(?: field| box| input| value)?$',
                                                   'fill',           lambda m: {'value': _q(m.group(1)), 'locator': _q(m.group(2))}),

    # Fill / Enter / Type
    (r'^enters? (.+?) in(?:to)? (?:the )?(.+?) (?:field|box|input)$',
                                                   'fill',           lambda m: {'value': _q(m.group(1)), 'locator': _q(m.group(2))}),
    (r'^types? (.+?) (?:in|into) (?:the )?(.+)$',
                                                   'fill',           lambda m: {'value': _q(m.group(1)), 'locator': _q(m.group(2))}),
    (r'^fills? (?:in )?(?:the )?(.+?) with (.+)$',
                                                   'fill',           lambda m: {'locator': _q(m.group(1)), 'value': _q(m.group(2))}),
    (r'^clears? (?:the )?(.+?) (?:field|box|input)$',
                                                   'clear',          lambda m: {'locator': _q(m.group(1))}),

    # --- Screen/terminal bridge (NOOD_0024) — OCR + page-coordinate, no DOM.
    # Raw keyboard type (no locator): "types 'ls -la'" / "enters 'login admin'".
    # MUST follow the fill patterns above so "types X into Y" still routes to fill.
    (r'^(?:types?|enters?) ["\'](.+?)["\']$',      'type_text',      lambda m: {'text': _q(m.group(1))}),
    # Focus OCR/screen reads to a region: "focuses on the 'top-left' region".
    (r'^focus(?:es)? on (?:the )?["\'](.+?)["\'] (?:region|area)$',
                                                   'focus_region',   lambda m: {'region': _q(m.group(1))}),
    # NOOD_0114 — focus on a DOM element's rendered box: "focuses on the
    # 'hero' image". Later screen/OCR steps scan only that element (carousel
    # tile, flyer, profile picture…). MUST follow the region form above.
    (rf'^focus(?:es)? on (?:the )?["\'](.+?)["\'] {_IMG}$',
                                                   'focus_element',  lambda m: {'locator': _q(m.group(1))}),
    # NOOD_0152 — same action, ordinary layout containers ('order summary'
    # panel, a terminal pane, a results card). focus_element was reachable
    # only through the _IMG noun set, so any non-image box missed entirely.
    (rf'^focus(?:es)? on (?:the )?["\'](.+?)["\'] {_BOX}$',
                                                   'focus_element',  lambda m: {'locator': _q(m.group(1))}),

    # Cell click (NOOD_0011) — "clicks the cell under 'Status' in the row
    # containing 'Contoso'". MUST precede the scoped/generic click catch-alls.
    (r'^clicks? (?:on )?(?:the )?cell under (?:the )?["\'](.+?)["\'](?: header| column)? in (?:the )?row (?:containing|with) ["\'](.+?)["\']$',
                                                   'click_cell',     lambda m: {'column': _q(m.group(1)), 'row': _q(m.group(2))}),

    # Scoped clicks (11.2) — MUST precede the generic click catch-alls below,
    # which would otherwise swallow the whole "X in the row/section ..." phrase.
    (r'^clicks? (?:on )?["\']?(.+?)["\']? in (?:the )?row (?:containing|with) ["\'](.+?)["\']$',
                                                   'click_in_row',   lambda m: {'locator': _q(m.group(1)), 'row': _q(m.group(2))}),
    (r'^clicks? (?:on )?["\']?(.+?)["\']? in (?:the )?["\'](.+?)["\'] (?:section|panel|dialog|region|area)$',
                                                   'click_in_section', lambda m: {'locator': _q(m.group(1)), 'section': _q(m.group(2))}),

    # Keyboard keys (11.1) — a real keypress, distinct from "press the X button"
    # (a click). MUST precede the press-button + click catch-alls.
    # Chords (NOOD_0009): 'Control+A', 'Shift+Tab', 'Ctrl+Shift+K' — the action
    # normalises Ctrl/Cmd/Option aliases before handing to Playwright.
    # NOOD_0177 — the chord segment excludes '+' itself. With [^\s"']+ the class
    # could swallow the very '+' that separates segments, giving 2^k ways to
    # split k pairs: a 110-byte .feature file cost 39.8s of backtracking through
    # check_feature(). Excluding '+' makes the split unambiguous (linear) and is
    # behaviour-identical — no real key name contains '+'.
    (r'^presses? (?:the )?["\']?((?:Control|Ctrl|Alt|Option|Shift|Meta|Cmd|Command)(?:\s*\+\s*[^\s"\'+]+)+)["\']?(?: keys?)?$',
                                                   'press_key',      lambda m: {'key': m.group(1)}),
    (r'^presses? (?:the )?["\']?(Enter|Return|Tab|Escape|Esc|Space|Backspace|Delete|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|Up|Down|Left|Right|Home|End|PageUp|PageDown)["\']?(?: key)?$',
                                                   'press_key',      lambda m: {'key': m.group(1)}),

    # Coordinate / OCR clicks (NOOD_0024) — MUST precede the generic click
    # catch-alls, which would otherwise capture "at 10, 20" / "on the text ..."
    # as a DOM locator.
    (r'^clicks? at \(?(\d+)\s*,\s*(\d+)\)?$',       'click_at',       lambda m: {'x': int(m.group(1)), 'y': int(m.group(2))}),
    (r'^clicks? on (?:the )?(?:screen )?text ["\'](.+?)["\']$',
                                                   'click_text',     lambda m: {'text': _q(m.group(1))}),
    # NOOD_0114 — OCR click inside one element's box: "clicks 'Dog' in the
    # 'product carousel' image". MUST precede the generic click catch-alls.
    (rf'^clicks? (?:on )?(?:the )?(?:text )?["\'](.+?)["\'] (?:in|inside|within|on) (?:the )?["\'](.+?)["\'] {_IMG}$',
                                                   'click_image_text', lambda m: {'text': _q(m.group(1)), 'locator': _q(m.group(2))}),

    # NOOD_0152 — right-click → context-menu pick. MUST precede the bare
    # right_click below, whose greedy (.+?) swallowed the whole tail into the
    # locator ("Row 1' and selects 'Delete") — a silent mis-route that
    # right-clicked a nonexistent element instead of failing cleanly.
    (r'^right[- ]clicks? (?:on )?(?:the )?["\'](.+?)["\'] (?:and|then) (?:selects?|clicks?|chooses?|picks?) (?:the )?["\'](.+?)["\'](?: (?:menu )?(?:item|option|entry))?$',
                                                   'context_menu_select', lambda m: {'locator': _q(m.group(1)), 'item': _q(m.group(2))}),

    # NOOD_0152 — modifier clicks (multi-select in grids, file managers, mail).
    # MUST precede every click catch-all: "clicks 'Row 2' while holding Shift"
    # previously MATCHED the plain click pattern with the modifier text baked
    # into the locator, so it silently did the wrong thing rather than missing.
    (r'^(?:clicks?|taps?) (?:on )?(?:the )?["\'](.+?)["\'] (?:while |whilst )?(?:holding|with|pressing)(?: down)? (?:the )?((?:Ctrl|Control|Shift|Alt|Option|Meta|Cmd|Command)(?:\s*(?:\+|and)\s*(?:Ctrl|Control|Shift|Alt|Option|Meta|Cmd|Command))*)(?: keys?| held)?$',
                                                   'click_modifier', lambda m: {'locator': _q(m.group(1)), 'modifiers': re.split(r'\s*(?:\+|and)\s*', m.group(2))}),
    (r'^((?:ctrl|control|shift|alt|option|meta|cmd|command)(?:[-+](?:ctrl|control|shift|alt|option|meta|cmd|command))*)[- ]clicks? (?:on )?(?:the )?["\'](.+?)["\']$',
                                                   'click_modifier', lambda m: {'locator': _q(m.group(2)), 'modifiers': re.split(r'[-+]', m.group(1))}),

    # Double / right click (NOOD_0025) — MUST precede the generic click
    # catch-alls (which start with "clicks", so they can't match these anyway,
    # but keep them grouped). Strip an optional quoted locator with _q.
    (r'^double[- ]clicks? (?:on )?(?:the )?(.+?)(?: button| link)?$',
                                                   'double_click',   lambda m: {'locator': _q(m.group(1))}),
    (r'^right[- ]clicks? (?:on )?(?:the )?(.+?)(?: button| link)?$',
                                                   'right_click',    lambda m: {'locator': _q(m.group(1))}),

    # Submit a form (NOOD_0025) — the form name is descriptive only; we click
    # the form's submit control. MUST precede the generic click catch-alls.
    (r'^submits? (?:the )?(.+?) form$',            'submit',         lambda m: {'locator': _q(m.group(1))}),

    # Device keys (Phase F, Appium) — MUST precede the press-button click
    # catch-all. On web this degrades to a normal click on "back"/"home".
    (r'^presses? the (back|home) button$',         'device_key',     lambda m: {'key': m.group(1).lower()}),

    # Long press (NOOD_0032, Appium) — MUST precede the press-button and
    # click catch-alls, which would otherwise eat "presses and holds".
    # NOOD_0152 — explicit hold duration. MUST precede the bare form below,
    # whose greedy locator capture silently swallowed "for 2 seconds" INTO the
    # locator ("'Row 1' for 2 seconds") — a mis-route, not a clean miss. The
    # action already accepted `seconds`; only the phrasing was unreachable.
    (r'^(?:long[- ]presses?|presses? and holds?) (?:on )?(?:the )?(.+?)(?: button)? for (\d+(?:\.\d+)?) seconds?$',
                                                   'long_press',     lambda m: {'locator': _q(m.group(1)), 'seconds': float(m.group(2))}),
    (r'^(?:long[- ]presses?|presses? and holds?) (?:on )?(?:the )?(.+?)(?: button)?$',
                                                   'long_press',     lambda m: {'locator': _q(m.group(1))}),

    # NOOD_0062 — role-word + qualifier clicks. MUST precede the generic click
    # catch-alls, which would capture the whole qualifier phrase as locator.
    # "clicks the button with a label 'stonemountain'" / "clicks the link
    # whose text is 'Sign up'" / "taps the element containing 'Add to cart'"
    (r'^(?:clicks?|taps?|presses?) (?:on |upon )?(?:the |a |an )?(?:button|link|element|icon|tab|option|item|control|menu(?: item)?) (?:with|having|whose|that has|labell?ed(?: as)?|containing) (?:the |a |an )?(?:label|text|name|title|caption|value|id|aria-label|placeholder)?(?: value)?(?: of| is|:)?\s*["\'](.+?)["\']$',
                                                   'click',          lambda m: {'locator': _q(m.group(1))}),

    # Click / Press / Tap — "on|upon" tolerated everywhere (NOOD_0062).
    (r'^clicks? (?:on |upon )?(?:the )?(.+?) button$',
                                                   'click',          lambda m: {'locator': _q(m.group(1))}),
    (r'^clicks? (?:on |upon )?(?:the )?(.+?) link$',
                                                   'click',          lambda m: {'locator': _q(m.group(1))}),
    (r'^clicks? (?:on |upon )?["\'](.+?)["\']$',   'click',          lambda m: {'locator': _q(m.group(1))}),
    (r'^clicks? (?:on |upon )?(?:the )?(.+)$',     'click',          lambda m: {'locator': _q(m.group(1))}),
    (r'^presses? (?:on )?(?:the )?(.+?) button$',  'click',          lambda m: {'locator': _q(m.group(1))}),
    (r'^taps? (?:on )?(?:the )?(.+)$',             'click',          lambda m: {'locator': _q(m.group(1))}),

    # Hover (11.1)
    (r'^hovers? (?:over|on) (?:the )?(.+)$',        'hover',          lambda m: {'locator': _q(m.group(1))}),

    # Variable write target: `name` (captured, preferred) or [name] (legacy).
    # Seed a literal into a variable (12.1) — e.g. an expected value.
    (r'^sets? [\[`]([^\]`]+)[\]`] to ["\'](.+?)["\']$',
                                                   'set_var',        lambda m: {'var': m.group(1), 'value': _q(m.group(2))}),

    # NOOD_0114 — OCR extraction into a variable: what an image *renders*
    # (carousel tile price, flyer text) rather than DOM text. MUST precede the
    # generic store-text pattern below, which shares the 'grabs' verb.
    (rf'^(?:reads?|grabs?|extracts?|scans?) (?:the )?(?:text|contents?|caption) (?:from|of|in) (?:the )?["\'](.+?)["\'] {_IMG} (?:as|into|in) [\[`]([^\]`]+)[\]`]$',
                                                   'read_image_text', lambda m: {'locator': _q(m.group(1)), 'var': m.group(2)}),
    (rf'^(?:reads?|grabs?|extracts?|scans?) (?:the )?(?:number|price|amount|count|quantity|value) (?:from|of|in) (?:the )?["\'](.+?)["\'] {_IMG} (?:as|into|in) [\[`]([^\]`]+)[\]`]$',
                                                   'read_image_number', lambda m: {'locator': _q(m.group(1)), 'var': m.group(2)}),
    (r'^(?:reads?|extracts?|scans?) (?:the )?screen text (?:as|into|in) [\[`]([^\]`]+)[\]`]$',
                                                   'read_screen_text', lambda m: {'var': m.group(1)}),

    # Store an element ATTRIBUTE into a variable (12.1) — MUST precede the
    # generic store-text pattern below, which would otherwise eat the phrase.
    (r'^stores? attribute ["\'](.+?)["\'] of (?:the )?(.+?) (?:as|into|in) [\[`]([^\]`]+)[\]`]$',
                                                   'store_attribute', lambda m: {'attribute': _q(m.group(1)), 'locator': _q(m.group(2)), 'var': m.group(3)}),

    # Store/grab element text into a variable (11.1) — usable by later steps.
    # "X" <role> form: strip the role word so the locator is just the name.
    # Must precede the generic pattern below.
    # NOOD_0152 — clipboard → variable. MUST precede both store_text forms:
    # "stores the clipboard as `C`" matched the generic one and hunted the DOM
    # for an element named "clipboard". read_clipboard already existed but was
    # only ever reachable from inside assert_clipboard.
    (r'^(?:stores?|grabs?|reads?) (?:the )?clipboard(?: contents?| text)? (?:as|into|in) [\[`]([^\]`]+)[\]`]$',
                                                   'store_clipboard', lambda m: {'var': m.group(1)}),
    (r'^(?:stores?|grabs?) (?:the )?["\'](.+?)["\'] (?:heading|text|cell|label|element) (?:as|into|in) [\[`]([^\]`]+)[\]`]$',
                                                   'store_text',     lambda m: {'locator': m.group(1), 'var': m.group(2)}),
    (r'^(?:stores?|grabs?) (?:the )?(.+?) (?:as|into|in) [\[`]([^\]`]+)[\]`]$',
                                                   'store_text',     lambda m: {'locator': _q(m.group(1)), 'var': m.group(2)}),

    # Switch into an iframe (11.2) — and back out (NOOD_0009). "main tab/window"
    # already routes to switch_tab above, so this only owns frame/content/document.
    (r'^switches? (?:back )?to (?:the )?main (?:frame|content|document)$',
                                                   'switch_main_frame', lambda m: {}),
    # NOOD_0152 — NESTED frames (a payment iframe inside a vendor iframe).
    # MUST precede the single-frame form, whose (.+?) captured the whole
    # phrase "inner' frame inside the 'outer" as one frame name and silently
    # resolved to nothing. Listed outermost-first in the step, as read.
    (r'^switches? to (?:the )?["\'](.+?)["\'] (?:frame|iframe) (?:inside|within|in) (?:the )?["\'](.+?)["\'] (?:frame|iframe)$',
                                                   'switch_frame_chain', lambda m: {'names': [_q(m.group(2)), _q(m.group(1))]}),
    (r'^switches? to (?:the )?["\'](.+?)["\'] (?:frame|iframe)$',
                                                   'switch_frame',   lambda m: {'name': _q(m.group(1))}),

    # Select / Check — accept "from the X" and "in the X" (NOOD_0025).
    # Multi-value select (NOOD_0008 gap #5): a quoted list joined by "and"/commas.
    # MUST precede the single select, whose greedy capture used to swallow the
    # whole list as one value.
    (r'^selects? ((?:["\'][^"\']+["\'])(?:\s*(?:,|and)\s*["\'][^"\']+["\'])+) (?:from|in) (?:the )?(.+)$',
                                                   'select_multi',   lambda m: {'values': re.findall(r'["\']([^"\']+)["\']', m.group(1)), 'locator': _q(m.group(2))}),
    # Single select — value capture rejects quotes so a quoted list can never
    # false-match as one value (gap #9).
    (r'^selects? ["\']([^"\']+)["\'](?: option)? (?:from|in|on) (?:the )?(.+)$',
                                                   'select',         lambda m: {'value': m.group(1), 'locator': _q(m.group(2))}),
    # Radio vocabulary (NOOD_0008 low note) — reads better than phrasing a
    # radio as a checkbox; same check() action underneath.
    (r'^selects? (?:the )?["\']?(.+?)["\']? radio(?: button| option)?$',
                                                   'check',          lambda m: {'locator': _q(m.group(1))}),
    (r'^checks? (?:the )?["\']?(.+?)["\']? checkbox$',
                                                   'check',          lambda m: {'locator': _q(m.group(1))}),
    (r'^unchecks? (?:the )?["\']?(.+?)["\']? checkbox$',
                                                   'uncheck',        lambda m: {'locator': _q(m.group(1))}),

    # Wait
    # NOOD_0044 — timed page-load sleep: "waits for the page to load : 20
    # seconds" / "... for 20 seconds". A hard sleep (thread sleep), not a
    # load-state wait — MUST precede the plain wait_load pattern below.
    # Accepts the value bare, quoted, or backticked (an unresolved
    # {var:20 seconds} survives normalize_phrasing as `20 seconds`).
    (r'^waits? for (?:the )?page to load(?:\s*[:\-]\s*|\s+for\s+)["\'`\[]?(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?)["\'`\]]?$',
                                                   'wait_seconds',     lambda m: {'seconds': float(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]}),
    (r'^waits? for (?:the )?page to (?:load|be ready)$',
                                                   'wait_load',        lambda m: {}),
    (r'^waits? for (?:the )?page to fully load$',
                                                   'wait_networkidle', lambda m: {}),
    (r'^waits? for (?:the )?network to be idle$',
                                                   'wait_networkidle', lambda m: {}),
    # OCR wait (NOOD_0024) — MUST precede the generic wait-until below.
    (r'^waits? until (?:the )?(?:screen|terminal) (?:shows?|displays?) ["\'](.+?)["\']$',
                                                   'wait_screen_text', lambda m: {'text': _q(m.group(1))}),

    # NOOD_0152 — state/count/change waits. ALL of these MUST precede the
    # generic "waits until X is visible|appears" forms below, whose (.+?)
    # catch-all would swallow "the 'Save' button is enabled" as element TEXT
    # and then hunt the DOM for that literal sentence. These replace the hard
    # sleep, which was the only tool for "not ready yet" beyond visibility.
    (r'^waits? until (?:the )?["\']?(.+?)["\']?(?: (?:button|field|input|box|link|checkbox|element|icon|dropdown|menu))? (?:is|becomes?) (enabled|disabled|checked|unchecked|selected|editable|read-?only)$',
                                                   'wait_state',     lambda m: {'locator': _q(m.group(1)), 'state': m.group(2)}),
    (r'^waits? until (?:the )?["\']?(.+?)["\']?(?: (?:button|field|input|box|link|element))? is (?:not|no longer) (enabled|disabled|checked|unchecked|selected|editable)$',
                                                   'wait_state',     lambda m: {'locator': _q(m.group(1)),
                                                                                'state': {'enabled': 'disabled', 'disabled': 'enabled',
                                                                                          'checked': 'unchecked', 'unchecked': 'checked',
                                                                                          'selected': 'unchecked', 'editable': 'readonly'}[m.group(2)]}),
    # "waits until there are 10 'rows'" — the right wait for "results loaded".
    (r'^waits? until there (?:are|is) (?:at least )?(\d+) ["\'](.+?)["\'](?: items?| results?| rows?| elements?| entries?)?$',
                                                   'wait_count',     lambda m: {'locator': _q(m.group(2)), 'count': int(m.group(1)), 'op': '>='}),
    (r'^waits? until (?:the )?["\'](.+?)["\'] count (?:is|reaches) (\d+)$',
                                                   'wait_count',     lambda m: {'locator': _q(m.group(1)), 'count': int(m.group(2)), 'op': '=='}),
    # Live tickers / async totals — "changed from X" pins the old value.
    (r'^waits? until (?:the )?["\'](.+?)["\'] changes? from ["\'](.+?)["\']$',
                                                   'wait_text_change', lambda m: {'locator': _q(m.group(1)), 'was': _q(m.group(2))}),
    (r'^waits? until (?:the )?["\'](.+?)["\'] (?:changes?|updates?)$',
                                                   'wait_text_change', lambda m: {'locator': _q(m.group(1)), 'was': None}),
    # Network response wait — the correct replacement for "waits 3 seconds"
    # in an SPA. Previously MISSED into wait_visible, hunting the page for the
    # literal text "response from '/api/orders'".
    (r'^waits? for (?:the )?(?:response|reply) (?:from|to|for) ["\'](.+?)["\']' + _WITHIN + r'$',
                                                   'wait_response',  lambda m: {'fragment': _q(m.group(1)), 'timeout': _ms(m.group(2))}),
    (r'^waits? for (?:the )?["\'](.+?)["\'] (?:request|call|api call) to (?:complete|finish|respond)' + _WITHIN + r'$',
                                                   'wait_response',  lambda m: {'fragment': _q(m.group(1)), 'timeout': _ms(m.group(2))}),
    # Per-step wait timeout (NOOD_0009) — "for up to N seconds" overrides
    # NOODLE_TIMEOUT for this one wait. MUST precede the open-ended waits below.
    (r'^waits? until ["\'](.+?)["\'] (?:is visible|appears?|loads?) (?:for )?(?:up to |within )?(\d+) seconds?$',
                                                   'wait_visible',     lambda m: {'text': _q(m.group(1)), 'timeout': int(m.group(2)) * 1000}),
    (r'^waits? until ["\'](.+?)["\'] (?:disappears?|is hidden|is gone|vanishes) (?:for )?(?:up to |within )?(\d+) seconds?$',
                                                   'wait_hidden',      lambda m: {'text': _q(m.group(1)), 'timeout': int(m.group(2)) * 1000}),
    (r'^waits? until ["\'](.+?)["\'] (?:is visible|appears?|loads?)$',
                                                   'wait_visible',     lambda m: {'text': _q(m.group(1))}),
    (r'^waits? until (.+?) (?:is visible|appears?|loads?)$',
                                                   'wait_visible',     lambda m: {'text': m.group(1)}),
    (r'^waits? until ["\'](.+?)["\'] (?:disappears?|is hidden|is gone|vanishes)$',
                                                   'wait_hidden',      lambda m: {'text': _q(m.group(1))}),
    (r'^waits? until (.+?) (?:disappears?|is hidden|is gone|vanishes)$',
                                                   'wait_hidden',      lambda m: {'text': m.group(1)}),
    # NOOD_0044 — hard sleep: optional "for", sleep/pause verbs, ms/min/hour
    # units. "waits 3 seconds" and "sleeps for 500 ms" both land here.
    (r'^(?:waits?|sleeps?|pauses?)(?: for)?(?: up to| another| an? further)? (\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|h|milliseconds?|ms)(?: more)?$',
                                                   'wait_seconds',     lambda m: {'seconds': float(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]}),

    # NOOD_0143 — blocking URL wait (audit gap): SPA flows where navigation
    # completes async and the next step needs the new route. MUST precede the
    # generic element waits below — their "waits for the X" catch-all would
    # swallow it as an element wait.
    (r'^waits? (?:for|until) the url (?:to )?(?:contains?|includes?) ["\'](.+?)["\']$',
                                                   'wait_url',       lambda m: {'fragment': _q(m.group(1))}),
    (r'^waits? (?:for|until) the url (?:to )?(?:be|is|equals?) ["\'](.+?)["\']$',
                                                   'wait_url',       lambda m: {'fragment': _q(m.group(1)), 'mode': 'exact'}),

    # NOOD_0062 — generic element waits. "waits for the loading icon" (bare)
    # waits for it to be visible; "... to disappear/be gone" waits it out.
    # MUST follow every timed/page-load wait above so digits and "page to
    # load" keep their meaning.
    (r'^waits? (?:for|until) (?:the )?["\'](.+?)["\'](?: to)? (?:disappears?|vanish(?:es)?|go(?:es)? away|is (?:gone|hidden|dismissed)|be (?:gone|hidden|dismissed|removed))$',
                                                   'wait_hidden',      lambda m: {'text': _q(m.group(1))}),
    (r'^waits? (?:for|until) (?:the )?(.+?) (?:to )?(?:disappears?|vanish(?:es)?|go(?:es)? away|is (?:gone|hidden|dismissed)|be (?:gone|hidden|dismissed|removed)|no longer (?:be )?(?:visible|seen|shown|displayed))$',
                                                   'wait_hidden',      lambda m: {'text': _q(m.group(1))}),
    (r'^waits? for (?:the )?(.+?) to (?:appear|show(?: up)?|be (?:visible|seen|shown|displayed|present)|load|render|finish(?: loading)?)$',
                                                   'wait_visible',     lambda m: {'text': _q(m.group(1))}),
    (r'^waits? (?:for|until|on) (?:the )?["\'](.+?)["\']$',
                                                   'wait_visible',     lambda m: {'text': _q(m.group(1))}),
    (r'^waits? for (?:the )?(.+)$',                'wait_visible',     lambda m: {'text': _q(m.group(1))}),

    # Table/grid scrollbars (NOOD_0011) — scroll the grid's own scroll
    # container, not the page. bottom/top jump; right/left/down/up page.
    # MUST precede the generic scroll patterns below.
    (r'^scrolls? (?:the )?(?:["\'](.+?)["\'] )?(?:table|grid) (?:to the )?(bottom|top|right|left|down|up)$',
                                                   'scroll_table',   lambda m: {'name': _q(m.group(1)) if m.group(1) else None, 'direction': m.group(2).lower()}),

    # NOOD_0152 — scroll INSIDE any named container. scroll_table above needs
    # the literal noun table/grid, and scroll_edge below is page-level, so a
    # sidebar / results list / chat pane had no step at all. MUST sit between
    # them: after the table form (which is more specific), before the
    # page-level one (whose "to the bottom" would swallow the container name).
    (rf'^scrolls? (?:the )?["\'](.+?)["\'] {_BOX}? ?(?:to the )?(bottom|top|right|left|down|up)$',
                                                   'scroll_container', lambda m: {'locator': _q(m.group(1)), 'direction': m.group(2).lower()}),
    # NOOD_0152 — infinite scroll / lazy load. scroll_to only reaches elements
    # ALREADY in the DOM, so it can never drive the loader that adds them.
    (r'^scrolls? until ["\'](.+?)["\'] (?:is visible|appears?|loads?)$',
                                                   'scroll_until_visible', lambda m: {'text': _q(m.group(1))}),
    (r'^(?:loads?|reveals?) all (?:items?|results?|rows?|products?)(?: by scrolling)?$',
                                                   'scroll_until_visible', lambda m: {'text': None}),

    # NOOD_0143 — full-page jump (audit gap): the generic scroll below is a
    # one-viewport nudge; lazy-load footers and infinite lists need the real
    # bottom. MUST follow scroll_table (which requires the table/grid word).
    (r'^scrolls? (?:the page )?to the (bottom|top)(?: of the page)?$',
                                                   'scroll_edge',    lambda m: {'edge': m.group(1).lower()}),

    # Scroll
    (r'^scrolls? down$',                           'scroll',         lambda m: {'direction': 'down'}),
    (r'^scrolls? up$',                             'scroll',         lambda m: {'direction': 'up'}),
    (r'^scrolls? to ["\'](.+?)["\']$',             'scroll_to',      lambda m: {'locator': _q(m.group(1))}),

    # Assertions
    # Scoped visibility (NOOD_0009) — MUST precede the generic "should see".
    (r'^should (not )?see ["\'](.+?)["\'] in (?:the )?row (?:containing|with) ["\'](.+?)["\']$',
                                                   'assert_in_row',  lambda m: {'negate': bool(m.group(1)), 'text': _q(m.group(2)), 'row': _q(m.group(3))}),
    (r'^should (not )?see ["\'](.+?)["\'] in (?:the )?["\'](.+?)["\'] (?:section|panel|dialog|region|area)$',
                                                   'assert_in_section', lambda m: {'negate': bool(m.group(1)), 'text': _q(m.group(2)), 'section': _q(m.group(3))}),
    # a11y count cap (Phase P) — MUST precede the generic count comparisons,
    # whose "should see at most N X" would swallow it as locator text.
    # NOOD_0152 — the impact-qualified forms MUST precede the bare one, and all
    # of them the count comparisons. assert_a11y already took both params; only
    # the phrasing was missing, so an impact word used to mis-route to
    # assert_count (hunting the DOM for the literal text "serious accessibility
    # violations") or to the assert_semantic vision catch-all. Enterprise a11y
    # gates are always "no new criticals, at most N serious" — a bare
    # zero-tolerance gate is unusable on a legacy app.
    (r'^(?:the (?:page|screen) should have|should see) at most (\d+) (minor|moderate|serious|critical) accessibility violations?$',
                                                   'assert_a11y',    lambda m: {'impact': m.group(2), 'max': int(m.group(1))}),
    (r'^(?:the (?:page|screen) should have|should see) at most (\d+) accessibility violations?$',
                                                   'assert_a11y',    lambda m: {'impact': None, 'max': int(m.group(1))}),
    # NOOD_0197 — the disjunctive assertion ("any of" / "either" / "one of").
    # An OR of alternatives is one step, never N conjunctive `sees` steps.
    # MUST precede the count comparisons: their `at least (\d+) "X"` shape
    # would swallow `at least 2 of "A", "B"` with locator `of "A", "B"`.
    (r'^(?:should see|sees?) (?:(?:any|one) of|either) ((?:["\'][^"\']+["\'](?:,? *(?:or )?)?)+)$',
                                                   'assert_any_visible', lambda m: {'alternatives': _alternatives(m.group(1))}),
    (r'^(?:should see|sees?) at least (\d+) of ((?:["\'][^"\']+["\'](?:,? *(?:or )?)?)+)$',
                                                   'assert_any_visible', lambda m: {'alternatives': _alternatives(m.group(2)), 'min_count': int(m.group(1))}),
    # Count comparisons (NOOD_0009) — exact counts make list tests brittle.
    (r'^should see at least (\d+) ["\']?(.+?)["\']?(?: items?| results?| rows?| elements?| entries?)?$',
                                                   'assert_count',   lambda m: {'count': int(m.group(1)), 'locator': _q(m.group(2)), 'op': '>='}),
    (r'^should see at most (\d+) ["\']?(.+?)["\']?(?: items?| results?| rows?| elements?| entries?)?$',
                                                   'assert_count',   lambda m: {'count': int(m.group(1)), 'locator': _q(m.group(2)), 'op': '<='}),
    (r'^should see (?:more than|over) (\d+) ["\']?(.+?)["\']?(?: items?| results?| rows?| elements?| entries?)?$',
                                                   'assert_count',   lambda m: {'count': int(m.group(1)), 'locator': _q(m.group(2)), 'op': '>'}),
    (r'^should see (?:fewer|less) than (\d+) ["\']?(.+?)["\']?(?: items?| results?| rows?| elements?| entries?)?$',
                                                   'assert_count',   lambda m: {'count': int(m.group(1)), 'locator': _q(m.group(2)), 'op': '<'}),
    # Count assertion (11.1) — MUST precede the generic "should see X" below.
    (r'^should see (\d+) ["\']?(.+?)["\']?(?: items?| results?| rows?| elements?| entries?)?$',
                                                   'assert_count',   lambda m: {'count': int(m.group(1)), 'locator': _q(m.group(2))}),
    # NOOD_0115 — numeric read of a results-summary/badge/pagination element:
    # "the number in 'results count' should be at least 90". POM-aware via
    # get_text; parses the first number out of e.g. '93 results' / '1,234 items'.
    # NOOD_0152 — tolerance and range. MUST precede the plain comparisons
    # below, whose "should be <N>" would match first and drop the ± clause.
    # Mandatory wherever rounding is real (fintech totals, tax, FX, latency):
    # assert_compare is exact and refuses currency strings like '$45.00'.
    (r'^(?:the )?number (?:in|shown in|displayed in|of) ["\'](.+?)["\'] (?:should be|is) (?:approximately |about |roughly |~)?(-?\d+(?:\.\d+)?) ?(?:±|\+/-|plus or minus|within) ?(\d+(?:\.\d+)?)$',
                                                   'assert_number_tolerance', lambda m: {'locator': _q(m.group(1)), 'expected': float(m.group(2)), 'tolerance': float(m.group(3))}),
    (r'^(?:the )?number (?:in|shown in|displayed in|of) ["\'](.+?)["\'] (?:should be|is) (?:approximately|about|roughly|around|~) ?(-?\d+(?:\.\d+)?)$',
                                                   'assert_number_tolerance', lambda m: {'locator': _q(m.group(1)), 'expected': float(m.group(2)), 'tolerance': 0.01}),
    (r'^(?:the )?number (?:in|shown in|displayed in|of) ["\'](.+?)["\'] (?:should be|is) between (-?\d+(?:\.\d+)?) and (-?\d+(?:\.\d+)?)$',
                                                   'assert_number_between', lambda m: {'locator': _q(m.group(1)), 'low': float(m.group(2)), 'high': float(m.group(3))}),
    (r'^(?:the )?number (?:in|shown in|displayed in|of) ["\'](.+?)["\'] (?:should be|is) at least (\d+(?:\.\d+)?)$',
                                                   'assert_number',  lambda m: {'locator': _q(m.group(1)), 'count': float(m.group(2)), 'op': '>='}),
    (r'^(?:the )?number (?:in|shown in|displayed in|of) ["\'](.+?)["\'] (?:should be|is) at most (\d+(?:\.\d+)?)$',
                                                   'assert_number',  lambda m: {'locator': _q(m.group(1)), 'count': float(m.group(2)), 'op': '<='}),
    (r'^(?:the )?number (?:in|shown in|displayed in|of) ["\'](.+?)["\'] (?:should be|is) (?:more|greater) than (\d+(?:\.\d+)?)$',
                                                   'assert_number',  lambda m: {'locator': _q(m.group(1)), 'count': float(m.group(2)), 'op': '>'}),
    (r'^(?:the )?number (?:in|shown in|displayed in|of) ["\'](.+?)["\'] (?:should be|is) (?:fewer|less) than (\d+(?:\.\d+)?)$',
                                                   'assert_number',  lambda m: {'locator': _q(m.group(1)), 'count': float(m.group(2)), 'op': '<'}),
    (r'^(?:the )?number (?:in|shown in|displayed in|of) ["\'](.+?)["\'] (?:should be|is|should equal|equals?) (?:exactly )?(\d+(?:\.\d+)?)$',
                                                   'assert_number',  lambda m: {'locator': _q(m.group(1)), 'count': float(m.group(2)), 'op': '=='}),
    # --- NOOD_0180: on screen, vs merely in the DOM --------------------------
    # assert_visible (and every "sees 'X' on the screen" phrasing below) rides
    # Playwright's is_visible(), which ignores clipping by a scroll container:
    # the last section of a horizontal strip reads visible at scrollLeft=0
    # while none of it is showing, so a scroll test asserted with "should see"
    # is green before the scroll. These own the rendered-truth question and
    # MUST precede the assert_visible block below.
    (rf'^(?:the )?["\'](.+?)["\'] {_BOX}? ?(?:is|are|should be|must be) (?:now )?'
     r'(?:in view|in the viewport|on ?screen|scrolled into view)$',
                                                   'assert_on_screen', lambda m: {'text': _q(m.group(1)), 'on': True}),
    (rf'^(?:the )?["\'](.+?)["\'] {_BOX}? ?(?:is not|isn\'?t|are not|aren\'?t|should not be|'
     r'shouldn\'?t be|must not be|is still not|is no longer) '
     r'(?:in view|in the viewport|on ?screen|scrolled into view)$',
                                                   'assert_on_screen', lambda m: {'text': _q(m.group(1)), 'on': False}),

    (r'^should see ["\'](.+?)["\']$',              'assert_visible', lambda m: {'text': _q(m.group(1))}),
    (r'^should see (.+)$',                         'assert_visible', lambda m: {'text': m.group(1)}),
    (r'^should not see ["\'](.+?)["\']$',          'assert_hidden',  lambda m: {'text': _q(m.group(1))}),
    (r'^should not see (.+)$',                     'assert_hidden',  lambda m: {'text': m.group(1)}),

    # --- NOOD_0062: robust visibility phrasing --------------------------------
    # First-person/ability: "can see 'X'", "sees 'X'", "is able to see 'X'".
    (r'^(?:can |is able to |should be able to )?sees? (?:the |a |an )?(?:text |message |label |button |link )?["\'](.+?)["\'](?: (?:displayed|on (?:the )?(?:page|screen)))?$',
                                                   'assert_visible', lambda m: {'text': _q(m.group(1))}),
    (r'^(?:cannot|can\'?t|can no longer|does not|doesn\'?t|is (?:not |no longer )able to) see (?:the |a |an )?(?:text |message |label |button |link )?["\'](.+?)["\'](?: (?:anymore|on (?:the )?(?:page|screen)))?$',
                                                   'assert_hidden',  lambda m: {'text': _q(m.group(1))}),
    # Page contains / does not contain — negative first, or the positive
    # branch would have to reject "does not" one word at a time.
    (r'^the page (?:does not|doesn\'?t|should not|must not|no longer) (?:contains?|shows?|displays?|has|have|includes?|renders?) (?:the |a |an )?(?:text|message|word|string|value|label|heading)?\s*["\'](.+?)["\']$',
                                                   'assert_hidden',  lambda m: {'text': _q(m.group(1))}),
    (r'^the page (?:should |still )?(?:contains?|shows?|displays?|has|have|includes?|renders?) (?:the |a |an )?(?:text|message|word|string|value|label|heading)?\s*["\'](.+?)["\']$',
                                                   'assert_visible', lambda m: {'text': _q(m.group(1))}),
    # "the element with the text value of '1234xyz' is seen" — and its negation.
    (r'^(?:the |an? )?(?:element|text|label|message|button|link|icon|heading) (?:with|having|containing) (?:the |a |an )?(?:text|label|value|name|title|caption)?(?: value)?(?: of| is)?\s*["\'](.+?)["\'] (?:is not|isn\'?t|are not|should not be|must not be|is no longer) (?:seen|visible|displayed|shown|present|rendered)$',
                                                   'assert_hidden',  lambda m: {'text': _q(m.group(1))}),
    (r'^(?:the |an? )?(?:element|text|label|message|button|link|icon|heading) (?:with|having|containing) (?:the |a |an )?(?:text|label|value|name|title|caption)?(?: value)?(?: of| is)?\s*["\'](.+?)["\'] (?:is|are|should be|must be) (?:seen|visible|displayed|shown|present|rendered)(?: on (?:the )?(?:page|screen))?$',
                                                   'assert_visible', lambda m: {'text': _q(m.group(1))}),
    # Gone / dismissed / no longer seen — quoted, then the safer unquoted forms.
    (r'^(?:the |a |an )?["\'](.+?)["\'](?: (?:element|button|link|icon|text|message|label|spinner|loader|banner|modal|dialog))? (?:is|has|was|should be|must be) (?:gone|dismissed|removed|hidden|invisible|missing|absent|no longer (?:seen|visible|displayed|shown|present|there)|not (?:seen|visible|displayed|shown|present)(?: any ?more)?)$',
                                                   'assert_hidden',  lambda m: {'text': _q(m.group(1))}),
    (r'^(?:the )?([^"\']+?) (?:is|has|was) (?:gone|dismissed|no longer (?:seen|visible|displayed|shown|present|there))(?: from the (?:page|screen))?$',
                                                   'assert_hidden',  lambda m: {'text': _q(m.group(1))}),
    (r'^(?:the )?([^"\']+?) (?:is not|isn\'?t) (?:seen|visible|displayed|shown|present)(?: any ?more)?$',
                                                   'assert_hidden',  lambda m: {'text': _q(m.group(1))}),
    # Plain "is visible" — quoted then unquoted (unquoted captures reject
    # quotes so a wrapper like "makes sure the 'X' ..." can never be eaten).
    (r'^(?:the |a |an )?["\'](.+?)["\'](?: (?:element|button|link|icon|text|message|label|banner|modal|dialog|heading|section|spinner|loader))? (?:is|are|should be|must be) (?:seen|visible|displayed|shown|present)(?: on (?:the )?(?:page|screen))?$',
                                                   'assert_visible', lambda m: {'text': _q(m.group(1))}),
    (r'^(?:the )?([^"\']+?) (?:is|should be) (?:seen|visible|displayed|shown)(?: on (?:the )?(?:page|screen))?$',
                                                   'assert_visible', lambda m: {'text': _q(m.group(1))}),
    (r'^(?:the )?([^"\']+?) (?:appears|shows up|is present)$',
                                                   'assert_visible', lambda m: {'text': _q(m.group(1))}),
    # Landing / redirect phrasing for URL asserts.
    (r'^(?:should )?(?:lands? on|arrives? (?:at|on)|ends? up (?:at|on)|is (?:redirected|taken|sent|navigated|brought) to) (?:the )?["\']?(.+?)["\']? page$',
                                                   'assert_url',     lambda m: {'fragment': _q(m.group(1))}),
    (r'^is (?:redirected|taken|sent|navigated|brought) to ["\'](.+?)["\']$',
                                                   'assert_url',     lambda m: {'fragment': _q(m.group(1))}),
    (r'^the (?:current )?(?:page )?url (?:should )?(?:contains?|includes?) ["\'](.+?)["\']$',
                                                   'assert_url',     lambda m: {'fragment': _q(m.group(1))}),

    (r'^should be (?:on|at) (?:the )?(.+?) page$',
                                                   'assert_url',     lambda m: {'fragment': m.group(1)}),
    (r'^should have url containing ["\'](.+?)["\']$',
                                                   'assert_url',     lambda m: {'fragment': _q(m.group(1))}),
    # Exact / ends-with URL asserts (NOOD_0009). "the url should equal" must sit
    # here, well before the generic assert_compare "X should equal Y" catch-all.
    (r'^should have url ending (?:with|in) ["\'](.+?)["\']$',
                                                   'assert_url',     lambda m: {'fragment': _q(m.group(1)), 'mode': 'ends'}),
    (r'^the url should (?:be|equal) ["\'](.+?)["\']$',
                                                   'assert_url',     lambda m: {'fragment': _q(m.group(1)), 'mode': 'exact'}),

    # Title assertion
    (r'^the page title should (?:contain|include) ["\'](.+?)["\']$',
                                                   'assert_title',    lambda m: {'fragment': _q(m.group(1))}),

    # Table / grid assertions (11.2) — MUST precede the semantic catch-all,
    # which would otherwise eat "... should have ..." / "... should be ...".
    (r'^the (?:grid|table) should have (\d+) rows?$',
                                                   'assert_row_count', lambda m: {'count': int(m.group(1))}),
    (r'^the cell in (?:the )?row ["\'](.+?)["\'] column ["\'](.+?)["\'] should (?:be|contain|equal|show) ["\'](.+?)["\']$',
                                                   'assert_cell',     lambda m: {'row': _q(m.group(1)), 'column': _q(m.group(2)), 'expected': _q(m.group(3))}),

    # --- NOOD_0011: grid/table assertions (Dynamics 365-style grids) ---------
    # Cell under a named header: "the cell under 'Status' in the row containing
    # 'Contoso' should be 'Active'". Same assert_cell underneath.
    (r'^the cell under (?:the )?["\'](.+?)["\'](?: header| column)? in (?:the )?row (?:containing|with) ["\'](.+?)["\'] should (?:be|contain|equal|show) ["\'](.*?)["\']$',
                                                   'assert_cell',     lambda m: {'column': _q(m.group(1)), 'row': _q(m.group(2)), 'expected': _q(m.group(3))}),
    # Row values — inline quoted list (order-free contains) …
    (r'^the row (?:containing|with) ["\'](.+?)["\'] should have (?:the )?values ((?:["\'][^"\']*["\'])(?:\s*(?:,|and)\s*["\'][^"\']*["\'])*)$',
                                                   'assert_row_values', lambda m: {'row': _q(m.group(1)), 'values': re.findall(r'["\']([^"\']*)["\']', m.group(2))}),
    # … or table-driven (| column | value | rows; column-aware exact check).
    (r'^the row (?:containing|with) ["\'](.+?)["\'] should have (?:the )?(?:these )?values:?$',
                                                   'assert_row_values', lambda m: {'row': _q(m.group(1)), 'values': None}),
    # Column headers — inline quoted list or table-driven (| column | rows).
    (r'^the (?:grid|table) should have (?:the )?columns? ((?:["\'][^"\']*["\'])(?:\s*(?:,|and)\s*["\'][^"\']*["\'])*)$',
                                                   'assert_table_headers', lambda m: {'names': re.findall(r'["\']([^"\']*)["\']', m.group(1))}),
    (r'^the (?:grid|table) should have (?:the )?(?:these )?columns:?$',
                                                   'assert_table_headers', lambda m: {'names': None}),
    # Column contents — one value inline, or table-driven (| value | rows).
    (r'^the ["\'](.+?)["\'] column should (?:contain|include|have) ["\'](.+?)["\']$',
                                                   'assert_column_contains', lambda m: {'column': _q(m.group(1)), 'values': [_q(m.group(2))]}),
    (r'^the ["\'](.+?)["\'] column should contain:?$',
                                                   'assert_column_contains', lambda m: {'column': _q(m.group(1)), 'values': None}),
    # NOOD_0143 — sort-order verdict on a column (audit gap): numeric when
    # every cell parses as a number (currency/commas tolerated), else
    # case-insensitive text. "sorted" alone means ascending.
    (r'^the ["\'](.+?)["\'] column should be sorted(?: (?:in )?(ascending|descending)(?: order)?)?$',
                                                   'assert_column_sorted', lambda m: {'column': _q(m.group(1)), 'descending': m.group(2) == 'descending'}),
    # Whole-row presence — Gherkin table whose headings are column names; the
    # first column identifies the row ("verify the grid contains these rows").
    (r'^the (?:grid|table) should (?:contain|include|have) (?:the )?rows?:?$',
                                                   'assert_table_rows', lambda m: {}),

    # Element-scoped assertions (11.1) — attribute / value / state. Also before
    # the semantic catch-all for the same reason.
    (r'^the (.+?) should have attribute ["\'](.+?)["\'] (?:equal to|=|of) ["\'](.+?)["\']$',
                                                   'assert_attribute', lambda m: {'locator': _q(m.group(1)), 'attribute': _q(m.group(2)), 'value': _q(m.group(3))}),
    # Value asserts — (.*?) so `should have value ""` (empty field) matches too
    # (NOOD_0008 low note).
    (r'^the ["\']?(.+?)["\']? (?:field|input|box) should (?:contain|have|show)(?: the)?(?: value| text)? ["\'](.*?)["\']$',
                                                   'assert_value',    lambda m: {'locator': _q(m.group(1)), 'value': _q(m.group(2))}),
    (r'^the ["\']?(.+?)["\']? should have value ["\'](.*?)["\']$',
                                                   'assert_value',    lambda m: {'locator': _q(m.group(1)), 'value': _q(m.group(2))}),
    # NOOD_0152 — regex / format assertion. Price, ID, date and currency
    # formats can't be expressed as an exact-string compare, and there was no
    # regex-shaped pattern anywhere in the table.
    (r'^(?:the )?["\'](.+?)["\'] should match (?:the )?(?:pattern|regex|format) ["\'](.+?)["\']$',
                                                   'assert_matches',  lambda m: {'locator': _q(m.group(1)), 'pattern': _q(m.group(2))}),
    # Optional symbol, then EITHER comma-grouped thousands OR plain digits
    # (an unformatted '1234.56' is still a price), then optional 2 decimals.
    (r'^(?:the )?["\'](.+?)["\'] should be formatted as (?:a )?currency$',
                                                   'assert_matches',  lambda m: {'locator': _q(m.group(1)),
                                                                                 'pattern': r'^\s*[^\d\s]{0,3}\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?\s*$'}),
    # NOOD_0152 — the post-reset / post-clear check every form suite writes.
    # Expressible before only as `should have value ""`, which nobody types.
    (r'^the ["\']?(.+?)["\']?(?: (?:field|input|box|text ?box|text ?area))? should be (?:empty|blank|cleared)$',
                                                   'assert_value',    lambda m: {'locator': _q(m.group(1)), 'value': ''}),
    (r'^the ["\']?(.+?)["\']?(?: (?:field|input|box|text ?box|text ?area))? should not be (?:empty|blank)$',
                                                   'assert_value_not', lambda m: {'locator': _q(m.group(1)), 'value': ''}),
    # Negated mirror of assert_value (NOOD_0021) — scoped to one element, unlike
    # the page-wide "should not see", for asserting a specific field/cell never
    # shows a value (e.g. a leftover "undefined"/"null" from an unguarded assign).
    # Locator MUST be quoted — keeps this from swallowing the unquoted OCR
    # "the screen/terminal should not show '...'" patterns further below.
    (r'^the ["\'](.+?)["\'] ?(?:field|input|box)? ?should not (?:contain|have|show)(?: the)?(?: value| text)? ["\'](.*?)["\']$',
                                                   'assert_value_not', lambda m: {'locator': _q(m.group(1)), 'value': _q(m.group(2))}),
    (r'^the ["\']?(.+?)["\']?(?: (?:button|field|input|box|link|checkbox|element|icon|dropdown|menu))? should be (enabled|disabled|checked|unchecked|selected|editable|read-?only)$',
                                                   'assert_state',    lambda m: {'locator': _q(m.group(1)), 'state': m.group(2)}),
    # NOOD_0062 — "is enabled" (no modal verb) + clickable/greyed-out synonyms.
    (r'^the ["\']?(.+?)["\']?(?: (?:button|field|input|box|link|checkbox|element|icon|dropdown|menu))? is (enabled|disabled|checked|unchecked|selected|editable|read-?only)$',
                                                   'assert_state',    lambda m: {'locator': _q(m.group(1)), 'state': m.group(2)}),
    (r'^the ["\']?(.+?)["\']?(?: (?:button|field|input|box|link|checkbox|element|icon|dropdown|menu))? (?:is|should be|must be) (?:clickable|tappable|interactable|active)$',
                                                   'assert_state',    lambda m: {'locator': _q(m.group(1)), 'state': 'enabled'}),
    (r'^the ["\']?(.+?)["\']?(?: (?:button|field|input|box|link|checkbox|element|icon|dropdown|menu))? (?:is|should be|must be) (?:not clickable|unclickable|inactive|greyed[- ]?out|grayed[- ]?out)$',
                                                   'assert_state',    lambda m: {'locator': _q(m.group(1)), 'state': 'disabled'}),
    # NOOD_0143 — focus assert (audit gap): pairs with "presses Tab" for
    # keyboard/tab-order tests; also the landing-focus check on modals.
    (r'^the ["\']?(.+?)["\']?(?: (?:button|field|input|box|link|checkbox|element|icon|dropdown|menu))? (?:is|should be|should have|has) focus(?:ed)?$',
                                                   'assert_focused',  lambda m: {'locator': _q(m.group(1))}),
    # NOOD_0143 — computed-style assert (audit gap): theme/visual-state checks
    # ("the error banner should have css 'color' of 'rgb(220, 38, 38)'")
    # without a pixel baseline.
    (r'^the ["\']?(.+?)["\']?(?: (?:button|field|input|box|link|checkbox|element|icon|dropdown|menu|banner|label|heading))? should have (?:the )?(?:css|style) ["\'](.+?)["\'] (?:of|=|equal to|as|with value) ["\'](.+?)["\']$',
                                                   'assert_css',      lambda m: {'locator': _q(m.group(1)), 'prop': _q(m.group(2)), 'value': _q(m.group(3))}),

    # NOOD_0216 — the REST step family moved to its own wok table:
    # resolver/api_patterns.py (consulted right after this one everywhere,
    # first inside @api — wok.pattern_priority).

    # --- Phase M (F8) — console & network error visibility --------------------
    (r'^no console errors? should (?:be logged|occur|appear)$',
                                                   'assert_no_console_errors', lambda m: {}),
    (r'^no (?:uncaught )?(?:js|javascript|page) errors? should occur$',
                                                   'assert_no_page_errors',    lambda m: {}),
    (r'^no network requests? should fail$',        'assert_no_failed_requests', lambda m: {}),
    # NOOD_0187 — HTTP 4xx/5xx on the page's own traffic. Distinct from the
    # transport-level check above: a 500 completes "fine" as a request, so
    # "no network requests should fail" passed a page full of server errors.
    (r'^no (?:server|http) errors? should (?:have )?occur(?:red)?$',
                                                   'assert_no_server_errors', lambda m: {}),

    # --- Phase L — network request assertion (observed real traffic) ----------
    (r'^a request to ["\'](.+?)["\'] should (?:have been|be) made$',
                                                   'assert_request_made', lambda m: {'url': _q(m.group(1))}),
    # NOOD_0152 — page-weight budget. The request log was already captured for
    # the assertion above; nothing exposed its SIZE, so the cheapest perf gate
    # in the codebase was unreachable.
    (r'^(?:the )?page should make (?:fewer|less) than (\d+) requests?$',
                                                   'assert_request_count', lambda m: {'count': int(m.group(1)), 'op': '<'}),
    (r'^(?:the )?page should make at most (\d+) requests?$',
                                                   'assert_request_count', lambda m: {'count': int(m.group(1)), 'op': '<='}),
    # --- Phase L — soft assertions: explicit end-of-scenario check ------------
    (r'^all soft assertions should (?:pass|have passed)$',
                                                   'soft_assert_check',   lambda m: {}),

    # --- Phase N (F9) — runtime geolocation & permissions ----------------------
    (r'^sets? (?:the )?geolocation to ["\'](.+?)["\']$',
                                                   'set_geolocation',  lambda m: {'coords': _q(m.group(1))}),
    (r'^grants? (?:the )?permissions? ["\'](.+?)["\']$',
                                                   'grant_permissions', lambda m: {'permissions': _q(m.group(1))}),
    # NOOD_0122 — "accept/allow the location prompt" grants the named permission
    # for the current origin (the opposite of the close/dismiss pattern below).
    (r'^(?:accepts?|allows?|approves?|grants?) (?:the )?(location|geolocation|notifications?|camera|microphone)(?: permission)? (?:prompt|pop-?up|notification|bubble|request)$',
                                                   'grant_permissions', lambda m: {'permissions': m.group(1).lower()}),
    # "closes the location prompt" — the browser-chrome permission bubble
    # ("www.example.com wants to know your location"). Closing = denying
    # the pending request; it lives outside the DOM so close_popups can't.
    (r'^(?:closes?|dismiss(?:es)?) (?:the )?(location|geolocation|notifications?|camera|microphone)(?: permission)? (?:prompt|pop-?up|notification|bubble|request)$',
                                                   'dismiss_permission_prompt', lambda m: {'permission': m.group(1).lower()}),

    # --- Phase O (F10) — offline mode & network throttling ---------------------
    (r'^goes? offline$',                           'set_offline',      lambda m: {'offline': True}),
    (r'^goes? (?:back )?online$',                  'set_offline',      lambda m: {'offline': False}),
    (r'^throttles? the network to ["\'](.+?)["\']$',
                                                   'throttle_network', lambda m: {'profile': _q(m.group(1))}),

    # --- NOOD_0183 — clock control + pre-boot script injection ------------------
    # These sit ABOVE the assertion families because "advances the clock by 3
    # days" would otherwise be eaten by the generic number/compare patterns.
    (r'^(?:sets?|pins?) the (?:page |browser |system )?(?:clock|date|time) to ["\'](.+?)["\']$',
                                                   'set_clock',        lambda m: {'when': _q(m.group(1))}),
    (r'^freezes? the (?:page |browser |system )?(?:clock|date|time)(?: at ["\'](.+?)["\'])?$',
                                                   'freeze_clock',     lambda m: {'when': _q(m.group(1)) if m.group(1) else None}),
    (r'^advances? the (?:page |browser |system )?(?:clock|date|time) by (\d+(?:\.\d+)?) (second|minute|hour|day|week|month|year)s?$',
                                                   'advance_clock',    lambda m: {'amount': float(m.group(1)), 'unit': m.group(2)}),
    (r'^runs? the script ["\'](.+?)["\'] (?:on|before) (?:every|each) page load$',
                                                   'add_init_script',  lambda m: {'script': _q(m.group(1))}),

    # --- Phase P (F11) — accessibility auditing (axe-core) ---------------------
    # MUST precede the count assertions ("should see at most N ...") and the
    # semantic catch-all ("the X should have ..."), both of which would
    # otherwise swallow these phrases.
    # NOOD_0152 — widened phrasing only (shape unchanged: assert_a11y's
    # max_violations already defaults to 0, so "no violations" needs no `max`).
    (r'^(?:the (?:page|screen) should have|should see) (?:no|zero) (minor|moderate|serious|critical) accessibility violations?$',
                                                   'assert_a11y',      lambda m: {'impact': m.group(1).lower()}),
    (r'^(?:the (?:page|screen) should have|should see) (?:no|zero) accessibility violations?$',
                                                   'assert_a11y',      lambda m: {'impact': None}),
    (r'^(?:the (?:page|screen) )?(?:should be|is) accessible$',
                                                   'assert_a11y',      lambda m: {'impact': None}),

    # --- Phase Q (F12) — clipboard ---------------------------------------------
    (r'^copies ["\'](.+?)["\'] to the clipboard$', 'write_clipboard',  lambda m: {'text': _q(m.group(1))}),
    (r'^the clipboard should contain ["\'](.*?)["\']$',
                                                   'assert_clipboard', lambda m: {'text': _q(m.group(1))}),

    # --- Phase R (F13) — WebSocket observation ----------------------------------
    # NOOD_0188 — and DRIVING, not just watching: capture alone meant a
    # live-update UI (ticker, chat, order status) could never be tested,
    # because nothing could make the server "say" something. Routing attaches
    # only to a socket the page opens AFTER the mock, hence arm-first — same
    # contract as the JS dialog steps.
    (r'^mocks? the websocket(?: ["\'](.+?)["\'])?$',
                                                   'mock_websocket',   lambda m: {'url': _q(m.group(1)) if m.group(1) else None}),
    (r'^the (?:websocket |mocked )?server sends? ["\'](.+?)["\']$',
                                                   'send_ws_message',  lambda m: {'message': _q(m.group(1))}),
    (r'^(?:the )?(?:page|app|client) should have sent ["\'](.+?)["\'](?: (?:over|on) the websocket)?$',
                                                   'assert_ws_sent',   lambda m: {'contains': _q(m.group(1))}),
    (r'^a websocket message containing ["\'](.+?)["\'] should (?:be|have been) (sent|received)$',
                                                   'assert_ws_message', lambda m: {'contains': _q(m.group(1)), 'direction': m.group(2).lower()}),
    (r'^a websocket message containing ["\'](.+?)["\'] should (?:be|have been) (?:seen|observed)$',
                                                   'assert_ws_message', lambda m: {'contains': _q(m.group(1)), 'direction': None}),

    # --- Phase S (F14) — print media / PDF export -------------------------------
    (r'^emulates? (print|screen) media$',          'emulate_media',    lambda m: {'media': m.group(1).lower()}),
    (r'^saves? the page as (?:a )?pdf ["\'](.+?)["\']$',
                                                   'save_pdf',         lambda m: {'path': _q(m.group(1))}),

    # --- Phase J (F6) — multi-user / multi-context flows -------------------------
    (r'^a new browser context (?:as|named|called) ["\'](.+?)["\']$',
                                                   'new_context',      lambda m: {'name': _q(m.group(1))}),
    (r'^act(?:s|ing)? as ["\'](.+?)["\']$',        'use_context',      lambda m: {'name': _q(m.group(1))}),
    (r'^switches? to (?:the )?["\'](.+?)["\'] context$',
                                                   'use_context',      lambda m: {'name': _q(m.group(1))}),

    # --- Phase G4 — app lifecycle (desktop & REST shared primitive) -------------
    (r'^launches? the app ["\'](.+?)["\']$',       'app_launch',       lambda m: {'command': _q(m.group(1))}),
    (r'^the app should be running(?: on port (\d+))?$',
                                                   'app_assert_running', lambda m: {'port': int(m.group(1)) if m.group(1) else None}),
    (r'^stops? the app$',                          'app_stop',         lambda m: {}),

    # --- Phase F (F2) — mobile (Appium) gestures ---------------------------------
    (r'^swipes? (left|right|up|down)$',            'swipe',            lambda m: {'direction': m.group(1).lower()}),

    # --- NOOD_0032 — mobile keyboard & app lifecycle (Appium) --------------------
    (r'^(?:hides?|dismiss(?:es)?) the keyboard$',  'hide_keyboard',    lambda m: {}),
    (r'^sends? the app to the background(?: for (\d+) seconds?)?$',
                                                   'background_app',   lambda m: {'seconds': int(m.group(1) or 3)}),

    # Value comparison assertions (12.2) — both operands are already [VAR]-
    # substituted to literals by the time we get here. Order: longest operator
    # phrase first so "greater than or equal to" isn't eaten by "greater than".
    (r'^["\']?(.+?)["\']? should be (?:greater than or equal to|at least) ["\']?(.+?)["\']?$',
                                                   'assert_compare',  lambda m: {'left': _q(m.group(1)), 'op': '>=', 'right': _q(m.group(2))}),
    (r'^["\']?(.+?)["\']? should be (?:less than or equal to|at most) ["\']?(.+?)["\']?$',
                                                   'assert_compare',  lambda m: {'left': _q(m.group(1)), 'op': '<=', 'right': _q(m.group(2))}),
    (r'^["\']?(.+?)["\']? should be (?:greater than|more than) ["\']?(.+?)["\']?$',
                                                   'assert_compare',  lambda m: {'left': _q(m.group(1)), 'op': '>', 'right': _q(m.group(2))}),
    (r'^["\']?(.+?)["\']? should be (?:less than|fewer than) ["\']?(.+?)["\']?$',
                                                   'assert_compare',  lambda m: {'left': _q(m.group(1)), 'op': '<', 'right': _q(m.group(2))}),
    (r'^["\']?(.+?)["\']? should not (?:equal|be equal to) ["\']?(.+?)["\']?$',
                                                   'assert_compare',  lambda m: {'left': _q(m.group(1)), 'op': '!=', 'right': _q(m.group(2))}),
    (r'^["\']?(.+?)["\']? should (?:equal|be equal to) ["\']?(.+?)["\']?$',
                                                   'assert_compare',  lambda m: {'left': _q(m.group(1)), 'op': '==', 'right': _q(m.group(2))}),
    (r'^["\']?(.+?)["\']? should contain ["\']?(.+?)["\']?$',
                                                   'assert_compare',  lambda m: {'left': _q(m.group(1)), 'op': 'contains', 'right': _q(m.group(2))}),

    # OCR / terminal-buffer assertions (NOOD_0024) — deterministic, no LLM.
    # MUST precede the semantic catch-all, which would otherwise eat them.
    (r'^the (?:screen|terminal) should not (?:show|display) ["\'](.+?)["\']$',
                                                   'assert_screen_text_hidden', lambda m: {'text': _q(m.group(1))}),
    (r'^the (?:screen|terminal) (?:shows?|displays?) ["\'](.+?)["\']$',
                                                   'assert_screen_text', lambda m: {'text': _q(m.group(1))}),
    (r'^the terminal buffer (?:contains?|shows?|includes?) ["\'](.+?)["\']$',
                                                   'assert_buffer',   lambda m: {'text': _q(m.group(1))}),

    # NOOD_0114 — image-content assertions. Text baked into pixels (carousel
    # tiles, flyers, banners, logos): deterministic OCR, scoped to one
    # element's box. MUST precede the vision-LLM semantic catch-all below.
    (rf'^(?:the )?["\'](.+?)["\'] {_IMG} should not (?:show|display|contain|read) (?:the )?(?:text )?["\'](.+?)["\']$',
                                                   'assert_image_text_hidden', lambda m: {'locator': _q(m.group(1)), 'text': _q(m.group(2))}),
    # Object/scene check — vision LLM, nondeterministic (@potential-flake):
    # "the 'hero' image should depict 'a dog'". Deliberately a different verb
    # (depict / show a picture of) from the OCR text asserts, and ordered
    # before them so 'show a picture of' never parses as OCR text 'a picture'.
    (rf'^(?:the )?["\'](.+?)["\'] {_IMG} should (?:depict|show an? (?:image|picture|photo) of) ["\'](.+?)["\']$',
                                                   'assert_depicts', lambda m: {'locator': _q(m.group(1)), 'desc': _q(m.group(2))}),
    (r'^the screen should (?:depict|show an? (?:image|picture|photo) of) ["\'](.+?)["\']$',
                                                   'assert_depicts', lambda m: {'desc': _q(m.group(1))}),
    (rf'^(?:the )?["\'](.+?)["\'] {_IMG} (?:should )?(?:shows?|displays?|contains?|reads?) (?:the )?(?:text )?["\'](.+?)["\']$',
                                                   'assert_image_text', lambda m: {'locator': _q(m.group(1)), 'text': _q(m.group(2))}),

    # NOOD_0210 — the PAGE's own HTTP status. Distinct from the REST-wok
    # `the response status should be 200`, which asserts the last API call and
    # is meaningless after a plain navigation. "assert the UI page returns 200"
    # is a routine AC that previously had to be faked with a console/network
    # health step. Anchored on page/url/site so it can never shadow the REST
    # response assertions.
    (r'^(?:the )?(?:page|url|site|website)(?: status(?: code)?)? '
     r'(?:should |must )?(?:returns?|be|is|was)? ?(?:status |http )?(\d{3})$',
                                                   'assert_page_status', lambda m: {'status': int(m.group(1))}),
    (r'^(?:the )?(?:page|url|site|website) (?:should |must )?'
     r'(?:load|loads|loaded|return|returns|responds?)(?:ed)? '
     r'(?:successfully|ok|fine|without errors?)$',
                                                   'assert_page_status', lambda m: {}),

    # NOOD_0210 — motion assertions. Marketing pages animate logos and banner
    # strips, and until now a test could only say such an element was PRESENT:
    # a bouncing logo and a frozen one asserted identically. Deterministic —
    # samples the element's box over ~0.85s (agents/web/motion.py), no vision
    # model — so these sit here, ahead of the vision-LLM semantic catch-alls
    # below, which would otherwise swallow "should be moving" as a scene
    # description. The element name may be quoted or bare ("the pizza logo").
    # Direction FIRST so "moving left to right" never parses as bare motion,
    # and the negative before the bare positive so "is not moving" can't be
    # swallowed as a locator ending in "is not".
    (r'^(?:the )?["\']?(.+?)["\']? (?:can |could |should |will |'
     r'may )?(?:be |is |are |was |were )?(?:seen |shown |visible )?'
     r'(?:moving|bouncing|sliding|scrolling|drifting|animating|travelling|'
     r'traveling|panning|floating)(?: from)? '
     r'(left to right|right to left|top to bottom|bottom to top|up and down|'
     r'down and up|side to side|sideways|horizontally|vertically)$',
                                                   'assert_motion', lambda m: {'locator': _q(m.group(1)), 'direction': m.group(2)}),
    # The negative: a hero that should hold still, or an animation that must
    # stop once loaded. `prefers-reduced-motion` suites need exactly this.
    (r'^(?:the )?["\']?(.+?)["\']? (?:is |are |should be |'
     r'should remain |remains )?(?:not moving|not animated|not animating|'
     r'static|still|frozen|stationary|motionless)$',
                                                   'assert_no_motion', lambda m: {'locator': _q(m.group(1))}),
    # Bare "is moving / is animated" — any motion, no direction claimed.
    (r'^(?:the )?["\']?(.+?)["\']? (?:can |could |should |will )?'
     r'(?:be |is |are |was |were )?(?:seen |shown )?'
     r'(?:moving|bouncing|scrolling|sliding|animating|animated)$',
                                                   'assert_motion', lambda m: {'locator': _q(m.group(1)), 'direction': ''}),

    # Semantic (vision LLM) assertions
    (r'^the (.+?) should (?:show|display|have) (?:a )?(.+)$',
                                                   'assert_semantic', lambda m: {'assertion': f"{m.group(1)} shows {m.group(2)}"}),
    (r'^the (.+?) should look (.+)$',
                                                   'assert_semantic', lambda m: {'assertion': f"{m.group(1)} looks {m.group(2)}"}),

    # Deterministic pixel baseline (no LLM) — MUST precede the LLM visual_baseline
    # so "should match the baseline" routes to the pixel diff, not the model.
    # NOOD_0188 — the `ignoring X and Y` tail masks volatile regions (a clock,
    # an avatar, an ad slot) before the diff; without it any moving pixel made
    # a baseline permanently red, which is how visual tests get switched off.
    (r'^the screen should match (?:the )?(?:pixel )?baseline ignoring (?:the )?(.+)$',
                                                   'pixel_baseline', lambda m: {'name': 'default', 'ignore': _ignore_list(m.group(1))}),
    (r'^the ["\'](.+?)["\'] screen should match (?:the )?(?:pixel )?baseline ignoring (?:the )?(.+)$',
                                                   'pixel_baseline', lambda m: {'name': _q(m.group(1)), 'ignore': _ignore_list(m.group(2))}),
    (r'^the screen should match (?:the )?(?:pixel )?baseline$',
                                                   'pixel_baseline', lambda m: {'name': 'default'}),
    (r'^the ["\'](.+?)["\'] screen should match (?:the )?(?:pixel )?baseline$',
                                                   'pixel_baseline', lambda m: {'name': _q(m.group(1))}),

    # Visual baseline (semantic, LLM)
    (r'^the screen should look the same as before(?: ignoring (?:the )?(.+))?$',
                                                   'visual_baseline', lambda m: {'name': 'default', 'ignore': m.group(1)}),
    (r'^the ["\'](.+?)["\'] screen should look the same as before(?: ignoring (?:the )?(.+))?$',
                                                   'visual_baseline', lambda m: {'name': _q(m.group(1)), 'ignore': m.group(2)}),

    # Screenshot
    (r'^takes? a screenshot(?: ["\'](.+?)["\'])?$',
                                                   'screenshot',      lambda m: {'name': _q(m.group(1)) if m.group(1) else 'manual'}),
]


# NOOD_0026 — agent-suggested patterns, staged in docs/agent_patterns.yaml
# instead of spliced into PATTERNS above. PATTERNS is hand-curated and
# order-sensitive (a new regex placed below the wrong catch-all is silently
# shadowed — see docs/design-history.md's NOOD_0016 note), so automatically
# inserting a tuple into the "right" spot in this file is fragile. This tier
# is only ever consulted after every curated pattern has already failed, so
# a staged entry can never shadow one, and writing to it is pure data
# serialization (no code-gen/syntax risk).
#
# NOOD_0027 — this file is pure staging (no curated baseline to bundle), so
# it only ever resolves from a workspace: the one set via
# set_agent_patterns_dir() (real `noodle run`/step-search --workspace), or
# this repo's own docs/ as a dev-checkout fallback. Missing entirely (fresh
# workspace, or an installed wheel with no workspace set yet) -> empty list.
_REPO_AGENT_PATTERNS_PATH = Path(__file__).resolve().parents[2] / "docs" / "agent_patterns.yaml"
_workspace_patterns_dir: Path | None = None
_agent_patterns_cache: list[dict] | None = None


def set_agent_patterns_dir(path: Path | None) -> None:
    """Point the agent-patterns lookup at a workspace's own docs/ dir. Called
    from hooks.before_all (run time, cwd is already the workspace) and from
    the step-search CLI/agent (--workspace) so both agree on where a project's
    own accepted suggestions live."""
    global _workspace_patterns_dir
    _workspace_patterns_dir = Path(path) if path is not None else None
    clear_agent_patterns_cache()


def _agent_patterns_path() -> Path:
    if _workspace_patterns_dir is not None:
        return _workspace_patterns_dir / "agent_patterns.yaml"
    return _REPO_AGENT_PATTERNS_PATH


def _agent_patterns() -> list[dict]:
    global _agent_patterns_cache
    if _agent_patterns_cache is not None:
        return _agent_patterns_cache
    entries: list[dict] = []
    path = _agent_patterns_path()
    if yaml is not None and path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if isinstance(raw, list):
            entries = raw
    _agent_patterns_cache = entries
    return entries


def clear_agent_patterns_cache() -> None:
    """So a step just accepted via step_suggestion_engine.accept_suggestion()
    resolves immediately in the same process — no restart needed."""
    global _agent_patterns_cache
    _agent_patterns_cache = None


def _extract_declarative(m: "re.Match", param_spec: list[dict]) -> dict:
    """Declarative param vocabulary — no arbitrary Python lambdas, so an
    agent-drafted suggestion can be pure YAML data:
      source: literal, value: <any>            -> a fixed value, no capture
      source: group, group: <int>[, quoted: true][, cast: int]
    """
    params = {}
    for p in param_spec:
        if p.get("source") == "literal":
            params[p["name"]] = p.get("value")
            continue
        val = m.group(p["group"])
        if val is not None and p.get("quoted"):
            val = _q(val)
        if val is not None and p.get("cast") == "int":
            val = int(val)
        params[p["name"]] = val
    return params


# ---------------------------------------------------------------------------
# NOOD_0141 — deterministic typo tolerance for the LEADING VERB. Weak models
# and humans both write "clciks the login button"; without this the step falls
# to the LLM fallback (cost) or fails (red run) over one transposed letter.
# Tried ONLY after every pattern (curated + agent) has missed, so it can never
# reroute a step that already resolves. Rules keep it safe: the verb must be
# ≥4 chars, unknown, and within ONE edit (substitution, adjacent
# transposition, insert, delete) of exactly ONE known verb — ambiguity means
# no correction. Pure, zero model calls.
# ---------------------------------------------------------------------------

def _within_one_edit(a: str, b: str) -> bool:
    """Levenshtein distance ≤ 1, plus adjacent transposition ('clciks' →
    'clicks') as a single edit. Pure — unit-testable."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = 0
    skipped = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


_KNOWN_VERBS: frozenset | None = None


def _known_verbs() -> frozenset:
    global _KNOWN_VERBS
    if _KNOWN_VERBS is None:
        _KNOWN_VERBS = frozenset(_FIRST_TO_THIRD) | frozenset(_FIRST_TO_THIRD.values())
    return _KNOWN_VERBS


def _fuzzy_verb_fix(step_text: str) -> str | None:
    """The step with its leading verb typo-corrected (normalized to 3rd
    person), or None when the verb is fine / too short / ambiguous."""
    parts = step_text.split(maxsplit=1)
    if not parts or len(parts[0]) < 4:
        return None
    first = parts[0].lower()
    verbs = _known_verbs()
    if first in verbs:
        return None                       # verb fine — the miss is elsewhere
    hits = {v for v in verbs if _within_one_edit(first, v)}
    # collapse 1st/3rd person pairs ('click'/'clicks') to one canonical verb
    canon = {_FIRST_TO_THIRD.get(v, v) for v in hits}
    if len(canon) != 1:
        return None                       # no match, or ambiguous — don't guess
    fixed = canon.pop()
    return fixed + (" " + parts[1] if len(parts) > 1 else "")


def match(step_text: str):
    """Return (action_type, params) or None."""
    for pattern, action_type, extractor in PATTERNS:
        m = re.match(pattern, step_text, re.IGNORECASE)
        if m:
            return action_type, extractor(m)
    for entry in _agent_patterns():
        m = re.match(entry["phrase"], step_text, re.IGNORECASE)
        if m:
            return entry["action_type"], _extract_declarative(m, entry.get("params", []))
    # NOOD_0141 — last deterministic resort: one-edit leading-verb typo.
    fixed = _fuzzy_verb_fix(step_text)
    if fixed is not None:
        for pattern, action_type, extractor in PATTERNS:
            m = re.match(pattern, fixed, re.IGNORECASE)
            if m:
                return action_type, extractor(m)
    return None
