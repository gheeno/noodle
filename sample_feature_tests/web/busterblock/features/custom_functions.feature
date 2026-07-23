@web @function @capability
Feature: Call a custom Python function — in-process code as a Gherkin step

  # run_script covers external programs, but its only channel back is stdout.
  # "calls the function" imports and calls a Python function *in-process*, so
  # the step gets the function's real return value — the Noodle equivalent of
  # a Java/Cucumber step class method (JDBC setup, token minting, data prep).
  #
  # Patterns demonstrated:
  #   calls the function 'file.py:func'                       — run and ignore result
  #   calls the function 'pkg.module:func'                    — module form
  #   calls the function '...' with args 'a b'                — positional string args
  #   calls the function '...' and saves the result as {var:VAR}  — capture the return
  #   {var:VAR} in a later step                                    — dependency injection
  #
  # The return value is always stored in {var:FUNCTION_RESULT}; "saves the result
  # as {var:VAR}" additionally stores it under that name. dict/list returns are
  # JSON-encoded. A raised exception fails the step.
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/custom_functions.feature --headless

  @smoke @function_basic
  Scenario: Call a function with args and grab its return value
    When User calls the function "sample_feature_tests/web/busterblock/resources/functions/helpers.py:add" with args "2 3" and saves the result as {var:SUM}
    Then {var:SUM} should equal "5"
    And {var:FUNCTION_RESULT} should equal "5"

  @smoke @function_di
  Scenario: Dependency injection — one step generates a value, the next consumes it
    # Step 1 generates a unique username; step 2 receives it via {var:USERNAME}.
    # Variable substitution happens before matching, so {var:USERNAME} in the args
    # string is replaced with the value captured in the previous step.
    Given User calls the function "sample_feature_tests/web/busterblock/resources/functions/helpers.py:make_username" and saves the result as {var:USERNAME}
    When User calls the function "sample_feature_tests/web/busterblock/resources/functions/helpers.py:greet" with args "{var:USERNAME}" and saves the result as {var:GREETING}
    Then {var:GREETING} should contain "Hello, noodle-"

  @function_module_form
  Scenario: Call a function by module path (anything importable works)
    When User calls the function "os.path:basename" with args "folder/report.pdf" and saves the result as {var:NAME}
    Then {var:NAME} should equal "report.pdf"
