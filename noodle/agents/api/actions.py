"""NOOD_0216 — the api wok's protocol engine, extracted from the runner.

Everything here is browserless and page-free: the session state is the
scenario's captured-vars dict (headers/auth in _REST_HEADERS, cookies in
_REST_COOKIES, oauth2 grant in _REST_OAUTH, last response in
REST_STATUS/REST_BODY/REST_HEADERS), exactly as before the move — the
runner's _execute_rest dispatches into these and owns the step-text errors.
"""
import json
import time

from noodle.log import logger

from . import rest_client

# NOOD_0201 — a batch step is still a functional test, not a load test; the
# perf wok's loadgen owns volume. The cap catches a generated "repeated
# 100000 times" before it hammers someone's laptop app.
REST_BATCH_CAP = 1000

# NOOD_0216 — request/response evidence: per-call cap on recorded body bytes,
# per-scenario cap on recorded calls (a 1000-call batch is a count, not a
# transcript). The list lands in Allure as the scenario's "api log".
_EVIDENCE_BODY_CAP = 2000
_EVIDENCE_CALL_CAP = 200


def _record(evidence, method, url, status, body, rbody):
    if evidence is None:
        return
    if len(evidence) >= _EVIDENCE_CALL_CAP:
        if len(evidence) == _EVIDENCE_CALL_CAP:
            evidence.append(
                {"truncated": f"api log capped at {_EVIDENCE_CALL_CAP} calls"})
        return
    if isinstance(body, bytes):
        body = f"<{len(body)} bytes multipart>"
    evidence.append({
        "method": method, "url": url, "status": status,
        "request_body": (body or "")[:_EVIDENCE_BODY_CAP] or None,
        "response_body": (rbody or "")[:_EVIDENCE_BODY_CAP],
    })


def oauth2_fetch(vars: dict, url: str, client_id: str, client_secret: str):
    """Client-credentials grant → Authorization: Bearer <token> in _REST_HEADERS.
    Grant params are kept in vars so a later 401 can refresh once (rest_one).
    The token/secret are never logged."""
    import urllib.parse
    form = urllib.parse.urlencode({
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
    })
    status, resp, _ = rest_client.rest_call(
        'POST', url, form, {'Content-Type': 'application/x-www-form-urlencoded'})
    assert status == 200, f"OAuth2 token fetch failed: {status} {resp[:200]}"
    try:
        token = json.loads(resp).get('access_token')
    except ValueError:
        token = None
    assert token, f"OAuth2 response has no access_token: {resp[:200]}"
    hdrs = json.loads(vars.get('_REST_HEADERS', '{}'))
    hdrs['Authorization'] = f"Bearer {token}"
    vars['_REST_HEADERS'] = json.dumps(hdrs)
    vars['_REST_OAUTH'] = json.dumps(
        {'url': url, 'client_id': client_id, 'client_secret': client_secret})


def rest_one(vars: dict, method: str, path: str, body, timeout,
             evidence: list | None = None, headers: dict | None = None):
    """One HTTP call with the session's headers, cookie jar, REST_BASE_URL
    join and the single oauth2 401-refresh; records REST_STATUS/BODY/HEADERS
    (and the request/response pair into `evidence`). Shared by rest_call, the
    NOOD_0201 batch steps and polling so every call gets the same auth
    semantics as a lone one. Returns (status, url, body)."""
    import urllib.parse
    base = vars.get('REST_BASE_URL', '')
    url = path if path.startswith('http') else base.rstrip('/') + '/' + path.lstrip('/')
    host = urllib.parse.urlsplit(url).netloc
    jar = json.loads(vars.get('_REST_COOKIES', '{}'))

    def _headers():
        hdrs = json.loads(vars.get('_REST_HEADERS', '{}'))
        hdrs.update(headers or {})
        # NOOD_0216 — Postman-style session cookies: the per-host jar rides on
        # every call unless the test set a Cookie header itself.
        if jar.get(host) and not any(k.lower() == 'cookie' for k in hdrs):
            hdrs['Cookie'] = '; '.join(f'{k}={v}' for k, v in jar[host].items())
        return hdrs

    status, rbody, rheaders = rest_client.rest_call(
        method, url, body, _headers(), timeout)
    if status == 401 and '_REST_OAUTH' in vars:
        # Token likely expired — refresh once and retry once, never loop.
        o = json.loads(vars['_REST_OAUTH'])
        oauth2_fetch(vars, o['url'], o['client_id'], o['client_secret'])
        status, rbody, rheaders = rest_client.rest_call(
            method, url, body, _headers(), timeout)
    # NOOD_0216 — fill the jar from Set-Cookie, keyed by host so a session
    # never walks to another server. ponytail: name=value only via the
    # headers dict (one Set-Cookie per response; no path/expiry/secure) —
    # swap in http.cookiejar if a suite ever needs full semantics.
    set_cookie = next((v for k, v in dict(rheaders).items()
                       if k.lower() == 'set-cookie'), None)
    if set_cookie:
        first = set_cookie.split(';', 1)[0]
        if '=' in first:
            name, value = first.split('=', 1)
            jar.setdefault(host, {})[name.strip()] = value.strip()
            vars['_REST_COOKIES'] = json.dumps(jar)
    vars['REST_STATUS'] = str(status)
    vars['REST_BODY'] = rbody
    vars['REST_HEADERS'] = json.dumps(dict(rheaders))
    _record(evidence, method, url, status, body, rbody)
    return status, url, rbody


def rest_batch(vars: dict, action: dict, rows: list[dict],
               evidence: list | None = None):
    """N calls from one step (NOOD_0201): each row's {placeholder} tokens are
    substituted into the path/body, and `expecting status` asserts EVERY call —
    a trailing status assertion after N pasted calls only ever saw the last."""
    total = len(rows)
    assert total <= REST_BATCH_CAP, (
        f"{total} calls in one step exceeds the batch cap ({REST_BATCH_CAP}). "
        "Volume belongs to the perf wok: \"runs a load test on '<url>' with "
        "N requests\".")
    for n, subs in enumerate(rows, 1):
        path, body = action['path'], action.get('body')
        for k, v in subs.items():
            path = path.replace('{%s}' % k, v)
            if body:
                body = body.replace('{%s}' % k, v)
        status, url, rbody = rest_one(
            vars, action['method'], path, body, action.get('timeout'), evidence)
        if action.get('expect') is not None and status != action['expect']:
            detail = ', '.join(f'{k}={v}' for k, v in subs.items())
            raise AssertionError(
                f"Call {n} of {total} ({detail}): {action['method']} {url} → "
                f"expected status {action['expect']}, got {status}. "
                f"Body: {rbody[:200]}")
    logger.info(f"\n  🌐 {action['method']} ×{total} → "
                + (f"all {action['expect']}" if action.get('expect') is not None
                   else "done"))


def rest_poll(vars: dict, action: dict, evidence: list | None = None):
    """NOOD_0201 — call until the condition holds or the budget runs out.

    The REST twin of the web wok's smart wait: an endpoint that answers 202 and
    finishes the write asynchronously made the next assertion a race. Returns
    on the first satisfying response (the budget is a ceiling, not a sleep),
    and the last response stays in REST_STATUS/REST_BODY either way."""
    from noodle.config import rest_timeout
    budget = rest_timeout(action.get('timeout'))
    deadline = time.monotonic() + budget
    attempts, status, body = 0, None, ''
    while True:
        attempts += 1
        # Each attempt gets what's left of the window, so one hung call can't
        # outlive the budget the step was given.
        left = max(0.5, deadline - time.monotonic())
        status, url, body = rest_one(vars, action['method'], action['path'],
                                     None, left, evidence)
        ok = status == action['expected'] and \
            (not action.get('needle') or action['needle'] in body)
        if ok:
            logger.info(f"\n  ⏳ {action['method']} {url} → {status} after "
                        f"{attempts} attempt(s)")
            return
        if time.monotonic() >= deadline:
            want = f"status {action['expected']}"
            if action.get('needle'):
                want += f" and body containing '{action['needle']}'"
            raise AssertionError(
                f"{action['method']} {url} never returned {want} within "
                f"{budget:g}s ({attempts} attempts). Last: {status} "
                f"{body[:200]}\n"
                f"  → Endpoint slower than the budget? Append \"within "
                f"{int(budget * 2)} seconds\" to this step, or raise "
                f"NOODLE_REST_TIMEOUT.")
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def graphql_call(vars: dict, path: str, query: str, timeout,
                 evidence: list | None = None):
    """NOOD_0216 — GraphQL is a POST with a JSON envelope; wrap the step's
    docstring query and send it through the normal REST machinery, so auth,
    cookies, base-URL join and the response steps all just work."""
    return rest_one(vars, 'POST', path, json.dumps({'query': query}),
                    timeout, evidence)


def json_path(data, path: str):
    """Walk a dotted path with optional [n] indexes ('data.items[0].id')
    through parsed JSON. Raises AssertionError naming the part that missed."""
    import re
    cur = data
    for name, idx in re.findall(r'([^.\[\]]+)|\[(\d+)\]', path):
        if name:
            if not isinstance(cur, dict) or name not in cur:
                raise AssertionError(f"Key '{name}' not found walking '{path}' in response JSON")
            cur = cur[name]
        else:
            i = int(idx)
            if not isinstance(cur, list) or i >= len(cur):
                raise AssertionError(f"Index [{i}] out of range walking '{path}' in response JSON")
            cur = cur[i]
    return cur


def json_typed_eq(actual, expected: str) -> bool:
    """NOOD_0201 — compare a parsed-JSON value against the step's quoted
    string the way the JSON meant it: booleans and null by name, numbers by
    value (so '20' ≠ 200 and '1.0' == 1), everything else as text."""
    if isinstance(actual, bool):
        return expected.strip().lower() == str(actual).lower()
    if actual is None:
        return expected.strip().lower() in ('null', 'none')
    if isinstance(actual, (int, float)):
        try:
            return float(expected) == actual
        except ValueError:
            return False
    return str(actual) == expected


# NOOD_0201 — the JSON Schema subset an API response contract actually uses.
# ponytail: type/required/properties/items/enum/bounds, hand-walked in ~40
# lines instead of declaring the `jsonschema` package for the base install
# (it is only ever present transitively today). Ceiling: no $ref, no
# oneOf/allOf/not, no format. If a workspace needs those, declare jsonschema
# and swap schema_errors for its validator — the step contract stays.
_JSON_TYPES = {'string': str, 'number': (int, float), 'integer': int,
               'boolean': bool, 'object': dict, 'array': list,
               'null': type(None)}


def _type_ok(value, name: str) -> bool:
    py = _JSON_TYPES.get(name)
    if py is None:
        return True                      # unknown keyword: not ours to police
    if name in ('number', 'integer') and isinstance(value, bool):
        return False                     # JSON booleans are not numbers
    return isinstance(value, py)


def schema_errors(data, schema: dict, where: str = '$') -> list[str]:
    """Every way `data` violates `schema`, each naming its own path."""
    errs: list[str] = []
    if not isinstance(schema, dict):
        return errs
    types = schema.get('type')
    if types is not None:
        names = types if isinstance(types, list) else [types]
        if not any(_type_ok(data, n) for n in names):
            got = 'null' if data is None else type(data).__name__
            return [f"{where}: expected type {'|'.join(names)}, got {got}"]
    if (enum := schema.get('enum')) is not None and data not in enum:
        errs.append(f"{where}: {data!r} is not one of {enum}")
    if isinstance(data, dict):
        for key in schema.get('required') or []:
            if key not in data:
                errs.append(f"{where}: missing required property {key!r}")
        for key, sub in (schema.get('properties') or {}).items():
            if key in data:
                errs += schema_errors(data[key], sub, f"{where}.{key}")
    if isinstance(data, list):
        if (lo := schema.get('minItems')) is not None and len(data) < lo:
            errs.append(f"{where}: {len(data)} items, minimum {lo}")
        if (hi := schema.get('maxItems')) is not None and len(data) > hi:
            errs.append(f"{where}: {len(data)} items, maximum {hi}")
        if isinstance(schema.get('items'), dict):
            for i, item in enumerate(data):
                errs += schema_errors(item, schema['items'], f"{where}[{i}]")
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if (lo := schema.get('minimum')) is not None and data < lo:
            errs.append(f"{where}: {data} is below minimum {lo}")
        if (hi := schema.get('maximum')) is not None and data > hi:
            errs.append(f"{where}: {data} is above maximum {hi}")
    return errs


def rest_json(vars: dict, path: str):
    """The latest response body parsed as JSON, walked to `path` ('$'/'' = root)."""
    body = vars.get('REST_BODY', '')
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise AssertionError(f"REST_BODY is not valid JSON: {body[:100]}") from exc
    return data if path in ('', '$', '.') else json_path(data, path)
