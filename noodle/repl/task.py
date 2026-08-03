"""NOOD_0191 — one door for a driving agent: free text in, one envelope out.

Every existing entry point assumes the caller already knows which command it
wants. That holds for a human and for an agent that read AGENTS.md. It does not
hold for an AI-SDLC builder agent that has never seen our docs and sends
"write me a regression test for the new checkout discount and tell me if it
passes" — `author --prompt` refuses that (no URL), and nothing tells the caller
what to send instead, so the loop stalls with no way forward.

The instinct is a smarter parser. Wrong rung: the caller is already an LLM
holding the app it just built. Noodle does not need to understand arbitrary
prose — it needs to be UNAMBIGUOUS ABOUT WHAT IT NEEDS, once, and let the
caller re-emit. That is bounded work; open-ended prompt understanding is not.

So this module is two small things and no third:

  route()   — a deterministic keyword scan over five intents, dispatching to
              commands that already exist. No new authoring logic, no LLM.
  contract()— the grammar, the five intents, and the envelope schema as ONE
              payload, so an agent that can't read the .claude/skills/ card
              (i.e. anything that isn't Claude Code or Copilot) can fetch the
              contract instead of guessing.

`need` is the load-bearing key. A refusal that only says "no URL in the prompt"
costs a round trip to interpret; a refusal that says need: ["base_url"] next:
"re-send with a URL and numbered steps" is actionable on the first read.

ponytail: the classifier is keyword scoring, not a model and not a grammar. The
ceiling is a prompt that names two intents ("update the test and run it") —
first-listed wins by source position and `alternatives` names the other, which
is enough because the caller can re-send. Reach for a parser only if that
ordering is measurably wrong, never because it looks naive.
"""
from __future__ import annotations

import re

# (intent, regex). Order is irrelevant — every pattern is scored against the
# whole text and the earliest MATCH POSITION wins, so "update the test, then
# run it" routes to update. A tie goes to the earlier entry here.
_INTENTS: list[tuple[str, re.Pattern]] = [
    # NOOD_0192 — "review this repo and generate tests for the new features"
    # is the AI-SDLC opener, and it names TWO intents. Earliest match wins by
    # source position, so the repo half routes first and comes back with the
    # stack, how the repo serves itself, and the questions still open — which
    # is what the caller needs before a generate can succeed at all.
    ("scan", re.compile(
        r"\b(?:review|scan|inspect|analyz|analys|look\s+at|explore|"
        r"understand|what(?:'s|\s+is)\s+in)\w*\b"
        r"(?:(?!\brepo\b|\bcodebase\b|\bproject\b).){0,30}?"
        r"\b(?:repo(?:sitory)?|code\s*base|project)\b|"
        r"\btech(?:nology)?\s+stack\b", re.I | re.S)),
    # NOOD_0192 — the nouns are plural too. "generate new API tests" matched
    # NOTHING (\btest\b can't match "tests"), so every plural ask fell through
    # to the generate DEFAULT — right answer, no confidence, and invisible in
    # `also_matched` when another intent was named first.
    ("update", re.compile(
        r"\b(?:update|amend|modify|revise|extend|adjust|edit|fix|repair|"
        r"correct|change)\b(?:(?!\btests?\b|\bscenarios?\b|\bfeatures?\b)"
        r".){0,40}?\b(?:tests?|scenarios?|features?|specs?)\b", re.I | re.S)),
    ("generate", re.compile(
        r"\b(?:write|create|generate|author|add|build|make|new|draft|"
        r"scaffold)\b(?:(?!\btests?\b|\bscenarios?\b).){0,40}?"
        r"\b(?:tests?|scenarios?|features?|specs?|coverage|cases?)\b",
        re.I | re.S)),
    ("verdict", re.compile(
        r"\b(?:did\s+(?:it|they|the\s+tests?)\s+pass|is\s+it\s+green|"
        r"verdict|pass(?:ed)?\s+or\s+fail(?:ed)?|what(?:'s|\s+is|\s+was)\s+"
        r"the\s+(?:result|outcome|status)|benchmark|regression\s+verdict)\b",
        re.I)),
    ("report", re.compile(
        r"\b(?:allure|rca|root\s*cause|show\s+me\s+the\s+(?:report|results)|"
        r"open\s+the\s+report|serve\s+the\s+report|the\s+report)\b", re.I)),
    ("run", re.compile(
        r"\b(?:run|execute|re-?run|kick\s+off|trigger|smoke|regress(?:ion)?|"
        r"exercise)\b", re.I)),
]

# Intents that need no prompt text beyond the verb — "run it", "did it pass"
# are complete instructions. `generate`/`update` carry a payload we compile.
_VERB_ONLY = {"run", "report", "verdict"}

INTENTS = ("scan", "generate", "update", "run", "report", "verdict")

# The first numbered step, at line start or inline. A caller that says
# "create a test: 1. Go to ... 2. Search for ..." has written a routing
# sentence AND a step list in one string; the sentence is ours (we classified
# on it) and would otherwise reach the prompt compiler as a step it can only
# refuse. Only stripped when a numbered list is actually present — that is the
# unambiguous case, and anything else stays a negotiation reply by design.
_FIRST_STEP = re.compile(r"(?:^|[\s:;,-])(1\s*[.)]\s)")


def steps_of(text: str) -> str:
    """`text` with any leading routing sentence removed."""
    m = _FIRST_STEP.search(text or "")
    return text[m.start(1):] if m else text


# NOOD_0191 — a non-web ask, routed to the right wok instead of refused with
# web grammar. A developer asked an agent for API tests and was told "Noodle
# is a web UI testing framework, use pytest or Postman" — wrong since
# NOOD_0007, but the only vocabulary it had been shown was clicks and
# searches. NOOD_0192 removed this list's premise for the api wok — that one
# compiles from a prompt now, so its hint names the cheap door first; the rest
# still redirect to feature_content.
_WOKS = [
    ("api", re.compile(
        r"\b(?:api|apis|rest|restful|endpoints?|http|https?\s+call|"
        r"swagger|openapi|graphql|payload|webhooks?|"
        r"(?:GET|POST|PUT|PATCH|DELETE)\s+/|status\s+code)\b", re.I),
     "steps_dictionary', query='REST",
     # NOOD_0192 — the api wok authors from a PROMPT now, same as web. This
     # hint used to send every API request to hand-written Gherkin.
     "REST is browserless. Send it as numbered steps like any other test: "
     "`1. GET https://host/path` / `2. Verify the response status is 200` — "
     "an api-only prompt compiles to an @api scenario (no browser started), "
     "and mixing web steps into the same prompt gives you a cross-wok test. "
     "Hand-authored feature_content is still the door for headers, auth and "
     "{var:} chaining (`Given sets {var:REST_BASE_URL} to "
     "'{env:API_BASE_URL}'`; sample_feature_tests/api/ has the worked "
     "files)."),
    ("mobile", re.compile(
        r"\b(?:appium|android|ios|mobile\s+app|native\s+app|emulator|"
        r"simulator|apk|\.ipa)\b", re.I),
     "woks",
     "Native mobile runs through Appium: tag @appium plus the platform "
     "(@android/@ios) and discover with probe_app, not probe_page."),
    ("desktop", re.compile(
        r"\b(?:desktop\s+app|windows\s+app|winapp|spreadsheet|excel|"
        r"\.exe\b)\b", re.I),
     "woks",
     "Desktop runs through the desktop wok — tag @windows/@mac."),
]


def wok_hint(text: str) -> dict | None:
    """The non-web wok this request belongs to, if any. Returned on a refusal
    so the caller is redirected rather than told 'no'."""
    for name, pat, doc, how in _WOKS:
        if pat.search(text or ""):
            return {"wok": name, "read_docs": doc, "how": how}
    return None


def classify(text: str) -> dict:
    """{intent, confidence, alternatives}. `confidence` is 'explicit' when a
    keyword matched and 'default' when nothing did — the SDLC default is
    generate, because a builder agent handing Noodle a task with no recognized
    verb is asking for a test, not for last run's report."""
    hits = []
    for name, pat in _INTENTS:
        m = pat.search(text or "")
        if m:
            hits.append((m.start(), name))
    if not hits:
        return {"intent": "generate", "confidence": "default",
                "alternatives": []}
    order = [name for name, _ in _INTENTS]
    hits.sort(key=lambda h: (h[0], order.index(h[1])))
    return {"intent": hits[0][1], "confidence": "explicit",
            "alternatives": [name for _, name in hits[1:]]}


def contract() -> dict:
    """The whole calling contract in one payload: what the five intents are,
    what shape a generate/update payload must take, and what comes back. A
    caller fetches this ONCE instead of discovering the grammar through
    rejections."""
    from noodle.repl import goal as goal_mod
    from noodle.repl import prompt_expander
    return {
        "command": "noodle task \"<text>\" --workspace <ws> --json",
        "intents": {
            "scan": "what is this repo — stack, how it serves itself, "
                    "OpenAPI endpoints, and what is still unanswered before "
                    "a test can be authored",
            "generate": "write a new test — needs a URL and numbered steps",
            "update": "rewrite an existing test — same payload, overwrites",
            "run": "execute the tests and serve both reports",
            "report": "serve the last run's Allure + RCA reports",
            "verdict": "the last run's pass/fail counts",
        },
        "default_intent": "generate",
        # Named up front so a cold caller never concludes Noodle is web-only
        # and declines the request — which is exactly what happened once.
        "woks": {
            "_": "Noodle is universal; these are independent, none a "
                 "sub-mode of another. The prompt/goal grammar below covers "
                 "web AND api (including both in one scenario) — the other "
                 "woks author with feature_content.",
            "web": "browser UI via Playwright, tag @web (default)",
            "api": "REST, browserless (no Playwright needed), tag @api — "
                   "prompt-authorable: `GET <url>` + `verify the response "
                   "status is 200`",
            "mobile": "native via Appium, tag @appium + @android/@ios — "
                      "read_docs('woks')",
            "desktop": "tag @windows/@mac/@visual — read_docs('woks')",
            "performance": "HTTP load + latency gates, tag @perf",
        },
        "prompt_grammar": prompt_expander.VERBS_HELP,
        "prompt_example": ("1. Go to the URL https://example.com\n"
                           "2. Search for \"office chair\"\n"
                           "3. Verify \"office chair\""),
        "goal_example": goal_mod.EXAMPLE,
        "goal_vocabulary": goal_mod.vocabulary(),
        "envelope": {
            "ok": "bool — the request succeeded end to end",
            "intent": "|".join(INTENTS),
            "confidence": "explicit | default",
            "need": "list — MISSING inputs; present only when ok is false",
            "next": "one sentence naming the next action, or null",
        },
        "notes": ("A refusal carries `need` and `next` — fill them and "
                  "re-send. Noodle never guesses a URL or a control name; "
                  "zero LLM calls here."),
    }


def _envelope(intent: str, cls: dict, workspace: str, **rest) -> dict:
    out = {"ok": False, "intent": intent, "confidence": cls["confidence"],
           "workspace": workspace}
    if cls.get("alternatives"):
        out["also_matched"] = cls["alternatives"]
    out.update(rest)
    out.setdefault("next", None)
    return out


def _needs(author: dict) -> tuple[list[str], str]:
    """(need, next) — derived from the author refusal, never guessed. Getting
    this wrong is worse than not answering: a caller told to "re-send with
    numbered steps" when the real problem is an existing file will rewrite a
    perfectly good prompt and hit the same wall."""
    err = (author.get("error") or "").lower()
    if "exists" in err and "overwrite" in err:
        return (["overwrite"],
                "the feature already exists — re-send as an update "
                "(\"update the test: 1. ...\") to replace it")
    need = []
    if "url" in err or "base_url" in err:
        need.append("base_url")
    if author.get("unresolved") or author.get("unrecognized_steps"):
        need.append("numbered_steps_in_grammar")
    if author.get("conflicts"):
        return (["resolve_contradictory_steps"],
                "two steps contradict each other — drop or reconcile the "
                "pair named in `conflicts` and re-send")
    return (need or ["numbered_steps_in_grammar"],
            "re-send with a URL and numbered steps that use the grammar, "
            "or send a goal object")


def _author(text: str, cls: dict, workspace: str, *, overwrite: bool,
            headless: bool, serve: bool) -> dict:
    from noodle.repl import core
    # The atomic author+run is headless-and-serve by construction — the SDLC
    # default, one call, one probe, one run. A caller asking for anything else
    # gets the two-step form: silently ignoring --headed would be exactly the
    # quiet lie the report-integrity work exists to prevent.
    atomic = headless and serve
    result = core.author_test(prompt=steps_of(text), run_after_author=atomic,
                              overwrite=overwrite, workspace=workspace)
    intent = cls["intent"]
    if not result.get("ok") and "author" not in result:
        # Compilation refused before any file was written — this is the
        # negotiation reply, the whole reason this module exists.
        err = result.get("error") or ""
        need, nxt = _needs(result)
        # A non-web ask never gets web grammar back — that reads as "Noodle
        # can't do this", which is how the capability got denied to a user in
        # the first place.
        if (hint := wok_hint(text)) and need != ["overwrite"]:
            out = _envelope(intent, cls, workspace, error=err,
                            wok=hint["wok"], need=["feature_content"],
                            how=hint["how"],
                            read_docs=hint["read_docs"],
                            next=f"supported — this is the {hint['wok']} wok, "
                                 "not web. Author it with feature_content "
                                 "using the steps named in `how`.")
            return out
        out = _envelope(intent, cls, workspace, error=err, need=need, next=nxt)
        # Empty lists and nulls are noise in an 8 KB budget — carry a key only
        # when it says something.
        for key in ("unresolved", "unrecognized_steps", "conflicts", "example"):
            if result.get(key):
                out[key] = result[key]
        # The engine's refusal already inlines the verb list most of the time;
        # repeating it as its own key would spend ~1 KB to say it twice.
        grammar = contract()["prompt_grammar"]
        if need != ["overwrite"] and grammar not in err:
            out["grammar"] = grammar
        return out
    author, run = result.get("author", result), result.get("run") or {}
    ok = bool(result.get("ok"))
    # NOOD_0214 — a blocked lap is not ok. The atomic path already said so
    # (ok:false + run:skipped); the non-atomic one reported ok:true beside
    # ready:false, so the envelope never reached the `need`/`next` branch that
    # explains what a blocker is and what to do with it.
    if author.get("ready") is False:
        ok = False
    if not atomic and author.get("ready"):
        run = core.run_and_report(author.get("feature"), workspace=workspace,
                                  headless=headless, retries=0,
                                  serve_reports=serve)
        ok = bool(run.get("ok"))
    out = _envelope(intent, cls, workspace, ok=ok,
                    feature=author.get("feature"),
                    ready=author.get("ready"),
                    blocking=author.get("blocking") or [])
    if author.get("missing_secret_keys"):
        out["missing_secret_keys"] = author["missing_secret_keys"]
        out["need"] = ["secret_values"]
        out["next"] = ("populate the listed keys in the app's gitignored "
                       "secrets.env, then re-send with intent run")
    if run.get("skipped"):
        out["run"] = {"skipped": run["skipped"]}
    elif run:
        out["run"] = {"passed": run.get("passed", 0),
                      "failed": run.get("failed", 0),
                      "verified": run.get("verified")}
        out["verdict"] = _verdict(run)
        out["reports"] = (run.get("served") or {}).get("urls") or []
        if run.get("rca_compact"):
            out["rca"] = run["rca_compact"]
    if not out["ok"] and not out.get("need"):
        out["need"] = ["fix_blocking"]
        # NOOD_0214 — "repair every item" read as error handling; the list is
        # discovery output, and saying so is what stops a re-probe.
        out["next"] = core.BLOCKING_IS_EVIDENCE
    return out


def _verdict(run: dict) -> str:
    """Green is failed == 0 AND verified — a pass carried by fuzzy healing is
    not a pass (docs/agent-playbook.md), and a router that flattened that
    distinction would report the exact lie the engine works to prevent."""
    if run.get("failed"):
        return "FAIL"
    if run.get("verified") is False:
        return "UNVERIFIED"
    return "PASS" if run.get("passed") else "NO_TESTS_RAN"


def route(text: str, *, workspace: str = ".", headless: bool = True,
          tag: str | None = None, serve: bool = True) -> dict:
    """Free text → the right command → one envelope. Never raises for a
    routing miss: an unroutable request comes back as ok:false with `need`."""
    cls = classify(text or "")
    intent = cls["intent"]
    from noodle.repl import core

    if intent == "scan":
        # NOOD_0192 — the repo question, answered from marker files. ok:true
        # with open `questions` is the normal reply: the scan succeeded AND
        # something is still missing before a test can be authored.
        from noodle import repo_scan
        rep = repo_scan.scan(workspace)
        if not rep.get("ok"):
            return _envelope(intent, cls, workspace, error=rep.get("error"),
                             need=["a_readable_repo_path"],
                             next="re-send with --workspace <repo dir>")
        return _envelope(intent, cls, workspace, ok=True,
                         **{k: v for k, v in rep.items()
                            if k not in ("ok", "root")},
                         repo=rep["root"],
                         need=["answers_to_questions"] if rep["questions"]
                         else [])

    if intent in ("generate", "update"):
        return _author(text, cls, workspace, overwrite=(intent == "update"),
                       headless=headless, serve=serve)

    if intent == "run":
        run = core.run_and_report(tag=tag, workspace=workspace,
                                  headless=headless, retries=0,
                                  serve_reports=serve)
        out = _envelope(intent, cls, workspace, ok=bool(run.get("ok")),
                        feature=run.get("target"),
                        run={"passed": run.get("passed", 0),
                             "failed": run.get("failed", 0),
                             "verified": run.get("verified")},
                        verdict=_verdict(run),
                        reports=(run.get("served") or {}).get("urls") or [])
        if run.get("error"):
            out["error"] = run["error"]
            out["need"] = ["a_runnable_test"]
            out["next"] = ("author a test first — re-send with "
                           "'write a test that ...' and numbered steps")
        if run.get("rca_compact"):
            out["rca"] = run["rca_compact"]
        return out

    if intent == "report":
        served = core.serve_report(workspace=workspace)
        out = _envelope(intent, cls, workspace, ok=bool(served.get("ok")),
                        reports=served.get("urls") or [])
        if served.get("ok"):
            out["rca"] = core.rca(workspace, compact=True)
        else:
            out["error"] = served.get("error")
            out["need"] = ["a_completed_run"]
            out["next"] = "run the tests first — re-send with 'run the tests'"
        return out

    # verdict
    last = core.last_result(workspace)
    ran = (last.get("passed", 0) or 0) + (last.get("failed", 0) or 0)
    out = _envelope(intent, cls, workspace, ok=ran > 0,
                    run={"passed": last.get("passed", 0),
                         "failed": last.get("failed", 0),
                         "verified": last.get("verified")},
                    verdict=_verdict(last))
    if not ran:
        out["error"] = "no completed run in this workspace"
        out["need"] = ["a_completed_run"]
        out["next"] = "run the tests first — re-send with 'run the tests'"
    return out
