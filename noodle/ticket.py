"""NOOD_0201 — a ticket payload is the test plan; read it, don't guess at it.

An AI-SDLC workflow agent hands Noodle a raw JIRA issue: Atlassian Document
Format trees, spec-link boilerplate, a summary, a parent epic, and acceptance
criteria as loose Given/When/Then paragraphs. Two things went wrong when that
arrived as an "ambiguous prompt": the noise (SPEC_LINK markers, sha256 anchors)
was read as intent, and the ACs' own wording — "POST /greeting" — was taken as
the route, when the service actually served POST /greeting/new.

This module is the deterministic reader. No LLM, no network, nothing written:
  parse()          → key/summary/ACs/paths the ticket NAMES, noise stripped
  match_endpoint() → the ticket's wording resolved against the endpoints the
                     app REALLY serves (from api_probe), or an honest miss
  plan()           → one authorable goal per API-testable AC, plus the ACs that
                     are NOT testable through the API and why

`questions` stays load-bearing: what the ticket cannot answer (base URL,
credentials, an endpoint nobody serves) comes back as a question, never a
guess. The caller — an agent, or `noodle ticket` — settles those.

ponytail: paragraph-level parsing of ADF and AC prose, not an NLP pipeline.
The ceiling is an AC whose behaviour is only implied ("it should be fast");
that lands in `not_automatable` with its reason, which is the correct failure.
"""
from __future__ import annotations

import json
import re

# The spec-link boilerplate an SDLC pipeline staples into descriptions. It is
# provenance for humans, never test intent — reading it as intent is how a
# "generated sha256" line became a step.
_NOISE = re.compile(
    r"^\s*(?:<!--\s*SPEC_LINK:(?:START|END)\s*-->"
    r"|Spec Anchor:.*|Anchor Heading:.*|Generated Sha256:.*"
    r"|<!--.*-->)\s*$", re.I)
_AC_HEADER = re.compile(r"^\s*AC[-_ ]?(\d+)\s*[:.)]?\s*(.*)$", re.I)
_GWT = re.compile(r"^\s*(given|when|then|and|but)\b[:\s]*(.*)$", re.I)
# "POST /greeting", "GET /greetings with pagination", "POST /api/reviews/new"
_METHOD_PATH = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE)\b\s+(/[\w\-./{}]*)", re.I)
_BARE_PATH = re.compile(r"(?<![\w/])(/[a-z][\w\-]*(?:/[\w\-{}]+)*)", re.I)
_STATUS = re.compile(r"\b(?:status(?:\s+code)?\s*)?(\d{3})\b")
# "returns a JSON response containing id, name, date, and response"
_FIELDS = re.compile(
    r"\b(?:containing|contains|with|includes?|including|returns?)\b\s+"
    r"(?:a\s+)?(?:json\s+)?(?:response\s+)?(?:body\s+)?"
    r"(?:containing\s+)?([\w, ]+?(?:and\s+\w+)?)\s*(?:$|[.;])", re.I)
_NEGATIVE = re.compile(
    r"\b(reject(?:s|ed)?|invalid|missing|empty|malformed|error|fail(?:s|ure)?|"
    r"unauthori[sz]ed|forbidden|not\s+found|does\s+not\s+persist)\b", re.I)
_PAGINATION = re.compile(r"\bpaginat(?:ed|ion)|per\s+page|page\s+size\b", re.I)
# Prose that shows up inside a field list but names no field.
_NOT_FIELDS = frozenset({
    "a", "an", "the", "json", "response", "body", "status", "code", "it",
    "and", "with", "list", "of", "in", "one", "record", "table", "api",
    "client", "request", "value", "values", "containing", "returns", "return",
    "generated", "stored", "exactly", "greeting", "greetings"})
_CAP = 10


def _adf_text(node) -> list[str]:
    """Every paragraph's text from an Atlassian Document Format tree, in order.

    ADF nests text nodes inside paragraphs inside content arrays; a plain
    string description (older JIRA, or a REST view that flattens it) is
    returned as-is, so both shapes read the same downstream.
    """
    if isinstance(node, str):
        return [ln.strip() for ln in node.splitlines() if ln.strip()]
    if not isinstance(node, dict):
        return []

    def walk(n, out: list[str]):
        if not isinstance(n, dict):
            return
        if n.get("type") in ("paragraph", "heading", "listItem", "tableCell"):
            text = "".join(_leaf_text(c) for c in (n.get("content") or []))
            if text.strip():
                out.append(text.strip())
            # a listItem wraps paragraphs — recurse for anything deeper too
            for c in n.get("content") or []:
                if isinstance(c, dict) and c.get("type") not in ("text",):
                    walk(c, out)
            return
        for c in n.get("content") or []:
            walk(c, out)

    out: list[str] = []
    walk(node, out)
    return list(dict.fromkeys(out))          # ADF nesting can repeat a line


def _leaf_text(node) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text", ""))
    if node.get("type") == "hardBreak":
        return " "
    return "".join(_leaf_text(c) for c in (node.get("content") or []))


def _clean(lines: list[str]) -> list[str]:
    return [ln for ln in lines if not _NOISE.match(ln)]


def _acceptance_criteria(lines: list[str]) -> list[dict]:
    """Loose Given/When/Then paragraphs → one block per AC.

    JIRA's AC field is prose in practice: an "AC-2" line, then Given/When/Then
    on their own paragraphs. Blocks are split on the AC header; a ticket with
    no headers but Given/When/Then lines becomes one block, which is the other
    common shape.
    """
    blocks: list[dict] = []
    cur: dict | None = None

    def start(label: str, seed: str = ""):
        nonlocal cur
        cur = {"id": label, "given": [], "when": [], "then": [], "text": []}
        blocks.append(cur)
        if seed.strip():
            add(seed)

    def add(line: str):
        if cur is None:
            return
        cur["text"].append(line)
        m = _GWT.match(line)
        if m:
            word = m.group(1).lower()
            body = m.group(2).strip() or line
            if word in ("and", "but"):
                # continuation of whichever clause is open
                for key in ("then", "when", "given"):
                    if cur[key]:
                        cur[key].append(body)
                        return
                cur["given"].append(body)
            else:
                cur[word].append(body)

    for ln in lines:
        if m := _AC_HEADER.match(ln):
            start(f"AC-{m.group(1)}", m.group(2))
            continue
        if cur is None:
            if not _GWT.match(ln):
                continue                      # prose before the first AC
            start("AC-1")
        add(ln)
    return [b for b in blocks if b["given"] or b["when"] or b["then"]]


def _paths(text: str) -> list[dict]:
    """Endpoints the text NAMES: (method, path) pairs, then bare paths."""
    out: dict[tuple[str, str], None] = {}
    for m in _METHOD_PATH.finditer(text):
        out[(m.group(1).upper(), m.group(2).rstrip(".,;"))] = None
    for m in _BARE_PATH.finditer(text):
        path = m.group(1).rstrip(".,;")
        if not any(p == path for _, p in out):
            out[("", path)] = None
    return [{"method": k[0], "path": k[1]} for k in out][:_CAP]


def parse(payload) -> dict:
    """A JIRA issue payload → {ok, key, summary, description, acceptance_criteria,
    endpoints_mentioned, questions}. `payload` is a dict, or JSON text.

    Returns {"ok": False} for anything that isn't ticket-shaped, so a caller
    can try this first and fall through to normal prompt handling.
    """
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return {"ok": False, "error": "not JSON"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "not a JSON object"}
    # A JIRA issue (or the `fields` block on its own).
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) \
        else payload
    summary = fields.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return {"ok": False,
                "error": "no fields.summary — not a ticket payload"}

    desc = _clean(_adf_text(fields.get("description")))
    ac_lines = _clean(_adf_text(fields.get("acceptanceCriteria")))
    # Teams that keep ACs inside the description: fall back to it, so the
    # criteria are never silently empty.
    acs = _acceptance_criteria(ac_lines) or _acceptance_criteria(desc)
    all_text = " \n".join([summary, *desc, *ac_lines])
    status = (fields.get("status") or {}).get("name") \
        if isinstance(fields.get("status"), dict) else None
    issue_type = (fields.get("issuetype") or {}).get("name") \
        if isinstance(fields.get("issuetype"), dict) else None
    project = (fields.get("project") or {}).get("name") \
        if isinstance(fields.get("project"), dict) else None

    questions = []
    if not acs:
        questions.append(
            "this ticket carries no acceptance criteria Noodle could read — "
            "paste the criteria as Given/When/Then lines, or send the test as "
            "numbered steps")
    questions.append(
        "which base URL does the service answer on? (`noodle api-scan` finds a "
        "running localhost app; Noodle never guesses a URL)")
    return {
        "ok": True,
        "key": payload.get("key"),
        "summary": summary.strip(),
        "issue_type": issue_type,
        "status": status,
        "project": project,
        "description": desc[:_CAP],
        "acceptance_criteria": [
            {"id": b["id"], "given": b["given"], "when": b["when"],
             "then": b["then"]} for b in acs[:_CAP]],
        "endpoints_mentioned": _paths(all_text),
        "questions": questions,
    }


def _segments(path: str) -> list[str]:
    """Path segments, singular/plural folded.

    A ticket writes the resource the way a human says it ("POST /api/review");
    the service routes the collection ("/api/reviews/new"). Without the fold,
    those share only the generic "api" segment — which ties with every other
    endpoint under /api and makes an obvious match look ambiguous.
    """
    out = []
    for seg in re.split(r"[/{}]+", (path or "").casefold()):
        if not seg:
            continue
        out.append(seg[:-1] if len(seg) > 3 and seg.endswith("s") else seg)
    return out


def match_endpoint(method: str, path: str, real: list[str]) -> dict:
    """The ticket's wording resolved against the endpoints the app really
    serves — `real` is api_probe's "METHOD /path" list.

    This is the fix for the failure that opened NOOD_0201: a ticket said
    POST /greeting, the service served POST /greeting/new, and the authored
    test 404'd. An exact hit wins; otherwise the best segment-overlap match
    within the same method wins ONLY if it is unambiguous. A tie or a miss
    returns matched: None with the candidates — the caller asks, never guesses.
    """
    parsed = []
    for entry in real or []:
        bits = str(entry).split(None, 1)
        if len(bits) == 2:
            parsed.append((bits[0].upper(), bits[1]))
    want_m = (method or "").upper()
    same = [(m, p) for m, p in parsed if not want_m or m == want_m]
    for m, p in same:
        if p.casefold() == (path or "").casefold():
            return {"matched": f"{m} {p}", "path": p, "confidence": "exact",
                    "corrected": False, "candidates": []}
    want = set(_segments(path or ""))
    scored: list[tuple[int, str, str]] = []
    for m, p in same:
        segs = set(_segments(p))
        overlap = len(want & segs)
        if overlap:
            # prefer the closest length once overlap ties: /reviews/new beats
            # /reviews/{id}/comments for a ticket that said /reviews
            scored.append((overlap * 10 - abs(len(segs) - len(want)), m, p))
    scored.sort(key=lambda t: -t[0])
    if not scored:
        return {"matched": None, "path": None, "confidence": "none",
                "corrected": False,
                "candidates": [f"{m} {p}" for m, p in same][:_CAP],
                "question": f"no endpoint the app serves resembles "
                            f"{want_m or 'ANY'} {path!r} — which one does this "
                            f"criterion mean?"}
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return {"matched": None, "path": None, "confidence": "ambiguous",
                "corrected": False,
                "candidates": [f"{m} {p}" for _, m, p in scored][:_CAP],
                "question": f"{want_m or 'ANY'} {path!r} matches "
                            f"{len(scored)} served endpoints equally — which "
                            f"one does this criterion mean?"}
    _, m, p = scored[0]
    return {"matched": f"{m} {p}", "path": p, "confidence": "resolved",
            "corrected": p.casefold() != (path or "").casefold(),
            "candidates": []}


def _named_fields(text: str) -> list[str]:
    """The response fields a Then-clause names ("... containing id, name,
    date, and response") — each becomes a presence assertion."""
    m = _FIELDS.search(text)
    if not m:
        return []
    raw = re.split(r",|\band\b", m.group(1))
    out = []
    for part in raw:
        word = part.strip().strip(".").strip()
        # identifiers only, and never the prose words that surround a field
        # list ("returns a JSON response containing id, name" → id, name)
        if re.fullmatch(r"[a-z][\w]{1,29}", word or "", re.I) and \
                word.casefold() not in _NOT_FIELDS:
            out.append(word)
    return list(dict.fromkeys(out))[:8]


def plan(ticket: dict, endpoints: list[str] | None = None,
         base_url: str | None = None) -> dict:
    """One authorable goal per API-testable acceptance criterion.

    Each AC is read for: the endpoint it names (resolved against `endpoints` —
    the app's real routes), whether it describes a happy path or a rejection,
    the status it claims, and the response fields it lists. ACs that describe
    something the API cannot show (repo scaffolding, a database row, a coding
    standard) land in `not_automatable` WITH the reason — a test that pretends
    to cover those is worse than no test.
    """
    goals, skipped, questions = [], [], []
    for ac in ticket.get("acceptance_criteria") or []:
        text = " ".join([*ac.get("given", []), *ac.get("when", []),
                         *ac.get("then", [])])
        named = _paths(text)
        call = next((e for e in named if e["method"]), None)
        if call is None:
            skipped.append({
                "id": ac["id"],
                "reason": "names no HTTP method and path — nothing to call "
                          "through the API (implementation/standards criteria "
                          "belong to code review, database-state criteria "
                          "need a DB step family)"})
            continue
        resolved = match_endpoint(call["method"], call["path"], endpoints or [])
        if endpoints and not resolved["matched"]:
            questions.append(f"{ac['id']}: {resolved.get('question')}")
            skipped.append({"id": ac["id"],
                            "reason": resolved.get("question"),
                            "candidates": resolved.get("candidates")})
            continue
        path = resolved["path"] or call["path"]
        then_text = " ".join(ac.get("then", [])) or text
        negative = bool(_NEGATIVE.search(then_text))
        status = next((int(m.group(1)) for m in _STATUS.finditer(then_text)
                       if 100 <= int(m.group(1)) <= 599), None)
        if status is None:
            # The AC states the OUTCOME, not the code — map the two outcomes a
            # criterion can describe onto their conventional codes, and record
            # it as an assumption the author can see.
            status = 400 if negative else (
                201 if call["method"] == "POST" else 200)
        action = {"do": "api", "id": "call", "method": call["method"],
                  "url": path}
        if call["method"] in ("POST", "PUT", "PATCH"):
            action["body"] = "{}" if negative else \
                '{"name":"<value>"}'      # placeholder: the author fills it
        checks = [{"status": status, "after": "call"}]
        for field in ([] if negative else _named_fields(then_text)):
            checks.append({"response_contains": field, "after": "call"})
        goal = {"scenario": f"{ticket.get('key') or 'ticket'} {ac['id']} — "
                            f"{call['method']} {path}"[:80],
                "actions": [action], "checks": checks}
        entry = {"id": ac["id"], "goal": goal,
                 "endpoint": resolved["matched"] or f"{call['method']} {path}",
                 "assumptions": []}
        if resolved.get("corrected"):
            entry["assumptions"].append(
                f"the criterion says {call['method']} {call['path']}, but the "
                f"app serves {resolved['matched']} — using the served route")
        if not _STATUS.search(then_text):
            entry["assumptions"].append(
                f"no status code in the criterion; used {status} for a "
                f"{'rejection' if negative else 'success'} outcome")
        if _PAGINATION.search(then_text):
            entry["assumptions"].append(
                "criterion mentions pagination — add page/size query "
                "parameters and an item-count check once the defaults are known")
        if action.get("body") == '{"name":"<value>"}':
            questions.append(
                f"{ac['id']}: what request body should {call['method']} {path} "
                f"be sent? (the criterion names none — the placeholder "
                f"'{action['body']}' must be replaced)")
        goals.append(entry)
    if base_url is None:
        questions.append(
            "no base URL yet — run `noodle api-scan` (a single running "
            "localhost app is adopted automatically) or pass base_url")
    return {"ok": bool(goals), "key": ticket.get("key"),
            "summary": ticket.get("summary"),
            "goals": goals[:_CAP], "not_automatable": skipped[:_CAP],
            "questions": list(dict.fromkeys(questions))[:_CAP],
            "next": ("author each goal with author_test(goal=..., "
                     "base_url=...); replace every <value> placeholder first, "
                     "and settle `questions` — Noodle never guesses a URL, a "
                     "payload, or an endpoint.")}
