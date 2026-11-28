"""align-text's pooled priors (pool_priors.py, 2026-11-16) and its numbering choice for a text with gaps."""
import json
from types import SimpleNamespace as NS

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lexeme_aligner import align_text, pool_priors, versification as vf


def _part(fp, rows):
    fp.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([dict(source_corpus="x", hi_conf=0.0, **r) for r in rows]), fp)
    return fp


ROWS = [dict(surface="lord", lexeme="hbo:3068", method="eflomal", base_text="A", count=90),
        dict(surface="god", lexeme="hbo:3068", method="eflomal", base_text="A", count=10),
        dict(surface="lord", lexeme="hbo:3068", method="gloss", base_text="A", count=500),
        dict(surface="yahweh", lexeme="hbo:3068", method="eflomal", base_text="SELF", count=1000),
        dict(surface="the lord", lexeme="hbo:3068", method="eflomal", base_text="A", count=50)]


def _recs(*toks):
    return [NS(heb=[NS(lexeme=lx, strong=s, is_content=c) for lx, s, c in toks])]


def test_pairs_take_eflomal_single_words_leave_out_the_excluded_edition(tmp_path):
    fp = _part(tmp_path / "iso=eng" / "data.parquet", ROWS)
    got = {(s, w): round(a, 3) for s, w, a in pool_priors.pairs(_recs(("hbo:3068", "H3068", True), ("hbo:0001", "H0001", True)),
                                                                fp, exclude=["self"])}
    # lord 90/100, god 10/100 (>= min_share 0.1): alpha = weight .5 x share x scale 2; gloss rows and the two-word surface ignored
    assert got == {("H3068", "lord"): 0.9, ("H3068", "god"): 0.1}


def test_pairs_skip_function_tokens(tmp_path):
    fp = _part(tmp_path / "iso=eng" / "data.parquet", ROWS)
    assert pool_priors.pairs(_recs(("hbo:3068", "H3068", False)), fp) == []


def test_merge_scales_the_pool_to_the_texts_own_total(tmp_path):
    pool = _part(tmp_path / "pool" / "data.parquet", ROWS)
    own = _part(tmp_path / "own" / "iso=eng" / "data.parquet",
                [dict(surface="jehovah", lexeme="hbo:3068", method="eflomal", base_text="mine", count=20)])
    rep = pool_priors.merge(tmp_path / "own", "eng", pool, tmp_path / "merged", exclude=["SELF"])
    assert rep["pool_editions"] == 1 and rep["own_count"] == 20 and rep["pool_count"] == 150
    rows = pq.read_table(tmp_path / "merged" / "iso=eng" / "data.parquet").to_pylist()
    assert sum(r["count"] for r in rows if r["base_text"] == "A") == pytest.approx(20)
    assert {r["base_text"] for r in rows} == {"mine", "A"} and all(r["method"] == "eflomal" for r in rows)


def test_merge_without_an_own_export_keeps_the_pool_counts(tmp_path):
    pool = _part(tmp_path / "pool" / "data.parquet", ROWS)
    rep = pool_priors.merge(tmp_path / "none", "eng", pool, tmp_path / "merged")
    assert rep["own_rows"] == 0 and rep["pool_scale"] == 1.0


def test_plan_with_pool_adds_priors_merge_and_points_gloss_at_the_merged_root(tmp_path):
    pool = {"path": tmp_path / "p.parquet", "exclude": ["BSB"]}
    steps = align_text.plan(tmp_path / "usj", "eng_film", "eng", tmp_path / "o", "--ot", False, None, pool)
    names = [s[0] for s in steps]
    assert names == ["eflomal", "export", "pool-merge", "gloss", "spanext", "gapfill", "residual", "function-rule", "compact"]
    d = {s[0]: s for s in steps}
    ef = d["eflomal"][1]
    assert ef[ef.index("--pool-priors") + 1] == str(tmp_path / "p.parquet") and ef[ef.index("--pool-exclude") + 1] == "BSB"
    assert d["gloss"][2] == {"ALIGNER_LEX_ROOT": str(tmp_path / "o" / align_text.POOL_ROOT)}
    assert "--pool-priors" not in d["gloss"][1]
    bridged = align_text.plan(tmp_path / "usj", "x", "eng", tmp_path / "o", "--all", True, None, pool)
    assert all("--pool-priors" in s[1] for s in bridged if s[0].startswith("eflomal"))


def test_plan_without_pool_is_unchanged(tmp_path):
    assert align_text.plan(tmp_path, "x", "eng", tmp_path / "o", "--nt", False, None) == \
        align_text.plan(tmp_path, "x", "eng", tmp_path / "o", "--nt", False, None, None)


def test_resolve_pool_auto_missing_is_recorded_not_an_error(tmp_path):
    pool, rec = align_text.resolve_pool("auto", "zzz", "zzz_x", tmp_path)
    assert pool is None and rec["used"] is False and "zzz" in rec["why"]
    assert align_text.resolve_pool(None, "eng", "x", tmp_path) == (None, {"used": False, "why": "--no-pool"})


def test_resolve_pool_leaves_out_the_edition_itself(tmp_path):
    _part(tmp_path / "lexeme-alignments" / "iso=eng" / "data.parquet", ROWS + [dict(ROWS[0], base_text="BSB")])
    pool, rec = align_text.resolve_pool("auto", "eng", "eng_BSB", tmp_path, exclude="SELF")
    assert pool["exclude"] == ["BSB", "SELF"] and rec["editions"] == 1 and rec["sha256"]


def _usj(d, book, chapters):
    from lexeme_aligner.text_input import book_usj
    d.mkdir(parents=True, exist_ok=True)
    (d / f"01-{book}.json").write_text(json.dumps(book_usj(book, {c: [(str(v), "w") for v in vs] for c, vs in chapters.items()})))
    return d


def test_complete_share_and_the_choice_for_a_text_with_gaps(tmp_path):
    full = _usj(tmp_path / "full", "GEN", {1: range(1, 32), 2: range(1, 26)})
    gaps = _usj(tmp_path / "gaps", "GEN", {1: [3, 7, 20], 2: [1, 2, 3], 3: [5]})
    assert vf.complete_share(str(full)) == 1.0
    assert vf.complete_share(str(gaps)) == pytest.approx(1 / 3)
    rec = align_text.versification_choice(None, gaps, warn=open("/dev/null", "w"))
    assert rec["forced"] == "eng" and rec["how"].startswith("default")
    assert "forced" not in align_text.versification_choice(None, full)
    assert align_text.versification_choice("org", gaps)["forced"] == "org"
    with pytest.raises(SystemExit):
        align_text.versification_choice("klingon", gaps)


def test_forced_scheme_overrides_detection(tmp_path, monkeypatch):
    gaps = _usj(tmp_path / "gaps", "GEN", {1: [3]})
    monkeypatch.setenv("ALIGNER_VERSIFICATION", "org")
    assert vf.scheme_of("x", str(gaps)) == "hebrew" and vf.edition_scheme("x", str(gaps)) == "org"
    monkeypatch.setenv("ALIGNER_VERSIFICATION", "protestant")
    assert vf.edition_scheme("x", str(gaps)) == "eng"
