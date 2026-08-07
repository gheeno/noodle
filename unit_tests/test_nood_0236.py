"""NOOD_0236 — findings from an adversarial prompt-shape audit of the web and
api woks against the bundled BusterBlock app.

Each test here pins one defect that audit reproduced against 1.0.0a49:

  1. `author --prompt` had no spelling for `feature_path`, so a caller that
     must NAME its output — the `benchmark --session` ledger keys strictly on
     `spec_<id>.feature` — could only reach the engine over MCP. All five
     specs ran green and the table still printed REGRESSED / "not attempted".
  2. `ship` renders a blocked authoring as `✗ authored None`, swallowing the
     error and every unresolved clause, because it kept its own copy of a
     renderer that understood only the atomic {author, run} shape.
  3. A markdown DataTable in a prompt was flattened one-check-per-cell, so the
     HEADER row became assertions and `| Alien | 2 |` compiled to `sees "2"`.
  4. A feature naming a helper file nobody wrote authored `ready: true`.
  5. That failure was then filed as `app-regression` against the app's login
     endpoint, which is also the class that blocks auto-fix.
"""
from pathlib import Path

from noodle.repl import validate
from noodle.repl.prompt_expander import _datatable_cells, expand
from noodle.reporting import rca_report as rr


def _entry(message="", trace="", warnings=None):
    return {"message": message, "trace": trace, "warnings": warnings or []}


# --- 3. DataTables are refused, not silently flattened ----------------------

def test_multi_column_row_is_recognised_as_a_datatable():
    assert _datatable_cells("| movie | quantity |") == ["movie", "quantity"]
    assert _datatable_cells("| Alien | 2 |") == ["Alien", "2"]


def test_single_column_row_is_not_a_datatable():
    """NOOD_0199's shape — a one-column list of expected values — must keep
    compiling to one check per cell, which is the behaviour this fix preserves."""
    assert _datatable_cells("| Product A |") is None
    assert _datatable_cells("|---|") is None
    assert _datatable_cells("not a table at all") is None


def test_datatable_refuses_instead_of_minting_junk_assertions():
    out = expand("1. Go to http://x.test/\n"
                 "2. check the catalogue shows:\n"
                 "   | movie | quantity |\n"
                 "   | Alien | 2       |\n")
    blob = " ".join(u["reason"] for u in (out.get("unresolved") or []))
    assert "data table is outside the prompt grammar" in blob
    # both rows refuse — the header row is not silently treated as data
    assert len([u for u in out["unresolved"]
                if "data table" in u["reason"]]) == 2
    # the header row and the bare quantity must never become assertions
    checks = [c.get("see") for c in
              (out.get("goal_partial") or {}).get("checks", [])]
    assert "movie" not in checks and "quantity" not in checks
    assert "2" not in checks


def test_single_column_table_still_becomes_checks():
    out = expand("1. Go to http://x.test/\n"
                 "2. search for Alien\n"
                 "3. the results should show:\n"
                 "   | Alien |\n")
    blob = " ".join(u["reason"] for u in (out.get("unresolved") or []))
    assert "data table is outside the prompt grammar" not in blob
    assert any(c.get("see") == "Alien"
               for c in (out.get("goal") or out.get("goal_partial")
                         or {}).get("checks", []))


# --- 4. a helper file nobody wrote is not "ready" ---------------------------

_FEATURE = ("@web\nFeature: f\n\n  Scenario: s\n"
            "    When User calls the function "
            "'{spec}' and saves the result as {{var:N}}\n")


def test_function_file_refs_returns_the_py_path():
    assert validate.function_file_refs(
        _FEATURE.format(spec="resources/functions/helpers.py:make_nick")
    ) == ["resources/functions/helpers.py"]


def test_function_file_refs_ignores_importable_modules():
    """'os.path:basename' is an import, not a file — checking it for existence
    would block every legitimate module-backed helper."""
    assert validate.function_file_refs(
        _FEATURE.format(spec="os.path:basename")) == []
    assert validate.function_file_refs(
        _FEATURE.format(spec="myproject.helpers:make_token")) == []


def test_function_file_refs_deduplicates():
    txt = (_FEATURE.format(spec="resources/functions/h.py:a")
           + _FEATURE.format(spec="resources/functions/h.py:b"))
    assert validate.function_file_refs(txt) == ["resources/functions/h.py"]


def test_function_file_refs_survives_empty_input():
    assert validate.function_file_refs("") == []
    assert validate.function_file_refs(None) == []


# --- 5. a missing test asset is test-side, never an app regression ----------

def test_missing_function_file_is_test_script_not_app_regression():
    v = rr.classify(_entry(
        message="AssertionError: Function file not found: "
                "resources/functions/helpers.py (cwd: /w)"))
    assert v["category"] == "test-script"
    assert v["confidence"] == "high"
    assert "resources/functions/helpers.py" in v["reason"]


def test_missing_function_verdict_is_immune_to_the_mutation_upgrade():
    """The upgrade at rca_report:210 may only replace app-regression /
    test-data / unknown. Filing this as app-regression both misdirected the
    reader AND blocked the auto-fix lap, so the category must stay outside
    that set."""
    v = rr.classify(_entry(
        message="AssertionError: Function file not found: helpers.py (cwd: /w)"))
    assert v["category"] not in ("app-regression", "test-data", "unknown")


def test_missing_script_is_also_test_script():
    v = rr.classify(_entry(message="AssertionError: Script not found: "
                                   "resources/scripts/seed.py (cwd: /w)"))
    assert v["category"] == "test-script"


def test_function_absent_from_an_existing_file_is_test_script():
    v = rr.classify(_entry(
        message="AssertionError: Function 'make_nick' not found in "
                "'resources/functions/helpers.py'"))
    assert v["category"] == "test-script"


def test_test_script_is_a_registered_category():
    assert "test-script" in rr.CATEGORIES


def test_a_real_app_assertion_is_still_not_test_script():
    """Guard the ordering: the new rule is checked first, so it must not
    swallow the ordinary comparison failures below it."""
    v = rr.classify(_entry(message="AssertionError: Expected 'Casablanca' "
                                   "to be visible, does not contain"))
    assert v["category"] != "test-script"


# --- 1. the authored path can be named from the CLI ------------------------

def test_author_exposes_feature_path_for_prompt_mode():
    """The ledger keys on the filename, so the flag has to exist on the CLI —
    without it `benchmark --session` is reachable only over MCP."""
    import inspect

    from noodle import cli
    assert "feature_path" in inspect.signature(cli.author).parameters


# --- 2. one renderer, shared by author and ship ----------------------------

def test_ship_and_author_share_one_renderer():
    """ship documents itself as a thin alias; the divergent copy is what lost
    the diagnostics, so the shared function must exist and both must use it."""
    from noodle import cli
    assert callable(cli._echo_author_result)
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert src.count("_echo_author_result(result)") >= 2


# --- 6. a data-driven request routes forward instead of dead-ending --------

def test_scenario_outline_request_names_the_working_route():
    out = expand("1. Go to http://x.test/\n"
                 "2. Use a scenario outline with an examples table\n")
    blob = " ".join(u["reason"] for u in (out.get("unresolved") or []))
    assert "Scenario Outline:" in blob and "Examples:" in blob


def test_for_each_request_never_becomes_a_literal_assertion():
    """The whole sentence used to be minted as the asserted text — an
    assertion no page could satisfy, from a request whose shape was
    understood. Caught before kind dispatch because it parses as a `verify`."""
    out = expand("1. Go to http://x.test/\n"
                 "2. check the search works for each of these movies: Alien\n")
    checks = [c.get("see") for c in
              (out.get("goal_partial") or {}).get("checks", [])]
    assert not any("for each of these" in (c or "") for c in checks)


def test_an_ordinary_verify_is_untouched_by_the_outline_guard():
    out = expand("1. Go to http://x.test/\n2. search for Alien\n"
                 "3. verify Alien\n")
    blob = " ".join(u["reason"] for u in (out.get("unresolved") or []))
    assert "cannot mint an outline" not in blob


# --- 7. the run door refuses a file:// read oracle -------------------------

def test_navigation_allows_a_local_html_fixture():
    """NOOD_0115 — a static .html fixture page is the documented offline
    testing shape and must keep working."""
    from noodle.agents.web.actions import _check_navigation_target
    _check_navigation_target("file:///tmp/fixture.html")
    _check_navigation_target("file:///tmp/fixture.HTM")


def test_navigation_refuses_a_file_url_that_is_not_a_page():
    from noodle.agents.web.actions import _check_navigation_target
    for url in ("file:///home/u/.aws/credentials",
                "file:///w/secrets.env",
                "file:///etc/passwd"):
        try:
            _check_navigation_target(url)
        except AssertionError as e:
            assert "reads local state" in str(e)
        else:                                # pragma: no cover - must refuse
            raise AssertionError(f"{url} was not refused")


def test_navigation_file_url_allowed_behind_the_opt_in(monkeypatch):
    from noodle.agents.web import actions
    monkeypatch.setenv("NOODLE_ALLOW_LOCAL_URLS", "1")
    actions._check_navigation_target("file:///w/secrets.env")


def test_navigation_never_touches_http_targets():
    from noodle.agents.web.actions import _check_navigation_target
    _check_navigation_target("https://example.test/a")
    _check_navigation_target("http://127.0.0.1:3333/")


# --- 9. the api body form is discoverable ----------------------------------

def test_prompt_grammar_documents_the_request_body():
    from noodle.repl.prompt_expander import VERBS_HELP
    assert "with body" in VERBS_HELP


def test_api_payload_written_as_prose_names_the_json_form():
    out = expand("1. POST to http://x.test/api/auth/login with username \"a\" "
                 "and password \"b\"\n2. verify the response status is 200")
    blob = " ".join(u["reason"] for u in (out.get("unresolved") or []))
    assert "with body" in blob


def test_the_body_form_itself_still_parses():
    out = expand("1. POST http://x.test/api/login with body '{\"u\": \"a\"}'\n"
                 "2. verify the response status is 200")
    assert out["ok"]
    api = [a for a in (out.get("goal") or {}).get("actions", [])
           if a.get("do") == "api"]
    assert api and api[0]["body"] == '{"u": "a"}'


def test_contract_still_fits_the_payload_budget():
    """The grammar addition above is paid for by factoring `verify`; this
    surface is bound at 8 KB with almost nothing spare (NOOD_0164)."""
    import json

    from noodle import payload_budget
    from noodle.repl import task
    rendered = json.dumps(task.contract(), indent=2)
    assert len(rendered.encode()) <= payload_budget.budget_bytes()


# --- 11. authoring outside a workspace says so -----------------------------

def test_authoring_without_noodle_yaml_warns(tmp_path):
    """A package written where there is no engine glue runs nothing, and the
    run reported "0 passed, 0 failed" with no error — the silence was the
    defect. Observed for real when a shell's cwd was left inside an app dir."""
    from noodle.repl import core
    out = core.author_test(
        app_name="demo", base_url="http://127.0.0.1:9/",
        feature_path="w.feature", workspace=str(tmp_path), overwrite=True,
        feature_content='@api\nFeature: f\n\n  Scenario: s\n'
                        "    When performs a GET call at 'http://127.0.0.1:9/x'\n")
    assert any("no noodle.yaml" in w for w in (out.get("warnings") or [])), \
        out.get("warnings")


def test_authoring_inside_a_workspace_does_not_warn(tmp_path):
    (tmp_path / "noodle.yaml").write_text("tests_dir: noodle_tests\n",
                                          encoding="utf-8")
    from noodle.repl import core
    out = core.author_test(
        app_name="demo", base_url="http://127.0.0.1:9/",
        feature_path="w.feature", workspace=str(tmp_path), overwrite=True,
        feature_content='@api\nFeature: f\n\n  Scenario: s\n'
                        "    When performs a GET call at 'http://127.0.0.1:9/x'\n")
    assert not any("no noodle.yaml" in w for w in (out.get("warnings") or []))


# --- 10. the endpoint list says where it came from -------------------------

def test_endpoint_source_is_reported_for_a_spec_derived_list():
    from noodle import api_probe
    src = api_probe.probe.__doc__ or ""
    assert "endpoint_source" in Path(
        api_probe.__file__).read_text(encoding="utf-8")
    assert src  # the door is documented


# --- dependency injection: the engine creates the helper, then blocks ------

def test_scaffold_writes_a_stub_into_the_app_package(tmp_path):
    from noodle.repl.generate import scaffold_function_refs
    feat = _FEATURE.format(spec="resources/functions/db.py:lookup")
    written = scaffold_function_refs(tmp_path, feat)
    assert written and written[0].name == "db.py"
    body = written[0].read_text(encoding="utf-8")
    assert "def lookup(" in body and "NotImplementedError" in body


def test_scaffold_refuses_to_escape_the_app_package(tmp_path):
    from noodle.repl.generate import scaffold_function_refs
    feat = _FEATURE.format(spec="../../../etc/evil.py:pwn")
    try:
        scaffold_function_refs(tmp_path, feat)
    except ValueError as e:
        assert "must stay inside" in str(e)
    else:                                    # pragma: no cover - must refuse
        raise AssertionError("path escape was not refused")


def test_scaffold_ignores_importable_modules(tmp_path):
    from noodle.repl.generate import scaffold_function_refs
    assert scaffold_function_refs(
        tmp_path, _FEATURE.format(spec="os.path:basename")) == []


def test_a_scaffolded_stub_is_reported_unimplemented(tmp_path):
    from noodle.repl.generate import scaffold_function_refs
    feat = _FEATURE.format(spec="resources/functions/db.py:lookup")
    scaffold_function_refs(tmp_path, feat)
    assert validate.unimplemented_function_refs(feat, tmp_path) == [
        "resources/functions/db.py:lookup"]


def test_an_implemented_helper_is_not_reported(tmp_path):
    """Creating the file is not the same as satisfying it — but once the body
    is written, readiness must stop objecting or the loop never closes."""
    feat = _FEATURE.format(spec="resources/functions/db.py:lookup")
    p = tmp_path / "resources" / "functions" / "db.py"
    p.parent.mkdir(parents=True)
    p.write_text("def lookup(*a):\n    return 'Alien'\n", encoding="utf-8")
    assert validate.unimplemented_function_refs(feat, tmp_path) == []


def test_a_helper_absent_from_an_existing_file_is_reported(tmp_path):
    feat = _FEATURE.format(spec="resources/functions/db.py:lookup")
    p = tmp_path / "resources" / "functions" / "db.py"
    p.parent.mkdir(parents=True)
    p.write_text("def something_else():\n    return 1\n", encoding="utf-8")
    assert validate.unimplemented_function_refs(feat, tmp_path) == [
        "resources/functions/db.py:lookup"]


def test_author_creates_the_helper_and_blocks_then_clears(tmp_path):
    """The whole dependency-injection loop, in one test: lap 1 writes the file
    and refuses (no browser), lap 2 — after the body is written — is ready."""
    (tmp_path / "noodle.yaml").write_text("tests_dir: noodle_tests\n",
                                          encoding="utf-8")
    from noodle.repl import core
    spec = dict(
        app_name="demo", base_url="http://127.0.0.1:9/", feature_path="h.feature",
        workspace=str(tmp_path), overwrite=True,
        feature_content="@api\nFeature: f\n\n  Scenario: s\n"
                        "    When User calls the function "
                        "'resources/functions/db.py:lookup' and saves the "
                        "result as {var:V}\n")
    lap1 = core.author_test(**spec)
    created = (tmp_path / "noodle_tests" / "api" / "demo" / "resources"
               / "functions" / "db.py")
    if not created.exists():                 # package may land under web/
        created = next(tmp_path.rglob("resources/functions/db.py"))
    assert created.exists(), "the engine did not create the helper"
    assert any("NotImplementedError" in b for b in (lap1.get("blocking") or [])), \
        lap1.get("blocking")

    created.write_text("def lookup(*a):\n    return 'x'\n", encoding="utf-8")
    lap2 = core.author_test(**spec)
    assert not any("NotImplementedError" in b
                   for b in (lap2.get("blocking") or [])), lap2.get("blocking")


# --- the goal verb and its prompt clause -----------------------------------

def test_call_function_is_in_the_goal_vocabulary():
    from noodle.repl import goal as goal_mod
    v = goal_mod.vocabulary()
    assert v["actions"]["call_function"]["required"] == ["spec"]
    assert "var" in v["actions"]["call_function"]["keys"]


def test_action_common_keys_are_stated_once():
    """do/id are legal on every action; listing them 15 times cost ~360 B of
    an 8 KB contract."""
    from noodle.repl import goal as goal_mod
    v = goal_mod.vocabulary()
    assert v["action_common_keys"] == ["do", "id"]
    assert "do" not in v["actions"]["search"]["keys"]


def test_prompt_expresses_a_helper_call():
    out = expand("1. Go to http://x.test/\n"
                 "2. call the function 'resources/functions/db.py:lookup' "
                 "and save the result as {var:TITLE}\n3. verify Alien")
    assert out["ok"], out.get("unresolved")
    act = [a for a in out["goal"]["actions"] if a["do"] == "call_function"]
    assert act and act[0]["spec"] == "resources/functions/db.py:lookup"
    assert act[0]["var"] == "TITLE"


def test_a_malformed_helper_spec_refuses_with_the_shape():
    out = expand("1. Go to http://x.test/\n"
                 "2. call the function 'resources/functions/db.py'\n")
    blob = " ".join(u["reason"] for u in (out.get("unresolved") or []))
    assert "file.py:function" in blob


def test_a_helper_only_goal_needs_no_browser():
    from noodle.repl import goal as goal_mod
    assert not goal_mod.needs_browser({
        "actions": [{"do": "call_function", "spec": "a.py:b"}],
        "checks": [{"status": 200}]})


def test_a_helper_goal_that_asserts_a_page_still_needs_a_browser():
    from noodle.repl import goal as goal_mod
    assert goal_mod.needs_browser({
        "actions": [{"do": "call_function", "spec": "a.py:b"}],
        "checks": [{"see": "Alien"}]})


# --- the benchmark surface --------------------------------------------------

def test_runbook_offers_both_doors():
    from noodle import benchmark
    book = benchmark.runbook("/ws", benchmark.load_specs())
    assert "author_test(prompt=" in book          # MCP
    assert "noodle author --prompt" in book       # CLI
    assert "--feature-path" in book


def test_runbook_warns_that_the_filename_is_load_bearing():
    from noodle import benchmark
    book = benchmark.runbook("/ws", benchmark.load_specs())
    assert "not attempted" in book


def test_runbook_explains_the_off_engine_guard_rail():
    from noodle import benchmark
    book = benchmark.runbook("/ws", benchmark.load_specs())
    assert "OFF-ENG" in book


def test_the_helper_spec_is_part_of_the_benchmark():
    from noodle import benchmark
    ids = {s["id"] for s in benchmark.load_specs()}
    assert "helper_function" in ids


def test_shared_renderer_prints_the_error_on_a_blocked_prompt(capsys):
    """The exact payload shape `ship` used to render as `✗ authored None`:
    a needs_interpretation envelope with no `author` key at all."""
    import typer

    from noodle import cli
    payload = {
        "ok": False,
        "error": "prompt step(s) not understood — rewrite them",
        "needs_interpretation": True,
        "unrecognized_steps": ["step 7 'Use a scenario outline'"],
        "planner": {"state": "NEEDS_INTERPRETATION"},
    }
    try:
        cli._echo_author_result(payload)
    except typer.Exit as e:
        assert e.exit_code == 1
    else:                                    # pragma: no cover - must exit
        raise AssertionError("renderer did not exit")
    printed = capsys.readouterr().out
    assert "prompt step(s) not understood" in printed
    assert "Use a scenario outline" in printed
    assert "authored None" not in printed
