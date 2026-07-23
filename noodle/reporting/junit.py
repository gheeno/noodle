import xml.etree.ElementTree as ET
from pathlib import Path

from noodle.reporting import paths as _paths


def write_junit(results: list, path: str = None):
    """Write JUnit XML from a list of ScenarioResult objects."""
    total = len(results)
    failures = sum(1 for r in results if r.result.get("status") == "failed")

    total_ms = sum(
        (r.result.get("stop", 0) - r.result.get("start", 0)) for r in results
    )
    total_secs = round(total_ms / 1000, 3)

    suite = ET.Element("testsuite", {
        "name": "Noodle",
        "tests": str(total),
        "failures": str(failures),
        "time": str(total_secs),
    })

    for r in results:
        res = r.result
        duration_ms = res.get("stop", 0) - res.get("start", 0)
        duration_secs = round(duration_ms / 1000, 3)

        # classname: derive from feature label if present
        feature_name = next(
            (lbl["value"] for lbl in res.get("labels", []) if lbl["name"] == "feature"),
            "unknown",
        )

        tc = ET.SubElement(suite, "testcase", {
            "name": res.get("name", ""),
            "classname": feature_name,
            "time": str(duration_secs),
        })

        if res.get("status") == "failed":
            details = res.get("statusDetails", {})
            msg = details.get("message", "Test failed")
            trace = details.get("trace", "")
            failure = ET.SubElement(tc, "failure", {"message": msg})
            failure.text = trace

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    out = Path(path or _paths.reports_dir() / "junit.xml")
    out.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(out), encoding="unicode", xml_declaration=True)
    return out


def merge_junits(suite_paths, out_path=None):
    """Combine per-worker <testsuite> files into one <testsuites> root so the
    parallel run publishes a single junit.xml — identical artifact to a
    single-process run. Missing/malformed files are skipped."""
    root = ET.Element("testsuites")
    for p in suite_paths:
        p = Path(p)
        if not p.is_file():
            continue
        try:
            root.append(ET.parse(str(p)).getroot())
        except ET.ParseError:
            continue
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    out = Path(out_path or _paths.reports_dir() / "junit.xml")
    out.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(out), encoding="unicode", xml_declaration=True)
    return out
