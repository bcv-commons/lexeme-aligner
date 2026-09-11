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

A file may exist either as plain `<BOOK>.jsonl` or gzip-compressed `<BOOK>.jsonl.gz` — once a
language's been through `full_chain.py`'s clean-out step, its raw jsonl is kept compressed rather
than deleted (see full_chain.py's clean-out block for why: these files are cheap to keep — a
full-Bible edition gzips to ~1/10th its ~190MB raw size — and a retroactive fix that needs to
re-derive gapfill/compact-alignments/etc. for hundreds of already-published languages is NOT cheap
if that means re-running eflomal+gloss from scratch for all of them).

`tag_files`/`tag_files_any_method` match both extensions AND return `AlignPath` instances (a `Path`
subclass, Path.glob() preserves the subclass — verified on this repo's Python 3.14) whose
`.open()`/`.read_text()`/`.stem` transparently treat a `.gz` suffix as gzip-compressed. This means
every one of the ~20 existing callers — which just do `fp.open(encoding="utf-8")`,
`fp.read_text(encoding="utf-8")`, or `fp.stem` to pull the book code — keeps working completely
unchanged, gzipped or not. Deliberately NOT solved with a separate `open_align()` helper function
callers would each have to remember to call instead of `.open()`: that shape requires touching every
call site and silently breaks again the next time someone writes `fp.open(...)` in new code. Moving
the gzip-transparency into the Path type itself needs the change made in exactly one place.
"""
from __future__ import annotations

import gzip
import re
from pathlib import Path

from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS

ALL_BOOKS = frozenset(OT_BOOKS + NT_BOOKS)


class AlignPath(Path):
    """A Path to an align_*.jsonl(.gz) file. `.open()`/`.read_text()`/`.stem` treat a `.gz` suffix
    as gzip-compressed transparently; a plain `.jsonl` path behaves exactly like a normal Path."""

    def open(self, mode="r", *args, **kwargs):
        if self.suffix != ".gz":
            return super().open(mode, *args, **kwargs)
        if "b" not in mode and "t" not in mode:
            mode += "t"
        if "b" not in mode:
            kwargs.setdefault("encoding", "utf-8")
        return gzip.open(self, mode, **kwargs)

    def read_text(self, encoding=None, errors=None):
        if self.suffix != ".gz":
            return super().read_text(encoding=encoding, errors=errors)
        with self.open("rt", encoding=encoding or "utf-8", errors=errors) as f:
            return f.read()

    @property
    def stem(self):
        # only ONE suffix would otherwise be stripped, leaving "<BOOK>.jsonl" instead of "<BOOK>"
        # for a gzipped file — every caller that does `fp.stem.rsplit("_", 1)[-1]` to get the book
        # code needs this fixed transparently too, same reasoning as .open()/.read_text() above.
        if self.name.endswith(".jsonl.gz"):
            return self.name[:-len(".jsonl.gz")]
        return super().stem


def _strip_book(name: str, prefix: str) -> str | None:
    """`name` minus `prefix` and a trailing .jsonl or .jsonl.gz, or None if neither suffix matches."""
    if name.endswith(".jsonl.gz"):
        return name[len(prefix):-len(".jsonl.gz")]
    if name.endswith(".jsonl"):
        return name[len(prefix):-len(".jsonl")]
    return None


def tag_files(out_dir: Path, method: str, tag: str) -> list[AlignPath]:
    """Exact-tag align_<method>_<tag>_<BOOK>.jsonl(.gz) files for ONE known method."""
    prefix = f"align_{method}_{tag}_"
    candidates = list(out_dir.glob(f"{prefix}*.jsonl")) + list(out_dir.glob(f"{prefix}*.jsonl.gz"))
    return sorted(AlignPath(fp) for fp in candidates if _strip_book(fp.name, prefix) in ALL_BOOKS)


def tag_files_any_method(out_dir: Path, tag: str) -> list[AlignPath]:
    """Exact-tag align_<method>_<tag>_<BOOK>.jsonl(.gz) files across ALL methods (method name unknown)."""
    rx = re.compile(rf"^align_(?P<method>[a-z]+)_{re.escape(tag)}_(?P<book>[A-Z0-9]+)\.jsonl(?:\.gz)?$")
    candidates = list(out_dir.glob(f"align_*_{tag}_*.jsonl")) + list(out_dir.glob(f"align_*_{tag}_*.jsonl.gz"))
    return sorted(AlignPath(fp) for fp in candidates
                  if (m := rx.match(fp.name)) and m.group("book") in ALL_BOOKS)


def methods_present(out_dir: Path, tag: str, candidates: list[str]) -> list[str]:
    """Which of `candidates` (method names) have at least one exact-tag file for `tag`."""
    return [m for m in candidates if tag_files(out_dir, m, tag)]
