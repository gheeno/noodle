"""NOOD_0216 — Tier-1 step patterns for the api wok.

Same structure as patterns.py / perf_patterns.py / desktop_patterns.py —
PATTERNS list + match(). Until NOOD_0216 these rows lived inside the web
table (patterns.py), the last trace of the pre-NOOD_0191 "API is a corner of
web" mislabelling; now the api wok owns its grammar like every other wok.

Table order (wok.pattern_priority): this table precedes web in EVERY order —
REST steps are plain I/O available in every scenario (the wok's founding
contract), and the rows sat mid-web-table before web's tail compare
catch-alls, which would otherwise steal "the response json 'x' should equal
'y'". Every phrasing here is namespaced (call at / the response / returns
status) so nothing can shadow a genuine web verb.
"""
import re

from .patterns import _WITHIN, _secs

# All verbs canonical 3rd person (callers have been through
# normalize_subject); trailing s optional — same conventions as patterns.py.
PATTERNS = [
    # --- REST testing (NOOD_0029) — proper HTTP client assertions ------------
    # Set a per-session request header (stored in _REST_HEADERS var).
    (r"^sets? (?:a |an )?request header '([^']+)' to '([^']+)'$",
     'rest_set_header',        lambda m: {'name': m.group(1), 'value': m.group(2)}),
    # Auth sugar (NOOD_0007) — best-practice auth without hand-building headers.
    # Values come through '[VAR]' substitution so secrets stay out of features;
    # Authorization is never logged (runner logs only method/url/status).
    (r"^sets? the bearer token to '([^']+)'$",
     'rest_set_auth',          lambda m: {'scheme': 'bearer', 'token': m.group(1)}),
    (r"^uses? basic auth with '([^']+)' and '([^']+)'$",
     'rest_set_auth',          lambda m: {'scheme': 'basic', 'user': m.group(1), 'password': m.group(2)}),
    (r"^sets? the api key header '([^']+)' to '([^']+)'$",
     'rest_set_header',        lambda m: {'name': m.group(1), 'value': m.group(2)}),
    (r"^fetch(?:es)? an oauth2 token from '([^']+)' with client '([^']+)' and secret '([^']+)'$",
     'rest_oauth2',            lambda m: {'url': m.group(1), 'client_id': m.group(2), 'client_secret': m.group(3)}),
    # NOOD_0216 — the per-scenario cookie jar fills itself from Set-Cookie;
    # this empties it (e.g. to prove an endpoint 401s without the session).
    (r'^clears? the (?:rest |api )?cookies$',
     'rest_clear_cookies',     lambda m: {}),
    # HTTP call: method + path (required) + optional body + optional var store.
    # Path can be absolute (http...) or relative (prepends REST_BASE_URL).
    (r"^performs? (?:a |an )?(GET|POST|PUT|PATCH|DELETE) (?:call|request) "
     r"(?:at|to|on) '([^']+)'"
     r"(?: with (?:request )?body '([^']+)')?"
     r"(?: (?:and )?stor(?:e|es|ing) (?:the )?(?:response )?(?:as|in) [\[`]([^\]`]+)[\]`])?"
     + _WITHIN + r"$",
     'rest_call',              lambda m: {'method': m.group(1).upper(), 'path': m.group(2), 'body': m.group(3), 'var': m.group(4), 'timeout': _secs(m.group(5))}),
    # NOOD_0216 — form-encoded body: same call, Content-Type
    # application/x-www-form-urlencoded for this one request.
    (r"^performs? (?:a |an )?(POST|PUT|PATCH) (?:call|request) "
     r"(?:at|to|on) '([^']+)' with form body '([^']+)'"
     r"(?: (?:and )?stor(?:e|es|ing) (?:the )?(?:response )?(?:as|in) [\[`]([^\]`]+)[\]`])?"
     + _WITHIN + r"$",
     'rest_call',              lambda m: {'method': m.group(1).upper(), 'path': m.group(2), 'body': m.group(3), 'var': m.group(4), 'timeout': _secs(m.group(5)), 'form': True}),
    # NOOD_0216 — multipart file upload over plain REST (the browserless twin
    # of the web wok's set_input_files). File resolves against resources/.
    (r"^performs? (?:a |an )?(POST|PUT) (?:call|request) "
     r"(?:at|to|on) '([^']+)' uploading (?:the )?file '([^']+)' as '([^']+)'"
     + _WITHIN + r"$",
     'rest_upload',            lambda m: {'method': m.group(1).upper(), 'path': m.group(2), 'file': m.group(3), 'field': m.group(4), 'timeout': _secs(m.group(5))}),
    # NOOD_0201 — batch calls: one step, N requests. Seeding 20 rows is one
    # table step (or one `repeated N times` line), never 20 pasted calls —
    # and `expecting status` asserts EVERY call, where a trailing status
    # assertion only ever saw the last one. Table headings name the {placeholder}
    # tokens substituted into the path and body per row.
    (r"^performs? (?:a |an )?(GET|POST|PUT|PATCH|DELETE) (?:call|request)s? "
     r"(?:at|to|on) '([^']+)'"
     r"(?: with (?:request )?body '([^']+)')?"
     r" for each row(?: expecting status (\d+))?"
     + _WITHIN + r":?$",
     'rest_call_each',         lambda m: {'method': m.group(1).upper(), 'path': m.group(2), 'body': m.group(3), 'expect': int(m.group(4)) if m.group(4) else None, 'timeout': _secs(m.group(5))}),
    (r"^performs? (?:a |an )?(GET|POST|PUT|PATCH|DELETE) (?:call|request)s? "
     r"(?:at|to|on) '([^']+)'"
     r"(?: with (?:request )?body '([^']+)')?"
     r" repeated (\d+) times(?: expecting status (\d+))?"
     + _WITHIN + r"$",
     'rest_call_repeat',       lambda m: {'method': m.group(1).upper(), 'path': m.group(2), 'body': m.group(3), 'count': int(m.group(4)), 'expect': int(m.group(5)) if m.group(5) else None, 'timeout': _secs(m.group(6))}),
    # NOOD_0201 — a payload that doesn't fit on one line reads as a docstring.
    # NOOD_0216 — it can store the response too (parity with the quoted form,
    # and the escape hatch for a body containing a single quote).
    (r"^performs? (?:a |an )?(GET|POST|PUT|PATCH|DELETE) (?:call|request) "
     r"(?:at|to|on) '([^']+)' with this (?:request )?body"
     r"(?: (?:and )?stor(?:e|es|ing) (?:the )?(?:response )?(?:as|in) [\[`]([^\]`]+)[\]`])?"
     + _WITHIN + r":?$",
     'rest_call_doc',          lambda m: {'method': m.group(1).upper(), 'path': m.group(2), 'var': m.group(3), 'timeout': _secs(m.group(4))}),
    # NOOD_0216 — GraphQL sugar: the docstring is the query, wrapped into
    # {"query": ...} and POSTed. Assertions stay the normal response json
    # steps ('data.x.y'), plus the errors gate below.
    (r"^performs? (?:a |an )?graphql query (?:at|to|on|against) '([^']+)'"
     + _WITHIN + r":?$",
     'rest_graphql',           lambda m: {'path': m.group(1), 'timeout': _secs(m.group(2))}),
    (r'^the response should (?:have|contain) no graphql errors$',
     'rest_assert_graphql',    lambda m: {}),
    # NOOD_0201 — polling. An async backend answers 202 and finishes the write
    # later, so the very next assertion raced it and the suite went flaky. This
    # is the REST twin of the web wok's smart wait: retry the call until the
    # condition holds or the budget expires (the budget is a ceiling, not a
    # sleep — it returns the instant the condition is met).
    (r"^waits? until (?:a |an )?(?:(GET|POST|PUT|PATCH|DELETE) )?"
     r"(?:(?:call|request)s? )?(?:(?:at|to|on) )?'([^']+)' returns? status (\d+)"
     r"(?: and (?:the )?(?:response )?body contains '([^']+)')?"
     + _WITHIN + r"$",
     'rest_wait_until',        lambda m: {'method': (m.group(1) or 'GET').upper(), 'path': m.group(2), 'expected': int(m.group(3)), 'needle': m.group(4), 'timeout': _secs(m.group(5))}),
    # Status code assertion.
    (r'^the response status(?: code)? should (?:be|equal) (\d+)$',
     'rest_assert_status',     lambda m: {'expected': int(m.group(1))}),
    # NOOD_0201 — contract assertion: the response SHAPE, not one value. A
    # field that silently changed type or vanished passes every substring
    # check ever written; a schema file catches it in one step.
    (r"^the response (?:body )?should match (?:the )?schema '([^']+)'$",
     'rest_assert_schema',     lambda m: {'file': m.group(1)}),
    # Extract a JSON key from the latest response body into a named variable.
    (r"^extracts? (?:json )?(?:key )?'([^']+)' from (?:the )?(?:response|REST_BODY)(?: body)? "
     r"(?:and )?stor(?:e|es|ing) (?:it )?(?:as|in) [\[`]([^\]`]+)[\]`]$",
     'rest_extract_json',      lambda m: {'key': m.group(1), 'var': m.group(2)}),
    # Body contains a single string (key or value).
    (r"^the response body should contain '([^']+)'$",
     'rest_assert_body',       lambda m: {'needle': m.group(1)}),
    # NOOD_0201 — typed JSON assertions: substring checks can't tell
    # "count": 20 from "count": 200, or the string "true" from the boolean.
    # Path is the rest_extract_json dotted/indexed walk; '$' means the root.
    (r"^the response json '([^']*)' should (?:be|equal) '([^']*)'$",
     'rest_assert_json',       lambda m: {'path': m.group(1), 'op': 'equal', 'expected': m.group(2)}),
    (r"^the response json '([^']*)' should contain '([^']+)'$",
     'rest_assert_json',       lambda m: {'path': m.group(1), 'op': 'contain', 'expected': m.group(2)}),
    (r"^the response json '([^']*)' should have (\d+) items?$",
     'rest_assert_json_count', lambda m: {'path': m.group(1), 'count': int(m.group(2))}),
    # Body contains — table driven (Key / Value rows; empty Value = key-exists check).
    (r'^the response body should contain:?$',
     'rest_assert_body_table', lambda m: {}),
    # Single header assertion.
    (r"^the response header '([^']+)' should (?:be|equal|contain) '([^']+)'$",
     'rest_assert_header',     lambda m: {'name': m.group(1), 'value': m.group(2)}),
    # Headers — table driven (Header / Value rows).
    (r'^the response headers? should contain:?$',
     'rest_assert_header_table', lambda m: {}),
]

def match(step_text: str):
    """Return (action_type, params) or None — same contract as patterns.match."""
    for pattern, action_type, extractor in PATTERNS:
        m = re.match(pattern, step_text, re.IGNORECASE)
        if m:
            return action_type, extractor(m)
    return None
