"""NOOD_0188 — the deferred NOOD_0187 batch: goal-grammar growth, probe
caching, richer mocking/HAR, driven websockets, binary downloads, pixel masks,
and an artifact cross-check on the regression benchmark's self-report."""
import json
import zipfile
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noodle import docparse
from noodle import regression as R
from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe
from noodle.repl import validate as v
from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject
from noodle.resolver.step_resolver import VALID_TYPES


def _resolves(sentence):
    return match(normalize_phrasing(normalize_subject(sentence)))


# --- goal grammar growth ----------------------------------------------------

NEW_ACTIONS = ("check", "uncheck", "hover", "upload", "press_key",
               "pick_date", "go_back")


def test_new_actions_are_in_the_vocabulary_with_required_keys():
    vocab = goal_mod.vocabulary()
    for do in NEW_ACTIONS:
        assert do in vocab["actions"], do
        # every action must have a _ACTION_REQUIRED entry or validate() KeyErrors
        assert do in goal_mod._ACTION_REQUIRED, do
        assert set(vocab["actions"][do]["required"]) <= set(vocab["actions"][do]["keys"])


def test_new_check_kinds_are_in_the_vocabulary():
    vocab = goal_mod.vocabulary()
    assert "not_see" in vocab["check_keys"]
    assert "url_contains" in vocab["check_keys"]
    # the notes prose is generated from the kinds tuple, so it cannot drift
    for kind in goal_mod._CHECK_KINDS:
        assert kind in vocab["notes"]


def test_every_new_action_compiles_to_a_step_the_runtime_matches():
    goal = {"scenario": "form flow",
            "actions": [{"do": "check", "target": "Terms"},
                        {"do": "uncheck", "target": "Newsletter"},
                        {"do": "hover", "target": "Products"},
                        {"do": "upload", "target": "Avatar", "file": "id.png"},
                        {"do": "pick_date", "target": "Departure",
                         "date": "March 15, 2026"},
                        {"do": "press_key", "key": "Enter"},
                        {"do": "go_back"}],
            "checks": [{"see": "Welcome"}, {"not_see": "Error"},
                       {"url_contains": "/done"}]}
    assert goal_mod.validate(goal) == []
    feat, _ = goal_mod.compile_goal(
        goal, {"controls": {}, "proven": {}, "runtime_asserted": []}, "BASE")
    chk = v.check_feature(feat)
    assert chk["error"] is None
    assert v.unmatched(chk) == []          # the whole point: no phantom steps


def test_action_step_raises_instead_of_silently_compiling_a_select():
    # NOOD_0188 — the tail used to fall through to `select`, so a table-only
    # addition produced a wrong-but-matching step no test could catch.
    with pytest.raises(ValueError, match="no branch for do="):
        goal_mod._action_step({"do": "brand_new_verb", "target": "x"}, "x")


def test_check_step_raises_instead_of_silently_compiling_an_any_of():
    with pytest.raises(ValueError, match="no branch for check"):
        goal_mod._check_step({"totally_new_kind": "x"})


def test_new_verbs_are_targeted_so_they_earn_a_pom_entry():
    for do in ("check", "uncheck", "hover", "upload", "pick_date"):
        assert do in goal_mod._TARGETED_ACTIONS, do
    # go_back/press_key name no control
    assert "go_back" not in goal_mod._TARGETED_ACTIONS


def test_state_changing_verbs_gate_the_probe_but_hover_does_not():
    gate = goal_mod._runtime_gate
    for do in ("check", "uncheck", "upload", "press_key", "pick_date", "go_back"):
        assert gate([{"do": do}]) == 0, do        # probe must not perform it
    # hover only reveals — the probe may perform it, like a reveal click
    assert gate([{"do": "hover", "target": "Menu"}]) is None


def test_hover_counts_as_a_reveal_prerequisite():
    actions = [{"do": "hover", "target": "Products"},
               {"do": "click", "target": "Shoes"}]
    assert goal_mod._reveal_click_before(actions, actions[1], "Products") is True


def test_absence_and_url_checks_are_runtime_asserted_never_probe_proven():
    goal = {"scenario": "s", "actions": [{"do": "click", "target": "Go"}],
            "checks": [{"not_see": "Error"}, {"url_contains": "/done"}]}
    ev = goal_mod.evidence(goal, {"pages": [{"url": "http://x", "controls": [
        {"name": "Go", "selector": "#go", "role": "button"}]}]})
    runtime = " ".join(ev.get("runtime_asserted") or [])
    assert "Error" in runtime and "/done" in runtime
    # a probe snapshot can never prove an absence, so it must not block either
    assert not [b for b in ev["blocking"] if "Error" in b or "/done" in b]


def test_new_verbs_reach_the_goal_from_plain_english():
    exp = pe.expand(
        '1. Go to the URL https://example.com/signup\n'
        '2. Enter "a@b.com" in the Email field\n'
        '3. Check the "Terms" checkbox\n'
        '4. Uncheck the "Newsletter" checkbox\n'
        '5. Upload "id.png" to the Photo field\n'
        '6. Select "March 15, 2026" from the Birthday calendar\n'
        '7. Hover over the Help menu\n'
        '8. Press Enter\n'
        '9. Go back\n'
        '10. Verify "Welcome"\n'
        '11. Verify "Error" is not visible\n'
        '12. Verify the url contains /done')
    dos = [a["do"] for a in exp["goal"]["actions"]]
    assert dos == ["enter", "check", "uncheck", "upload", "pick_date",
                   "hover", "press_key", "go_back"]
    assert exp["goal"]["checks"] == [
        {"see": "Welcome"}, {"not_see": "Error"}, {"url_contains": "/done"}]
    assert exp["unresolved"] == []
    assert exp["translation_mode"] == "deterministic-fast-path"
    assert goal_mod.validate(exp["goal"]) == []


def test_check_verb_does_not_steal_an_ordinary_verify():
    # "check" is also the verify verb — only the checkbox noun claims it
    exp = pe.expand("1. Go to the URL https://x.com\n2. Check the total is 5")
    assert [a["do"] for a in exp["goal"]["actions"]] == []
    assert exp["goal"]["checks"] == [{"see": "the total is 5"}]


def test_press_verb_still_clicks_a_named_button():
    exp = pe.expand("1. Go to the URL https://x.com\n2. Press the Submit button")
    assert [(a["do"], a.get("target")) for a in exp["goal"]["actions"]] \
        == [("click", "Submit button")]


def test_select_from_calendar_is_a_date_not_a_dropdown():
    exp = pe.expand('1. Go to the URL https://x.com\n'
                    '2. Select "March 15, 2026" from the Departure calendar')
    a = exp["goal"]["actions"][0]
    assert a["do"] == "pick_date" and a["target"] == "Departure"


def test_targeted_verbs_all_gated_by_the_provenance_review():
    exp = pe.expand('1. Go to the URL https://x.com\n'
                    '2. Check the "Terms" checkbox')
    exp["goal"]["actions"][0]["target"] = "Invented Control"   # no source clause
    review = pe.review_contract(exp)
    assert review["ok"] is False
    assert any("no source clause" in p for p in review["problems"])


def test_localhost_and_port_urls_are_navigations_not_clicks():
    # NOOD_0188 — pre-existing: _URLISH required a dot and forbade a port, so
    # EVERY localhost:PORT prompt failed "no URL in the prompt" — the cheap
    # authoring path could not target the app you're developing.
    for url in ("http://localhost:3333", "http://127.0.0.1:8080/app"):
        exp = pe.expand(f"1. Go to the URL {url}\n2. Verify 'X'")
        assert exp.get("error") is None, url
        assert exp["goal"]["navigation"] == [url]
    # a dotless bare word must still be a CLICK, not a navigation
    exp = pe.expand("1. Go to the URL https://x.com\n2. Go to the cart\n3. Verify 'X'")
    assert [(a["do"], a.get("target")) for a in exp["goal"]["actions"]] \
        == [("click", "cart")]


def test_control_free_actions_survive_evidence_and_verify_honestly():
    # NOOD_0188 — press_key/go_back name no control. evidence() keyed the
    # generic path on a["target"] (KeyError mid-author), and intent_trace
    # reported them "missing", dragging intent_verified false for any goal
    # using one.
    goal = {"scenario": "s",
            "actions": [{"do": "enter", "target": "username", "value": "x"},
                        {"do": "press_key", "key": "Enter"},
                        {"do": "go_back"}],
            "checks": [{"see": "Home"}]}
    probe = {"pages": [{"url": "http://x", "headings": ["Home"], "controls": [
        {"name": "username", "selector": "#u", "role": "textbox"}]}]}
    ev = goal_mod.evidence(goal, probe)          # must not raise
    trace = goal_mod.intent_trace(goal, ev)
    control_free = [t for t in trace if t["node"] in ("actions[1]", "actions[2]")]
    assert len(control_free) == 2
    assert all(t["ok"] and t["evidence"] == "deterministic:no-control"
               for t in control_free), control_free


def test_fixture_mock_accepts_a_method():
    # found by live-running the new steps: `mocks GET '…' with the fixture …`
    # is the natural phrasing and didn't parse.
    m = _resolves("mocks GET '**/api/**' with the fixture 'orders.json'")
    assert m[0] == "mock_route"
    assert m[1]["method"] == "GET" and m[1]["fixture"] == "orders.json"
    # still optional
    assert _resolves("mocks '**/x' with the fixture 'o.json'")[1]["method"] is None


# --- probe cache ------------------------------------------------------------

def test_probe_cache_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("NOODLE_PROBE_CACHE_TTL", raising=False)
    from noodle.repl import core
    calls = []
    run = lambda: (calls.append(1), {"pages": []})[1]          # noqa: E731
    for _ in range(2):
        core._cached_probe(str(tmp_path), "http://x", "each", {}, "f.feature", run)
    assert len(calls) == 2, "a fresh page must be probed every author call"


def test_probe_cache_reuses_only_the_identical_transaction(tmp_path, monkeypatch):
    monkeypatch.setenv("NOODLE_PROBE_CACHE_TTL", "120")
    from noodle.repl import core
    calls = []
    run = lambda: (calls.append(1), {"pages": []})[1]          # noqa: E731
    args = {"search": "shoes"}
    _, h1 = core._cached_probe(str(tmp_path), "http://x", "each", args, "f", run)
    _, h2 = core._cached_probe(str(tmp_path), "http://x", "each", args, "f", run)
    assert (h1, h2) == (False, True) and len(calls) == 1
    # a different feature must never be served this feature's evidence
    _, h3 = core._cached_probe(str(tmp_path), "http://x", "each", args, "other", run)
    # a changed transaction re-probes
    _, h4 = core._cached_probe(str(tmp_path), "http://x", "each",
                               {"search": "boots"}, "f", run)
    assert (h3, h4) == (False, False) and len(calls) == 3


def test_discover_probe_is_never_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("NOODLE_PROBE_CACHE_TTL", "120")
    from noodle.repl import core
    calls = []
    run = lambda: (calls.append(1), {"pages": []})[1]          # noqa: E731
    for _ in range(2):
        core._cached_probe(str(tmp_path), "http://x", "each",
                           {"discover": True}, "f", run)
    assert len(calls) == 2


# --- binary downloads -------------------------------------------------------

def _xlsx(path, rows=3):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/sharedStrings.xml",
                   "<sst><si><t>Widget</t></si><si><t>Total 42</t></si></sst>")
        z.writestr("xl/worksheets/sheet1.xml",
                   "<worksheet><sheetData>"
                   + "".join(f'<row r="{i}"><c/></row>' for i in range(1, rows + 1))
                   + "</sheetData></worksheet>")
    return path


def test_xlsx_text_and_row_count(tmp_path):
    x = _xlsx(tmp_path / "export.xlsx", rows=4)
    assert "Widget" in docparse.text_of(x) and "Total 42" in docparse.text_of(x)
    assert docparse.row_count(x) == 3           # header excluded, as for CSV


def test_pdf_text_plain_and_compressed(tmp_path):
    body = b"BT /F1 12 Tf (Order 12345 total $9.99) Tj ET"
    plain = tmp_path / "a.pdf"
    plain.write_bytes(b"%PDF-1.4\nstream\n" + body + b"\nendstream")
    comp = tmp_path / "b.pdf"
    comp.write_bytes(b"%PDF-1.4\nstream\n" + zlib.compress(body) + b"\nendstream")
    for p in (plain, comp):
        assert "Order 12345" in docparse.text_of(p)


def test_download_content_assertion_reads_a_binary_export(tmp_path):
    from noodle.agents.web import actions
    x = _xlsx(tmp_path / "report.xlsx", rows=3)
    dl = SimpleNamespace(suggested_filename="report.xlsx", path=lambda: str(x))
    page = SimpleNamespace(url="http://x/", wait_for_timeout=lambda ms: None)
    actions.assert_download_content(page, [dl], needle="Widget")
    actions.assert_download_content(page, [dl], rows=2)
    with pytest.raises(AssertionError, match="does not contain"):
        actions.assert_download_content(page, [dl], needle="Nope")


def test_csv_download_still_counts_lines(tmp_path):
    from noodle.agents.web import actions
    c = tmp_path / "e.csv"
    c.write_text("h1,h2\na,1\nb,2\n", encoding="utf-8")
    dl = SimpleNamespace(suggested_filename="e.csv", path=lambda: str(c))
    page = SimpleNamespace(url="http://x/", wait_for_timeout=lambda ms: None)
    actions.assert_download_content(page, [dl], needle="a,1", rows=2)


# --- richer mocking + HAR ---------------------------------------------------

@pytest.mark.parametrize("sentence,expect", [
    ("mocks '**/api/o' with the fixture 'orders.json'", "mock_route"),
    ("mocks POST '**/api/cart' with status 500", "mock_route"),
    ("mocks '**/api/s' with status 200 after 3 seconds", "mock_route"),
    ("replays 'checkout.har'", "route_from_har"),
    ("records 'checkout.har' for '**/api/**'", "route_from_har"),
    ("mocks the websocket", "mock_websocket"),
    ("the server sends '{\"p\": 1}'", "send_ws_message"),
    ("the page should have sent 'subscribe'", "assert_ws_sent"),
    ("the screen should match the baseline ignoring the clock", "pixel_baseline"),
])
def test_new_web_steps_resolve(sentence, expect):
    m = _resolves(sentence)
    assert m is not None and m[0] == expect, sentence


def test_new_action_types_are_mirrored_in_valid_types():
    for t in ("route_from_har", "mock_websocket", "send_ws_message",
              "assert_ws_sent"):
        assert t in VALID_TYPES, t


def test_mock_route_carries_method_delay_and_fixture():
    assert _resolves("mocks POST '**/x' with status 500")[1]["method"] == "POST"
    assert _resolves("mocks '**/x' with status 200 after 2 seconds")[1]["delay_ms"] == 2000
    assert _resolves("mocks '**/x' with the fixture 'o.json'")[1]["fixture"] == "o.json"


def test_mock_route_content_type_is_guessed_not_always_json():
    from noodle.agents.web import actions
    assert actions._content_type_for('{"a":1}') == "application/json"
    assert actions._content_type_for("<div>hi</div>") == "text/html"
    assert actions._content_type_for("a,b\n1,2") == "text/plain"
    assert actions._content_type_for("", Path("x.csv")) == "text/csv"


def test_mock_route_only_intercepts_the_named_method():
    from noodle.agents.web import actions
    page, handler = MagicMock(), {}
    page.route.side_effect = lambda url, fn: handler.setdefault("fn", fn)
    actions.mock_route(page, "**/x", 500, method="POST")
    get = MagicMock(request=SimpleNamespace(method="GET"))
    handler["fn"](get)
    get.fallback.assert_called_once()          # untouched verb passes through
    get.fulfill.assert_not_called()
    post = MagicMock(request=SimpleNamespace(method="POST"))
    handler["fn"](post)
    post.fulfill.assert_called_once()


def test_har_replay_refuses_a_missing_recording():
    from noodle.agents.web import actions
    with pytest.raises(AssertionError, match="HAR file not found"):
        actions.route_from_har(MagicMock(), "/nope/missing.har")


# --- driven websockets ------------------------------------------------------

def test_send_ws_requires_the_mock_to_be_armed_first():
    from noodle.agents.web import actions
    page = SimpleNamespace(url="http://x/")
    with pytest.raises(AssertionError, match="arm|mock"):
        actions.send_ws_message(page, "hi")


def test_mock_websocket_records_page_frames_and_sends_server_frames():
    from noodle.agents.web import actions
    page, captured = MagicMock(), {}
    page.route_web_socket.side_effect = lambda url, fn: captured.setdefault("fn", fn)
    actions.mock_websocket(page)
    ws = MagicMock()
    handlers = {}
    ws.on_message.side_effect = lambda fn: handlers.setdefault("msg", fn)
    captured["fn"](ws)                       # the page opens its socket
    handlers["msg"]("subscribe cart")        # the page sends a frame
    actions.assert_ws_sent(page, "subscribe")
    actions.send_ws_message(page, '{"price": 42}')
    ws.send.assert_called_once_with('{"price": 42}')
    with pytest.raises(AssertionError, match="never sent"):
        actions.assert_ws_sent(page, "nothing-like-this")


# --- pixel masks ------------------------------------------------------------

def test_ignore_tail_parses_into_separate_locators():
    m = _resolves("the screen should match the baseline ignoring "
                  "the clock, the avatar and the ad banner")
    assert m[1]["ignore"] == ["clock", "avatar", "ad banner"]


def test_masked_regions_are_painted_before_comparison():
    from PIL import Image

    from noodle.agents.web import actions
    img = Image.new("RGB", (40, 40), (255, 255, 255))
    page = MagicMock()
    loc = MagicMock()
    loc.bounding_box.return_value = {"x": 0, "y": 0, "width": 20, "height": 20}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "find", lambda p, phrase: loc)
        masked = actions._mask_regions(img, page, ["clock"])
    assert masked.getpixel((5, 5)) == (0, 0, 0)        # inside the mask
    assert masked.getpixel((30, 30)) == (255, 255, 255)  # outside, untouched


def test_unresolvable_mask_is_skipped_not_fatal():
    from PIL import Image

    from noodle.agents.web import actions
    img = Image.new("RGB", (10, 10), (255, 255, 255))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(actions, "find", lambda p, phrase: None)
        out = actions._mask_regions(img, MagicMock(), ["ghost"])
    assert out.getpixel((5, 5)) == (255, 255, 255)


# --- benchmark artifact cross-check -----------------------------------------

def _claims_green():
    """One healthy claim per CANONICAL case — sized off PROMPTS so adding a
    test case to the benchmark can't silently turn these into "not all cases
    measured" regressions (NOOD_0191 added tc3 and did exactly that)."""
    return {"test_cases": [
        {"id": p["id"], "elapsed_s": 20, "run_s": 14, "corrections": 0,
         "green": True, "verified": True} for p in R.PROMPTS]}


def test_score_stays_pure_without_a_workspace():
    v_ = R.score(_claims_green())
    assert v_["verdict"] == "PASS" and v_["audit"] == []


def test_audit_flags_a_self_report_contradicted_by_the_run(tmp_path):
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "last_run.json").write_text(json.dumps(
        {"passed": 1, "failed": 2, "verified": False,
         "unverified_reasons": ["healed via dom-scan"]}), encoding="utf-8")
    verdict = R.score(_claims_green(), str(tmp_path))
    assert verdict["verdict"] == "REGRESSED"
    assert any("claim green" in r for r in verdict["regressions"])
    assert any("verified" in r for r in verdict["regressions"])


def test_audit_passes_when_artifacts_agree(tmp_path):
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "last_run.json").write_text(json.dumps(
        {"passed": 2, "failed": 0, "verified": True}), encoding="utf-8")
    assert R.score(_claims_green(), str(tmp_path))["verdict"] == "PASS"


def test_audit_flags_a_green_claim_over_zero_scenarios(tmp_path):
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "last_run.json").write_text(json.dumps(
        {"passed": 0, "failed": 0, "verified": True}), encoding="utf-8")
    verdict = R.score(_claims_green(), str(tmp_path))
    assert verdict["verdict"] == "REGRESSED"
    assert any("0 passed" in r for r in verdict["regressions"])


def test_missing_artifacts_are_advisory_not_a_regression(tmp_path):
    verdict = R.score(_claims_green(), str(tmp_path))
    assert verdict["verdict"] == "PASS"
    assert any("unverifiable" in a for a in verdict["audit"])

# NOOD_0190 deleted the host-aware cost unit (aic/tokens ceilings, host_unit(),
# cost_basis): the benchmark measures the ENGINE, not the driving agent's bill.
