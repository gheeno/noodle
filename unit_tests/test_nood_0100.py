"""NOOD_0100 — generate_test gains explicit tag pass-through (@tag tokens in
a free-text description land on the generated scenario) and append_to (add
a scenario to an existing .feature instead of always writing a new one).
No browser, no LLM, no network."""
from noodle.repl import generate

WS = {"tests_dir": "tests"}


def test_extract_tags_pulls_bare_at_tokens():
    assert generate.extract_tags(
        'search for "Product A" and add gherkin tags @hello.com'
    ) == ["@hello.com"]


def test_extract_tags_empty_when_none_present():
    assert generate.extract_tags("search for \"Product A\"") == []


def test_generate_applies_tag_to_scenario(tmp_path):
    feat, _ = generate.generate(
        'search for "Product A" @hello.com', "https://hello.test",
        WS, str(tmp_path))
    text = feat.read_text()
    assert "@hello.com" in text
    # tag lands on the Scenario line, not smuggled into Feature:
    assert "@hello.com\n  Scenario:" in text


def test_generate_append_to_adds_scenario_to_existing_file(tmp_path):
    feat1, _ = generate.generate('search for "Product A"', "https://hello.test",
                                 WS, str(tmp_path))
    stem = feat1.stem
    before = feat1.read_text()

    result = generate.generate('search for "Product B"', "https://hello.test",
                               WS, str(tmp_path), append_to=stem)
    assert result is not None
    feat2, _ = result
    assert feat2 == feat1  # same file, not a second one

    after = feat1.read_text()
    assert after.startswith(before.rstrip("\n"))
    assert after.count("Scenario:") == 2
    assert "Product B" in after


def test_generate_append_to_tags_only_the_new_scenario(tmp_path):
    feat1, _ = generate.generate('search for "Product A"', "https://hello.test",
                                 WS, str(tmp_path))
    feat, _ = generate.generate('search for "Product B" @followup',
                                "https://hello.test",
                                WS, str(tmp_path), append_to=feat1.stem)
    text = feat.read_text()
    assert text.count("@followup") == 1


def test_generate_append_to_missing_file_falls_back_to_new_file(tmp_path):
    result = generate.generate('search for "Product A"', "https://hello.test",
                               WS, str(tmp_path), append_to="does_not_exist")
    assert result is not None
    feat, _ = result
    assert feat.exists()


def test_generate_new_description_same_app_writes_a_new_file_by_default(tmp_path):
    """Different topic, same app/host, no append_to -> a distinct .feature
    (task: "generate new .feature files if it's a different test suite")."""
    feat1, _ = generate.generate('search for "Product A"', "https://hello.test",
                                 WS, str(tmp_path))
    feat2, _ = generate.generate('checks the newsletter checkbox', "https://hello.test",
                                 WS, str(tmp_path))
    assert feat1 != feat2
    assert feat1.exists() and feat2.exists()
