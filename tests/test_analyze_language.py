"""Offline tests for analyze_language.py (phase 1: eflomal-output vs Grambank typology, no gold needed).

Nothing here touches the real vendored Grambank file or a real align_*.jsonl — both are synthesized in
tmp_path, so these tests exercise the RULE LOGIC (does a risk flag + a low observed rate produce a
finding, does an unflagged feature stay silent) rather than any one language's real numbers. The real
numbers (Hindi correctly flagged for case_marking/name, French correctly not) were checked by hand
running the CLI against the real data this session — see internal-docs/llm-align-experiment-plan.md.
"""
import json

from lexeme_aligner.analyze_language import analyze, load_grambank, multiword_rates


def write_grambank(tmp_path, languages):
    fp = tmp_path / "features.json"
    fp.write_text(json.dumps({"languages": languages}), encoding="utf-8")
    return fp


def write_align(out_dir, iso, method, book, records):
    fp = out_dir / f"align_{method}_{iso}_{book}.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def pair(h_idx, lexeme, t_idx, content=True):
    return {"h_idx": h_idx, "lexeme": lexeme, "t_idx": t_idx, "content": content,
            "target": " ".join(str(t) for t in t_idx)}


def test_load_grambank_missing_file_returns_none(tmp_path):
    assert load_grambank("xyz", tmp_path / "nope.json") is None


def test_load_grambank_missing_language_returns_none(tmp_path):
    fp = write_grambank(tmp_path, {"fra": {"GB072": "0"}})
    assert load_grambank("hin", fp) is None


def test_load_grambank_returns_the_language_dict(tmp_path):
    fp = write_grambank(tmp_path, {"hin": {"GB070": "1", "GB072": "1"}})
    assert load_grambank("hin", fp) == {"GB070": "1", "GB072": "1"}


def test_multiword_rates_buckets_by_pos_and_ignores_non_content_or_unaligned(tmp_path):
    write_align(tmp_path, "iso1", "eflomal", "MAT", [
        {"ref": 1, "pairs": [
            pair(0, "lx:name1", [0]),                          # name, single-word
            pair(1, "lx:name2", [1, 2]),                        # name, multi-word
            pair(2, "lx:noun1", [3], content=False),            # not content -> excluded
            pair(3, "lx:verb1", []),                            # no t_idx -> excluded
        ]},
    ])
    lex_pos = {"lx:name1": "name", "lx:name2": "name", "lx:noun1": "noun", "lx:verb1": "verb"}
    rates = multiword_rates("iso1", tmp_path, lex_pos)
    assert rates == {"name": (1, 2)}                            # 1 multi-word of 2 total; noun/verb excluded


def test_analyze_end_to_end_flags_risk_only_when_grambank_says_so(tmp_path, monkeypatch):
    import lexeme_aligner.analyze_language as al

    grambank_fp = write_grambank(tmp_path, {
        "hin_like": {"GB070": "1", "GB072": "1"},    # case_marking ON
        "fra_like": {"GB070": "0", "GB072": "0"},    # case_marking OFF
    })
    monkeypatch.setattr(al, "GRAMBANK_FEATURES_FILE", grambank_fp)

    # Same underlying eflomal shape for both: 100 name pairs, only 2 multi-word (2% — below threshold).
    records = [{"ref": 1, "pairs": [pair(i, "lx:name", [i]) for i in range(98)] +
                                   [pair(98, "lx:name", [98, 99]), pair(99, "lx:name", [100, 101])]}]
    write_align(tmp_path, "hin_edition", "eflomal", "MAT", records)
    write_align(tmp_path, "fra_edition", "eflomal", "MAT", records)
    lex_pos_fp = tmp_path / "unused"                             # analyze() takes prior_pack path, not used
    # analyze() reads lex_pos via gapfill.load_priors(prior_pack) -> patch it directly for a clean unit test
    monkeypatch.setattr(al, "load_priors", lambda _pp: ({"lx:name": "name"}, {}))

    hin_report = al.analyze("hin_edition", "hin_like", out_dir=tmp_path, prior_pack=lex_pos_fp)
    fra_report = al.analyze("fra_edition", "fra_like", out_dir=tmp_path, prior_pack=lex_pos_fp)

    assert hin_report["grambank_covered"] and fra_report["grambank_covered"]
    assert any(f["risk"] == "case_marking" and f["pos"] == "name" for f in hin_report["findings"])
    assert not any(f["risk"] == "case_marking" for f in fra_report["findings"])   # same rate, feature off


def test_all_zero_polarity_flags_only_when_every_feature_is_zero(tmp_path, monkeypatch):
    """The `all_zero` polarity branch itself, exercised via a synthetic risk key — NOT possession_affix,
    which tried this polarity and measured a wash-to-loss on real hin/fra gold, and was reverted to
    `any_one` (see analyze_language.py's RISK_RULES comment and span_extension.py's docstring). The
    mechanism stays implemented and tested for a future category it might actually fit."""
    import lexeme_aligner.analyze_language as al

    monkeypatch.setattr(al, "RISK_RULES", al.RISK_RULES + [
        ("synthetic_all_zero", ("noun",), 0.05, "test-only all_zero rule.", "all_zero", "n/a"),
    ])
    grambank_fp = write_grambank(tmp_path, {
        "hin_like": {"GBsyn1": "0", "GBsyn2": "0"},    # all zero -> flagged
        "eng_like": {"GBsyn1": "0", "GBsyn2": "1"},    # one "1" -> NOT flagged
    })
    monkeypatch.setattr(al, "GRAMBANK_FEATURES_FILE", grambank_fp)
    monkeypatch.setattr(al, "GRAMBANK_FEATURES", dict(al.GRAMBANK_FEATURES, synthetic_all_zero=["GBsyn1", "GBsyn2"]))

    # Same underlying shape for both: 100 noun pairs, only 2 multi-word (2% — below threshold).
    records = [{"ref": 1, "pairs": [pair(i, "lx:noun", [i]) for i in range(98)] +
                                   [pair(98, "lx:noun", [98, 99]), pair(99, "lx:noun", [100, 101])]}]
    write_align(tmp_path, "hin_edition", "eflomal", "MAT", records)
    write_align(tmp_path, "eng_edition", "eflomal", "MAT", records)
    lex_pos_fp = tmp_path / "unused"
    monkeypatch.setattr(al, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))

    hin_report = al.analyze("hin_edition", "hin_like", out_dir=tmp_path, prior_pack=lex_pos_fp)
    eng_report = al.analyze("eng_edition", "eng_like", out_dir=tmp_path, prior_pack=lex_pos_fp)

    assert any(f["risk"] == "synthetic_all_zero" and f["pos"] == "noun" for f in hin_report["findings"])
    assert not any(f["risk"] == "synthetic_all_zero" for f in eng_report["findings"])


def test_all_zero_polarity_does_not_flag_on_missing_feature_data(tmp_path, monkeypatch):
    import lexeme_aligner.analyze_language as al

    monkeypatch.setattr(al, "RISK_RULES", al.RISK_RULES + [
        ("synthetic_all_zero", ("noun",), 0.05, "test-only all_zero rule.", "all_zero", "n/a"),
    ])
    # GBsyn2 absent entirely (partial coverage) — must NOT be treated as an implicit "0".
    grambank_fp = write_grambank(tmp_path, {"partial": {"GBsyn1": "0"}})
    monkeypatch.setattr(al, "GRAMBANK_FEATURES_FILE", grambank_fp)
    monkeypatch.setattr(al, "GRAMBANK_FEATURES", dict(al.GRAMBANK_FEATURES, synthetic_all_zero=["GBsyn1", "GBsyn2"]))
    records = [{"ref": 1, "pairs": [pair(i, "lx:noun", [i]) for i in range(98)] +
                                   [pair(98, "lx:noun", [98, 99]), pair(99, "lx:noun", [100, 101])]}]
    write_align(tmp_path, "partial_edition", "eflomal", "MAT", records)
    monkeypatch.setattr(al, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))

    report = al.analyze("partial_edition", "partial", out_dir=tmp_path, prior_pack=tmp_path / "unused")
    assert not any(f["risk"] == "synthetic_all_zero" for f in report["findings"])


def test_possession_affix_stays_any_one_polarity_in_production():
    """Regression guard: `all_zero` was tried for possession_affix and measured a wash-to-loss on real
    Clear gold (hin, fra whole Bible) — reverted. Fails loudly if someone re-flips it without re-measuring."""
    import lexeme_aligner.analyze_language as al
    entry = next(r for r in al.RISK_RULES if r[0] == "possession_affix")
    assert entry[4] == "any_one"


def test_analyze_without_grambank_coverage_reports_no_findings(tmp_path, monkeypatch):
    import lexeme_aligner.analyze_language as al
    monkeypatch.setattr(al, "GRAMBANK_FEATURES_FILE", tmp_path / "does-not-exist.json")
    monkeypatch.setattr(al, "load_priors", lambda _pp: ({"lx:name": "name"}, {}))
    write_align(tmp_path, "xyz_edition", "eflomal", "MAT",
               [{"ref": 1, "pairs": [pair(0, "lx:name", [0])]}])
    report = al.analyze("xyz_edition", "xyz", out_dir=tmp_path, prior_pack=tmp_path / "unused")
    assert report["grambank_covered"] is False and report["findings"] == []


def test_analyze_falls_back_to_typology_when_grambank_is_none(tmp_path, monkeypatch):
    """Step 2: a language absent from Grambank entirely still gets flagged for a direction-bearing
    risk (case_marking/articles/possession_affix) if the typology table resolves a direction for it."""
    import lexeme_aligner.analyze_language as al
    monkeypatch.setattr(al, "GRAMBANK_FEATURES_FILE", tmp_path / "does-not-exist.json")   # no Grambank
    monkeypatch.setattr(al, "load_priors", lambda _pp: ({"lx:name": "name"}, {}))
    records = [{"ref": 1, "pairs": [pair(i, "lx:name", [i]) for i in range(98)] +
                                   [pair(98, "lx:name", [98, 99]), pair(99, "lx:name", [100, 101])]}]
    write_align(tmp_path, "xyz_edition", "eflomal", "MAT", records)

    import lexeme_aligner.typology as ty
    monkeypatch.setattr(ty, "direction",
                        lambda iso, slot, path=ty._OUT: "before" if slot == "adposition" else None)

    report = al.analyze("xyz_edition", "xyz", out_dir=tmp_path, prior_pack=tmp_path / "unused",
                        use_typology=True)
    assert report["grambank_covered"] is False                     # still correctly reports no Grambank
    assert any(f["risk"] == "case_marking" and f["grambank_ids"] == ["typology:adposition"]
              for f in report["findings"])


def test_analyze_typology_fallback_off_by_default(tmp_path, monkeypatch):
    """use_typology defaults False — a Grambank-absent language must NOT be flagged via the typology
    table unless explicitly opted in (measured net-negative for 2 of 3 languages tested)."""
    import lexeme_aligner.analyze_language as al
    monkeypatch.setattr(al, "GRAMBANK_FEATURES_FILE", tmp_path / "does-not-exist.json")
    monkeypatch.setattr(al, "load_priors", lambda _pp: ({"lx:name": "name"}, {}))
    records = [{"ref": 1, "pairs": [pair(i, "lx:name", [i]) for i in range(98)] +
                                   [pair(98, "lx:name", [98, 99]), pair(99, "lx:name", [100, 101])]}]
    write_align(tmp_path, "xyz_edition", "eflomal", "MAT", records)
    import lexeme_aligner.typology as ty
    monkeypatch.setattr(ty, "direction",
                        lambda iso, slot, path=ty._OUT: "before" if slot == "adposition" else None)

    report = al.analyze("xyz_edition", "xyz", out_dir=tmp_path, prior_pack=tmp_path / "unused")
    assert not any(f["risk"] == "case_marking" for f in report["findings"])


def test_analyze_typology_fallback_still_requires_the_rate_anomaly(tmp_path, monkeypatch):
    """The typology fallback only supplies EXISTENCE — the empirical rate-anomaly threshold still has
    to hold, same as the Grambank path; a healthy (non-anomalous) multiword rate must NOT be flagged."""
    import lexeme_aligner.analyze_language as al
    monkeypatch.setattr(al, "GRAMBANK_FEATURES_FILE", tmp_path / "does-not-exist.json")
    monkeypatch.setattr(al, "load_priors", lambda _pp: ({"lx:name": "name"}, {}))
    # 50% multi-word — well above the 0.05 anomaly threshold, a healthy rate.
    records = [{"ref": 1, "pairs": [pair(i, "lx:name", [i, i + 1000]) for i in range(50)] +
                                   [pair(i, "lx:name", [i]) for i in range(50, 100)]}]
    write_align(tmp_path, "xyz_edition", "eflomal", "MAT", records)

    import lexeme_aligner.typology as ty
    monkeypatch.setattr(ty, "direction",
                        lambda iso, slot, path=ty._OUT: "before" if slot == "adposition" else None)

    report = al.analyze("xyz_edition", "xyz", out_dir=tmp_path, prior_pack=tmp_path / "unused")
    assert not any(f["risk"] == "case_marking" for f in report["findings"])
