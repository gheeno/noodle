"""Locator inspector (NOOD_0115) — "why does/would this phrase resolve to X?"

The retail SPA session's dominant cost was hand-written throwaway Playwright
scripts to answer exactly that question: which elements a phrase matches,
through which source (text node / alt / aria-label / POM key / DOM scan),
and which one find() actually picks. This runs the same resolution machinery
find() uses, headless, and reports every candidate — one command instead of
a bespoke script.

Like probe.py: the browser driver is thin; candidates() down is plain calls
against a page-shaped object — unit-testable with mocks, no browser.
No logger calls — stdout must stay clean for `noodle inspect --json`.
"""
import re

from noodle import healing

from . import dom_scan, pom


def _describe(loc, limit: int = 5) -> tuple[int, list[dict]]:
    """(total match count, up to `limit` short per-match descriptions)."""
    out = []
    try:
        n = loc.count()
    except Exception:
        return 0, out
    for i in range(min(n, limit)):
        h = loc.nth(i)
        try:
            out.append({
                "tag": h.evaluate("e => e.tagName.toLowerCase()"),
                "text": (h.inner_text() or "").strip().replace("\n", " ")[:60],
                "visible": h.is_visible(),
            })
        except Exception:
            out.append({"tag": "?", "text": "", "visible": False})
    return n, out


def candidates(page, text: str) -> list[dict]:
    """Every source find() consults, checked independently and labeled —
    including ones a bare get_by_text can never see (alt / aria-label /
    title / POM / DOM attribute scan)."""
    pattern = re.compile(re.escape(text), re.IGNORECASE)
    sources = [
        ("role=button accessible name", lambda: page.get_by_role("button", name=pattern)),
        ("role=link accessible name",   lambda: page.get_by_role("link", name=pattern)),
        ("label / aria-label",          lambda: page.get_by_label(pattern)),
        ("placeholder",                 lambda: page.get_by_placeholder(pattern)),
        ("role=textbox accessible name", lambda: page.get_by_role("textbox", name=pattern)),
        ("role=combobox accessible name", lambda: page.get_by_role("combobox", name=pattern)),
        ("role=checkbox accessible name", lambda: page.get_by_role("checkbox", name=pattern)),
        ("title attribute",             lambda: page.get_by_title(pattern)),
        ("image alt text",              lambda: page.get_by_alt_text(pattern)),
        ("visible text node",           lambda: page.get_by_text(pattern, exact=False)),
    ]
    out = []
    key = pom.is_explicit(text)
    ploc = pom.locate_all(page, key or text)
    if ploc is not None:
        n, matches = _describe(ploc)
        label = f"pom.yaml (explicit {{pom:{key}}})" if key else "pom.yaml"
        out.append({"source": label, "count": n, "matches": matches})
    for label, build in sources:
        try:
            loc = build()
            if loc.count() == 0:
                continue
        except Exception:
            continue
        n, matches = _describe(loc)
        out.append({"source": label, "count": n, "matches": matches})
    try:
        sel = dom_scan.best_selector(page, text)
        if sel:
            loc = page.locator(sel)
            if loc.count() > 0:
                n, matches = _describe(loc)
                out.append({"source": f"dom scan → {sel}", "count": n, "matches": matches})
    except Exception:
        pass
    return out


# NOOD_0167 — the zero-candidate dead end. "no source matches" left a
# reviewed session with nothing to act on while the page's tiles carried a
# differently-named control the whole time; listing what the page DOES call
# its controls turns the dead end into a redirect. Accessible names of
# interactive elements, top by count, capped for the payload budget.
_VOCAB_JS = """
() => {
  const names = {};
  for (const el of document.querySelectorAll(
      'button, a[href], input, select, textarea,'
      + ' [role="button"], [role="link"]')) {
    const n = (el.getAttribute('aria-label') || el.innerText || el.value
               || el.placeholder || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
    if (n) names[n] = (names[n] || 0) + 1;
  }
  return Object.entries(names).sort((a, b) => b[1] - a[1]).slice(0, 12);
}
"""


def page_vocabulary(page) -> list:
    """[[accessible name, count], …] of the page's interactive controls."""
    try:
        return [list(pair) for pair in page.evaluate(_VOCAB_JS)]
    except Exception:
        return []


def resolve_on(page, text: str) -> dict | None:
    """What find() actually picks on this page, plus any heal tier it used."""
    from . import locator
    before = len(healing.EVENTS)
    loc = locator.find(page, text, poll=False)   # one full-power pass, no 2-min poll
    healed = [f'{e["strategy"]}{" (" + e["detail"] + ")" if e["detail"] else ""}'
              for e in healing.EVENTS[before:]]
    if loc is None:
        return None
    if isinstance(loc, tuple):
        return {"source": "ocr-coordinate", "x": loc[1], "y": loc[2], "healed": healed}
    _, matches = _describe(loc.first if hasattr(loc, "first") else loc, limit=1)
    picked = matches[0] if matches else {"tag": "?", "text": "", "visible": False}
    picked["healed"] = healed
    return picked


def inspect(url: str, text: str, timeout_ms: int = 15000,
            screenshot_path: str | None = None) -> dict:
    """Open `url` headless and report how `text` resolves. Never raises —
    an unreachable page lands in "error", like probe()."""
    from .probe import outside_asyncio
    return outside_asyncio(_inspect_sync)(url, text, timeout_ms, screenshot_path)


def _inspect_sync(url: str, text: str, timeout_ms: int = 15000,
                  screenshot_path: str | None = None) -> dict:
    # NOOD_0141 (E7) — same asyncio-host guard as probe(): sync Playwright
    # cannot start on a running event loop (the FastMCP tool thread).
    result = {"url": url, "text": text, "candidates": [], "resolved": None,
              "screenshot": None, "error": None}
    try:
        from playwright.sync_api import sync_playwright

        from .probe import _settle
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                _settle(page, timeout_ms)
                result["candidates"] = candidates(page, text)
                result["resolved"] = resolve_on(page, text)
                if not result["candidates"]:
                    result["page_controls"] = page_vocabulary(page)
                if screenshot_path and result["resolved"] and "tag" in result["resolved"]:
                    try:
                        from . import locator
                        loc = locator.find(page, text, poll=False, heal=False)
                        loc.first.evaluate(
                            "el => { el.style.outline = '4px solid red';"
                            " el.style.outlineOffset = '2px'; }")
                        page.screenshot(path=screenshot_path, full_page=True)
                        result["screenshot"] = screenshot_path
                    except Exception:
                        pass
            finally:
                browser.close()
    except Exception as e:
        result["error"] = str(e)
    return result


def render(result: dict) -> str:
    """Human/agent-readable text for the CLI."""
    out = [f"Inspect: '{result['text']}' on {result['url']}"]
    if result.get("error"):
        out.append(f"⚠ inspect failed: {result['error']}")
        return "\n".join(out)
    if not result["candidates"]:
        out.append("  no source matches this phrase — nothing in POM, "
                   "accessible names, text nodes, or DOM attributes")
        vocab = result.get("page_controls") or []
        if vocab:
            out.append("  the page's interactive controls (top by count): "
                       + ", ".join(f"{n!r} ×{c}" for n, c in vocab))
    for c in result["candidates"]:
        out.append(f'  [{c["source"]}] {c["count"]} match(es):')
        for m in c["matches"]:
            vis = "visible" if m["visible"] else "HIDDEN"
            out.append(f'      <{m["tag"]}> {m["text"]!r} ({vis})')
    r = result.get("resolved")
    if r is None:
        out.append("  → find() resolves: NOTHING — a step using this phrase will fail")
    elif r.get("source") == "ocr-coordinate":
        out.append(f'  → find() resolves: OCR coordinate ({r["x"]:.0f}, {r["y"]:.0f})')
    else:
        vis = "visible" if r.get("visible") else "HIDDEN"
        out.append(f'  → find() resolves: <{r.get("tag")}> {r.get("text")!r} ({vis})')
    if r and r.get("healed"):
        out.append(f'    via self-heal: {", ".join(r["healed"])}')
        out.append('    ⚠ DIAGNOSTIC ONLY — do NOT author this phrase: it '
                   'resolved by self-heal (partial/fuzzy match), not a stable '
                   'contract. POM the element or use its exact name/selector.')
    if result.get("screenshot"):
        out.append(f'  screenshot (match outlined red): {result["screenshot"]}')
    return "\n".join(out)
