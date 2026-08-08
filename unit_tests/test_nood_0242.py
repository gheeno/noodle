"""NOOD_0242 — the install docs work in the reader's shell, not just in zsh.

The defect this guards: every venv-activate line in the docs was the POSIX
one, `source .venv/bin/activate`. fish cannot parse that file — it fails with
`Unsupported use of '='` and, critically, leaves the venv INACTIVE, so the
next step (`uv pip install -e ".[all]"`) errors with `No virtual environment
found` and the reader blames Noodle rather than the activate line. uv writes
a per-shell script (`activate.fish`, `.csh`, `.nu`, `.ps1`) beside the POSIX
one, so the cure was always on disk — only the docs never named it.

Two invariants, one per layer:

* docs — a file that tells the reader to source the POSIX activate script
  must also name the fish script, so no reader is left with a line that
  errors in their shell;
* engine — `install_check` keeps a fish-specific PATH fix, because
  `uv tool update-shell` (the Option B route) is what a fish user runs when
  `noodle` resolves in one terminal and not their own, and fish's fallback
  (`fish_add_path`) is not the POSIX `export PATH=` one.

Option B itself needs no fish path and is deliberately NOT asserted here:
uv writes each shell's profile in that shell's own syntax already
(verified: `fish_add_path` into `~/.config/fish/config.fish`, `export PATH`
into BOTH `~/.bashrc` and `~/.bash_profile`, `~/.zshenv` for zsh).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from noodle import install_check

REPO = Path(__file__).resolve().parent.parent

POSIX_ACTIVATE = "source .venv/bin/activate"
FISH_ACTIVATE = "activate.fish"


def _docs_with_posix_activate() -> list[Path]:
    """Every tracked markdown doc that instructs a `source .venv/bin/activate`.

    Discovered, not hard-coded: a NEW doc added later with the POSIX line and
    no fish counterpart is exactly the regression this test exists to catch.
    CHANGELOG.md is excluded — it is an append-only historical record, not
    instructions anyone follows.
    """
    hits = []
    for p in sorted(REPO.rglob("*.md")):
        rel = p.relative_to(REPO)
        if rel.parts[0] in {".venv", "node_modules", ".git"} or rel.name == "CHANGELOG.md":
            continue
        # `.fish`/`.ps1` suffixes are themselves matches for the prefix, so
        # test the bare line: "activate" followed by end-of-line or non-suffix.
        text = p.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            i = line.find(POSIX_ACTIVATE)
            if i >= 0 and not line[i + len(POSIX_ACTIVATE):].startswith("."):
                hits.append(p)
                break
    return hits


def test_docs_exist_to_check() -> None:
    """Guard the guard: if the discovery above silently matches nothing, the
    per-file test below would vacuously pass forever."""
    assert _docs_with_posix_activate(), (
        "no doc matched the POSIX activate line — the discovery pattern in "
        "this test has drifted from how the docs spell it")


@pytest.mark.parametrize("doc", _docs_with_posix_activate(),
                         ids=lambda p: str(p.relative_to(REPO)))
def test_posix_activate_is_paired_with_fish(doc: Path) -> None:
    """A doc that prescribes the POSIX activate must also name the fish one."""
    text = doc.read_text(encoding="utf-8", errors="replace")
    assert FISH_ACTIVATE in text, (
        f"{doc.relative_to(REPO)} tells the reader to run `{POSIX_ACTIVATE}` "
        f"but never names `{FISH_ACTIVATE}`. In fish that line fails with "
        "\"Unsupported use of '='\" and leaves the venv inactive, so the next "
        "`uv pip install` errors with \"No virtual environment found\". Add "
        "the fish variant beside the POSIX one (NOOD_0242).")


def test_fish_gets_its_own_path_fix() -> None:
    """`uv tool update-shell` is the Option B cure, but a fish user who needs
    the manual fallback cannot use the POSIX `export PATH=` form."""
    assert "fish" in install_check._PATH_FIX, (
        "install_check._PATH_FIX lost its fish entry — `noodle doctor`'s "
        "install.login-shell warning would hand a fish user a fix that "
        "doesn't work in their shell (NOOD_0192/NOOD_0242)")
    assert "fish_add_path" in install_check._PATH_FIX["fish"]


def test_login_shell_report_names_the_fish_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever the report can or can't check, it always carries a fix line
    matched to $SHELL — the fish user's terminal is the one that can't find
    `noodle`, so the fix has to be fish's."""
    monkeypatch.setenv("SHELL", "/opt/homebrew/bin/fish")
    r = install_check.login_shell_report(timeout=5.0)
    assert r["shell"] == "fish"
    assert "fish_add_path" in r["fix"]


def test_login_shell_report_is_not_a_failure_when_unanswerable(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """No $SHELL (Windows, some CI) must read as "not checked", never as a
    broken install — and must still print a usable default fix."""
    monkeypatch.delenv("SHELL", raising=False)
    r = install_check.login_shell_report(timeout=5.0)
    assert r["checked"] is False
    assert r["fix"] == install_check._PATH_FIX_DEFAULT
