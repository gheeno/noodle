"""NOOD_0155 — Tier-1 step patterns for the performance wok.

Same structure as patterns.py / visual_patterns.py — PATTERNS list + match().
Consulted by step_resolver.resolve() only after the web table misses, so a
@web scenario can mix in a load-test step (cross-wok) with zero risk of
shadowing an existing web verb. Verbs are canonical 3rd person (callers have
been through normalize_subject); trailing s stays optional.
"""
import re

PATTERNS = [
    # Load generation — exactly one of duration / request-count
    (r'^runs? a load test on ["\'](.+?)["\'] with (\d+) users? for (\d+) seconds?$',
     'perf_load', lambda m: {'url': m.group(1), 'users': int(m.group(2)),
                             'duration_s': int(m.group(3))}),

    (r'^runs? a load test on ["\'](.+?)["\'] with (\d+) requests? using (\d+) users?$',
     'perf_load', lambda m: {'url': m.group(1), 'requests': int(m.group(2)),
                             'users': int(m.group(3))}),

    (r'^runs? a load test on ["\'](.+?)["\'] with (\d+) requests?$',
     'perf_load', lambda m: {'url': m.group(1), 'requests': int(m.group(2)),
                             'users': 5}),

    # Latency assertions against the last load test
    (r'^(?:the )?(p\d{1,2}|average|mean|max(?:imum)?|slowest) response time '
     r'should be (?:under|below|less than) (\d+) ?ms$',
     'perf_assert_time', lambda m: {'metric': m.group(1), 'max_ms': int(m.group(2))}),

    # Reliability / throughput assertions
    (r'^(?:the )?error rate should be (?:under|below|less than) ([\d.]+) ?%$',
     'perf_assert_error_rate', lambda m: {'max_pct': float(m.group(1))}),

    # "should exceed" works everywhere; the "should be at least" phrasing only
    # wins inside a @perf scenario, where this table outranks the web
    # assert_compare catch-all that owns "X should be at least Y" elsewhere
    # (wok.pattern_priority — tag-aware step grammar).
    (r'^(?:the )?throughput should (?:exceed|reach|be at least) '
     r'([\d.]+) requests? per second$',
     'perf_assert_throughput', lambda m: {'min_rps': float(m.group(1))}),

    # Evidence: latency chart PNG through the screenshot pipeline
    (r'^saves? the load test (?:report|chart) as ["\'](.+?)["\']$',
     'perf_report', lambda m: {'name': m.group(1)}),

    # Cross-wok: expose a metric to {var:...} for any later step
    (r'^stores? the (p\d{1,2}|average|mean|max(?:imum)?|slowest|error rate|throughput) '
     r'(?:response time )?in(?:to)? ["\'](.+?)["\']$',
     'perf_store', lambda m: {'metric': m.group(1), 'var': m.group(2)}),
]


def match(step_text: str):
    """Return (action_type, params) or None."""
    for pattern, action_type, extractor in PATTERNS:
        m = re.match(pattern, step_text.strip(), re.IGNORECASE)
        if m:
            return action_type, extractor(m)
    return None
