import urllib.error
import urllib.request

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


def rest_call(method, url, body=None, headers=None):
    """HTTP request via stdlib. Returns (status_code, body_str, headers_dict)."""
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
    try:
        with _OPENER.open(req, timeout=30) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)
