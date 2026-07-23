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
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

from noodle import payload_budget
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

# Same walk cap as dom_scan. ponytail: raise if a real page buries its
# controls below the cap.
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
            return c[key].strip().lower(), key
    if c.get("text") and len(c["text"]) <= 40:
        return c["text"].strip().lower(), "text"
    # NOOD_0115 — an image tile's caption often lives ONLY in its alt text
    if c.get("alt"):
        return c["alt"].strip().lower(), "alt"
    if c.get("title"):
        return c["title"].strip().lower(), "title"
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


def _reveal(page, pg: dict, clicks: list[str], timeout_ms: int) -> None:
    """NOOD_0116 — click each named target in order and append what it
    reveals (controls/headings not present before the click) to
    pg["revealed"], each a summarize()-shaped dict. Targets execute for
    REAL — reveal controls only. Advisory like the rest of probe: a target
    that can't be clicked lands in pg["click_warnings"], the initial
    snapshot stays intact, nothing raises."""
    known = list(pg["controls"])
    seen = {c["selector"] for c in known}
    seen_head = set(pg["headings"])
    for target in clicks:
        try:
            sel = _click_selector(known, target)
            ctrl = next((c for c in known if c["selector"] == sel), None)
            loc = page.locator(sel).first
            armed, u = _arm(page), page.url
            # NOOD_0135 — a control the probe already saw as hidden (0-size
            # trigger zone) has no click box: dispatch straight away instead
            # of burning the 3 s actionability wait discovering that.
            if ctrl is not None and ctrl.get("visible") is False:
                loc.dispatch_event("click")
            else:
                try:
                    loc.click(timeout=3000)
                except Exception:
                    # hidden hitboxes (0-size trigger zones) have no click box
                    loc.dispatch_event("click")
            settled = _settle(page, timeout_ms, armed=armed, url_before=u)
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
            pg.setdefault("revealed", []).append(rev)
        except Exception as e:
            pg.setdefault("click_warnings", []).append(
                f'--click "{target}": {e}')


# NOOD_0144 — the stateful-transaction grammar. Three verbs cover a
# fill → select → save flow; <value> is non-greedy, so "enter a in b in c"
# reads value=a, field="b in c".
_DO_RE = re.compile(
    r"^\s*(?:click\s+(?P<btn>.+?)"
    r"|enter\s+(?P<val>.+?)\s+in\s+(?P<field>.+?)"
    r"|select\s+(?P<opt>.+?)\s+from\s+(?P<dd>.+?))\s*$", re.I)


def parse_do(actions: list[str]) -> list[tuple[str, str, str | None]]:
    """Parse --do items into (verb, target, value) triples. Raises ValueError
    naming the bad item — callers check BEFORE any browser launches."""
    out = []
    for a in actions:
        m = _DO_RE.match(a or "")
        if not m:
            raise ValueError(
                f'bad do action {a!r} — use "click <name>", '
                '"enter <value> in <field>" or "select <option> from <dropdown>"')
        g = m.groupdict()
        if g["btn"] is not None:
            out.append(("click", g["btn"].strip(), None))
        elif g["val"] is not None:
            out.append(("enter", g["field"].strip(), g["val"].strip()))
        else:
            out.append(("select", g["dd"].strip(), g["opt"].strip()))
    return out


def _do(page, pg: dict, actions: list[tuple], timeout_ms: int) -> None:
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
    dropdown fallback), so probe and run time agree on what is selectable."""
    # NOOD_0168 — newest snapshot first: the transaction acts on the page the
    # flow is ON now, so a landed-page control outranks a same-named twin from
    # the start page; what an action just revealed outranks both (prepend).
    known = [c for b in reversed(_blocks(pg)) for c in b["controls"]]
    seen = {c["selector"] for c in known}
    seen_head = {h for b in _blocks(pg) for h in b["headings"]}
    pg["do_completed"] = 0
    for i, (verb, target, value) in enumerate(actions):
        label = f"do: {verb} {target}"
        sel = None
        try:
            sel = _click_selector(known, target)
            ctrl = next((c for c in known if c["selector"] == sel), None)
            loc = page.locator(sel).first
            armed, u = _arm(page), page.url
            if verb == "enter":
                loc.fill(value)
            elif verb == "select":
                # NOOD_0145 — the SAME select implementation the runtime step
                # uses (actions.select_on): native select_option plus the
                # open-and-click-options fallback for custom comboboxes. The
                # probe previously supported native <select> only, so a
                # transaction against a custom dropdown failed where the
                # authored test would have passed.
                from noodle.agents.web.actions import select_on
                select_on(page, loc, value)
            elif ctrl is not None and ctrl.get("visible") is False:
                loc.dispatch_event("click")
            else:
                try:
                    loc.click(timeout=3000)
                except Exception:
                    loc.dispatch_event("click")
            settled = _settle(page, timeout_ms, armed=armed, url_before=u,
                              mutating=(verb == "click" and (
                                  _is_mutating_control(ctrl) if ctrl
                                  else _is_mutating(target))))
            raw = page.evaluate(_COLLECT_JS)
            raw["controls"] = [c for c in raw["controls"]
                               if _selector(c) not in seen]
            raw["headings"] = [h for h in raw.get("headings", [])
                               if h not in seen_head]
            rev = summarize(raw, url=page.url, title=page.title())
            rev["revealed_by"], rev["settled"] = label, settled
            if rev["controls"] or rev["headings"] or settled == "navigation":
                _verify_unique(page, rev["controls"])
                known = rev["controls"] + known
                seen |= {c["selector"] for c in rev["controls"]}
                seen_head |= set(rev["headings"])
                pg.setdefault("revealed", []).append(rev)
            elif verb == "click":
                # NOOD_0156 follow-up — a CLICK that changed nothing must
                # still leave a record: silence made "the click did nothing"
                # and "the click worked, UI rendered late" indistinguishable,
                # and the reviewed session burned 4 probes telling them
                # apart. Fills/selects stay silent — a no-delta fill is the
                # normal case, not a signal.
                rev["note"] = ("no new controls or headings appeared within "
                               "the settle window — the click landed but "
                               "produced no observable delta")
                pg.setdefault("revealed", []).append(rev)
            pg["do_completed"] = i + 1
        except Exception as e:
            pg.setdefault("do_warnings", []).append(f"{label}: {e}")
            pg["do_failed"] = {
                "index": i, "action": label, "selector": sel or target,
                "error": str(e),
                "skipped": [f"do: {v} {t}" for v, t, _ in actions[i + 1:]],
            }
            return


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
                loc.dispatch_event("click")
            else:
                try:
                    loc.click(timeout=3000)
                except Exception:
                    loc.dispatch_event("click")
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
          mutate: str | None = None) -> None:
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
        return
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
        return
    from noodle.repl.goal import bind_result  # pure; lazy to avoid a cycle
    cand, why = bind_result(sr.get("controls") or [], term, target,
                            items=sr.get("result_items"))
    if cand is None:
        sr["pick_warning"] = why
        return
    try:
        loc = page.locator(cand["selector"]).first
        armed, u = _arm(page), page.url
        loc.click(timeout=5000)
        _settle(page, timeout_ms, armed=armed, url_before=u)
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
        if not rows:                      # slow async render — bounded retry
            page.wait_for_timeout(1500)
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
    name. Advisory — a failure lands in pg["expect_warning"], never raises."""
    try:
        pg["expect"] = page.evaluate(_EXPECT_JS, texts)
    except Exception as e:
        pg["expect_warning"] = f"--expect: {e}"


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


def _verify_unique(target, controls: list[dict]) -> None:
    """Mark each control unique True/False (+match count) via the SAME
    resolution surface find() uses (Playwright locator — pierces open shadow
    roots). target is the page or the frame the control lives in. Bounded by
    _UNIQUE_CAP; an unverifiable selector is left unmarked, never guessed."""
    for c in controls[:_UNIQUE_CAP]:
        try:
            n = target.locator(c["selector"]).count()
        except Exception:
            continue
        if isinstance(n, int) and n:
            c["unique"] = n == 1
            if n > 1:
                c["matches"] = n


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
            ph.dispatch_event("click")
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
                loc.dispatch_event("click")
            else:
                try:
                    loc.click(timeout=2000)
                except Exception:
                    loc.dispatch_event("click")
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


def _skeleton_lines(pg: dict, indent: str = "  ") -> list[str]:
    out = [f"{indent}scenario skeleton (paste, keep the steps the goal needs, "
           "add assertions from the exact texts above; <APP> = author_test's "
           "base_url_key):"]
    out += [f"{indent}  {s}" for s in _skeleton_steps(pg)]
    return out


@outside_asyncio
def probe(urls: list[str], timeout_ms: int = 15000,
          clicks: list[str] | None = None,
          do: list[str] | None = None,
          search: str | None = None, suggest: str | None = None,
          pick: str | None = None, mutate: str | None = None,
          follow: str | None = None, expect: list[str] | None = None,
          open_native_controls: bool = False,
          max_reveal_depth: int = 1, discover: bool = False,
          act_on: str = "each") -> dict:
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
    try:
        do_actions = parse_do(do) if do else None
    except ValueError as e:
        return {"pages": [], "errors": [{"url": ", ".join(urls),
                                         "error": str(e)}]}
    try:
        from playwright.sync_api import sync_playwright

        from noodle import counters
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            counters.bump("browser_launch")
            try:
                page = browser.new_page()
                try:                       # NOOD_0137 — permission-API shim
                    page.add_init_script(_PERM_JS)
                except Exception:
                    pass
                for url_i, url in enumerate(urls):
                    # NOOD_0156 — act_on="last": earlier URLs of an ordered
                    # navigation contract are setup-only (cookies/session),
                    # never searched or clicked around in.
                    acting = act_on != "last" or url_i == len(urls) - 1
                    try:
                        resp = page.goto(url, timeout=timeout_ms,
                                         wait_until="domcontentloaded")
                        _settle(page, timeout_ms)
                        # NOOD_0137 — sweep popups exactly like the run-time
                        # engine, so the snapshot below is the real page and
                        # the observation feeds the signals + skeleton.
                        popups_closed = _dismiss_popups(page)
                        if popups_closed:
                            _settle(page, min(timeout_ms, 3000))
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
                        if clicks and acting:
                            _reveal(page, pg, clicks, timeout_ms)
                        if do_actions and acting and not search:
                            _do(page, pg, do_actions, timeout_ms)
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
                                _pick(page, pg, search, pick, timeout_ms,
                                      mutate=mutate)
                            if do_actions:
                                # NOOD_0168 — a do-transaction sharing the
                                # call with a search targets the page the
                                # search/pick LANDED on, not the start page
                                # (the reviewed session's "click Add to
                                # cart" fired on the homepage instead).
                                _do(page, pg, do_actions, timeout_ms)
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
                        pages.append(pg)
                    except Exception as e:
                        errors.append({"url": url, "error": str(e)})
            finally:
                browser.close()
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


def _step_lines(controls: list[dict], indent: str = "  ",
                cap: int | None = None) -> list[str]:
    """NOOD_0131 — compact mode: copy-ready steps for the controls that need
    NO POM entry (visible, readable name). The needs-POM list filters those
    out, and hiding their steps made the baseline re-probe `steps`/`revealed`
    per control. Bounded by the same cap as the control lists."""
    ready = _rank_ready([c for c in controls
                         if not _compact_keep(c) and not _tile_caption(c)
                         and not _is_consent_noise(c)])
    shown, hidden = _cap(ready, cap)
    if not shown:
        return []
    out = [f"{indent}copy-ready steps (no POM entry needed — use as-is, do "
           "not re-derive them via dictionary searches):"]
    for c in shown:
        out.append(f'{indent}  {c["step"]}')
        if c.get("options"):
            out.append(f'{indent}    options: '
                       + ", ".join(f'"{o}"' for o in c["options"]))
    if hidden:
        out.append(f"{indent}  … (+{hidden} more — raise --max-controls)")
    return out


def _control_lines(controls: list[dict], indent: str = "  ") -> list[str]:
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
        out.append(f'{indent}{mark} [{c["kind"]}] {c["name"]}{sub} — '
                   f'{c["selector"]}{hidden}  →  {c["step"]}{warn}')
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


def render(result: dict, compact: bool = False, section: str = "all",
           max_controls: int | None = None) -> str:
    """Human/agent-readable text for the CLI.

    NOOD_0117 knobs, all token-savers for agent callers:
      compact       — only the controls that need a POM entry (or are
                      attribute-caption-only), no next-pages; POM YAML,
                      headings and search/reveal blocks stay.
      section       — controls|pom|steps|headings|all: emit exactly one slice
                      instead of the whole dump (grep-in-context killer).
      max_controls  — cap each control list, noting how many were hidden.
    """
    if section != "all":
        return _render_section(result, section, max_controls)
    out = []
    # W1 — compact mode caps each list by default; explicit --max-controls wins,
    # full (non-compact) render stays uncapped (it is opt-in verbose).
    cap = max_controls if max_controls is not None else (
        DEFAULT_COMPACT_CAP if compact else None)
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
        controls = (_compact_controls(pg["controls"])
                    if compact else pg["controls"])
        shown, hidden = _cap(controls, page_cap)
        label = ("needing a POM entry, of "
                 f"{len(pg['controls'])} total — --section controls for all"
                 if compact else "* = needs POM entry")
        out.append(f"  controls ({len(controls)}; {label}):")
        out += _control_lines(shown)
        if hidden:
            out.append(f"    … (+{hidden} more — raise --max-controls)")
        if compact and not diet:
            out += _tile_lines(pg["controls"], cap=page_cap)
            out += _step_lines(pg["controls"], cap=page_cap)
        elif diet:
            out.append("    initial-page tiles/steps dieted (task flags "
                       "active) — pass --max-controls or re-probe without "
                       "--suggest/--search/--expect for the full inventory")
        out += _section_lines(pg, compact=compact, cap=page_cap)
        # NOOD_0116 — controls only visible AFTER a --click, labelled apart so
        # an agent doesn't author against them as if visible on load
        for rev in pg.get("revealed", []):
            diet = compact and rev.get("discovered")
            rev_cap = (DISCOVER_COMPACT_CAP
                       if diet and max_controls is None else cap)
            rev_controls = (_compact_controls(rev["controls"])
                            if compact else rev["controls"])
            rev_shown, rev_hidden = _cap(rev_controls, rev_cap)
            how = "discovered by clicking" if rev.get("discovered") \
                else "revealed after clicking"
            out.append(f'  {how} "{rev["revealed_by"]}" '
                       f'({len(rev["controls"])} new controls; * = needs POM entry):')
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
    return hits


def render_find(result: dict, needle: str) -> str:
    """The find_controls hits as paste-ready text — selector + POM line each,
    nothing else. Empty result says so instead of printing a blank page."""
    hits = find_controls(result, needle)
    if not hits:
        return (f'--find "{needle}": no matching control, result item, or '
                "card action in this probe — loosen the text or re-probe "
                "with --search/--click to reach the state that renders it")
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
        out.append(f"        css: '{h.get('selector', '')}'")
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
            controls = [c for blk in _blocks(pg) for c in blk["controls"]]
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


def _compact_page(pg: dict, max_controls: int) -> dict:
    """One page of compact_payload()."""
    need, hidden = _cap(_compact_controls(pg["controls"]), max_controls)
    steps, steps_hidden = _cap(
        [c["step"] for c in _rank_ready(pg["controls"])
         if not _is_consent_noise(c)],
        max_controls)
    headings = pg["headings"]
    if pg.get("term"):        # search block — Fix B: no result-echo headings
        headings = [h for h in headings if not _is_search_echo(h, pg["term"])]
    out = {"url": pg["url"], "title": pg["title"],
           "total_controls": len(pg["controls"]),
           "needs_pom": need, "suggested_steps": steps,
           "headings": headings, "pom_yaml": _compact_pom(pg, max_controls)}
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
                # NOOD_0156 follow-up — the no-delta note on a do-reveal
                "note",
                "search_warning", "click_warnings",
                # NOOD_0136 — scope/honesty contract keys
                "warnings", "coverage", "framework_hints", "discovered",
                "settled", "frame", "switch_step", "discovery",
                # NOOD_0137 — run-time signals: popup/permission observations
                "popups_closed", "permission_prompts"):
        if pg.get(key):
            out[key] = pg[key]
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
    if pg.get("revealed"):
        # NOOD_0137 Fix A — discovered blocks are signals, not catalogs; the
        # per-block `truncated` note points at compact=False for the full dump.
        out["revealed"] = [
            _compact_page(r, min(max_controls, DISCOVER_COMPACT_CAP)
                          if r.get("discovered") else max_controls)
            for r in pg["revealed"]]
    if pg.get("frames"):
        out["frames"] = [_compact_page(f, max_controls) for f in pg["frames"]]
    if pg.get("search"):
        out["search"] = _compact_page(pg["search"], max_controls)
    if pg.get("picked"):   # NOOD_0156 — inside a search block's recursion
        out["picked"] = _compact_page(pg["picked"], max_controls)
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


def compact_payload(result: dict, max_controls: int = 40) -> dict:
    """NOOD_0117 — the MCP-default probe payload: everything an author needs
    (needs-POM controls, paste-ready POM YAML, suggested steps, exact heading
    texts, search/reveal blocks) minus the full selector dump and next-pages
    list that made the raw payload a 24 KB resident blob.

    NOOD_0158 — and bounded as a WHOLE: the per-list cap steps down the ladder
    until the serialized payload fits COMPACT_BUDGET_BYTES, so a multi-page
    probe cannot blow a caller's context. Trimming is honest — the surviving
    payload carries `budget_trimmed` naming the cap it settled on."""
    def _build(cap: int) -> dict:
        return {"pages": [_compact_page(pg, cap)
                          for pg in result.get("pages", [])],
                "errors": result.get("errors", [])}

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
