"""compact meta `parts` channel (2026-10-28, option A): part numbers of the edition's pinned analysis, no part text."""
import json

from lexeme_aligner import compact_align as ca, target_parts as tp


def test_encode_parts_maps_to_raw_tokens_and_keeps_whole_tokens_bare():
    raw = [0, 1, 2, 5, 6]                                  # clean token -> raw token (a strip rule moved two tokens)
    assert ca._encode_parts([[4, None], [3, 2], [3, 0]], raw) == "5.0,5.2,6"


def test_encode_parts_none_when_nothing_went_through_a_part():
    assert ca._encode_parts(None, [0]) is None
    assert ca._encode_parts([[0, None], [1, None]], [0, 1]) is None
    assert ca._encode_parts([[7, 1]], [0, 1]) is None      # a token that does not map to raw text


def test_parts_is_a_sidecar_channel():
    assert "parts" in ca.SIDECAR_CHANNELS


def test_parts_pin_leaves_out_a_local_file(tmp_path):
    cfg = tmp_path / "target_parts.json"
    cfg.write_text(json.dumps({
        "hub": {"dataset": "d", "revision": "v1", "file": "f.parquet", "sha256": "abc", "min_p_split": 0.9},
        "loc": {"chain": False, "format": "bcv-split", "local": "x/words.parquet", "sha256": "def",
                "segmenter": {"revision": "r"}, "numbering": "list index"}}))
    assert tp.parts_pin("hub", cfg) == {"dataset": "d", "revision": "v1", "file": "f.parquet", "sha256": "abc"}
    assert tp.parts_pin("loc", cfg) == {"format": "bcv-split", "segmenter": {"revision": "r"}, "numbering": "list index"}
    assert tp.parts_pin("none", cfg) is None
    assert tp.for_edition("loc", cfg) is None              # chain: false -> the chain keeps whole words


def test_marked_edition_inserts_the_mark_after_the_iso():
    import pytest
    assert ca.marked_edition("kvg_wbt", "kvg", "m") == "kvg-m_wbt"
    assert ca.marked_edition("eng_BSB", "eng", "m") == "eng-m_BSB"
    assert ca.marked_edition("heb", "heb", "m") == "heb-m"
    with pytest.raises(ValueError):
        ca.marked_edition("kvg_wbt", "kvg", "q")
    with pytest.raises(ValueError):
        ca.marked_edition("BSB", "eng", "m")
