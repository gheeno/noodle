"""NOOD_0033 — unified {source:name} parameter syntax.

One delimiter, three sources: {env:X} (config), {var:X} (run-captured),
{pom:x} (explicit pom.yaml pin). Legacy [X] / `X` / bare {x} still resolve
behind a once-per-run deprecation warning.
"""
import logging
from contextlib import contextmanager

import pytest

from noodle.agents.web import pom
from noodle.orchestrator import runner
from noodle.orchestrator.runner import substitute
from noodle.resolver.patterns import normalize_phrasing


@pytest.fixture(autouse=True)
def _fresh_warnings():
    runner._deprecation_warned.clear()
    yield
    runner._deprecation_warned.clear()


@contextmanager
def _capture_noodle_warnings(caplog):
    """noodle.log's logger has propagate=False by design (keeps console
    output correct under pytest's capsys) — but that also means pytest's
    caplog handler, which is only ever attached to the root logger, never
    sees its records, so `caplog.at_level("WARNING", logger="noodle")`
    alone leaves caplog.text empty no matter what runs inside the block.
    Attach caplog's handler directly to the noodle logger instead."""
    logger = logging.getLogger("noodle")
    with caplog.at_level("WARNING", logger="noodle"):
        logger.addHandler(caplog.handler)
        try:
            yield
        finally:
            logger.removeHandler(caplog.handler)


class TestEnvRefs:
    def test_env_resolves_from_environment(self, monkeypatch):
        monkeypatch.setenv("MY_URL", "http://x")
        assert substitute("go to {env:MY_URL}/api") == "go to http://x/api"

    def test_env_key_normalized_case_and_spaces(self, monkeypatch):
        monkeypatch.setenv("MY_URL", "http://x")
        assert substitute("{env:my url}") == "http://x"

    def test_env_prefers_captured_store_fallback(self, monkeypatch):
        monkeypatch.setenv("K", "from-env")
        assert substitute("{env:K}", {"K": "from-store"}) == "from-store"

    def test_unknown_env_left_untouched(self):
        assert substitute("{env:NOPE_NOT_SET}") == "{env:NOPE_NOT_SET}"


class TestVarRefs:
    def test_var_resolves_from_store(self):
        assert substitute("id is {var:DEVICE_ID}", {"DEVICE_ID": "42"}) == "id is 42"

    def test_var_never_reads_environment(self, monkeypatch):
        monkeypatch.setenv("SECRET_LEAK", "boo")
        assert substitute("{var:SECRET_LEAK}") == "{var:SECRET_LEAK}"

    def test_var_key_normalized(self):
        assert substitute("{var:device id}", {"DEVICE_ID": "42"}) == "42"

    def test_var_inside_json_body(self):
        body = '{"id": "{var:DEVICE_ID}", "n": 1}'
        assert substitute(body, {"DEVICE_ID": "42"}) == '{"id": "42", "n": 1}'


class TestJsonImmunity:
    def test_plain_json_never_touched(self, monkeypatch):
        monkeypatch.setenv("NAME", "oops")
        body = '{"name":"Alice","data":{"year":2026}}'
        assert substitute(body) == body

    def test_pom_ref_never_substituted(self, monkeypatch):
        monkeypatch.setenv("BURGER_MENU", "oops")
        assert substitute("{pom:burger menu}") == "{pom:burger menu}"


class TestLegacyShim:
    def test_brackets_still_resolve_with_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("OLD_KEY", "v")
        with _capture_noodle_warnings(caplog):
            assert substitute("[OLD_KEY]") == "v"
        assert "{env:OLD_KEY}" in caplog.text

    def test_backticks_still_resolve_with_warning(self, caplog):
        with _capture_noodle_warnings(caplog):
            assert substitute("`X`", {"X": "v"}) == "v"
        assert "{var:X}" in caplog.text

    def test_warning_fires_once_per_ref(self, monkeypatch, caplog):
        monkeypatch.setenv("OLD_KEY", "v")
        with _capture_noodle_warnings(caplog):
            substitute("[OLD_KEY]")
            substitute("[OLD_KEY]")
        assert caplog.text.count("Deprecated syntax [OLD_KEY]") == 1

    def test_unknown_legacy_refs_untouched_and_silent(self, caplog):
        with _capture_noodle_warnings(caplog):
            assert substitute("[NOT_SET_ANYWHERE] `NOPE`") == "[NOT_SET_ANYWHERE] `NOPE`"
        assert "Deprecated" not in caplog.text


class TestPomExplicit:
    def test_pom_prefix(self):
        assert pom.is_explicit("{pom:login button}") == "login button"

    def test_pom_prefix_strips_whitespace(self):
        assert pom.is_explicit("  {pom: search field }  ") == "search field"

    def test_legacy_bare_braces_still_explicit(self, caplog):
        with _capture_noodle_warnings(caplog):
            assert pom.is_explicit("{login button}") == "login button"
        assert "{pom:login button}" in caplog.text

    def test_unresolved_env_var_refs_are_not_pom_keys(self):
        assert pom.is_explicit("{env:NOPE}") is None
        assert pom.is_explicit("{var:NOPE}") is None

    def test_plain_text_not_explicit(self):
        assert pom.is_explicit("login button") is None


class TestWriteTargetCanonicalization:
    def test_var_write_target_matches_store_pattern(self):
        # {var:X} surviving substitution (it's a write target) is canonicalized
        # so the existing backtick/bracket patterns match unchanged.
        assert normalize_phrasing("sets {var:NAME} to 'bob'") == "sets `NAME` to 'bob'"

    def test_env_ref_canonicalized_to_brackets(self):
        assert normalize_phrasing("is on {env:BASE}") == "is on [BASE]"

    def test_pom_ref_untouched_by_normalization(self):
        assert normalize_phrasing("clicks the {pom:burger menu}") == \
            "clicks the {pom:burger menu}"
