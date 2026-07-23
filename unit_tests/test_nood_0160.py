"""NOOD_0160 — empty AGENTS.md into the bookshelf.

The first content move under the router rule (llm-performance §8): the
elaboration that duplicated playbook content left cli._AGENTS_MD
(5628 → 4589 bytes), and `noodle docs` gives CLI-only agents the same
retrieval path MCP agents get from read_docs.
"""
from typer.testing import CliRunner

from noodle import cli, instruction_budget

runner = CliRunner()


def test_agents_md_keeps_a_headroom_floor():
    # The NOOD_0159 woe was a surface at 4 bytes of headroom — every edit a
    # byte-fight. The cap (5632) is the hard ceiling; this floor is the
    # tripwire that forces the next content-move BEFORE the fights resume.
    row = next(r for r in instruction_budget.ledger()
               if r["surface"].startswith("agents-md"))
    assert row["headroom"] >= 512, (
        f"AGENTS.md headroom down to {row['headroom']} bytes — move content "
        "to a playbook section (surfaces route, docs carry), don't spend "
        "the last of the buffer.")


def test_agents_md_routes_to_the_cli_docs_command():
    # An MCP-blocked agent must still reach the moved content.
    assert "noodle docs" in cli._AGENTS_MD


def test_docs_command_lists_index_with_costs():
    r = runner.invoke(cli.app, ["docs"])
    assert r.exit_code == 0, r.output
    assert "agent-playbook" in r.output and '"bytes"' in r.output


def test_docs_command_fetches_one_section():
    big = runner.invoke(cli.app, ["docs", "agent-playbook"])
    assert big.exit_code == 0 and '"sections"' in big.output  # index, not 57 KB
    sec = runner.invoke(cli.app, ["docs", "agent-playbook", "-s", "North star"])
    assert sec.exit_code == 0, sec.output
    assert "North star" in sec.output and '"sections"' not in sec.output


def test_docs_command_greps_and_fails_on_unknown_doc():
    q = runner.invoke(cli.app, ["docs", "-q", "steps dictionary"])
    assert q.exit_code == 0 and '"section"' in q.output
    missing = runner.invoke(cli.app, ["docs", "no-such-doc"])
    assert missing.exit_code == 1
