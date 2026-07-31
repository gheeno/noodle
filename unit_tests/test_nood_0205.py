"""NOOD_0205 — one Azure job, three report surfaces, a log per scenario.

What must hold: the template compiles to ONE job by default, the Allure tab gets
a report shaped the way the extension will actually accept, every knob on the
template installs what backs it, and each scenario's own log lands next to its
screenshots. All of these failed silently before — a green post-job with an empty
tab, an install that succeeds and a run that then dies, a Run panel that won't
advance — so each gets a guard that fails loudly instead.
"""
import re
from pathlib import Path

import yaml
from typer.testing import CliRunner

from noodle.cli import app

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "ci" / "azure" / "noodle-tests.yml"


def _template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _params() -> dict:
    doc = yaml.safe_load(_template_text())
    return {p["name"]: p for p in doc["parameters"]}


def _flat_steps(job: dict) -> list[dict]:
    """Steps with every `${{ if … }}:` wrapper flattened away — the guards below
    care that a step exists and how it's configured, not how deeply the
    conditionals nest."""
    out = []
    def walk(items):
        for item in items:
            if len(item) == 1 and next(iter(item)).startswith("${{"):
                walk(next(iter(item.values())))
            else:
                out.append(item)
    walk(job["steps"])
    return out


# ── Issue 1 — one job ────────────────────────────────────────────────────────

def test_sharding_is_off_by_default():
    """Sharding scales linearly in JOBS: 29 features listed 31 of them, 1000
    would list 1002, and each pays the full ~90s setup to run one file."""
    params = _params()
    assert params["shard"]["default"] is False
    assert params["parallelProcesses"]["default"] == 4, \
        "the whole point of one job is that it parallelises inside itself"


def test_discover_and_merge_jobs_are_guarded_by_shard():
    """An unsharded run must expand to EXACTLY one job — the discover job (which
    builds the matrix) and the merge job (which re-generates the report) both
    only exist to serve a matrix."""
    doc = yaml.safe_load(_template_text())
    unguarded = [j for j in doc["jobs"] if "job" in j]
    guarded = [j for j in doc["jobs"] if "job" not in j]
    assert len(unguarded) == 1, f"unsharded runs must be one job, got {len(unguarded)}"
    assert unguarded[0]["displayName"] == "Noodle tests"
    assert guarded and all(
        any("parameters.shard, true" in k for k in j) for j in guarded), \
        "every extra job must be behind `if eq(parameters.shard, true)`"


# ── Issue 2 — the Allure tab ─────────────────────────────────────────────────

def test_single_file_report_uses_the_generator_that_supports_it(monkeypatch, tmp_path):
    """`allure generate` has NO --single-file flag (the CLI rejects it and
    prints usage); only `allure awesome` produces one. The Azure extension
    silently skips any report whose summary.json lacks meta.singleFile."""
    from noodle.reporting import builder

    seen = []
    monkeypatch.setattr(builder, "_allure_bin", lambda: "allure")
    monkeypatch.setattr(builder.subprocess, "run",
                        lambda cmd, **kw: seen.append(cmd) or type("P", (), {"returncode": 0})())
    report = str(tmp_path / "reports" / "allure-report")

    monkeypatch.setenv("NOODLE_ALLURE_SINGLE_FILE", "1")
    builder.generate(str(tmp_path / "results"), report)
    assert seen[-1][1] == "awesome" and "--single-file" in seen[-1]

    monkeypatch.delenv("NOODLE_ALLURE_SINGLE_FILE")
    builder.generate(str(tmp_path / "results"), report)
    assert seen[-1][1] == "generate" and "--single-file" not in seen[-1]


def test_allure_tab_publishes_from_a_job_that_has_a_source_tree():
    """Two things had to be true at once. reportDir resolves against the job's
    working directory, so an ABSOLUTE path in a `checkout: none` job published
    nothing, silently — relative, from the test job, is the shape that works.
    And the task depends on an org-level marketplace extension, so a missing
    extension must degrade the tab, not fail the suite."""
    doc = yaml.safe_load(_template_text())
    steps = _flat_steps(next(j for j in doc["jobs"] if "job" in j))
    task = next((s for s in steps
                 if str(s.get("task", "")).startswith("PublishAllureReport")), None)
    assert task, "the tab must publish from the test job, not a checkout:none job"
    assert "${{ if eq(parameters.publishAllureTab, true) }}" in _template_text(), \
        "an org with no extension must be able to switch it off"
    assert task["continueOnError"] is True
    assert task["inputs"]["reportDir"] == "allure-report", "must be RELATIVE"
    assert task["inputs"]["testResultsDir"].startswith("$("), "must be absolute"
    assert "NOODLE_ALLURE_SINGLE_FILE" in _template_text(), \
        "the extension only uploads a single-file report"


# ── Issue 3 — the scaffolded pipeline ────────────────────────────────────────

def test_init_scaffolds_the_pipeline_into_its_own_folder(tmp_path):
    """A repo grows more than one pipeline (nightly, PR, release), and that is
    where every other Azure repo keeps them."""
    CliRunner().invoke(app, ["init", str(tmp_path)])
    pipeline = tmp_path / "azure-pipelines" / "azure-pipelines.yml"
    assert pipeline.is_file()
    doc = yaml.safe_load(pipeline.read_text(encoding="utf-8"))
    assert doc["jobs"][0]["template"] == "ci/azure/noodle-tests.yml@noodle"
    # `noodle init <path>` makes that path the workspace root.
    assert doc["jobs"][0]["parameters"]["workspaceDir"] == ""


def test_scaffolded_pipeline_parameterises_the_engine_ref():
    """Azure does not match branch names across repos — the engine version comes
    from `ref:` and nowhere else. As a parameter it expands at compile time,
    which is when the @noodle template is resolved, so a one-off trial selects
    the template version too and leaves no commit behind."""
    from noodle.cli import _AZURE_PIPELINE
    doc = yaml.safe_load(_AZURE_PIPELINE)
    assert doc["resources"]["repositories"][0]["ref"] == "${{ parameters.noodleRef }}"
    ref_param = next(p for p in doc["parameters"] if p["name"] == "noodleRef")
    assert ref_param["default"].startswith("refs/tags/")


def test_scaffolded_readme_explains_the_pipeline_it_wrote():
    """Issue 7 — the two placeholders fail at COMPILE time, which produces no
    job log. The first file a greenfield team reads must name them."""
    from noodle.cli import _WORKSPACE_README
    for needle in ("azure-pipelines/azure-pipelines.yml", "REPLACE_ME",
                   "workspaceDir", "docs/ci-project-repo.md"):
        assert needle in _WORKSPACE_README, f"the scaffolded README never mentions {needle}"


# ── Issue 4 — a tag default Azure will accept ────────────────────────────────

def test_tag_filter_defaults_to_a_word_azure_will_accept():
    """Azure's Run-pipeline panel will not advance to Resources while a runtime
    string parameter is empty, so a blank default made the whole-suite manual
    run — the common case — literally unreachable."""
    assert _params()["testTag"]["default"] == "all"
    text = _template_text()
    assert "ne(lower(parameters.testTag), 'all')" in text, \
        "'all' must translate to no --tag flag, or it filters on a tag named all"
    assert "ne(parameters.testTag, '')" in text, \
        "'' must still mean the whole suite — automated callers pass it"


# ── Issue 6 — a knob installs what backs it ──────────────────────────────────

def test_parallel_default_installs_behavex():
    """parallelProcesses != 0 takes the --parallel path, which needs behavex
    from the `parallel` extra. Without it the install went green and the run
    died with 'Parallel runs need behavex' — the NOOD_0193 Key Vault footgun in
    a new costume, which is why both now go through one helper."""
    text = _template_text()
    assert "add_extra parallel" in text
    assert "'${{ parameters.parallelProcesses }}' != '0'" in text, \
        "0 must still install nothing extra"


# ── The provenance step ──────────────────────────────────────────────────────

def test_the_job_records_which_engine_build_ran():
    """`noodle --version` can't say: it reads the SHA by walking up from the
    PACKAGE dir for .git, and CI pip-installs a non-editable copy into
    site-packages with no repo above it. The checkout always has .git."""
    doc = yaml.safe_load(_template_text())
    steps = _flat_steps(next(j for j in doc["jobs"] if "job" in j))
    prov = next((s for s in steps if s.get("displayName") == "Engine build provenance"), None)
    assert prov, "nothing in the job log says which engine build ran"
    assert "git rev-parse HEAD" in prov["bash"] and "git tag --points-at HEAD" in prov["bash"]
    assert prov["workingDirectory"] == "$(noodleRoot)"


# ── The per-scenario log ─────────────────────────────────────────────────────

def test_scenario_log_captures_only_its_own_scenario():
    """artifacts/run.log is the whole suite, and under --parallel it is
    interleaved across workers — so it can't answer "what was the engine doing
    while THIS scenario ran". Each slice must hold its scenario's lines and
    nobody else's."""
    from noodle import log as nlog

    nlog.start_scenario_log()
    nlog.logger.info("first scenario line")
    first = nlog.pop_scenario_log()

    nlog.logger.info("between scenarios")          # nothing is buffering

    nlog.start_scenario_log()
    nlog.logger.info("second scenario line")
    second = nlog.pop_scenario_log()

    assert "first scenario line" in first
    assert "second" not in first and "between" not in first
    assert "second scenario line" in second and "first" not in second
    assert nlog.pop_scenario_log() == "", "popping twice must not resurrect a buffer"


def test_scenario_log_is_attached_as_evidence(tmp_path, monkeypatch):
    """It reaches the Allure report as a `run log` attachment — and from there
    the Azure Tests tab for free, because the junit writer emits every result
    attachment as an [[ATTACHMENT|path]] marker."""
    from noodle import hooks
    from noodle import log as nlog
    from unit_tests.test_nood_0023 import _base_context, _make_scenario
    monkeypatch.chdir(tmp_path)

    class _FakeResult:
        def __init__(self):
            self.attachments = []

        def add_attachment(self, name, path, mime_type):
            self.attachments.append((name, path, mime_type))

        def finish(self, scenario):
            pass

    fake = _FakeResult()
    monkeypatch.setattr(hooks, "_allure_result", lambda ctx: fake)
    monkeypatch.setattr(hooks._writer, "write_result", lambda ar: None)

    nlog.start_scenario_log()
    nlog.logger.info("engine did a thing")
    hooks.after_scenario(_base_context(_scenario_failed=False),
                         _make_scenario("Log me"))

    logs = [a for a in fake.attachments if a[0] == "run log"]
    assert len(logs) == 1, f"expected one run log, got {fake.attachments}"
    _, path, mime = logs[0]
    assert mime == "text/plain"
    assert "engine did a thing" in Path(path).read_text(encoding="utf-8")


# ── Greenfield / vendor neutrality ───────────────────────────────────────────

# ponytail: names assembled from fragments so this guard doesn't itself become
# the reference it forbids. Keep it that way when adding to the list.
_DENY = ["".join(p) for p in (
    ("can", "tire"), ("canadian ", "tire"), ("corp-", "ado-005"),
    ("ut", "af"), ("fsh-", "noodle"),
)]

# The second half of the same rule: a repo-resource `name:` is the one field a
# consumer must fill with their own, so it's where a tenant leaks in even when
# no denylisted word appears.
_PLACEHOLDERS = {"REPLACE" + "_ME/noodle", "Tool" + "ing/noodle"}
_REPO_NAME_RE = re.compile(r"^\s*#?\s*name:\s*([\w.-]+/[\w.-]+)\s*(?:#.*)?$", re.M)


def _offenders(text: str) -> list:
    low = text.lower()
    return [d for d in _DENY if d in low]


def test_shipped_ci_templates_name_no_customer():
    """Everything under ci/azure/ is copied verbatim by consumers."""
    for path in sorted((REPO / "ci" / "azure").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert not _offenders(text), f"{path.name} names a customer"
        found = set(_REPO_NAME_RE.findall(text))
        assert found <= _PLACEHOLDERS, f"{path.name} names a real repo: {found - _PLACEHOLDERS}"


def test_scaffolded_workspace_names_no_customer(tmp_path):
    """Everything `noodle init` writes must be committable by a stranger."""
    CliRunner().invoke(app, ["init", str(tmp_path)])
    offenders = {}
    for path in tmp_path.rglob("*"):
        if not path.is_file() or path.suffix not in (".yml", ".yaml", ".md"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not _offenders(text), f"{path.name} names a customer"
        found = set(_REPO_NAME_RE.findall(text))
        if found - _PLACEHOLDERS:
            offenders[path.name] = found - _PLACEHOLDERS
    assert not offenders, f"scaffold names a real repo: {offenders}"


def test_the_engine_repo_names_no_customer():
    """The CHANGELOG kept one — a line recording that the LAST customer name had
    been removed. Every tracked doc, template and source file is in scope."""
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                             text=True, check=True).stdout.split("\0")
    offenders = {}
    for rel in tracked:
        p = REPO / rel
        if not rel or p.suffix not in (".md", ".yml", ".yaml", ".py", ".txt", ".toml"):
            continue
        hits = _offenders(p.read_text(encoding="utf-8", errors="ignore"))
        if hits:
            offenders[rel] = hits
    assert not offenders, f"customer name in tracked files: {offenders}"
