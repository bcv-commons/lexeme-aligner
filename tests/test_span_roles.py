"""span_roles.py: per-kind evidence about what [...] and (...) spans mean, and the role rule."""
import gzip
import json
import re

import lexeme_aligner.span_roles as sr


def _stats(n=100, words=300, median=4, max_words=12, short=0.3, clause=0.2, ref=0, digits=0.0, lex=0):
    return {"n_spans": n, "n_words": words, "median_words": median, "p90_words": max_words, "max_words": max_words,
            "pct_short": short, "pct_clause": clause, "pct_digits": digits, "n_structural_ref": ref,
            "n_swedish_lexical": lex}


def test_length_stats_shape_and_noise_counts():
    spans = [(1, "Elohim"), (2, "the same"), (5, "for the Jordan overfloweth all."), (9, "a long aside with eight or more words in it")]
    st = sr.span_length_stats(spans, structural_hits=1, lexical_hits=0)
    assert st["n_spans"] == 4 and st["n_words"] == 17 and st["max_words"] == 9
    assert st["pct_short"] == 0.5 and st["pct_clause"] == 0.5 and st["n_structural_ref"] == 1


def test_parentheses_that_align_like_ordinary_text_are_content():
    role, why = sr.classify_role("parens", _stats(), pct_of_edition=0.4, align_ratio=0.96)
    assert role == "content" and "0.96" in why[0]


def test_bracketed_supplied_words_that_do_not_align_are_supplied_not_commentary():
    role, _ = sr.classify_role("brackets", _stats(n=10390, words=10837, median=1, max_words=3, short=1.0),
                               pct_of_edition=1.37, align_ratio=0.08)
    assert role == "supplied"


def test_the_same_low_ratio_on_parentheses_is_not_called_supplied():
    role, _ = sr.classify_role("parens", _stats(short=0.9, median=1, max_words=3), pct_of_edition=0.5, align_ratio=0.1)
    assert role == "unclear"                                   # supplied words are a bracket phenomenon


def test_extreme_length_or_citation_share_means_commentary_regardless_of_alignment():
    assert sr.classify_role("brackets", _stats(max_words=818), 35.0, 0.3)[0] == "commentary"
    assert sr.classify_role("parens", _stats(ref=40), 0.6, 0.9)[0] == "commentary"               # 40% citations
    assert sr.classify_role("brackets", _stats(median=12, max_words=40), 2.0, 0.2)[0] == "commentary"


def test_a_minority_of_citation_spans_in_otherwise_aligned_text_is_mixed():
    assert sr.classify_role("parens", _stats(ref=8), 0.6, 0.9)[0] == "mixed"


def test_negligible_and_insufficient_are_decided_before_any_judgement():
    assert sr.classify_role("parens", _stats(), 0.01, 1.0)[0] == "negligible"
    assert sr.classify_role("parens", _stats(n=5), 0.3, 1.0)[0] == "insufficient"
    assert sr.classify_role("brackets", _stats(n=0), 0.0, None)[0] == "negligible"


def test_without_alignment_data_only_clearly_short_spans_are_content():
    assert sr.classify_role("parens", _stats(short=0.95, max_words=6), 0.3, None)[0] == "content"
    role, why = sr.classify_role("parens", _stats(short=0.4, max_words=25), 0.3, None)
    assert role == "unclear" and "no alignment data" in why[0]


def test_ratio_needs_enough_words_and_a_nonzero_outside_rate():
    rates = {"outside": (400, 1000), "parens": (20, 50), "brackets": (1, 10)}
    assert abs(sr.ratio(rates, "parens") - (20 / 50) / (400 / 1000)) < 1e-9
    assert sr.ratio(rates, "brackets") is None                 # only 10 words inside
    assert sr.ratio(None, "parens") is None
    assert sr.ratio({"outside": (0, 5000), "parens": (5, 100), "brackets": (0, 0)}, "parens") is None


def test_alignment_rates_split_words_by_where_they_sit(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    rec = {"ref": 1, "chapter": 1, "verse": 1, "pairs": [{"content": True, "t_idx": [0, 1, 4]}, {"content": False, "t_idx": [2]}]}
    with gzip.open(out / "align_eflomal_zz_GEN.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    usj_root = tmp_path / "usj"
    (usj_root / "usj-zz").mkdir(parents=True)
    (usj_root / "usj-zz" / "01-GEN.json").write_text("{}", encoding="utf-8")
    text = "aa bb (cc dd) ee [ff]"                                # tokens 0..5: aa bb | cc dd | ee | ff
    spans = [(m.start(), m.end()) for m in re.finditer(r"\w+", text)]
    rates = sr.alignment_rates(
        "zz", usj_root, out, re.compile(r"\[[^\[\]]*\]"), re.compile(r"\(([^()]*)\)"), lambda t: spans,
        lambda fp, rules: {(1, 1): {"text": text, "verse_end": 1}}, [("GEN", "01")])
    assert rates["outside"] == (3, 3)        # aa bb ee: positions 0,1,4 are aligned... ee is token 4
    assert rates["parens"] == (0, 2) and rates["brackets"] == (0, 1)


def test_alignment_rates_is_none_for_an_edition_without_alignments(tmp_path):
    assert sr.alignment_rates("nope", tmp_path, tmp_path, None, None, None, None, []) is None


def _alt_stats(pct_dup, n_eval=100, **kw):
    st = {"n_spans": 200, "n_words": 2000, "median_words": 10, "p90_words": 30, "max_words": 120, "pct_short": 0.3,
          "pct_clause": 0.5, "pct_digits": 0.0, "n_structural_ref": 0, "n_swedish_lexical": 0,
          "n_dup_eval": n_eval, "dup_median": 0.6, "pct_dup": pct_dup, "n_dup_words": 500}
    st.update(kw)
    return st


def test_repetition_share_ignores_short_words_and_thin_spans():
    from lexeme_aligner import span_roles as r
    assert r.repetition_share(["in", "the", "sea"], {"the", "sea"}) is None
    assert r.repetition_share(["Saul", "saw", "tents"], {"saul", "tents", "camp"}) == 1.0
    assert r.repetition_share(["Saul", "tents", "camp", "dawn"], {"saul", "tents"}) == 0.5


def test_alternate_beats_length_and_alignment_commentary_signals():
    from lexeme_aligner import span_roles as r
    role, _ = r.classify_role("parens", _alt_stats(0.76), 42.0, 0.33)
    assert role == "alternate"


def test_low_repetition_keeps_the_old_decision():
    from lexeme_aligner import span_roles as r
    role, _ = r.classify_role("parens", _alt_stats(0.08), 42.0, 0.33)
    assert role == "commentary"


def test_citations_still_mean_commentary_even_with_repetition():
    from lexeme_aligner import span_roles as r
    role, _ = r.classify_role("parens", _alt_stats(0.6, n_structural_ref=80), 5.0, 0.9)
    assert role == "commentary"
