"""Roadmap X3 (internal-docs/aim1-typology-source-structure-plan.md §4R): a Glottolog genetic-
relatedness fallback for gram-struct's five direction slots (`typology.SLOTS`), reading bcv-query's
`resources/languages/languages.db` (a sibling repo, read-only; 7,925 languages, a `relatedness` table
of 171,818 rows keyed `(iso639_3, related_iso639_3, rank, distance, basis)`, genetic-tree distance
0.0-13.0, CC-BY-4.0 per Glottolog).

WHY THIS IS THE LOWEST-PRIORITY PARTITION, STRUCTURALLY, NOT BY CONVENTION: `build_kin()` only ever
emits a slot key that `existing_keys` (the union of external/imputed/derived/measured's OWN keys for
that language, computed BEFORE kin runs) does not already contain. A slot resolved-but-null (Grambank
knows both codes and they disagree — e.g. hin's `article`) already occupies that key and is correctly
left alone; kin fills the ABSENT case only. This makes `gram_struct.merge_partitions`'s existing
raise-on-collision the enforcement mechanism for "kin never overrides a measured verdict" — kin cannot
collide with anything by construction, so no special-case exception was needed there (contrast with
`derived`, whose own shadowing exception IS special-cased in that function).

Only propagates from `external`/`imputed` facts (Grambank/WALS/lang2vec-resolved neighbours), never
from `derived` or another language's own `kin` fact — a kin-of-kin chain would compound whatever error
the first hop already carries, and the plan's own phrasing ("nearest Grambank/WALS-resolved relative")
says external/imputed specifically.

Confidence is NOT the neighbour's own confidence — it is this session's OWN leave-one-out measurement
of how often the kin METHOD agrees with the true external/imputed value, banded by tree distance
(`leave_one_out()`), the same "confidence = the band's own held-out agreement, not the individual
fact's" rule Step 2's `typology.py` and D1's `validate_derived` already use.
"""
from __future__ import annotations

import collections
import sqlite3
from pathlib import Path

from lexeme_aligner import typology

DEFAULT_DB = Path("/home/lgunnars/dev/bcv-commons/bcv-query/resources/languages/languages.db")

# Real distance distribution (checked against the live table, 2026-09-25): 0-13, roughly log-shaped,
# peaking at 3-4. Bands chosen so each has enough leave-one-out observations to be a meaningful rate,
# not because of any a-priori linguistic claim about what "close" means at each cut point.
BANDS: list[tuple[int, int]] = [(0, 1), (2, 3), (4, 6), (7, 13)]


def _band_for(distance: float) -> tuple[int, int] | None:
    for lo, hi in BANDS:
        if lo <= distance <= hi:
            return (lo, hi)
    return None


def load_relatedness(db_path: Path = DEFAULT_DB) -> dict[str, list[tuple[str, float, int]]]:
    """{iso: [(related_iso, distance, rank), ...]}, nearest first (sorted by distance then rank).
    Read-only against the sibling repo's SQLite file; never writes there."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select iso639_3, related_iso639_3, distance, rank from relatedness").fetchall()
    finally:
        con.close()
    by_iso: dict[str, list] = collections.defaultdict(list)
    for iso, rel, dist, rank in rows:
        by_iso[iso].append((rel, dist, rank))
    for iso in by_iso:
        by_iso[iso].sort(key=lambda t: (t[1], t[2]))
    return dict(by_iso)


def build_resolved_directions(external: dict[str, dict], imputed: dict[str, dict]
                              ) -> dict[str, dict[str, str]]:
    """{iso: {slot: direction}} — ONLY real "before"/"after" values (never a resolved-null, never
    absent) from the external/imputed gram-struct partitions, the pool kin neighbours are drawn from."""
    resolved: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for source in (external, imputed):
        for iso, entry in source.items():
            for slot in typology.SLOTS:
                v = entry.get(slot)
                if v and v.get("direction"):
                    resolved[iso].setdefault(slot, v["direction"])
    return dict(resolved)


def nearest_resolved(iso: str, slot: str, relatedness: dict, resolved: dict[str, dict],
                     exclude: str | None = None) -> tuple[str, float] | None:
    """(neighbour_iso, distance) of the NEAREST relative with `slot` resolved in `resolved`, or None.
    `exclude`: skip this iso (used by leave-one-out to hide the true answer from its own search)."""
    for rel_iso, dist, _rank in relatedness.get(iso, []):
        if rel_iso == exclude:
            continue
        entry = resolved.get(rel_iso)
        if entry and slot in entry:
            return rel_iso, dist
    return None


def leave_one_out(relatedness: dict, resolved: dict[str, dict]) -> dict:
    """Per (slot, distance band): agreement rate of the kin method against the REAL external/imputed
    value, holding each language's own fact out of its own neighbour search. This is what calibrates
    `confidence` — never the raw rate of an individual fact."""
    band_stats: dict[tuple[str, tuple], list[int]] = collections.defaultdict(lambda: [0, 0])
    for iso, own in resolved.items():
        for slot, true_val in own.items():
            hit = nearest_resolved(iso, slot, relatedness, resolved, exclude=iso)
            if not hit:
                continue
            rel_iso, dist = hit
            band = _band_for(dist)
            if band is None:
                continue
            key = (slot, band)
            band_stats[key][1] += 1
            if resolved[rel_iso][slot] == true_val:
                band_stats[key][0] += 1
    return {f"{slot}@{lo}-{hi}": {"agree": a, "total": t, "rate": round(a / t, 4) if t else None}
           for (slot, (lo, hi)), (a, t) in sorted(band_stats.items())}


def build_kin(iso: str, relatedness: dict, resolved: dict[str, dict], existing_keys: set,
             confidence_by_band: dict) -> dict:
    """The `kin/<iso>.json` partition for one language — see module docstring for why this cannot
    collide with any other partition by construction. `existing_keys`: the union of
    external/imputed/derived/measured's own keys for `iso`, computed by the caller BEFORE this runs."""
    out: dict = {}
    for slot in typology.SLOTS:
        if slot in existing_keys:
            continue
        hit = nearest_resolved(iso, slot, relatedness, resolved)
        if not hit:
            continue
        rel_iso, dist = hit
        band = _band_for(dist)
        band_key = f"{slot}@{band[0]}-{band[1]}" if band else None
        confidence = confidence_by_band.get(band_key, {}).get("rate") if band_key else None
        out[slot] = {"direction": resolved[rel_iso][slot], "source": "kin", "neighbour": rel_iso,
                    "distance": dist, "confidence": confidence}
    return out
