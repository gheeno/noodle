"""NOOD_0133 — which noodle build is actually running, and will `git pull`
update it?

The incident this guards against: a stale NON-editable copy in some Python's
site-packages shadowed the editable dev clone on PATH, so months of already-
written fixes (TLS-ignore default, 2-min nav budget) never executed and the
drift was invisible. Everything here is read-only pure Python, identical on
macOS and Windows 11: diagnose and print the exact cure — never auto-remove.
"""
import json
import os
import re
import sys
from pathlib import Path


def package_dir() -> Path:
    import noodle
    return Path(noodle.__file__).resolve().parent


def dist_version() -> str:
    try:
        from importlib.metadata import version
        return version("noodle")
    except Exception:
        return "unknown"


def source_version() -> str | None:
    """The version the checked-out SOURCE declares (pyproject.toml sitting
    next to the package), or None when not running from a checkout. NOOD_0156
    — dist_version() reads the .dist-info written at install time, which goes
    stale the moment a git pull bumps pyproject; the source file is the truth
    a checkout user expects `--version` to reflect."""
    try:
        import tomllib
        with (package_dir().parent / "pyproject.toml").open("rb") as fh:
            v = tomllib.load(fh).get("project", {}).get("version")
        return str(v) if v else None
    except Exception:
        return None


def version_report() -> dict:
    """{"installed", "source", "mismatch"} — one derivation for --version,
    doctor and diagnostics, so they can't disagree. mismatch=True means the
    installed metadata lags the checkout (cure: `noodle update` from the
    clone — an editable install keeps CODE current across pulls, but the
    recorded version only refreshes on reinstall)."""
    installed, source = dist_version(), source_version()
    return {"installed": installed, "source": source,
            "mismatch": bool(source) and installed != "unknown"
            and source != installed}


def is_editable() -> bool | None:
    """True/False from the dist-info direct_url.json (PEP 610); None when
    unknowable — e.g. running straight from a source tree with no install."""
    try:
        from importlib.metadata import distribution
        raw = distribution("noodle").read_text("direct_url.json")
        return bool(json.loads(raw).get("dir_info", {}).get("editable")) if raw else None
    except Exception:
        return None


def git_sha() -> str | None:
    """Short SHA read straight from .git — pure Python, no subprocess: works
    without a git binary, same on Windows, and every `noodle run` prints it
    without shelling out. Returns None for an installed copy (no repo)."""
    try:
        d = package_dir()
        for parent in (d, *d.parents):
            git = parent / ".git"
            if git.is_file():  # worktree pointer: "gitdir: <path>"
                target = git.read_text().split("gitdir:", 1)[1].strip()
                git = (parent / target).resolve()
            if not git.is_dir():
                continue
            head = (git / "HEAD").read_text().strip()
            if not head.startswith("ref: "):
                return head[:7]
            ref = git / head[5:]
            if ref.exists():
                return ref.read_text().strip()[:7]
            packed = git / "packed-refs"
            if packed.is_file():
                for line in packed.read_text().splitlines():
                    if line.endswith(head[5:]):
                        return line.split()[0][:7]
            return None
    except Exception:
        return None
    return None


def shims_on_path() -> list[str]:
    """Every `noodle` launcher on PATH, in PATH order — the first one wins.
    Deduped on resolved target so a symlink and its target count once."""
    exts = (".exe", ".cmd", ".bat", "") if os.name == "nt" else ("",)
    seen, hits = set(), []
    for d in os.get_exec_path():
        for ext in exts:
            p = Path(d) / f"noodle{ext}"
            try:
                if not (p.is_file() and os.access(p, os.X_OK)):
                    continue
                real = str(p.resolve())
            except OSError:
                continue
            if real not in seen:
                seen.add(real)
                hits.append(str(p))
    return hits


def _under_uv_tools(p: Path) -> bool:
    return {"uv", "tools"} <= set(p.parts)


def _is_uv_tool_install() -> bool:
    """Whether this build lives in a uv tool venv (.../uv/tools/noodle/...).

    Decided from the INTERPRETER first, not the package dir: an EDITABLE uv
    tool install — the one docs/llm-install.md prescribes — leaves the package
    in the clone, so package_dir() names the checkout and the old package-dir-
    only test silently fell through to the pip branch. uv's venvs ship no pip,
    so that produced "No module named pip". sys.executable still points into
    uv's tool venv in both the editable and copied cases."""
    return _under_uv_tools(Path(sys.executable)) or _under_uv_tools(package_dir())


def _has_pip() -> bool:
    import importlib.util
    return importlib.util.find_spec("pip") is not None


def reinstall_cmd() -> str:
    """Human-pasteable form of reinstall_argv(), run from the clone. Derived
    from the argv so the command doctor PRINTS can never drift from the one
    `noodle update` RUNS."""
    return " ".join(reinstall_argv()) + "  # from the clone"


def clone_root() -> Path | None:
    """The engine checkout this build should track (NOOD_0156). An editable
    install already points AT the clone, so its package dir names it; a
    non-editable one lives in site-packages, and then the only honest
    candidate is a checkout at or above the cwd — the tester is standing in
    the clone they just pulled. None when neither exists."""
    from noodle.doctor import _is_engine_root
    candidates = [package_dir().parent]
    try:
        cwd = Path.cwd()
        candidates += [cwd, *cwd.parents]
    except OSError:  # cwd deleted out from under us
        pass
    return next((c for c in candidates if _is_engine_root(c)), None)


def reinstall_argv() -> list[str]:
    """reinstall_cmd() without a shell — run it WITH cwd set to clone_root(),
    which is why the target stays the relative ".[all]" the docs print.
    `--force`/pip's own replace do the uninstall step in one move, so a failed
    resolve leaves the working install in place instead of a removed one.
    sys.executable, never a bare `pip`: the interpreter that imported THIS
    noodle is by definition the environment whose copy must be replaced."""
    if _is_uv_tool_install():
        return ["uv", "tool", "install", "--force", "--editable", ".[all]",
                "--with-executables-from", "playwright"]
    if _has_pip():
        return [sys.executable, "-m", "pip", "install", "-e", ".[all]"]
    # A uv-created project venv: no pip inside it either, but uv installs into
    # any interpreter by path — so target the SAME environment, not uv's default.
    return ["uv", "pip", "install", "--python", sys.executable, "-e", ".[all]"]


def build_line() -> str:
    """One line naming the running build — version, editable-or-copy, path,
    SHA. Printed by `noodle --version` and atop every `noodle run`."""
    ed = is_editable()
    kind = {True: "editable", False: "NON-EDITABLE COPY", None: "source tree"}[ed]
    sha = git_sha()
    return f"noodle {dist_version()} ({kind}) {package_dir()}" + (f" @ {sha}" if sha else "")


_BUILD_LINE_RE = re.compile(
    r"noodle (\S+) \((editable|NON-EDITABLE COPY|source tree)\) "
    r"(.+?)(?: @ ([0-9a-fA-F]{4,40}))?\s*$")


def parse_build_line(text: str) -> dict | None:
    """Parse a `noodle --version` build line back into structured provenance:
    {"version", "kind", "root", "sha"|None}. None when no line matches — the
    caller decides how loudly unknown provenance matters (NOOD_0138)."""
    for line in text.splitlines():
        m = _BUILD_LINE_RE.search(line)
        if m:
            return {"version": m.group(1), "kind": m.group(2),
                    "root": m.group(3), "sha": m.group(4)}
    return None


def probe_launcher(path: str, timeout: float = 10.0) -> dict:
    """Ask one PATH launcher which build it executes: `[path, "--version"]`,
    shell=False, short timeout, bounded output, no env changes (NOOD_0138).
    Returns parsed provenance or {"error": ...} — never raises."""
    import subprocess
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True,
                             timeout=timeout).stdout or ""
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout}s"}
    except OSError as e:
        return {"error": f"could not execute: {e.__class__.__name__}"}
    return parse_build_line(out) or {"error": f"unrecognized --version output: {out[:200]!r}"}


def warn_if_stale(echo) -> None:
    """One loud non-fatal line from `noodle init`/`noodle run` when the
    running build can't track the source. Diagnose and continue."""
    if is_editable() is False:
        echo(f"  ⚠️ non-editable noodle install at {package_dir()} — a git pull/"
             "re-clone won't update the CLI. Run `noodle update` (or `noodle doctor` "
             "for the full diagnosis).")
        return
    # NOOD_0156 — editable keeps the CODE current, but a pull that changed
    # pyproject (version, or worse, DEPENDENCIES) needs a reinstall to land.
    # The recorded version lagging the source is the visible proxy for both.
    vr = version_report()
    if vr["mismatch"]:
        echo(f"  ⚠️ this checkout is noodle {vr['source']} but the install "
             f"recorded {vr['installed']} — dependencies may also be stale. "
             "Run `noodle update`.")
