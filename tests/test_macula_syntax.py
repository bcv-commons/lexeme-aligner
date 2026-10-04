from types import SimpleNamespace as NS

import pytest

from lexeme_aligner import macula_syntax as ms
from lexeme_aligner.hebrew_source import HebToken
from lexeme_aligner.refs import encode


def tok(idx, role=None, head=None, content=True, **kw):
    t = HebToken(idx, f"s{idx}", f"H{1000 + idx:04d}", f"hbo:{idx}", "l", "m", content)
    t.phrase_role, t.head_idx = role, head
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def rec(heb, toks=10, ref=("GEN", 1, 1)):
    return NS(book=ref[0], ch=ref[1], v=ref[2], heb=heb, toks=[0] * toks)


def anchors(rec_, tmap):
    return {encode(rec_.book, rec_.ch, rec_.v): tmap}


# Ruth 1:1 shape: verb 13 with subject runs at 14 and 24-25, a pp (15-17) between them
RUTH = [tok(12, None), tok(13, "v"), tok(14, "s", 13), tok(15, "pp", 13), tok(16, "pp", 13), tok(17, "pp", 13),
        tok(24, "s", 13), tok(25, "s", 13)]


def test_runs_split_where_another_role_intervenes():
    a = {i: float(i) for i in (13, 14, 15, 16, 17, 24, 25)}
    units = ms.clause_units(RUTH, a)
    assert [(u.label, u.src) for u in units] == [("v", 13), ("s", 14), ("pp", 15), ("s", 24)]
    assert units[3].tpos == pytest.approx(24.5)             # mean of 24 and 25
    assert all(u.ident == 13 for u in units)


def test_role_less_token_ends_a_run_and_is_never_a_unit():
    heb = [tok(0, None), tok(1, "v"), tok(2, "s", 1), tok(3, None), tok(4, "s", 1)]
    units = ms.clause_units(heb, {i: float(i) for i in range(5)})
    assert [(u.label, u.src) for u in units] == [("v", 1), ("s", 2), ("s", 4)]


def test_only_aligned_content_tokens_place_a_unit():
    heb = [tok(1, "v"), tok(2, "o", 1, content=False), tok(3, "o", 1), tok(4, "s", 1)]
    units = ms.clause_units(heb, {1: 5.0, 3: 2.0})           # idx 4 not aligned, idx 2 not content
    assert [(u.label, u.src, u.tpos) for u in units] == [("o", 3, 2.0), ("v", 1, 5.0)] or \
           [(u.label, u.src, u.tpos) for u in units] == [("v", 1, 5.0), ("o", 3, 2.0)]


def test_clause_role_stat_scopes_to_the_verbs_own_clause():
    # two clauses: verb 1 with subject 2; verb 5 with subject 6. Target puts subject 2 BEFORE verb 1, subject 6 AFTER verb 5.
    heb = [tok(1, "v"), tok(2, "s", 1), tok(5, "v"), tok(6, "s", 5)]
    r = rec(heb)
    a = anchors(r, {1: 4.0, 2: 3.0, 5: 8.0, 6: 9.0})
    st = ms.clause_role_stat([r], a, "s")
    assert (st["n_before"], st["n_after"], st["n_opportunities"], st["rate_after"]) == (1, 1, 2, 0.5)


def test_clause_role_stat_does_not_pair_a_subject_with_another_clauses_verb():
    heb = [tok(1, "v"), tok(5, "v"), tok(6, "s", 5)]       # subject belongs to verb 5 only
    r = rec(heb)
    st = ms.clause_role_stat([r], anchors(r, {1: 9.0, 5: 3.0, 6: 4.0}), "s")
    assert st["n_opportunities"] == 1 and st["n_after"] == 1      # vs verb 5 (target 3 < 4), NOT vs verb 1


def test_fused_counted_separately():
    heb = [tok(1, "v"), tok(2, "o", 1)]
    r = rec(heb)
    st = ms.clause_role_stat([r], anchors(r, {1: 3.0, 2: 3.0}), "o")
    assert st["n_fused"] == 1 and st["n_resolved"] == 0 and st["rate_after"] is None


def test_construct_role_fallback_without_construct_role_column():
    assert ms.construct_role_of(tok(1, construct_role="rectum")) == "rectum"
    assert ms.construct_role_of(tok(1, construct_group="g", state="construct")) == "regens"
    assert ms.construct_role_of(tok(1, construct_group="g", state="absolute")) == "rectum"
    assert ms.construct_role_of(tok(1)) is None


def test_construct_after_counts_adjacent_regens_rectum_links():
    # chain A-of B-of C (regens, regens+rectum, rectum): links (A,B) and (B,C); target order A < B but C < B
    heb = [tok(1, construct_group="g", construct_role="regens"), tok(2, construct_group="g", construct_role="regens+rectum"),
           tok(3, construct_group="g", construct_role="rectum")]
    r = rec(heb)
    st = ms.construct_after_stat([r], anchors(r, {1: 1.0, 2: 5.0, 3: 2.0}), min_n=1)
    assert st["rec_after_n"] == 2 and st["rec_after_rate"] == 0.5


def test_construct_after_ignores_non_content_suffix_and_respects_min_n():
    heb = [tok(1, construct_group="g", construct_role="regens"), tok(2, construct_group="g", construct_role="rectum", content=False)]
    r = rec(heb)
    assert ms.construct_after_stat([r], anchors(r, {1: 1.0, 2: 2.0}), min_n=1) == {"rec_after_rate": None, "rec_after_n": 0}
    heb2 = [tok(1, construct_group="g", construct_role="regens"), tok(2, construct_group="g", construct_role="rectum")]
    r2 = rec(heb2)
    assert ms.construct_after_stat([r2], anchors(r2, {1: 1.0, 2: 2.0}), min_n=50)["rec_after_rate"] is None     # one link < min_n


def test_profile_pairs_adjacent_order_and_drift():
    heb = [tok(1, "v"), tok(2, "s", 1), tok(3, "o", 1)]
    r = rec(heb, toks=10)
    pk, dr, n = ms.profile_pairs([r], anchors(r, {1: 6.0, 2: 1.0, 3: 8.0}))
    assert n == 1
    assert pk[("v", "s")] == [0, 1]                           # source v then s; target put s first -> not kept
    assert pk[("s", "o")] == [1, 1]
    assert set(dr) == {"v", "s", "o"} and dr["s"][0] == pytest.approx(0.1 - 2 / 3)


def test_verse_without_anchors_or_with_one_unit_is_skipped():
    r = rec([tok(1, "v")])
    pk, dr, n = ms.profile_pairs([r], anchors(r, {1: 1.0}))
    assert n == 0 and not pk
    assert ms.clause_role_stat([r], {}, "s")["n_opportunities"] == 0
