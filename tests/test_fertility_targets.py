"""R1 (2026-09-27): per-lexeme fertility targets from gold parquets."""
import lexeme_aligner.fertility_targets as ft


def _row(strong, surface, ref, sid, method="manual", base_text="X"):
    return {"strong": strong, "surface": surface, "ref": ref, "source_id": sid, "method": method,
            "base_text": base_text}


def test_per_source_fertility_counts_letter_rows_per_source_token():
    rows = [_row("H2617", "loving", "1", "s1"), _row("H2617", "kindness", "1", "s1"),
            _row("H2617", ",", "1", "s1"),                      # punctuation-only row ignored
            _row("H2617", "mercy", "2", "s2"),
            _row("H0430", "God", "1", "s3")]
    f = ft.per_source_fertility(rows)
    assert sorted(f[("X", "H2617")]) == [1, 2]
    assert f[("X", "H0430")] == [1]


def test_build_targets_aggregates_across_languages(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    d = tmp_path / "strongs" / "attestations"
    d.mkdir(parents=True)
    cols = ["strong", "surface", "ref", "source_id", "method", "base_text"]

    def write(iso, rows):
        pq.write_table(pa.table({c: pa.array([r[c] for r in rows], pa.string()) for c in cols}), d / f"{iso}.parquet")

    # eng: H2617 always 2 words (3 occurrences); fra: H2617 1 word (3 occurrences); H0430 single everywhere
    write("eng", [*(r for i in range(3) for r in (_row("H2617", "loving", str(i), f"s{i}", base_text="BSB"),
                                                   _row("H2617", "kindness", str(i), f"s{i}", base_text="BSB"))),
                  *(_row("H0430", "God", str(i), f"g{i}", base_text="BSB") for i in range(3))])
    write("fra", [*(_row("H2617", "bonté", str(i), f"s{i}", base_text="LSG") for i in range(3)),
                  *(_row("H0430", "Dieu", str(i), f"g{i}", base_text="LSG") for i in range(3))])
    t = ft.build_targets(tmp_path, min_occurrences=3)
    assert t["H2617"]["langs"] == 2 and t["H2617"]["multi_langs"] == 1
    assert abs(t["H2617"]["mean"] - 1.5) < 1e-6 and t["H2617"]["f"] == 2
    assert t["H0430"]["multi_langs"] == 0 and t["H0430"]["f"] == 1


def test_build_targets_min_occurrences_gate(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    d = tmp_path / "strongs" / "attestations"
    d.mkdir(parents=True)
    cols = ["strong", "surface", "ref", "source_id", "method", "base_text"]
    rows = [_row("H1", "a", "1", "s1"), _row("H1", "b", "1", "s1")]      # one occurrence only
    pq.write_table(pa.table({c: pa.array([r[c] for r in rows], pa.string()) for c in cols}), d / "xx.parquet")
    assert ft.build_targets(tmp_path, min_occurrences=3) == {}


def test_load_targets_missing_file_is_empty(tmp_path):
    assert ft.load_targets(tmp_path / "nope.json") == {}
