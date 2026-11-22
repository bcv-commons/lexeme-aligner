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
