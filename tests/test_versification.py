"""Offline tests for versification.py — synthetic USJ/.vrs data, no real vendored files.

Written for roadmap item E1 (internal-docs/aim1-typology-source-structure-plan.md §4R): the
declared-vs-detected CDN versification diff. Finding: `scheme_of()` already prefers structure-based
auto-detection (`detect_scheme`) over the manual `config/versification.json` fallback whenever a
usj_dir is given — true at every real call site in this codebase (run_pilot, gapfill, span_extension,
compact_align, derive_typology, pos_score, ...) — so a wrong/missing manual entry is a latent risk
only for a language with no ingested text to fingerprint. Verified against the 22 editions the CDN
declares non-eng that we align: 21/22 auto-detect to the exact declared scheme (hun/HUNHUN, the
flagged critical Psalm-superscription case, at 100% confidence); the 22nd (ind/INDASV) has an empty
ingest-cache directory (0 files) — an onboarding gap, not a versification bug. No `config/
versification.json` edit was warranted by this diff: every entry it could add for these 22 would be
dead code, shadowed by auto-detection.
"""
import json

import lexeme_aligner.versification as vf


def _usj(book, verses_per_chapter):
    """A minimal single-book USJ doc: {chapter: n_verses}."""
    content = [{"type": "book", "marker": "id", "code": book, "content": []}]
    for ch, n in verses_per_chapter.items():
        content.append({"type": "chapter", "marker": "c", "number": str(ch)})
        content.append({"type": "para", "marker": "p", "content": [
            {"type": "verse", "marker": "v", "number": str(v)} for v in range(1, n + 1)]})
    return {"type": "USJ", "version": "3.0", "content": content}


def _write_usj(tmp_path, book, verses_per_chapter):
    fp = tmp_path / f"{book}.json"
    fp.write_text(json.dumps(_usj(book, verses_per_chapter)), encoding="utf-8")
    return tmp_path


def test_usj_structure_reads_last_verse_per_chapter(tmp_path):
    _write_usj(tmp_path, "GEN", {1: 31, 2: 25})
    struct = vf._usj_structure(str(tmp_path))
    assert struct["GEN"] == {1: 31, 2: 25}


def test_usj_structure_handles_bridged_verse_numbers(tmp_path):
    # a "3-4" bridged verse marker — takes the first number, matching real USJ ingestion
    fp = tmp_path / "GEN.json"
    doc = {"type": "USJ", "content": [
        {"type": "book", "marker": "id", "code": "GEN", "content": []},
        {"type": "chapter", "marker": "c", "number": "1"},
        {"type": "para", "marker": "p", "content": [
            {"type": "verse", "marker": "v", "number": "1"},
            {"type": "verse", "marker": "v", "number": "3-4"}]}]}
    fp.write_text(json.dumps(doc), encoding="utf-8")
    struct = vf._usj_structure(str(tmp_path))
    assert struct["GEN"][1] == 3


def test_detect_scheme_picks_the_best_matching_cdn_scheme(tmp_path, monkeypatch):
    # PSA 51 with a 2-verse superscription (Hebrew/org numbering: 21 verses vs KJV/eng's 19)
    usj_dir = _write_usj(tmp_path, "PSA", {51: 21})
    monkeypatch.setattr(vf, "_load_vrs", lambda name: {
        "eng": {"PSA": {51: 19}}, "org": {"PSA": {51: 21}}, "orgw": {"PSA": {51: 20}},
    }.get(name, {}))
    vf._DETECT_CACHE.clear()
    label, best_cdn, scores = vf.detect_scheme(str(usj_dir))
    assert best_cdn == "org"
    assert label == "hebrew"
    assert scores["org"] == 1.0
    assert scores["eng"] < 1.0


def test_detect_scheme_empty_usj_returns_none(tmp_path):
    vf._DETECT_CACHE.clear()
    assert vf.detect_scheme(str(tmp_path)) == (None, None, {})


def test_scheme_of_prefers_autodetect_over_manual_file(tmp_path, monkeypatch):
    """The core E1 finding: a usj_dir that auto-detects cleanly wins over config/versification.json,
    even when the manual file disagrees or has no entry at all."""
    usj_dir = _write_usj(tmp_path, "PSA", {51: 21})
    monkeypatch.setattr(vf, "_load_vrs", lambda name: {
        "eng": {"PSA": {51: 19}}, "org": {"PSA": {51: 21}},
    }.get(name, {}))
    vf._DETECT_CACHE.clear()
    # manual file says protestant (or is silent) for this iso — auto-detect must still win
    versif_fp = tmp_path.parent / "versification_manual.json"
    versif_fp.write_text(json.dumps({"hunhun": "protestant"}), encoding="utf-8")
    monkeypatch.setattr(vf, "_VERSIF", versif_fp)
    assert vf.scheme_of("hunhun", str(usj_dir)) == "hebrew"


def test_scheme_of_falls_back_to_manual_file_when_no_usj(tmp_path, monkeypatch):
    versif_fp = tmp_path / "versification_manual.json"
    versif_fp.write_text(json.dumps({"rus": "septuagint"}), encoding="utf-8")
    monkeypatch.setattr(vf, "_VERSIF", versif_fp)
    assert vf.scheme_of("rus", None) == "septuagint"
    assert vf.scheme_of("rus", "/no/such/dir") == "septuagint"


def test_scheme_of_defaults_to_protestant_with_no_signal_at_all(tmp_path, monkeypatch):
    monkeypatch.setattr(vf, "_VERSIF", tmp_path / "nope.json")
    assert vf.scheme_of("zzz", None) == "protestant"


# --- 2026-10-05: the spine's OT is HEBREW-numbered (MACULA = WLC), not KJV. The remap now goes spine -> KJV
# (hebrew.tsv) -> target. Before this fix, protestant targets got identity (Hebrew PSA 3:1, the superscription,
# was paired with English 3:1) and hebrew targets got a KJV->Hebrew shift on top of the Hebrew spine.
def test_hebrew_numbered_target_needs_no_remap():
    assert vf.remapper_for_scheme("hebrew") is None


def test_protestant_target_is_remapped_from_the_hebrew_spine():
    f = vf.remapper_for_scheme("protestant")
    assert f is not None
    assert f("PSA", 3, 2) == ("PSA", 3, 1)          # Hebrew v2 = English v1 ("O LORD, how my foes...")
    assert f("PSA", 51, 3) == ("PSA", 51, 1)        # past a two-verse superscription
    assert f("1CH", 5, 27) == ("1CH", 6, 1)
    assert f("1CH", 6, 1) == ("1CH", 6, 16)
    assert f("MAL", 3, 19) == ("MAL", 4, 1)
    assert f("JOL", 4, 1) == ("JOL", 3, 1)
    assert f("DAN", 3, 31) == ("DAN", 4, 1)         # in hebrew.tsv, missing from bcv-commons/bibles' org map
    assert f("GEN", 1, 1) == ("GEN", 1, 1)          # identity where the numberings agree


def test_superscription_maps_to_verse_zero_for_an_english_numbered_target():
    f = vf.remapper_for_scheme("protestant")
    assert f("PSA", 3, 1) == ("PSA", 3, 0)          # English leaves the title unnumbered: no text, never the wrong verse
    assert f("PSA", 51, 1) == ("PSA", 51, 0) and f("PSA", 51, 2) == ("PSA", 51, 0)


def test_parse_reads_title_as_verse_zero():
    assert vf._parse("PSA 3:title") == ("PSA", 3, 0)
    assert vf._parse("PSA 3:2") == ("PSA", 3, 2)


# --- 2026-09-26: real exact tables for vul/rso + the catm->hebrew bug fix, from real bcv-commons/bibles
# data (config/bibles_vrs/) — see versification.py's own module docstring for the full comparison this
# was based on (catm==org exactly, org/orgw are subsets of our own hebrew.tsv, vul/rso are genuinely
# distinct from each other and from our own lxx.tsv).
def test_cdn_table_catm_maps_to_hebrew_not_septuagint():
    # regression test for a real bug found and fixed 2026-09-26: catm was wrongly bucketed with the
    # lxx/septuagint family, but catm's real data is byte-identical to org's, which is hebrew-family.
    assert vf._CDN_TABLE["catm"] == "hebrew"


def test_cdn_table_org_and_orgw_still_map_to_hebrew():
    assert vf._CDN_TABLE["org"] == "hebrew"
    assert vf._CDN_TABLE["orgw"] == "hebrew"


def test_cdn_table_vul_and_rso_have_their_own_distinct_tables():
    assert vf._CDN_TABLE["vul"] == "vul"
    assert vf._CDN_TABLE["rso"] == "rso"
    assert vf._CDN_TABLE["vul"] != vf._CDN_TABLE["rso"]


def test_cdn_table_lxx_keeps_its_own_existing_table_unchanged():
    assert vf._CDN_TABLE["lxx"] == "lxx"


def test_vul_reverse_table_loads_real_data():
    rev = vf.load_reverse("vul")
    assert len(rev) > 2000                              # real table, not empty/stub


def test_rso_reverse_table_loads_real_data():
    rev = vf.load_reverse("rso")
    assert len(rev) > 3000


def test_vul_and_rso_tables_are_not_identical():
    # a real, verified finding: vul (2845 rows) and rso (4132 rows) share only 2647 rows — genuinely
    # distinct schemes, not aliases of one another.
    vul = vf.load_reverse("vul")
    rso = vf.load_reverse("rso")
    shared = set(vul) & set(rso)
    disagree = sum(1 for k in shared if vul[k] != rso[k])
    assert disagree > 0                                  # real disagreements exist, not a coincidence


def test_rso_gives_a_genuinely_different_remap_than_the_old_lxx_fallback_for_some_verses():
    # before this fix, rso silently resolved through our own lxx.tsv (the "septuagint" table); now it
    # has its own real table. Confirm the two tables really do disagree on at least one real verse —
    # this is what makes wiring in the dedicated table meaningful rather than a no-op relabeling.
    old = vf.load_reverse("septuagint")
    new = vf.load_reverse("rso")
    shared = set(old) & set(new)
    disagreements = [k for k in shared if old[k] != new[k]]
    # 2026-10-06: bcv-commons/bibles' rebuilt rso table (Synodal = English layout in Jeremiah/Exodus/1 Kings/1 Chronicles) no longer
    # carries the LXX-style rows there, so the two tables now differ in 9 verses (all Job 41; was 53, 82 before the PSA 9 fix).
    assert len(disagreements) >= 5 and all(k[0] == "JOB" for k in disagreements)


def test_remapper_for_scheme_rso_uses_the_new_dedicated_table():
    f = vf.remapper_for_scheme("rso")
    assert f is not None
    # a real disagreement verse between the two tables. (Until 2026-10-05 this used PSA 10:8 — but that difference was the
    # rso off-by-one bug in Psalms 9-10, now fixed; JER 27/49 and JOB 29 are where rso and lxx genuinely differ.)
    old_f = vf.remapper_for_scheme("septuagint")
    assert f("JER", 27, 10) != old_f("JER", 27, 10)


def test_rso_psalm_9_10_merge_boundary_is_not_shifted():
    # 2026-10-05: the vendored rso table was off by one from Synodal 9:21 (checked against the rus_syn text:
    # Synodal 9:22 "Для чего, Господи, стоишь вдали" = English/Hebrew 10:1); confirmed by bcv-commons/bibles.
    f = vf.remapper_for_scheme("rso")
    assert f("PSA", 10, 1) == ("PSA", 9, 22)
    assert f("PSA", 10, 18) == ("PSA", 9, 39)
    assert f("PSA", 9, 21) == ("PSA", 9, 21)      # Hebrew 9:21 (title = v1 in both) = Synodal 9:21


def test_two_hebrew_verses_onto_one_english_verse_merge():
    # 2026-10-05: TVTMS "Concatenation" cases where two Hebrew verses are ONE English verse — both map to it and the chain
    # pools them (within a chapter) instead of pairing the second Hebrew verse with the next English verse.
    f = vf.remapper_for_scheme("protestant")
    assert f("1KI", 22, 44) == ("1KI", 22, 43) and f("1KI", 22, 45) == ("1KI", 22, 44)
    assert f("1CH", 12, 5) == ("1CH", 12, 4) and f("1CH", 12, 6) == ("1CH", 12, 5)
    assert f("NUM", 25, 19) == ("NUM", 26, 1)        # English Numbers 25 has 18 verses: this one used to get no text
    assert f("1SA", 21, 1) == ("1SA", 20, 42) and f("1SA", 21, 2) == ("1SA", 21, 1)
    assert f("PSA", 13, 6) == ("PSA", 13, 5)          # one-to-two in truth (13:5-6); the larger half


def _spine_with(tmp_path, value):
    import sqlite3
    db = tmp_path / f"spine_{value}.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE spine_meta (key TEXT, value TEXT)")
    if value is not None:
        con.execute("INSERT INTO spine_meta VALUES ('versification_ot', ?)", (value,))
    con.commit()
    con.close()
    return db


def test_spine_numbering_check_accepts_org_and_an_undeclared_spine(tmp_path):
    assert vf.check_spine_numbering(_spine_with(tmp_path, "org")) == "org"
    assert vf.check_spine_numbering(_spine_with(tmp_path, None)) is None


def test_spine_numbering_check_refuses_another_declared_scheme(tmp_path):
    import pytest
    with pytest.raises(RuntimeError, match="versification_ot='eng'"):
        vf.check_spine_numbering(_spine_with(tmp_path, "eng"))


def test_the_active_spine_declares_org():
    assert vf.check_spine_numbering() in ("org", None)


def test_two_line_psalm_titles_pair_in_order_in_the_synodal_scheme():
    # 2026-10-06: Synodal 50:1-2 / 51:1-2 / 53:1-2 / 59:1-2 are two title lines (verified against the Russian text);
    # both Hebrew title lines used to land on the LAST Synodal one (collision), leaving the first unpaired.
    f = vf.remapper_for_scheme("rso")
    for heb, syn in ((51, 50), (52, 51), (54, 53), (60, 59)):
        assert f("PSA", heb, 1) == ("PSA", syn, 1)
        assert f("PSA", heb, 2) == ("PSA", syn, 2)
        assert f("PSA", heb, 3) == ("PSA", syn, 3)        # Synodal n:3 = English (n+1):1


def test_one_line_psalm_titles_are_unchanged_in_the_synodal_scheme():
    f = vf.remapper_for_scheme("rso")
    assert f("PSA", 53, 1) == ("PSA", 52, 1) and f("PSA", 53, 2) == ("PSA", 52, 2)     # Synodal 52: ONE title line
    assert f("PSA", 3, 1) == ("PSA", 3, 1)


def test_english_titles_still_map_to_verse_zero_even_with_two_title_lines():
    f = vf.remapper_for_scheme("protestant")
    assert f("PSA", 51, 1) == ("PSA", 51, 0) and f("PSA", 51, 2) == ("PSA", 51, 0)


def test_verse_map_records_the_ordered_title_pairing_for_rso():
    vm = vf.verse_map("rso", [("PSA", 51, 1), ("PSA", 51, 2), ("PSA", 51, 3)])
    assert vm == {"PSA 51:1": "PSA 50:1", "PSA 51:2": "PSA 50:2", "PSA 51:3": "PSA 50:3"}


def test_load_reverse_all_keeps_every_scheme_verse_and_load_reverse_keeps_the_last():
    ra = vf.load_reverse_all("rso")
    assert ra[("PSA", 51, 0)] == [("PSA", 50, 1), ("PSA", 50, 2)]
    assert vf.load_reverse("rso")[("PSA", 51, 0)] == ("PSA", 50, 2)


# --- 2026-10-06: bcv-commons/bibles' multi-verse relations file for rso (<table>.multiverse.json) -----------------
def test_expand_ref_handles_single_ranges_and_cross_chapter_ranges():
    assert vf.expand_ref("LEV 14:55") == [("LEV", 14, 55)]
    assert vf.expand_ref("PSA 13:5-6") == [("PSA", 13, 5), ("PSA", 13, 6)]
    assert vf.expand_ref("NUM 25:19-26:1") == [("NUM", 25, 19), ("NUM", 26, 1)]
    assert vf.expand_ref("1SA 20:42-43") == [("1SA", 20, 42), ("1SA", 20, 43)]
    assert vf.expand_ref("PSA 3:title") == [("PSA", 3, 0)]


def _scheme_dir(tmp_path, monkeypatch, tsv_rows, multiverse):
    import json as _json
    d = tmp_path / "schemes"
    d.mkdir()
    (d / "hebrew.tsv").write_text(Path("pipeline/vendor/versification/schemes/hebrew.tsv").read_text(encoding="utf-8"), encoding="utf-8")
    (d / "rso.tsv").write_text("source_ref\tstandard_ref\taction\n" + "".join(f"{s}\t{t}\tRenumber verse\n" for s, t in tsv_rows),
                               encoding="utf-8")
    if multiverse is not None:
        (d / "rso.multiverse.json").write_text(_json.dumps({"map": multiverse}), encoding="utf-8")
    monkeypatch.setattr(vf, "_REG_DIR", d)


from pathlib import Path  # noqa: E402


def test_one_synodal_verse_covering_two_english_verses_pairs_both_spine_verses_with_it(tmp_path, monkeypatch):
    _scheme_dir(tmp_path, monkeypatch, [("LEV 14:56", "LEV 14:57")], [{"s": "LEV 14:55", "t": "LEV 14:55-56"}])
    f = vf.remapper_for_scheme("rso")
    assert f("LEV", 14, 55) == ("LEV", 14, 55) and f("LEV", 14, 56) == ("LEV", 14, 55)      # both land on Synodal 14:55
    assert f("LEV", 14, 57) == ("LEV", 14, 56)


def test_two_synodal_verses_for_one_english_verse_pair_in_order_with_the_two_spine_verses(tmp_path, monkeypatch):
    # English 20:42 = Synodal 20:42 + 20:43; the Hebrew spine folds 20:42 and 21:1 into that one English verse (hebrew.tsv)
    _scheme_dir(tmp_path, monkeypatch, [], [{"s": "1SA 20:42-43", "t": "1SA 20:42"}])
    f = vf.remapper_for_scheme("rso")
    assert f("1SA", 20, 42) == ("1SA", 20, 42) and f("1SA", 21, 1) == ("1SA", 20, 43)
    assert f("1SA", 21, 2) == ("1SA", 21, 1)


def test_without_a_multiverse_file_nothing_changes(tmp_path, monkeypatch):
    _scheme_dir(tmp_path, monkeypatch, [("LEV 14:56", "LEV 14:57")], None)
    assert vf.multiverse_pairs("rso") == []
    assert vf.remapper_for_scheme("rso")("LEV", 14, 56) == ("LEV", 14, 56)
