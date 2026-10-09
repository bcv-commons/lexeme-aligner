"""eval/llm_edit.py: the edit grammar, the applier, overlap resolution and the chapter chunker (no spine, no model)."""
import collections

from lexeme_aligner.eval import llm_edit as le
from lexeme_aligner.hebrew_source import HebToken


def tok(idx, strong="H0001", content=True):
    return HebToken(idx, f"w{idx}", strong, f"hbo:{strong[1:]}", f"l{idx}", None, content)


def prop(ref=8001001, ch=1, v=1, owner=None, n_t=6):
    p = le.Proposal(ref, "RUT", ch, v, [tok(0), tok(1), tok(2, "H9001", False)], [f"w{j}" for j in range(n_t)])
    p.owner = owner if owner is not None else {0: [0, 1], 1: [2], 2: []}
    p.method = {0: "eflomal", 1: "gloss", 2: "eflomal"}
    return p


def test_codes_parse_every_form_and_reject_garbage():
    edits, bad = le.parse_edits("h7>t10 h7>t10-12 h4>t2,7 h4>t2-3,9 h3= t12+ x9 h1>t h2>tq")
    got = [(e.kind, e.h, e.t) for e in edits]
    assert got == [("assign", 7, [10]), ("assign", 7, [10, 11, 12]), ("assign", 4, [2, 7]), ("assign", 4, [2, 3, 9]),
                   ("none", 3, []), ("added", None, [12])]
    assert bad == ["x9", "h1>t", "h2>tq"]


def test_assigning_takes_the_words_from_their_old_owner_and_keeps_a_partition():
    p = prop()
    owner, changed, c = le.apply_edits(p, le.parse_edits("h1>t1-2 t0+ h2>t5")[0])
    assert owner == {0: [], 1: [1, 2], 2: [5]} and changed == {0, 1, 2}
    owned = [j for s in owner.values() for j in s]
    assert len(owned) == len(set(owned))                                     # a partition
    assert c["h>t"] == 2 and c["t+"] == 1


def test_unknown_ids_and_positions_outside_the_verse_are_dropped_and_counted():
    p = prop()
    owner, changed, c = le.apply_edits(p, le.parse_edits("h9>t1 h1>t40 h0=")[0])
    assert owner == {0: [], 1: [2], 2: []} and changed == {0}
    assert c["dropped: unknown source id"] == 1 and c["dropped: target position outside the verse"] == 1


def test_an_edit_that_restates_the_proposal_changes_nothing():
    p = prop()
    owner, changed, _ = le.apply_edits(p, le.parse_edits("h0>t0-1 h1>t2")[0])
    assert owner == p.owner and changed == set()


def test_overlaps_go_to_the_content_token_then_method_priority_and_both_are_flagged():
    p = prop(owner={0: [0, 1], 1: [1, 2], 2: [2]})
    p.method = {0: "gloss", 1: "spanext", 2: "eflomal"}
    n = le.resolve_overlaps(p, content={0, 1})
    assert p.owner == {1: [1, 2], 0: [0], 2: []} and n == 2               # spanext before gloss; content before function word
    assert {h for h, s in p.doubt_h.items() if "overlap" in s} == {0, 1, 2}


def test_added_lists_the_target_words_nobody_owns():
    assert prop().added() == [3, 4, 5]


def test_a_chapter_is_one_chunk_and_a_long_one_splits_evenly_without_repeating_a_verse():
    ps = [prop(8001000 + v, 1, v) for v in range(1, 46)] + [prop(8002000 + v, 2, v) for v in range(1, 6)]
    cs = le.chunks(ps, cap=40)
    assert [len(c) for c in cs] == [23, 22, 5]
    assert [c[0].ch for c in cs] == [1, 1, 2]


def test_span_format_is_compact():
    assert le._fmt_span([3]) == "t3" and le._fmt_span([5, 3, 4]) == "t3-5" and le._fmt_span([3, 4, 5, 9]) == "t3-5,9"
    assert le.parse_edits("h1>" + le._fmt_span([3, 4, 5, 9]))[0][0].t == [3, 4, 5, 9]


def test_records_carry_kept_edited_unrepresented_and_added():
    p = prop()
    recs = le.to_records([p], {p.ref: ({0: [0], 1: [1, 2], 2: []}, {0, 1})}, {"edit_version": le.EDIT_VERSION})["RUT"]
    r = recs[0]
    assert [(x["h_idx"], x["t_idx"], x["prior"]) for x in r["pairs"]] == [(0, [0], "llm_edit_edited"), (1, [1, 2], "llm_edit_edited")]
    assert r["llm_skipped"] == [{"h_idx": 2, "strong": "H9001", "lexeme": "hbo:9001", "status": "unrepresented", "prior": "llm_edit_kept"}]
    assert r["llm_added"] == [3, 4, 5]


def test_the_oracle_only_touches_doubtful_tokens_the_gold_judges():
    from lexeme_aligner.eval.pos_score import GoldVerse
    p = prop()
    p.doubt_h = {0: {"low"}, 2: {"overlap"}}
    gv = GoldVerse(p.ref, links={("H0001", 0): {0}, ("H0001", 1): {4}})
    spine = type("S", (), {"key_of": {p.ref: {0: ("H0001", 0), 1: ("H0001", 1), 2: ("H9001", 0)}}})()
    ans = le.oracle_edits([p], {p.ref: gv}, spine)
    assert ans == {"edits": [{"ref": p.ref, "e": "h0>t0"}]}                  # h1 not doubtful, h2 not judged


def test_the_estimate_counts_calls_items_and_caches_the_prefix():
    from lexeme_aligner.eval.llm_providers import PRICES
    p = prop()
    p.doubt_h, p.doubt_t = {0: {"low"}}, {4: {"unowned"}}
    est = le.estimate([[p], [p]], "x" * 1000, ["y" * 400, "y" * 400], PRICES["claude-sonnet-5"])
    assert est["calls"] == 2 and est["doubtful_items"] == 4 and est["pessimistic"]["usd"] > est["optimistic"]["usd"] > 0


def test_check_mode_codes_keep_and_extend():
    edits, bad = le.parse_edits("h0. t3. h1+t3 h1+t4-5 h2.x")
    assert [(e.kind, e.h, e.t) for e in edits] == [("keep", 0, []), ("keep", None, [3]), ("add", 1, [3]), ("add", 1, [4, 5])]
    assert bad == ["h2.x"]
    p = prop()
    owner, changed, c = le.apply_edits(p, edits)
    assert owner == {0: [0, 1], 1: [2, 3, 4, 5], 2: []} and changed == {1}           # keeps change nothing; + extends the span
    assert c["kept (h. / t.)"] == 2 and c["h+t"] == 2


def test_add_takes_a_word_from_its_old_owner():
    p = prop()
    owner, changed, _ = le.apply_edits(p, le.parse_edits("h1+t1")[0])
    assert owner == {0: [0], 1: [1, 2], 2: []} and changed == {0, 1}


def test_the_check_line_lists_flagged_source_then_target_ids_and_only_in_check_mode():
    p = prop()
    p.doubt_h, p.doubt_t = {1: {"low"}, 0: {"overlap"}}, {4: {"unowned-fn"}}
    assert le.check_ids(p) == ["h0", "h1", "t4"]
    assert le.render_verse(p, {}, "check").splitlines()[-1] == "CHECK: h0 h1 t4"
    assert "CHECK" not in le.render_verse(p, {}, "edits")
    assert "llm-edit-v2" in le.render_prefix("fra", "French", None, "check") and "llm-edit-v1" in le.render_prefix("fra", "French", None, "edits")
