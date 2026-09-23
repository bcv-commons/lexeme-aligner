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


def test_analyze_without_grambank_coverage_reports_no_findings(tmp_path, monkeypatch):
    import lexeme_aligner.analyze_language as al
    monkeypatch.setattr(al, "GRAMBANK_FEATURES_FILE", tmp_path / "does-not-exist.json")
    monkeypatch.setattr(al, "load_priors", lambda _pp: ({"lx:name": "name"}, {}))
    write_align(tmp_path, "xyz_edition", "eflomal", "MAT",
               [{"ref": 1, "pairs": [pair(0, "lx:name", [0])]}])
    report = al.analyze("xyz_edition", "xyz", out_dir=tmp_path, prior_pack=tmp_path / "unused")
    assert report["grambank_covered"] is False and report["findings"] == []
