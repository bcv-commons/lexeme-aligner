"""fullalign_compact.py, layer version 3: decision lists (! u e +) and keyed layers (agreed with bsb-add-books 2026-10-16)."""
import collections
import json

import lexeme_aligner.fullalign_compact as fc
from test_fullalign_compact import BASE, LETTERS, _bsb_row, group

P = ("manual", None, None, None, "{}")


def _rt(crows_by_ref, refs, groups, **kw):
    enc = fc.LayerEncoder("L", "manual", BASE, False, **kw)
    main, meta = enc.encode_book(crows_by_ref, refs, groups)
    main, meta, lj = json.loads(json.dumps(main)), json.loads(json.dumps(meta)), json.loads(json.dumps(enc.layer_json()))
    return enc, main, meta, fc.LayerDecoder(lj).decode_book(main, meta, refs, groups)


def _canon(rows):
    return collections.Counter(c.canon() for c in rows)


def test_items_codec_writes_ranges_and_lone_items_with_an_extra():
    items = [(9, "10"), (0, None), (4, None), (5, None), (6, None), (12, None), (13, None)]
    assert fc.enc_items(items, "@") == "0,4-6,9@10,12,13"
    assert sorted(fc.dec_items("0,4-6,9@10,12,13", "@")) == sorted(items)
    assert fc.enc_items([(1, "5"), (10, None), (29, None)], ">") == "1>5,10,29"
    assert fc.dec_items("3@-1", "@") == [(3, "-1")]
    assert fc.parse_owner("f3") == ("f", 3) and fc.owner_str(("c", 5)) == "5"


def test_default_slot_is_after_the_nearest_preceding_token_the_strings_show_positions_for():
    order = {("f", 0): 0, ("c", 0): 1, ("c", 1): 2, ("f", 1): 3}        # source order
    shown = {("c", 0): {2}, ("c", 1): {0, 1}}                           # c1: its link 0 plus the `+1>1` word
    assert fc.default_slot(shown, order, ("f", 1)) == 1
    assert fc.default_slot(shown, order, ("c", 1)) == 2
    assert fc.default_slot(shown, order, ("f", 0)) == -1


def test_a_joint_row_shows_nothing_in_the_strings_so_it_does_not_move_the_default_slot():
    g = group()
    rows = [fc.CRow("RUT 1:1", [("c", 0)], span=[0], prof=P),
            fc.CRow("RUT 1:1", [("c", 1)], key=None, span=[3], joint=True, prof=P),   # a row only, no view
            fc.CRow("RUT 1:1", [("f", 1)], span=[], mark=(0, "u"), prof=P)]
    enc, main, meta, dec = _rt({"RUT 1:1": rows}, ["RUT 1:1"], {"RUT 1:1": g}, lists=True)
    assert meta["fn"] == ["u1"]                                         # default = after c0's word 0, not after the joint word 3
    assert _canon(dec) == _canon(rows)


def test_a_fully_decided_verse_is_links_plus_lists_and_round_trips():
    g = group()
    rows = [fc.CRow("RUT 1:1", [("c", 0)], span=[2], prof=P),
            fc.CRow("RUT 1:1", [("c", 1)], span=[0], sup=[1], prof=P),                  # word 1 added for c1
            fc.CRow("RUT 1:1", [("f", 0)], span=[], prof=P),                            # no target word
            fc.CRow("RUT 1:1", [("f", 1)], span=[], mark=(4, "u"), prof=P),             # untranslated, slot differs from default
            fc.CRow("RUT 1:1", [], span=[], sup=[5], prof=P)]                           # added for the grammar
    enc, main, meta, dec = _rt({"RUT 1:1": rows}, ["RUT 1:1"], {"RUT 1:1": g}, lists=True)
    assert main == ["0:2 1:0"]
    assert meta["fn"] == ["!0 u1@4 +1>1 +5"]
    assert meta["rows"] is None and meta["off"] is None                                  # nothing left over
    assert meta["fp"][0][2] == fc.NO_PROFILE and len(meta["fp"][0]) == 4
    assert _canon(dec) == _canon(rows)


def test_a_marker_at_the_default_slot_carries_no_slot():
    g = group()
    rows = [fc.CRow("RUT 1:1", [("c", 1)], span=[0, 1], prof=P), fc.CRow("RUT 1:1", [("f", 1)], span=[], mark=(1, "e"), prof=P)]
    enc, main, meta, dec = _rt({"RUT 1:1": rows}, ["RUT 1:1"], {"RUT 1:1": g}, lists=True)
    assert meta["fn"] == ["e1"] and _canon(dec) == _canon(rows)


def test_without_lists_the_old_encoding_is_unchanged():
    g = group()
    rows = [fc.CRow("RUT 1:1", [("c", 0)], span=[2], prof=P), fc.CRow("RUT 1:1", [("f", 0)], span=[], prof=P)]
    enc, main, meta, dec = _rt({"RUT 1:1": rows}, ["RUT 1:1"], {"RUT 1:1": g})
    assert meta["fn"] is None and meta["rows"][0].startswith("f0:!:")
    assert enc.layer_json()["version"] == 2 and "lists" not in enc.layer_json()
    assert _canon(dec) == _canon(rows)


def test_bsb_rows_in_a_keyed_lists_layer_are_claimed_and_the_rest_stays_in_rows():
    g = group()
    g.toks = ["In", "the", "days", "of", "Ruth", "x"]
    vrows = [_bsb_row([1], 1, [0, 1, 2], " In the days "),
             _bsb_row([0], 0, [], " - ", strong="H9001"),                 # untranslated after word 2; default for f0 is -1
             _bsb_row([2], 2, [], " vvv "),                               # joint: stays a row
             _bsb_row([3], 3, [3, 4], " [of] Ruth ", strong="H9002"),     # 'of' supplied -> `+3>f1`
             _bsb_row([4], 4, None, " ?? ", strong="H0003")]              # pooled-verse token, unplaced: stays a row
    cr = fc.bsb_crows(vrows, g, BASE, LETTERS)
    enc, main, meta, dec = _rt({"RUT 1:1": cr}, ["RUT 1:1"], {"RUT 1:1": g}, lists=True, keyed=True)
    assert main == ["0:0-2"] and meta["wp"][0] != "."                      # claimed, not a view
    assert meta["fn"] == ["1:4 u0@2 +3>f1"]
    assert len(meta["rows"][0].split()) == 2 and ":k" not in meta["rows"][0]   # key 0 is implied
    assert _canon(dec) == _canon(cr)
    lj = enc.layer_json()
    assert lj["version"] == 3 and lj["keyed"] and lj["lists"]


def test_a_keyed_single_source_row_without_a_keyed_token_says_k_minus():
    g = group()
    rows = [fc.CRow("RUT 1:1", [("c", 0)], key=None, span=None, prof=P), fc.CRow("RUT 1:1", [("c", 1)], key=0, span=None, prof=P)]
    enc, main, meta, dec = _rt({"RUT 1:1": rows}, ["RUT 1:1"], {"RUT 1:1": g}, lists=True, keyed=True)
    assert sorted(meta["rows"][0].split()) == sorted(["c0:~:a:k-", "c1:~:a"])
    assert _canon(dec) == _canon(rows)


def test_multi_source_markers_show_list_views_and_their_rows_keep_the_data():
    g = group()
    rows = [fc.CRow("RUT 1:1", [("c", 1)], key=0, span=[0], prof=P),
            fc.CRow("RUT 1:1", [("f", 0), ("c", 0)], key=1, span=[], mark=(0, "e"), prof=P)]
    enc, main, meta, dec = _rt({"RUT 1:1": rows}, ["RUT 1:1"], {"RUT 1:1": g}, lists=True, keyed=True)
    assert main[0] == "1:0 e0@0" and meta["fn"][0] == "e0@0"                 # a view on each slot (nothing precedes: default -1)
    assert meta["wp"][0][1] == "." and meta["fp"][0] == "."
    assert meta["rows"][0].startswith("f0,c0:@0e:")
    assert _canon(dec) == _canon(rows)


def test_the_index_pin_is_recorded_in_layer_json():
    enc = fc.LayerEncoder("L", "manual", BASE, False, lists=True, index_sha256="2a5007e4")
    assert enc.layer_json()["spine_index_sha256"] == "2a5007e4"
