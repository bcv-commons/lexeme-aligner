"""ubs_senses.py: binding UBS sense-tagged references to spine tokens (synthetic verses)."""
import json
import sqlite3

import lexeme_aligner.ubs_senses as us


def _tok(idx, strong, lemma):
    return {"idx": idx, "strong": strong, "lemma_c": us.cons(lemma)}


ENTRY = {"M1": {"strongs": {100}, "lemmas": {us.cons("אב")}}, "M2": {"strongs": {200}, "lemmas": {us.cons("בן")}},
         "M3": {"strongs": {999}, "lemmas": {us.cons("בית")}}}


def test_cons_strips_points_and_keeps_letters():
    assert us.cons("אָב֙") == "אב" and us.cons("הַ-") == "ה" and us.cons(None) == ""


def test_a_unique_candidate_binds_as_unique():
    toks = [_tok(0, 1, "x"), _tok(1, 100, "אב"), _tok(2, 2, "y")]
    bound, stats = us.bind_verse([(2, "M1", "S1")], toks, ENTRY)
    assert bound == {1: ("S1", "M1", "unique")} and stats["unique"] == 1


def test_lemma_matches_when_the_strongs_number_does_not():
    toks = [_tok(0, 5, "בֵּית"), _tok(1, 6, "z")]
    bound, _ = us.bind_verse([(1, "M3", "S3")], toks, ENTRY)
    assert bound == {0: ("S3", "M3", "unique")}


def test_an_ambiguous_reference_is_settled_by_the_offsets_of_its_neighbouring_anchors():
    # slot n=2 (idx 1 expected +offset); the word אב occurs at idx 1 and idx 5. Anchors: n=1 -> idx 0 (offset 0) and
    # n=5 -> idx 8 (offset 4, i.e. suffix tokens crept in). For n=4 the allowed offset is [0,4]: idx 5-(4-1)=2 fits,
    # idx 1-(4-1)=-2 does not, so the later occurrence is chosen.
    toks = [_tok(0, 200, "בן"), _tok(1, 100, "אב"), _tok(2, 7, "a"), _tok(3, 8, "b"), _tok(4, 9, "c"),
            _tok(5, 100, "אב"), _tok(6, 10, "d"), _tok(7, 11, "e"), _tok(8, 999, "בית")]
    refs = [(1, "M2", "SB"), (4, "M1", "SA"), (5, "M3", "SH")]
    bound, stats = us.bind_verse(refs, toks, ENTRY)
    assert bound[0] == ("SB", "M2", "unique") and bound[8] == ("SH", "M3", "unique")
    assert bound[5] == ("SA", "M1", "anchored") and 1 not in bound


def test_anchors_that_break_the_monotone_offset_are_dropped_and_counted():
    # second anchor implies offset 0 after a first implying offset 5: impossible (offsets never decrease)
    toks = [_tok(6, 200, "בן"), _tok(1, 999, "בית")]
    toks.sort(key=lambda t: t["idx"])
    refs = [(1, "M2", "SB"), (2, "M3", "SH")]                 # n=1 -> idx 6 (offset 6); n=2 -> idx 1 (offset 0)
    bound, stats = us.bind_verse(refs, toks, ENTRY)
    assert stats["anchors_dropped_nonmonotone"] == 1


def test_lnds_keeps_a_longest_non_decreasing_run():
    assert us._lnds([0, 0, 3, 1, 1, 2]) == [0, 1, 3, 4, 5] or len(us._lnds([0, 0, 3, 1, 1, 2])) == 5
    assert us._lnds([]) == []


def test_load_token_senses_is_empty_without_a_database(tmp_path):
    assert us.load_token_senses(tmp_path / "missing.db") == {}


def test_write_and_load_round_trip(tmp_path, monkeypatch):
    ubs = tmp_path / "u.json"
    ubs.write_text("[]", encoding="utf-8")
    spine = tmp_path / "spine.db"
    con = sqlite3.connect(spine)
    con.execute("CREATE TABLE spine_meta (key TEXT, value TEXT)")
    con.execute("INSERT INTO spine_meta VALUES ('macula_spine_sha256','abc')")
    con.commit()
    con.close()
    monkeypatch.setattr(us, "SPINE_DB", spine)
    dest = tmp_path / "ubs.db"
    us.write_db(dest, {"S1": {"main_id": "M1", "lemma": "אב", "strongs": [100], "gloss": "father", "definition": "d",
                              "domains": ["1"]}},
                {("GEN", 1, 1, 3): ("S1", "M1", "unique", "hbo:0001"), ("GEN", 1, 1, 4): ("S1", "M1", "nearest", "hbo:0001")}, ubs)
    assert us.load_token_senses(dest) == {("GEN", 1, 1, 3): "S1", ("GEN", 1, 1, 4): "S1"}
    assert us.load_token_senses(dest, min_how=("unique",)) == {("GEN", 1, 1, 3): "S1"}
    assert us.load_token_senses(dest, with_lexeme=True)[("GEN", 1, 1, 4)] == ("S1", "hbo:0001")
    meta = dict(sqlite3.connect(dest).execute("SELECT key,value FROM ubs_meta").fetchall())
    assert meta["license"] == "CC-BY-SA-4.0" and meta["spine_sha256"] == "abc"
