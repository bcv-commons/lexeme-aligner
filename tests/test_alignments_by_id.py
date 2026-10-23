"""alignments_by_id.verse_links: one outcome per Greek word (kept / edited / edited-wide / inside / part / none), parts as "t.p"."""
from types import SimpleNamespace as NS

from lexeme_aligner.alignments_by_id import verse_links


def _g(keys, lexeme, content=False):
    return NS(keys=keys, lexeme=lexeme, is_content=content)


POS = {"grc:2532": "conj", "grc:3588": "det", "grc:1722": "prep", "grc:3624": "noun", "grc:846": "pron"}.get
TOKS = ["ג", "ובביתו", "ישב", "הוא"]                              # verse 3: its number, "and in his house", "sat", "he"
PARTS = {1: {0: ("וּ", "cj"), 1: ("בְ", "prep"), 2: ("בֵית", "noun"), 3: ("וֹ", "pron")}}


def test_outcomes():
    heb = [_g(["40001003001"], "grc:2532"), _g(["40001003002"], "grc:1722"), _g(["40001003003"], "grc:3624", True),
           _g(["40001003004"], "grc:846"), _g(["40001003005"], "grc:3588"), _g(["40001003006"], "grc:2532")]
    chain = {0: {"t_idx": [1], "t_parts": [[1, 0]]}, 1: {"t_idx": [1], "t_parts": [[1, 1]]}, 2: {"t_idx": [1], "t_parts": [[1, 2]]},
             3: {"t_idx": [1], "t_parts": [[1, 3]]}, 4: {"t_idx": [0], "t_parts": [[0, None]]}}
    jev = {"pairs": [{"h_idx": 0, "t_idx": [1], "prior": "llm_edit_kept"},
                     {"h_idx": 2, "t_idx": [1, 2], "prior": "llm_edit_edited"},          # swept in a neighbour: edited-wide
                     {"h_idx": 3, "t_idx": [1], "prior": "llm_edit_inside"},
                     {"h_idx": 5, "t_idx": [0], "prior": "llm_edit_edited"}],          # on the verse number: dropped -> none
           "llm_skipped": [{"h_idx": 1, "status": "unrepresented"}, {"h_idx": 4, "status": "unrepresented"}]}
    links, none, added = verse_links(heb, TOKS, chain, jev, PARTS, POS)
    assert links == [[["1.0"], ["n40001003001"], "kept"],
                     [["1.1"], ["n40001003002"], "part"],                  # Jev: none; the chain's prep part fits ἐν
                     [["1.2", 2], ["n40001003003"], "edited-wide"],
                     [["1.3"], ["n40001003004"], "inside"]]
    assert none == ["n40001003005", "n40001003006"]                       # the article's chain link was the verse number
    assert added == [3]                                                   # "הוא" renders nothing; the verse number is never added


def test_inside_picks_the_one_fitting_part():
    heb = [_g(["40001003001"], "grc:1722")]
    links, _, _ = verse_links(heb, TOKS, {}, {"pairs": [{"h_idx": 0, "t_idx": [1], "prior": "llm_edit_inside"}]}, PARTS, POS)
    assert links == [[["1.1"], ["n40001003001"], "inside"]]
