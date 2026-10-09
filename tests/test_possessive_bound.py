from types import SimpleNamespace as T

from lexeme_aligner.possessive_bound import has_possessor, stratified_lift


def tok(idx, key, content, lexeme="x", person=None, case_=None):
    return T(idx=idx, keys=[key], is_content=content, lexeme=lexeme, person=person, case_=case_)


def test_hebrew_suffix_of_the_same_word_counts_a_later_word_does_not():
    toks = [tok(0, "080010010172", True), tok(1, "080010010173", False, person="3"), tok(2, "080010010181", False, person="3")]
    assert has_possessor(toks, 0)
    toks2 = [tok(0, "080010010172", True), tok(1, "080010010181", False, person="3")]
    assert not has_possessor(toks2, 0)


def test_greek_following_genitive_pronoun_counts():
    toks = [tok(0, "40001001001", True), tok(1, "40001001002", False, lexeme="grc:0846", case_="genitive")]
    assert has_possessor(toks, 0)
    toks[1].case_ = "nominative"
    assert not has_possessor(toks, 0)


def test_within_lexeme_lift_ignores_which_lexemes_are_possessed():
    # lexeme A always ends in "r" (father/mother), lexeme B never; possessed rows are mostly A -> cross-lexeme lift for "r" is high,
    # within-lexeme lift is zero. A real suffix "-hu" on possessed forms of both lexemes is found.
    rows = [("fathr", True, "A")] * 9 + [("fathr", False, "A")] * 3 + [("house", True, "B")] * 3 + [("house", False, "B")] * 9
    best = stratified_lift(rows)
    assert best["lift"] < 0.05
    rows2 = [("fathrhu", True, "A")] * 6 + [("fathr", False, "A")] * 6 + [("househu", True, "B")] * 6 + [("house", False, "B")] * 6
    best2 = stratified_lift(rows2)
    assert best2["edge"] == "suffix" and best2["affix"].endswith("u") and best2["lift"] > 0.9
