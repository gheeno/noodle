import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path

from noodle.log import redact as _log_redact
from noodle.reporting.paths import results_dir


def _redact(text) -> str:
    """NOOD_0177 — value-scrub anything on its way into a shared artifact.
    Never raises: a redaction failure must not lose a failure message, but it
    must also never fall back to the unscrubbed original."""
    try:
        return _log_redact("" if text is None else str(text))
    except Exception:
        return "<redaction failed — message withheld>"


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
        # NOOD_0187 — real durations: this runs in after_step, so start is
        # stop minus behave's measured step.duration. start == stop rendered
        # every step as 0 ms and made the Allure timeline meaningless.
        now = time.time()
        duration = getattr(step, "duration", 0)
        if not isinstance(duration, (int, float)):
            duration = 0
        entry = {
            # NOOD_0177 — redact the step NAME too. A step written with a
            # literal credential ("enters 'hunter2' in the password field")
            # leaks it into the Allure step title on the PASSING path, where no
            # failure message exists to scrub.
            "name": _redact(f"{step.keyword} {step.name}"),
            "status": status,
            "start": int((now - duration) * 1000),
            "stop": int(now * 1000),
        }
        if status == "failed":
            # NOOD_0177 — redact BOTH fields. log.redact() masks by secret VALUE,
            # but it was only wired into the logging filter and diagnostics, so
            # the console was scrubbed while this — the artifact people serve,
            # email and publish to the CI Tests tab — was not. assert_value
            # formats "Expected '<{env:PASSWORD}>' … actual: '<live field value>'",
            # so an ordinary failing login printed the credential twice.
            error_msg = _redact(str(step.exception) if step.exception else "Step failed")
            entry["statusDetails"] = {
                "message": error_msg,
                "trace": _redact(step.error_message or ""),
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
            entry.setdefault("statusDetails", {})["warnings"] = [
                _redact(str(w)) for w in warnings]
        # NOOD_0156 — per-step healing events + evidence metadata, so the run
        # payload can surface every substitution behind a green step and mark
        # whether the run counts as verified.
        if healing:
            entry.setdefault("statusDetails", {})["healing"] = healing
        if evidence_meta:
            entry.setdefault("statusDetails", {})["evidence"] = evidence_meta
        self.result["steps"].append(entry)

    def add_failure(self, name: str, message: str):
        """NOOD_0187 — a failure with no failing behave step behind it (soft-
        assert collection is the caller today): record it as a synthetic failed
        step so finish() and every result reader agree with behave's verdict
        instead of writing a green result next to a red exit code."""
        now = int(time.time() * 1000)
        self.result["steps"].append({
            "name": _redact(name), "status": "failed", "start": now, "stop": now,
            "statusDetails": {"message": _redact(message)},
        })

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
        elif (reason := _scenario_failure(scenario, statuses)) is not None:
            # NOOD_0187 — no recorded step failed, but behave's own verdict
            # says the scenario did: an undefined step (behave never fires
            # after_step for it) or a setup hook/precondition error (no step
            # ever ran). Reading steps only wrote these as "passed" — a green
            # Allure/junit/last_run row contradicting the red exit code.
            self.result["status"] = "failed"
            self.result["statusDetails"] = {"message": _redact(reason)}
        else:
            self.result["status"] = "passed"


def _scenario_failure(scenario, recorded_statuses) -> str | None:
    """Why behave considers `scenario` failed when no recorded step is, or
    None if it genuinely passed. Best-effort: unit-test doubles that don't
    behave like behave objects read as passed, same as before."""
    try:
        status = getattr(scenario, "status", None)
        name = getattr(status, "name", None)
        name = name if isinstance(name, str) else str(status or "")
        undefined = []
        for s in list(getattr(scenario, "steps", None) or []):
            st = getattr(s, "status", None)
            st_name = getattr(st, "name", None)
            st_name = st_name if isinstance(st_name, str) else str(st or "")
            if st_name == "undefined":
                undefined.append(str(getattr(s, "name", "?")))
        if undefined:
            return "undefined step (no matching implementation): " + "; ".join(undefined)
        if getattr(scenario, "hook_failed", False) is True and not recorded_statuses:
            return ("scenario setup hook/precondition failed before any step "
                    "ran — see run.log")
        if name in ("failed", "error", "hook_error", "cleanup_error", "undefined"):
            return (f"scenario finished '{name}' with no failing step "
                    "recorded — see run.log")
    except Exception:
        return None
    return None


def write_skip_result(scenario, reason: str):
    """NOOD_0187 — a gated skip (@live off, missing OCR/Appium/NOODLE_MODEL)
    used to leave NO result at all: the scenario vanished from Allure, junit
    and the summary, so a suite that skipped everything still read as a clean
    run. Returns the written path and the ScenarioResult."""
    r = ScenarioResult(scenario)
    r.result["status"] = "skipped"
    r.result["stop"] = r.result["start"]
    r.result["statusDetails"] = {"message": _redact(reason)}
    return write_result(r), r


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
    path.write_text(json.dumps(scenario_result.result, indent=2), encoding="utf-8")
    return path
