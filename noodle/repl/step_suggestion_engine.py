"""NOOD_0026 — the step-suggestion-engine: when the step-search-engine
(noodle/resolver/step_search_engine.py) finds no good match for a plain-
English description, draft a new step and — on acceptance — write it.

Safety boundary: this only ever drafts a new *phrasing* for a capability
that already exists at runtime (an existing action_type in
step_resolver.VALID_TYPES). It never fabricates new Playwright/REST business
logic — if nothing in VALID_TYPES fits, it says so plainly and writes
nothing (docs/encyclopedia.md §15 has the manual workflow for that case).

Accepted suggestions are staged in docs/agent_patterns.yaml (not spliced
into the hand-curated, order-sensitive noodle/resolver/patterns.py — see
that file's own comment for why) plus an example line in
docs/steps_dictionary.md's "Agent-Suggested Steps (staging)" section. A
human periodically reviews and promotes a good one into patterns.py proper.
"""
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # ponytail: only fail at accept-time, not import time

from noodle.resolver import patterns as _patterns
from noodle.resolver import step_resolver
from noodle.resolver.patterns import _FIRST_TO_THIRD, normalize_subject
from noodle.resolver.step_resolver import VALID_TYPES
from noodle.resolver.step_search_engine import _MIN_SCORE, StepSearchResult

_REPO_DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"
_ANCHOR = "<!-- agent-suggestions-anchor -->"
_STAGING_HEADING = "## Agent-Suggested Steps (staging)"


def _default_docs_dir() -> Path:
    """NOOD_0027 — write into whatever workspace patterns.set_agent_patterns_dir()
    last pointed at (real `noodle run`/step-search --workspace), falling back
    to this repo's own docs/ for a dev checkout with no workspace set."""
    return _patterns._workspace_patterns_dir or _REPO_DOCS_DIR

_ASSERTION_WORDS = ("should", "verify", "assert", "confirm", "expect")

# Hand-kept param-name mapping so a drafted step reuses the same names the
# runtime already expects for that action_type — mirrors the "WEB param keys
# by type" comment already embedded in step_resolver.py's own LLM prompt.
# ponytail: not exhaustive; extend as a real suggestion needs a type not
# listed here (falls back to value_1/value_2/... rather than erroring).
_PARAM_NAMES_BY_TYPE = {
    "click": ["locator"], "hover": ["locator"], "check": ["locator"],
    "uncheck": ["locator"], "clear": ["locator"], "double_click": ["locator"],
    "right_click": ["locator"],
    "fill": ["locator", "value"], "select": ["locator", "value"],
    "store_text": ["locator", "var"],
    "store_attribute": ["attribute", "locator", "var"],
    "assert_visible": ["text"], "assert_hidden": ["text"],
    "assert_url": ["fragment"], "assert_title": ["fragment"],
    "assert_value": ["locator", "value"], "assert_state": ["locator", "state"],
    "assert_attribute": ["locator", "attribute", "value"],
    "assert_count": ["count", "locator"],
    "assert_compare": ["left", "op", "right"],
    "set_var": ["var", "value"], "navigate": ["url"], "search": ["query"],
    "press_key": ["key"], "scroll": ["direction"], "screenshot": ["name"],
    "wait_visible": ["text"], "wait_hidden": ["text"],
    "wait_seconds": ["seconds"],
    "run_if": ["condition", "negate", "then"],
}


@dataclass
class StepSuggestion:
    query: str
    keyword: str                       # "When" or "Then"
    phrase: str                        # the drafted step body (no keyword)
    regex: str                         # anchored regex to persist, or "" if it doesn't fit
    action_type: str | None
    params: list[dict] = field(default_factory=list)
    rationale: str = ""
    based_on: str | None = None        # nearest existing example step-search found, if any
    fits_existing_type: bool = False


def _looks_like_assertion(query: str) -> bool:
    lowered = query.lower()
    return any(re.search(rf'\b{w}\b', lowered) for w in _ASSERTION_WORDS)


def _conjugate_leading_verb(body: str) -> str:
    """A free-form query is usually typed as a bare imperative ("frobnicate
    the widget"), not the 3rd-person Gherkin form a real "When User
    frobnicates ..." step needs — normalize_subject() only conjugates when it
    actually strips a subject prefix, so a subject-less query passes through
    unchanged. Reuses patterns.py's own first->third-person verb map for
    recognized verbs; falls back to a naive "+s" for anything else (a human
    reviewing the staged suggestion can always fix the wording — this just
    avoids an obviously bare-infinitive phrase in the drafted step)."""
    words = body.split(' ', 1)
    if not words or not words[0]:
        return body
    head, rest = words[0], (words[1] if len(words) > 1 else '')
    head_l = head.lower()
    if head_l in _FIRST_TO_THIRD:
        head = _FIRST_TO_THIRD[head_l]
    elif not head_l.endswith('s'):
        head = head + 's'
    return f'{head} {rest}'.rstrip()


def _build_regex(body: str) -> str:
    """Anchored regex that matches `body` verbatim, except:
    - each quoted span becomes a generic capture group — so the same
      drafted pattern also matches future steps with different quoted
      values.
    - the leading verb's trailing 's' is optional (`frobnicates?`), mirroring
      the `clicks?`/`verifies?` convention every hand-written entry in
      PATTERNS already uses. normalize_subject() only conjugates a small
      hand-kept set of common verbs (patterns.py's _FIRST_TO_THIRD) — an
      unknown verb is left as typed when the subject was "I", so without
      this the draft would only ever match the "User ..." form, never
      "I ...".
    """
    head, _, rest = body.partition(' ')
    if head.endswith('s') and len(head) > 2:
        head_pattern = re.escape(head[:-1]) + 's?'
    else:
        head_pattern = re.escape(head)

    parts = [head_pattern]
    last = 0
    if rest:
        parts.append(r'\ ')
        for m in re.finditer(r'["\'](.*?)["\']', rest):
            parts.append(re.escape(rest[last:m.start()]))
            parts.append(r'["\'](.+?)["\']')
            last = m.end()
        parts.append(re.escape(rest[last:]))
    return '^' + ''.join(parts) + '$'


def _draft_params(body: str, action_type: str) -> list[dict]:
    quoted_spans = list(re.finditer(r'["\'](.*?)["\']', body))
    names = _PARAM_NAMES_BY_TYPE.get(action_type) or [f"value_{i + 1}" for i in range(len(quoted_spans))] or ["value"]
    params = []
    for i, name in enumerate(names):
        if i < len(quoted_spans):
            params.append({"name": name, "source": "group", "group": i + 1, "quoted": True})
        else:
            # Fewer quoted values in the query than this action_type expects —
            # leave an explicit placeholder for a human to fill in, same
            # "generated skeletons are honest about what's missing"
            # convention as generate.py's <css selector> stubs.
            params.append({"name": name, "source": "literal", "value": "<TODO>"})
    return params


def _llm_pick_type(query: str) -> str | None:
    """Ask the local LLM to classify `query` into one existing VALID_TYPES
    entry, only used when the nearest search neighbor has no resolvable
    type of its own. Never raises; rejects anything not in VALID_TYPES
    (same guard step_resolver._llm_resolve_uncached uses)."""
    try:
        from noodle.llm import client
    except ImportError:
        return None
    prompt = (
        "Classify this test step description into exactly ONE of these "
        "action types, or reply NONE if nothing fits.\n\n"
        f"Types: {', '.join(sorted(VALID_TYPES))}\n\n"
        f"Description: \"{query}\"\n\nReply with just the type name or NONE."
    )
    try:
        reply = client.ask(prompt)
    except Exception:
        return None
    if not isinstance(reply, str):
        return None
    # Small local models (e.g. Ollama llama3.1) sometimes echo a chat-
    # template artifact before the answer ("### User:\nassert_value") —
    # an exact match on the whole stripped reply missed that in practice.
    # The actual answer is reliably the last non-empty line.
    lines = [ln.strip().strip('."\'`') for ln in reply.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    candidate = lines[-1]
    return candidate if candidate in VALID_TYPES else None


def draft_suggestion(query: str, search_result: StepSearchResult,
                      *, use_llm: bool = True) -> StepSuggestion:
    """The step-suggestion-engine's entry point — called when
    step_search_engine.search_step() found no good match."""
    stripped = query.strip()
    body = normalize_subject(stripped)
    if body == stripped:
        # normalize_subject() found no subject prefix to strip — this is a
        # bare description ("frobnicate the widget"), not yet in the
        # 3rd-person form a real Gherkin step needs. Conjugate it so the
        # regex we draft matches what normalize_subject() will actually
        # produce once someone writes "When User frobnicates ...".
        body = _conjugate_leading_verb(body)
    keyword = "Then" if _looks_like_assertion(query) else "When"

    # draft_suggestion() is only ever called once search_step() has already
    # decided there's no real match — so shortlist[0], if present, scored
    # below _MIN_SCORE and may be pure noise (e.g. "frobnicate the sprocket
    # widget" weakly resembling "closes the current tab" at score 0.14).
    # Only trust it as a type-reuse hint when it still clears the same floor
    # search_step uses to call something "related at all"; otherwise treat
    # it as no deterministic hint and let the LLM (if any) classify instead.
    nearest = search_result.shortlist[0] if search_result.shortlist else None
    nearest_is_meaningful = nearest is not None and nearest.score >= _MIN_SCORE
    based_on = nearest.step if nearest_is_meaningful else None
    action_type = nearest.type if nearest_is_meaningful else None
    rationale = ""

    if action_type is None and use_llm and os.getenv("NOODLE_MODEL"):
        action_type = _llm_pick_type(query)
        if action_type:
            rationale = "Local LLM classified this into an existing action type."

    if not action_type:
        rationale = rationale or (
            "No existing action type covers this — it needs new runtime logic "
            "in orchestrator/runner.py (+ an action handler), not auto-generated. "
            "See docs/encyclopedia.md §15 for the manual 3-step workflow."
        )
        return StepSuggestion(query=query, keyword=keyword, phrase=body, regex="",
                               action_type=None, rationale=rationale,
                               based_on=based_on, fits_existing_type=False)

    regex = _build_regex(body)
    params = _draft_params(body, action_type)
    rationale = rationale or f'Nearest existing step: "{based_on}" ({action_type}).'
    return StepSuggestion(query=query, keyword=keyword, phrase=body, regex=regex,
                           action_type=action_type, params=params, rationale=rationale,
                           based_on=based_on, fits_existing_type=True)


def accept_suggestion(suggestion: StepSuggestion, *, docs_dir: Path | None = None) -> dict:
    """Stage `suggestion` in docs/agent_patterns.yaml + append an example to
    docs/steps_dictionary.md, then invalidate every in-process cache so it
    resolves/searches immediately, no restart needed."""
    if not suggestion.fits_existing_type:
        raise ValueError("Cannot accept a suggestion with no existing action type — "
                          "see its .rationale for why.")
    if yaml is None:
        raise ImportError("Writing docs/agent_patterns.yaml requires pyyaml (pip install pyyaml)")

    docs_dir = docs_dir or _default_docs_dir()
    docs_dir.mkdir(parents=True, exist_ok=True)
    patterns_path = docs_dir / "agent_patterns.yaml"
    dictionary_path = docs_dir / "steps_dictionary.md"

    entries = []
    if patterns_path.exists():
        raw = yaml.safe_load(patterns_path.read_text()) or []
        if isinstance(raw, list):
            entries = raw
    entries.append({
        "phrase": suggestion.regex,
        "action_type": suggestion.action_type,
        "params": suggestion.params,
        "added_by": "noodle repl",
        "added_on": date.today().isoformat(),
        "source_query": suggestion.query,
        "status": "staging",
    })
    patterns_path.write_text(yaml.safe_dump(entries, sort_keys=False))

    new_line = f"{suggestion.keyword} {suggestion.phrase}"
    gherkin_block = f"```gherkin\n{new_line}\n```\n\n"
    if dictionary_path.exists():
        text = dictionary_path.read_text()
    else:
        text = ""
    if _ANCHOR in text:
        text = text.replace(_ANCHOR, f"{gherkin_block}{_ANCHOR}")
    else:
        if _STAGING_HEADING not in text:
            text += (
                f"\n{_STAGING_HEADING}\n\n"
                "Steps accepted via `noodle step-search --accept` or the "
                "`noodle repl` y/N prompt land here first. Review "
                "periodically and promote a good one into "
                "`noodle/resolver/patterns.py` (docs/encyclopedia.md §15), "
                "then delete its entry here and from "
                "`docs/agent_patterns.yaml`.\n\n"
            )
        text += f"{gherkin_block}{_ANCHOR}\n"
    dictionary_path.write_text(text)

    step_resolver.clear_index_cache()
    _patterns.clear_agent_patterns_cache()

    print(f"→ Wrote {patterns_path}")
    print(f"→ Wrote {dictionary_path}")
    return {"patterns_file": patterns_path, "dictionary_file": dictionary_path}
