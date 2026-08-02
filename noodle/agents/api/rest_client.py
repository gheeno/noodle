"""The api wok's transport — stdlib urllib, no browser, no driver.

Lived at agents/web/rest_client.py until NOOD_0216 (a shim remains there);
the api wok owns its engine like every other wok.
"""
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid

from noodle.config import rest_timeout
from noodle.target_policy import check_target


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """NOOD_0177 — urllib follows redirects silently, and rest_set_auth puts a
    bearer token on the request, so a 302 to another host walked the credential
    across with it. Surface the redirect as a normal response instead: a test
    that means to follow one can assert the Location header and issue the next
    call itself, through check_target like any other."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(
    urllib.request.HTTPHandler, urllib.request.HTTPSHandler, _NoRedirect)


def rest_call(method, url, body=None, headers=None, timeout=None):
    """HTTP request via stdlib. Returns (status_code, body_str, headers_dict).

    `timeout` (seconds) is the step's own "within N seconds"; without one the
    run-wide budget applies (NOODLE_REST_TIMEOUT, default 30s — NOOD_0182)."""
    # NOOD_0177 — the default opener also carries FileHandler/FTPHandler, so
    # REST_BASE_URL='file:///etc' + a GET turned this into a local file reader
    # whose body landed in an assertable {var:REST_BODY}. _OPENER carries only
    # http/https; check_target refuses metadata endpoints and honours the
    # allowlist.
    check_target(url, what="REST call")
    h = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (compatible; Noodle/1.0)',
    }
    h.update(headers or {})
    data = body.encode() if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    budget = rest_timeout(timeout)
    try:
        with _OPENER.open(req, timeout=budget) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)
    # NOOD_0182 — a slow-but-healthy endpoint used to die here as a raw
    # URLError(timed out) with no hint that the budget was the problem. Name it,
    # and name both ways to raise it. Non-timeout URLErrors (DNS, refused,
    # TLS) still propagate untouched — those are real failures.
    except (TimeoutError, socket.timeout) as e:
        raise AssertionError(_slow(method, url, budget)) from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            raise AssertionError(_slow(method, url, budget)) from e
        raise


def multipart_body(field, filename, content: bytes):
    """(body_bytes, content_type) for one file as multipart/form-data
    (NOOD_0216) — the browserless twin of the web wok's set_input_files."""
    boundary = uuid.uuid4().hex
    head = (f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{field}"; '
            f'filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode()
    tail = f'\r\n--{boundary}--\r\n'.encode()
    return head + content + tail, f'multipart/form-data; boundary={boundary}'


def _slow(method, url, budget) -> str:
    return (
        f"{method} {url} did not respond within {budget:g}s.\n"
        f"  → Endpoint is slow, not broken? Give this step more time: append "
        f"\"within {int(budget * 2)} seconds\" to it,\n"
        f"    or raise the run-wide budget in .env: NOODLE_REST_TIMEOUT="
        f"{int(budget * 2)} (seconds)."
    )
