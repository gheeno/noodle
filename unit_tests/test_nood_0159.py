"""NOOD_0159 — the instruction budget ledger.

The byte ceilings on always-on instruction surfaces used to be asserted in
seven per-ticket test files (0117/0126/0127/0128/0130/0131/0147), with
duplicate pins at different values and per-file accounting comments. They now
live in ONE place — noodle/instruction_budget.py — and this file is the only
enforcement point. A failure prints the whole ledger, so the editor sees
every surface's headroom, not one opaque number.
"""
from noodle import instruction_budget
from noodle.mcp import server


def test_every_surface_fits_its_ceiling():
    over = [r for r in instruction_budget.ledger()
            if r["used"] is not None and r["used"] > r["cap"]]
    assert not over, (
        "surface over budget — move the guidance to a docs/ section "
        "(surfaces route, docs carry; llm-performance §8) or raise the cap "
        "in noodle/instruction_budget.py with a CHANGELOG note.\n"
        + instruction_budget.format_ledger())


def test_ledger_reads_every_surface_in_a_repo_checkout():
    # A surface the ledger can't read is a surface nothing guards. In the
    # repo (where CI runs) every file source must resolve.
    rows = instruction_budget.ledger()
    assert {r["surface"] for r in rows} == set(instruction_budget.CEILINGS)
    unread = [r["surface"] for r in rows if r["used"] is None]
    assert not unread, f"ledger cannot read: {unread}"


def test_format_ledger_shows_headroom_per_surface():
    out = instruction_budget.format_ledger()
    assert "headroom" in out
    assert all(r["surface"] in out for r in instruction_budget.ledger())


def test_doc_index_carries_retrieval_cost():
    # Google's modern-web-guidance surfaces tokenCount per search hit so the
    # agent knows what a retrieval spends before making it; our index does
    # the same in bytes.
    out = server.read_docs()
    assert out["docs"], out
    for d in out["docs"]:
        assert {"name", "summary", "bytes", "sections"} <= set(d), d
        assert d["bytes"] > 0


def test_doc_query_hits_name_their_section():
    # A query hit must be retrievable in one follow-up call — doc + section,
    # no extra index round trip.
    out = server.read_docs(query="steps dictionary")
    assert out["hits"], out
    assert all(h.get("section") for h in out["hits"])
