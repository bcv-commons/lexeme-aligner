"""Offline tests for edition_struct.py (roadmap E2) — synthetic inputs via path parameters, no real
config files or sibling DB needed."""
import json

import lexeme_aligner.edition_struct as es


def _w(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_build_tag_to_iso_inverts_compact_alignments_manifest(tmp_path):
    manifest = _w(tmp_path / "m.json", {"languages": {"hin": {"editions": {"hinirv": {}}},
                                                       "eng": {"editions": {"engbsb": {}, "eng_ylt": {}}}}})
    out = es.build_tag_to_iso(manifest)
    assert out == {"hinirv": "hin", "engbsb": "eng", "eng_ylt": "eng"}


def test_build_tag_to_iso_keys_are_lowercased_to_join_against_lowercase_pin_filenames(tmp_path):
    # regression: compact-alignments' own `editions` keys are UPPERCASE (AAAMLT), but
    # config/pins/<tag>.json filenames are lowercase (aaamlt.json) — a naive exact-string join
    # silently lost 797 of 1,726 real tags to this casing mismatch alone.
    manifest = _w(tmp_path / "m.json", {"languages": {"aaa": {"editions": {"AAAMLT": {}}}}})
    out = es.build_tag_to_iso(manifest)
    assert out == {"aaamlt": "aaa"}


def test_build_record_resolves_iso_case_insensitively():
    pin = {"provider": "p", "version_id": "V", "name": "n", "license_url": None, "sha256": "s",
          "books": 1, "language_name": "n"}
    rec = es.build_record("AAAMLT", pin, {"aaamlt": "aaa"}, {}, {}, {}, {})
    assert rec["iso"] == "aaa"


def test_load_helloao_by_version_id(tmp_path):
    fp = _w(tmp_path / "h.json", {"translations": [
        {"id": "AAB", "licenseUrl": "https://x", "sha256": "abc", "textDirection": "ltr"}]})
    out = es.load_helloao_by_version_id(fp)
    assert out["AAB"]["textDirection"] == "ltr"


def test_build_record_resolved_iso_and_helloao_join():
    pin = {"provider": "bible.helloao.org", "version_id": "HINIRV", "name": "IRV", "iso": "hin",
          "license_url": None, "sha256": "abc123", "books": 66, "language_name": "Hindi"}
    tag_to_iso = {"hinirv": "hin"}
    helloao = {"HINIRV": {"textDirection": "ltr", "licenseUrl": "https://real-license", "id": "HINIRV",
                          "sha256": "abc123"}}
    rec = es.build_record("hinirv", pin, tag_to_iso, helloao, {}, {}, {"hin": "Deva"})
    assert rec["tag"] == "hinirv"
    assert rec["iso"] == "hin"
    assert rec["iso_source"] == "pin"
    assert rec["pinned"]["license_url"] == "https://real-license"   # falls back to helloAO's own
    assert rec["pinned"]["helloao"] == {"text_direction": "ltr", "id": "HINIRV"}
    assert rec["derived"]["script"] == {"code": "Deva", "source": "languages_db"}
    assert rec["derived"]["text_strip"] == {"strip_brackets": False, "strip_parens_noise": False,
                                            "source": "default"}


def test_build_record_unresolved_iso_is_explicit_not_a_guess():
    pin = {"provider": "x", "version_id": "ZZZ", "name": "n", "license_url": None, "sha256": "s",
          "books": 1, "language_name": "n"}   # no "iso" key at all
    rec = es.build_record("zzztag", pin, {}, {}, {}, {}, {})
    assert rec["iso"] is None
    assert rec["iso_source"] == "unresolved"
    assert rec["derived"]["script"] is None


def test_build_record_prefers_pins_own_iso_over_compact_alignments():
    # the pin's own iso field covers pinned-but-never-pooled editions the compact-alignments reverse
    # index misses entirely (found while building this module: 459 real cases) — it must win, not
    # just be a fallback.
    pin = {"provider": "p", "version_id": "V", "name": "n", "iso": "aaz", "license_url": None,
          "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("aazpkf", pin, {}, {}, {}, {}, {})
    assert rec["iso"] == "aaz"
    assert rec["iso_source"] == "pin"


def test_build_record_falls_back_to_compact_alignments_when_pin_has_no_iso():
    pin = {"provider": "p", "version_id": "V", "name": "n", "license_url": None, "sha256": "s",
          "books": 1, "language_name": "n"}
    rec = es.build_record("tag", pin, {"tag": "xx"}, {}, {}, {}, {})
    assert rec["iso"] == "xx"
    assert rec["iso_source"] == "compact-alignments"


def test_build_record_flags_a_genuine_disagreement_between_pin_and_compact_alignments():
    pin = {"provider": "p", "version_id": "V", "name": "n", "iso": "aaa", "license_url": None,
          "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("tag", pin, {"tag": "bbb"}, {}, {}, {}, {})
    assert rec["iso"] == "aaa"                      # the pin's own value still wins
    assert rec["iso_conflict"] == {"pin": "aaa", "compact_alignments": "bbb"}


def test_build_record_no_conflict_key_when_sources_agree_or_only_one_resolves():
    pin = {"provider": "p", "version_id": "V", "name": "n", "iso": "aaa", "license_url": None,
          "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("tag", pin, {"tag": "aaa"}, {}, {}, {}, {})
    assert "iso_conflict" not in rec


# --- _looks_like_a_real_iso / the pin-iso-is-a-tag-copy placeholder bug -----------------------------------
def test_looks_like_a_real_iso_accepts_three_letter_codes():
    assert es._looks_like_a_real_iso("aaz") is True
    assert es._looks_like_a_real_iso("hin") is True


def test_looks_like_a_real_iso_rejects_a_tag_shaped_value():
    assert es._looks_like_a_real_iso("aaamlt") is False    # 6 chars — a tag, not an iso
    assert es._looks_like_a_real_iso("abt_wos") is False   # underscore — definitely not an iso
    assert es._looks_like_a_real_iso(None) is False
    assert es._looks_like_a_real_iso("") is False


def test_build_record_ignores_a_pin_iso_that_is_just_a_copy_of_the_tag(tmp_path):
    # regression: config/pins/aaamlt.json's real "iso" field is the literal string "aaamlt" (a
    # placeholder, not a real code) — build_record must fall through to compact-alignments instead
    # of trusting it, exactly the bug found and fixed while building this module.
    pin = {"provider": "p", "version_id": "AAAMLT", "name": "n", "iso": "aaamlt",
          "license_url": None, "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("aaamlt", pin, {"aaamlt": "aaa"}, {}, {}, {}, {})
    assert rec["iso"] == "aaa"
    assert rec["iso_source"] == "compact-alignments"
    assert "iso_conflict" not in rec   # the bogus pin value never even enters the comparison


def test_build_record_trusts_a_genuine_three_letter_pin_iso_even_when_it_equals_the_tag(tmp_path):
    # the 10 legitimate single-edition "primary tag == bare iso" cases must NOT be caught by the
    # tag-copy guard just because pin_iso == tag — only the length/alpha shape matters.
    pin = {"provider": "p", "version_id": "HIN", "name": "n", "iso": "hin", "license_url": None,
          "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("hin", pin, {}, {}, {}, {}, {})
    assert rec["iso"] == "hin"
    assert rec["iso_source"] == "pin"


def test_build_record_carries_own_license_url_over_helloao_when_present():
    pin = {"provider": "x", "version_id": "AAB", "name": "n", "license_url": "https://own",
          "sha256": "s", "books": 1, "language_name": "n"}
    helloao = {"AAB": {"licenseUrl": "https://helloao-fallback", "textDirection": "ltr", "id": "AAB",
                       "sha256": "x"}}
    rec = es.build_record("tag", pin, {}, helloao, {}, {}, {})
    assert rec["pinned"]["license_url"] == "https://own"


def test_build_record_textual_basis_and_text_strip_from_their_own_edition_entries():
    pin = {"provider": "x", "version_id": "V", "name": "n", "license_url": None, "sha256": "s",
          "books": 1, "language_name": "n"}
    tb = {"tag": {"verdict": "tr", "checked": 15, "present": 10, "bracketed": 5}}
    ts = {"tag": {"strip_brackets": True, "strip_parens_noise": True, "reason": "editorial notes"}}
    rec = es.build_record("tag", pin, {}, {}, tb, ts, {})
    assert rec["derived"]["textual_basis"] == {"verdict": "tr", "checked": 15, "present": 10,
                                               "bracketed": 5, "source": "diagnostic-verses"}
    assert rec["derived"]["text_strip"] == {"strip_brackets": True, "strip_parens_noise": True,
                                            "reason": "editorial notes", "source": "manual"}


def test_build_writes_one_file_per_pin_plus_coverage(tmp_path):
    pins = tmp_path / "pins"
    pins.mkdir()
    _w(pins / "aaa.json", {"provider": "p", "version_id": "AAA", "name": "n", "license_url": None,
                           "sha256": "s", "books": 1, "language_name": "n"})
    _w(pins / "bbb.json", {"provider": "p", "version_id": "BBB", "name": "n", "license_url": None,
                           "sha256": "s", "books": 1, "language_name": "n"})
    out = tmp_path / "out"
    manifest = _w(tmp_path / "m.json", {"languages": {"xx": {"editions": {"aaa": {}}}}})
    cov = es.build(pins_dir=pins, out_dir=out, manifest_path=manifest,
                   helloao_file=tmp_path / "absent-helloao.json",
                   textual_basis_file=tmp_path / "absent-tb.json",
                   text_strip_file=tmp_path / "absent-ts.json",
                   languages_db=tmp_path / "absent.db",
                   ebible_csv=tmp_path / "absent-ebible.csv")
    assert cov["editions"] == 2
    assert cov["resolved_iso"] == 1
    assert cov["unresolved_iso"] == 1
    assert json.loads((out / "aaa.json").read_text())["iso"] == "xx"
    assert json.loads((out / "bbb.json").read_text())["iso"] is None
    assert (out / "_coverage.json").exists()


def test_build_degrades_gracefully_when_sibling_db_absent(tmp_path):
    assert es.load_scripts(tmp_path / "nope.db") == {}


# --- roadmap E5 (reduced scope, 2026-09-26): ebible translations.csv license/script/direction ---------
def test_ebible_id_from_license_url_extracts_the_real_join_key():
    # verified directly against real data: hinirv.json's own license_url carries ebible's translationId
    url = "https://ebible.org/Scriptures/details.php?id=hin2017"
    assert es.ebible_id_from_license_url(url) == "hin2017"


def test_ebible_id_from_license_url_none_for_a_non_ebible_url():
    assert es.ebible_id_from_license_url("https://cdn.bibel.wiki/pkf/ind/app-config.json") is None
    assert es.ebible_id_from_license_url(None) is None


def test_load_ebible_translations_keys_by_translation_id(tmp_path):
    csv_fp = tmp_path / "translations.csv"
    csv_fp.write_text(
        "languageCode,translationId,Redistributable,Copyright,textDirection,script\n"
        "hin,hin2017,True,Copyright (C) 2017 Bridge,ltr,Devanagari\n",
        encoding="utf-8")
    out = es.load_ebible_translations(csv_fp)
    assert set(out) == {"hin2017"}
    assert out["hin2017"]["Redistributable"] == "True"


def test_load_ebible_translations_degrades_to_empty_when_absent(tmp_path):
    assert es.load_ebible_translations(tmp_path / "nope.csv") == {}


def test_build_record_adds_real_ebible_enrichment_when_license_url_matches():
    pin = {"provider": "bible.helloao.org", "version_id": "HINIRV", "name": "n",
          "license_url": "https://ebible.org/Scriptures/details.php?id=hin2017",
          "sha256": "s", "books": 66, "language_name": "Hindi"}
    ebible = {"hin2017": {"Redistributable": "True",
                         "Copyright": "Copyright (C) 2017 Bridge Connectivity Solutions",
                         "textDirection": "ltr", "script": "Devanagari"}}
    rec = es.build_record("hinirv", pin, {}, {}, {}, {}, {}, ebible)
    assert rec["derived"]["ebible"] == {
        "license": "Copyright (C) 2017 Bridge Connectivity Solutions",
        "redistributable": True, "script": "Devanagari", "text_direction": "ltr",
        "translation_id": "hin2017", "source": "ebible-translations-csv"}


def test_build_record_ebible_is_none_when_license_url_does_not_match_any_row():
    pin = {"provider": "p", "version_id": "V", "name": "n",
          "license_url": "https://ebible.org/Scriptures/details.php?id=zzz9999",
          "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("tag", pin, {}, {}, {}, {}, {}, {"hin2017": {}})
    assert rec["derived"]["ebible"] is None


def test_build_record_ebible_is_none_when_license_url_is_not_an_ebible_url():
    pin = {"provider": "p", "version_id": "V", "name": "n",
          "license_url": "https://cdn.bibel.wiki/pkf/ind/app-config.json",
          "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("tag", pin, {}, {}, {}, {}, {}, {"hin2017": {}})
    assert rec["derived"]["ebible"] is None


def test_build_record_ebible_redistributable_false_is_a_real_boolean_not_a_truthy_string():
    pin = {"provider": "p", "version_id": "V", "name": "n",
          "license_url": "https://ebible.org/Scriptures/details.php?id=restricted1",
          "sha256": "s", "books": 1, "language_name": "n"}
    ebible = {"restricted1": {"Redistributable": "False", "Copyright": "All rights reserved",
                             "textDirection": "ltr", "script": "Latin"}}
    rec = es.build_record("tag", pin, {}, {}, {}, {}, {}, ebible)
    assert rec["derived"]["ebible"]["redistributable"] is False


def test_build_writes_has_ebible_coverage_stat(tmp_path):
    pins = tmp_path / "pins"
    pins.mkdir()
    _w(pins / "aaa.json", {"provider": "p", "version_id": "AAA", "name": "n",
                           "license_url": "https://ebible.org/Scriptures/details.php?id=aaa1",
                           "sha256": "s", "books": 1, "language_name": "n"})
    ebible_csv = tmp_path / "translations.csv"
    ebible_csv.write_text(
        "translationId,Redistributable,Copyright,textDirection,script\n"
        "aaa1,True,Copyright X,ltr,Latin\n", encoding="utf-8")
    out = tmp_path / "out"
    cov = es.build(pins_dir=pins, out_dir=out, manifest_path=tmp_path / "absent-m.json",
                   helloao_file=tmp_path / "absent-helloao.json",
                   textual_basis_file=tmp_path / "absent-tb.json",
                   text_strip_file=tmp_path / "absent-ts.json",
                   languages_db=tmp_path / "absent.db", ebible_csv=ebible_csv)
    assert cov["has_ebible"] == 1
    assert json.loads((out / "aaa.json").read_text())["derived"]["ebible"]["translation_id"] == "aaa1"


def test_real_ebible_csv_and_pins_produce_the_verified_match_count():
    # not a synthetic test: confirms the real, committed config/ebible/translations.csv actually joins
    # against our real pins at the count verified during this task (551 real matches).
    import pathlib
    if not pathlib.Path("config/ebible/translations.csv").exists():
        return
    ebible = es.load_ebible_translations()
    n = 0
    for p in pathlib.Path("config/pins").glob("*.json"):
        d = json.loads(p.read_text())
        eid = es.ebible_id_from_license_url(d.get("license_url"))
        if eid and eid in ebible:
            n += 1
    assert n == 551
