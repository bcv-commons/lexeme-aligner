"""Offline tests for compact_align.py's _resolve() — the per-position conflict resolution, and
specifically the spanext/contest-rule interaction fixed 2026-09-23 (spanext used to be silently ignored
whenever the contest-rule branch fired, because that branch only ever checked eflomal/gloss by name).
"""
from lexeme_aligner.compact_align import _resolve, _method_char

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
