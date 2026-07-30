"""NOOD_0204 — the Tests tab must carry the steps and the evidence, not just
pass/fail — it is the fallback when the Allure tab's marketplace extension
publishes nothing. Guards the `[[ATTACHMENT|path]]` marker Azure DevOps scans
for.
"""
import xml.etree.ElementTree as ET

from noodle.reporting import junit


class _R:
    def __init__(self, result):
        self.result = result


def _result(tmp_path, shot="shot.jpg"):
    (tmp_path / shot).write_bytes(b"jpg")
    return {
        "name": "Dismissing a confirm dialog",
        "status": "passed",
        "start": 0,
        "stop": 1000,
        "labels": [{"name": "feature", "value": "Alerts"}],
        "steps": [
            {"name": "Given User is on \"/alerts\"", "status": "passed",
             "start": 0, "stop": 400},
            {"name": "When User clicks \"OK\"", "status": "passed",
             "start": 400, "stop": 900,
             "attachments": [{"name": "evidence", "source": shot}]},
        ],
        "attachments": [{"name": "network log", "source": "missing.json"}],
    }


def test_system_out_has_steps_and_attachment(tmp_path):
    out = tmp_path / "junit.xml"
    junit.write_junit([_R(_result(tmp_path))], str(out), results_dir=tmp_path)

    tc = ET.parse(out).getroot().find("testcase")
    body = tc.find("system-out").text

    assert 'Given User is on "/alerts"' in body
    assert "✓" in body
    # evidence rides in via the documented ADO JUnit marker, absolute path
    assert f"[[ATTACHMENT|{(tmp_path / 'shot.jpg').resolve()}]]" in body
    # an attachment the run never wrote must not be advertised
    assert "missing.json" not in body


def test_worker_paths_point_at_the_flattened_home(tmp_path):
    """--parallel moves <results>/p0/* up to <results>/ and deletes p0."""
    worker = tmp_path / "p0"
    worker.mkdir()
    out = tmp_path / "junit.0.xml"
    junit.write_junit([_R(_result(worker))], str(out), results_dir=worker)

    body = ET.parse(out).getroot().find("testcase").find("system-out").text
    assert f"[[ATTACHMENT|{(tmp_path / 'shot.jpg').resolve()}]]" in body
    assert "p0" not in body


def test_no_steps_writes_no_system_out(tmp_path):
    res = {"name": "bare", "status": "passed", "start": 0, "stop": 1, "labels": []}
    out = tmp_path / "junit.xml"
    junit.write_junit([_R(res)], str(out), results_dir=tmp_path)

    assert ET.parse(out).getroot().find("testcase").find("system-out") is None
