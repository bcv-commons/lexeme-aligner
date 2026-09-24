"""Offline tests for sword_source.py's OSIS/GBF Strong's-tag parsing (Step G0). No pysword module or
network needed — these exercise the regex/positional logic against synthetic tagged text."""
from lexeme_aligner.sword_source import (
    _cipherkey_for, _format_for, extract_verse_rows, extract_verse_rows_gbf, parse_strong_ids,
)


def test_cipherkey_for_finds_mods_d_five_levels_up(tmp_path):
    """mods.d is a SIBLING of modules/, i.e. up to 5 levels above a typical .../modules/texts/ztext/
    <modname> module_dir — the exact off-by-one this function's docstring says was caught testing on
    SpaRV1909 (4 levels stops one short, at modules/ itself, and silently returns None)."""
    root = tmp_path / "extracted"
    module_dir = root / "modules" / "texts" / "ztext" / "sparv1909"
    module_dir.mkdir(parents=True)
    mods_d = root / "mods.d"
    mods_d.mkdir()
    (mods_d / "sparv1909.conf").write_text("[SpaRV1909]\nCipherKey=SpaRV1909\n", encoding="utf-8")
    assert _cipherkey_for(module_dir) == "SpaRV1909"


def test_cipherkey_for_returns_none_when_conf_has_no_cipherkey(tmp_path):
    root = tmp_path / "extracted"
    module_dir = root / "modules" / "texts" / "ztext" / "chiuns"
    module_dir.mkdir(parents=True)
    mods_d = root / "mods.d"
    mods_d.mkdir()
    (mods_d / "chiuns.conf").write_text("[ChiUns]\nModDrv=zText\n", encoding="utf-8")
    assert _cipherkey_for(module_dir) is None


def test_cipherkey_for_returns_none_when_no_mods_d_exists(tmp_path):
    module_dir = tmp_path / "modules" / "texts" / "ztext" / "orphan"
    module_dir.mkdir(parents=True)
    assert _cipherkey_for(module_dir) is None


def test_format_for_detects_gbf_and_osis(tmp_path):
    root = tmp_path / "extracted"
    module_dir = root / "modules" / "texts" / "ztext" / "rusvzh"
    module_dir.mkdir(parents=True)
    mods_d = root / "mods.d"
    mods_d.mkdir()
    (mods_d / "rusvzh.conf").write_text("[RusVZh]\nSourceType=GBF\n", encoding="utf-8")
    assert _format_for(module_dir) == "gbf"


def test_format_for_defaults_to_osis(tmp_path):
    root = tmp_path / "extracted"
    module_dir = root / "modules" / "texts" / "ztext" / "fresegond1910"
    module_dir.mkdir(parents=True)
    mods_d = root / "mods.d"
    mods_d.mkdir()
    (mods_d / "fresegond1910.conf").write_text("[FreSegond1910]\nSourceType=OSIS\n", encoding="utf-8")
    assert _format_for(module_dir) == "osis"
    # no conf at all -> still osis, not a crash
    orphan = tmp_path / "orphan" / "modules" / "texts" / "ztext" / "x"
    orphan.mkdir(parents=True)
    assert _format_for(orphan) == "osis"


def test_extract_verse_rows_gbf_single_tags():
    raw = ' Родословие<WG976><WG1078> Иисуса<WG2424> Христа<WG5547>.'
    rows, clean = extract_verse_rows_gbf(raw, "40001001", "RusVZh", "")
    assert clean == " Родословие Иисуса Христа."
    strongs = [r["strong"] for r in rows]
    assert strongs == ["G0976", "G1078", "G2424", "G5547"]
    # the two stacked tags on "Родословие" both point at the SAME (first) target word
    assert rows[0]["target_id"] == rows[1]["target_id"]
    assert rows[0]["target_id"].endswith("001")
    assert rows[2]["target_id"].endswith("002")                      # Иисуса
    src_ids = [r["source_id"] for r in rows]
    assert len(set(src_ids)) == len(src_ids)                          # each tag is its own source occurrence


def test_extract_verse_rows_gbf_strips_red_letter_markup_between_word_and_tag():
    raw = '<FR>Ибо<Fr><WG1063> <FR>так<Fr><WG3779> <FR>возлюбил<Fr><WG25><WG3588>'
    rows, clean = extract_verse_rows_gbf(raw, "43003016", "RusVZh", "")
    assert clean == "Ибо так возлюбил"
    strongs = [r["strong"] for r in rows]
    assert strongs == ["G1063", "G3779", "G0025", "G3588"]
    # WG25 and WG3588 both attach to "возлюбил" (the 3rd word), not to different words
    assert rows[2]["target_id"] == rows[3]["target_id"]
    assert rows[2]["target_id"].endswith("003")


def test_extract_verse_rows_gbf_drops_tag_with_no_preceding_word():
    raw = '<WG25>слово'                                              # tag before any word at all
    rows, clean = extract_verse_rows_gbf(raw, "01001001", "RusVZh", "")
    assert clean == "слово"
    assert rows == []                                                 # nothing to anchor the tag to


def test_extract_verse_rows_gbf_drops_verse_on_tag_boundary_tokenization_mismatch(monkeypatch):
    import lexeme_aligner.sword_source as ss

    calls = {"n": 0}
    real = ss.clear_tokens

    def flaky(text):
        calls["n"] += 1
        if calls["n"] == 1:
            return ["x", "y"]
        return real(text)

    monkeypatch.setattr(ss, "clear_tokens", flaky)
    rows, clean = ss.extract_verse_rows_gbf('слово<WG25>', "01001001", "x", "")
    assert rows is None
    assert clean == "слово"


def test_parse_strong_ids_normalizes_and_pads():
    assert parse_strong_ids('strong:H7225') == ["H7225"]
    assert parse_strong_ids('strong:H07225') == ["H7225"]            # leading zero stripped, repadded
    assert parse_strong_ids('Strong:G11') == ["G0011"]                # case-insensitive prefix
    assert parse_strong_ids('strong:H1285a') == ["H1285a"]            # augmented-Strong's suffix kept


def test_parse_strong_ids_multiple_on_one_tag():
    assert parse_strong_ids('strong:H0622 strong:H0235') == ["H0622", "H0235"]


def test_parse_strong_ids_drops_out_of_range_morph_pseudo_codes():
    # H8804 is a Sword morph pseudo-code (Qal Perfect), not a real Strong's number (max H8674).
    assert parse_strong_ids('strong:H1254 strong:H8804 strong:H0853') == ["H1254", "H0853"]
    assert parse_strong_ids('strong:G9999') == []                    # above G5624


def test_extract_verse_rows_single_word_tags():
    raw = 'Au <w lemma="strong:H7225">commencement</w>, <w lemma="strong:H0430">Dieu</w> <w lemma="strong:H1254">créa</w>.'
    rows, clean = extract_verse_rows(raw, "01001001", "Segond1910", "")
    assert clean == "Au commencement, Dieu créa."
    assert rows is not None
    strongs = [r["strong"] for r in rows]
    assert strongs == ["H7225", "H0430", "H1254"]
    for r in rows:
        assert r["method"] == "sword" and r["base_text"] == "Segond1910" and r["ref"] == "01001001"
    # source_ids are distinct (no collision) and increase in document order
    src_ids = [r["source_id"] for r in rows]
    assert len(set(src_ids)) == len(src_ids)
    assert src_ids == sorted(src_ids)


def test_extract_verse_rows_multi_word_tag_shares_one_source_id():
    raw = '<w lemma="Strong:H7225">EN el principio</w> <w lemma="Strong:H1254">crió</w>.'
    rows, clean = extract_verse_rows(raw, "01001001", "RV1909", "")
    assert clean == "EN el principio crió."
    h7225_rows = [r for r in rows if r["strong"] == "H7225"]
    assert len(h7225_rows) == 3                                      # one row per target word in the span
    assert {r["source_id"] for r in h7225_rows} == {h7225_rows[0]["source_id"]}   # same source occurrence
    assert [r["target_id"][-3:] for r in h7225_rows] == ["001", "002", "003"]


def test_extract_verse_rows_repeated_strong_gets_distinct_source_ids_not_colliding():
    """Two DIFFERENT strongs, each at their own first occurrence, must not collide on source_id (the
    bug this function's docstring documents catching) — and a REPEATED strong's two occurrences must
    also get distinct, correctly-ordered source_ids."""
    raw = ('<w lemma="strong:H0430">Dieu</w> <w lemma="strong:H1254">créa</w> '
          '<w lemma="strong:H0430">Dieu</w>')
    rows, _clean = extract_verse_rows(raw, "01001001", "Segond1910", "")
    src_ids = [r["source_id"] for r in rows]
    assert len(set(src_ids)) == 3                                    # no collision across strongs
    assert src_ids == sorted(src_ids)                                 # still document order


def test_extract_verse_rows_ignores_untagged_function_words():
    raw = '<w lemma="strong:H7225">commencement</w>, les <w lemma="strong:H8064">cieux</w>'
    rows, clean = extract_verse_rows(raw, "01001001", "Segond1910", "")
    assert clean == "commencement, les cieux"
    assert {r["strong"] for r in rows} == {"H7225", "H8064"}
    # "les" is untagged and produces no row, but still occupies a target position (target_id skips it)
    cieux_row = next(r for r in rows if r["strong"] == "H8064")
    # clear_tokens position 4 (1-based): commencement(1) ,(2) les(3) cieux(4) — punctuation and
    # untagged function words still occupy a position, they just never get a gold row of their own.
    assert cieux_row["target_id"].endswith("004")


def test_extract_verse_rows_drops_verse_on_tag_boundary_tokenization_mismatch(monkeypatch):
    import lexeme_aligner.sword_source as ss

    calls = {"n": 0}
    real = ss.clear_tokens

    def flaky(text):
        calls["n"] += 1
        if calls["n"] == 1:
            return ["x"]                                              # first segment call: wrong count
        return real(text)

    monkeypatch.setattr(ss, "clear_tokens", flaky)
    rows, clean = ss.extract_verse_rows('<w lemma="strong:H0430">Dieu</w>', "01001001", "x", "")
    assert rows is None
    assert clean == "Dieu"
