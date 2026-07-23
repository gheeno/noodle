@web @script @capability
Feature: Run a custom script — invoke external code as a Gherkin step

  # Sometimes a test needs to do something the browser can't — seed a database,
  # call a shell tool, run a Java jar, invoke a Python data-setup script.
  # Noodle can run any external script or command as a step and capture stdout.
  #
  # Patterns demonstrated:
  #   the script 'path' runs                          — run and ignore output
  #   runs the script 'path' storing the output in {var:VAR}  — capture stdout
  #   runs the script 'path' with args 'arg1 arg2'    — pass CLI arguments
  #   runs the command 'shell command'                 — arbitrary shell command
  #   {var:SCRIPT_OUTPUT} should contain 'X'              — assert on captured output
  #
  # The interpreter is inferred from the file extension:
  #   .py → python3   .js → node   .sh → bash   .jar → java -jar
  # A non-zero exit code fails the step.
  #
  # Run:  noodle run sample_feature_tests/web/busterblock/features/run_custom_script.feature --headless

  @smoke @script_basic
  Scenario: Run a Python script that seeds data, then assert the UI reflects it
    # 1. Script resets BusterBlock's data and forces Jaws to stock 0.
    # 2. stdout is captured into SCRIPT_OUTPUT automatically.
    # 3. UI assertion confirms the seeded state is visible in the catalog.
    Given the script "sample_feature_tests/web/busterblock/resources/functions/seed_out_of_stock.py" runs
    And {var:SCRIPT_OUTPUT} should contain "OUT OF STOCK"

    When User is on "{env:BUSTERBLOCK}"
    And User enters {env:BB_USER} in the username field
    And User enters {env:BB_PASS} in the password field
    And User clicks the login button
    And User waits until "VHS Catalog" is visible
    Then the cell in row "Jaws" column "Stock" should be "Out"

  @script_with_args
  Scenario: Pass arguments to the script
    # "with args 'X Y'" forwards the string as CLI arguments to the interpreter.
    Given User runs the script "sample_feature_tests/web/busterblock/resources/functions/seed_out_of_stock.py" with args "{env:BUSTERBLOCK}" storing the output in {var:SEED_RESULT}
    Then {var:SEED_RESULT} should contain "OUT OF STOCK"

  @smoke @run_command
  Scenario: Run a shell command and capture its output
    # "runs the command '...'" executes a raw shell command via subprocess.
    # stdout is captured into SCRIPT_OUTPUT (and optionally a named variable).
    When User runs the command "echo Noodle-command-test" and storing the output in {var:CMD_OUT}
    Then {var:CMD_OUT} should contain "Noodle-command-test"

  @run_command @api_via_curl
  Scenario: Use curl as a command to hit the BusterBlock test API
    # curl gives full control over headers and body — useful when you need
    # Content-Type: application/json or you want to parse the response in a later step.
    When User runs the command "curl -s -o /dev/null -w '%{http_code}' {env:BUSTERBLOCK}/api/test/reset -X POST" and storing the output in {var:STATUS}
    Then {var:STATUS} should contain "200"
