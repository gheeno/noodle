"""Phase D — network mocking, API setup/teardown, test-data fixtures."""
from noodle.agents.web.actions import flatten_data, load_data
from noodle.resolver.patterns import match, normalize_subject


def _resolve(text):
    return match(normalize_subject(text))


# --- pattern routing ---------------------------------------------------------

def test_mock_route_with_body():
    action, p = _resolve('mocks "**/api/cart" with status 200 and body \'{"items":[]}\'')
    assert action == "mock_route"
    assert p["url"] == "**/api/cart"
    assert p["status"] == 200
    assert p["body"] == '{"items":[]}'


def test_mock_route_without_body():
    action, p = _resolve('mocks "**/api/x" with status 500')
    assert action == "mock_route"
    assert p["status"] == 500 and p["body"] is None


def test_block_route():
    action, p = _resolve('blocks requests to "**/analytics/**"')
    assert action == "block_route"
    assert p["url"] == "**/analytics/**"


def test_api_call_post_with_body():
    action, p = _resolve('calls POST "https://api.test/seed" with body \'{"id":1}\'')
    assert action == "api_call"
    assert p["method"] == "POST"
    assert p["url"] == "https://api.test/seed"
    assert p["body"] == '{"id":1}'


def test_api_call_get():
    action, p = _resolve('calls GET "https://api.test/reset"')
    assert action == "api_call" and p["method"] == "GET" and p["body"] is None


def test_load_data():
    action, p = _resolve('loads test data from "fixtures/users.yaml"')
    assert action == "load_data"
    assert p["file"] == "fixtures/users.yaml"


# --- data fixture helpers ----------------------------------------------------

def test_flatten_data_uppercases_keys():
    assert flatten_data({"sauce username": "bob", "Qty": 3}) == {
        "SAUCE_USERNAME": "bob", "QTY": "3"}


def test_load_data_reads_yaml(tmp_path):
    f = tmp_path / "d.yaml"
    f.write_text("base url: https://x\norder id: 42\n")
    assert load_data(str(f)) == {"BASE_URL": "https://x", "ORDER_ID": "42"}


def test_load_data_rejects_non_mapping(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("- just\n- a\n- list\n")
    try:
        load_data(str(f))
        assert False, "expected AssertionError"
    except AssertionError as e:
        assert "mapping" in str(e)
