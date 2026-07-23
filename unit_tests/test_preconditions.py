import json

import pytest

from noodle.preconditions import parse_call


def test_parse_call_with_body():
    method, url, body = parse_call('PATCH http://x/api/test/stock {"movieId": 1, "stock": 0}')
    assert method == "PATCH"
    assert url == "http://x/api/test/stock"
    assert body == {"movieId": 1, "stock": 0}


def test_parse_call_without_body():
    method, url, body = parse_call("POST http://x/api/test/reset")
    assert (method, url, body) == ("POST", "http://x/api/test/reset", None)


def test_parse_call_lowercases_to_upper_method():
    assert parse_call("post http://x")[0] == "POST"


def test_parse_call_rejects_missing_url():
    with pytest.raises(ValueError):
        parse_call("POST")


def test_parse_call_rejects_bad_json():
    with pytest.raises(json.JSONDecodeError):
        parse_call("POST http://x not-json")
