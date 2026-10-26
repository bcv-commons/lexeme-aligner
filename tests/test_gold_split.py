import collections
from types import SimpleNamespace as T

from lexeme_aligner.eval.gold_split import split_verse
from lexeme_aligner.eval.pos_score import GoldVerse


def _tok(idx, surface, lemma, content, key, gloss, strong):
    return T(idx=idx, surface=surface, lemma=lemma, is_content=content, keys=[key], gloss_en=gloss, strong=strong)


def test_prefix_and_suffix_take_their_own_words():
    # בִּימֵי -> "in the days" ; וַיֹּאמֶר -> "said" (no "and") ; אִשְׁתּוֹ -> "his wife"
    heb = [_tok(0, "בִּ", "בְּ", False, "080010010021", "in", "H0871"),
           _tok(1, "ימֵי", "יוֹם", True, "080010010022", "the.days", "H3117"),
           _tok(2, "וַ", "וְ", False, "080010010031", "and", "H2050"),
           _tok(3, "יֹּאמֶר", "אָמַר", True, "080010010032", "said", "H0559"),
           _tok(4, "אִשְׁתּ", "אִשָּׁה", True, "080010010041", "wife", "H0802"),
           _tok(5, "וֹ", "הוּא", False, "080010010042", "his", "H2050")]
    toks = ["in", "the", "days", "said", "his", "wife"]
    gv = GoldVerse(1)
    gv.links = {("H3117", 0): {0, 1, 2}, ("H0559", 0): {3}, ("H0802", 0): {4, 5}}
    gv.raw = {("H3117", 0): ("o080010010022", []), ("H0559", 0): ("o080010010032", []), ("H0802", 0): ("o080010010041", [])}
    key_of = {0: ("H0871", 0), 1: ("H3117", 0), 2: ("H2050", 0), 3: ("H0559", 0), 4: ("H0802", 0), 5: ("H2050", 1)}
    out = split_verse(gv, heb, toks, key_of, collections.Counter())
    assert out.exact_keys
    assert out.links[("H0871", 0)] == {0} and out.unit[("H0871", 0)] == "list"
    assert out.links[("H3117", 0)] == {1, 2} and out.unit[("H3117", 0)] == "rest"
    assert out.links[("H2050", 0)] == set() and out.unit[("H2050", 0)] == "none"
    assert out.links[("H0559", 0)] == {3}
    assert out.links[("H2050", 1)] == {4} and out.links[("H0802", 0)] == {5}
