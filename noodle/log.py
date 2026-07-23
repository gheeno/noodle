"""Structured logging for Noodle.

One logger, level from NOODLE_LOG_LEVEL (default INFO). Messages keep the
emoji breadcrumbs that the runtime always printed — the formatter just passes
them through, so console output looks the same but is now level-gated and
silenceable (NOODLE_LOG_LEVEL=WARNING in noisy CI).

ponytail: the handler writes to the *live* sys.stdout on every emit (not the
sys.stdout captured at import) so pytest's capsys still sees our output and
behave's own stdout interleaving stays correct.
"""
import logging
import os
import sys

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


def register_secret(value) -> None:
    """Register a value to scrub from all log output. Empty/short/placeholder
    values are skipped — masking a 1-3 char or 'CHANGE_ME' value would garble
    unrelated log lines for no security gain."""
    v = str(value or "").strip()
    if len(v) >= 4 and v.upper() not in _PLACEHOLDERS:
        _secret_values.add(v)


def _redact(msg: str) -> str:
    for secret in _secret_values:
        if secret in msg:
            msg = msg.replace(secret, "***")
    return msg


def redact(text: str) -> str:
    """NOOD_0147 — the same value-scrub, for writers outside the logging
    pipeline (session diagnostics land on disk without passing a handler)."""
    return _redact(text)


class _RedactFilter(logging.Filter):
    def filter(self, record):
        if _secret_values:
            record.msg = _redact(record.getMessage())
            record.args = ()
        return True


def get_warnings() -> list[str]:
    return list(_warnings)


def clear_warnings():
    _warnings.clear()


class _LiveStdoutHandler(logging.StreamHandler):
    """StreamHandler that always targets the current sys.stdout."""

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, _value):
        pass  # ignore the base class's captured stream


def _configure():
    if getattr(logger, "_noodle_configured", False):
        return
    handler = _LiveStdoutHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.addHandler(_CaptureHandler())
    logger.addFilter(_RedactFilter())
    logger.propagate = False
    set_level(os.getenv("NOODLE_LOG_LEVEL", "INFO"))
    logger._noodle_configured = True


def set_level(level: str):
    """Set the noodle log level from a name ('INFO', 'WARNING', ...)."""
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))


_file_handler: logging.Handler | None = None


def attach_file_handler(path: str):
    """Mirror everything the console gets into a file too (the run's "sys log").
    Replaces any previously attached file handler — one file per process.

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
    _file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_file_handler)


_configure()
