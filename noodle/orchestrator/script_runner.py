"""Run an external script or shell command as a Gherkin step.

Lets a scenario invoke a user-authored script in any language — seed a database
with Python, run a Java jar, call a shell tool — and use its result downstream.
The interpreter is inferred from the file extension; stdout is captured and
returned (the runner stores it in `SCRIPT_OUTPUT`). A non-zero exit fails the
step, so a broken setup script fails the test loudly.

Trust boundary: feature files are trusted code (like step definitions), so
run_command uses a shell. Don't drive these steps from untrusted input.
"""
import importlib
import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path

# Extension → command prefix. .py uses THIS interpreter (venv-aware); others use
# the conventional launcher on PATH. ponytail: extend this dict for new languages.
_INTERPRETERS = {
    ".py": [sys.executable],
    ".js": ["node"],
    ".mjs": ["node"],
    ".jar": ["java", "-jar"],
    ".sh": ["bash"],
    ".rb": ["ruby"],
    ".pl": ["perl"],
}


def command_for(path: str, args: str | None) -> list[str]:
    """Build the argv for a script path, inferring the interpreter by extension.
    Unknown extension → run the file directly (must be executable)."""
    ext = Path(path).suffix.lower()
    prefix = _INTERPRETERS.get(ext, [])
    extra = shlex.split(args) if args else []
    return [*prefix, path, *extra]


def _run(cmd, *, shell: bool, label: str) -> str:
    timeout = int(os.getenv("NOODLE_SCRIPT_TIMEOUT", "60"))
    result = subprocess.run(
        cmd, shell=shell, capture_output=True, text=True,
        cwd=Path.cwd(), timeout=timeout, env=os.environ,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{label} failed (exit {result.returncode})\n"
            f"  stderr: {result.stderr.strip()}\n"
            f"  stdout: {result.stdout.strip()}"
        )
    return result.stdout.strip()


def run_script(path: str, args: str | None = None) -> str:
    if not Path(path).exists():
        raise AssertionError(f"Script not found: {path} (cwd: {Path.cwd()})")
    return _run(command_for(path, args), shell=False, label=f"Script '{path}'")


def run_command(command: str) -> str:
    return _run(command, shell=True, label=f"Command '{command}'")


def call_function(spec: str, args: str | None = None, raw: bool = False):
    """Import and call a Python function in-process, returning its real return
    value (unlike run_script, which only captures stdout of a subprocess).

    spec is 'pkg.module:function' or 'path/to/file.py:function'. String args
    are shlex-split and passed positionally; raw=True ("with raw arg '...'")
    passes the whole string as ONE argument instead — captured page text like
    '93 results' must not become two args (NOOD_0115). Any exception becomes
    an AssertionError so a broken helper fails the step loudly."""
    target, sep, name = spec.rpartition(":")
    if not sep:
        raise AssertionError(
            f"Function spec must be 'module:function' or 'file.py:function', got: {spec!r}")
    if target.endswith(".py"):
        if not Path(target).exists():
            raise AssertionError(f"Function file not found: {target} (cwd: {Path.cwd()})")
        mspec = importlib.util.spec_from_file_location(Path(target).stem, target)
        module = importlib.util.module_from_spec(mspec)
        mspec.loader.exec_module(module)
    else:
        if str(Path.cwd()) not in sys.path:  # project-local modules importable
            sys.path.insert(0, str(Path.cwd()))
        module = importlib.import_module(target)
    fn = getattr(module, name, None)
    if not callable(fn):
        raise AssertionError(f"Function {name!r} not found in {target!r}")
    argv = [args] if (raw and args is not None) else (shlex.split(args) if args else [])
    try:
        return fn(*argv)
    except AssertionError:
        raise
    except Exception as e:
        hint = ""
        if (isinstance(e, TypeError) and len(argv) > 1
                and "positional argument" in str(e)):
            hint = (f"\n  Hint: the args string was shlex-split into "
                    f"{len(argv)} tokens {argv!r} — to pass the whole value "
                    f"as ONE string, write \"with raw arg '...'\" in the step")
        raise AssertionError(f"Function {spec!r} raised {type(e).__name__}: {e}{hint}") from e
