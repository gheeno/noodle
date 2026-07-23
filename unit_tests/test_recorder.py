"""
Phase 6 — Recorder unit tests.
No real browser launched; Playwright is not imported at test time.
"""

from noodle.recorder.recorder import Recorder
from noodle.recorder.sensitives import redact, suggest_var_name

# ---------------------------------------------------------------------------
# sensitives.redact
# ---------------------------------------------------------------------------

class TestRedact:
    def test_email_value_detected(self):
        placeholder, var = redact("user@example.com")
        assert var == "EMAIL"
        assert placeholder == "[EMAIL]"

    def test_email_field_name_hint(self):
        placeholder, var = redact("anything", field_name="email address")
        assert var == "EMAIL"

    def test_password_field_name_hint(self):
        placeholder, var = redact("s3cr3t!", field_name="password")
        assert var == "PASSWORD"

    def test_visa_card_detected(self):
        placeholder, var = redact("4111111111111111")
        assert var == "CARD_NUMBER"

    def test_mastercard_detected(self):
        placeholder, var = redact("5500005555555559")
        assert var == "CARD_NUMBER"

    def test_non_sensitive_value_passthrough(self):
        value, var = redact("John Smith")
        assert var is None
        assert value == "John Smith"

    def test_non_sensitive_field_passthrough(self):
        value, var = redact("New York", field_name="city")
        assert var is None
        assert value == "New York"


class TestSuggestVarName:
    def test_email_field(self):
        assert suggest_var_name("email field") == "EMAIL"

    def test_card_field(self):
        assert suggest_var_name("card number") == "CARD_NUMBER"

    def test_generic_field(self):
        name = suggest_var_name("first name")
        assert name == "FIRST_NAME"


# ---------------------------------------------------------------------------
# Recorder._on_navigate
# ---------------------------------------------------------------------------

class TestOnNavigate:
    def test_first_url_produces_given(self):
        r = Recorder("out.feature")
        r._on_navigate("https://example.com")
        assert r.steps == ['Given User is on "https://example.com"']

    def test_subsequent_url_produces_when(self):
        r = Recorder("out.feature")
        r._on_navigate("https://example.com")
        r._on_navigate("https://example.com/cart")
        assert r.steps[-1] == 'When User navigates to "https://example.com/cart"'

    def test_same_url_deduplicated(self):
        r = Recorder("out.feature")
        r._on_navigate("https://example.com")
        r._on_navigate("https://example.com")
        assert len(r.steps) == 1

    def test_non_http_url_ignored(self):
        r = Recorder("out.feature")
        r._on_navigate("about:blank")
        assert r.steps == []


# ---------------------------------------------------------------------------
# Recorder._on_fill
# ---------------------------------------------------------------------------

class TestOnFill:
    def test_email_value_becomes_placeholder(self):
        r = Recorder("out.feature")
        r._on_fill("email", "user@example.com")
        assert "[EMAIL]" in r.steps[0]

    def test_non_sensitive_value_kept(self):
        r = Recorder("out.feature")
        r._on_fill("city", "New York")
        assert '"New York"' in r.steps[0]

    def test_empty_value_ignored(self):
        r = Recorder("out.feature")
        r._on_fill("name", "")
        assert r.steps == []

    def test_duplicate_fill_deduplicated(self):
        r = Recorder("out.feature")
        r._on_fill("username", "standard_user")
        r._on_fill("username", "standard_user")
        assert len(r.steps) == 1


# ---------------------------------------------------------------------------
# Recorder._write_feature
# ---------------------------------------------------------------------------

class TestWriteFeature:
    def test_writes_valid_gherkin(self, tmp_path):
        out = tmp_path / "test.feature"
        r = Recorder(str(out), feature_name="Login Test")
        r.steps = [
            'Given User is on "https://example.com"',
            'When User clicks "Login"',
            'When User enters [EMAIL] in the email field',
        ]
        r._write_feature()

        content = out.read_text()
        assert "Feature: Login Test" in content
        assert "Scenario: Login Test" in content
        assert "@web" in content
        assert 'Given User is on "https://example.com"' in content
        assert "And User clicks" in content
        assert "And User enters" in content

    def test_creates_parent_directories(self, tmp_path):
        out = tmp_path / "a" / "b" / "c" / "test.feature"
        r = Recorder(str(out))
        r.steps = ['Given User is on "https://example.com"']
        r._write_feature()
        assert out.exists()

    def test_empty_steps_writes_empty_scenario(self, tmp_path):
        out = tmp_path / "empty.feature"
        r = Recorder(str(out))
        r._write_feature()
        content = out.read_text()
        assert "Feature:" in content
        assert "Scenario:" in content
