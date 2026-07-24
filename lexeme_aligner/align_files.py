"""Exact-tag matching for align_<method>_<tag>_<BOOK>.jsonl output files.

Every reader of this output used to glob `align_{method}_{tag}_*.jsonl` directly. That silently
matches SIBLING tags that happen to start with `tag + "_"` — e.g. tag "ind"'s glob also matches
"ind_ayt"'s own files (align_eflomal_ind_ayt_GEN.jsonl), because "ind_ayt" starts with "ind_".
Verified live (reverse_align_check.py, 2026-07): this pulled ind_ayt's own pairs — numbered against
ITS OWN verse-range pooling, not ind's — into ind's "covered" set in gapfill.py's load_covered(),
making gapfill think ind's own eflomal+gloss already covered tokens they never touched. The same
pattern affects any primary tag that's a literal prefix of a sibling: ind/ind_ags/ind_ayt,
hin/hin_cvb, cak/cak_smj, por/por_blt, spa/spa_bes, urd/urd_irv, quc/quc_new, poe/poe_tbl,
kkl/kkl_wbt, knj/knj_wbt, pls/pls_wbt, hvn/hvn_ubb, hch/hch_wbt.

Book codes (run_pilot.OT_BOOKS + NT_BOOKS) are always pure uppercase/digits with no underscore, so
the exact-tag file is unambiguous: after the `align_<method>_<tag>_` prefix, what remains must be
exactly one of those book codes.
"""
from __future__ import annotations

import re
from pathlib import Path

from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS

ALL_BOOKS = frozenset(OT_BOOKS + NT_BOOKS)


def tag_files(out_dir: Path, method: str, tag: str) -> list[Path]:
    """Exact-tag align_<method>_<tag>_<BOOK>.jsonl files for ONE known method."""
    prefix = f"align_{method}_{tag}_"
    return [fp for fp in sorted(out_dir.glob(f"{prefix}*.jsonl"))
            if fp.name[len(prefix):-len(".jsonl")] in ALL_BOOKS]


def tag_files_any_method(out_dir: Path, tag: str) -> list[Path]:
    """Exact-tag align_<method>_<tag>_<BOOK>.jsonl files across ALL methods (method name unknown)."""
    rx = re.compile(rf"^align_(?P<method>[a-z]+)_{re.escape(tag)}_(?P<book>[A-Z0-9]+)\.jsonl$")
    return sorted(fp for fp in out_dir.glob(f"align_*_{tag}_*.jsonl")
                  if (m := rx.match(fp.name)) and m.group("book") in ALL_BOOKS)


def methods_present(out_dir: Path, tag: str, candidates: list[str]) -> list[str]:
    """Which of `candidates` (method names) have at least one exact-tag file for `tag`."""
    return [m for m in candidates if tag_files(out_dir, m, tag)]
