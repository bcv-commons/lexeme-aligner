"""Positional gold scorer (pos_score.py): Clear-tokenization reconstruction, mapping onto our tokens, union
with vetoes, and the link metrics — all on synthetic data, no parquet needed."""
import collections
import pytest

import lexeme_aligner.eval.pos_score as ps
from lexeme_aligner.eval.pos_score import GoldVerse, Metrics, Ours, Spine, clear_tokens, map_positions, score, union
from lexeme_aligner.usj_source import tokenize


def test_clear_tokens_rules():
    assert clear_tokens("Généalogie de Jésus-Christ, fils d’Abraham.") == \
        ["Généalogie", "de", "Jésus-Christ", ",", "fils", "d’", "Abraham", "."]
    assert clear_tokens("c ’ est lui") == ["c’", "est", "lui"]              # our text spaces the apostrophe out
    assert clear_tokens("faites-le-moi savoir ; 12 !") == ["faites-le-moi", "savoir", ";", "12", "!"]
    assert clear_tokens("अब्राहम की सन्तान, दाऊद।")[:4] == ["अबराहम", "की", "सनतान", ","]   # marks stripped like the tokenizer


def test_map_positions_hyphen_apostrophe_punctuation_and_digits():
    text = "Généalogie de Jésus-Christ, fils d’Abraham 4."
    assert tokenize(text) == ["Généalogie", "de", "Jésus", "Christ", "fils", "d", "Abraham"]
    m = map_positions(text)
    #        Généalogie de  Jésus-Christ ,   fils d’  Abraham 4   .
    assert m == [[0],      [1], [2, 3],     [], [4], [5], [6],   [], []]


def test_map_positions_refuses_when_the_tokenizations_disagree(monkeypatch):
    import lexeme_aligner.eval.pos_score as ps
    monkeypatch.setattr(ps, "tokenize", lambda t: ["not", "this", "text"])
    assert map_positions("Généalogie de Jésus") is None


def spine():
    # one verse (ref 1): tokens h0 G1 content, h1 G2 content, h2 G2 content (2nd occurrence), h3 G3 function
    return Spine(key_of={1: {0: ("G1", 0), 1: ("G2", 0), 2: ("G2", 1), 3: ("G3", 0)}},
                 content={1: {("G1", 0), ("G2", 0), ("G2", 1)}},
                 counts={1: collections.Counter({"G1": 1, "G2": 2, "G3": 1})})


def gold():
    gv = GoldVerse(1, links={("G1", 0): {0}, ("G2", 0): {2, 3}, ("G2", 1): {5}, ("G3", 0): {1}})
    gv.claimed = {0, 1, 2, 3, 5}
    return {1: gv}


def test_score_exact_overlap_and_link_counts():
    ours = Ours(spans={1: {("G1", 0): {0}, ("G2", 0): {2}, ("G2", 1): {5, 6}, ("G3", 0): {1}}})
    r = score(gold(), ours, spine(), content_only=True).row()
    assert r["gold_links"] == 3 and r["answered"] == 3                    # G3 is a function word: not judged
    assert r["exact_span"] == 1 and r["overlap"] == 3                      # only G1 exact; G2#1 under-spans, G2#2 over-spans
    assert (r["link_precision"], r["link_recall"]) == (pytest.approx(3 / 4), pytest.approx(3 / 4))   # tp=3 fp=1 fn=1
    assert r["over_claimed"] == 1                                          # position 6: gold never claims it
    r_all = score(gold(), ours, spine(), content_only=False).row()
    assert r_all["gold_links"] == 4 and r_all["exact_span"] == 2


def test_score_skips_ambiguous_occurrences():
    g = gold()
    del g[1].links[("G2", 1)]                                              # gold aligned only one of the two G2s
    r = score(g, Ours(spans={1: {("G1", 0): {0}, ("G2", 0): {2, 3}}}), spine()).row()
    assert r["ambiguous_skipped"] == 1 and r["gold_links"] == 1 and r["exact_span"] == 1


def test_score_missing_answer_counts_as_false_negatives():
    r = score(gold(), Ours(spans={1: {}}), spine()).row()
    assert r["answered"] == 0 and r["link_recall"] == 0 and r["target_unclaimed_rate"] == pytest.approx(1.0)


def test_union_first_wins_and_vetoes_remove_later_claims():
    a = Ours(spans={1: {("G1", 0): {0}}}, vetoed={(1, "G2", 0)})
    b = Ours(spans={1: {("G1", 0): {9}, ("G2", 0): {2}, ("G2", 1): {5}}})
    u = union([a, b])
    assert u.spans[1] == {("G1", 0): {0}, ("G2", 1): {5}}                  # a's G1 kept; a's veto removed b's G2#1


# --- 2026-10-05: gold is keyed by SPINE verse, not by the gold's own (target-numbered) `ref` -----------------
def test_source_verse_reads_only_real_clear_ot_ids():
    assert ps._source_verse("o190030020011") == (19, 3, 2)
    assert ps._source_verse("n40001001001") is None          # Clear NT
    assert ps._source_verse("n19003002004") is None          # SWORD/HELFI-style synthetic id (target verse + seq)


def test_load_gold_keys_by_spine_verse_and_drops_links_from_another_verse(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM
    att = tmp_path / "strongs" / "attestations"
    att.mkdir(parents=True)
    rows = [  # English PSA 3:1 = Hebrew 3:2; English 3:2 = Hebrew 3:3
        {"ref": "19003001", "strong": "H3068", "target_id": "19003001002", "source_id": "o190030020011"},
        {"ref": "19003001", "strong": "H1121", "target_id": "19003001001", "source_id": "o190030010061"},  # superscription word
        {"ref": "19003002", "strong": "H7227", "target_id": "19003002001", "source_id": "n19003002001"},   # synthetic id
    ]
    for r in rows:
        r.update(method="manual", base_text="X")
    pq.write_table(pa.Table.from_pylist(rows), att / "zz.parquet")
    usj = tmp_path / "usj"
    usj.mkdir()
    (usj / f"{_BOOK_FILE_NUM['PSA']}-PSA.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ps, "read_verses", lambda fp: {(3, 1): "O LORD how", (3, 2): "Many say"})
    monkeypatch.setattr(ps, "spine_ref_of_target", lambda books, remap: {19003001: 19003002, 19003002: 19003003})
    remap = lambda b, c, v: (b, c, v - 1) if (b, c) == ("PSA", 3) else (b, c, v)      # spine 3:1 -> title (verse 0)
    gold, st = ps.load_gold("zz", usj, ["PSA"], "X", res_dir=tmp_path, remap=remap)
    assert set(gold) == {19003002, 19003003}                  # spine verses, not the gold's 19003001/19003002
    assert gold[19003002].links == {("H3068", 0): {1}}         # target word 2 of ENGLISH 3:1
    assert gold[19003003].links == {("H7227", 0): {0}}
    assert st["links_other_verse"] == 1                       # the superscription word lives in another spine verse
