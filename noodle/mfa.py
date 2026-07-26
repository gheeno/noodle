"""NOOD_0184 — MFA code sources: TOTP (RFC 6238) and email-inbox OTP.

Two ways a test gets past an MFA challenge, both stdlib-only:

  totp_code(secret)   — the 6-digit authenticator-app code, computed from the
                        account's enrolled base32 secret (RFC 6238 / RFC 4226).
  email_code(...)     — poll an IMAP inbox until a fresh message carrying a
                        numeric code arrives, and return that code.

What is deliberately NOT here: Entrust-style push approvals, hardware tokens
and SMS. Those are designed to be un-automatable — enroll the test account
with a TOTP soft token instead, or log in once and reuse the session
(save_session / NOODLE_STORAGE_STATE).
"""
import base64
import email
import email.policy
import hashlib
import hmac
import imaplib
import os
import re
import struct
import time

_UNRESOLVED_RE = re.compile(r'^\[([A-Za-z0-9_ ]+)\]$|^\{env:([^}]+)\}$')

# ponytail: 6 digits first (the overwhelming default), wider 4-8 fallback —
# a bare \d{4,8} scan would happily return the year out of a date.
_CODE_RES = (re.compile(r'\b(\d{6})\b'), re.compile(r'\b(\d{4,8})\b'))


def totp_code(secret: str | None, digits: int = 6, period: int = 30,
              now: float | None = None) -> str:
    """RFC 6238 TOTP from a base32 authenticator secret — what Google
    Authenticator / Microsoft Authenticator / an Entrust soft token display.
    Accepts the secret as apps show it (spaces/dashes, any case, unpadded)."""
    if not secret:
        secret = os.getenv('NOODLE_TOTP_SECRET')
    if not secret:
        raise AssertionError(
            "No TOTP secret: name one in the step "
            "(\"... the one-time code for {env:MFA_SECRET} ...\") or set "
            "NOODLE_TOTP_SECRET in secrets.env. The secret is the base32 "
            "string shown when the account enrolled its authenticator."
        )
    m = _UNRESOLVED_RE.match(secret.strip())
    if m:
        name = m.group(1) or m.group(2)
        raise AssertionError(
            f"TOTP secret '{name}' is not set — add it to secrets.env "
            "(the base32 authenticator secret from MFA enrollment)."
        )
    clean = re.sub(r'[\s-]', '', secret).upper()
    try:
        key = base64.b32decode(clean + '=' * (-len(clean) % 8))
    except Exception as exc:
        raise AssertionError(
            f"TOTP secret is not valid base32 ({exc}). Expected the "
            "authenticator enrollment string, e.g. 'JBSW Y3DP EHPK 3PXP'."
        ) from exc
    counter = struct.pack('>Q', int(now if now is not None else time.time()) // period)
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (int.from_bytes(digest[off:off + 4], 'big') & 0x7FFFFFFF) % 10 ** digits
    return f'{code:0{digits}d}'


def _message_text(raw: bytes) -> str:
    """Subject + decoded text of every text/* part, HTML tags stripped —
    verification emails are frequently HTML-only."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    parts = [str(msg.get('Subject', ''))]
    for part in msg.walk():
        if part.get_content_maintype() != 'text':
            continue
        try:
            body = part.get_content()
        except Exception:
            continue
        if part.get_content_subtype() == 'html':
            body = re.sub(r'<[^>]+>', ' ', body)
        parts.append(body)
    return '\n'.join(parts)


def _extract_code(text: str) -> str | None:
    for rx in _CODE_RES:
        m = rx.search(text)
        if m:
            return m.group(1)
    return None


def _scan_unseen(host: str, port: int, user: str, password: str,
                 folder: str, match: str | None) -> str | None:
    """One pass over UNSEEN messages, newest first. Scanning PEEKs (doesn't
    consume other mail); only the message whose code we return is marked
    seen, so the same code is never handed out twice."""
    with imaplib.IMAP4_SSL(host, port) as box:
        box.login(user, password)
        box.select(folder)
        _, data = box.search(None, 'UNSEEN')
        for num in reversed((data[0] or b'').split()):
            _, fetched = box.fetch(num, '(BODY.PEEK[])')
            raw = next((p[1] for p in fetched if isinstance(p, tuple)), None)
            if raw is None:
                continue
            text = _message_text(raw)
            if match and match.lower() not in text.lower():
                continue
            code = _extract_code(text)
            if code:
                box.store(num, '+FLAGS', '\\Seen')
                return code
    return None


def email_code(match: str | None = None, timeout: float | None = None) -> str:
    """Poll the NOODLE_IMAP_* inbox until an unseen message (optionally
    containing `match`) carries a 4-8 digit code; return the code."""
    host = os.getenv('NOODLE_IMAP_HOST')
    user = os.getenv('NOODLE_IMAP_USER')
    password = os.getenv('NOODLE_IMAP_PASSWORD')
    if not (host and user and password):
        raise AssertionError(
            "Email-code steps need the inbox configured in secrets.env: "
            "NOODLE_IMAP_HOST, NOODLE_IMAP_USER, NOODLE_IMAP_PASSWORD "
            "(optional: NOODLE_IMAP_PORT=993, NOODLE_IMAP_FOLDER=INBOX). "
            "Use a dedicated test inbox, never a personal account."
        )
    port = int(os.getenv('NOODLE_IMAP_PORT', '993'))
    folder = os.getenv('NOODLE_IMAP_FOLDER', 'INBOX')
    budget = timeout if timeout is not None else float(os.getenv('NOODLE_MAIL_TIMEOUT', '90'))
    deadline = time.monotonic() + budget
    last_err = None
    while True:
        try:
            code = _scan_unseen(host, port, user, password, folder, match)
            if code:
                return code
        except (imaplib.IMAP4.error, OSError) as exc:
            last_err = exc          # transient IMAP hiccup — keep polling
        if time.monotonic() >= deadline:
            detail = f" (last IMAP error: {last_err})" if last_err else ""
            wanted = f" containing '{match}'" if match else ""
            raise AssertionError(
                f"No email{wanted} with a code arrived in {folder} on {host} "
                f"within {budget:.0f}s{detail}. Raise the budget with "
                "'within N seconds' on the step or NOODLE_MAIL_TIMEOUT."
            )
        time.sleep(3)
