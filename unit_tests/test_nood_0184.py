"""NOOD_0184 — MFA one-time codes: TOTP (RFC 6238) and email-inbox OTP.

MFA'd test environments previously had exactly one answer (session reuse via
NOODLE_STORAGE_STATE). These tests pin the two new challenge-solvers:

  - totp_code: stdlib RFC 6238 against the published RFC test vectors, plus
    the secret-hygiene edges (spaces/dashes/lowercase/unpadded, unresolved
    {env:X} refs, bad base32).
  - email_code: IMAP polling with a stubbed imaplib — filter match, 6-digit
    preference over date-like numbers, mark-seen on consume, timeout honesty.

No network, no browser, no LLM.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noodle import mfa
from noodle.orchestrator import runner
from noodle.resolver.patterns import match, normalize_phrasing, normalize_subject


def _r(text):
    return match(normalize_phrasing(normalize_subject(text)))


def _ctx():
    return SimpleNamespace(page=MagicMock(), _vars={})


# --- TOTP: RFC 6238 correctness ----------------------------------------------

# RFC 6238 Appendix B vectors (SHA-1, 8 digits, secret "12345678901234567890").
_RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize("t, expected", [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
])
def test_totp_rfc6238_vectors(t, expected):
    assert mfa.totp_code(_RFC_SECRET, digits=8, now=t) == expected


def test_totp_default_is_6_digits_and_zero_padded():
    code = mfa.totp_code(_RFC_SECRET, now=59)
    assert code == "94287082"[-6:] == "287082"
    assert len(mfa.totp_code(_RFC_SECRET, now=1111111109)) == 6


def test_totp_accepts_the_secret_as_authenticator_apps_display_it():
    """Spaces, dashes, lowercase, missing padding — all the same secret."""
    reference = mfa.totp_code(_RFC_SECRET, now=59)
    assert mfa.totp_code("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", now=59) == reference
    assert mfa.totp_code("GEZD-GNBV-GY3T-QOJQ-GEZD-GNBV-GY3T-QOJQ", now=59) == reference


def test_totp_unresolved_env_ref_names_the_missing_variable():
    """{env:MFA_SECRET} that survived substitution arrives as [MFA_SECRET] —
    the error must say WHICH variable to set, not choke on base32."""
    with pytest.raises(AssertionError, match="MFA_SECRET.*not set"):
        mfa.totp_code("[MFA_SECRET]")
    with pytest.raises(AssertionError, match="MFA_SECRET.*not set"):
        mfa.totp_code("{env:MFA_SECRET}")


def test_totp_no_secret_anywhere_explains_both_sources(monkeypatch):
    monkeypatch.delenv("NOODLE_TOTP_SECRET", raising=False)
    with pytest.raises(AssertionError, match="NOODLE_TOTP_SECRET"):
        mfa.totp_code(None)


def test_totp_env_fallback(monkeypatch):
    monkeypatch.setenv("NOODLE_TOTP_SECRET", _RFC_SECRET)
    assert mfa.totp_code(None, now=59) == "287082"


def test_totp_rejects_garbage_base32():
    with pytest.raises(AssertionError, match="not valid base32"):
        mfa.totp_code("hunter2!!")


# --- Patterns: the steps resolve to the right actions -------------------------

@pytest.mark.parametrize("step, expected_type", [
    ("User enters the one-time code for 'ABC234' in the 'mfa code' field", "totp_fill"),
    ("User types the TOTP code in the 'code' field",                       "totp_fill"),
    ("User enters the 2FA code for [MFA_SECRET] into 'verification code'", "totp_fill"),
    ("User stores the one-time code for 'ABC234' in {var:OTP}",            "totp_store"),
    ("User generates the authenticator code as `OTP`",                     "totp_store"),
    ("User waits for an email code and stores it in {var:OTP}",            "email_code"),
    ("User waits for an email code containing 'Verify' and stores it in {var:OTP} within 120 seconds",
                                                                           "email_code"),
])
def test_mfa_steps_resolve(step, expected_type):
    resolved = _r(step)
    assert resolved is not None, f"{step!r} does not resolve"
    assert resolved[0] == expected_type, f"{step!r} -> {resolved[0]}"


def test_totp_fill_params_carry_secret_and_locator():
    t, params = _r("User enters the one-time code for 'GEZD GNBV' in the 'mfa code' field")
    assert t == "totp_fill"
    assert params == {"secret": "GEZD GNBV", "locator": "mfa code"}


def test_email_code_params_carry_filter_var_and_budget():
    t, params = _r("User waits for an email code containing 'Verify' "
                   "and stores it in {var:OTP} within 120 seconds")
    assert params == {"match": "Verify", "var": "OTP", "timeout": 120.0}


def test_unfiltered_email_code_defaults():
    t, params = _r("User waits for an email code and stores it in {var:OTP}")
    assert params == {"match": None, "var": "OTP", "timeout": None}


# --- Dispatch: resolver → runner → mfa/actions --------------------------------

def test_totp_fill_dispatches_a_fresh_code_into_the_field(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner.actions, "fill",
                        lambda page, locator, value: seen.update(locator=locator, value=value))
    monkeypatch.setattr(runner.mfa, "totp_code", lambda secret: "123456")
    runner.execute_step("User enters the one-time code for 'ABC234' in the 'mfa code' field", _ctx())
    assert seen == {"locator": "mfa code", "value": "123456"}


def test_totp_store_puts_the_code_in_vars(monkeypatch):
    monkeypatch.setattr(runner.mfa, "totp_code", lambda secret: "654321")
    ctx = _ctx()
    runner.execute_step("User stores the one-time code for 'ABC234' in {var:OTP}", ctx)
    assert ctx._vars["OTP"] == "654321"


def test_email_code_stores_and_passes_filter_and_budget(monkeypatch):
    got = {}
    def fake(match, timeout):
        got.update(match=match, timeout=timeout)
        return "998877"
    monkeypatch.setattr(runner.mfa, "email_code", fake)
    ctx = _ctx()
    runner.execute_step("User waits for an email code containing 'Verify' "
                        "and stores it in {var:CODE} within 45 seconds", ctx)
    assert ctx._vars["CODE"] == "998877"
    assert got == {"match": "Verify", "timeout": 45.0}


# --- email_code: IMAP behaviour with a stubbed mailbox ------------------------

class _FakeIMAP:
    """Just enough of imaplib.IMAP4_SSL for _scan_unseen."""
    instances = []

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.stored = []
        _FakeIMAP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.creds = (user, password)

    def select(self, folder):
        self.folder = folder

    def search(self, charset, criterion):
        return "OK", [b" ".join(m[0] for m in self.mailbox)]

    def fetch(self, num, what):
        raw = dict(self.mailbox)[num]
        return "OK", [(num + b" (BODY[])", raw)]

    def store(self, num, op, flags):
        self.stored.append((num, op, flags))


def _msg(subject, body):
    return f"Subject: {subject}\r\nContent-Type: text/plain\r\n\r\n{body}".encode()


@pytest.fixture
def inbox(monkeypatch):
    _FakeIMAP.instances = []
    monkeypatch.setattr(mfa.imaplib, "IMAP4_SSL", _FakeIMAP)
    monkeypatch.setenv("NOODLE_IMAP_HOST", "imap.test")
    monkeypatch.setenv("NOODLE_IMAP_USER", "qa@test")
    monkeypatch.setenv("NOODLE_IMAP_PASSWORD", "pw")
    monkeypatch.setattr(mfa.time, "sleep", lambda s: None)
    return _FakeIMAP


def test_email_code_extracts_and_marks_seen(inbox):
    inbox.mailbox = [(b"1", _msg("Your verification code", "Use 445566 to sign in."))]
    assert mfa.email_code() == "445566"
    assert inbox.instances[-1].stored == [(b"1", "+FLAGS", "\\Seen")]


def test_email_code_prefers_six_digits_over_date_noise(inbox):
    inbox.mailbox = [(b"1", _msg("Code", "On 2026-07-26 your code is 445566."))]
    assert mfa.email_code() == "445566"


def test_email_code_filter_skips_non_matching_mail_without_consuming_it(inbox):
    inbox.mailbox = [
        (b"1", _msg("Weekly newsletter", "Save 20260726 today!")),
        (b"2", _msg("Verify your login", "Your code: 112233")),
    ]
    assert mfa.email_code(match="verify") == "112233"
    assert inbox.instances[-1].stored == [(b"2", "+FLAGS", "\\Seen")]


def test_email_code_newest_message_wins(inbox):
    inbox.mailbox = [
        (b"1", _msg("Verify", "Old code 111111")),
        (b"2", _msg("Verify", "Fresh code 222222")),
    ]
    assert mfa.email_code() == "222222"


def test_email_code_reads_html_only_mail(inbox):
    raw = (b"Subject: Verify\r\nContent-Type: text/html\r\n\r\n"
           b"<html><b>Your code is</b> <span>334455</span></html>")
    inbox.mailbox = [(b"1", raw)]
    assert mfa.email_code() == "334455"


def test_email_code_times_out_honestly(inbox, monkeypatch):
    inbox.mailbox = []
    clock = iter([0, 0, 1, 2, 3])
    monkeypatch.setattr(mfa.time, "monotonic", lambda: next(clock))
    with pytest.raises(AssertionError, match="No email .*within 2s"):
        mfa.email_code(match=None, timeout=2)


def test_email_code_without_config_says_what_to_set(monkeypatch):
    for k in ("NOODLE_IMAP_HOST", "NOODLE_IMAP_USER", "NOODLE_IMAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(AssertionError, match="NOODLE_IMAP_HOST"):
        mfa.email_code()
