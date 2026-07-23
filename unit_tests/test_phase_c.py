"""Phase C — healing telemetry + Azure Key Vault loader (no Azure, no network)."""
import os

from noodle import healing
from noodle.secrets_akv import _apply, _normalize

# --- healing telemetry -------------------------------------------------------

def test_healing_records_and_reports(tmp_path):
    healing.reset()
    healing.record("Add to cart", "partial-text", "matched on 'Add'")
    healing.record("burger menu", "vision-llm")
    report = tmp_path / "healing-report.txt"
    healing.write_report(str(report))

    text = report.read_text()
    assert "2 event(s)" in text
    assert "Add to cart" in text and "burger menu" in text
    # POM suggestion line per distinct locator
    assert "add to cart:" in text and "burger menu:" in text


def test_healing_dedupes_suggestions(tmp_path):
    healing.reset()
    healing.record("Login", "scroll")
    healing.record("Login", "partial-text")     # same locator twice
    healing.write_report(str(tmp_path / "r.txt"))
    suggestions = healing._suggestions()
    assert len(suggestions) == 1


def test_healing_report_noop_when_empty(tmp_path):
    healing.reset()
    report = tmp_path / "none.txt"
    healing.write_report(str(report))
    assert not report.exists()


# --- Key Vault loader --------------------------------------------------------

def test_normalize_dashes_to_underscore_upper():
    assert _normalize("sauce-password") == "SAUCE_PASSWORD"
    assert _normalize("base-url") == "BASE_URL"


class _FakeProp:
    def __init__(self, name):
        self.name = name


class _FakeSecret:
    def __init__(self, value):
        self.value = value


class _FakeClient:
    def __init__(self, secrets):
        self._secrets = secrets

    def list_properties_of_secrets(self):
        return [_FakeProp(n) for n in self._secrets]

    def get_secret(self, name):
        return _FakeSecret(self._secrets[name])


def test_akv_apply_writes_environ(monkeypatch):
    monkeypatch.delenv("SAUCE_PASSWORD", raising=False)
    client = _FakeClient({"sauce-password": "secret_sauce"})
    count = _apply(client)
    assert count == 1
    assert os.environ["SAUCE_PASSWORD"] == "secret_sauce"


def test_akv_apply_respects_no_override(monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://local")
    client = _FakeClient({"base-url": "https://vault"})
    _apply(client, override=False)
    assert os.environ["BASE_URL"] == "https://local"   # existing kept


def test_akv_apply_override_wins(monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://local")
    client = _FakeClient({"base-url": "https://vault"})
    _apply(client, override=True)
    assert os.environ["BASE_URL"] == "https://vault"
