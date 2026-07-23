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
import subprocess
import sys
from pathlib import Path

from noodle.reporting import paths as _paths
from noodle.reporting import summary


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

    original = feat_path.read_text()
    pom_text = pom_path.read_text() if pom_path.exists() else ""
    fixed = generate._strip_fence(ask(
        prompts.reflect_prompt(original, pom_text, failure), system=prompts.SYSTEM))

    feat_path.write_text(fixed if fixed.endswith("\n") else fixed + "\n")
    _run(str(feat_path), workspace)
    after = summary.collect(results_dir)
    if after["failed"] < before["failed"]:
        print("  ✅ fix reduced failures — kept.")
        return True

    feat_path.write_text(original)
    print("  ✗ automatic fix didn't help — reverted. Check the Allure report by hand.")
    return False
