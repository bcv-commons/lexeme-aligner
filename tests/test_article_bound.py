"""article_bound.py: the derived 'is the definite article fused into the noun?' fact, and its three consumers
(span_extension gate, gram_struct derived partition, decisions ledger)."""
import gzip
import json

import pytest

import lexeme_aligner.article_bound as ab
import lexeme_aligner.pipeline_decisions as pd
import lexeme_aligner.span_extension as se
from tests.test_span_extension import (_FakeTok, _FakeVerseRec, _fake_heb_init, pair, write_align)


# --- statistics -------------------------------------------------------------------------------------------

def _rows(n_art=400, n_no=400, suffix_art=0.6, suffix_no=0.1, suffix="en"):
    """Synthetic nouns: with-article words end in `suffix` with rate suffix_art, anarthrous with suffix_no."""
    rows = []
    for i in range(n_art):
        w = f"stam{i % 40}" + (suffix if i < n_art * suffix_art else "x")
        rows.append((w, True, f"lx{i % 40}"))
    for i in range(n_no):
        w = f"stam{i % 40}" + (suffix if i < n_no * suffix_no else "x")
        rows.append((w, False, f"lx{i % 40}"))
    return rows


def test_a_fused_suffix_is_detected_by_the_affix_lift():
    slot = ab.article_bound_slot(_rows())
    assert slot["bound"] is True and "affix_lift" in slot["via"]
    assert slot["edge"] == "suffix" and slot["marker"] in ("-n", "-en")
    assert slot["affix_lift"]["lift"] >= ab.MIN_LIFT and slot["affix_lift"]["ratio"] >= ab.MIN_RATIO


def test_a_prefix_article_is_detected_too():
    rows = [("al" + f"kitab{i % 30}" if i < 240 else f"kitab{i % 30}", True, f"l{i % 30}") for i in range(400)]
    rows += [(f"kitab{i % 30}" if i >= 20 else "al" + f"kitab{i % 30}", False, f"l{i % 30}") for i in range(400)]
    slot = ab.article_bound_slot(rows)
    assert slot["bound"] is True and slot["edge"] == "prefix" and slot["marker"] in ("a-", "al-")


def test_no_difference_between_with_and_without_article_is_not_bound():
    slot = ab.article_bound_slot(_rows(suffix_art=0.3, suffix_no=0.3))
    assert slot["bound"] is False and slot["via"] == []


def test_a_plural_like_suffix_that_barely_correlates_with_the_article_is_not_bound():
    slot = ab.article_bound_slot(_rows(suffix_art=0.35, suffix_no=0.30, suffix="s"))     # spa/eng-shaped
    assert slot["bound"] is False


def test_too_few_nouns_gives_no_fact_not_a_guess():
    assert ab.article_bound_slot(_rows(n_art=100, n_no=400)) is None
    assert ab.article_bound_slot([]) is None


def test_the_paradigm_view_alone_can_carry_the_verdict():
    # every lexeme's article form = plain form + "ka": a clean paradigm even though "-ka" is rare token-wise
    rows = []
    for i in range(200):
        stem = f"w{i}xy"
        rows += [(stem + "ka", True, f"L{i}")] * 4 + [(stem, False, f"L{i}")] * 4
    p = ab.paradigm_stat(rows)
    assert p["tested"] == 200 and p["top"][0] == ("-ka", 200)
    slot = ab.article_bound_slot(rows)
    assert slot["bound"] is True and slot["marker"].endswith("a") and "paradigm" in slot["via"]


# --- collecting nouns from alignment files -----------------------------------------------------------------

def test_collect_nouns_uses_single_word_noun_pairs_and_the_source_article_fact(tmp_path):
    def p(h, lx, t, target, content=True):
        return {"h_idx": h, "lexeme": lx, "t_idx": t, "target": target, "content": content}
    rec = {"ref": 40001001, "pairs": [p(2, "grc:N1", [1], "Brodern"), p(4, "grc:N1", [3, 4], "two words"),
                                     p(6, "grc:V1", [5], "ran"), p(8, "grc:N1", [7], "brodern")]}
    with gzip.open(tmp_path / "align_eflomal_zz_MAT.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    article_before = {(40001001, 2): True, (40001001, 4): True, (40001001, 6): True, (40001001, 8): False}
    rows = ab.collect_nouns("zz", {"grc:N1": "noun", "grc:V1": "verb"}, article_before, tmp_path)
    assert sorted(rows) == [("brodern", False, "grc:N1"), ("brodern", True, "grc:N1")]   # normalised; 2-word and verb skipped


# --- storage ---------------------------------------------------------------------------------------------

def test_is_bound_only_for_a_positive_fact_and_survives_a_broken_file(tmp_path):
    fp = tmp_path / "ab.json"
    fp.write_text(json.dumps({"languages": {"swe": {"bound": True}, "eng": {"bound": False},
                                            "xx": {"bound": None}}}), encoding="utf-8")
    assert ab.is_bound("swe", fp) and not ab.is_bound("eng", fp) and not ab.is_bound("xx", fp)
    assert not ab.is_bound("nope", fp) and not ab.is_bound("swe", tmp_path / "missing.json")
    fp.write_text("{ half writ", encoding="utf-8")
    assert ab.load_article_bound("swe", fp) is None and not ab.is_bound("swe", fp)


# --- consumers ---------------------------------------------------------------------------------------------

def _articles_world(tmp_path, monkeypatch):
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
                [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                  "pairs": [pair(0, "lx:name", "H1", [3])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:name": "name"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB022": "1", "GB023": "0"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [{"risk": "articles", "pos": "name"}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "the", "NAME", "d"], [_FakeTok(0, "lx:name")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())

    class FakeStop:
        def __init__(self, *a, **k):
            pass

        def is_function(self, word):
            return word == "the"
    monkeypatch.setattr(se, "StopwordFilter", FakeStop)


def test_the_articles_mechanism_skips_a_language_whose_article_is_fused(tmp_path, monkeypatch):
    _articles_world(tmp_path, monkeypatch)
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path, article_bound=False)
    assert by_book["MAT"][0]["pairs"][0]["t_idx"] == [2, 3]                 # free article: widened backward
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path, article_bound=True)
    assert by_book == {} and "no flagged" in stats["skipped"]               # fused article: nothing to append


def test_extend_spans_reads_the_stored_fact_when_not_told(tmp_path, monkeypatch):
    _articles_world(tmp_path, monkeypatch)
    fp = tmp_path / "ab.json"
    fp.write_text(json.dumps({"languages": {"fake": {"bound": True}}}), encoding="utf-8")
    monkeypatch.setattr(ab, "OUT_FILE", fp)
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert by_book == {}


def test_ledger_records_articles_as_not_in_force_for_a_bound_language(monkeypatch):
    monkeypatch.setattr(pd, "load_spanext_flags", lambda iso, path=None, tag=None: {})
    monkeypatch.setattr(pd, "analyze", lambda *a, **k: {"findings": [
        {"risk": "articles", "pos": "noun", "grambank_ids": ["GB020", "GB023"]}]})
    monkeypatch.setattr(pd, "load_grambank", lambda iso: {})
    monkeypatch.setattr(pd, "direction_for", lambda *a, **k: "after")
    monkeypatch.setattr(ab, "is_bound", lambda iso, path=None: iso == "sv")
    assert pd.always_on_mechanisms("t", "sv")["articles"]["value"] is False
    assert "fused" in pd.always_on_mechanisms("t", "sv")["articles"]["gated_source"]
    assert pd.always_on_mechanisms("t", "en")["articles"]["value"] is True


def test_gram_struct_derived_partition_carries_the_slot(tmp_path):
    import lexeme_aligner.gram_struct as gs
    fp = tmp_path / "ab.json"
    fp.write_text(json.dumps({"languages": {"swe": {"bound": True, "marker": "-en"}}}), encoding="utf-8")
    out = gs.build_derived("swe", tmp_path / "none1", tmp_path / "none2", article_bound_file=fp)
    assert out["article_bound"] == {"bound": True, "marker": "-en"}
    assert "article_bound" not in gs.build_derived("eng", tmp_path / "none1", tmp_path / "none2",
                                                   article_bound_file=fp)


# --- per-edition verdicts (2026-09-30): no edition is privileged --------------------------------------------
def _slot(bound, n_article=1000, **kw):
    return {"bound": bound, "source": "derived", "n_article": n_article, "n_anarthrous": 900, **kw}


def test_combine_article_bound_clear_majority_wins_and_lists_every_edition():
    out = ab.combine_article_bound({"swe_fol": _slot(True, 2000, marker="-en", edge="suffix"),
                                    "swe_svk": _slot(True, 1500, marker="-en", edge="suffix"),
                                    "swe_xx": _slot(False, 300)})
    assert out["bound"] is True and out["agreement"]["voting"] == 3 and out["agreement"]["agree"] == 2
    assert set(out["editions"]) == {"swe_fol", "swe_svk", "swe_xx"}
    assert out["editions"]["swe_fol"]["marker"] == "-en" and out["editions"]["swe_fol"]["n"] == 2000


def test_combine_article_bound_split_abstains_and_never_gates(tmp_path):
    out = ab.combine_article_bound({"a": _slot(True, 1000), "b": _slot(False, 900)})
    assert out["bound"] is None and out["reason"] == "edition_conflict"
    f = tmp_path / "ab.json"
    f.write_text(json.dumps({"languages": {"xx": out}}))
    assert ab.is_bound("xx", f) is False


def test_combine_article_bound_too_few_nouns_editions_abstain_but_are_listed():
    out = ab.combine_article_bound({"a": _slot(False, 800),
                                    "b": {"bound": None, "source": "derived", "reason": "too few nouns"}})
    assert out["bound"] is False and out["editions"]["b"]["reason"] == "too few nouns"


def test_build_analyses_every_edition_and_replaces_old_single_edition_entries(tmp_path, monkeypatch):
    import sys
    from lexeme_aligner import derive_typology as dt
    monkeypatch.chdir(tmp_path)
    (tmp_path / "publish/compact-alignments").mkdir(parents=True)
    (tmp_path / "publish/compact-alignments/manifest.json").write_text(json.dumps({"languages": {
        "xx": {"editions": {"E1": {"tag": "xx_a", "books": ["GEN"]}, "E2": {"tag": "xx_b", "books": ["GEN"]}}},
        "yy": {"editions": {"E1": {"tag": "yy_a", "books": ["GEN"]}}}}}))
    out_file = tmp_path / "article_bound.json"
    out_file.write_text(json.dumps({"languages": {"yy": {"bound": True, "source": "derived"}}}))   # old entry, no `editions`
    monkeypatch.setattr(ab, "OUT_FILE", out_file)
    monkeypatch.setattr(ab, "source_article_index", lambda books: {})
    monkeypatch.setattr(ab, "tag_files", lambda out, method, tag: ["x"])
    monkeypatch.setattr(dt, "resolve_tag", lambda tag: (tag, tmp_path))
    import lexeme_aligner.span_extension as se
    monkeypatch.setattr(se, "load_priors", lambda p: ({}, {}))
    seen = []
    verdicts = {"xx_a": _slot(True, 1000), "xx_b": _slot(True, 500), "yy_a": _slot(False, 700)}
    monkeypatch.setattr(ab, "derive_for_tag", lambda tag, idx, lp, out: seen.append(tag) or verdicts[tag])
    assert ab.main(["--build"]) == 0
    doc = json.loads(out_file.read_text())["languages"]
    assert sorted(seen) == ["xx_a", "xx_b", "yy_a"]                       # every edition, and the stale yy entry redone
    assert doc["xx"]["bound"] is True and doc["xx"]["agreement"]["voting"] == 2
    assert doc["yy"]["bound"] is False and "editions" in doc["yy"]
    seen.clear()
    assert ab.main(["--build"]) == 0 and seen == []                       # resumable: per-edition entries are kept
