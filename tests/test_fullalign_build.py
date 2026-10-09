"""fullalign_build.py: which gold layers an edition gets, and the edition-name mapping (no spine, no files)."""
from lexeme_aligner import fullalign_build as fb


def _manifest(layers):
    return {"languages": {"xyz": {"editions": {"xyzed": {"layers": {"manual": layers}}}}}}


def test_gbt_quarantined_transfer_and_vendor_partitions_are_left_out_with_a_reason():
    m = _manifest({
        "AAA": {"source": "clear", "kind": "manual", "license": "CC-BY-4.0", "publishable": True},
        "BSB-tables": {"source": "bsb-tables", "kind": "manual", "license": "CC0-1.0"},
        "gbt": {"source": "gbt", "kind": "manual", "license": "CC0-1.0", "publishable": True},
        "RUSSYN": {"source": "clear", "kind": "manual", "quarantined": True, "quarantine_reason": "scrambled", "publishable": True},
        "JFA11": {"source": "clear", "kind": "transfer", "publishable": True},
        "Segond1910": {"source": "sword", "kind": "manual", "publishable": False},
    })
    keep, skipped = fb.manual_layers("xyz", "xyzed", m)
    assert [(k["part"], k["source"], k["kind"]) for k in keep] == [("AAA", "clear", "manual"), ("BSB-tables", "bsb-tables", "bsb")]
    reasons = " | ".join(skipped)
    assert "gbt" in reasons and "quarantined: scrambled" in reasons and "transfer" in reasons and "not publishable" in reasons


def test_the_gold_edition_of_a_chain_tag_is_the_tag_itself_or_the_registry_align_tag():
    m = {"languages": {"eng": {"editions": {"engbsb": {}, "eng_ylt": {}}}, "fra": {"editions": {"fra-lsg": {}}}}}
    reg = {"eng": {"edition": "engbsb", "align_tag": "bsb"}, "fra": {"edition": "fra-lsg", "align_tag": "fra_lsg"}}
    assert fb.fa_edition_of("eng", "bsb", m, reg) == "engbsb"
    assert fb.fa_edition_of("eng", "eng_ylt", m, reg) == "eng_ylt"
    assert fb.fa_edition_of("fra", "fra_lsg", m, reg) == "fra-lsg"
    assert fb.fa_edition_of("eng", "eng_kjv", m, reg) is None                      # no gold for this edition: out of scope


def test_problems_are_the_counts_that_mean_data_would_be_lost():
    import collections
    t = collections.Counter({"rows": 10, "rows lost": 0, "derived mismatch: target": 2, "psalm-title token rows dropped": 5,
                             "books not decodable with the final _layer.json": 1})
    assert sorted(fb.problems(t)) == ["books not decodable with the final _layer.json", "derived mismatch: target"]
