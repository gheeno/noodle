"""`python -m noodle` — same CLI as the `noodle` launcher, without the shim.

NOOD_0156: Windows holds the running `noodle.exe` open, so `noodle update` cannot
replace its own launcher from inside it. Invoked this way there is no shim to
lock, which is what the fix-failure hint points users at.
"""
from noodle.cli import app

app()
