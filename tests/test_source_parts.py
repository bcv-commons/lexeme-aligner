"""source_parts: the parts spine keeps the word spine's content units and adds a word's other parts as function units."""
import sqlite3

import pytest

from lexeme_aligner import source_parts
from lexeme_aligner.hebrew_source import HebrewSource

COLS = ("book TEXT NOT NULL, chapter INTEGER NOT NULL, verse INTEGER NOT NULL, idx INTEGER NOT NULL, key TEXT NOT NULL, "
        "surface TEXT NOT NULL, lexeme TEXT NOT NULL, strong INTEGER, lemma TEXT, is_content INTEGER NOT NULL, morph TEXT, gloss TEXT, "
        "role TEXT, stem TEXT, person TEXT, number TEXT, gender TEXT, case_ TEXT, tense TEXT, voice TEXT, mood TEXT, degree TEXT, "
        "state TEXT, is_superscription INTEGER NOT NULL DEFAULT 0, head_idx INTEGER, phrase_role TEXT, construct_group TEXT, "
        "construct_role TEXT, PRIMARY KEY (book, chapter, verse, idx)")
# MAT 1:1 Βίβλος γενέσεως | RUT 1:16 two adjacent same-lexeme verbs (merged in the word spine), the first one cut
ROWS = [("MAT", 1, 1, 0, "40001001001", "Βίβλος", "grc:976", 976, "βίβλος", 1, "N-NSF", "book", None, 0),
        ("MAT", 1, 1, 1, "40001001002", "γενέσεως", "grc:1078", 1078, "γένεσις", 1, "N-GSF", "origin", 0, 0),
        ("RUT", 1, 16, 0, "080010160121", "תֵּלְכִ֗י", "hbo:1980", 1980, "הלך", 1, "Vqi2fs", "go", None, 0),
        ("RUT", 1, 16, 1, "080010160131", "אֵלֵ֔ךְ", "hbo:1980", 1980, "הלך", 1, "Vqi1cs", "go", None, 0)]
GREEK = ["part_id\tword_id\tn\tstart\tend\ttext\tkind\tfeatures",
         "400010010011\t40001001001\t1\t0\t4\tΒίβλ\tstem\t", "400010010012\t40001001001\t2\t4\t6\tος\tending\tN-NSF",
         "400010010021\t40001001002\t1\t0\t5\tγενέσ\tstem\t", "400010010022\t40001001002\t2\t5\t8\tεως\tending\tN-GSF"]
HEBREW = ["part_id\tmacula_id\tword_id\tn\tstart\tend\ttext\tkind\tfeatures",
          "0800101601211\t080010160121\t08001016012\t1\t0\t1\tתֵּ\tprefix\tVqi2fs", "0800101601212\t080010160121\t08001016012\t2\t1\t3\tלְכִ֗\tstem\t",
          "0800101601213\t080010160121\t08001016012\t3\t3\t4\tי\tending\tVqi2fs",
          "0800101601311\t080010160131\t08001016013\t1\t0\t4\tאֵלֵ֔ךְ\tstem\t"]


@pytest.fixture
def built(tmp_path):
    spine = tmp_path / "spine.db"
    c = sqlite3.connect(spine)
    c.execute(f"CREATE TABLE spine_words ({COLS})")
    c.execute("CREATE TABLE spine_meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT INTO spine_meta VALUES ('spine_content_sha256', 'abc')")
    c.executemany("INSERT INTO spine_words (book, chapter, verse, idx, key, surface, lexeme, strong, lemma, is_content, morph, gloss, "
                  "head_idx, is_superscription) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ROWS)
    c.commit()
    c.close()
    parts = tmp_path / "parts"
    (parts / "greek").mkdir(parents=True)
    (parts / "hebrew").mkdir()
    (parts / "greek/parts.tsv").write_text("\n".join(GREEK) + "\n", encoding="utf-8")
    (parts / "hebrew/parts.tsv").write_text("\n".join(HEBREW) + "\n", encoding="utf-8")
    out = tmp_path / "parts.db"
    rep = source_parts.build(spine, out, parts)
    return spine, out, rep


def test_counts_and_anchors(built):
    _spine, out, rep = built
    assert rep["rows_out"] == 8 and rep["anchors"] == 4
    c = sqlite3.connect(out)
    anchors = dict(c.execute("SELECT lexeme, strong FROM spine_part_anchors"))
    assert sorted(anchors) == ["grc:~ending:N-GSF", "grc:~ending:N-NSF", "hbo:~ending:Vqi2fs", "hbo:~prefix:Vqi2fs"]
    assert min(anchors.values()) == source_parts.ANCHOR_BASE
    meta = dict(c.execute("SELECT key, value FROM spine_meta"))
    assert "spine_content_sha256" not in meta and meta["source_parts_base_content_sha256"] == "abc"


def test_greek_word_becomes_stem_plus_ending(built):
    _spine, out, _ = built
    toks = HebrewSource(out).verse_tokens("MAT", 1, 1)
    assert [(t.surface, t.is_content, t.keys) for t in toks] == [
        ("Βίβλ", True, ["40001001001"]), ("ος", False, ["40001001001.2"]),
        ("γενέσ", True, ["40001001002"]), ("εως", False, ["40001001002.2"])]
    assert toks[0].lexeme == "grc:976" and toks[1].lexeme == "grc:~ending:N-NSF" and toks[1].strong.startswith("G1")
    assert toks[2].head_idx == 0                          # head_idx 0 (Βίβλος) -> its stem, still idx 0


def test_content_units_match_word_spine_incl_merge(built):
    spine, out, _ = built
    for ref in (("MAT", 1, 1), ("RUT", 1, 16)):
        word = [(t.lexeme, t.keys) for t in HebrewSource(spine).verse_tokens(*ref) if t.is_content]
        part = [(t.lexeme, t.keys) for t in HebrewSource(out).verse_tokens(*ref) if t.is_content]
        assert word == part
    rut = HebrewSource(out).verse_tokens("RUT", 1, 16)    # the ending of the first verb does not block the merge
    assert [t.surface for t in rut] == ["תֵּ", "לְכִ֗ אֵלֵ֔ךְ", "י"]


def test_part_blocked_gives_up_other_words_positions():
    from types import SimpleNamespace as N
    from lexeme_aligner.run_pilot import part_blocked
    heb = [N(idx=0, keys=["40001001001"]), N(idx=1, keys=["40001001001.2"]), N(idx=2, keys=["40001001002"])]
    by_h = {0: N(t_idx=[3]), 1: N(t_idx=[3, 5, 7]), 2: N(t_idx=[7])}
    assert part_blocked(heb, by_h) == {1: {7}}            # 3 is its own word's, 5 is free, 7 belongs to the next word
    assert part_blocked([heb[0], heb[2]], {0: by_h[0], 2: by_h[2]}) == {}


def test_jev_request_names_source_parts(built):
    _spine, out, _ = built
    from lexeme_aligner.eval import jev_check as jc, llm_edit as le
    toks = HebrewSource(out).verse_tokens("MAT", 1, 1)
    p = le.Proposal(40001001, "MAT", 1, 1, toks, ["The", "book", "of", "the", "genealogy"])
    src = jc.build_request(p, {}, "eng", "", "v4")["state"]["source_words"]
    assert src[1]["part"] == "ending" and src[1]["part_of"] == "h0" and src[1]["strong"] is None
    assert src[0]["written_word"] == src[1]["written_word"] == "Βίβλος" and "part" not in src[0]
    assert jc._src_label(p, toks[1], "") == "the ending ος of h0, written word Βίβλος"
    assert jc._src_label(p, toks[0], "") == "the stem Βίβλ of the written word Βίβλος"
    word = jc.build_request(le.Proposal(40001001, "MAT", 1, 1, HebrewSource(_spine).verse_tokens("MAT", 1, 1), ["x"]), {}, "eng", "", "v4")
    assert all("part" not in s and "written_word" not in s for s in word["state"]["source_words"])


def test_hybrid_matches_takes_words_from_the_word_run():
    from types import SimpleNamespace as NS
    from lexeme_aligner.eflomal_align import EMatch
    from lexeme_aligner.run_pilot import hybrid_matches
    tok = lambda i, k: NS(idx=i, keys=[k])
    parts = NS(heb=[tok(0, "w1"), tok(1, "w1.2"), tok(2, "w2"), tok(3, "w3")])
    word = NS(heb=[tok(0, "w1"), tok(1, "w2"), tok(2, "w3")])
    pm = [EMatch(0, [9], .6), EMatch(1, [4], .6), EMatch(2, [5], .6), EMatch(3, [6], .9)]
    wm = [EMatch(0, [1], .9), EMatch(1, [2], .6)]                 # w3 unaligned in the word run
    out = {m.h_idx: m.t_idx for m in hybrid_matches(parts, pm, word, wm)}
    assert out == {0: [1], 1: [4], 2: [2]}                        # words from the word run, the part from the parts run, w3 none



def test_parts_fold_record_word_view():
    from types import SimpleNamespace as NS
    from lexeme_aligner.parts_fold import fold_record
    P = [NS(idx=0, keys=["w1"]), NS(idx=1, keys=["w1.2"]), NS(idx=2, keys=["w2"]), NS(idx=3, keys=["w3"]), NS(idx=4, keys=["w3.2"])]
    W = [NS(idx=0, keys=["w1"], surface="ab", lexeme="x:1", strong="G1", lemma="a", is_content=1),
         NS(idx=1, keys=["w2"], surface="c", lexeme="x:2", strong="G2", lemma="c", is_content=0),
         NS(idx=2, keys=["w3"], surface="de", lexeme="x:3", strong="G3", lemma="d", is_content=1)]
    pair = lambda h, t: {"h_idx": h, "t_idx": t, "surface": "?"}
    rec = {"ref": 1, "pairs": [pair(0, [1]), pair(1, [2, 5]), pair(2, [5]), pair(4, [7])],
           "llm_skipped": [{"h_idx": 1, "status": "unrepresented"}, {"h_idx": 2, "status": "rejected"}], "llm_none": [3]}
    out = fold_record(rec, P, W, rule="fold")
    got = {p["h_idx"]: p["t_idx"] for p in out["pairs"]}
    assert got == {0: [1, 2], 1: [5], 2: [7]}               # w1 + its part (5 is w2's), w2 re-indexed, w3 from its part only
    stem = fold_record(rec, P, W)                           # default rule "stem": carrying parts only
    assert {p["h_idx"]: p["t_idx"] for p in stem["pairs"]} == {0: [1], 1: [5]}        # w3's only link is its ending's: no pair
    assert out["pairs"][0]["surface"] == "ab" and out["pairs"][2]["lexeme"] == "x:3"
    assert out["llm_skipped"] == [{"h_idx": 1, "status": "rejected"}] and out["llm_none"] == [3]


def test_align_text_plan_on_source_parts_folds_before_compact(tmp_path):
    from pathlib import Path
    from lexeme_aligner.align_text import plan
    steps = plan(Path("/t/usj"), "eng_x", "eng", tmp_path, "--nt", False, None, None, None, Path("/s/word.db"))
    names = [s[0] for s in steps]
    assert names[-3:] == ["parts-fold", "part-alignments", "compact"]
    compact = steps[-1]
    assert compact[2] == {"ALIGNER_SPINE_DB": "/s/word.db"} and str(tmp_path / "words") in compact[1]
    assert compact[1][compact[1].index("--from-parts") + 1] == str(tmp_path / "part-alignments")    # compact = fold(part-alignments)
    assert "parts-fold" not in [s[0] for s in plan(Path("/t/usj"), "eng_x", "eng", tmp_path, "--nt", False, None)]


def test_parts_fold_carries_target_parts():
    """A target-parts edition (heb_scg) on source parts: the folded word keeps the target-part links of the positions it takes."""
    from types import SimpleNamespace as NS
    from lexeme_aligner.parts_fold import fold_record
    P = [NS(idx=0, keys=["w1"]), NS(idx=1, keys=["w1.2"]), NS(idx=2, keys=["w2"]), NS(idx=3, keys=["w2.2"])]
    W = [NS(idx=0, keys=["w1"], surface="ab", lexeme="x:1", strong="G1", lemma="a", is_content=1),
         NS(idx=1, keys=["w2"], surface="cd", lexeme="x:2", strong="G2", lemma="c", is_content=1)]
    rec = {"ref": 1, "pairs": [{"h_idx": 0, "t_idx": [1], "t_parts": [[1, 2]]}, {"h_idx": 1, "t_idx": [1, 4], "t_parts": [[1, 1], [4, 0]]},
                               {"h_idx": 3, "t_idx": [6], "t_parts": [[6, 1]]}]}
    out = {p["h_idx"]: p for p in fold_record(rec, P, W, rule="fold")["pairs"]}
    assert out[0]["t_idx"] == [1, 4] and out[0]["t_parts"] == [[1, 1], [1, 2], [4, 0]]
    assert out[1]["t_idx"] == [6] and out[1]["t_parts"] == [[6, 1]]
