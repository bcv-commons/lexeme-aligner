"""Offline tests for family_consistency.py (roadmap D5) — synthetic derived-doc/family fixtures, no
real gram-struct output or sibling DB needed."""
import lexeme_aligner.eval.family_consistency as fc


def _doc(direction, reason=None):
    d = {"direction": direction, "source": "derived"}
    if reason:
        d["reason"] = reason
    return {"subject_verb": d}


def test_family_consistency_computes_agreement_within_a_family():
    docs = {
        "eng": _doc("before"), "deu": _doc("before"), "nld": _doc("before"),
        "fas": _doc("after"), "hin": _doc("before"),
    }
    family_of = {"eng": "Indo-European", "deu": "Indo-European", "nld": "Indo-European",
                "fas": "Indo-European", "hin": "Indo-European"}
    report = fc.family_consistency(docs, family_of, "subject_verb", min_resolved=5)
    ie = report["Indo-European"]
    assert ie["n_resolved"] == 5
    assert ie["dominant"] == "before"
    assert ie["agreement"] == 4 / 5


def test_family_consistency_skips_families_below_min_resolved():
    docs = {"eng": _doc("before"), "deu": _doc("before")}
    family_of = {"eng": "Indo-European", "deu": "Indo-European"}
    report = fc.family_consistency(docs, family_of, "subject_verb", min_resolved=5)
    assert report == {}


def test_family_consistency_counts_mixed_and_total_separately_from_resolved():
    docs = {"eng": _doc("before"), "deu": _doc("before"), "nld": _doc("before"),
           "fas": _doc(None, reason="mixed"), "hin": _doc(None, reason="mixed")}
    family_of = {iso: "Indo-European" for iso in docs}
    report = fc.family_consistency(docs, family_of, "subject_verb", min_resolved=3)
    ie = report["Indo-European"]
    assert ie["n_resolved"] == 3
    assert ie["total_with_data"] == 5
    assert ie["mixed_n"] == 2
    assert ie["mixed_rate"] == 0.4


def test_family_consistency_ignores_languages_with_no_entry_for_the_slot_at_all():
    docs = {"eng": _doc("before"), "xyz": {"possessor": {"direction": "after"}}}
    family_of = {"eng": "Indo-European", "xyz": "Indo-European"}
    report = fc.family_consistency(docs, family_of, "subject_verb", min_resolved=1)
    assert report["Indo-European"]["total_with_data"] == 1     # xyz has no subject_verb key at all


def test_family_consistency_ignores_languages_with_no_known_family():
    docs = {"eng": _doc("before"), "zzz": _doc("after")}
    family_of = {"eng": "Indo-European"}                        # zzz absent -> no family
    report = fc.family_consistency(docs, family_of, "subject_verb", min_resolved=1)
    assert "Indo-European" in report
    assert sum(r["total_with_data"] for r in report.values()) == 1


def test_flag_suspicious_flags_low_agreement():
    report = {"Fam": {"n_resolved": 10, "dominant": "before", "agreement": 0.5,
                      "counts": {"before": 5, "after": 5}, "total_with_data": 10,
                      "mixed_n": 0, "mixed_rate": 0.0}}
    flagged = fc.flag_suspicious(report, "subject_verb", agreement_floor=0.6)
    assert len(flagged) == 1
    assert flagged[0]["family"] == "Fam"
    assert flagged[0]["slot"] == "subject_verb"
    assert "low agreement (0.5)" in flagged[0]["reasons"]


def test_flag_suspicious_flags_high_mixed_rate():
    report = {"Fam": {"n_resolved": 5, "dominant": "before", "agreement": 1.0,
                      "counts": {"before": 5}, "total_with_data": 10,
                      "mixed_n": 5, "mixed_rate": 0.5}}
    flagged = fc.flag_suspicious(report, "subject_verb", mixed_ceiling=0.4)
    assert len(flagged) == 1
    assert "high mixed rate (0.5)" in flagged[0]["reasons"]


def test_flag_suspicious_does_not_flag_a_healthy_family():
    report = {"Fam": {"n_resolved": 10, "dominant": "before", "agreement": 0.9,
                      "counts": {"before": 9, "after": 1}, "total_with_data": 10,
                      "mixed_n": 0, "mixed_rate": 0.0}}
    assert fc.flag_suspicious(report, "subject_verb") == []


def test_audit_all_slots_sorts_by_agreement_ascending():
    docs = {}
    family_of = {}
    # Fam1: subject_verb split 3/3 (agreement 0.5); Fam2: possessor split 9/1 (agreement 0.9)
    for i in range(3):
        docs[f"a{i}"] = {"subject_verb": {"direction": "before"}}
        docs[f"b{i}"] = {"subject_verb": {"direction": "after"}}
        family_of[f"a{i}"] = family_of[f"b{i}"] = "Fam1"
    for i in range(9):
        docs[f"c{i}"] = {"possessor": {"direction": "before"}}
        family_of[f"c{i}"] = "Fam2"
    docs["d0"] = {"possessor": {"direction": "after"}}
    family_of["d0"] = "Fam2"
    flagged = fc.audit_all_slots(docs, family_of, min_resolved=5, agreement_floor=0.95)
    assert len(flagged) == 2
    assert flagged[0]["family"] == "Fam1"                       # lower agreement (0.5) sorts first
    assert flagged[0]["agreement"] < flagged[1]["agreement"]


# --- sanity check: would this have caught the real D2 literalism bug? ---------------------------------
def test_would_have_flagged_the_real_literalism_bug_pattern():
    # Reconstructs the REAL pre-fix subject_verb pattern this session found by hand: eng landed
    # ambiguous/near-toss-up, rus landed confidently WRONG ("after", contradicting known SVO), cmn
    # landed correctly. All three are Eurasian-ish but not one family in reality; here we put them in
    # one synthetic family to prove the MECHANISM would flag a family with this shape, regardless of
    # whether real Glottolog groups them together (it doesn't - this is a mechanism test, not a claim
    # about real language classification).
    docs = {
        "eng": _doc(None, reason="mixed"),        # real: rate_after 0.41, ambiguous
        "rus": _doc("after"),                     # real: rate_after 0.822, CONFIDENTLY WRONG pre-fix
        "cmn": _doc("before"),                    # real: rate_after 0.28, correct
        "fra": _doc(None, reason="mixed"),
        "arb": _doc("after"),                     # real, but correctly VSO for arb specifically
    }
    family_of = {iso: "SyntheticTestFamily" for iso in docs}
    report = fc.family_consistency(docs, family_of, "subject_verb", min_resolved=3)
    flagged = fc.flag_suspicious(report, "subject_verb", agreement_floor=0.7, mixed_ceiling=0.5)
    # 3 resolved (rus=after, cmn=before, arb=after) -> dominant "after" 2/3 = 0.667 agreement,
    # below the 0.7 floor -> flagged. The other 2 are mixed -> mixed_rate 2/5=0.4, below ceiling here.
    assert len(flagged) == 1
    assert flagged[0]["agreement"] < 0.7
