"""NOOD_0119 — squeeze probe-side generation cost. Browser-free checks for:

  W1  compact control lists cap by default (facet-flood killer)
  W2  OneTrust-shaped consent noise dropped from compact, kept in full/--json
  W3a image-tile alt/title captions get their own author-ready slice

The model-agnostic gate (Part C-cross: green retail_SPA run on Sonnet 4.6 /
Haiku / GPT-4-class with no curl/grep recovery) is manual — it needs a real
browser and real models, so it lives in the ticket, not here.
"""
import json

from noodle.agents.web import probe
from unit_tests.test_nood_0117 import _c

# --- W1: default cap on compact lists ----------------------------------------

def _facets(n):
    """n visible checkbox facets, each named only by its class — exactly the
    results-page flood: needs_pom True, so nothing readable drops them."""
    return [_c(tag="input", type="checkbox", cls=f"nl-checkbox__facet-{i}")
            for i in range(n)]


def test_compact_caps_a_facet_flood_and_keeps_the_results_summary():
    sr = probe.summarize({"controls": _facets(200), "headings": []},
                         url="https://shop.example/s", title="Results")
    sr["term"] = "hotwheels"
    sr["results_summary"] = {
        "text": "1,234 results", "selector": '[id="summary"]', "count": 1234,
        "pom_yaml": "results summary:\n  css: '[id=\"summary\"]'\n",
        "suggested_assertion": probe._summary_assertion(),
    }
    home = probe.summarize({"controls": [_c(tag="button", text="Menu")],
                            "headings": []}, url="https://shop.example/")
    home["search"] = sr
    out = probe.render({"pages": [home], "errors": []}, compact=True)

    # NOOD_0137 — the flood now collapses BEFORE the cap: 200 same-shape
    # numbered facets render as one exemplar + a family count, not 25 lines.
    assert "(+199 more numbered like it)" in out
    assert "nl checkbox facet 0" in out          # one exemplar…
    assert "nl checkbox facet 1" not in out      # …and no other family member
    # the one thing --search exists to surface is never hidden by the cap
    assert "1,234 results" in out


def test_default_cap_constant_is_the_documented_value():
    assert probe.DEFAULT_COMPACT_CAP == 25


# --- W2: consent-manager noise ------------------------------------------------

def _consent_page():
    noisy = [_c(id="ot-group-id-C0004", tag="div"),
             _c(id="save-preference-btn-handler", tag="div"),
             _c(id="filter-apply-handler", tag="div")]
    pg = probe.summarize({"controls": noisy, "headings": []},
                         url="https://x/")
    return {"pages": [pg], "errors": []}, pg


def test_consent_noise_gone_from_compact_but_kept_in_full():
    result, pg = _consent_page()
    comp = probe.render(result, compact=True)
    full = probe.render(result, compact=False)
    for c in pg["controls"]:
        assert c["name"] not in comp, f"consent control leaked into compact: {c['name']}"
        assert c["name"] in full, f"full render dropped {c['name']}"


def test_consent_noise_gone_from_json_compact_but_kept_in_raw():
    result, pg = _consent_page()
    blob = json.dumps(probe.compact_payload(result))
    for c in pg["controls"]:
        assert c["name"] not in blob
    # raw (--json without --compact) is untouched — full completeness
    assert all(c["name"] in json.dumps(result) for c in pg["controls"])
    assert probe.compact_payload(result)["pages"][0]["needs_pom"] == []


# --- W3a: image-tile caption slice --------------------------------------------

def test_alt_only_tile_lands_in_the_caption_slice_with_a_pom_entry():
    tile = _c(tag="a", href="/tires", alt="Shop Tires now.")
    result = {"pages": [probe.summarize({"controls": [tile], "headings": []},
                                        url="https://x/")], "errors": []}
    comp = probe.render(result, compact=True)
    assert "tile captions" in comp
    assert "shop tires now." in comp
    assert "alt_text: 'Shop Tires now.'" in comp   # paste-ready POM, inline

    pg = probe.compact_payload(result)["pages"][0]
    tiles = pg["tile_captions"]
    assert len(tiles) == 1 and tiles[0]["name"] == "shop tires now."
    assert tiles[0]["pom"] == ["shop tires now.:", "  alt_text: 'Shop Tires now.'"]
    # moved OUT of the general dump, not duplicated into it
    assert pg["needs_pom"] == []
