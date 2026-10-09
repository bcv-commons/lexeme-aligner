"""E6 (xedition_verify.py): gold-free verification of span_extension against other editions' TEXT.
All synthetic, hand-computable."""
import json

import lexeme_aligner.eval.xedition_verify as xv


# --- geometry: which side did spanext widen? (independent of the `prior` label format) -------------------

def test_added_positions_before_and_after():
    assert xv.added_positions([5], [4, 5, 6]) == [("before", 4), ("after", 6)]


def test_added_positions_single_side():
    assert xv.added_positions([5, 6], [5, 6, 7]) == [("after", 7)]
    assert xv.added_positions([5], [4, 5]) == [("before", 4)]


def test_added_positions_nothing_added():
    assert xv.added_positions([5], [5]) == []


def test_neighbor():
    assert xv.neighbor((3, 4), "after") == 5
    assert xv.neighbor((3, 4), "before") == 2


# --- evaluate(): observed support and the null baseline --------------------------------------------------

def _pair(h, t_idx, method="eflomal", score=0.9):
    return {"h_idx": h, "t_idx": list(t_idx), "content": True, "score": score, "_method": method}


def _ref(tag, toks, spans):
    """toks: {ref: [...]}, spans: {ref: {h_idx: (t..)}} (already hi-conf)."""
    return xv.RefEdition(tag, toks, spans)


def test_evaluate_counts_a_matching_adjacent_word_as_support():
    base = {(1, 0): _pair(0, [1])}
    ext = {(1, 0): _pair(0, [1, 2], method="spanext")}         # spanext added position 2 ("of") AFTER
    test_toks = {1: ["the", "king", "of", "x"]}
    ref = _ref("R", {1: ["a", "king", "of", "y"]}, {1: {0: (1,)}})     # R also has "of" right after "king"
    st = xv.evaluate(base, ext, test_toks, [ref])
    assert (st.tokens, st.pairs, st.hits) == (1, 1, 1)
    assert st.support == 1.0


def test_evaluate_non_matching_neighbour_is_a_miss():
    base = {(1, 0): _pair(0, [1])}
    ext = {(1, 0): _pair(0, [1, 2], method="spanext")}
    test_toks = {1: ["the", "king", "of", "x"]}
    ref = _ref("R", {1: ["a", "king", "sword", "y"]}, {1: {0: (1,)}})
    st = xv.evaluate(base, ext, test_toks, [ref])
    assert (st.pairs, st.hits) == (1, 0)


def test_evaluate_null_baseline_uses_other_tokens_in_the_same_reference_verse():
    # 'of' sits after k's span (hit) AND after ONE of two other tokens' spans -> null rate 1/2.
    base = {(1, 0): _pair(0, [1])}
    ext = {(1, 0): _pair(0, [1, 2], method="spanext")}
    test_toks = {1: ["the", "king", "of", "x"]}
    rtoks = ["a", "king", "of", "lord", "of", "z", "end"]
    ref = _ref("R", {1: rtoks}, {1: {0: (1,), 1: (3,), 2: (5,)}})
    # other tokens: h1 span (3,) -> neighbour 4 = "of" (null hit); h2 span (5,) -> neighbour 6 = "end" (miss)
    st = xv.evaluate(base, ext, test_toks, [ref])
    assert (st.null_pairs, st.null_hits) == (2, 1)
    assert st.null == 0.5
    assert st.support == 1.0
    assert st.lift == 2.0


def test_evaluate_ignores_non_spanext_and_unreferenced_tokens():
    base = {(1, 0): _pair(0, [1]), (1, 1): _pair(1, [3])}
    ext = {(1, 0): _pair(0, [1], method="eflomal"),                  # not widened by spanext -> ignored
           (1, 1): _pair(1, [3, 4], method="spanext")}
    test_toks = {1: ["a", "b", "c", "d", "e"]}
    ref = _ref("R", {1: ["a", "b", "c"]}, {1: {0: (1,)}})            # R has no span for h_idx 1
    st = xv.evaluate(base, ext, test_toks, [ref])
    assert st.tokens == 0 and st.pairs == 0


def test_evaluate_skips_out_of_range_neighbour():
    base = {(1, 0): _pair(0, [1])}
    ext = {(1, 0): _pair(0, [1, 2], method="spanext")}
    test_toks = {1: ["a", "b", "c"]}
    ref = _ref("R", {1: ["a", "b"]}, {1: {0: (1,)}})                 # neighbour index 2 is off the end
    st = xv.evaluate(base, ext, test_toks, [ref])
    assert st.pairs == 0


def test_evaluate_two_sided_extension_counts_both_sides():
    base = {(1, 0): _pair(0, [2])}
    ext = {(1, 0): _pair(0, [1, 2, 3], method="spanext")}
    test_toks = {1: ["x", "the", "king", "of", "y"]}
    ref = _ref("R", {1: ["q", "the", "king", "of", "z"]}, {1: {0: (2,)}})
    st = xv.evaluate(base, ext, test_toks, [ref])
    assert st.pairs == 2 and st.hits == 2
    assert dict(st.by_side) == {"before": [1, 1], "after": [1, 1]}


def test_stats_merge_and_row():
    a = xv.Stats(tokens=1, pairs=2, hits=1, null_pairs=4, null_hits=1)
    b = xv.Stats(tokens=2, pairs=2, hits=2, null_pairs=4, null_hits=1)
    a.merge(b)
    assert (a.tokens, a.pairs, a.hits, a.null_pairs, a.null_hits) == (3, 4, 3, 8, 2)
    assert a.row()["support"] == 0.75 and a.row()["lift"] == 3.0


# --- load_layers(): first-wins, content-only, gz and plain ------------------------------------------------

def _write(tmp_path, method, tag, book, pairs, gz=False):
    import gzip
    fp = tmp_path / f"align_{method}_{tag}_{book}.jsonl{'.gz' if gz else ''}"
    line = json.dumps({"ref": 1, "book": book, "chapter": 1, "verse": 1, "pairs": pairs}) + "\n"
    (gzip.open(fp, "wt", encoding="utf-8") if gz else open(fp, "w", encoding="utf-8")).write(line)


def test_load_layers_first_method_wins_and_skips_noncontent(tmp_path):
    _write(tmp_path, "eflomal", "zz", "RUT", [
        {"h_idx": 0, "t_idx": [1], "content": True, "score": 0.9},
        {"h_idx": 1, "t_idx": [2], "content": False},
    ])
    _write(tmp_path, "gloss", "zz", "RUT", [{"h_idx": 0, "t_idx": [7], "content": True, "score": 1.0}], gz=True)
    d = xv.load_layers("zz", ("eflomal", "gloss"), tmp_path)
    assert d[(1, 0)]["t_idx"] == [1] and d[(1, 0)]["_method"] == "eflomal"
    assert (1, 1) not in d


def test_make_reference_keeps_only_hi_conf(tmp_path):
    layers = {(1, 0): {"t_idx": [3, 2], "score": 0.9}, (1, 1): {"t_idx": [4], "score": 0.6}}
    ref = xv.make_reference("R", {1: ["a"] * 6}, layers)
    assert ref.by_ref[1] == {0: (2, 3)}


# --- reference guards: script mismatch and near-duplicate texts ------------------------------------------

def test_dominant_script_latin_vs_devanagari():
    assert xv.dominant_script({1: ["king", "of", "israel"]}) == "LATIN"
    assert xv.dominant_script({1: ["राजा", "इस्राएल"]}) == "DEVANAGARI"


def test_reference_ok_rejects_other_script():
    ok, why = xv.reference_ok({1: ["king", "of"]}, {1: ["राजा", "का"]})
    assert (ok, why) == (False, "script")


def test_reference_ok_rejects_near_duplicate_text():
    a = {i: ["a", "b", str(i)] for i in range(10)}
    b = dict(a)                                   # 100% identical verses
    assert xv.reference_ok(a, b) == (False, "duplicate")


def test_reference_ok_accepts_a_genuinely_different_translation():
    a = {1: ["the", "king", "of", "x"], 2: ["and", "he", "went"]}
    b = {1: ["a", "king", "of", "y"], 2: ["then", "he", "left"]}
    assert xv.reference_ok(a, b) == (True, "")


def test_identical_verse_rate_partial():
    a = {1: ["x"], 2: ["y"], 3: ["z"]}
    b = {1: ["x"], 2: ["q"], 3: ["z"]}
    assert abs(xv.identical_verse_rate(a, b) - 2 / 3) < 1e-9


def test_verdict_bands_and_gold_calibration_points():
    # the nine gold-judged editions must land on the side gold says (the calibration contract)
    for sup in (.215, .245, .270, .301):                  # gold-negative
        assert xv.verdict(sup, 5000) == "flip"
    for sup in (.472, .485, .496, .556, .703):            # gold-positive
        assert xv.verdict(sup, 5000) == "keep"
    assert xv.verdict(.36, 5000) == "uncertain"
    assert xv.verdict(.05, 100) == "insufficient"
    assert xv.verdict(float("nan"), 0) == "insufficient"


def test_record_and_apply_flag_round_trip(tmp_path):
    import json
    from lexeme_aligner.eval import xedition_verify as xv
    e6, flags = tmp_path / "e6.json", tmp_path / "flags.json"
    flags.write_text(json.dumps({"_doc": "x"}), encoding="utf-8")
    bad = xv.record("zz", "zz_a", ["zz_b", "zz_c"], {"pairs": 500, "support": 0.2, "null": 0.03, "lift": 6.7},
                    {"zz_c": "near-duplicate"}, "2026-10-08", e6)
    assert bad["verdict"] == "flip" and bad["refs"] == ["zz_b"]
    assert xv.apply_flag("zz_a", bad, flags) == "flipped"
    assert json.loads(flags.read_text())["zz_a"]["base_mechanisms"] is False
    good = xv.record("zz", "zz_a", ["zz_b"], {"pairs": 500, "support": 0.5, "null": 0.03, "lift": 16.0}, {}, "2026-10-09", e6)
    assert xv.apply_flag("zz_a", good, flags) == "reverted an earlier E6 flip"
    assert "zz_a" not in json.loads(flags.read_text())
    assert json.loads(e6.read_text())["editions"]["zz_a"]["date"] == "2026-10-09"
