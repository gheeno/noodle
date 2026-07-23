"""NOOD_0124 — `noodle report serve` is the only sanctioned way to host the
Allure + RCA reports. Every always-on agent surface (the copilot/vscode
digest, both skill cards, the AGENTS.md floor, and the canonical playbook)
must name it AND forbid the raw alternatives agents reach for — `allure
serve`, `python -m http.server`, a `file://` open — that break Allure's SPA
or drop the RCA."""
from noodle import cli
from unit_tests.test_nood_0110 import REPO

_SURFACES = {
    "copilot/vscode digest": (REPO / ".github" / "copilot-instructions.md").read_text(),
    ".claude skill card": (REPO / ".claude" / "skills" / "noodle" / "SKILL.md").read_text(),
    ".copilot skill card": (REPO / ".copilot" / "skills" / "noodle" / "SKILL.md").read_text(),
    "agent-playbook": (REPO / "docs" / "agent-playbook.md").read_text(),
    "AGENTS.md floor": cli._AGENTS_MD,
}


def test_report_serve_is_the_only_hosting_path_on_every_surface():
    for name, text in _SURFACES.items():
        low = " ".join(text.split()).lower()
        assert "report serve" in low, f"{name}: lost the `noodle report serve` instruction"
        assert "allure serve" in low, f"{name}: doesn't forbid `allure serve`"
        assert "http.server" in low, f"{name}: doesn't forbid `python -m http.server`"
