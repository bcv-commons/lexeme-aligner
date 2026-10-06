import collections

from lexeme_aligner.eval import gold_refs


def test_ot_gold_rekeyed_to_spine_for_english_numbered_edition():
    # no usj dir for this tag -> scheme falls back to the manual config (protestant = English numbering)
    gold = collections.defaultdict(set)
    gold[("19003001", "H1")].add("a")      # English PSA 3:1 = the spine's superscription's neighbour: spine PSA 3:2
    gold[("19003008", "H2")].add("b")      # English PSA 3:8 = spine PSA 3:9
    gold[("01001001", "H3")].add("c")      # GEN 1:1 unchanged
    gold[("40001001", "G4")].add("d")      # NT untouched
    out = gold_refs.to_spine(gold, "zz-no-such-edition")
    assert ("19003002", "H1") in out and ("19003001", "H1") not in out
    assert ("19003009", "H2") in out
    assert ("01001001", "H3") in out and ("40001001", "G4") in out


def test_no_tag_is_identity():
    g = {("19003001", "H1"): {"a"}}
    assert gold_refs.to_spine(g, None) is g
