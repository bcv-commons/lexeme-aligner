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
