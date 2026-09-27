"""Roadmap D5 (internal-docs/aim1-typology-source-structure-plan.md §4R, "### derived/"): a general QA
layer over gram-struct's `derived/` output, not specific to any one slot's bug. Automates the kind of
pattern-noticing that manually caught a real bug in this session's own D2 work: eng/rus/cmn's
subject_verb results each looked individually plausible-ish in isolation, but were collectively
suspicious once compared side by side against known typology and against each other. This module makes
that comparison systematic and per-family instead of ad hoc and per-language.

Idea: group languages by Glottolog `stock` (bcv-query's own `languages.db`, the same sibling DB
`kin_prior.py` already reads read-only — see that module's own docstring for the DB's provenance). For
a family with enough members that ALSO have a resolved (non-null) direction for a given slot, compute
how often they agree with each other. A family that is internally scattered on a slot MIGHT be
genuinely typologically diverse (real families vary internally, sometimes a lot) — this module does
NOT claim to know which is which. What it does claim: flagging this is a cheap, cross-slot,
cross-language triage signal worth a human's five minutes, exactly the kind of check that would have
surfaced the D2 literalism bug earlier than a manual side-by-side read did. It is a DETECTOR, not a
corrector — it never modifies a gram-struct fact, only reports.

No universal "expected agreement" threshold is asserted here, because none is independently knowable at
this generality (a real family CAN be typologically split — Indo-European alone spans SOV Hindi and SVO
English). Reported thresholds are a coarse first-pass triage default, not a calibrated statistical bound;
treat every flag as "worth a look", never as "provably a bug".

    python -m lexeme_aligner.family_consistency --derived-dir config/gram_struct/derived_input
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
from pathlib import Path

from lexeme_aligner import typology
from lexeme_aligner.kin_prior import DEFAULT_DB

_DERIVED_DIR = Path("config/gram_struct/derived_input")


def load_family(db_path: Path = DEFAULT_DB) -> dict[str, str]:
    """{iso: stock} — Glottolog top-level family ("stock" in this DB's own column naming), read-only
    from bcv-query's sibling languages.db. Real coverage checked live: 7,925/7,925 languages have a
    non-empty stock (including the deliberate `"Isolate"`/`"Bookkeeping"`/`"Sign Language"` buckets,
    which are excluded below since "internal agreement" is not a meaningful question for them)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select iso639_3, stock from language where stock is not null and stock != ''").fetchall()
    finally:
        con.close()
    excluded = {"Isolate", "Bookkeeping", "Sign Language", "Unclassified", "Unattested", "Artificial Language"}
    return {iso: stock for iso, stock in rows if stock not in excluded}


def family_consistency(derived_docs: dict[str, dict], family_of: dict[str, str], slot: str,
                       min_resolved: int = 5) -> dict[str, dict]:
    """{family: {n_resolved, dominant, agreement, counts, total_with_data, mixed_n, mixed_rate}} for
    `slot`, over families with >= `min_resolved` languages that have a REAL (non-null) direction for
    it. `total_with_data` / `mixed_n` count every language in the family that has ANY entry for the
    slot (resolved or explicitly null/mixed), for the mixed-rate half of the check — a family can be
    flagged for a high mixed rate even if too few of its members resolved to compute an agreement rate."""
    resolved_by_family: dict[str, list[str]] = collections.defaultdict(list)
    total_by_family: dict[str, int] = collections.defaultdict(int)
    mixed_by_family: dict[str, int] = collections.defaultdict(int)
    for iso, doc in derived_docs.items():
        fam = family_of.get(iso)
        if not fam:
            continue
        entry = doc.get(slot)
        if not entry:
            continue
        total_by_family[fam] += 1
        direction = entry.get("direction")
        if direction:
            resolved_by_family[fam].append(direction)
        else:
            mixed_by_family[fam] += 1

    report: dict[str, dict] = {}
    for fam, directions in resolved_by_family.items():
        n = len(directions)
        if n < min_resolved:
            continue
        counts = collections.Counter(directions)
        dominant, dom_n = counts.most_common(1)[0]
        total = total_by_family[fam]
        mixed_n = mixed_by_family.get(fam, 0)
        report[fam] = {
            "n_resolved": n, "dominant": dominant, "agreement": round(dom_n / n, 4),
            "counts": dict(counts), "total_with_data": total, "mixed_n": mixed_n,
            "mixed_rate": round(mixed_n / total, 4) if total else None,
        }
    return report


def flag_suspicious(report: dict[str, dict], slot: str, agreement_floor: float = 0.6,
                    mixed_ceiling: float = 0.6) -> list[dict]:
    """Families worth a human look for `slot`: internal agreement below `agreement_floor`, or a mixed
    rate above `mixed_ceiling`. See module docstring — these are triage defaults, not proven bounds."""
    flagged = []
    for fam, stats in report.items():
        reasons = []
        if stats["agreement"] < agreement_floor:
            reasons.append(f"low agreement ({stats['agreement']})")
        if stats["mixed_rate"] is not None and stats["mixed_rate"] > mixed_ceiling:
            reasons.append(f"high mixed rate ({stats['mixed_rate']})")
        if reasons:
            flagged.append({"family": fam, "slot": slot, "reasons": reasons, **stats})
    return flagged


def audit_all_slots(derived_docs: dict[str, dict], family_of: dict[str, str], min_resolved: int = 5,
                    agreement_floor: float = 0.6, mixed_ceiling: float = 0.6) -> list[dict]:
    """Runs `family_consistency` + `flag_suspicious` for every slot in `typology.SLOTS`, returns the
    combined flagged list across all slots, most-suspicious (lowest agreement) first."""
    flagged: list[dict] = []
    for slot in typology.SLOTS:
        report = family_consistency(derived_docs, family_of, slot, min_resolved)
        flagged.extend(flag_suspicious(report, slot, agreement_floor, mixed_ceiling))
    flagged.sort(key=lambda f: f["agreement"])
    return flagged


def _load_derived_docs(derived_dir: Path) -> dict[str, dict]:
    docs = {}
    for fp in Path(derived_dir).glob("*.json"):
        try:
            docs[fp.stem] = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return docs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--derived-dir", type=Path, default=_DERIVED_DIR)
    ap.add_argument("--min-resolved", type=int, default=5)
    ap.add_argument("--agreement-floor", type=float, default=0.6)
    ap.add_argument("--mixed-ceiling", type=float, default=0.6)
    a = ap.parse_args(argv)
    docs = _load_derived_docs(a.derived_dir)
    family_of = load_family()
    flagged = audit_all_slots(docs, family_of, a.min_resolved, a.agreement_floor, a.mixed_ceiling)
    print(json.dumps({"languages_loaded": len(docs), "families_known": len(set(family_of.values())),
                      "flagged": flagged}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
