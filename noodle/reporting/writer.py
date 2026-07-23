import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path

from noodle.reporting.paths import results_dir


class ScenarioResult:
    def __init__(self, scenario):
        self.uuid = str(uuid.uuid4())
        self._failure_message = None
        self._failure_trace = None
        full_name = f"{scenario.feature.name}: {scenario.name}"
        labels = [
            {"name": "feature", "value": scenario.feature.name},
            # suite/parentSuite give the Allure Suites tab a real hierarchy
            # (app folder → feature → scenario) instead of one node per test.
            {"name": "suite", "value": scenario.feature.name},
            *[{"name": "tag", "value": t} for t in scenario.tags],
        ]
        filename = getattr(scenario.feature, "filename", None)
        if isinstance(filename, str) and filename:
            # .feature files live in <app_dir>/features/ — the app name (what
            # we actually want as the Allure parentSuite) is the grandparent,
            # not the immediate parent, which is just the literal "features".
            parent = Path(filename).parent
            app_name = parent.parent.name if parent.name == "features" else parent.name
            labels.append({"name": "parentSuite", "value": app_name})
            # NOOD_0089 — provenance for the RCA report: which app package and
            # .feature file a failure came from (workspace-relative, as behave
            # reports it), so a served report can never be mistaken for a
            # different suite's run.
            labels.append({"name": "featureFile", "value": filename})
        self.result = {
            "uuid": self.uuid,
            # historyId folds auto-retry attempts into one test case (Retries
            # tab) instead of listing each attempt as a separate test.
            "historyId": hashlib.md5(full_name.encode()).hexdigest(),
            "name": scenario.name,
            "fullName": full_name,
            "labels": labels,
            "steps": [],
            "start": int(time.time() * 1000),
            "status": "passed",
        }

    def add_attachment(self, name: str, path: str, mime_type: str = "application/json"):
        """Test-case-level attachment (as opposed to add_step's per-step one) —
        for artifacts that belong to the whole scenario, like its network log,
        not to any single step."""
        self.result.setdefault("attachments", []).append(_attach(path, name, mime_type))

    def add_step(self, step, status, attachment_path=None, warnings=None,
                 attachment_name=None, healing=None, evidence_meta=None):
        entry = {
            "name": f"{step.keyword} {step.name}",
            "status": status,
            "start": int(time.time() * 1000),
            "stop": int(time.time() * 1000),
        }
        if status == "failed":
            error_msg = str(step.exception) if step.exception else "Step failed"
            entry["statusDetails"] = {
                "message": error_msg,
                "trace": step.error_message or "",
            }
        # NOOD_0153 — attachments on ANY status: failure screenshots keep their
        # historic name, evidence/manual shots on passed steps get theirs.
        if attachment_path:
            name = attachment_name or ("failure_screenshot" if status == "failed"
                                       else "evidence")
            entry["attachments"] = [_attach(attachment_path, name, _image_mime(attachment_path))]
        # NOOD_0018 — console-only ⚠️ warnings (ambiguous locator, vision
        # fallback failures, self-heal matches) captured during this step, so
        # an RCA report can see the same signal a human reads off stdout.
        # NOOD_0021 — also kept on a PASSED step: lenient mode never fails the
        # build on these, so this is the only way they're not console-only.
        if warnings:
            entry.setdefault("statusDetails", {})["warnings"] = warnings
        # NOOD_0156 — per-step healing events + evidence metadata, so the run
        # payload can surface every substitution behind a green step and mark
        # whether the run counts as verified.
        if healing:
            entry.setdefault("statusDetails", {})["healing"] = healing
        if evidence_meta:
            entry.setdefault("statusDetails", {})["evidence"] = evidence_meta
        self.result["steps"].append(entry)

    def finish(self, scenario):
        self.result["stop"] = int(time.time() * 1000)
        # Determine overall status from steps
        statuses = [s["status"] for s in self.result["steps"]]
        if "failed" in statuses:
            self.result["status"] = "failed"
            # Propagate first failure details to top-level statusDetails
            for s in self.result["steps"]:
                if s["status"] == "failed" and "statusDetails" in s:
                    self.result["statusDetails"] = s["statusDetails"]
                    break
        else:
            self.result["status"] = "passed"


def _image_mime(path: str) -> str:
    """image/jpeg for .jpg/.jpeg (evidence shots), image/png otherwise —
    failure screenshots and the historic default."""
    return "image/jpeg" if Path(path).suffix.lower() in (".jpg", ".jpeg") \
        else "image/png"


def _attach(path: str, name: str, mime_type: str) -> dict:
    """Allure only serves attachments that live inside the results dir — copy
    the file there under the <uuid>-attachment name it expects. A missing
    source file keeps the old name-only reference (harmless, just no preview)."""
    src = Path(path)
    source = src.name
    if src.is_file():
        d = results_dir()
        d.mkdir(parents=True, exist_ok=True)
        source = f"{uuid.uuid4()}-attachment{src.suffix or '.png'}"
        shutil.copyfile(src, d / source)
    return {"name": name, "source": source, "type": mime_type}


def write_result(scenario_result: ScenarioResult):
    d = results_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{scenario_result.uuid}-result.json"
    path.write_text(json.dumps(scenario_result.result, indent=2))
    return path
