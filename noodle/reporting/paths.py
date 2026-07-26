"""Where this run writes its results.

Single source of truth so parallel workers (behavex) can each write to their
own subdir instead of clobbering the shared one. Read from the env every call
so a hook can repoint it mid-process. Everything lands under one root
(default `artifacts/`) so a CI step can archive/ship the whole tree in one
shot instead of collecting scattered top-level dirs.
"""
import os
from pathlib import Path


def artifacts_root() -> Path:
    return Path(os.getenv("NOODLE_ARTIFACTS_DIR", "artifacts"))


# NOOD_0086 — single-app runs keep everything inside the app package:
# `noodle run <tests_dir>/<app>` points NOODLE_ARTIFACTS_DIR at <app>/report/,
# so results, screenshots, reports and trend history stay encapsulated per
# app. This pointer file records which root the last run wrote, so follow-up
# commands in a fresh process (summary, rca, report serve, MCP get_last_result)
# find it without the env var.
_POINTER = Path(".noodle") / "last_run_root"


def record_last_run_root(workspace: str = ".") -> None:
    """Persist this run's artifacts root (workspace-relative) for later readers."""
    p = Path(workspace) / _POINTER
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(artifacts_root()) + "\n")
    except OSError:
        pass  # pointer is a nicety — never fail the run over it


def last_run_root(workspace: str = ".") -> Path:
    """Artifacts root of this workspace's most recent run. Explicit
    NOODLE_ARTIFACTS_DIR wins; a workspace that is itself an app package
    (has features/, no noodle.yaml — e.g. `cd noodle_tests/app1`) always
    reads its own report/; otherwise follow the pointer file; otherwise the
    classic <workspace>/artifacts."""
    if not os.getenv("NOODLE_ARTIFACTS_DIR"):
        ws = Path(workspace)
        if (ws / "features").is_dir() and not (ws / "noodle.yaml").exists():
            return ws / "report"
        try:
            rel = (ws / _POINTER).read_text().strip()
        except OSError:
            rel = ""
        if rel and (ws / rel).is_dir():
            return ws / rel
    return Path(workspace) / artifacts_root()


def results_dir() -> Path:
    return Path(os.getenv("NOODLE_RESULTS_DIR", str(artifacts_root() / "allure-results")))


def reports_dir() -> Path:
    return artifacts_root() / "reports"


# NOOD_0183 — every evidence filename below is derived from TEXT (a step
# sentence, a scenario name), never from a uuid: `FAILED_the_cart_is_empty.png`,
# `Checkout.zip`, `Checkout.json`. Two feature files that share a step sentence
# — which is the whole point of a shared step vocabulary — therefore wrote the
# same file from two processes at the same moment under --parallel, and the
# report attached whichever write finished last. Giving each worker its own
# leaf makes the collision impossible instead of unlikely; the paths recorded
# in allure-results stay absolute-from-workspace, so the report and RCA follow
# them with no merge step.
def _worker_leaf() -> str:
    return f"p{os.getpid()}" if os.getenv("NOODLE_PARALLEL_WORKER") == "1" else ""


def screenshots_dir() -> Path:
    return artifacts_root() / "screenshots" / _worker_leaf()


def traces_dir() -> Path:
    return artifacts_root() / "traces" / _worker_leaf()


def videos_dir() -> Path:
    return artifacts_root() / "videos" / _worker_leaf()


def network_dir() -> Path:
    return artifacts_root() / "network" / _worker_leaf()


def logs_dir() -> Path:
    return artifacts_root() / "logs"
