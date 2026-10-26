"""Offline tests for bsb_tables.py: column parsing, HTML → structure and back, empty-row retention, sort
keys, the 1:n source mapping, the target letters-walk, and the 23-column round trip — all on synthetic
rows, no vendor file needed."""
import lexeme_aligner.eval.bsb_tables as bt


def _cells(**kw):
    """A 23-cell row with everything blank except the given short keys."""
    base = {k: "" for k in bt.KEYS}
    base.update(heb_sort="1", grk_sort="0", bsb_sort="1", verse_num="1", language="Hebrew")
    base.update(kw)
    return [base[k] for k in bt.KEYS]


HDG = "<p class=|hdg|>The Creation"
XREF = ("<br /><span class=|cross|>(<a href =|../john/1.htm|>John 1:1–5</a>; "
        "<a href =|../hebrews/11.htm|>Hebrews 11:1–3</a>)</span>")


def test_parse_row_keeps_verbatim_columns_and_normalizes_strong():
    r = bt.parse_row(_cells(base="אֱלֹהִ֑ים", base_variants="אֱלֹהִ֑ים", translit="’ĕ·lō·hîm", parsing="N-mp",
                            parsing_long="Noun - masculine plural", str_heb="430", text=" God ", pnc=","))
    assert r["text"] == " God "                       # surrounding spaces are data
    assert r["strong"] == "H0430" and r["str_heb"] == "430"
    assert r["kind"] == "row"


def test_greek_strong_prefix_and_suffix_letter():
    r = bt.parse_row(_cells(language="Greek", heb_sort="999999", grk_sort="5", base="Βίβλος", str_grk="976"))
    assert r["strong"] == "G0976"
    r2 = bt.parse_row(_cells(base="x", str_heb="5445a"))
    assert (r2["strong"], r2["strong_suffix"]) == ("H5445", "a")


def test_empty_padding_row_is_retained_as_kind_empty():
    r = bt.parse_row(_cells(heb_sort="8", bsb_sort="8"))
    assert r["kind"] == "empty" and r["bsb_sort"] == "8" and r["heb_sort"] == "8"


def test_hdg_round_trip():
    d = bt.parse_hdg(HDG)
    assert d == {"tag": "p", "cls": "hdg", "text": "The Creation"}
    assert bt.emit_hdg(d) == HDG
    assert bt.parse_hdg("") is None and bt.emit_hdg(None) == ""


def test_crossref_round_trip_keeps_text_and_href():
    v = bt.parse_crossref(XREF)
    assert v == [{"text": "John 1:1–5", "href": "../john/1.htm"}, {"text": "Hebrews 11:1–3", "href": "../hebrews/11.htm"}]
    assert bt.emit_crossref(v) == XREF


def test_par_red_letter_is_structured_not_dropped():
    v = bt.parse_par("<span class=|red|>")
    assert v == [{"tag": "span", "cls": "red"}]
    assert bt.emit_par(v) == "<span class=|red|>"
    assert bt.emit_par(bt.parse_par("<p class=|list1stline|>")) == "<p class=|list1stline|>"


def test_end_text_and_footnote_round_trip():
    e = bt.parse_end_text("”</span>")
    assert e == [{"text": "”"}, {"close": "span"}] and bt.emit_end_text(e) == "”</span>"
    assert bt.emit_end_text(bt.parse_end_text("’</span>”")) == "’</span>”"     # order is data
    f = bt.parse_footnote("Hebrew; LXX <i>Mareshah</i>")
    assert f == "Hebrew; LXX *Mareshah*" and bt.emit_footnote(f) == "Hebrew; LXX <i>Mareshah</i>"


def test_unparseable_html_is_kept_not_dropped():
    assert bt.parse_hdg("<div>odd</div>") == {"unparsed": "<div>odd</div>"}
    assert bt.emit_hdg({"unparsed": "<div>odd</div>"}) == "<div>odd</div>"


def test_emit_row_is_the_inverse_of_parse_row_with_parsing_table():
    cells = _cells(base="בְּרֵאשִׁ֖ית", base_variants="בְּרֵאשִׁ֖ית", translit="bə·rê·šîṯ", parsing="Prep-b | N-fs",
                   parsing_long="Preposition-b | Noun - feminine singular", str_heb="7225",
                   verse_id="Genesis 1:1", hdg=HDG, crossref=XREF, par="<p class=|reg|>", text=" In the beginning ",
                   footnotes="a <i>b</i>", end_text="”</span>")
    r = bt.parse_row(cells)
    table = {"Prep-b | N-fs": "Preposition-b | Noun - feminine singular"}
    assert bt.emit_row(dict(r, parsing_long=None), table) == cells


def test_parsing_table_reports_non_function_instead_of_forcing():
    rows = [bt.parse_row(_cells(base="a", parsing="N-mp", parsing_long="Noun - masculine plural")),
            bt.parse_row(_cells(base="b", parsing="N-mp", parsing_long="Noun - masc. pl.")),
            bt.parse_row(_cells(base="c", parsing="Conj", parsing_long="Conjunction"))]
    table, exc = bt.build_parsing_table(rows)
    assert table["Conj"] == "Conjunction"
    assert exc == [("N-mp", {"Noun - masculine plural", "Noun - masc. pl."})]
    # the minority long is kept per row as an override, so emit_row reproduces it exactly
    minority = next(r for r in rows if r["parsing_long"] == "Noun - masc. pl.")
    ov = bt.parsing_long_override(minority, table)
    assert bt.emit_row(dict(minority, parsing_long=ov), table)[9] == "Noun - masc. pl."
    blank_short = bt.parse_row(_cells(base="d", parsing="", parsing_long="HTj"))
    assert "" not in bt.build_parsing_table([blank_short])[0]
    assert bt.parsing_long_override(blank_short, {}) == "HTj"


def _grk(sort, base, strong, text=" x "):
    return bt.parse_row(_cells(language="Greek", heb_sort="999999", grk_sort=str(sort), bsb_sort=str(sort), base=base,
                               str_grk=strong, text=text))


def _heb(sort, base, strong="", text=" x ", bsb=None):
    return bt.parse_row(_cells(heb_sort=str(sort), bsb_sort=str(bsb or sort), base=base, str_heb=strong, text=text))


def test_map_source_fused_token_takes_both_rows():
    # spine fuses "Ἰσαάκ, Ἰσαὰκ" (two MACULA words) into ONE token; BSB has two rows -> both on it, the second `fused`
    rows = [_grk(1, "Ἰσαάκ", "2464"), _grk(2, "Ἰσαὰκ", "2464"), _grk(3, "δὲ", "1161")]
    m = bt.map_source(rows, [(0, "G2464", "Ἰσαάκ Ἰσαὰκ", ["40001002004", "40001002005"], True),
                             (1, "G1161", "δὲ", ["40001002006"], False)])
    assert m["1"] == {"h_idx_key": 0, "h_idx": [0], "match": "strong"}
    assert m["2"] == {"h_idx_key": 0, "h_idx": [0], "match": "fused"}
    assert m["3"] == {"h_idx_key": 1, "h_idx": [1], "match": "strong"}


def test_map_source_repeated_strong_follows_order_not_kth_occurrence():
    # MAT 2:9 shape: the table numbers εἶδον G3708 like ἰδοὺ (MACULA: ἰδοὺ G3708, εἶδον G3708 -> here G1492) ; the old
    # (Strong's, k-th occurrence) key gave εἶδον the ἰδοὺ token. Order + spelling keep each row on its own word.
    rows = [_grk(1, "ἰδοὺ", "3708"), _grk(2, "ὁ", "3588"), _grk(3, "ἀστήρ", "792"), _grk(4, "ὃν", "3739"), _grk(5, "εἶδον", "3708")]
    spine = [(0, "G2400", "ἰδοὺ", ["40002009001"], False), (1, "G3588", "ὁ", ["40002009002"], False),
             (2, "G0792", "ἀστήρ", ["40002009003"], True), (3, "G3739", "ὃν", ["40002009004"], False),
             (4, "G3708", "εἶδον", ["40002009005"], True)]
    m = bt.map_source(rows, spine)
    assert m["1"] == {"h_idx_key": 0, "h_idx": [0], "match": "spelling"}
    assert m["5"] == {"h_idx_key": 4, "h_idx": [4], "match": "strong"}


def test_map_source_compound_name_two_rows_on_one_fused_token():
    # BSB: בֵּית (H1004) + אֵל (H1008); spine: ONE token "בֵּית אֵל" H1008 holding two words
    rows = [_heb(1, "בֵּֽית־", "1004", " vvv "), _heb(2, "אֵ֖ל", "1008", " Bethel ")]
    m = bt.map_source(rows, [(0, "H1008", "בֵּֽית אֵ֖ל", ["010280190081", "010280190091"], True)])
    assert m["1"] == {"h_idx_key": 0, "h_idx": [0], "match": "spelling"}
    assert m["2"] == {"h_idx_key": 0, "h_idx": [0], "match": "fused"}


def test_map_source_row_takes_its_whole_word_prefix_and_suffix():
    # spine: בְּ רֵאשִׁית | בָּרָא | אֱלֹהִים | ... ; and a word with a pronoun suffix followed by a conjunction word:
    # אִשְׁתּ+וֹ (wife+his) וְ+... -> the suffix stays with its own word (the old rule gave it to the next row)
    rows = [_heb(1, "בְּרֵאשִׁ֖ית", "7225"), _heb(2, "בָּרָ֣א", "1254"), _heb(3, "אִשְׁתּ֔וֹ", "802"), _heb(4, "וַיֵּ֣לֶךְ", "1980")]
    spine = [(0, "H0871", "בְּ", ["010010010011"], False), (1, "H7225", "רֵאשִׁ֖ית", ["010010010012"], True),
             (2, "H1254", "בָּרָ֣א", ["010010010021"], True),
             (3, "H0802", "אִשְׁתּ֔", ["010010010031"], True), (4, "H2050", "וֹ", ["010010010032"], False),
             (5, "H2050", "וַ", ["010010010041"], False), (6, "H1980", "יֵּ֣לֶךְ", ["010010010042"], True)]
    m = bt.map_source(rows, spine)
    assert m["1"] == {"h_idx_key": 1, "h_idx": [0, 1], "match": "strong"}
    assert m["3"] == {"h_idx_key": 3, "h_idx": [3, 4], "match": "strong"}
    assert m["4"] == {"h_idx_key": 6, "h_idx": [5, 6], "match": "strong"}


def test_map_source_strongless_row_by_spelling():
    # לָהֶם "Prep | 3mp" has no Strong's number in the table: placed by order and spelling on its word (both tokens)
    rows = [_heb(1, "הָאָרֶץ", "776"), _heb(2, "לָהֶם")]
    spine = [(0, "H1886", "הָ", ["010010010011"], False), (1, "H0776", "אָרֶץ", ["010010010012"], True),
             (2, "H0871", "לָ", ["010010010021"], False), (3, "H1886", "הֶם", ["010010010022"], False)]
    m = bt.map_source(rows, spine)
    assert m["1"] == {"h_idx_key": 1, "h_idx": [0, 1], "match": "strong"}
    assert m["2"] == {"h_idx_key": 2, "h_idx": [2, 3], "match": "spelling"}


def test_aramaic_row_takes_its_strong_from_str_heb():
    r = bt.parse_row(_cells(language="Aramaic", heb_sort="1", grk_sort="0", base="חָזֵ֤ה", str_heb="2370"))
    assert r["strong"] == "H2370"


def test_map_source_aramaic_rows_go_in_heb_sort_order():
    # DAN/EZR rows say language "Aramaic" and carry Heb Sort (Greek Sort 0); listed here in English (BSB Sort) order
    rows = [bt.parse_row(_cells(language="Aramaic", heb_sort="2", grk_sort="0", bsb_sort="1", base="עִם־", text=" with ")),
            bt.parse_row(_cells(language="Aramaic", heb_sort="1", grk_sort="0", bsb_sort="2", base="חָזֵ֤ה", text=" I saw "))]
    m = bt.map_source(rows, [(0, "H2370", "חָזֵ֤ה", ["270070130011"], True), (1, "H5974", "עִם", ["270070130061"], False)])
    assert m["2"]["h_idx_key"] == 0 and m["1"]["h_idx_key"] == 1


def test_map_source_word_of_another_edition_is_none_not_guessed():
    # a table word the spine's text lacks (another edition's reading) and an unrelated Strong's -> none
    rows = [_grk(1, "καὶ", "2532"), _grk(2, "ἐγένετο", "1096"), _grk(3, "λόγος", "3056")]
    m = bt.map_source(rows, [(0, "G1096", "ἐγένετο", ["43001003001"], True), (1, "G3056", "λόγος", ["43001003002"], True)])
    assert m["1"]["match"] == "none" and m["2"]["h_idx_key"] == 0 and m["3"]["h_idx_key"] == 1
    m2 = bt.map_source([_heb(1, "x", "9999")], [(0, "H0430", "אֱלֹהִים", ["010010010031"], True)])
    assert m2["1"] == {"h_idx_key": None, "h_idx": [], "match": "none"}


def test_map_target_walks_spans_in_bsb_sort_order():
    rows = [bt.parse_row(_cells(bsb_sort="2", base="b", str_heb="430", text=" God ")),
            bt.parse_row(_cells(bsb_sort="1", base="a", str_heb="7225", text=" In the beginning ")),
            bt.parse_row(_cells(bsb_sort="3", base="c", str_heb="853", text=" - ")),
            bt.parse_row(_cells(bsb_sort="4", base="d", str_heb="1096", text=" vvv ")),
            bt.parse_row(_cells(bsb_sort="5", base="e", str_heb="1254", text=" created "))]
    toks = ["In", "the", "beginning", "God", "created"]
    assert bt.map_target(rows, toks) == {"1": [0, 1, 2], "2": [3], "3": [], "4": [], "5": [4]}


def test_fractional_sort_key_survives_verbatim_and_orders_numerically():
    r = bt.parse_row(_cells(heb_sort="8132.5", bsb_sort="10", base="x", str_heb="1"))
    assert r["heb_sort"] == "8132.5" and bt.sort_key(r["heb_sort"]) == 8132.5
    assert bt.emit_row(dict(r, parsing_long=None), {})[0] == "8132.5"
    assert sorted(["10", "9", "8132.5"], key=bt.sort_key) == ["9", "10", "8132.5"]


def test_map_target_refuses_when_spans_do_not_tile_our_tokens():
    rows = [bt.parse_row(_cells(bsb_sort="1", base="a", str_heb="1", text=" In the beginning "))]
    assert bt.map_target(rows, ["In", "the", "beginning", "God"]) is None      # our token left over
    assert bt.map_target(rows, ["In", "the"]) is None                          # span longer than our text


# --- the `bsb` nested struct: lossless through pyarrow, no JSON-in-a-string --------------------------------
def _struct_round_trip(cells, table):
    import pyarrow as pa
    r = bt.parse_row(cells)
    rec = {"ref": 1001001, "book": "GEN", "chapter": 1, "verse": 1, "h_idx": [0], "h_idx_key": 0,
           "lexeme": "hbo:7225", "strong": r["strong"], "t_idx": [0], "target": r["text"], "content": True,
           "method": "manual", "score": None,
           "attribution": {"source": "bsb-tables", "kind": "manual", "base_text": "BSB", "license": "CC0-1.0",
                           "pin_sha256": "x", "match": "strong"},
           "bsb": bt.to_struct(r, table)}
    t = pa.Table.from_pylist([rec], schema=bt.record_schema())        # explicit schema, must not raise
    back = t.to_pylist()[0]["bsb"]
    return bt.emit_row(bt.from_struct(back), table), t


def test_bsb_struct_round_trips_parsed_html_through_pyarrow():
    table = {"Prep-b | N-fs": "Preposition-b | Noun - feminine singular"}
    cells = _cells(base="בְּרֵאשִׁ֖ית", base_variants="בְּרֵאשִׁ֖ית", translit="bə·rê·šîṯ", parsing="Prep-b | N-fs",
                   parsing_long="Preposition-b | Noun - feminine singular", str_heb="7225", verse_id="Genesis 1:1",
                   hdg=HDG, crossref=XREF, par="<p class=|reg|><span class=|red|>", text=" In the beginning ",
                   footnotes="a <i>b</i>", end_text="’</span>”", beg_q="“", pnc=".")
    regen, t = _struct_round_trip(cells, table)
    assert regen == cells
    b = t.column("bsb").type
    assert str(b.field("crossref").type).startswith("list<")            # a real list<struct>, not a string
    assert str(b.field("hdg").type).startswith("struct<")


def test_bsb_struct_keeps_unparsed_html_in_the_sibling_field_and_round_trips():
    cells = _cells(base="x", str_heb="1", hdg="<div>odd</div>", crossref="<b>weird</b>", text=" x ")
    regen, t = _struct_round_trip(cells, {})
    assert regen == cells
    back = t.to_pylist()[0]["bsb"]
    assert back["hdg"] is None and back["hdg_unparsed"] == "<div>odd</div>"
    assert back["crossref"] is None and back["crossref_unparsed"] == "<b>weird</b>"


def test_bsb_struct_empty_padding_row_round_trips():
    cells = _cells(heb_sort="8", bsb_sort="8")
    regen, t = _struct_round_trip(cells, {})
    assert regen == cells and t.to_pylist()[0]["bsb"]["kind"] == "empty"


def test_merge_into_manifest_updates_atomically_and_regenerates_card(tmp_path, monkeypatch):
    import json
    root = tmp_path / "full-align"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"languages": {"eng": {"editions": {"engbsb": {"layers": {
        "manual": {"BSB": {"source": "clear"}}, "statistical": {"rows": 1}}}}}}}), encoding="utf-8")
    entry = {"source": "bsb-tables", "owner": "bsb_tables", "license": "CC0-1.0", "rows": 3, "coverage": {},
             "contract": {}, "sidecar": {}, "round_trip_sample": {}, "source_none_breakdown": {}, "pin": {}}
    assert bt.merge_into_manifest(root, entry) is True
    m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manual = m["languages"]["eng"]["editions"]["engbsb"]["layers"]["manual"]
    assert manual["BSB"] == {"source": "clear"} and manual["BSB-tables"]["owner"] == "bsb_tables"
    assert m["languages"]["eng"]["editions"]["engbsb"]["layers"]["statistical"] == {"rows": 1}
    assert not (root / "manifest.json.tmp").exists()
    assert "BSB Translation Tables layer" in (root / "README.md").read_text(encoding="utf-8")


# --- 2026-09-28: _resolve_bsb_usj_dir() — config-driven, never a hardcoded tag name ----------------------

def _write_sources(tmp_path, **tag_to_edition):
    import json
    fp = tmp_path / "sources.json"
    fp.write_text(json.dumps({tag: {"edition": ed} for tag, ed in tag_to_edition.items()}), encoding="utf-8")
    return fp


def test_resolve_bsb_usj_dir_prefers_engbsb_when_present(tmp_path):
    sources = _write_sources(tmp_path, engbsb="BSB", bsb="BSB")
    (tmp_path / "usj-engbsb").mkdir()
    (tmp_path / "usj-bsb").mkdir()
    got = bt._resolve_bsb_usj_dir(sources_path=sources, ingest_root=tmp_path)
    assert got == tmp_path / "usj-engbsb"


def test_resolve_bsb_usj_dir_falls_back_when_engbsb_is_gone(tmp_path):
    # the real scenario this fixes: production now writes under "bsb", "engbsb" no longer exists on disk.
    sources = _write_sources(tmp_path, engbsb="BSB", bsb="BSB")
    (tmp_path / "usj-bsb").mkdir()   # engbsb's own directory is absent
    got = bt._resolve_bsb_usj_dir(sources_path=sources, ingest_root=tmp_path)
    assert got == tmp_path / "usj-bsb"


def test_resolve_bsb_usj_dir_is_case_insensitive_on_the_edition_value(tmp_path):
    sources = _write_sources(tmp_path, weirdtag="bsb")
    (tmp_path / "usj-weirdtag").mkdir()
    got = bt._resolve_bsb_usj_dir(sources_path=sources, ingest_root=tmp_path)
    assert got == tmp_path / "usj-weirdtag"


def test_resolve_bsb_usj_dir_last_resort_when_nothing_exists_on_disk(tmp_path):
    sources = _write_sources(tmp_path, engbsb="BSB")
    got = bt._resolve_bsb_usj_dir(sources_path=sources, ingest_root=tmp_path)
    assert got == tmp_path / "usj-engbsb"   # historical default, even though absent -- never crashes


def test_resolve_bsb_usj_dir_missing_sources_file(tmp_path):
    got = bt._resolve_bsb_usj_dir(sources_path=tmp_path / "nope.json", ingest_root=tmp_path)
    assert got == tmp_path / "usj-engbsb"


def test_edition_and_lang_constants_are_never_renamed_by_this_fix():
    # the real published path publish/full-align/eng/engbsb/... already has real data under this exact
    # name -- only USJ_DIR (where we READ from) may vary, never EDITION (where we PUBLISH to).
    assert bt.EDITION == "engbsb"
    assert bt.LANG == "eng"


def test_map_target_skips_leading_title_rows_only_when_asked():
    rows = [{"bsb_sort": "1", "text": "A Psalm"}, {"bsb_sort": "2", "text": "of David"},
            {"bsb_sort": "3", "text": "The LORD"}, {"bsb_sort": "4", "text": "is my shepherd"}]
    toks = ["The", "LORD", "is", "my", "shepherd"]
    assert bt.map_target(rows, toks) is None                                    # default: the strict tiling
    assert bt.map_target(rows, toks, max_skip=6) == {"1": [], "2": [], "3": [0, 1], "4": [2, 3, 4]}
    assert bt.map_target(rows, ["Something", "else"], max_skip=6) is None       # skipping never forces a match


def test_map_target_ignores_markup_and_inline_joint_marker_in_cells():
    rows = [{"bsb_sort": "1", "text": "The sons of Shem"}, {"bsb_sort": "2", "text": "<p class=|list2|>[Elam]"},
            {"bsb_sort": "3", "text": "and vvv Aram"}]
    assert bt.map_target(rows, ["The", "sons", "of", "Shem", "Elam", "and", "Aram"]) == {"1": [0, 1, 2, 3], "2": [4], "3": [5, 6]}


def test_map_target_tolerant_allows_a_few_unclaimed_words_and_unmatched_rows_but_only_when_asked():
    rows = [{"bsb_sort": "1", "text": "Make five posts"}, {"bsb_sort": "2", "text": "[use] hooks"}, {"bsb_sort": "3", "text": "of gold"}]
    toks = ["Make", "five", "posts", "hooks", "of", "gold", "Then", "she", "tightened"]      # row 2's "use" is not in our text; 3 extra words
    assert bt.map_target(rows, toks) is None
    got = bt.map_target(rows, toks, tolerant=True)
    assert got is not None and got["1"] == [0, 1, 2] and got["3"] == [4, 5]
    far = toks + ["x"] * 20
    assert bt.map_target(rows, far, tolerant=True) is None                                  # too many unclaimed words: still refused
