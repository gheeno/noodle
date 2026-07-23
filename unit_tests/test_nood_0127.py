"""NOOD_0127 — speed up test dev on slow sites, cut agent AIC.

A multi-iteration authoring session on a gated, slow flow burns runs on two
avoidable traps: blind-guessing decorated assertion text across several slow
runs, and asserting against a page the probe could never cross an auth/config
gate to reach. It can also silently ship a green test that drops the asked-for
verification. These contracts pin the discipline that prevents all three, plus
the dev-loop timeout floor. No browser, no LLM."""
from noodle import cli
from unit_tests.test_nood_0110 import REPO


def test_agents_md_carries_the_four_discipline_fixes():
    a = cli._AGENTS_MD.lower()
    # A1 — loosest stable substring, never brute-force decorated text
    assert "smallest stable" in a
    # A2 — probe can't cross gates; budget one exploratory run
    assert "exploratory run" in a and "gate" in a
    # A4 — token hygiene: grep the line, vision costs ~10x text
    assert "grep" in a and "vision costs" in a
    # A5 — never silently drop the asked-for verify
    assert "silently drop" in a


def test_env_stub_offers_a_ci_safe_dev_loop_floor():
    env = cli._ENV_STUB_BASE
    assert "dev-loop floor" in env
    assert "#NOODLE_FIND_TIMEOUT=25000" in env      # commented = CI-safe by default
    assert "#NOODLE_WAIT_EXTENSION=15000" in env
    assert "false-fail" in env                       # the restore-for-CI warning


def test_agent_playbook_carries_the_mcp_only_rules():
    # read_docs('agent-playbook') is the MCP fallback for agents driving from
    # outside the workspace (they never read the scaffolded AGENTS.md), so A2
    # (gated-page exploratory run) and A5 (never drop the verify) must live here
    # too — A1/A4 already did. Guards against the two surfaces drifting apart.
    pb = (REPO / "docs/agent-playbook.md").read_text().lower()
    assert "exploratory run" in pb and "gate" in pb        # A2
    assert "silently drop the asked-for check" in pb       # A5
