"""NOOD_0131 — deterministic work-shape counters.

In-process tallies of the operations the NOOD_0131 pipeline gates care
about — browser launches, run-target resolutions, result-file scans, report
builds, serve freshness checks — so unit tests can assert the work SHAPE
("this run resolved its target once and rebuilt its report zero times")
without a browser or a model. Not telemetry: process-local, reset per test.
"""
from collections import Counter

counts: Counter = Counter()


def bump(name: str) -> None:
    counts[name] += 1


def reset() -> None:
    counts.clear()
