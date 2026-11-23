from lexeme_aligner import pa_fold, pa_from_compact as pc

# three tokens: καί (function, unit 0), λέγει (units 1-2, carrier 1), αὐτοῖς (units 3-4, carrier 3)
VERSE = {
    "units": [["400010010011", "particle", "καὶ", 0],
              ["400010010021", "stem", "λέγ", 1], ["400010010022", "ending", "ει", 1],
              ["400010010031", "stem", "αὐτ", 2], ["400010010032", "ending", "οῖς", 2]],
    "tokens": [["40001001001", "2532", 0, 0], ["40001001002", "3004", 1, 1], ["40001001003", "0846", 1, 3]],
}


def test_links_go_on_carrying_units_and_fold_back_exactly():
    main, meta = "0:0-1 1:3", {"method": "EG", "conf": "21", "contested": "1:e:4", "rend": "1 2"}
    pm, pmeta, px = pc.convert_verse(VERSE, main, meta, "")
    assert pm == "1:0-1 3:3" and pmeta["contested"] == "3:e:4" and pmeta["method"] == "EG"
    for rule in ("fold", "stem"):
        assert pa_fold.fold_verse(VERSE, pm, pmeta, px, fold_rule=rule) == (main, meta, "")


def test_extra_and_bonus_for_unlinked_words():
    pm, pmeta, px = pc.convert_verse(VERSE, "0:1", {"method": "E", "bonus": "1:7:12:40"}, "1:5-6")
    assert px == "3:5-6" and pmeta["bonus"] == "3:7:12:40"
    assert pa_fold.fold_verse(VERSE, pm, pmeta, px) == ("0:1", {"method": "E", "bonus": "1:7:12:40"}, "1:5-6")


def test_stale_extra_for_a_linked_word_is_dropped():
    assert pc.clean_extra("0:2 6:19", "6:1 3:4") == ("3:4", 1)
    assert pc.clean_extra("", "") == ("", 0)
