@web @llm @fallback @capability
Feature: LLM Fallback — unmatched steps resolved by a language model

  # When a step text matches NO regex in patterns.py, Noodle hands it to the
  # configured LLM. The model receives the step text and a screenshot, then
  # returns a JSON action ({type, locator, ...}) that the orchestrator executes.
  #
  # Resolution order:
  #   1. Built-in patterns (patterns.py) — free, deterministic
  #   2. POM alias (pom.yaml)            — free, deterministic
  #   3. LLM fallback                    — requires NOODLE_MODEL
  #
  # Steps that reach the LLM are marked [LLM] in comments below.
  # Steps resolved locally are marked [A11Y] or [PATTERN].
  #
  # Requires a model:
  #   NOODLE_MODEL=anthropic/claude-haiku-4-5-20251001
  #   ANTHROPIC_API_KEY=sk-ant-...
  # Without a model, [LLM] steps fail loudly: "No pattern matched and no LLM configured"
  #
  # Run (with model):
  #   NOODLE_MODEL=anthropic/claude-haiku-4-5-20251001 \
  #   noodle run sample_feature_tests/web/busterblock/features/llm_fallback.feature --no-capture

  Scenario: An unrecognised verb falls through to the LLM

    # [A11Y] standard navigate — matches the built-in "is on '...'" pattern
    Given User is on "{env:BUSTERBLOCK}"

    # [PATTERN] fill — matches "enters X in the Y field"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field

    # [LLM] "authenticates" is not in any regex pattern.
    # normalize_subject strips "User " → "authenticates using the login button".
    # No pattern matches "authenticates" as a verb, so step_resolver returns None
    # and the framework calls the LLM with the step text + screenshot.
    # Expected model response: {"type": "click", "locator": "Login"}
    When User authenticates using the login button  # llm-ok: deliberately unmatched, demonstrates LLM fallback

    # [A11Y] plain DOM text assertion — confirms the LLM's action worked
    Then User should see "VHS Catalog"

  Scenario: A second unrecognised verb handled by the model

    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User authenticates using the login button  # llm-ok: deliberately unmatched, demonstrates LLM fallback
    And User waits until "VHS Catalog" is visible

    # [LLM] "verifies" is not a known verb — routes to LLM.
    # Model should interpret this as an assertion and check the page.
    Then User verifies the catalog is displayed
