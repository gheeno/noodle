import sys

import pytest

from noodle.orchestrator import script_runner
from noodle.resolver.step_resolver import resolve


def test_command_for_infers_interpreter_by_extension():
    assert script_runner.command_for("x.py", None) == [sys.executable, "x.py"]
    assert script_runner.command_for("x.js", None) == ["node", "x.js"]
    assert script_runner.command_for("tool.jar", None) == ["java", "-jar", "tool.jar"]


def test_command_for_unknown_ext_runs_directly():
    assert script_runner.command_for("./run", None) == ["./run"]


def test_command_for_splits_args():
    assert script_runner.command_for("x.py", "--flag val") == [sys.executable, "x.py", "--flag", "val"]


def test_run_script_returns_stdout(tmp_path):
    s = tmp_path / "hi.py"
    s.write_text("print('hello from script')")
    assert script_runner.run_script(str(s)) == "hello from script"


def test_run_script_nonzero_exit_raises(tmp_path):
    s = tmp_path / "boom.py"
    s.write_text("import sys; sys.exit(3)")
    with pytest.raises(AssertionError):
        script_runner.run_script(str(s))


def test_run_script_missing_file_raises():
    with pytest.raises(AssertionError):
        script_runner.run_script("nope/does_not_exist.py")


def test_resolver_matches_run_script_phrasings():
    assert resolve('runs the script "x.py"')['type'] == 'run_script'
    assert resolve('script "x.py" executes')['type'] == 'run_script'
    got = resolve('runs the script "x.py" with "--a b" storing the output as `RESULT`')
    assert got == {'type': 'run_script', 'path': 'x.py', 'args': '--a b', 'var': 'RESULT'}
    assert resolve('runs the command "ls -la"') == {'type': 'run_command', 'command': 'ls -la', 'var': None}


# --- call_function: in-process custom functions + D.I. (NOOD_0009) ----------

def test_call_function_from_file_returns_real_value(tmp_path):
    f = tmp_path / "helpers.py"
    f.write_text("def add(a, b):\n    return int(a) + int(b)\n")
    assert script_runner.call_function(f"{f}:add", "2 3") == 5


def test_call_function_module_form():
    assert script_runner.call_function("os.path:basename", "a/b") == "b"


def test_call_function_bad_spec_raises():
    with pytest.raises(AssertionError, match="module:function"):
        script_runner.call_function("no_separator")


def test_call_function_missing_function_raises(tmp_path):
    f = tmp_path / "empty.py"
    f.write_text("")
    with pytest.raises(AssertionError, match="not found"):
        script_runner.call_function(f"{f}:nope")


def test_call_function_exception_becomes_assertion(tmp_path):
    f = tmp_path / "boom.py"
    f.write_text("def boom():\n    raise ValueError('nope')\n")
    with pytest.raises(AssertionError, match="ValueError"):
        script_runner.call_function(f"{f}:boom")


def test_resolver_matches_call_function_phrasings():
    assert resolve('calls the function "helpers.py:add"')['type'] == 'call_function'
    got = resolve('calls the function "helpers.py:add" with args "2 3" and saves the result as `SUM`')
    assert got == {'type': 'call_function', 'spec': 'helpers.py:add', 'args': '2 3',
                   'raw': False, 'var': 'SUM'}
    assert resolve('calls function "mod:fn" storing the return value in `X`')['var'] == 'X'
    # NOOD_0115 — "with raw arg" passes the whole string as ONE argument
    got = resolve('calls the function "helpers.py:parse_int" with raw arg "93 results"')
    assert got['raw'] is True and got['args'] == '93 results'


def test_call_function_dependency_injection(tmp_path):
    """Step 1 generates a value, step 2 consumes it via `VAR` substitution —
    end to end through execute_step with a browserless stub context."""
    from types import SimpleNamespace

    from noodle.orchestrator.runner import execute_step

    f = tmp_path / "di.py"
    f.write_text(
        "def make_token():\n    return 'tok-123'\n"
        "def greet(token):\n    return f'Bearer {token}'\n"
    )
    context = SimpleNamespace(page=None, _vars={})
    execute_step(f'calls the function "{f}:make_token" and saves the result as `TOKEN`', context)
    assert context._vars['TOKEN'] == 'tok-123'
    assert context._vars['FUNCTION_RESULT'] == 'tok-123'

    execute_step(f'calls the function "{f}:greet" with args "`TOKEN`" and saves the result as `AUTH`', context)
    assert context._vars['AUTH'] == 'Bearer tok-123'
