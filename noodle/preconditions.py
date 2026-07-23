"""Tag-driven data preconditions & teardowns — the JDBC-fixture analog.

A scenario tagged ``@precondition:NAME`` runs NAME's ``setup:`` HTTP calls before
it (in before_scenario) and NAME's ``teardown:`` calls after it (in
after_scenario, even on failure). Fixtures live in ``preconditions.yaml`` in the
app's ``resources/`` folder (sibling of the ``features/`` folder the .feature
file itself lives in):

    jaws_out_of_stock:
      setup:
        - POST {env:BUSTERBLOCK}/api/test/reset
        - PATCH {env:BUSTERBLOCK}/api/test/stock {"movieId": 1, "stock": 0}
      teardown:
        - POST {env:BUSTERBLOCK}/api/test/reset

Each line is ``METHOD URL [JSON-body]``. {env:X}/{var:X} references resolve
from environments.yaml / .env via the same substitution the step runner uses.
HTTP is stdlib urllib — no extra dependency.
"""
import json
import urllib.request
from pathlib import Path

from noodle.log import logger
from noodle.orchestrator.runner import substitute

_TAG_PREFIX = "precondition:"


def parse_call(line: str):
    """'METHOD URL [JSON]' -> (method, url, body|None). Body is optional."""
    parts = line.strip().split(None, 2)
    if len(parts) < 2:
        raise ValueError(f"Precondition call needs 'METHOD URL [JSON]': {line!r}")
    method, url = parts[0].upper(), parts[1]
    body = json.loads(parts[2]) if len(parts) == 3 else None
    return method, url, body


def _http(method: str, url: str, body):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (test seam, local)
        return resp.status


def _fixtures_for(scenario):
    """Return (names, fixtures_dict) for a scenario, or (None, None) if untagged."""
    names = [t[len(_TAG_PREFIX):] for t in scenario.effective_tags
             if t.startswith(_TAG_PREFIX)]
    if not names:
        return None, None
    # .feature files live in <app_dir>/features/ — resources/ is the sibling
    # one level up.
    folder = Path(scenario.feature.filename).parent.parent / "resources"
    path = folder / "preconditions.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"@precondition tag on '{scenario.name}' but no {path}"
        )
    import yaml
    return names, (yaml.safe_load(path.read_text()) or {})


def run(scenario, phase: str):
    """Run the `phase` ('setup'|'teardown') calls for every @precondition on the
    scenario. Teardown failures are logged, not raised, so cleanup never masks a
    test result."""
    names, fixtures = _fixtures_for(scenario)
    if not names:
        return
    for name in names:
        fixture = fixtures.get(name)
        if fixture is None:
            raise KeyError(f"@precondition:{name} not found in preconditions.yaml")
        for line in fixture.get(phase, []):
            method, url, body = parse_call(substitute(line))
            try:
                status = _http(method, url, body)
                logger.info(f"\n  🧱 {phase}: {method} {url} → {status}")
            except Exception as e:
                if phase == "teardown":
                    logger.warning(f"\n  ⚠️  teardown {method} {url} failed: {e}")
                else:
                    raise
