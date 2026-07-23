"""NOOD_0026 — the step-search-engine: given a plain-English description of a
test action, find the closest existing step in docs/steps_dictionary.md.

Reuses step_resolver.example_index() directly — the dictionary IS the
corpus, nothing here re-parses it a second way.

Deterministic ranking (stdlib only: re, difflib, collections — no new
dependency, no database; see docs/steps_dictionary.md "Finding a step" for
why) runs first and always. A local LLM (via noodle.llm.client.ask) is only
consulted as a tie-breaker when the deterministic ranking is ambiguous, and
only if NOODLE_MODEL/litellm are actually available — otherwise the best
deterministic guess is still reported, just at lower confidence. The LLM is
never required and never the primary mechanism.
"""
import difflib
import os
import re
from dataclasses import dataclass, field

from .step_resolver import example_index

_STOPWORDS = {
    "the", "a", "an", "to", "for", "of", "and", "on", "in", "is", "are",
    "that", "this", "it", "as", "user", "i", "should", "with", "by",
}
_KEYWORD_RE = re.compile(r'^(?:given|when|then|and|but)\s+', re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9']+")

# Confidence ladder — tuned against the worked example in the feature
# request ("store a return param and use it to another step") and a
# deliberately unrelated nonsense query; adjust here if a real query misranks.
_MIN_SCORE = 0.20      # below this: no match at all, offer a suggestion instead
_CONFIDENT_SCORE = 0.45   # at/above this + a clear gap: skip the LLM entirely
_GAP_MARGIN = 0.10


def _stem(word: str) -> str:
    """Loafer's stemmer — strip one trailing 's' (store<->stores,
    param<->params, click<->clicks) or 'ing'. Not linguistically rigorous;
    good enough to close the gap between a free-form query and dictionary
    wording. Ambiguous cases fall to the LLM tie-breaker instead of a
    smarter stemmer."""
    if len(word) > 4 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


_REF_PREFIX_RE = re.compile(r'\{(?:env|var|pom):([^}]*)\}')


def _normalize_for_search(step_line: str) -> str:
    """Strip a Given/When/Then prefix so only the action wording is compared.
    NOOD_0033: unwrap {env:X}/{var:X}/{pom:x} refs to their bare name so the
    prefix words never count as (or against) match tokens."""
    return _REF_PREFIX_RE.sub(r'\1', _KEYWORD_RE.sub("", step_line.strip()))


def tokenize(text: str) -> set[str]:
    """Lowercase words, drop punctuation/quotes/backticks (the char class
    below already excludes them), drop stopwords, stem what's left."""
    normalized = _normalize_for_search(text).lower()
    return {_stem(w) for w in _WORD_RE.findall(normalized)
            if w not in _STOPWORDS and len(w) > 1}


def _score(query_tokens: set[str], query_norm: str,
           cand_tokens: set[str], cand_norm: str) -> float:
    if query_tokens or cand_tokens:
        union = query_tokens | cand_tokens
        jaccard = len(query_tokens & cand_tokens) / len(union) if union else 0.0
    else:
        jaccard = 0.0
    ratio = difflib.SequenceMatcher(None, query_norm, cand_norm).ratio()
    return 0.7 * jaccard + 0.3 * ratio


@dataclass
class ScoredStep:
    section: str
    step: str
    type: str | None
    score: float


def rank(query: str, index: list[dict] | None = None, top_n: int = 5) -> list[ScoredStep]:
    """Score every example_index() entry against `query`, return the top_n
    sorted by score descending."""
    index = example_index() if index is None else index
    query_tokens = tokenize(query)
    query_norm = _normalize_for_search(query).lower()
    scored = []
    for entry in index:
        cand_tokens = tokenize(entry["step"])
        cand_norm = _normalize_for_search(entry["step"]).lower()
        score = _score(query_tokens, query_norm, cand_tokens, cand_norm)
        scored.append(ScoredStep(section=entry["section"], step=entry["step"],
                                  type=entry["type"], score=score))
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:top_n]


@dataclass
class StepSearchResult:
    query: str
    match: ScoredStep | None
    shortlist: list[ScoredStep] = field(default_factory=list)
    confidence: str = "none"     # "high" | "low" | "none"
    llm_used: bool = False
    reason: str = ""


def _llm_classify(query: str, shortlist: list[ScoredStep]) -> int | str | None:
    """Ask the local LLM to pick the best candidate index (0-based) out of
    `shortlist`. Returns an int index, the string "NONE" (the model
    explicitly says nothing fits), or None (call unavailable/failed/
    unparseable — i.e. the LLM had nothing to add, distinct from an
    explicit NONE). Never raises."""
    try:
        from noodle.llm import client
    except ImportError:
        return None
    lines = "\n".join(f"{i + 1}. {c.step}" for i, c in enumerate(shortlist))
    prompt = (
        "You are matching a plain-English test step description to the "
        "closest existing step from a fixed shortlist. Reply with ONLY the "
        "number of the best match, or NONE if none of them mean the same "
        f"thing.\n\nDescription: \"{query}\"\n\nCandidates:\n{lines}\n\n"
        "Reply with just the number or NONE."
    )
    try:
        reply = client.ask(prompt)
    except Exception:
        return None
    if not isinstance(reply, str):
        return None
    # Small local models (e.g. Ollama llama3.1) sometimes echo a chat-
    # template artifact before the answer ("### User:\n2") — matching only
    # the start of the whole reply missed that in practice. The actual
    # answer is reliably the last non-empty line.
    lines = [ln.strip() for ln in reply.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1]
    if re.match(r'(?i)^none\b', last):
        return "NONE"
    m = re.match(r'\s*(\d+)', last)
    if not m:
        return None
    idx = int(m.group(1)) - 1
    return idx if 0 <= idx < len(shortlist) else None


def search_step(query: str, *, use_llm: bool = True) -> StepSearchResult:
    """The step-search-engine's entry point. Deterministic ranking always
    runs; the LLM is only consulted when the result is ambiguous."""
    shortlist = rank(query)
    if not shortlist or shortlist[0].score < _MIN_SCORE:
        return StepSearchResult(query=query, match=None, shortlist=shortlist,
                                 confidence="none",
                                 reason="No existing step scores highly enough to be a real match.")

    top = shortlist[0]
    # Gap is measured against the next candidate of a *different* action
    # type, not just position 2 — the dictionary documents the same step in
    # 3 subject forms ("User"/"I"/"The user"), so the literal runner-up is
    # almost always a near-duplicate of the correct answer, not a genuine
    # rival interpretation. A tiny gap between two identical-type near
    # duplicates must not read as ambiguity.
    rival = next((s for s in shortlist[1:] if s.type != top.type), None)
    gap = top.score - (rival.score if rival else 0.0)
    if top.score >= _CONFIDENT_SCORE and gap >= _GAP_MARGIN:
        return StepSearchResult(query=query, match=top, shortlist=shortlist,
                                 confidence="high",
                                 reason="Deterministic ranking found a clear best match.")

    if use_llm and os.getenv("NOODLE_MODEL"):
        verdict = _llm_classify(query, shortlist)
        if isinstance(verdict, int):
            return StepSearchResult(query=query, match=shortlist[verdict], shortlist=shortlist,
                                     confidence="high", llm_used=True,
                                     reason="Local LLM picked the best candidate from an ambiguous shortlist.")
        if verdict == "NONE":
            # The model looked at the shortlist and said none of them fit —
            # that overrides showing a weak deterministic guess as if it
            # were a real match. The model can't invent a match from
            # nothing either, so this becomes "no match", not a fabrication.
            return StepSearchResult(query=query, match=None, shortlist=shortlist,
                                     confidence="none", llm_used=True,
                                     reason="Local LLM reviewed the shortlist and found no real match.")
        # verdict is None: LLM unavailable/failed/unparseable — fall through
        # to the deterministic best-effort result below, same as if it were
        # never consulted.

    return StepSearchResult(query=query, match=top, shortlist=shortlist,
                             confidence="low",
                             reason="Best-effort deterministic match — ranking was ambiguous.")
