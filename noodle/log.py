"""Structured logging for Noodle.

One logger. Two output shapes, chosen by NOODLE_LOG_FORMAT:

- **text** (default) — the emoji breadcrumbs the runtime always printed, live to
  sys.stdout, level-gated by NOODLE_LOG_LEVEL. A human/CI/TTY surface, unchanged.
- **json** — one JSON object per line to *stderr* (never stdout: MCP stdio uses
  stdout for protocol frames, so a stray line there corrupts the session). Field
  names follow the OpenTelemetry log data model so a later OTel adoption is a
  transport swap, not a schema migration. This is what a container ships to its
  platform log store (12-factor XI: the app writes the stream, the platform owns
  files/routing/retention).

ponytail: the console handler writes to the *live* sys.stdout/sys.stderr on every
emit (not the stream captured at import) so pytest's capsys still sees our output
and behave's own stdout interleaving stays correct.

Correlation (NOOD_0171): a contextvar carries run_id/workspace/feature/scenario
onto every record via _ContextFilter, so one shared multi-team server's
interleaved lines can be sliced back to a single run. run_id crosses the
CLI->behave subprocess boundary through NOODLE_RUN_ID (contextvars don't).
"""
import contextvars
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

logger = logging.getLogger("noodle")

# NOOD_0018 — per-step WARNING+ capture. The console gets ⚠️ ambiguous-locator /
# vision-locate-failed / healed-via-partial-text messages via logger.warning,
# but those never reached the Allure JSON — only the final AssertionError text
# did. hooks.after_step drains this buffer into the failed step's result so an
# RCA report generator has the same signal a human reads off the console.
_warnings: list[str] = []


class _CaptureHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            _warnings.append(record.getMessage())


# NOOD_0118 — secret-value redaction. Values loaded from any *secrets.env file
# (or Key Vault) are registered here and scrubbed from every log line — console,
# file log, and the captured warnings that feed the RCA report. runner._safe_repr
# masks by variable *name*; this masks by *value*, so a secret leaks nowhere it's
# named blandly (a connection string, a script that echoes a password) — the source is the
# signal, not the name. Attached at logger level so one filter covers every handler.
_secret_values: set[str] = set()
_PLACEHOLDERS = {"CHANGE_ME", "CHANGEME", "TODO", "XXX", "REPLACE_ME", "<SET IN .ENV>"}

# NOOD_0171 — env-injected secrets. A container/K8s host delivers secrets as env
# vars (Container Apps secrets, AKV CSI driver), which never pass through a
# secrets.env file, so nothing registered them. register_env_secrets() sweeps
# os.environ by key name and registers the values. Broad on purpose:
# over-registering a value only widens the scrub set (safe-fail for compliance).
_SENSITIVE_ENV_RE = re.compile(
    r"(SECRET|PASSWORD|PASSWD|API[_-]?KEY|APIKEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|"
    r"TOKEN|CREDENTIAL|CONNECTION[_-]?STRING|CONN[_-]?STR|BEARER|_JWT|_SAS|_PAT_)",
    re.I)

# NOOD_0171 — attribute key-name masking. Belt-and-suspenders over value-scrub:
# a structured attribute literally named like a credential is masked whatever its
# value. Deliberately precise — must NOT match `tokens` (a token *count* is
# governance data, not a secret) or `path`/`model`/`duration`.
_SENSITIVE_KEY_RE = re.compile(
    r"^(.*[_-])?(password|passwd|secret|credential|apikey|api_key|access_key|"
    r"private_key|authorization|auth_token|bearer|jwt|sas_token|conn_str|"
    r"connection_string)([_-].*)?$", re.I)


def register_secret(value) -> None:
    """Register a value to scrub from all log output. Empty/short/placeholder
    values are skipped — masking a 1-3 char or 'CHANGE_ME' value would garble
    unrelated log lines for no security gain."""
    v = str(value or "").strip()
    if len(v) >= 4 and v.upper() not in _PLACEHOLDERS:
        _secret_values.add(v)


def register_env_secrets() -> None:
    """NOOD_0171 — register the *values* of every credential-looking env var so
    they're scrubbed everywhere register_secret already reaches (console, file
    log, captured warnings, RCA, diagnostics). Called at run start (hooks) and
    at server start (mcp.server.main) — the container's secrets get injected as
    env, and this is the only thing that puts them in the redaction set."""
    for k, v in os.environ.items():
        if _SENSITIVE_ENV_RE.search(k):
            register_secret(v)


def _redact(msg: str) -> str:
    for secret in _secret_values:
        if secret in msg:
            msg = msg.replace(secret, "***")
    return msg


def redact(text: str) -> str:
    """NOOD_0147 — the same value-scrub, for writers outside the logging
    pipeline (session diagnostics land on disk without passing a handler)."""
    return _redact(text)


def _redact_attrs(attrs: dict) -> dict:
    """NOOD_0171 — scrub a structured event's attributes: mask by credential
    key-name, value-scrub every string, recurse into nested dicts. Leaves
    numbers/bools (token counts, durations) untouched."""
    out = {}
    for k, v in attrs.items():
        if _SENSITIVE_KEY_RE.search(str(k)):
            out[k] = "***"
        elif isinstance(v, str):
            out[k] = _redact(v)
        elif isinstance(v, dict):
            out[k] = _redact_attrs(v)
        else:
            out[k] = v
    return out


class _RedactFilter(logging.Filter):
    def filter(self, record):
        if _secret_values:
            record.msg = _redact(record.getMessage())
            record.args = ()
        attrs = getattr(record, "attributes", None)
        if attrs:
            record.attributes = _redact_attrs(attrs)  # key-name masking runs even with no registered values
        return True


def get_warnings() -> list[str]:
    return list(_warnings)


def clear_warnings():
    _warnings.clear()


# NOOD_0171 — correlation context. Immutable-replace so a copy captured mid-run
# isn't mutated under us. bind() merges; the filter stamps it onto every record.
_context: contextvars.ContextVar[dict] = contextvars.ContextVar("noodle_log_ctx", default={})


def bind(**kwargs) -> None:
    """Merge non-None keys into the correlation context (run_id, workspace,
    feature, scenario, …) for all subsequent log records in this context."""
    merged = {**_context.get(), **{k: v for k, v in kwargs.items() if v is not None}}
    _context.set(merged)


def get_context() -> dict:
    return dict(_context.get())


def run_id() -> str:
    return _context.get().get("run_id", "")


def new_run_id() -> str:
    """16 hex chars. Minted by whichever process starts the work; propagated to
    the behave child via NOODLE_RUN_ID."""
    return os.urandom(8).hex()


class _ContextFilter(logging.Filter):
    def filter(self, record):
        record.noodle_context = _context.get()
        return True


def event(name: str, body: str = "", level: int = logging.INFO, **attrs) -> None:
    """Emit one structured event (NOOD_0171). `body` doubles as the human console
    line, so a text-mode reader sees exactly what a plain logger.info() gave;
    json mode additionally carries `event` + `attributes`. An un-migrated
    logger.info() still produces a valid, correlated line with no event."""
    logger.log(level, body or name, extra={"event": name, "attributes": attrs})


def telemetry(name: str, body: str = "", level: int = logging.INFO, **attrs) -> None:
    """NOOD_0173 — a structured lifecycle event (run/scenario/step).

    Emitted in json mode, and (NOOD_0202) in text mode when progress_mode() is
    on. The original contract was "json only, so the human console stays
    byte-for-byte unchanged" — which held because the human already had behave's
    output. In `--quiet` (every CI run: non-TTY) that premise is false, behave's
    stream is in run.log and the console sees NOTHING for the length of the
    suite. progress_mode() is precisely "someone is watching a console", so
    these lines go there and the byte-for-byte TTY contract still holds.

    Use event() for a signal a person should see on the console either way."""
    if _json_mode() or progress_mode():
        event(name, body or name, level=level, **attrs)


_OTEL_SEVERITY = {"DEBUG": 5, "INFO": 9, "WARNING": 13, "ERROR": 17, "CRITICAL": 21}


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, OpenTelemetry log-data-model field names."""

    def format(self, record):
        ts = datetime.fromtimestamp(record.created, timezone.utc)
        obj = {
            "timestamp": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "severity_text": record.levelname,
            "severity_number": _OTEL_SEVERITY.get(record.levelname, 9),
            "body": record.getMessage(),  # already redacted by _RedactFilter
            "service_name": "noodle",
        }
        obj.update(getattr(record, "noodle_context", None) or {})  # run_id, workspace, feature, scenario
        ev = getattr(record, "event", None)
        if ev:
            obj["event"] = ev
        attrs = getattr(record, "attributes", None)
        if attrs:
            obj["attributes"] = attrs  # already redacted by _RedactFilter
        if record.exc_info:
            obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(obj, default=str, ensure_ascii=False)


class _LiveStreamHandler(logging.StreamHandler):
    """StreamHandler that always targets the current sys.stdout/sys.stderr,
    resolved at emit time (so pytest capsys + behave interleaving still work)."""

    def __init__(self, stream_name: str = "stdout"):
        super().__init__()
        self._stream_name = stream_name

    @property
    def stream(self):
        return getattr(sys, self._stream_name)

    @stream.setter
    def stream(self, _value):
        pass  # ignore the base class's captured stream


def _json_mode() -> bool:
    return os.getenv("NOODLE_LOG_FORMAT", "text").lower() == "json"


def progress_mode() -> bool:
    """NOOD_0202 — is a build console watching this run?

    `--quiet` (automatic off a TTY) sends behave's stream to run.log, which is
    right for an agent and wrong for CI: an Azure job watched a 20-minute suite
    in total silence, and the scenario/step events documented in docs/logging.md
    never reached the log stream at all. This gate decides who gets the curated
    stream on the console. The *file* log is unconditional either way — nothing
    is ever lost, it only stops being charged to an LLM's context window.

    Resolution, first match wins:
      NOODLE_LOG_PROGRESS  explicit 0/1 — the pipeline's own switch
      CI / TF_BUILD        a build console is watching → on by default
      otherwise            off

    The agent doors set NOODLE_LOG_PROGRESS=0 themselves (repl.core._engine and
    `noodle run --json`) rather than relying on CI being unset: an AI-SDLC
    orchestrator runs *inside* the pipeline, where CI=true, and its captured
    subprocess pipes stderr straight into the payload the model reads.
    """
    env = os.getenv("NOODLE_LOG_PROGRESS", "").strip().lower()
    if env:
        return env not in ("0", "false", "no", "off")
    return bool(os.getenv("CI") or os.getenv("TF_BUILD"))


def _configure():
    if getattr(logger, "_noodle_configured", False):
        return
    if _json_mode():
        handler = _LiveStreamHandler("stderr")
        handler.setFormatter(_JsonFormatter())
    else:
        handler = _LiveStreamHandler("stdout")
        handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.addHandler(_CaptureHandler())
    logger.addFilter(_ContextFilter())   # stamp context first...
    logger.addFilter(_RedactFilter())    # ...then scrub
    logger.propagate = False
    set_level(os.getenv("NOODLE_LOG_LEVEL", "INFO"))
    logger._noodle_configured = True


def set_level(level: str):
    """Set the noodle log level from a name ('INFO', 'WARNING', ...)."""
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))


def route_console_to_stderr() -> None:
    """NOOD_0172 — force the console handler to stderr, never stdout. The MCP
    stdio server uses stdout for its protocol frames, so any log line there
    corrupts the session. Call once at server start; a no-op in json mode
    (already stderr) and harmless for the http transport."""
    for h in logger.handlers:
        if isinstance(h, _LiveStreamHandler):
            h._stream_name = "stderr"


# NOOD_0202 — emoji ranges, for stripping the console's breadcrumbs out of the
# CI build log. Covers arrows, the misc-symbol/dingbat blocks (⚠ ✅ ❌ ▶),
# geometric shapes and the SMP pictographs (🩹 🏁 📊), plus the variation
# selector and ZWJ that follow them. Trailing spaces go with the run so the
# line's own indentation survives.
_EMOJI_RE = re.compile(
    "[←-⇿⌀-⏿■-➿⬀-⯿"
    "️‍\U0001f000-\U0001faff]+[ ]*")


class _PlainFormatter(logging.Formatter):
    """NOOD_0202 — the CI build log is a log file, not a terminal: one level
    prefix per line and no emoji, so it greps and diffs like maven/gradle
    output. The local TTY console keeps its breadcrumbs — this formatter is
    attached to the progress handler only."""

    def format(self, record):
        msg = _EMOJI_RE.sub("", super().format(record).lstrip("\n")).rstrip()
        if not msg:
            return ""
        # NOOD_0202 — the lane tag. 8-10 behavex workers stream to ONE stderr, so
        # without it a parallel build log is four features' scenarios shuffled
        # together with no way to tell which line belongs to which: two identical
        # "Step failed" lines in a row were different features. `grep '\[w3\]'`
        # replays one lane in order. Absent on a sequential run — nothing to
        # disambiguate, so no noise.
        lane = (getattr(record, "noodle_context", None) or {}).get("lane")
        tag = f"[w{lane}] " if lane else ""
        # Every line gets the prefix, not just the first: a traceback from an
        # engine crash has to grep as [ERROR] all the way down.
        return "\n".join(f"[{record.levelname}] {tag}{ln}" for ln in msg.splitlines())


_progress_handler: logging.Handler | None = None


def attach_progress_handler() -> None:
    """NOOD_0202 — the CI build log: a stderr handler carrying ONLY the curated
    stream (records with an `event`, plus every WARNING+).

    stderr because in `--quiet` this process's *stdout* is a redirect into
    run.log, and behave's per-action firehose (81 logger call sites in
    actions.py alone) belongs there, not on a build console. What lands here is
    ~2 lines per scenario at INFO, plus one per step at DEBUG — so
    `noodle run --log-level DEBUG` is the `mvn -X` of a Noodle run, and it's the
    tier that tells you WHERE a wedged suite stopped.

    No-op in json mode: the console handler is already stderr there, and a
    second one would double every line.
    """
    global _progress_handler
    if _json_mode() or _progress_handler is not None:
        return
    h = _LiveStreamHandler("stderr")
    h.setFormatter(_PlainFormatter("%(message)s"))
    h.addFilter(lambda r: getattr(r, "event", None) is not None
                or r.levelno >= logging.WARNING)
    logger.addHandler(h)
    _progress_handler = h


def route_console_to_build_log() -> None:
    """NOOD_0202 — the CLI process's own console handler joins the build log:
    stderr, level-prefixed, emoji-stripped, so its run.start/run.end read like
    every line the behave child emits through attach_progress_handler(). A
    no-op on formatting in json mode (that stream is already machine-shaped)."""
    for h in logger.handlers:
        if isinstance(h, _LiveStreamHandler):
            h._stream_name = "stderr"
            if not _json_mode():
                h.setFormatter(_PlainFormatter("%(message)s"))


_file_handler: logging.Handler | None = None


def attach_file_handler(path: str):
    """Mirror everything the console gets into a file too (the run's "sys log").
    Replaces any previously attached file handler — one file per process.

    This is the RELIABLE sink for a run: a direct FileHandler writes to its own
    file object, so unlike the console handler it's immune to behave's
    per-scenario stdout/stderr capture (which otherwise swallows a passing run's
    events). In a no-MCP / no-streaming deployment (CORP: CI archives the
    artifacts/ tree, nothing streams to a log store) this file IS the log
    destination — so it honors NOODLE_LOG_FORMAT: JSON when json mode is set
    (structured, correlated, redacted — CI-shippable), human text otherwise.

    Removes every FileHandler on the logger, not just the tracked one — a
    stray handler can outlive its owner (e.g. a caller that attached one
    without going through this function's own replacement logic), and "one
    file log" should hold regardless of how prior handlers got attached.
    """
    global _file_handler
    for h in list(logger.handlers):
        if isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    _file_handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    _file_handler.setFormatter(
        _JsonFormatter() if _json_mode()
        else logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_file_handler)


_configure()
