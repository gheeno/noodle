import urllib.error
import urllib.request


def rest_call(method, url, body=None, headers=None):
    """HTTP request via stdlib. Returns (status_code, body_str, headers_dict)."""
    h = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (compatible; Noodle/1.0)',
    }
    h.update(headers or {})
    data = body.encode() if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)
