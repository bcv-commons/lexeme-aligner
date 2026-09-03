"""Slot-allocation tests — protection #1 (docs/pipeline-overview.md).

The pipeline's recurring failure is a function-word source token holding a target position a content
token needed. These lock down the two mechanisms that decide who gets a position, so a future change
to the symmetrizer can't silently reopen it.
"""
import pytest

from lexeme_aligner.eflomal_align import _content_priority, _grow_diag_final_and
from lexeme_aligner.hebrew_source import HebToken


def tok(idx, is_content, strong="G0001"):
    return HebToken(idx, f"w{idx}", strong, f"grc:{idx}", f"l{idx}", None, is_content)


# ── the symmetrizer we are protecting ──────────────────────────────────────────────────

def test_gdfa_keeps_the_intersection():
    fwd, rev = {(0, 0), (1, 1)}, {(0, 0), (1, 2)}
    sym, inter = _grow_diag_final_and(fwd, rev, 2, 3)
    assert inter == {(0, 0)}
    assert {(0, 0)} <= sym


def test_gdfa_final_and_gives_a_free_slot_to_whoever_is_free():
    """The behaviour protection #1 exists to constrain: the last pass hands a free target position to
    any free source token, with no content preference and no score threshold."""
    fwd, rev = {(0, 0)}, {(0, 0), (1, 1)}
    sym, _ = _grow_diag_final_and(fwd, rev, 2, 2)
    assert (1, 1) in sym


# ── _content_priority ──────────────────────────────────────────────────────────────────

def test_reassigns_a_noncontent_held_slot_to_an_unaligned_content_token():
    src = [tok(0, False), tok(1, True)]          # 0 = article-like, 1 = real content
    sym = {(0, 5)}                                # the function word holds position 5
    union = sym | {(1, 5)}                        # ...but the model also proposed it for content
    out, moved = _content_priority(sym, union, src)
    assert moved == 1
    assert (1, 5) in out and (0, 5) not in out


def test_never_takes_a_slot_another_content_token_holds():
    src = [tok(0, True), tok(1, True)]
    sym = {(0, 5)}
    union = sym | {(1, 5)}
    out, moved = _content_priority(sym, union, src)
    assert moved == 0 and out == sym


def test_does_not_let_an_already_aligned_content_token_grow():
    """Growing an existing span is span extension — a different question with its own measurements."""
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5), (1, 2)}                        # content token already has position 2
    union = sym | {(1, 5)}
    out, moved = _content_priority(sym, union, src)
    assert moved == 0 and out == sym


def test_requires_the_model_to_have_proposed_the_link():
    """Reallocation may only use links eflomal itself put in the union — never invent one."""
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5)}
    out, moved = _content_priority(sym, union=sym, src_toks=src)
    assert moved == 0 and out == sym


def test_is_a_noop_when_every_slot_has_a_content_holder():
    src = [tok(0, True)]
    sym = {(0, 1)}
    out, moved = _content_priority(sym, sym | {(0, 2)}, src)
    assert moved == 0 and out is sym


def test_one_content_token_cannot_take_two_reassignable_slots():
    src = [tok(0, False), tok(1, False), tok(2, True)]
    sym = {(0, 3), (1, 4)}
    union = sym | {(2, 3), (2, 4)}
    out, moved = _content_priority(sym, union, src)
    assert moved == 1
    assert len([t for s, t in out if s == 2]) == 1


# ── _longest_contiguous (protection #2) ────────────────────────────────────────────────

from lexeme_aligner.eflomal_align import _longest_contiguous


def test_keeps_the_only_run_untouched():
    assert _longest_contiguous([3, 4, 5], set()) == [3, 4, 5]


def test_drops_the_scattered_outlier():
    assert _longest_contiguous([3, 4, 9], set()) == [3, 4]


def test_prefers_the_run_holding_an_intersection_backed_link():
    """A shorter run both alignment directions agreed on beats a longer, weaker one."""
    assert _longest_contiguous([1, 5, 6], inter_ts={1}) == [1]


def test_falls_back_to_length_then_earliest_when_no_intersection():
    assert _longest_contiguous([1, 5, 6], inter_ts=set()) == [5, 6]
    assert _longest_contiguous([1, 2, 8, 9], inter_ts=set()) == [1, 2]


def test_single_position_is_unchanged():
    assert _longest_contiguous([7], {7}) == [7]


# ── _content_priority level 2: displacement (protection #1, risky tier) ────────────────

def test_level2_displaces_a_weak_link_for_a_function_word_held_slot():
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5), (1, 2)}                       # content token holds 2, but only weakly
    union = sym | {(1, 5)}
    out, moved = _content_priority(sym, union, src, inter=frozenset(), displace_weak=True)
    assert moved == 1
    assert (1, 5) in out and (1, 2) not in out and (0, 5) not in out


def test_level2_will_not_displace_an_intersection_backed_link():
    """A link both alignment directions agreed on is never traded away."""
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5), (1, 2)}
    union = sym | {(1, 5)}
    out, moved = _content_priority(sym, union, src, inter=frozenset({(1, 2)}), displace_weak=True)
    assert moved == 0 and out == sym


def test_level2_displaces_rather_than_adds():
    """Displacement must never grow a span — that is the trap that made content-only look good."""
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5), (1, 2)}
    union = sym | {(1, 5)}
    out, _ = _content_priority(sym, union, src, inter=frozenset(), displace_weak=True)
    assert len([t for s, t in out if s == 1]) == 1


def test_level1_still_refuses_to_displace():
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5), (1, 2)}
    union = sym | {(1, 5)}
    out, moved = _content_priority(sym, union, src, inter=frozenset(), displace_weak=False)
    assert moved == 0 and out == sym


# ── default posture (protection #2 is ON by default since 2026-09-01) ─────────────────

def test_contiguity_protection_is_on_by_default():
    """Guards the default flip: a regression here silently republishes scattered spans."""
    from lexeme_aligner.eflomal_align import EflomalAligner
    assert EflomalAligner().contiguous_only is True
    assert EflomalAligner().displace_weak is False
    assert EflomalAligner().content_only is False


# ── gloss source-gated stopword filter (protection #4, ON by default) ─────────────────

class _Priors:
    def __init__(self, m): self.m = m
    def lookup(self, tok): return self.m.get(tok.lexeme, [])


class _SW:
    def __init__(self, words): self.words = words
    def is_function(self, w): return w.lower() in self.words


def _gloss(heb, tokens, priors, **kw):
    from lexeme_aligner.gloss_align import align_verse
    return align_verse(heb, tokens, _Priors(priors), "xx", **kw)


def test_source_gate_blocks_a_content_lexeme_from_a_stopword_target():
    h = tok(0, True); h.lexeme = "grc:1"
    got = _gloss([h], ["de"], {"grc:1": [("de",)]},
                 stopwords=_SW({"de"}), source_gated_stopwords=True)
    assert got == []


def test_source_gate_still_allows_a_FUNCTION_lexeme_onto_a_stopword():
    """The unconditional gate's fatal flaw: it also blocked legitimate function<->function matches."""
    h = tok(0, False); h.lexeme = "grc:3588"
    got = _gloss([h], ["de"], {"grc:3588": [("de",)]},
                 stopwords=_SW({"de"}), source_gated_stopwords=True)
    assert [m.t_idx for m in got] == [[0]]


def test_unconditional_gate_blocks_both_which_is_why_it_is_not_used():
    h = tok(0, False); h.lexeme = "grc:3588"
    got = _gloss([h], ["de"], {"grc:3588": [("de",)]},
                 stopwords=_SW({"de"}), source_gated_stopwords=False)
    assert got == []


def test_source_gate_is_a_noop_on_non_stopword_targets():
    h = tok(0, True); h.lexeme = "grc:1"
    got = _gloss([h], ["maison"], {"grc:1": [("maison",)]},
                 stopwords=_SW({"de"}), source_gated_stopwords=True)
    assert [m.t_idx for m in got] == [[0]]


def test_light_last_defers_a_light_lexeme_to_a_real_match():
    light = tok(0, True); light.lexeme = "grc:1510"
    real = tok(1, True); real.lexeme = "grc:2316"
    priors = {"grc:1510": [("est",)], "grc:2316": [("est",)]}
    got = _gloss([light, real], ["est"], priors, skip_lexemes={"grc:1510"}, light_last=True)
    assert [(m.h_idx, m.t_idx) for m in got] == [(1, [0])]


# ── gap-fill slot bookkeeping (protections #3 and #5) ─────────────────────────────────

def _covered(tmp_path, pairs, **kw):
    import json
    from lexeme_aligner.gapfill import load_covered
    (tmp_path / "align_eflomal_xx_RUT.jsonl").write_text(
        json.dumps({"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1, "pairs": pairs}) + "\n")
    return load_covered("xx", tmp_path, ("eflomal",), 0.0, {}, **kw)


def test_light_pair_stays_covered_but_releases_its_slot(tmp_path):
    p = {"h_idx": 0, "strong": "G1510", "lexeme": "grc:1510", "target": "est",
         "t_idx": [3], "score": 0.9, "content": True, "light": True}
    covered, taken, anchors, _, _ = _covered(tmp_path, [p], release_light=True)
    assert 0 in covered[8001001]          # never re-attempted as a gap
    assert taken[8001001] == set()        # ...but a real match may take position 3
    assert anchors[8001001] == {}


def test_light_release_can_be_ablated(tmp_path):
    p = {"h_idx": 0, "strong": "G1510", "lexeme": "grc:1510", "target": "est",
         "t_idx": [3], "score": 0.9, "content": True, "light": True}
    _, taken, _, _, _ = _covered(tmp_path, [p], release_light=False)
    assert taken[8001001] == {3}


def test_function_word_slots_are_free_by_default(tmp_path):
    """#5 default OFF: reserving them costs 29-42% of gap fills for ~0 precision."""
    p = {"h_idx": 0, "strong": "G3588", "lexeme": "grc:3588", "target": "le",
         "t_idx": [2], "score": 0.9, "content": False}
    covered, taken, _, _, _ = _covered(tmp_path, [p])
    assert taken[8001001] == set() and covered[8001001] == set()


def test_function_word_slots_can_be_reserved_on_request(tmp_path):
    p = {"h_idx": 0, "strong": "G3588", "lexeme": "grc:3588", "target": "le",
         "t_idx": [2], "score": 0.9, "content": False}
    covered, taken, _, _, _ = _covered(tmp_path, [p], reserve_function_slots=True)
    assert taken[8001001] == {2}
    assert covered[8001001] == set()      # still never a gap, and never a vocabulary exemplar


# ── gbt as a third gold backend ───────────────────────────────────────────────────────

def test_gbt_positional_gold_has_the_same_shape_as_clear(tmp_path):
    """contest_rule/score_gapfill plug gbt straight into the Clear code path, so the key shape
    ((zero-padded ref, strong) -> {surfaces}) must match exactly."""
    import json as _json
    from lexeme_aligner.benchmark import load_gold_gbt_positional
    (tmp_path / "gbt_xx.jsonl").write_text("\n".join(_json.dumps(r, ensure_ascii=False) for r in [
        {"kind": "1:1", "verse_ref": 8001001, "source_strong": ["H1961"],
         "target_gloss": ["Et il fut"], "target_ids": [1], "source_ids": [1]},
        {"kind": "many:1", "verse_ref": 8001002, "source_strong": ["H3117"],
         "target_gloss": ["ignored"], "target_ids": [1], "source_ids": [1]},
        {"kind": "1:1", "verse_ref": 8001003, "source_strong": ["H1234"],
         "target_gloss": [None], "target_ids": [1], "source_ids": [1]},
    ]) + "\n")
    g = load_gold_gbt_positional("xx", tmp_path)
    assert set(g) == {("08001001", "H1961")}          # only 1:1 rows with real content
    assert g[("08001001", "H1961")] == {"et", "il", "fut"}   # every word of the gloss phrase counts


def test_gbt_gold_langs_are_inert_until_aligned():
    """Adding gbt languages to config/gold_langs.json must not change the trust-matrix language set
    until those languages actually have alignment output."""
    import json as _json
    from pathlib import Path as _P
    cfg = _json.loads(_P("config/gold_langs.json").read_text())
    gbt = {k for k, v in cfg.items()
           if not k.startswith("_") and isinstance(v, dict) and v.get("gold") == "gbt"}
    assert {"tam", "tel", "njm", "hun"} <= gbt
    # a gbt language must carry MEASURED usable-row counts, not just a row count from the file size:
    # amh/kan/mal/tgl/tpi were added on the raw count (~448k source words each) and removed again once
    # measured at under 2k usable rows. See gbt_align --coverage.
    for iso in gbt:
        assert cfg[iso].get("gbt_usable_rows", 0) >= 5000, f"{iso} listed without usable gbt gold"
    # The four typology candidates are unvalidated and must not enter the trust matrix until aligned.
    # rus is excluded from this check: it is a deliberate, measured rehabilitation (see below), not a
    # speculative addition, so it is *meant* to rejoin GOLD as soon as it has alignment output.
    from lexeme_aligner.contest_rule import GOLD
    assert not ((gbt - {"rus"}) & set(GOLD)), "an unvalidated gbt language leaked into GOLD"


def test_rus_is_rehabilitated_onto_gbt_gold():
    """rus was quarantined for a scrambled CLEAR gold; gbt's independent gold measures healthy
    (gold-health gap 27.7pt vs 1.5pt on the same alignment). Guard the switch."""
    import json as _json
    from pathlib import Path as _P
    cfg = _json.loads(_P("config/gold_langs.json").read_text())
    assert cfg["rus"]["gold"] == "gbt"
    assert cfg["rus"]["gbt_greek_rows"] >= 30000
    assert "REHABILITATED" in cfg["_quarantine"]


# ── residual re-alignment ─────────────────────────────────────────────────────────────

def test_residual_target_side_light_forms_need_a_share_not_a_single_hit(tmp_path):
    """A form that merely brushed a light lexeme once must not be stripped from the whole corpus;
    fra 'est' (mostly grc:1510) must be. Hence a share floor, not 'ever aligned to a light lexeme'."""
    import json as _json
    from lexeme_aligner.residual_align import light_target_forms
    rows = [{"ref": 1, "pairs": [
        {"content": True, "lexeme": "grc:1510", "target": "est"},
        {"content": True, "lexeme": "grc:1510", "target": "est"},
        {"content": True, "lexeme": "grc:2316", "target": "dieu"},
        {"content": True, "lexeme": "grc:2316", "target": "dieu"},
        {"content": True, "lexeme": "grc:1510", "target": "dieu"},   # 1 of 3 -> below the floor
    ]}]
    (tmp_path / "align_eflomal_xx_RUT.jsonl").write_text(
        "\n".join(_json.dumps(r) for r in rows) + "\n")
    got = light_target_forms("xx", tmp_path, ("eflomal",), {"grc:1510"})
    assert got == {"est"}          # est 2/2 light; dieu 1/3 light -> kept in the residual pool


def test_residual_strips_taken_stopword_and_light_targets():
    from lexeme_aligner.residual_align import build_residual

    class _R:
        def __init__(s, heb, toks): s.book, s.ch, s.v, s.heb, s.toks = "RUT", 1, 1, heb, toks

    class _SW:
        words = {"de"}
        def is_function(s, w): return w.lower() in s.words

    gap, done = tok(0, True), tok(1, True)
    rec = _R([gap, done], ["alpha", "de", "est", "beta"])
    from lexeme_aligner.refs import encode
    ref = encode("RUT", 1, 1)
    out = build_residual([rec], {ref: {1}}, {ref: {0}}, _SW(), {"est"})
    assert len(out) == 1
    assert out[0].toks == ["beta"]        # 0 taken, 'de' stopword, 'est' light rendering
    assert out[0].orig == [3]             # ...and it remembers the ORIGINAL position
    assert [t.idx for t in out[0].heb] == [0]   # only the uncovered source token


def test_combine_with_gapfill_stratifies_agree_contested_and_solo(tmp_path):
    """Agreement between two INDEPENDENT mechanisms is the confidence signal; gap-fill wins contests
    (measured hin 68.8 vs 50.5, eng 51.9 vs 40.7); residual-only ships below hi_conf."""
    import json as _json
    from lexeme_aligner.residual_align import combine_with_gapfill
    (tmp_path / "align_gapfill_xx_RUT.jsonl").write_text(_json.dumps({
        "ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1, "pairs": [
            {"h_idx": 0, "strong": "H1", "t_idx": [4], "content": True},   # agrees below
            {"h_idx": 1, "strong": "H2", "t_idx": [7], "content": True},   # contested below
        ]}) + "\n")
    by_ref = {8001001: {"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1, "pairs": [
        {"h_idx": 0, "strong": "H1", "t_idx": [4], "score": 0.75},   # same target -> agree
        {"h_idx": 1, "strong": "H2", "t_idx": [9], "score": 0.75},   # different -> gap-fill wins
        {"h_idx": 2, "strong": "H3", "t_idx": [11], "score": 0.75},  # gap-fill never fired -> solo
    ]}}
    tally = combine_with_gapfill(by_ref, tmp_path, "xx", agree_score=0.9, fill_score=0.75)
    kept = {p["h_idx"]: (p["tier"], p["score"]) for p in by_ref[8001001]["pairs"]}
    assert kept == {0: ("agree_gapfill", 0.9), 2: ("residual_only", 0.75)}
    assert tally["contested_dropped"] == 1


def test_combiner_drops_a_verse_that_loses_every_pair(tmp_path):
    import json as _json
    from lexeme_aligner.residual_align import combine_with_gapfill
    (tmp_path / "align_gapfill_xx_RUT.jsonl").write_text(_json.dumps({
        "ref": 8001001, "pairs": [{"h_idx": 0, "strong": "H1", "t_idx": [4], "content": True}]}) + "\n")
    by_ref = {8001001: {"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1,
                        "pairs": [{"h_idx": 0, "strong": "H1", "t_idx": [9], "score": 0.75}]}}
    combine_with_gapfill(by_ref, tmp_path, "xx", 0.9, 0.75)
    assert by_ref == {}


# ── verse-local checks ────────────────────────────────────────────────────────────────

def _write(tmp_path, method, pairs):
    import json as _json
    (tmp_path / f"align_{method}_xx_RUT.jsonl").write_text(_json.dumps(
        {"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1, "pairs": pairs}) + "\n")


def test_agreement_counts_only_IDENTICAL_spans(tmp_path):
    """Two methods picking DIFFERENT targets is disagreement; counting it as corroboration would make
    the whole signal meaningless."""
    from lexeme_aligner.verse_checks import agreement
    _write(tmp_path, "eflomal", [{"h_idx": 0, "t_idx": [3]}, {"h_idx": 1, "t_idx": [5]}])
    _write(tmp_path, "gloss",   [{"h_idx": 0, "t_idx": [3]}, {"h_idx": 1, "t_idx": [9]}])
    a = agreement(tmp_path, "xx")[(1, 1)]
    assert a == {0: 2, 1: 1}


def test_agreement_ignores_pairs_with_no_span(tmp_path):
    from lexeme_aligner.verse_checks import agreement
    _write(tmp_path, "eflomal", [{"h_idx": 0, "t_idx": [3]}])
    _write(tmp_path, "gloss",   [{"h_idx": 0, "t_idx": []}])
    assert agreement(tmp_path, "xx")[(1, 1)] == {0: 1}


def test_annotate_is_idempotent(tmp_path):
    import json as _json
    from lexeme_aligner.verse_checks import annotate
    _write(tmp_path, "eflomal", [{"h_idx": 0, "t_idx": [3]}])
    _write(tmp_path, "gloss",   [{"h_idx": 0, "t_idx": [3]}])
    annotate(tmp_path, "xx")
    first = (tmp_path / "align_eflomal_xx_RUT.jsonl").read_text()
    annotate(tmp_path, "xx")
    assert (tmp_path / "align_eflomal_xx_RUT.jsonl").read_text() == first
    assert _json.loads(first)["pairs"][0]["agree"] == 2


def test_residual_enforces_contiguity_in_ORIGINAL_coordinates():
    """The residual target side is stripped to a few percent of the verse, so a contiguous run in
    residual space maps back to a scattered span. Measured before the fix: 15-18% of fills scattered,
    gaps up to 27 tokens. Contiguity must be re-applied after the mapping."""
    from lexeme_aligner.eflomal_align import _longest_contiguous
    # residual positions 0,1,2 are contiguous; their originals are not
    orig_map = [8, 10, 15]
    mapped = sorted(orig_map[j] for j in (0, 1, 2))
    assert _longest_contiguous(mapped, set()) == [8]
    # a genuinely adjacent pair survives
    assert _longest_contiguous(sorted([11, 12, 20]), set()) == [11, 12]


# ── compact-alignments: residual ships as an opt-in LAYER, not merged in ──────────────

def test_residual_is_not_in_the_base_compact_methods():
    """Merging residual (23-27% precise) into a 70-90% file would dilute every consumer silently."""
    from lexeme_aligner.compact_align import METHODS, LAYER_METHODS
    assert "residual" not in METHODS
    assert LAYER_METHODS == ("residual",)


def test_layer_drops_anything_the_base_already_covers(monkeypatch):
    """The two files must not be able to contradict each other, even under a naive client merge."""
    import lexeme_aligner.compact_align as ca
    base = {"RUT 1:1": "0:3 1:5", "RUT 1:2": ""}
    layer = {"RUT 1:1": "1:9 4:11", "RUT 1:2": "2:1"}
    calls = []

    def fake(tag, usj, heb, out, books, methods, contest=None):
        calls.append(methods)
        return (layer if methods == ca.LAYER_METHODS else base), {}

    monkeypatch.setattr(ca, "build_compact", fake)
    got = ca.build_layer("xx", None, None, None, ["RUT"], base=base)
    assert got == {"RUT 1:1": "4:11", "RUT 1:2": "2:1"}   # ordinal 1 dropped: base has it


def test_residual_source_light_gate_is_opt_in_and_works_when_asked():
    """DEFAULT OFF: measured to cost content fills (fra 72->62 correct) without the light-source fills
    being less trustworthy, because the residual pass has ~2.9 target candidates per source token and
    therefore no slot scarcity. The mechanism is kept for the case where that stops being true."""
    from lexeme_aligner.residual_align import build_residual
    from lexeme_aligner.refs import encode

    class _R:
        def __init__(s, heb, toks): s.book, s.ch, s.v, s.heb, s.toks = "RUT", 1, 1, heb, toks

    light = tok(0, True); light.lexeme = "grc:1510"
    real = tok(1, True); real.lexeme = "grc:2316"
    rec = _R([light, real], ["alpha", "beta"])
    ref = encode("RUT", 1, 1)
    gated = build_residual([rec], {ref: set()}, {ref: set()}, None, set(), {"grc:1510"})
    assert [t.idx for t in gated[0].heb] == [1]      # asked for: light source token is not a gap
    default = build_residual([rec], {ref: set()}, {ref: set()}, None, set())
    assert [t.idx for t in default[0].heb] == [0, 1]  # default: it stays, and contributes to the model


def test_publish_compact_requires_an_explicit_index_root():
    """It has TWO output locations and must not invent either. The old default pointed at
    config/canonical_index (a different artifact's home) and only fired on direct in-process calls,
    silently leaving stray index files there."""
    import inspect
    from lexeme_aligner.compact_align import publish_compact
    sig = inspect.signature(publish_compact)
    assert sig.parameters["index_root"].default is inspect.Parameter.empty
    assert sig.parameters["out_root"].default is inspect.Parameter.empty


# ── usj_dir_for must resolve real edition tags, not guess from the iso ─────────────────

def test_usj_dir_for_resolves_tags_that_do_not_start_with_the_iso(tmp_path, monkeypatch):
    """The old version globbed `usj-<iso>*` on the assumption that a tag is the iso plus a suffix.
    False whenever the edition code comes from a DIFFERENT iso — ayr ingests as aymbsb, ndp as
    kdpbsu, xmz as mzqlai — so it silently returned None for 10 published languages."""
    import lexeme_aligner.target_stopwords as ts
    (tmp_path / "usj-aymbsb").mkdir()
    (tmp_path / "usj-aymbsb" / "01-GEN.json").write_text("{}")
    monkeypatch.setattr(ts, "_INGEST_CACHE", tmp_path)
    import lexeme_aligner.onboard as ob
    monkeypatch.setattr(ob, "allowed_testaments", lambda iso, *a, **k: {"ot"})
    monkeypatch.setattr(ob, "editions_for", lambda iso, *a, **k: [{"edition_code": "AYMBSB"}])
    monkeypatch.setattr(ob, "_tag", lambda iso, code, is_primary=False: "aymbsb")
    got = ts.usj_dir_for("ayr", tmp_path)
    assert got is not None and got.name == "usj-aymbsb"


def test_usj_dir_for_falls_back_to_the_prefix_glob(tmp_path, monkeypatch):
    """Retired/renamed editions whose text is still cached have no catalog entry — the glob must
    still find them."""
    import lexeme_aligner.onboard as ob
    monkeypatch.setattr(ob, "editions_for", lambda *a, **k: (_ for _ in ()).throw(KeyError("gone")))
    from lexeme_aligner.target_stopwords import usj_dir_for
    (tmp_path / "usj-xyzold").mkdir()
    (tmp_path / "usj-xyzold" / "01-GEN.json").write_text("{}")
    got = usj_dir_for("xyz", tmp_path)
    assert got is not None and got.name == "usj-xyzold"


def test_usj_dir_for_prefers_the_richest_dir_not_the_first(tmp_path, monkeypatch):
    """A bare-iso dir is often an empty shell from a failed ingest and sorts first."""
    import lexeme_aligner.onboard as ob
    monkeypatch.setattr(ob, "editions_for", lambda *a, **k: (_ for _ in ()).throw(KeyError("none")))
    from lexeme_aligner.target_stopwords import usj_dir_for
    (tmp_path / "usj-ayz").mkdir()                       # empty shell
    (tmp_path / "usj-ayzyss").mkdir()
    for b in ("01-GEN.json", "02-EXO.json"):
        (tmp_path / "usj-ayzyss" / b).write_text("{}")
    assert usj_dir_for("ayz", tmp_path).name == "usj-ayzyss"


def test_contest_rule_overrules_low_confidence_eflomal():
    """The 2026-09 change: a position both eflomal and gloss reached is decided by the LOO-validated
    rule, not by method order. Low-confidence eflomal (0.6) loses to an exact gloss match — the single
    biggest flip class (11,488 of 14,256 on swk)."""
    import lexeme_aligner.compact_align as ca
    rule = ca.load_contest_rule()
    ef = {"_method": "eflomal", "score": 0.6, "t_idx": [4], "target": "wrong"}
    gl = {"_method": "gloss", "method": "exact", "score": 1.0, "t_idx": [7], "target": "right"}
    win, lose = ca._resolve({"eflomal": ef, "gloss": gl}, ca.METHODS, rule)
    assert win is gl and lose is ef
    # ...and the pre-2026-09 behaviour is still one flag away, unchanged
    win, lose = ca._resolve({"eflomal": ef, "gloss": gl}, ca.METHODS, None)
    assert win is ef and lose is None


def test_contest_rule_keeps_high_confidence_eflomal():
    import lexeme_aligner.compact_align as ca
    ef = {"_method": "eflomal", "score": 0.9, "t_idx": [4], "target": "a"}
    gl = {"_method": "gloss", "method": "exact", "score": 1.0, "t_idx": [7], "target": "b"}
    win, lose = ca._resolve({"eflomal": ef, "gloss": gl}, ca.METHODS, ca.load_contest_rule())
    assert win is ef and lose is gl


def test_agreement_is_not_a_contest():
    """Same span from both methods must report no loser — .contested.json means 'we had to choose'."""
    import lexeme_aligner.compact_align as ca
    ef = {"_method": "eflomal", "score": 0.6, "t_idx": [4], "target": "Same"}
    gl = {"_method": "gloss", "method": "exact", "score": 1.0, "t_idx": [4], "target": "same "}
    win, lose = ca._resolve({"eflomal": ef, "gloss": gl}, ca.METHODS, ca.load_contest_rule())
    assert win is ef and lose is None


def test_light_gloss_pair_does_not_vote_but_is_still_emitted():
    """Not voting is about who decides a contest, not about whether an alignment exists — dropping
    these would have cost swk 1,308 aligned positions for no gain."""
    import lexeme_aligner.compact_align as ca
    rule = ca.load_contest_rule()
    light = {"_method": "gloss", "method": "exact", "score": 1.0, "t_idx": [7],
             "target": "x", "light": True}
    ef = {"_method": "eflomal", "score": 0.6, "t_idx": [4], "target": "y"}
    win, lose = ca._resolve({"eflomal": ef, "gloss": light}, ca.METHODS, rule)
    assert win is ef and lose is None                    # light pair did not contest
    win, lose = ca._resolve({"gloss": light}, ca.METHODS, rule)
    assert win is light                                  # ...but alone, it still aligns the position


def test_method_char_encodes_the_tier_the_rule_keys_on():
    import lexeme_aligner.compact_align as ca
    assert ca._method_char({"_method": "eflomal", "score": 0.9}) == "E"
    assert ca._method_char({"_method": "eflomal", "score": 0.6}) == "e"
    assert ca._method_char({"_method": "gloss", "method": "exact"}) == "G"
    assert ca._method_char({"_method": "gloss", "method": "head"}) == "g"
    assert ca._method_char({"_method": "gapfill"}) == "f"
    assert ca._method_char({"_method": "residual"}) == "r"


def test_contest_pick_survives_a_light_gloss_only_position():
    """Latent crash fixed alongside: gl is zeroed for being light, no eflomal, no gapfill -> the old
    code did None.get('score'). Callers already handle a None winner."""
    from lexeme_aligner.merge_align import _contest_pick
    mp = {"gloss": {"method": "exact", "score": 1.0, "target": "x", "light": True}}
    win, voters, score = _contest_pick(mp, {})
    assert win is None and voters == [] and score == 0.0
