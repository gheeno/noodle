import functools
import http.server
import json
import shutil
import subprocess
from pathlib import Path

from noodle.reporting import paths as _paths


def _history_path(report_dir: str) -> Path:
    """NOOD_0039: Allure 3 keeps trend history in one JSONL file (v2 copied a
    history/ dir between report and results every run — see the deleted
    _seed_history). It lives OUTSIDE allure-report/ so regeneration can't wipe
    it, and inside its own dir so Azure's Cache@2 (dir-only paths) can carry it
    across pipeline runs."""
    return Path(report_dir).parent / "allure-history" / "history.jsonl"


def _write_config(report_dir: str) -> Path:
    """historyPath has no CLI flag in Allure 3 — config file only."""
    history = _history_path(report_dir)
    history.parent.mkdir(parents=True, exist_ok=True)
    config = Path(report_dir).parent / "allurerc.mjs"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"export default {{ historyPath: {json.dumps(str(history))} }};\n"
    )
    return config


def _allure_bin() -> str | None:
    """NOOD_0052 — resolve the allure executable with shutil.which instead of
    passing the bare name to subprocess: npm installs `allure.cmd` on Windows,
    which CreateProcess won't launch by name (only which() honours PATHEXT),
    and on any OS a missing install raised FileNotFoundError despite the
    'silently skips' contract."""
    return shutil.which("allure")


def generate(results_dir: str = None, report_dir: str = None) -> bool:
    """Run `allure generate` (Allure 3, `npm i -g allure`) to build the HTML
    report. Reads the same allure2-format result JSON we've always written —
    attachments (screenshots, network logs, API responses) carry over as-is.
    Skips with a note if allure is not installed.

    NOOD_0055 — returns whether a report was actually built. The skip note is
    fine on a run's tail-end reporting, but `noodle report generate` (and the
    MCP run_and_report behind it) previously exited 0 either way, handing
    agents an index.html path that didn't exist."""
    from noodle import counters
    counters.bump("report_generation")
    allure = _allure_bin()
    if not allure:
        print("allure not found on PATH (`npm i -g allure`) — skipping report generation.")
        return False
    results_dir = results_dir or str(_paths.results_dir())
    report_dir = report_dir or str(_paths.reports_dir() / "allure-report")
    config = _write_config(report_dir)
    # Allure 3 dropped `--clean`; wipe the old report ourselves or stale files
    # (e.g. a leftover v2 index.html) shadow the fresh one. History survives —
    # it lives outside report_dir (see _history_path).
    shutil.rmtree(report_dir, ignore_errors=True)
    proc = subprocess.run(
        [allure, "generate", results_dir, "-o", report_dir, "--config", str(config)],
        check=False,
    )
    return proc.returncode == 0


def ensure_fresh_reports(results_dir: str, reports_root: str) -> None:
    """NOOD_0089 — rebuild the Allure report and rca.html when they are
    MISSING **or STALE** (older than the newest result JSON). Serving used to
    rebuild only missing pieces, so a reports/ root could host an rca.html
    from one run next to an allure-report from another — Allure showing 100%
    green while the RCA listed failures from a run days earlier (and from a
    different suite). Best-effort: no allure binary → the RCA half still
    refreshes."""
    from noodle import counters
    counters.bump("freshness_check")
    results = Path(results_dir)
    root = Path(reports_root)
    if not results.is_dir():
        return
    # No result JSONs (e.g. attachments only) → keep the pre-0089 contract:
    # rebuild whatever is missing, leave existing files alone.
    newest = max((f.stat().st_mtime for f in results.glob("*-result.json")), default=0.0)

    def _stale(p: Path) -> bool:
        return not p.is_file() or (newest > 0 and p.stat().st_mtime < newest)

    if _stale(root / "allure-report" / "index.html"):
        generate(str(results), str(root / "allure-report"))
    if _stale(root / "rca.html"):
        from noodle.reporting import rca_report
        rca_report.write_reports(str(results), str(root))


def open_report(report_dir: str = None):
    """Open the Allure report in the default browser.

    NOOD_0093 — serve it over our own no-store http.server (localhost) and
    point the browser at it, instead of `allure open`. `allure open` spawns
    Allure's Node static server, which sends cacheable headers — that was the
    Chrome-caches-the-report complaint. Reusing serve_report means one code
    path, one cache policy (no-store), and no dependency on Allure's own
    server. Blocks until Ctrl+C, exactly like `allure open` did."""
    report_dir = report_dir or str(_paths.reports_dir() / "allure-report")
    import webbrowser
    serve_report(report_dir, host="127.0.0.1", port=0,
                 on_bound=lambda p: webbrowser.open(f"http://127.0.0.1:{p}/index.html"))


class _NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """NOOD_0089 — never cache. SimpleHTTPRequestHandler sends only
    Last-Modified; with no Cache-Control, browsers apply HEURISTIC caching
    (~10% of the file's age), so an rca.html that sat on disk for days got
    served from browser cache for HOURS after the file was rebuilt.

    NOOD_0093 — `no-cache` still lets Chrome STORE the response (it just
    revalidates), and the Allure SPA's data JSON is reused across regenerated
    reports on the same port. `no-store` forbids storage outright, so a
    re-served report is always the freshly built one."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def _make_server(report_dir: str, host: str = "0.0.0.0", port: int = 8000) -> http.server.ThreadingHTTPServer:
    handler = functools.partial(_NoCacheHandler, directory=report_dir)
    return http.server.ThreadingHTTPServer((host, port), handler)


def report_urls(report_dir: str, host: str, port: int) -> list[str]:
    """NOOD_0082 — the clickable URLs inside a served reports root: the Allure
    report (either nested under allure-report/ or the dir itself) and rca.html
    when present. Falls back to the root listing so there's always a link."""
    root = Path(report_dir)
    urls = []
    if (root / "allure-report" / "index.html").is_file():
        urls.append(f"http://{host}:{port}/allure-report/index.html")
    elif (root / "index.html").is_file():
        urls.append(f"http://{host}:{port}/index.html")
    if (root / "rca.html").is_file():
        urls.append(f"http://{host}:{port}/rca.html")
    return urls or [f"http://{host}:{port}/"]


# NOOD_0162 — the in-process daemon-thread server (start_report_server /
# stop_report_servers, NOOD_0082) is gone: NOOD_0161 made every hosting path a
# detached child that outlives the run, so the thread had no live caller and
# its stop path lied about what was running.


def serve_report(report_dir: str = None, host: str = "0.0.0.0", port: int = 8000,
                 on_bound=None):
    """NOOD_0035: `allure open` only binds to localhost, so a teammate can't
    just click a link to the report — they have to download the artifacts
    and run `allure open` themselves. Serve the already-built HTML over the
    network instead (stdlib http.server, no new dependency) so `noodle
    report serve` on the CI box/laptop gives out a link anyone on the same
    network can open directly.

    NOOD_0082 — the default dir is now the reports ROOT (artifacts/reports),
    not allure-report/, so one server hosts the Allure report AND rca.html."""
    report_dir = report_dir or str(_paths.reports_dir())
    httpd = _make_server(report_dir, host, port)
    bound_host, bound_port = httpd.server_address[:2]
    # NOOD_0089 — fires only after a successful bind, with the ACTUAL port
    # (matters for port=0): the CLI registers the server pid here, so a serve
    # that fails to bind never (un)registers anything.
    if on_bound:
        on_bound(bound_port)
    # NOOD_0104 — flush: with stdout piped (agents backgrounding this command),
    # Python block-buffers, and serve_forever() below means the buffer never
    # drains — the URLs sat invisible while agents probed the port with curl.
    print(f"Serving {report_dir} at http://{bound_host}:{bound_port}  (Ctrl+C to stop)", flush=True)
    for url in report_urls(report_dir, bound_host, bound_port):
        print(f"  → {url}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
