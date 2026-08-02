# NOOD_0216 — moved to the api wok's own engine package. Shim kept so any
# workspace code importing the old path keeps working.
from noodle.agents.api.rest_client import *  # noqa: F401,F403
from noodle.agents.api.rest_client import _OPENER, _NoRedirect, _slow  # noqa: F401
