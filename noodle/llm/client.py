import os

from noodle.llm import cost

# Runaway-spend guard (NOOD_0007, roadmap Phase I). NOODLE_LLM_MAX_CALLS caps
# model calls per run (0 = unlimited). A badly broken build in full-LLM mode
# otherwise burns one call per step, every step. RCA calls count against their
# own NOODLE_RCA_MAX_CALLS pool so a cap on fallback steps doesn't also
# silence RCA (and vice versa) — one counter per cap var.
_call_counts: dict[str, int] = {}


def reset_calls():
    _call_counts.clear()


def _check_cap(cap_var: str = "NOODLE_LLM_MAX_CALLS"):
    cap = int(os.getenv(cap_var, "0") or "0")
    count = _call_counts.get(cap_var, 0)
    if cap > 0 and count >= cap:
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


def ask(prompt: str, system: str | None = None) -> str:
    _check_cap()
    ll = _litellm()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = ll.completion(
        # NOOD_0151 — no silent ollama default: every caller gates on
        # NOODLE_MODEL, so an unset var here is a misconfig that should fail
        # loudly (as ask_vision always has), not dial a phantom localhost.
        model=os.getenv("NOODLE_MODEL"),
        api_base=_api_base(),
        messages=messages,
        **_sampling_kwargs(),
    )
    cost.record("llm", response)
    return response.choices[0].message.content


def ask_vision(prompt: str, image_b64: str,
               cap_var: str = "NOODLE_LLM_MAX_CALLS") -> str:
    """Send a text prompt + base64 screenshot to a vision-capable model.
    `cap_var` picks which spend cap the call counts against (RCA has its own)."""
    _check_cap(cap_var)
    ll = _litellm()
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
    cost.record("rca" if cap_var == "NOODLE_RCA_MAX_CALLS" else "llm", response)
    return response.choices[0].message.content
