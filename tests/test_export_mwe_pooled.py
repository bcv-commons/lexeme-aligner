"""aligned_mwe pools EVERY edition of a language (rows tagged by base_text); no edition is privileged."""
import json

import pytest

from lexeme_aligner import export_mwe as mwe


def _write(out, method, tag, book, pairs):
    (out / f"align_{method}_{tag}_{book}.jsonl").write_text(
        json.dumps({"book": book, "chapter": 1, "verse": 1, "pairs": pairs}) + "\n", encoding="utf-8")


def _pair(lexeme, strong, target, t_idx):
    return {"content": True, "lexeme": lexeme, "strong": strong, "target": target, "t_idx": t_idx}


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(mwe, "spine_corpus", lambda: "WLC")
    # edition A (NT-only in real life would be the "first"): one contiguous phrase twice; edition B: another once
    _write(tmp_path, "eflomal", "aaa", "MAT", [_pair("grc:1", "G1", "kasih setia", [3, 4]),
                                               _pair("grc:1", "G1", "kasih setia", [7, 8])])
    _write(tmp_path, "eflomal", "bbb", "GEN", [_pair("hbo:2", "H2", "ibu mertua", [1, 2]),
                                               _pair("hbo:2", "H2", "ibu  mertua", [1, 3])])   # scattered: dropped
    return tmp_path


def test_aggregate_folds_every_edition_with_its_base_text(out):
    counts, per_lex, n_files, seen, scattered, methods = mwe.aggregate(out, [("aaa", "AAA"), ("bbb", "BBB")])
    assert counts[("AAA", "grc:1", "G1", "kasih setia", 2)] == 2
    assert counts[("BBB", "hbo:2", "H2", "ibu mertua", 2)] == 1
    assert n_files == 2 and scattered == 1 and methods == "eflomal"


def test_share_is_within_one_edition(out):
    _write(out, "eflomal", "bbb", "EXO", [_pair("grc:1", "G1", "cinta kasih", [1, 2])])
    counts, per_lex, *_ = mwe.aggregate(out, [("aaa", "AAA"), ("bbb", "BBB")])
    rows = mwe.build_rows(counts, per_lex, 1)
    by = {(r[5], r[2]): r for r in rows}
    assert by[("AAA", "kasih setia")][7] == 1.0          # AAA's only phrase for grc:1
    assert by[("BBB", "cinta kasih")][7] == 1.0          # BBB's own denominator, not AAA's 2 + 1


def test_a_single_tag_string_still_works(out):
    counts, *_ = mwe.aggregate(out, "aaa")
    assert {k[0] for k in counts} == {"aaa"}


def test_entry_lists_every_base_text_and_its_source(out):
    counts, per_lex, n_files, seen, scattered, methods = mwe.aggregate(out, [("aaa", "AAA"), ("bbb", "BBB")])
    rows = mwe.build_rows(counts, per_lex, 1)
    entry = mwe.build_entry(rows, methods, 1, n_files, "Test", "iso=xx/data.parquet", scattered,
                            {"AAA": {"provider": "p"}, "BBB": {"provider": "q"}})
    assert entry["base_texts"] == ["AAA", "BBB"] and set(entry["by_base_text"]) == {"AAA", "BBB"}
    assert entry["sources"]["BBB"]["provider"] == "q"
    assert mwe.SCHEMA.index("base_text") == len(mwe.SCHEMA) - 4


def test_parquet_carries_the_base_text_column(out, tmp_path):
    pa = pytest.importorskip("pyarrow.parquet")
    counts, per_lex, *_ = mwe.aggregate(out, [("aaa", "AAA"), ("bbb", "BBB")])
    rows = mwe.build_rows(counts, per_lex, 1)
    dest = tmp_path / "data.parquet"
    mwe.write_parquet(rows, dest)
    table = pa.read_table(dest)
    assert table.column_names == mwe.SCHEMA
    assert set(table.column("base_text").to_pylist()) == {"AAA", "BBB"}


def test_an_edition_without_alignments_is_skipped_not_fatal(out):
    counts, *_ = mwe.aggregate(out, [("aaa", "AAA"), ("ghost", "GHOST")])
    assert {k[0] for k in counts} == {"AAA"}
    with pytest.raises(SystemExit):
        mwe.aggregate(out, [("ghost", "GHOST")])
