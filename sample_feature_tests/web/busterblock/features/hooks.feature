@web @hooks @capability
Feature: Hooks — cross-cutting behaviour with @before and @after

  # Hooks in Noodle fire around every scenario without touching the .feature
  # file. They are registered in tests/steps/custom_hooks.py using the
  # @hook decorator.
  #
  # Hook events supported:
  #   before_all       — once before the entire run (must register in environment.py)
  #   before_feature   — before each .feature file
  #   before_scenario  — before each scenario (access context, scenario)
  #   after_step       — after each step (access step.status)
  #   after_scenario   — after each scenario (context.page still alive)
  #   after_all        — once after the entire run
  #
  # The custom_hooks.py already registers:
  #   before_scenario → assign a session ID and start a timer
  #   after_scenario  → log elapsed time and session ID
  #   after_scenario  → emit an AUDIT log line when the @audit tag is present
  #
  # These scenarios need no step changes — hooks are transparent.
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/hooks.feature --headless --no-capture

  Background:
    Given User is on "{env:BUSTERBLOCK}"
    When User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    And User waits until "VHS Catalog" is visible

  @smoke
  Scenario: Hook fires transparently — session ID and timing logged automatically
    # before_scenario already ran: context.session_id is set, timer started.
    # after_scenario will log: [session_id] name — PASSED (Xs)
    # Nothing in the step list needs to change to benefit from the hook.
    Then User should see "VHS Catalog"

  @smoke @audit
  Scenario: The @audit tag triggers extra hook behaviour
    # The @audit tag is detected by the after_scenario hook in custom_hooks.py.
    # It emits an additional AUDIT log line alongside the normal timing output.
    # No step change needed — the tag alone activates the hook branch.
    Then User should see "VHS Catalog"
    And User should see "Die Hard"

  @before_all_note
  Scenario: Note — before_all and after_all hooks
    # @hook("before_all") and @hook("after_all") are supported but MUST be
    # registered in environment.py, NOT in tests/steps/ files. Step files
    # are loaded AFTER before_all fires, so a hook registered there will never
    # run. See tests/environment.py for how the framework's own before_all
    # is wired.
    #
    # To add your own before_all:
    #   In environment.py:
    #     from noodle.hooks import hook, before_all as _before_all
    #     @hook("before_all")
    #     def my_setup(context):
    #         print("suite starting")
    #     before_all = _before_all   # keep framework's before_all wired
    Then User should see "VHS Catalog"
