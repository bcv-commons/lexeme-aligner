"""Positional gold is keyed by the gold's own verse, which is the TARGET edition's numbering; our alignment output is keyed by SPINE
verse (Hebrew/WLC numbering in the OT). Comparing them as-is pairs e.g. English PSA 3:2 gold with spine PSA 3:2 (= English 3:1) and
rewards the old, wrong verse pairing (the same bug `pos_score.load_gold` had, fixed 2026-10-05). `to_spine` re-keys a positional gold
dict `{(ref8, strong): ...}` onto spine verses with the edition's own verse remap.

Rule: OT refs go through the inverse of the remap (target verse -> spine verse; the first spine verse wins where several map to one
target verse); an OT target verse no spine verse maps to is dropped (nothing to compare it with); NT refs and identity editions are
unchanged.
"""
from __future__ import annotations

import functools
from pathlib import Path

INGEST_CACHE = Path("pipeline/work/ingest-cache")


@functools.lru_cache(maxsize=None)
def _inverse(tag: str, usj_dir: str) -> dict[int, int] | None:
    from lexeme_aligner.eval.pos_score import spine_ref_of_target
    from lexeme_aligner.run_pilot import OT_BOOKS
    from lexeme_aligner.versification import remapper
    remap = remapper(tag, usj_dir)
    return spine_ref_of_target(list(OT_BOOKS), remap) if remap else None


def to_spine(gold: dict, tag: str | None, usj_dir: Path | None = None) -> dict:
    """Re-key `{(ref8, strong): value}` (ref8 = zero-padded BBCCCVVV string) from target verses to spine verses for edition `tag`."""
    if not tag:
        return gold
    inv = _inverse(tag, str(usj_dir or INGEST_CACHE / f"usj-{tag}"))
    if inv is None:
        return gold
    out: dict = type(gold)() if not hasattr(gold, "default_factory") else type(gold)(gold.default_factory)
    for (ref8, strong), val in gold.items():
        ref = int(ref8)
        if ref // 1_000_000 <= 39:
            ref = inv.get(ref)
            if ref is None:
                continue
        out[(f"{ref:08d}", strong)] = val if (f"{ref:08d}", strong) not in out else (out[(f"{ref:08d}", strong)] | val)
    return out
