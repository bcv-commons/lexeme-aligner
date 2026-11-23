from types import SimpleNamespace as NS

from lexeme_aligner.eval import jev_refine as jr
from lexeme_aligner.eval.jev_clauses import apply_accept
from lexeme_aligner.eval.llm_edit import Proposal


def tok(idx, key, lexeme, surface, content=True):
    return NS(idx=idx, keys=[key], lexeme=lexeme, surface=surface, strong="G1", lemma=surface, is_content=content, gloss_en=None)


def verse():
    """Greek: τοῖς (1 unit) μαθητ|αῖς (2 units); Hebrew: ל|תלמידים (2 pieces) + ויאמר (whole word)."""
    words = [tok(0, "w1", "grc:3588", "τοῖς", False), tok(1, "w2", "grc:3101", "μαθηταῖς"), tok(2, "w3", "grc:3004", "εἶπεν")]
    parts = [tok(0, "w1", "grc:3588", "τοῖς", False), tok(1, "w2", "grc:3101", "μαθητ"), tok(2, "w2.2", "grc:~ending:N-DPM", "αῖς", False),
             tok(3, "w3", "grc:3004", "εἶπ"), tok(4, "w3.2", "grc:~ending:V-2AAI-3S", "εν", False)]
    return words, parts


def test_components_and_classify():
    words, parts = verse()
    uo = jr.word_units(words, parts)
    assert [u.idx for u in uo[1]] == [1, 2] and jr.carrier(uo[1]).idx == 1 and jr.unit_kind(uo[1][1], uo[1]) == "ending"
    links = {0: [0], 1: [0], 2: [1]}
    pieces = {0: [(0, 0, "ל", "prefix"), (0, 1, "תלמידים", "stem")], 1: [(1, None, "ויאמר", "word")]}
    cs = sorted(jr.components(links, uo, pieces), key=lambda c: c.words)
    assert [c.words for c in cs] == [[0, 1], [2]]
    assert jr.classify(cs[0]) == "ask" and jr.classify(cs[1]) == "one-piece"
    one = jr.Component((1, 1), [0], [0], pieces[0], uo[0], links={0: [0]}, word_units={0: uo[0]})
    assert jr.classify(one) == "one-unit" and one.assign == {0: 0, 1: 0}


def test_chain_split_needs_every_word_and_no_double_claim():
    words, parts = verse()
    uo = jr.word_units(words, parts)
    c = jr.components({0: [0], 1: [0]}, uo, {0: [(0, 0, "ל", "prefix"), (0, 1, "תלמידים", "stem")]})[0]
    assert jr.chain_split(c, {(0, 0): {0}, (0, 1): {1}}) == {0: 0, 1: 1}
    assert jr.chain_split(c, {(0, 0): {0, 2}, (0, 1): {1}}) is None          # a piece claimed by two units
    assert jr.chain_split(c, {(0, 1): {1}}) is None                           # τοῖς (word 0) gets nothing


def test_finish_answers_fallback_and_word_kept():
    words, parts = verse()
    wr = NS(heb=words, ch=1, v=1)
    pr = NS(heb=parts, toks=["לתלמידים", "ויאמר"], ch=1, v=1, tref=(1, 1))
    uo = jr.word_units(words, parts)
    links = {0: [0], 1: [0], 2: [1]}
    pieces = {0: [(0, 0, "ל", "prefix"), (0, 1, "תלמידים", "stem")], 1: [(1, None, "ויאמר", "word")]}
    cs = sorted(jr.components(links, uo, pieces), key=lambda c: c.words)
    for c in cs:
        c.ref = (1, 1); jr.classify(c)
    rows = [(wr, pr, cs, {"links": links, "none": set()})]
    rec = jr.finish(rows, {((1, 1), 0): {0: 2, 1: 1}}, "MRK")[0]            # ל -> the ending αῖς, the stem -> μαθητ
    got = {p["h_idx"]: p["t_parts"] for p in rec["pairs"]}
    assert got[2] == [[0, 0]] and got[1] == [[0, 1]] and got[3] == [[1, None]]
    assert got[0] == [[0, 0], [0, 1]]                                          # τοῖς got no piece: keeps its word answer
    rec = jr.finish(rows, {}, "MRK")[0]                                        # unanswered: the word answers on the carriers
    assert {p["h_idx"] for p in rec["pairs"]} == {0, 1, 3}
    assert all(p["prior"] in ("llm_refine_unanswered", "llm_refine_word") for p in rec["pairs"])


def test_finish_word_none_marks_every_unit():
    words, parts = verse()
    wr, pr = NS(heb=words, ch=1, v=1), NS(heb=parts, toks=["x"], ch=1, v=1, tref=(1, 1))
    rec = jr.finish([(wr, pr, [], {"links": {}, "none": {2}})], {}, "MRK")[0]
    assert sorted(s["h_idx"] for s in rec["llm_skipped"]) == [3, 4]


def test_apply_accept_fixes_the_link_and_drops_questions():
    h = [NS(idx=0), NS(idx=1)]
    p = Proposal(41001001, "MRK", 1, 1, h, ["a", "b", "c"], owner={0: [0, 1], 1: [2]})
    p.doubt_h[0] = {"x"}; p.doubt_h[1] = {"y"}; p.doubt_t[1] = {"z"}; p.stop_t = {1, 2}
    n = apply_accept([p], {"books": {"MRK": {"1:1": {"1": [1]}}}}, "MRK")
    assert n == 1 and p.owner == {0: [0], 1: [1]} and p.method[1] == "reuse"
    assert 1 not in p.doubt_h and 0 in p.doubt_h and 1 not in p.doubt_t and p.stop_t == {2}
