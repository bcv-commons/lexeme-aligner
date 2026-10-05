"""compact-alignments additions of 2026-10-05: the `rend` channel and the published verse map."""
import json

import lexeme_aligner.compact_align as ca
import lexeme_aligner.versification as vf


def test_rend_ids_count_distinct_renderings_per_lexeme_by_first_appearance():
    state: dict = {}
    toks = ["The", "Spirit", "of", "God", "the", "spirit", "wind"]
    assert ca.rend_id(state, "hbo:7307", toks, [1]) == 1            # "spirit"
    assert ca.rend_id(state, "hbo:7307", toks, [6]) == 2            # "wind"
    assert ca.rend_id(state, "hbo:7307", toks, [5]) == 1            # "spirit" again: case-folded, same id
    assert ca.rend_id(state, "hbo:0430", toks, [3]) == 1            # numbering is per lexeme
    assert ca.rend_id(state, "hbo:7307", toks, [4, 5]) == 3         # a two-word rendering is its own rendering


def test_verse_map_english_numbering():
    verses = [("PSA", 3, 1), ("PSA", 3, 2), ("GEN", 1, 1), ("MAL", 3, 19)]
    m = vf.verse_map("eng", verses)
    assert m == {"PSA 3:1": "PSA 3:title", "PSA 3:2": "PSA 3:1", "MAL 3:19": "MAL 4:1"}   # GEN 1:1 identical: absent


def test_verse_map_is_empty_for_hebrew_numbering():
    assert vf.verse_map("org", [("PSA", 3, 1), ("MAL", 3, 19)]) == {}


def test_write_verse_map_writes_once_and_keeps_an_unchanged_file(tmp_path, monkeypatch):
    class Heb:
        def chapters(self, b):
            return [3] if b == "PSA" else []

        def verses(self, b, ch):
            return [1, 2]
    fp = ca.write_verse_map(Heb(), "eng", tmp_path)
    doc = json.loads(fp.read_text(encoding="utf-8"))
    assert fp.name == "_versification_eng.json" and doc["scheme"] == "eng"
    assert doc["map"] == {"PSA 3:1": "PSA 3:title", "PSA 3:2": "PSA 3:1"}
    mtime = fp.stat().st_mtime_ns
    ca.write_verse_map(Heb(), "eng", tmp_path)
    assert fp.stat().st_mtime_ns == mtime                              # not rewritten when unchanged


def test_rend_is_a_sidecar_channel():
    assert "rend" in ca.SIDECAR_CHANNELS


# --- 2026-10-06: stopwords dropped before numbering (bcv-query's measured recommendation) ----------------------
def test_stopwords_make_related_renderings_share_one_id():
    stop = frozenset({"the", "of", "his"})
    toks = ["the", "spirit", "his", "spirit", "spirit"]
    state: dict = {}
    a = ca.rend_id(state, "hbo:7307", toks, [0, 1], stop)          # "the spirit"  -> "spirit"
    b = ca.rend_id(state, "hbo:7307", toks, [4], stop)             # "spirit"
    c = ca.rend_id(state, "hbo:7307", toks, [2, 3], stop)          # "his spirit"  -> "spirit"
    assert a == b == c == 1


def test_a_rendering_made_only_of_stopwords_keeps_its_original_words():
    stop = frozenset({"the", "of"})
    toks = ["of", "the", "wind"]
    state: dict = {}
    assert ca.rend_id(state, "hbo:1", toks, [2], stop) == 1
    assert ca.rend_id(state, "hbo:1", toks, [0, 1], stop) == 2      # "of the": every word is a stopword -> kept as is
    assert ca.rend_id(state, "hbo:1", toks, [0, 1], stop) == 2      # and stays stable


def test_stopword_match_is_exact_and_case_folded():
    stop = frozenset({"the"})
    toks = ["The", "theory", "wind"]
    state: dict = {}
    assert ca.rend_id(state, "hbo:1", toks, [0, 2], stop) == ca.rend_id(state, "hbo:1", toks, [2], stop)   # "The" dropped
    assert ca.rend_id(state, "hbo:1", toks, [1, 2], stop) == 2                                             # "theory" is not "the"


def test_load_rend_stopwords_reads_the_published_list_and_hashes_it(tmp_path):
    (tmp_path / "zz.txt").write_text("The\nof\n\n  His \n", encoding="utf-8")
    words, sha = ca.load_rend_stopwords("zz", tmp_path)
    assert words == frozenset({"the", "of", "his"})
    import hashlib
    assert sha == hashlib.sha256((tmp_path / "zz.txt").read_bytes()).hexdigest()
    assert ca.load_rend_stopwords("nolist", tmp_path) == (frozenset(), None)        # no list: nothing is dropped
