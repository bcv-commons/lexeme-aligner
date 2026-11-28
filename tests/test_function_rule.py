from types import SimpleNamespace as NS

from lexeme_aligner import compact_align as CA
from lexeme_aligner import function_rule as FR


def tok(lexeme, content):
    return NS(lexeme=lexeme, is_content=content)


def rec(heb, toks):
    return NS(heb=heb, toks=toks)


# source: waw + verb + "the" + noun   target: "and he saw the king"
R = rec([tok("hbo:2050", False), tok("hbo:7200", True), tok("hbo:1886", False), tok("hbo:4428", True)],
        ["and", "he", "saw", "the", "king"])
LISTS = {"hbo:2050": {"and"}, "hbo:1886": {"the"}}


def test_places_listed_forms_between_content_links():
    assert FR.place(R, {1: (1, 2), 3: (4,)}, LISTS) == {0: 0, 2: 3}


def test_no_answer_when_the_count_differs_and_never_removes():
    r = rec(R.heb, ["and", "and", "saw", "the", "king"])
    assert FR.place(r, {1: (2,), 3: (4,)}, LISTS) == {2: 3}          # two "and" for one waw: no answer for the waw


def test_crossing_links_give_no_window():
    assert FR.place(R, {1: (4,), 3: (1,)}, LISTS) == {0: 0}          # the article sits between crossing links: no answer


def test_a_token_held_by_a_content_link_is_not_in_the_window():
    assert FR.place(R, {1: (0, 2), 3: (4,)}, LISTS) == {2: 3}


def test_learn_keeps_forms_linked_to_function_tokens():
    recs = {("RUT", 1, v): R for v in range(1, 5)}
    links = {("RUT", 1, v, h): ts for v in range(1, 5) for h, ts in ((0, (0,)), (1, (1, 2)), (2, (3,)), (3, (4,)))}
    lists = FR.learn(recs, links)
    assert lists["hbo:2050"] == {"and"} and lists["hbo:1886"] == {"the"}


def test_resolve_rule_wins_only_where_it_differs():
    ef = {"_method": "eflomal", "t_idx": [3], "target": "the", "score": 0.9}
    fr_same = {"_method": "fnrule", "t_idx": [3], "target": "the"}
    fr_diff = {"_method": "fnrule", "t_idx": [0], "target": "and"}
    methods = ("spanext", "eflomal", "gloss", "gapfill", "fnrule")
    assert CA._resolve({"eflomal": ef, "fnrule": fr_same}, methods, None) == (ef, None)
    assert CA._resolve({"eflomal": ef, "fnrule": fr_diff}, methods, None) == (fr_diff, ef)
    assert CA._resolve({"fnrule": fr_diff}, methods, None) == (fr_diff, None)
