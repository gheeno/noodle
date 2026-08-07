"""NOOD_0240 — a test case carries every tag that applies to it, in a stable order.

`.feature` files put the broad tags on the Feature and the specific ones on the
Scenario:

    @api @rest @busterblock_api @capability
    Feature: BusterBlock Reviews API
      @smoke @typed @schema
      Scenario: Submitting a review returns the stored record

The Allure writer built its `tag` labels from `scenario.tags` — the scenario's
OWN tags only — so that case reported three chips and silently dropped the four
feature-level ones.

The second half of this file exists because the obvious fix (read
`scenario.effective_tags`) is version-dependent in a way that a single-version
test suite cannot see:

  * behave 1.2.6 — `effective_tags` is a LIST (`feature.tags + own`), and
    Scenario has no `parent` attribute at all
  * behave 1.3.x — `effective_tags` is a SET, `Rule` sits between Feature and
    Scenario, and set iteration order for strings varies per process

pyproject asks for `behave>=1.2.6`, so either can be installed, and the repo's
own environments disagree — pytest ran 1.2.6 while the installed `noodle`
launcher ran 1.3.3. Reading `effective_tags` directly therefore passed its
tests on 1.2.6 while shipping reshuffled chips on 1.3.3. Both shapes are
emulated below so the suite pins the behaviour under whichever is installed.
"""
from unittest.mock import MagicMock

import behave
import pytest
from behave.parser import parse_feature

from noodle.reporting import writer


def _tags_of(scenario) -> list[str]:
    """The tag label values, in the order the report will show them."""
    result = writer.ScenarioResult(scenario)
    return [lb["value"] for lb in result.result["labels"] if lb["name"] == "tag"]


# --------------------------------------------------------------------------
# against the real behave model, whichever version is installed
# --------------------------------------------------------------------------

FEATURE = """\
@api @rest @busterblock_api @capability
Feature: BusterBlock Reviews API

  @smoke @typed @schema
  Scenario: Submitting a review returns the stored record
    Given a step

  @negative
  Scenario: An empty name is rejected
    Given a step

  Scenario: An untagged scenario
    Given a step
"""


class TestRealBehaveModel:
    def test_feature_level_tags_are_labelled(self):
        """The reported case: 4 inherited + 3 own = 7 chips, broad → specific."""
        sc = parse_feature(FEATURE).scenarios[0]
        assert _tags_of(sc) == ["api", "rest", "busterblock_api", "capability",
                                "smoke", "typed", "schema"]

    def test_scenario_with_one_own_tag(self):
        sc = parse_feature(FEATURE).scenarios[1]
        assert _tags_of(sc) == ["api", "rest", "busterblock_api", "capability",
                                "negative"]

    def test_untagged_scenario_still_inherits(self):
        """No own tags is not no tags — the feature's still apply."""
        sc = parse_feature(FEATURE).scenarios[2]
        assert _tags_of(sc) == ["api", "rest", "busterblock_api", "capability"]

    def test_nothing_behave_considers_effective_is_dropped(self):
        """Completeness, stated against behave's own authority rather than a
        hand-written expectation."""
        for sc in parse_feature(FEATURE).scenarios:
            assert set(_tags_of(sc)) == set(sc.effective_tags)

    def test_order_is_identical_across_repeated_builds(self):
        """The 1.3 regression this guards: a set-ordered chip row differs run
        to run. Same parse, same list, every time."""
        sc = parse_feature(FEATURE).scenarios[0]
        assert len({tuple(_tags_of(sc)) for _ in range(20)}) == 1

    def test_no_duplicate_chips(self):
        sc = parse_feature("@shared\nFeature: F\n  @shared @own\n"
                           "  Scenario: S\n    Given a step\n").scenarios[0]
        assert _tags_of(sc) == ["shared", "own"]

    def test_feature_with_no_tags(self):
        sc = parse_feature("Feature: F\n  @own\n"
                           "  Scenario: S\n    Given a step\n").scenarios[0]
        assert _tags_of(sc) == ["own"]

    def test_scenario_outline_row_carries_every_level(self):
        """behave folds the outline's tags and its Examples' tags into each
        generated row's own tags, so they ride along with no special case."""
        feature = parse_feature(
            "@featlvl\nFeature: F\n"
            "  @outline\n"
            "  Scenario Outline: S <who>\n"
            "    Given a step for <who>\n"
            "  @examples_tag\n"
            "  Examples:\n"
            "    | who   |\n"
            "    | admin |\n")
        rows = list(feature.scenarios[0].scenarios)
        assert rows, "expected behave to generate a scenario per Examples row"
        tags = _tags_of(rows[0])
        assert tags[0] == "featlvl"                    # inherited stays first
        assert {"outline", "examples_tag"} <= set(tags)


@pytest.mark.skipif(behave.__version__ < "1.3",
                    reason="Rule requires behave 1.3+")
class TestRuleLevelTags:
    def test_rule_tags_sit_between_feature_and_scenario(self):
        feature = parse_feature(
            "@featlvl\nFeature: F\n"
            "  @rulelvl\n  Rule: R\n"
            "    @scenlvl\n    Example: S\n      Given a step\n")
        sc = list(feature.rules)[0].scenarios[0]
        assert _tags_of(sc) == ["featlvl", "rulelvl", "scenlvl"]


# --------------------------------------------------------------------------
# both behave shapes, emulated — so this holds on whichever is installed
# --------------------------------------------------------------------------

class _Tag(str):
    """behave.model.Tag is a str subclass; nothing may assume plain str."""


def _node(tags, parent=None):
    node = MagicMock()
    node.tags = [_Tag(t) for t in tags]
    node.parent = parent
    return node


def _scenario_1_2_6(feature_tags, own_tags):
    """behave 1.2.6: no `parent` link, effective_tags is an ordered list."""
    sc = MagicMock()
    sc.name = "S"
    sc.tags = [_Tag(t) for t in own_tags]
    sc.feature = MagicMock()
    sc.feature.name = "F"
    sc.feature.tags = [_Tag(t) for t in feature_tags]
    del sc.parent                      # 1.2.6 genuinely has no such attribute
    sc.effective_tags = sc.feature.tags + sc.tags
    return sc


def _scenario_1_3(feature_tags, own_tags, rule_tags=()):
    """behave 1.3: parent chain, effective_tags is an unordered set."""
    feature = _node(feature_tags)
    feature.name = "F"
    parent = _node(rule_tags, parent=feature) if rule_tags else feature
    sc = MagicMock()
    sc.name = "S"
    sc.tags = [_Tag(t) for t in own_tags]
    sc.feature = feature
    sc.parent = parent
    sc.effective_tags = {*feature_tags, *rule_tags, *own_tags}   # a SET
    return sc


class TestBothBehaveShapes:
    def test_1_2_6_shape_keeps_file_order(self):
        assert _tags_of(_scenario_1_2_6(("api", "rest"), ("smoke",))) == [
            "api", "rest", "smoke"]

    def test_1_3_shape_keeps_file_order_despite_set(self):
        """The shipped bug: effective_tags is a set, so trusting it for order
        scrambles the chips. The parent chain is what keeps them in file order."""
        assert _tags_of(_scenario_1_3(("api", "rest", "busterblock_api",
                                       "capability"), ("smoke",))) == [
            "api", "rest", "busterblock_api", "capability", "smoke"]

    def test_1_3_shape_with_rule(self):
        assert _tags_of(_scenario_1_3(("featlvl",), ("scenlvl",),
                                      rule_tags=("rulelvl",))) == [
            "featlvl", "rulelvl", "scenlvl"]

    def test_both_shapes_agree(self):
        args = (("api", "rest"), ("smoke", "typed"))
        assert _tags_of(_scenario_1_2_6(*args)) == _tags_of(_scenario_1_3(*args))

    def test_set_ordered_source_never_decides_order(self):
        """Directly: a set is not an acceptable order source."""
        assert writer._ordered_tags({"b", "a"}) is None
        assert writer._ordered_tags(["b", "a"]) == ["b", "a"]

    def test_backstop_recovers_a_tag_the_walk_cannot_see(self):
        """If a future behave inherits from somewhere this walk doesn't know,
        the tag is still reported — appended sorted, never dropped."""
        sc = _scenario_1_3(("api",), ("smoke",))
        sc.effective_tags = {"api", "smoke", "from_the_future", "another"}
        tags = _tags_of(sc)
        assert tags[:2] == ["api", "smoke"]          # known order preserved
        assert tags[2:] == ["another", "from_the_future"]   # sorted, deterministic


class TestDegradedInputs:
    """MagicMock is truthy but iterates EMPTY, so a truthiness-based fallback
    reads every mocked scenario as untagged — a regression that only ever ADDS
    empty and so trips no other assertion."""

    def test_magicmock_iterates_empty_confirming_the_hazard(self):
        assert list(MagicMock()) == [] and bool(MagicMock()) is True

    def test_bare_magicmock_scenario_falls_back_to_own_tags(self):
        sc = MagicMock()
        sc.name, sc.tags = "Mocked", ["smoke"]
        sc.feature = MagicMock()
        sc.feature.name = "Mocked feature"
        assert _tags_of(sc) == ["smoke"]

    def test_infinite_magicmock_parent_chain_terminates(self):
        """`sc.parent.parent.parent…` never ends on a MagicMock; the walk is
        guarded, and the feature's tags still arrive."""
        sc = MagicMock()
        sc.name, sc.tags = "Mocked", ["own"]
        sc.feature = MagicMock()
        sc.feature.name, sc.feature.tags = "F", ["inherited"]
        assert _tags_of(sc) == ["inherited", "own"]

    def test_no_tags_anywhere_yields_no_labels(self):
        sc = MagicMock()
        sc.name, sc.tags = "Odd", None
        sc.feature = MagicMock()
        sc.feature.name, sc.feature.tags = "Odd feature", None
        sc.effective_tags = None
        assert _tags_of(sc) == []

    def test_values_are_plain_strings_in_the_json(self):
        """Tag is a str subclass; the JSON must not carry the subclass."""
        assert all(type(v) is str
                   for v in _tags_of(_scenario_1_3(("api",), ("smoke",))))


class TestSurroundingLabelsIntact:
    def test_feature_and_suite_labels_unchanged(self):
        sc = parse_feature(FEATURE).scenarios[0]
        by_name = {lb["name"]: lb["value"]
                   for lb in writer.ScenarioResult(sc).result["labels"]}
        assert by_name["feature"] == "BusterBlock Reviews API"
        assert by_name["suite"] == "BusterBlock Reviews API"

    def test_skip_result_carries_inherited_tags_too(self):
        """A gated skip is still a reported test case (NOOD_0187) — same chips
        as a scenario that ran."""
        sc = parse_feature(FEATURE).scenarios[1]
        result = writer.ScenarioResult(sc)
        result.result["status"] = "skipped"
        assert [lb["value"] for lb in result.result["labels"]
                if lb["name"] == "tag"] == [
            "api", "rest", "busterblock_api", "capability", "negative"]
