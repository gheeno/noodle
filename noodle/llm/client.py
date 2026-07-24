import json
import logging
import os
import time

from noodle import log
from noodle.llm import cost

_TRUTHY = {"1", "true", "yes", "on"}

# Runaway-spend guard (NOOD_0007, roadmap Phase I). NOODLE_LLM_MAX_CALLS caps
# model calls per run (0 = unlimited). A badly broken build in full-LLM mode
# otherwise burns one call per step, every step. RCA calls count against their
# own NOODLE_RCA_MAX_CALLS pool so a cap on fallback steps doesn't also
# silence RCA (and vice versa) — one counter per cap var.
_call_counts: dict[str, int] = {}


def reset_calls():
    _call_counts.clear()


def _purpose(cap_var: str) -> str:
    return "rca" if cap_var == "NOODLE_RCA_MAX_CALLS" else "llm"


def _check_cap(cap_var: str = "NOODLE_LLM_MAX_CALLS"):
    cap = int(os.getenv(cap_var, "0") or "0")
    count = _call_counts.get(cap_var, 0)
    if cap > 0 and count >= cap:
        # NOOD_0172 — governance: the spend cap stopping a call is itself an
        # auditable event (a build burned its model budget).
        log.event("llm.call", f"🛑 spend cap reached ({cap}) for {cap_var}",
                  level=logging.WARNING, purpose=_purpose(cap_var), capped=True, cap=cap)
        raise AssertionError(
            f"LLM call cap reached ({cap}) — the build may be badly broken.\n"
            f"  → Raise {cap_var}, or set it to 0 to disable the cap"
        )
    _call_counts[cap_var] = count + 1


def _litellm():
    try:
        import litellm
        return litellm
    except ImportError:
        raise ImportError("LLM support requires: pip install noodle[llm]")


def _api_base():
    """The endpoint override, or None to let LiteLLM use the provider's default.

    Only Ollama / Foundry Local / self-hosted OpenAI-compatible servers need an
    explicit base URL. Cloud providers (Anthropic, Gemini, Groq, OpenAI) resolve
    their own endpoint from the model string + API key — passing a hardcoded
    localhost base here would silently misroute them. ponytail: unset → None,
    LiteLLM fills in the right URL per provider.
    """
    return os.getenv("NOODLE_LLM_URL") or None


def _sampling_kwargs() -> dict:
    """NOOD_0101 — temperature 0 by default. Every ask()/ask_vision() call in
    the framework is a constrained-output task (step JSON, vocabulary-bound
    Gherkin, pick-a-number classification); sampling entropy there only buys
    repair loops and wrong-enum retries (docs/llm-setup.md §7 measured this on
    Ollama, whose chat default is ~0.8). NOODLE_LLM_TEMPERATURE overrides;
    set it to the empty string to omit the param entirely for providers/models
    that reject it."""
    raw = os.getenv("NOODLE_LLM_TEMPERATURE", "0")
    if raw.strip() == "":
        return {}
    return {"temperature": float(raw)}


def _api_host() -> str:
    """The endpoint host for the llm.call event — host only, NEVER the
    key-bearing URL. Cloud providers resolve their own endpoint, so fall back
    to the provider prefix of NOODLE_MODEL."""
    base = _api_base()
    if base:
        from urllib.parse import urlparse
        return urlparse(base).hostname or base
    model = os.getenv("NOODLE_MODEL", "") or ""
    return model.split("/")[0] if "/" in model else "provider-default"


def _log_call(purpose: str, response, metrics: dict, dur_ms: int) -> None:
    """NOOD_0172 — the AI-governance record of one model call: model, purpose,
    tokens, dollars, latency, endpoint host. Never the prompt/response content
    (that's the opt-in payload log below) and never the API key. `dur_ms` times
    the completion only, not our own cost bookkeeping."""
    model = getattr(response, "model", None) or os.getenv("NOODLE_MODEL")
    dur = dur_ms
    m = metrics or {}
    log.event("llm.call", f"🤖 {purpose} call → {model} ({dur}ms)",
              purpose=purpose, model=model, duration_ms=dur,
              input_tokens=m.get("input_tokens"), output_tokens=m.get("output_tokens"),
              usd=m.get("usd"), temperature=_sampling_kwargs().get("temperature"),
              api_host=_api_host())


def _log_payload(purpose: str, prompt: str, system: str | None, response, image: bool = False) -> None:
    """Opt-in (NOODLE_LOG_LLM_PAYLOADS) — prompt + completion to a separate,
    gitignored artifact (artifacts/llm/<run_id>.jsonl), redacted, NEVER the log
    stream: prompts carry page text, which carries customer data. Screenshots
    are omitted whole. Best-effort — diagnostic, never breaks the call."""
    if os.getenv("NOODLE_LOG_LLM_PAYLOADS", "").strip().lower() not in _TRUTHY:
        return
    try:
        from noodle.reporting import paths
        rid = os.getenv("NOODLE_RUN_ID") or log.run_id() or "no-run"
        d = paths.artifacts_root() / "llm"
        d.mkdir(parents=True, exist_ok=True)
        completion = response.choices[0].message.content
        rec = {"purpose": purpose,
               "model": getattr(response, "model", None) or os.getenv("NOODLE_MODEL"),
               "system": log.redact(system or ""), "prompt": log.redact(prompt or ""),
               "completion": log.redact(completion or "")}
        if image:
            rec["image"] = "omitted (screenshot may contain PII)"
        with open(d / f"{rid}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def ask(prompt: str, system: str | None = None) -> str:
    _check_cap()
    ll = _litellm()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    t0 = time.monotonic()
    response = ll.completion(
        # NOOD_0151 — no silent ollama default: every caller gates on
        # NOODLE_MODEL, so an unset var here is a misconfig that should fail
        # loudly (as ask_vision always has), not dial a phantom localhost.
        model=os.getenv("NOODLE_MODEL"),
        api_base=_api_base(),
        messages=messages,
        **_sampling_kwargs(),
    )
    dur_ms = int((time.monotonic() - t0) * 1000)
    metrics = cost.record("llm", response)
    _log_call("llm", response, metrics, dur_ms)
    _log_payload("llm", prompt, system, response)
    return response.choices[0].message.content


def ask_vision(prompt: str, image_b64: str,
               cap_var: str = "NOODLE_LLM_MAX_CALLS") -> str:
    """Send a text prompt + base64 screenshot to a vision-capable model.
    `cap_var` picks which spend cap the call counts against (RCA has its own)."""
    _check_cap(cap_var)
    ll = _litellm()
    t0 = time.monotonic()
    response = ll.completion(
        model=os.getenv("NOODLE_MODEL"),
        api_base=_api_base(),
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        **_sampling_kwargs(),
    )
    dur_ms = int((time.monotonic() - t0) * 1000)
    purpose = _purpose(cap_var)
    metrics = cost.record(purpose, response)
    _log_call(purpose, response, metrics, dur_ms)
    _log_payload(purpose, prompt, None, response, image=True)
    return response.choices[0].message.content
