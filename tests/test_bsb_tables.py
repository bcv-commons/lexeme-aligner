"""Offline tests for bsb_tables.py: column parsing, HTML → structure and back, empty-row retention, sort
keys, the 1:n source mapping, the target letters-walk, and the 23-column round trip — all on synthetic
rows, no vendor file needed."""
import lexeme_aligner.bsb_tables as bt


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


def test_map_source_fused_consecutive_same_strong_attaches_to_same_token():
    # spine fuses "Ἰσαάκ, Ἰσαὰκ" into ONE token; BSB has two rows -> second attaches to the same idx, flagged
    rows = [bt.parse_row(_cells(language="Greek", heb_sort="999999", grk_sort="1", bsb_sort="1", base="Ἰσαάκ", str_grk="2464", text=" Isaac ")),
            bt.parse_row(_cells(language="Greek", heb_sort="999999", grk_sort="2", bsb_sort="2", base="Ἰσαὰκ", str_grk="2464", text=" Isaac ")),
            bt.parse_row(_cells(language="Greek", heb_sort="999999", grk_sort="3", bsb_sort="3", base="δὲ", str_grk="1161", text=" and "))]
    m = bt.map_source(rows, [(0, "G2464", "Ἰσαάκ Ἰσαὰκ"), (1, "G1161", "δὲ")])
    assert m["1"] == {"h_idx_key": 0, "h_idx": [0], "match": "strong"}
    assert m["2"] == {"h_idx_key": 0, "h_idx": [0], "match": "fused"}
    assert m["3"] == {"h_idx_key": 1, "h_idx": [1], "match": "strong"}


def test_map_source_fusion_does_not_consume_a_spine_occurrence():
    # GEN 6:9 shape: BSB rows Noah, Noah, ..., Noah (3) vs spine "נֹחַ נֹחַ"(fused, 1 token) ... נֹחַ (2 tokens)
    rows = [bt.parse_row(_cells(heb_sort="1", bsb_sort="1", base="נֹחַ", str_heb="5146", text=" of Noah ")),
            bt.parse_row(_cells(heb_sort="2", bsb_sort="2", base="נֹחַ", str_heb="5146", text=" Noah ")),
            bt.parse_row(_cells(heb_sort="3", bsb_sort="3", base="הָיָה", str_heb="1961", text=" was ")),
            bt.parse_row(_cells(heb_sort="4", bsb_sort="4", base="נֹחַ", str_heb="5146", text=" Noah "))]
    m = bt.map_source(rows, [(0, "H5146", "נֹ֔חַ נֹ֗חַ"), (1, "H1961", "הָיָה"), (2, "H5146", "נֹֽחַ")])
    assert m["1"]["match"] == "strong" and m["1"]["h_idx_key"] == 0
    assert m["2"] == {"h_idx_key": 0, "h_idx": [0], "match": "fused"}
    assert m["4"] == {"h_idx_key": 2, "h_idx": [2], "match": "strong"}   # the 2nd spine token, not lost


def test_map_source_compound_name_with_two_bsb_strongs_attaches_by_exact_surface_word():
    # BSB: בֵּית (H1004) + אֵל (H1008); spine: ONE token "בֵּית אֵל" H1008 -> בֵּית fused by surface equality, not guessed
    rows = [bt.parse_row(_cells(heb_sort="1", bsb_sort="1", base="בֵּֽית־", str_heb="1004", text=" vvv ")),
            bt.parse_row(_cells(heb_sort="2", bsb_sort="2", base="אֵ֖ל", str_heb="1008", text=" Bethel "))]
    m = bt.map_source(rows, [(0, "H1008", "בֵּֽית אֵ֖ל")])
    assert m["2"] == {"h_idx_key": 0, "h_idx": [0], "match": "strong"}
    assert m["1"] == {"h_idx_key": 0, "h_idx": [0], "match": "fused"}
    # a row whose base is NOT a word of the fused surface stays 'none' even when adjacent
    m2 = bt.map_source([bt.parse_row(_cells(heb_sort="1", bsb_sort="1", base="שָׁלוֹם", str_heb="1004", text=" x ")), rows[1]],
                       [(0, "H1008", "בֵּֽית אֵ֖ל")])
    assert m2["1"]["match"] == "none"


def test_map_source_one_bsb_row_to_prefix_plus_word():
    # spine: בְּ(H0871) רֵאשִׁית(H7225) | BSB: one row "בְּרֵאשִׁית" strong 7225 -> keyed to idx 1, prefix idx 0 attached
    rows = [bt.parse_row(_cells(heb_sort="1", bsb_sort="1", base="בְּרֵאשִׁ֖ית", str_heb="7225", text=" In the beginning ")),
            bt.parse_row(_cells(heb_sort="3", bsb_sort="2", base="אֱלֹהִ֑ים", str_heb="430", text=" God ")),
            bt.parse_row(_cells(heb_sort="2", bsb_sort="3", base="בָּרָ֣א", str_heb="1254", text=" created "))]
    spine = [(0, "H0871"), (1, "H7225"), (2, "H1254"), (3, "H0430")]
    m = bt.map_source(rows, spine)
    assert m["1"] == {"h_idx_key": 1, "h_idx": [0, 1], "match": "strong"}
    assert m["3"] == {"h_idx_key": 2, "h_idx": [2], "match": "strong"}
    assert m["2"] == {"h_idx_key": 3, "h_idx": [3], "match": "strong"}


def test_map_source_kth_occurrence_and_strongless_row_positional():
    # spine: וְ(H2050) אֵת(H0853) הָ(H1886) אָרֶץ(H0776) לָ(H0871) הֶם(H1886d->H1886) ; BSB rows: וְאֵת/853, הָאָרֶץ/776, לָהֶם/(no strong)
    rows = [bt.parse_row(_cells(heb_sort="1", bsb_sort="1", base="וְאֵת", str_heb="853", text=" and ")),
            bt.parse_row(_cells(heb_sort="2", bsb_sort="2", base="הָאָרֶץ", str_heb="776", text=" the earth ")),
            bt.parse_row(_cells(heb_sort="3", bsb_sort="3", base="לָהֶם", parsing="Prep | 3mp", text=""))]
    spine = [(0, "H2050"), (1, "H0853"), (2, "H1886"), (3, "H0776"), (4, "H0871"), (5, "H1886")]
    m = bt.map_source(rows, spine)
    assert m["1"] == {"h_idx_key": 1, "h_idx": [0, 1], "match": "strong"}
    # trailing unkeyed tokens 4,5 first fall to the last keyed row (suffix convention), then the strongless
    # row that FOLLOWS it in source order claims them positionally — recorded as such, not as a keyed match
    assert m["2"] == {"h_idx_key": 3, "h_idx": [2, 3], "match": "strong"}
    assert m["3"] == {"h_idx_key": None, "h_idx": [4, 5], "match": "positional"}


def test_map_source_unmatched_strong_is_none_not_guessed():
    rows = [bt.parse_row(_cells(heb_sort="1", bsb_sort="1", base="x", str_heb="9999", text=" x "))]
    m = bt.map_source(rows, [(0, "H0430")])
    assert m["1"] == {"h_idx_key": None, "h_idx": [], "match": "none"}


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
