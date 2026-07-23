"""NOOD_0155 — the performance wok's engine: a built-in HTTP load generator.

Deliberately stdlib-only (threads + urllib) so `pip install noodle` can run a
load test with zero extra dependencies, on any OS, and unit tests can drive it
against a local http.server. It is a *test-assertion* load tool — "does the
p95 stay under 800ms with 20 concurrent users" as a CI gate — not a
load-farm: one process, N worker threads, one connection per request. For
sustained heavy load or distributed generation, graduate to Locust (Python,
code-based scenarios — the modern answer to JMeter) and keep these Gherkin
assertions as the contract; docs/woks.md § Performance covers the trade-off.
"""
from __future__ import annotations

import ssl
import threading
import time
import urllib.request
from dataclasses import dataclass, field

# One insecure-by-default TLS context, matching the web wok's
# ignore_https_errors default (dev/sandbox certs) — NOODLE_IGNORE_HTTPS_ERRORS
# is honoured by the caller building opener kwargs, not here, to keep this
# module env-free and deterministic under test.
_LAX_TLS = ssl.create_default_context()
_LAX_TLS.check_hostname = False
_LAX_TLS.verify_mode = ssl.CERT_NONE


@dataclass
class Sample:
    offset_s: float   # seconds since the run started
    ms: float         # wall-clock latency of this request
    ok: bool          # 2xx/3xx and no transport error
    status: int       # HTTP status, 0 on transport error


@dataclass
class LoadResult:
    url: str
    users: int
    duration_s: float = 0.0
    samples: list[Sample] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def errors(self) -> int:
        return sum(1 for s in self.samples if not s.ok)

    @property
    def error_rate_pct(self) -> float:
        return (self.errors / self.count * 100.0) if self.samples else 0.0

    @property
    def throughput_rps(self) -> float:
        return (self.count / self.duration_s) if self.duration_s > 0 else 0.0

    @property
    def avg_ms(self) -> float:
        return (sum(s.ms for s in self.samples) / self.count) if self.samples else 0.0

    @property
    def max_ms(self) -> float:
        return max((s.ms for s in self.samples), default=0.0)

    def percentile_ms(self, p: float) -> float:
        """Nearest-rank percentile over all sample latencies (0 when empty)."""
        if not self.samples:
            return 0.0
        ordered = sorted(s.ms for s in self.samples)
        rank = max(1, round(p / 100.0 * len(ordered)))
        return ordered[min(rank, len(ordered)) - 1]

    def metric(self, name: str) -> float:
        """Look up a metric by its step-text name ('p95', 'average', ...)."""
        name = name.lower().strip()
        if name.startswith('p') and name[1:].isdigit():
            return self.percentile_ms(int(name[1:]))
        return {
            'average': self.avg_ms, 'avg': self.avg_ms, 'mean': self.avg_ms,
            'max': self.max_ms, 'maximum': self.max_ms, 'slowest': self.max_ms,
            'error rate': self.error_rate_pct,
            'throughput': self.throughput_rps,
        }[name]

    def summary(self) -> str:
        return (f"{self.count} requests, {self.users} users, "
                f"{self.duration_s:.1f}s — avg {self.avg_ms:.0f}ms, "
                f"p95 {self.percentile_ms(95):.0f}ms, max {self.max_ms:.0f}ms, "
                f"{self.error_rate_pct:.1f}% errors, "
                f"{self.throughput_rps:.1f} req/s")


def _hit(url: str, method: str, timeout_s: float) -> tuple[float, bool, int]:
    """One request; returns (latency_ms, ok, status). Never raises."""
    req = urllib.request.Request(url, method=method,
                                 headers={"User-Agent": "noodle-perf/0.1"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=_LAX_TLS) as resp:
            resp.read()
            status = resp.status
            ok = status < 400
    except urllib.error.HTTPError as e:
        status, ok = e.code, False
    except Exception:
        status, ok = 0, False
    return (time.perf_counter() - t0) * 1000.0, ok, status


def run_load(url: str, users: int = 5, duration_s: float | None = None,
             total_requests: int | None = None, method: str = "GET",
             timeout_s: float = 30.0) -> LoadResult:
    """Hammer `url` with `users` concurrent workers until the duration elapses
    or the request budget is spent (exactly one of duration_s/total_requests).

    Workers loop back-to-back requests; samples are appended under a lock and
    timestamped relative to the run start so a chart can plot latency over
    time. Elapsed wall-clock (not the nominal duration) feeds throughput.
    """
    if (duration_s is None) == (total_requests is None):
        raise ValueError("run_load needs exactly one of duration_s / total_requests")
    users = max(1, int(users))
    result = LoadResult(url=url, users=users)
    lock = threading.Lock()
    budget = [total_requests if total_requests is not None else -1]
    start = time.perf_counter()
    deadline = start + duration_s if duration_s is not None else None

    def worker():
        while True:
            if deadline is not None and time.perf_counter() >= deadline:
                return
            with lock:
                if budget[0] == 0:
                    return
                if budget[0] > 0:
                    budget[0] -= 1
            ms, ok, status = _hit(url, method, timeout_s)
            with lock:
                result.samples.append(
                    Sample(time.perf_counter() - start, ms, ok, status))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(users)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    result.duration_s = time.perf_counter() - start
    return result
