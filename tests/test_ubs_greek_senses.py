from lexeme_aligner.ubs_greek_senses import gnorm
from lexeme_aligner.ubs_senses import bind_verse


def test_gnorm_drops_accents_and_folds_final_sigma():
    assert gnorm("Ἰησοῦς") == gnorm("ιησουσ") == "ιησουσ"
    assert gnorm("βίβλος") == "βιβλοσ"


def test_greek_binding_uses_lemma_when_strongs_differ():
    toks = [{"idx": 0, "strong": 976, "lemma_c": gnorm("βίβλος")}, {"idx": 1, "strong": 1078, "lemma_c": gnorm("γένεσις")}]
    entry = {"m1": {"strongs": {976}, "lemmas": {gnorm("βίβλος")}}, "m2": {"strongs": set(), "lemmas": {gnorm("γένεσις")}}}
    bound, _ = bind_verse([(1, "m1", "s1"), (2, "m2", "s2")], toks, entry)
    assert bound[0][0] == "s1" and bound[1][0] == "s2"
