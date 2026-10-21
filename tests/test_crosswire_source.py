"""Catalog source `c` (CrossWire modules bibles republishes, 2026-10-21): resolved like `o`, ingested from the crosswire CDN root."""
from lexeme_aligner import catalog_source as cs
from lexeme_aligner import openbible_source as ob


def test_c_ids_resolve_to_a_fetchable_edition_with_the_cdn_abbr_as_param():
    assert cs._split_id("c:GreVamvas") == ("c", "GreVamvas")
    ed = cs._edition("c:GreVamvas", "ell")
    assert ed["source"] == "c" and ed["param"] == "GreVamvas" and ed["fetchable"]
    assert cs.PRIORITY.index("c") > cs.PRIORITY.index("dbt")            # a direct source wins a same-text family


def test_pin_names_the_crosswire_root_licence_and_original_source():
    m = {"name": "Vamvas", "licenses": [{"type": "Public Domain"}],
         "source": "https://www.crosswire.org/sword/modules/ModInfo.jsp?modName=GreVamvas"}
    pin = ob.build_pin("ell", "GreVamvas", "grevamvas", m, 66, root="crosswire")
    assert pin["provider"] == "cdn.bibel.wiki/crosswire" and pin["license"] == "Public Domain"
    assert pin["license_url"] == "https://cdn.bibel.wiki/crosswire/ell/GreVamvas/_meta.json"
    assert pin["original_source"].endswith("GreVamvas") and pin["books"] == 66


def test_openbible_pins_are_unchanged_by_default():
    pin = ob.build_pin("mgw", "MATUMBI", "matumbi", {"name": "x"}, 27)
    assert pin["provider"] == "cdn.bibel.wiki/openbible" and "license" not in pin
    assert pin["license_url"] == "https://cdn.bibel.wiki/openbible/mgw/MATUMBI/_meta.json"
