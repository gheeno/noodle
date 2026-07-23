"""
Stub step definitions for the cucumberautocomplete VS Code extension.
NOT used by behave — lives outside tests/steps/ so behave ignores it.
Wildcard patterns suppress "undefined step" warnings in the editor.
"""
from behave import given, then, when


@given(u'.*')
def _(context): pass

@when(u'.*')
def _(context): pass

@then(u'.*')
def _(context): pass
