from types import SimpleNamespace as T

from lexeme_aligner.eval.word_checks import _gold_wrong_words, verse_checks


def tok(idx, key, lexeme, content, strong=None, **kw):
    base = dict(idx=idx, keys=[key], lexeme=lexeme, is_content=content, strong=strong if strong else (lexeme if content else None),
                person=None, construct_group=None, is_superscription=False, surface="x")
    base.update(kw)
    return T(**base)


# RUT 1:1-like word: בִּ (prefix) + ימֵי (stem); word 2: אִשְׁתּ (stem) + וֹ (suffix, person 3)
HEB = [tok(0, "080010010021", "hbo:0871a", False, strong="H0871"), tok(1, "080010010022", "hbo:3117", True),
       tok(2, "080010010171", "hbo:0802", True), tok(3, "080010010172", "hbo:2050c", False, strong="H2050", person="3")]


def test_suffix_pronoun_and_prefix_preposition_checks():
    aligned = {0: [0], 1: [2], 2: [10], 3: [9]}          # "In the days ... his wife": prefix before, suffix adjacent
    items = {it["check"]: it for it in verse_checks(HEB, aligned, {"adposition": "before"}, None)}
    assert items["prefix_prep"]["detail"] == "before" and not items["prefix_prep"]["flagged"]
    assert items["suffix_pronoun"]["detail"] == 1 and not items["suffix_pronoun"]["flagged"]
    far = verse_checks(HEB, {0: [5], 1: [2], 2: [10], 3: [4]}, {"adposition": "before"}, None)
    by = {it["check"]: it for it in far}
    assert by["prefix_prep"]["flagged"] and by["prefix_prep"]["detail"] == "after"
    assert by["suffix_pronoun"]["flagged"] and by["suffix_pronoun"]["detail"] == 6


def test_bound_article_check_only_for_bound_languages():
    heb = [tok(0, "40001001001", "grc:3588", False, strong="G3588"), tok(1, "40001001002", "grc:2316", True)]
    assert verse_checks(heb, {0: [3], 1: [4]}, {"article_bound": False}, None) == []
    (it,) = verse_checks(heb, {0: [3], 1: [4]}, {"article_bound": True}, None)
    assert it["check"] == "article_word" and it["flagged"]
    (ok,) = verse_checks(heb, {0: [4], 1: [4]}, {"article_bound": True}, None)
    assert not ok["flagged"]


def test_construct_gap_ignores_function_words():
    heb = [tok(0, "080010010011", "hbo:1121", True, construct_group="g1"), tok(1, "080010010021", "hbo:3478", True, construct_group="g1")]
    fn = lambda p: p == 1                                   # noqa: E731  position 1 is "of"
    (a, b) = verse_checks(heb, {0: [0], 1: [2]}, {}, fn)
    assert not a["flagged"]
    (a, b) = verse_checks(heb, {0: [0], 1: [4]}, {}, fn)
    assert a["flagged"] and a["detail"] == 2


def test_gold_wrong_words_compares_whole_word_spans():
    gv = T(links={("H0871", 0): {0, 1, 2}})
    wrong = _gold_wrong_words(HEB, {0: [0], 1: [1, 2]}, gv)
    assert wrong == {"08001001002": False}
