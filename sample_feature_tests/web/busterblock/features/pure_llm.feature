@web @llm @pure_llm @capability
Feature: Pure LLM Mode — every step interpreted by the language model

  # NOODLE_LLM_MODE=full bypasses the built-in pattern resolver entirely.
  # EVERY step goes directly to the LLM — no regex matching, no POM lookup.
  # The model interprets each Gherkin step from the screenshot + step text.
  #
  # When to use:
  #   - Exploring a new app without writing any step patterns first
  #   - Verifying that the LLM can handle an entire flow end-to-end
  #   - Rapid prototyping before committing to deterministic step patterns
  #
  # Trade-offs vs auto mode:
  #   + Zero step-pattern configuration needed
  #   + Natural language with no regex constraints
  #   - Every step costs an LLM call (slower, not free)
  #   - Non-deterministic: model output can vary between runs
  #   - Not suitable for CI where reproducibility matters
  #
  # Requires:
  #   NOODLE_LLM_MODE=full
  #   NOODLE_MODEL=anthropic/claude-sonnet-4-6   (or any vision model)
  #   ANTHROPIC_API_KEY=sk-ant-...
  #
  # Run:
  #   NOODLE_LLM_MODE=full \
  #   NOODLE_MODEL=anthropic/claude-sonnet-4-6 \
  #   noodle run sample_feature_tests/web/busterblock/features/pure_llm.feature --no-capture

  @no_retry
  Scenario: Full LLM mode — entire login flow interpreted by the model
    # Every step below goes to the model. The phrasing is intentionally more
    # natural and less structured than the built-in pattern vocabulary.
    Given the BusterBlock video store is open at "{env:BUSTERBLOCK}"  # llm-ok: deliberately unmatched, full LLM mode
    When I sign in as {env:BB_USER} using the password {env:BB_PASS}  # llm-ok: deliberately unmatched, full LLM mode
    Then I should be looking at the VHS movie catalog  # llm-ok: deliberately unmatched, full LLM mode

  @no_retry
  Scenario: Full LLM mode — catalog interaction in natural language
    Given the user is logged into BusterBlock at "{env:BUSTERBLOCK}" as {env:BB_USER} with password {env:BB_PASS}  # llm-ok: deliberately unmatched, full LLM mode
    When the catalog is visible
    And I add the first available movie to my cart  # llm-ok: deliberately unmatched, full LLM mode
    Then the cart count should show 1
