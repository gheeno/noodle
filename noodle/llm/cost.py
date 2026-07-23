"""LLM token + dollar ledger (NOOD_0080).

Every model call the engine makes funnels through noodle.llm.client, so one
in-process ledger here captures the exact spend of a run. hooks.after_all
persists it as llm_cost*.json in the results dir; readers (CLI summary line,
RCA report footer, MCP tool results) sum every llm_cost*.json under the
results root, so single-process and parallel-worker runs share one code path.

This only covers Noodle's own calls (NOODLE_MODEL). The spend of an external
driving agent (Claude Code, Copilot) never passes through the engine — see
docs/llm-setup.md "Who pays for what".
"""
import json
import os
from pathlib import Path

# purpose -> {"calls", "input_tokens", "output_tokens", "usd"}. Purposes are
# the spend pools from client.py's cap vars: "llm" (steps, @visual locator,
# generate, summaries) and "rca" (vision verdicts on failure screenshots).
_ledger: dict[str, dict] = {}
_model: str | None = None


def reset():
    _ledger.clear()
    global _model
    _model = None


def record(purpose: str, response) -> None:
    """Add one litellm response to the ledger. Never raises — cost tracking
    must not break the call that produced the response."""
    global _model
    try:
        usage = getattr(response, "usage", None)
        entry = _ledger.setdefault(
            purpose, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": None})
        entry["calls"] += 1
        entry["input_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
        entry["output_tokens"] += getattr(usage, "completion_tokens", 0) or 0
        _model = getattr(response, "model", None) or os.getenv("NOODLE_MODEL")
        try:
            import litellm
            usd = litellm.completion_cost(completion_response=response)
        except Exception:
            usd = None  # unknown model pricing (e.g. self-hosted) — tokens still count
        if usd is not None:
            entry["usd"] = (entry["usd"] or 0.0) + usd
    except Exception:
        pass


def summary() -> dict | None:
    """Aggregate ledger, or None when no model call happened."""
    if not _ledger:
        return None
    return _merge(_ledger, _model)


def _merge(by_purpose: dict, model: str | None) -> dict:
    usds = [e["usd"] for e in by_purpose.values() if e.get("usd") is not None]
    return {
        "calls": sum(e["calls"] for e in by_purpose.values()),
        "input_tokens": sum(e["input_tokens"] for e in by_purpose.values()),
        "output_tokens": sum(e["output_tokens"] for e in by_purpose.values()),
        "usd": round(sum(usds), 6) if usds else None,
        "model": model,
        "by_purpose": by_purpose,
    }


def format_line(s: dict | None = None) -> str:
    """One human-readable line for the CLI / report footer."""
    s = s or summary()
    if not s or not s["calls"]:
        return "LLM cost: none (no model calls this run)"
    usd = f"~${s['usd']:.2f}" if s["usd"] is not None else f"cost unknown for {s['model']}"
    split = ", ".join(
        f"{p} ${e['usd']:.2f}" for p, e in s["by_purpose"].items()
        if e.get("usd") is not None)
    return (f"LLM cost: {s['calls']} call(s) | "
            f"{s['input_tokens']:,} in / {s['output_tokens']:,} out tokens | "
            f"{usd}" + (f" ({split})" if split and len(s["by_purpose"]) > 1 else "")
            + f" | model {s['model']}")


def write_json(results_dir) -> None:
    """Persist the ledger next to the run results (no-op when no calls).
    Parallel workers get a per-pid filename so the CLI's flatten-merge
    doesn't clobber one worker's ledger with another's."""
    s = summary()
    if not s:
        return
    name = (f"llm_cost.p{os.getpid()}.json"
            if os.getenv("NOODLE_PARALLEL_WORKER") == "1" else "llm_cost.json")
    d = Path(results_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(s, indent=2))


def load_total(results_root) -> dict | None:
    """Sum every llm_cost*.json under the results root (flat after a parallel
    merge, still in p*/ worker dirs before it — rglob covers both)."""
    root = Path(results_root)
    if not root.is_dir():
        return None
    by_purpose: dict[str, dict] = {}
    model = None
    found = False
    for f in sorted(root.rglob("llm_cost*.json")):
        try:
            s = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        found = True
        model = s.get("model") or model
        for p, e in (s.get("by_purpose") or {}).items():
            tgt = by_purpose.setdefault(
                p, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": None})
            tgt["calls"] += e.get("calls", 0)
            tgt["input_tokens"] += e.get("input_tokens", 0)
            tgt["output_tokens"] += e.get("output_tokens", 0)
            if e.get("usd") is not None:
                tgt["usd"] = (tgt["usd"] or 0.0) + e["usd"]
    return _merge(by_purpose, model) if found else None


def estimate(text: str, model: str | None = None) -> dict:
    """Pre-flight estimate: model-correct token count for `text` plus the
    input-token dollar floor. Output tokens are unknowable in advance, so the
    dollar figure is a floor, not a forecast."""
    # NOOD_0151 — no model configured: price against the recommended cloud
    # default so the estimate is representative, not a $0 local figure.
    model = model or os.getenv("NOODLE_MODEL", "anthropic/claude-sonnet-5")
    try:
        import litellm
    except ImportError:
        raise ImportError("Cost estimation requires: pip install noodle[llm]")
    tokens = litellm.token_counter(model=model, text=text)
    try:
        usd_in, _ = litellm.cost_per_token(
            model=model, prompt_tokens=tokens, completion_tokens=0)
    except Exception:
        usd_in = None
    return {"model": model, "input_tokens": tokens,
            "usd_input_floor": round(usd_in, 6) if usd_in is not None else None}
