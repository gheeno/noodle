"""NOOD_0215 — observability is noodle commands only, on every agent door.

The defect this guards: a session in the ENGINE checkout reached for
`curl -o /dev/null -w '%{http_code}'` and `sed -n '120,320p'`. Neither was a
discipline failure. The no-shell rule lived only in the scaffolded workspace
`AGENTS.md` and in the skill cards, so an engine-repo session never had it in
context (leak 1); and a dead origin surfaced as raw
`net::ERR_CONNECTION_REFUSED`, which reads like a Noodle bug rather than "your
app isn't running" — so curl was how the agent got an answer it trusted
(leak 2).

Three things are asserted here: every always-on agent surface carries the
rule, a dead origin comes back in words, and the schema dump is one
unpageable line.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from noodle.cli import app

REPO = Path(__file__).resolve().parent.parent


def _flat(text: str) -> str:
    """Whitespace-collapsed, so a rule that wrapped across lines in one door
    and not another still matches. Line breaks are layout, not content."""
    return re.sub(r"\s+", " ", text)


# --- leak 1: the rule reaches every door -------------------------------------

def _doors() -> dict[str, str]:
    from noodle.cli import _AGENTS_MD
    return {
        "AGENTS.md (engine root)": (REPO / "AGENTS.md").read_text(encoding="utf-8"),
        "CLAUDE.md": (REPO / "CLAUDE.md").read_text(encoding="utf-8"),
        ".github/copilot-instructions.md":
            (REPO / ".github/copilot-instructions.md").read_text(encoding="utf-8"),
        ".claude/skills/noodle/SKILL.md":
            (REPO / ".claude/skills/noodle/SKILL.md").read_text(encoding="utf-8"),
        ".copilot/skills/noodle/SKILL.md":
            (REPO / ".copilot/skills/noodle/SKILL.md").read_text(encoding="utf-8"),
        "cli._AGENTS_MD (scaffolded workspace)": _AGENTS_MD,
    }


@pytest.mark.parametrize("door", sorted(_doors()))
def test_every_agent_door_bans_curl_by_name(door):
    """Naming the reflex is what makes the rule land. A door that only says
    "use noodle commands" leaves the agent to decide whether curl counts —
    and nine calls deep, it decides it doesn't."""
    assert "curl" in _flat(_doors()[door]), (
        f"{door} never mentions curl, so an agent reading only that file has "
        "no reason to think a quick reachability check is off-limits")


@pytest.mark.parametrize("door", sorted(_doors()))
def test_every_agent_door_carries_the_escape_hatch(door):
    """The rule that generalises past today's command list: an observability
    need noodle can't serve is a gap to report, not a shell to improvise in.
    Without it, the next unlisted question produces the next curl."""
    assert "engine gap to report" in _flat(_doors()[door]), (
        f"{door} tells the agent what to use but not what to do when nothing "
        "listed fits — that silence is where the shell command comes back")


def test_doors_stay_inside_the_instruction_budget():
    """Adding a rule to a capped surface must cost bytes somewhere, not raise
    the cap (NOOD_0159)."""
    from noodle import instruction_budget as ib
    over = [r for r in ib.ledger()
            if r["headroom"] is not None and r["headroom"] < 0]
    assert not over, ib.format_ledger()


# --- leak 2: a dead origin comes back in words -------------------------------

def test_connection_refused_reads_as_a_dead_app_not_a_broken_probe():
    """The exact string the session saw. `net::ERR_CONNECTION_REFUSED at
    http://localhost:5173` is why curl got run: it does not say which of the
    two possible things went wrong."""
    from noodle.agents.web.probe import _origin_error
    msg = _origin_error("http://localhost:5173", Exception(
        "Page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:5173/"))
    assert "not reachable" in msg
    assert "nothing is listening" in msg
    assert "http://localhost:5173" in msg


@pytest.mark.parametrize("marker", ["ERR_NAME_NOT_RESOLVED",
                                    "ERR_CONNECTION_TIMED_OUT",
                                    "ERR_INTERNET_DISCONNECTED"])
def test_the_other_origin_failures_are_named_too(marker):
    from noodle.agents.web.probe import _origin_error
    msg = _origin_error("http://host/", Exception(f"Page.goto: net::{marker}"))
    assert msg.startswith("http://host/ is not reachable")
    assert marker in msg


def test_an_unrecognised_failure_passes_through_verbatim():
    """A wrong guess about the cause is worse than a raw Playwright message —
    the translation table only speaks where it actually knows."""
    from noodle.agents.web.probe import _origin_error
    raw = "Page.goto: Timeout 15000ms exceeded"
    assert _origin_error("http://host/", Exception(raw)) == raw


def test_report_url_precheck_is_untouched():
    """NOOD_0166's `http_ok` contract predates this branch and must survive
    it — the report URLs are still pre-checked, so still no curl there."""
    from noodle.cli import _urls_http_ok
    assert _urls_http_ok(["http://127.0.0.1:1/"]) is False


# --- leak 3: the schema dump can't be paged ----------------------------------

def test_vocabulary_is_one_unpageable_line():
    """245 indented lines is what invited `sed -n '120,320p'`. One line means
    the whole schema arrives or nothing does."""
    r = CliRunner().invoke(app, ["author", "--vocabulary"])
    assert r.exit_code == 0
    body = r.stdout.strip()
    assert len(body.splitlines()) == 1, "vocabulary is pageable again"
    payload = json.loads(body)
    assert {"example", "vocabulary", "prompt_grammar"} <= set(payload)


def test_other_json_doors_still_indent():
    """`indent=None` is opt-in per call — a human-read `--json` payload must
    not have been flattened along with it."""
    r = CliRunner().invoke(app, ["doctor", "--json"])
    assert len(r.stdout.strip().splitlines()) > 1


def test_doctor_grew_no_new_flag():
    """NOOD_0215 considered a `doctor --url` reachability command and dropped
    it: the probe already had to load that page, so a second door would be a
    surface to learn, document and keep true for no new information."""
    r = CliRunner().invoke(app, ["doctor", "--url", "http://x/"])
    assert r.exit_code != 0


# --- the engine's own AGENTS.md is not a stray init artifact ------------------

def test_engine_agents_md_is_not_flagged_as_workspace_debris():
    """doctor's engine profile warns about an accidental `noodle init` at the
    root by looking for AGENTS.md. The engine now ships its own, and warning
    about it would make `noodle doctor` permanently red in this repo."""
    from noodle import doctor
    checks = doctor.engine_checks(REPO)
    artifacts = [c for c in checks if c.id == "engine.workspace-artifacts"]
    assert not artifacts, f"engine AGENTS.md flagged as debris: {artifacts}"


def test_a_real_scaffolded_agents_md_is_still_flagged(tmp_path):
    """...but the check it guards must still fire on the real thing."""
    from noodle import doctor
    from noodle.cli import _AGENTS_MD
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "noodle"\n')
    (tmp_path / "AGENTS.md").write_text(_AGENTS_MD, encoding="utf-8")
    checks = doctor.engine_checks(tmp_path)
    assert any(c.id == "engine.workspace-artifacts" for c in checks)
