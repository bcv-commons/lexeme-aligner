"""fullalign_compact.py: the container codec on synthetic verses (no spine, no files except tmp_path)."""
import collections

import pytest

import lexeme_aligner.fullalign_compact as fc
from lexeme_aligner.hebrew_source import HebToken

LETTERS = lambda s: "".join(c for c in s.lower() if c.isalpha())          # noqa: E731


def tok(idx, strong, content=True, key=None):
    t = HebToken(idx, f"w{idx}", strong, f"hbo:{strong[1:]}" if strong else None, f"l{idx}", None, content)
    t.keys = [key] if key else []
    return t


def group(ref="RUT 1:1", n_content=3):
    # idx 0: function word, 1-2: content, 3: function word, 4: a token of a folded-in verse
    members = [(1, tok(0, "H9001", False, "080010010011")), (1, tok(1, "H0001", True, "080010010021")),
               (1, tok(2, "H0002", True, "080010010031")), (1, tok(3, "H9002", False, "080010010041")),
               (2, tok(4, "H0003", True, "080010020011"))]
    g = fc.Group(ref, 1, members)
    g.toks = ["a", "b", "c", "d", "e", "f"]
    g.tprefix = "08001001"
    return g


def test_span_codec_round_trips_every_form():
    for t in ([5], [5, 6, 7], [5, 9], [3, 1, 2], [], None):
        assert fc.dec_span(fc.enc_span(t)) == t
    assert fc.enc_span([5, 6, 7]) == "5-7" and fc.enc_span([5, 9]) == "5,9" and fc.enc_span([3, 1, 2]) == "L3,1,2"
    assert fc.enc_span(None) == "~" and fc.enc_span([]) == "!"
    assert fc.dec_marker(fc.enc_marker(-1, "u")) == (-1, "u") and fc.dec_marker("@6e") == (6, "e")


def test_group_slots_split_content_function_and_folded_verse_tokens():
    g = group()
    assert g.slot_of == {0: ("f", 0), 1: ("c", 0), 2: ("c", 1), 3: ("f", 1), 4: ("p", 4)}
    assert g.h_of[("c", 1)] == 2 and (g.n_content, g.n_fn) == (2, 2)


def test_bracket_roles_follow_the_cell_letters():
    toks = ["The", "sons", "of", "Noah"]
    assert fc.roles_of_positions("[The sons of] Noah", [0, 1, 2, 3], toks, LETTERS) == {0: "s", 1: "s", 2: "s"}
    assert fc.roles_of_positions("And {I will} never", [0, 1, 2, 3], ["And", "I", "will", "never"], LETTERS) == {1: "i", 2: "i"}
    assert fc.roles_of_positions(" In the days ", [0, 1, 2], ["In", "the", "days"], LETTERS) == {}


def _bsb_row(h, key, t, cell, match="strong", strong="H0001"):
    return {"h_idx": h, "h_idx_key": key, "t_idx": t, "target": cell, "method": "manual", "score": None, "strong": strong,
            "attribution": {"source": "bsb-tables", "kind": "manual", "match": match}}


BASE = {"source": "bsb-tables", "kind": "manual", "match": "strong"}


def test_bsb_rows_become_markers_joint_spans_and_role_flags():
    g = group()
    g.toks = ["In", "the", "days", "of", "Ruth", "x"]
    rows = [_bsb_row([1], 1, [0, 1, 2], " In the days "),
            _bsb_row([0], 0, [], " - ", strong="H9001"),                          # untranslated
            _bsb_row([2], 2, [], " vvv "),                                          # joint with the next placed row
            _bsb_row([3], 3, [3, 4], " [of] Ruth ", strong="H9002"),                # 'of' supplied
            _bsb_row([4], 4, None, " ?? ", strong="H0003")]                         # unplaced
    cr = fc.bsb_crows(rows, g, BASE, LETTERS)
    assert cr[0].span == [0, 1, 2] and cr[0].key == 0 and cr[0].mark is None
    assert cr[1].mark == (2, "u") and cr[1].span == []                              # after the last placed word of row 0
    assert cr[2].joint and cr[2].span == [4]                                        # the next placed row's span, supplied removed
    assert cr[3].span == [4] and cr[3].sup == [3]
    assert cr[4].span is None


def _roundtrip(crows_by_ref, refs, groups, has_ids=False, hints=None):
    enc = fc.LayerEncoder("L", "manual", BASE, has_ids)
    main, meta = enc.encode_book(crows_by_ref, refs, groups, hints)
    main, meta = __import__("json").loads(__import__("json").dumps(main)), __import__("json").loads(__import__("json").dumps(meta))
    dec = fc.LayerDecoder(enc.layer_json()).decode_book(main, meta, refs, groups)
    return enc, main, meta, dec


def _canon(rows):
    return collections.Counter(c.canon() for c in rows)


def test_statistical_rows_round_trip_with_compact_hint_and_keep_the_hinted_main_entries():
    g = group()
    P = lambda m, s: (m, s, None, None, "{}")                                        # noqa: E731
    rows = [fc.CRow("RUT 1:1", [("c", 0)], span=[0], prof=P("eflomal", 0.9)),
            fc.CRow("RUT 1:1", [("c", 0)], span=[0, 1], prof=P("gloss", 0.5)),        # a second, losing row on the same slot
            fc.CRow("RUT 1:1", [("c", 1)], span=[2], prof=P("eflomal", 0.6)),
            fc.CRow("RUT 1:1", [("f", 0)], span=[3], prof=P("eflomal", 0.9)),
            fc.CRow("RUT 1:1", [("c", 1)], span=None, prof=P("llm", None))]          # unplaced
    refs = ["RUT 1:1", "RUT 1:2"]
    enc, main, meta, dec = _roundtrip({"RUT 1:1": rows}, refs, {"RUT 1:1": g}, hints={"RUT 1:1": {0: [0], 1: [2]}})
    assert main[0] == "0:0 1:2" and main[1] == ""                                     # compact's own entries, untouched
    assert _canon(dec) == _canon(rows)
    assert meta["fn"][0] == "0:3" and meta["rows"][0]                                  # fn channel + the leftover rows


def test_a_compact_entry_without_a_matching_row_is_kept_as_a_view_and_creates_no_row():
    g = group()
    refs = ["RUT 1:1"]
    enc, main, meta, dec = _roundtrip({}, refs, {"RUT 1:1": g}, hints={"RUT 1:1": {0: [5]}})
    assert main[0] == "0:5" and meta["wp"][0] == "." and dec == []


def test_clear_rows_carry_ids_and_derive_the_default_source_id():
    g = group()
    P = ("manual", None, None, None, "{}")
    r1 = fc.CRow("RUT 1:1", [("c", 0)], span=[0, 1], prof=P, sid="o080010010021", tids=[1, 2])      # default sid
    r2 = fc.CRow("RUT 1:1", [("c", 1)], span=[2], prof=P, sid="o080010019999", tids=[3])            # an override
    r3 = fc.CRow("RUT 1:1", [("f", 1)], span=[3], prof=P, sid="o080010010041", tids=[4])
    enc, main, meta, dec = _roundtrip({"RUT 1:1": [r1, r2, r3]}, ["RUT 1:1"], {"RUT 1:1": g}, has_ids=True)
    assert _canon(dec) == _canon([r1, r2, r3])
    assert "S" not in meta["wx"][0].replace("S" + "o080010019999", "")                  # only the override is spelled out


def test_bsb_rows_round_trip_including_multi_source_markers_joint_roles_and_off_index_rows():
    g = group()
    P = ("manual", None, None, None, "{}")
    rows = [fc.CRow("RUT 1:1", [("f", 0), ("c", 0)], key=1, span=[0, 1], prof=P),
            fc.CRow("RUT 1:1", [("c", 1)], key=0, span=[], mark=(1, "e"), prof=P),
            fc.CRow("RUT 1:1", [("c", 1)], key=0, span=[3], joint=True, prof=P),
            fc.CRow("RUT 1:1", [("f", 1)], key=0, span=[4], sup=[2], inf=[5], prof=P, tstrong="-"),
            fc.CRow("RUT 1:1", [("c", 0)], key=None, span=None, prof=P),                       # adjacency-attached, unplaced
            fc.CRow("RUT 9:9", [], span=[], mark=(-1, "u"), prof=P, tstrong="H0001")]          # no spine token at all
    refs = ["RUT 1:1", "RUT 1:2"]
    enc, main, meta, dec = _roundtrip({"RUT 1:1": rows[:5], "RUT 9:9": rows[5:]}, refs, {"RUT 1:1": g})
    assert _canon(dec) == _canon(rows)
    assert meta["off"] == {"RUT 9:9": ["-:@-1u:" + enc.profiles.char_of[P] + ":TH0001"]}
    assert main[0] == "0:0-1" and "." in meta["wp"][0]                                     # the multi-source row shows as a view
    assert "1:3" not in main[0] and "f" not in meta["rows"][0].split()[2][:1]              # the joint row (span [3]) is NOT in main


def test_a_row_with_source_tokens_but_no_spine_group_is_an_error():
    enc = fc.LayerEncoder("L", "manual", BASE, False)
    with pytest.raises(KeyError):
        enc.encode_book({"RUT 2:2": [fc.CRow("RUT 2:2", [("c", 0)], span=[1], prof=("m", None, None, None, "{}"))]},
                        ["RUT 1:1"], {}, None)


def test_meta_merge_adds_channels_and_refuses_to_change_an_existing_one():
    assert fc.merge_meta({"method": ["e"]}, {"fn": ["0:1"], "wp": None}) == {"method": ["e"], "fn": ["0:1"]}
    with pytest.raises(ValueError):
        fc.merge_meta({"fn": ["0:1"]}, {"fn": ["0:2"]})


def test_numbers_in_a_prior_are_row_arguments_so_the_profile_table_stays_small():
    assert fc.split_prior("spanext_name_before:12+spanext_noun_before:3") == ("spanext_name_before:#+spanext_noun_before:#", [12, 3])
    assert fc.split_prior("cross_edition") == ("cross_edition", []) and fc.split_prior(None) == (None, [])
    assert fc.join_prior("spanext_name_before:#+spanext_noun_before:#", [12, 3]) == "spanext_name_before:12+spanext_noun_before:3"
    g = group()
    attr = {"source": "lexeme-aligner", "kind": "statistical", "license": "CC0-1.0"}
    row = lambda h, t, prior: {"h_idx": h, "t_idx": t, "method": "spanext", "score": 0.9, "prior": prior,      # noqa: E731
                               "extra": None, "attribution": attr}
    rows = [fc.simple_crow(row(1, [0, 1], f"spanext_name_before:{n}"), g, attr, False) for n in (1, 7)]
    rows.append(fc.simple_crow(row(2, [2, 3], "spanext_noun_before:3"), g, attr, False))
    rows.append(fc.simple_crow(row(3, [4], "spanext_noun_before:4"), g, attr, False))      # a function word: the fn channel
    enc, main, meta, dec = _roundtrip({"RUT 1:1": rows}, ["RUT 1:1"], {"RUT 1:1": g})
    assert len(enc.profiles.of_char) == 2                                                 # two templates, not four profiles
    assert _canon(dec) == _canon(rows)
    assert sorted(c.prior() for c in dec) == ["spanext_name_before:1", "spanext_name_before:7", "spanext_noun_before:3",
                                              "spanext_noun_before:4"]
    assert meta["wx"] is not None and "P" in meta["wx"][0]                                # no gold ids, but arguments to carry


def test_a_vetoed_function_word_row_is_neither_claimed_nor_a_view_and_survives_in_rows():
    g = group()
    P = lambda m, e=None: (m, 0.9, None, e, "{}")                                      # noqa: E731
    vetoed = fc.CRow("RUT 1:1", [("f", 0)], span=[4], prof=P("eflomal", fc.with_veto(None, "suffix_pronoun")))
    rows = [fc.CRow("RUT 1:1", [("c", 0)], span=[0], prof=P("eflomal")), vetoed,
            fc.CRow("RUT 1:1", [("f", 1)], span=[5], prof=P("eflomal"))]
    enc, main, meta, dec = _roundtrip({"RUT 1:1": rows}, ["RUT 1:1"], {"RUT 1:1": g})
    assert meta["fn"][0] == "1:5"                                   # f0 has no entry at all (no winner, no view)
    assert len(meta["rows"][0].split()) == 1 and meta["rows"][0].startswith("f0:4:")
    assert _canon(dec) == _canon(rows)
    assert fc.is_vetoed(vetoed) and fc.with_veto('{"light": true}', "prefix_prep") == '{"light": true, "veto": "prefix_prep"}'


def test_a_whole_word_gold_row_is_one_row_from_every_morpheme_keyed_by_the_stem():
    g = group()
    attr = {"source": "clear", "kind": "manual", "license": "CC-BY-4.0"}
    row = {"h_idx": 1, "t_idx": [0, 1], "method": "manual", "score": None, "prior": None,
           "extra": '{"word_h_idx": [0, 1]}', "attribution": attr}
    c = fc.simple_crow(row, g, attr, False)
    assert c.src == [("f", 0), ("c", 0)] and c.key == 1 and c.prof[3] is None      # word_h_idx lives in the srcs, not the profile
    enc, main, meta, dec = _roundtrip({"RUT 1:1": [c]}, ["RUT 1:1"], {"RUT 1:1": g})
    assert main[0] == "0:0-1" and meta["wp"][0] == "." and meta["fn"][0] == "0:0-1"   # views on both slots; the row is in `rows`
    assert _canon(dec) == _canon([c])
