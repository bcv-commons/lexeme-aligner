"""Offline tests for compact_align.py's _resolve() — the per-position conflict resolution, and
specifically the spanext/contest-rule interaction fixed 2026-09-23 (spanext used to be silently ignored
whenever the contest-rule branch fired, because that branch only ever checked eflomal/gloss by name).
Also covers the 2026-09-28 'rule' sidecar channel (_rule_label / _merged_pairs threading 'prior' through).
"""
import json

from lexeme_aligner.compact_align import _merged_pairs, _method_char, _resolve, _rule_label

METHODS = ("eflomal", "gloss", "gapfill")
# contest_rule.json cells key on (_tier("eflomal", p), _tier("gloss", p)) = (f"score {score}", p["method"])
# — gloss's OWN "method" field (its sub-tier: exact/stem/fuzzy/...), unrelated to "_method" (the top-level
# method name compact_align tags every pair with). "exact" here is that gloss sub-tier, not a top-level tag.
CONTEST_GL_WINS = {("score 0.6", "exact"): "gl"}   # a contest cell that picks gloss over eflomal


def pair(method, target, t_idx=(3,), score=0.6, light=False, gloss_tier="exact"):
    return {"_method": method, "method": gloss_tier, "target": target, "t_idx": list(t_idx),
           "score": score, "light": light}


# --- pre-existing behavior, unaffected by the fix ---------------------------------------------------
def test_only_eflomal_present_no_contest():
    mp = {"eflomal": pair("eflomal", "foo")}
    win, alt = _resolve(mp, METHODS, contest=None)
    assert win["_method"] == "eflomal" and alt is None


def test_eflomal_and_gloss_agree_no_contest_needed():
    mp = {"eflomal": pair("eflomal", "foo"), "gloss": pair("gloss", "foo", score=1.0)}
    win, alt = _resolve(mp, METHODS, contest=CONTEST_GL_WINS)
    assert win["_method"] == "eflomal" and alt is None   # agreement — contest never consulted


def test_contest_rule_picks_gloss_on_real_disagreement():
    mp = {"eflomal": pair("eflomal", "foo"), "gloss": pair("gloss", "bar", score=1.0)}
    win, alt = _resolve(mp, METHODS, contest=CONTEST_GL_WINS)
    assert win["_method"] == "gloss"
    assert alt["_method"] == "eflomal"


def test_flat_priority_without_contest():
    mp = {"gloss": pair("gloss", "bar"), "eflomal": pair("eflomal", "foo")}
    win, alt = _resolve(mp, METHODS, contest=None)
    assert win["_method"] == "eflomal"   # first in METHODS wins the flat-priority path


# --- the actual fix: spanext must win even when eflomal/gloss disagree and a contest rule fires -------
def test_spanext_wins_over_a_real_eflomal_gloss_disagreement():
    mp = {"eflomal": pair("eflomal", "foo"), "gloss": pair("gloss", "bar", score=1.0),
         "spanext": pair("spanext", "foo baz", t_idx=(3, 4))}
    win, alt = _resolve(mp, ("spanext",) + METHODS, contest=CONTEST_GL_WINS)
    assert win["_method"] == "spanext"
    assert alt is None                    # spanext isn't "the other side" of a contest — no relitigation


def test_spanext_wins_even_when_contest_rule_absent():
    mp = {"eflomal": pair("eflomal", "foo"), "spanext": pair("spanext", "foo baz", t_idx=(3, 4))}
    win, _ = _resolve(mp, ("spanext",) + METHODS, contest=None)
    assert win["_method"] == "spanext"


def test_spanext_absent_falls_through_to_normal_resolution():
    # mp simply has no "spanext" key (the caller never asked for it) — unaffected by the fix.
    mp = {"eflomal": pair("eflomal", "foo"), "gloss": pair("gloss", "bar", score=1.0)}
    win, alt = _resolve(mp, METHODS, contest=CONTEST_GL_WINS)
    assert win["_method"] == "gloss"


def test_method_char_recognizes_spanext():
    assert _method_char({"_method": "spanext"}) == "x"


# --- 2026-09-28: the 'rule' sidecar channel ------------------------------------------------------------

def test_rule_label_strips_the_spanext_prefix_legacy_no_position():
    # pre-position-embedding format (no ':pos') -- must not crash, prefix still stripped.
    assert _rule_label("spanext_name_after") == "name_after"


def test_rule_label_passes_gapfill_bare_labels_through_unchanged():
    assert _rule_label("strong") == "strong"
    assert _rule_label("name") == "name"


def test_rule_label_none_for_no_prior():
    assert _rule_label(None) is None
    assert _rule_label("") is None


# --- reversibility: the embedded clean-text position remaps to raw-text coordinates --------------------

def test_rule_label_remaps_the_embedded_position_via_raw_idx_of():
    # clean-text position 5 maps to raw-text position 9 (e.g. an earlier stripped annotation shifted it).
    raw_idx_of = [0, 1, 2, 3, 4, 9, 6, 7]
    assert _rule_label("spanext_name_after:5", raw_idx_of) == "name_after:9"


def test_rule_label_remaps_both_positions_of_a_two_sided_extension():
    raw_idx_of = [10, 11, 12, 13, 14]
    label = _rule_label("spanext_definite_before:0+spanext_struct_after:2", raw_idx_of)
    assert label == "definite_before:10+struct_after:12"


def test_rule_label_drops_the_position_when_it_cannot_be_remapped():
    # no raw_idx_of given at all -- keep the label, never publish a clean-text index unlabelled as raw.
    assert _rule_label("spanext_name_after:5", None) == "name_after"


def test_rule_label_drops_the_position_when_raw_idx_of_marks_it_stripped():
    # remap_clean_to_raw uses -1 for a position that didn't survive into raw text (inside a stripped
    # annotation span) -- must not publish a bogus negative index.
    raw_idx_of = [0, 1, -1, 3]
    assert _rule_label("spanext_name_after:2", raw_idx_of) == "name_after"


def test_rule_label_reversibility_a_client_can_reconstruct_the_original_span():
    # end-to-end demonstration of the actual guarantee: published_span - {embedded positions} ==
    # the pre-extension span, entirely from data already in the two published channels.
    published_span = {44, 45, 46}         # "srcOrd:44-46" in the main array, say
    raw_idx_of = list(range(50))          # identity mapping for this example
    rule_entry = _rule_label("spanext_possessor_after:46", raw_idx_of)
    assert rule_entry == "possessor_after:46"
    added = {int(part.rsplit(":", 1)[1]) for part in rule_entry.split("+")}
    assert published_span - added == {44, 45}   # the original, pre-extension span


def test_merged_pairs_threads_the_spanext_prior_into_rule(tmp_path):
    (tmp_path / "align_spanext_zz_RUT.jsonl").write_text(json.dumps({
        "chapter": 1, "verse": 1,
        "pairs": [{"h_idx": 5, "t_idx": [3, 4], "target": "foo bar", "prior": "spanext_name_after"}],
    }) + "\n", encoding="utf-8")
    by_verse = _merged_pairs("zz", "RUT", tmp_path, ("spanext", "eflomal", "gloss"), contest=None)
    rec = by_verse[(1, 1)][5]
    assert rec["rule"] == "spanext_name_after"
    assert _rule_label(rec["rule"]) == "name_after"


def test_merged_pairs_rule_is_none_for_a_plain_eflomal_position(tmp_path):
    (tmp_path / "align_eflomal_zz_RUT.jsonl").write_text(json.dumps({
        "chapter": 1, "verse": 1,
        "pairs": [{"h_idx": 5, "t_idx": [3], "target": "foo", "score": 0.9}],
    }) + "\n", encoding="utf-8")
    by_verse = _merged_pairs("zz", "RUT", tmp_path, ("spanext", "eflomal", "gloss"), contest=None)
    rec = by_verse[(1, 1)][5]
    assert rec.get("rule") is None
