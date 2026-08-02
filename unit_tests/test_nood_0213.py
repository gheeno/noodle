"""NOOD_0213 — the three frictions the 2026-08-01 agent-regression drill
measured on 1.0.0a27:

1. A goal blocked ONLY by an unprovable prose verify ("Suggestion search
   works") cost a hand-rebuilt goal: the blocked payload now carries
   `repair.goal`, ready to send back. The engine still never drops the check
   itself — NOOD_0212's guard stands.
2. The repair lap then re-asked for app_name/base_url/feature_path — values
   goal.navigation[0] already determines, by the same derivation the prompt
   path uses.
3. `build_line()` printed the install-time metadata version even when the
   executing code was the source tree ("noodle 1.0.0a11 ... @ <current sha>"),
   and the stale-metadata warning did not say WHICH environment was stale —
   so a reader who had updated their shell's env learned to ignore it.
"""
from noodle import install_check
from noodle.repl import core
from noodle.repl import goal as goal_mod
from noodle.repl import prompt_expander as pe

UNPROVABLE = ('check "Suggestion search works": no probed heading or control '
              "shows that text on the page it is scoped to — fix the text")
GOAL = {"scenario": "s", "navigation": ["https://www.shop.example/en.html"],
        "actions": [{"do": "suggest", "id": "s1", "term": "Vaccu",
                     "option": "Vaccum cleaner"}],
        "checks": [{"see": "BISSELL Vacuum"},
                   {"see": "Suggestion search works"}]}


# --- 1. repair.goal on an unprovable-see block --------------------------------

def test_repair_goal_drops_only_the_unprovable_see():
    rep = goal_mod.repair_goal(GOAL, [UNPROVABLE])
    assert rep["dropped_checks"] == ["Suggestion search works"]
    assert rep["goal"]["checks"] == [{"see": "BISSELL Vacuum"}]
    # everything else is carried verbatim — the caller resends it as-is
    assert rep["goal"]["actions"] == GOAL["actions"]
    assert rep["goal"]["navigation"] == GOAL["navigation"]


def test_no_repair_when_a_real_blocker_is_mixed_in():
    # the drop-offer is only honest when the unprovable text is the WHOLE
    # problem; alongside a control failure it would invite dropping a check
    # that might prove fine once the control is fixed
    assert goal_mod.repair_goal(
        GOAL, [UNPROVABLE, 'add_to "cart": no probed control mutates']) is None


def test_no_repair_that_would_leave_zero_checks():
    lone = {**GOAL, "checks": [{"see": "Suggestion search works"}]}
    assert goal_mod.repair_goal(lone, [UNPROVABLE]) is None


def test_blocked_author_payload_carries_repair(monkeypatch, tmp_path):
    """End-to-end through author_test: goal mode, no app/base/feature args,
    _author_test_impl stubbed to a blocked result."""
    seen = {}

    def fake_impl(**kw):
        seen.update(kw)
        return {"ok": True, "ready": False,
                "blocking": [UNPROVABLE]}

    monkeypatch.setattr(core, "_author_test_impl", fake_impl)
    out = core.author_test(goal=GOAL, workspace=str(tmp_path))
    assert out["blocking"] == [UNPROVABLE]
    # 2. the derivation: navigation[0] filled all three required params
    assert seen["base_url"] == "https://www.shop.example/en.html"
    assert seen["app_name"] == "shop_example"
    assert "features/" in seen["feature_path"]


# --- 2. goal-mode derivation ---------------------------------------------------

def test_app_from_url_strips_www_and_guards_ip_hosts():
    assert pe.app_from_url("https://www.shop.example/en.html") == "shop_example"
    assert pe.app_from_url("http://127.0.0.1:8080/x") == "app_127_0_0_1_8080"


def test_env_ref_navigation_does_not_derive():
    # an {env:} navigation entry is not a URL — the old error must remain
    g = {**GOAL, "navigation": ["{env:SHOP}"]}
    out = core.author_test(goal=g, workspace=".")
    assert out["ok"] is False and "required" in out["error"]


def test_cli_spec_gate_admits_a_goal_that_navigates(monkeypatch):
    """The --spec gate rejected app_name/base_url before author_test could
    derive them — the same friction at a second door."""
    from typer.testing import CliRunner

    from noodle.cli import app

    monkeypatch.setattr(core, "author_test",
                        lambda **kw: {"ok": True, "ready": True,
                                      "blocking": [], "warnings": []})
    r = CliRunner().invoke(app, ["author", "--json", "--spec",
                                 '{"goal": {"scenario": "s", "navigation": '
                                 '["https://www.shop.example/"], '
                                 '"checks": [{"see": "x"}]}}'])
    assert "missing required field" not in r.output
    assert r.exit_code == 0


def test_cli_spec_gate_still_rejects_an_underivable_spec():
    from typer.testing import CliRunner

    from noodle.cli import app
    r = CliRunner().invoke(app, ["author", "--json", "--spec",
                                 '{"goal": {"scenario": "s", "navigation": '
                                 '["{env:SHOP}"], "checks": [{"see": "x"}]}}'])
    assert r.exit_code == 2 and "missing required field" in r.output


# --- 4. duplicate navigation ---------------------------------------------------
#
# The drill's TC1 brief names its URL twice (a "base URL:" line and step 1), so
# the goal carried two identical navigation entries and the feature opened with
# TWO Givens against two env keys holding one value.

def test_consecutive_duplicate_navigation_collapses():
    g, notes = goal_mod.normalize(
        {**GOAL, "navigation": ["https://www.shop.example/en.html",
                                "https://www.shop.example/en.html"]})
    assert g["navigation"] == ["https://www.shop.example/en.html"]
    assert any("repeated entry" in n for n in notes)


def test_dedup_ignores_scheme_and_trailing_slash():
    g, _ = goal_mod.normalize(
        {**GOAL, "navigation": ["http://www.shop.example/",
                                "https://www.shop.example"]})
    assert len(g["navigation"]) == 1


def test_a_return_trip_is_not_a_duplicate():
    """A → B → A is a deliberate navigation, not a repeat."""
    navs = ["https://www.shop.example/", "https://www.shop.example/cart",
            "https://www.shop.example/"]
    g, notes = goal_mod.normalize({**GOAL, "navigation": navs})
    assert g["navigation"] == navs and not notes


def test_dedup_survives_dict_navigation_entries():
    g, _ = goal_mod.normalize(
        {**GOAL, "navigation": [{"url": "https://www.shop.example/en.html"},
                                {"url": "https://www.shop.example/en.html"}]})
    assert g["navigation"] == [{"url": "https://www.shop.example/en.html"}]


def test_one_env_key_for_a_twice_named_url():
    """The visible symptom: <APP> and <APP>_EN, one value, two Givens."""
    g, _ = goal_mod.normalize(
        {**GOAL, "navigation": ["https://www.shop.example/en.html",
                                "https://www.shop.example/en.html"]})
    pairs = goal_mod.navigation_env(g, "shop_example",
                                    base_url="https://www.shop.example/en.html")
    assert [k for k, _ in pairs] == ["SHOP_EXAMPLE"]


# --- 3. build_line names the running code's version ---------------------------

def test_build_line_prefers_source_version_for_editable(monkeypatch):
    monkeypatch.setattr(install_check, "is_editable", lambda: True)
    monkeypatch.setattr(install_check, "dist_version", lambda: "0.9.0a1")
    monkeypatch.setattr(install_check, "source_version", lambda: "0.9.0a5")
    monkeypatch.setattr(install_check, "git_sha", lambda: "abc1234")
    assert install_check.build_line().startswith("noodle 0.9.0a5 (editable)")


def test_build_line_keeps_dist_version_for_noneditable_copy(monkeypatch):
    # a copied install runs the code its metadata recorded — dist IS truth
    monkeypatch.setattr(install_check, "is_editable", lambda: False)
    monkeypatch.setattr(install_check, "dist_version", lambda: "0.9.0a1")
    monkeypatch.setattr(install_check, "source_version", lambda: "0.9.0a5")
    monkeypatch.setattr(install_check, "git_sha", lambda: None)
    assert "noodle 0.9.0a1 (NON-EDITABLE COPY)" in install_check.build_line()


def test_stale_warning_names_the_environment(monkeypatch):
    import sys
    monkeypatch.setattr(install_check, "is_editable", lambda: True)
    monkeypatch.setattr(install_check, "dist_version", lambda: "0.9.0a1")
    monkeypatch.setattr(install_check, "source_version", lambda: "0.9.0a5")
    out = []
    install_check.warn_if_stale(out.append)
    assert len(out) == 1 and sys.prefix in out[0]
    assert "noodle update" in out[0]
