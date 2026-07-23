# Engine glue — re-exports the framework's behave lifecycle hooks so behave
# finds them (behave requires a file named exactly environment.py at the root
# it's pointed at). Same file `noodle init` scaffolds into a fresh workspace.
from noodle.hooks import (  # noqa: F401
    after_all,
    after_scenario,
    after_step,
    before_all,
    before_feature,  # sets POM folder context — required for local pom.yaml lookup
    before_scenario,
)
