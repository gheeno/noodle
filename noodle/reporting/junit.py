import re
import xml.etree.ElementTree as ET
from pathlib import Path

from noodle import safe_xml as _safe_xml
from noodle.reporting import paths as _paths

_STATUS_MARK = {"passed": "✓", "failed": "✗", "skipped": "-", "broken": "!"}


def _step_lines(steps: list, out: list, depth: int = 0) -> None:
    """Flatten Allure steps into readable `✓ Given ...` lines, nested included."""
    for st in steps or []:
        mark = _STATUS_MARK.get(st.get("status", ""), "?")
        secs = round((st.get("stop", 0) - st.get("start", 0)) / 1000, 3)
        out.append(f"{'  ' * depth}{mark} {st.get('name', '')}  [{secs}s]")
        _step_lines(st.get("steps"), out, depth + 1)


def _attachment_paths(res: dict, results_dir) -> list:
    """Every attachment on the scenario and its steps, as absolute paths.

    Under --parallel the worker writes into <results>/p<N>/, and the CLI later
    moves every file up into <results>/ and deletes the worker dir. Point at
    that final home now, or the published paths are dead by the time Azure
    reads them. uuid filenames make the flatten collision-free.
    """
    src_dir = Path(results_dir)
    final_dir = src_dir.parent if re.fullmatch(r"p\d+", src_dir.name) else src_dir
    found, seen = [], set()

    def walk(node):
        for att in node.get("attachments") or []:
            src = att.get("source")
            if not src or src in seen:
                continue
            seen.add(src)
            if (src_dir / src).is_file():
                found.append((final_dir / src).resolve())

    def walk_all(node):
        walk(node)
        for st in node.get("steps") or []:
            walk_all(st)

    walk_all(res)
    return found


def _system_out(res: dict, results_dir) -> str:
    """The Tests tab's Console log: the Gherkin steps, then the evidence.

    The Allure tab depends on an org-level marketplace extension that can
    silently publish nothing. The Tests tab is built in, so mirror the same
    detail here: steps as text, and evidence via `[[ATTACHMENT|path]]`, the
    documented JUnit hook Azure DevOps scans system-out for (Services and
    Server 2022.2+; older Server ignores the marker and just shows the text).
    """
    lines = []
    _step_lines(res.get("steps"), lines)
    atts = _attachment_paths(res, results_dir)
    if atts:
        lines.append("")
        lines.extend(f"[[ATTACHMENT|{p}]]" for p in atts)
    return "\n".join(lines)


def write_junit(results: list, path: str = None, results_dir=None):
    """Write JUnit XML from a list of ScenarioResult objects."""
    results_dir = results_dir or _paths.results_dir()
    total = len(results)
    failures = sum(1 for r in results if r.result.get("status") == "failed")
    skipped = sum(1 for r in results if r.result.get("status") == "skipped")

    total_ms = sum(
        (r.result.get("stop", 0) - r.result.get("start", 0)) for r in results
    )
    total_secs = round(total_ms / 1000, 3)

    suite = ET.Element("testsuite", {
        "name": "Noodle",
        "tests": str(total),
        "failures": str(failures),
        "skipped": str(skipped),
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
        elif res.get("status") == "skipped":
            ET.SubElement(tc, "skipped", {
                "message": res.get("statusDetails", {}).get("message", "skipped")})

        body = _system_out(res, results_dir)
        if body:
            ET.SubElement(tc, "system-out").text = body

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
            root.append(_safe_xml.parse_file(p).getroot())
        except (ET.ParseError, _safe_xml.UnsafeXML, OSError):
            continue
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    out = Path(out_path or _paths.reports_dir() / "junit.xml")
    out.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(out), encoding="unicode", xml_declaration=True)
    return out
