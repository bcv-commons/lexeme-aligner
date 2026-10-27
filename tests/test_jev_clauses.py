"""jev_clauses: clause units, target windows in written words, edits back to verses (no API)."""
from lexeme_aligner.eval import jev_clauses as jcl
from lexeme_aligner.eval import llm_edit as le
from lexeme_aligner.hebrew_source import HebToken


def row(sent, clause, words, text="x"):
    return {"sentence": sent, "clause": clause, "words": words, "text": text}


def test_interrupted_clause_is_one_item_and_verse_break_joins():
    rows = [row("s", "A", ["1", "2"]), row("s", "B", ["3", "4", "5"]), row("s", "A", ["6"]),   # A interrupted by B
            row("s", "C", ["7"]), row("s", "C", ["8"])]                                       # C over a verse break
    items = jcl.clause_items(rows)
    assert [it["words"] for it in items] == [["1", "2", "6"], ["3", "4", "5"], ["7", "8"]]


def test_join_rule_short_lines_up_to_three_and_sentence_bound():
    items = [{"sentence": "s", "clause": c, "words": [f"{c}{i}" for i in range(n)], "text": c} for c, n in
             [("a", 2), ("b", 7), ("c", 7), ("d", 2), ("e", 2), ("f", 2), ("g", 2)]]
    items.append({"sentence": "t", "clause": "h", "words": ["h0"], "text": "h"})
    us = jcl.join_units(items)
    assert [len(u["words"]) for u in us] == [9, 11, 4, 1]       # a+b (a < 6); c+d+e (d < 6, then 3 lines); f+g; h (other sentence)
    assert us[-1]["sentence"] == "t"


def test_title_row_goes_with_next_line():
    items = [{"sentence": "s", "clause": None, "words": list("abcdefgh"), "text": "t"},
             {"sentence": "s", "clause": "A", "words": list("ijklmnop"), "text": "a"}]
    assert len(jcl.join_units(items)) == 1


def _tok(i, key, content=True):
    return HebToken(i, f"g{i}", f"G{i:04d}", f"grc:{i}", f"g{i}", None, content, keys=[key])


def _book():
    # verse 1: source k1 k2, target 6 tokens; parts view: tokens 1-3 are one written word
    p1 = le.Proposal(41001001, "MRK", 1, 1, [_tok(0, "k1"), _tok(1, "k2")], ["a", "b1", "b2", "b3", "c", "d"])
    p1.owner = {0: [0], 1: [4]}
    p2 = le.Proposal(41001002, "MRK", 1, 2, [_tok(0, "k3")], ["e", "f"])
    p2.owner = {0: [1]}
    units = {41001001: [{"word_index": 0, "in_word": None, "part": None}] + [{"word_index": 1, "in_word": "b", "part": x}
                                                                            for x in ("prefix", "stem", "suffix")]
             + [{"word_index": 2, "in_word": None, "part": None}, {"word_index": 3, "in_word": None, "part": None}]}
    return jcl.lay_out([p1, p2], units)


def test_margin_counts_written_words_and_whole_words():
    b = _book()
    assert b.word == [0, 1, 1, 1, 2, 3, 4, 5]
    assert jcl._widen(b, 0, 0, 1) == (0, 3)            # one written word out = all three parts of b
    assert jcl._widen(b, 2, 2, 0) == (1, 3)            # a part in the window brings its whole word


def test_every_target_word_has_one_home_and_lies_in_its_window():
    b = _book()
    units = [{"sentence": "s", "words": ["k1"]}, {"sentence": "s", "words": ["k2", "k3"]}]
    ws = jcl.windows(b, units, 0)
    homes = [g for w in ws for g in w["home"]]
    assert sorted(homes) == list(range(len(b.seq)))
    for w in ws:
        assert all(w["lo"] <= g <= w["hi"] for g in w["home"])
    assert ws[1]["hs"] == [(41001001, 1), (41001002, 0)]    # a unit over a verse break


def test_cross_verse_answer_is_dropped_and_others_map_back():
    b = _book()
    ws = jcl.windows(b, [{"sentence": "s", "words": ["k1"]}, {"sentence": "s", "words": ["k2", "k3"]}], 0)
    w = ws[1]
    p, src, tgt, _ = jcl.unit_proposal(b, w)
    j_f = tgt.index(b.order[(41001002, 1)])
    j_c = tgt.index(b.order[(41001001, 4)])
    h_ed = [(0.9, le.Edit("assign", h=0, t=[j_f])),     # k2 (verse 1) -> f (verse 2): cannot be stored per verse
            (0.8, le.Edit("assign", h=1, t=[j_f]))]     # k3 (verse 2) -> f (verse 2)
    t_ed = [(0.7, le.Edit("added", t=[j_c]))]
    per, c = jcl.map_back(b, p, src, tgt, h_ed, t_ed, w["ring"])
    assert c["cross-verse answers (dropped)"] == 1
    assert per[41001002][0][0][1].h == 0 and per[41001002][0][0][1].t == [1]
    assert per[41001001][1][0][1].t == [4]


def test_filter_module_keeps_only_present_affixes_and_words():
    text = ("# zin\n- **ku-** prefix 1 verb INF → VERB [`m:ku-inf:verb` observed corpus-derived]\n"
            "- **ti-** prefix 1 verb NEG → NEG [`m:ti:verb` observed corpus-derived]\n"
            "- okulya: whole [`w:okulya` observed corpus-derived]\n- Link the demonstrative [`article_demonstrative` observed]")
    out, kept = jcl.filter_module(text, {"m:ti:verb"}, {"abali"})
    assert "ku-" not in out and "ti-" in out and "okulya" not in out and "demonstrative" in out
    assert kept == ["m:ti:verb", "article_demonstrative"]
