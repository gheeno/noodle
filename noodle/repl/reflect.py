"""Run-failure self-repair (NOOD_0012 Phase 4).

repl.py calls this only for a feature the agent itself just created and ran
this session — never for a file someone asked to run by name — so it never
silently rewrites a hand-authored test.

One repair pass, same keep-only-if-it-helped discipline as
generate.generate_llm's step-vocabulary repair: read the failure already on
disk from the run that just happened, ask the model for a fix, re-run once,
and keep the fix only if it reduced the failure count. Otherwise the
original file is restored and a human looks at the report.
"""
import re
import subprocess
import sys
from pathlib import Path

from noodle.repl import validate
from noodle.reporting import paths as _paths
from noodle.reporting import summary

# NOOD_0177 — step shapes the pattern table dispatches to shell / script /
# in-process Python. A self-repair pass must never introduce one.
_EXEC_STEP_RE = re.compile(
    r"^\s*(?:Given|When|Then|And|But)\b.*\b"
    r"(runs?|executes?|calls?)\s+(?:the\s+)?(command|script|function)\b",
    re.IGNORECASE | re.MULTILINE)


def _forbidden_steps(text: str) -> str:
    hits = {f"{m.group(1)} {m.group(2)}".lower() for m in _EXEC_STEP_RE.finditer(text or "")}
    return ", ".join(sorted(hits))


def _run(feat_path: str, workspace: str) -> None:
    subprocess.run([sys.executable, "-m", "noodle.cli", "run", feat_path], cwd=workspace)


def try_fix(feat_path: Path, pom_path: Path, workspace: str) -> bool:
    """Returns True if a fix was written and kept, False if there was
    nothing to fix or the automatic fix didn't help."""
    from noodle.llm.client import ask
    from noodle.repl import generate, prompts

    results_dir = str(Path(workspace) / _paths.artifacts_root() / "allure-results")
    before = summary.collect(results_dir)
    if before["failed"] == 0 or not before["failures"]:
        return False

    failure = before["failures"][0]
    print(f"⚠ failed at: {failure['step'] or failure['scenario']} — attempting one automatic fix...")

    original = feat_path.read_text(encoding="utf-8")
    pom_text = pom_path.read_text(encoding="utf-8") if pom_path.exists() else ""
    fixed = generate._strip_fence(ask(
        prompts.reflect_prompt(original, pom_text, failure), system=prompts.SYSTEM))

    # NOOD_0177 — the prompt above embeds failure['message'], which routinely
    # quotes page content verbatim (assert_title prints the live <title>). A
    # hostile page can therefore address the model directly and get its reply
    # written to disk and run. Gate the reply the way prompt_expander already
    # gates model_fallback: no execution primitives, and it must parse as
    # Gherkin whose steps all match. Refuse rather than repair — this path runs
    # unattended, so a silent "fix" nobody reviewed is the whole problem.
    bad = _forbidden_steps(fixed)
    if bad:
        print(f"  ✗ refusing model-authored fix: it introduces {bad} — "
              "an execution step can never come from an automatic repair.")
        return False
    check = validate.check_feature(fixed)
    if check.get("error") or not check.get("matched", True):
        print(f"  ✗ refusing model-authored fix: {check.get('error') or 'unmatched steps'}")
        return False

    feat_path.write_text(fixed if fixed.endswith("\n") else fixed + "\n", encoding="utf-8")
    _run(str(feat_path), workspace)
    after = summary.collect(results_dir)
    if after["failed"] < before["failed"]:
        print("  ✅ fix reduced failures — kept.")
        return True

    feat_path.write_text(original, encoding="utf-8")
    print("  ✗ automatic fix didn't help — reverted. Check the Allure report by hand.")
    return False
