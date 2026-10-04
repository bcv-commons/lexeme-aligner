"""Roadmap E3 (internal-docs/aim1-typology-source-structure-plan.md §4R, "### full-align" — spine
selection half; the textual-basis half already exists, `textual_basis.py`). Today EVERY edition is
aligned against the same Nestle1904-based spine (`pipeline/lexeme-spine.db`, critical text), regardless
of what Greek textual tradition it actually translates — `textual_basis.py` classifies 1,843 editions
from 15 diagnostic verses and already tells us **1,082 follow Textus Receptus and 23 follow the
Byzantine/Majority tradition** (Robinson-Pierpont), a combined 1,105 editions (60% of classified ones)
whose source words genuinely differ from Nestle1904 at the verses the classifier checks, and — by
implication — at others it doesn't. `HebrewSource.__init__` already takes a `spine_db` path (it was
never hardcoded); this module is the missing piece that decides WHICH path, from a tag's own recorded
textual basis.

Two real spines exist for exactly this, in bcv-query's sibling `shoresh/macula/` (same `spine_words`/
`spine_meta` schema as our own `pipeline/lexeme-spine.db`, verified column-for-column, including a
`role` column already populated — read-only, PD license per the plan's own note): `rp2018-spine.db`
(140,143 words, Robinson-Pierpont 2018 — the Byzantine/Majority tradition) and
`tr-textus-receptus-spine.db` (140,856 words, Textus Receptus). Mapping, deliberately narrow (only the
two verdicts with an actual matching alternative spine; everything else keeps today's default):
  tr        -> tr-textus-receptus-spine.db
  byzantine -> rp2018-spine.db
  mixed / critical / indeterminate / unclassified -> today's default (Nestle1904, unchanged)

**Status: plumbing only, opt-in, UNMEASURED — do not make this the default without a gold check.**
Per the plan's own effort/gate column: this needs "eflomal coverage/hi-conf and gold F1 on a TR-basis
gold edition ... before/after", exactly this session's established discipline (Step 1/Step 2/Step 3's
own opt-in-until-measured pattern) — a second spine changes which SOURCE tokens exist at all for a
verse, which is a bigger lever than any typology-conditioned trigger measured so far, and untested
against real gold it could easily be a regression (e.g. TR/RP-only verses introduce content our
Hebrew/NT-fusion downstream code has never seen, or a textual-basis misclassification silently swaps in
the wrong spine for an edition). `run_pilot --spine-select` (opt-in, default off) is how a caller uses
this; nothing in the default pipeline calls it yet.
"""
from __future__ import annotations

import json
from pathlib import Path

from lexeme_aligner.config import SPINE_DB

_EDITION_STRUCT_DIR = Path("config/edition_struct")
_TEXTUAL_BASIS_FILE = Path("config/textual_basis.json")

# Read-only, sibling-repo, PD (Robinson-Pierpont 2018 / Textus Receptus reconstructions carry no
# copyright on the base text; verified via bcv-query's own PROVENANCE-equivalent before this module
# was written — see this module's own docstring for the schema/row-count cross-check performed).
_SPINE_BY_VERDICT = {
    "tr": Path("/home/lgunnars/dev/bcv-commons/bcv-query/shoresh/macula/tr-textus-receptus-spine.db"),
    "byzantine": Path("/home/lgunnars/dev/bcv-commons/bcv-query/shoresh/macula/rp2018-spine.db"),
}


def textual_basis_verdict(tag: str, edition_struct_dir: Path = _EDITION_STRUCT_DIR,
                          textual_basis_file: Path = _TEXTUAL_BASIS_FILE) -> str | None:
    """The tag's textual-basis verdict, preferring edition-struct's own merged record (if built) and
    falling back to `textual_basis.json` directly (edition-struct is a convenience merge over it, not
    a new fact — either source gives the identical verdict for a tag that has one)."""
    es_fp = Path(edition_struct_dir) / f"{tag}.json"
    if es_fp.exists():
        rec = json.loads(es_fp.read_text(encoding="utf-8"))
        tb = rec.get("derived", {}).get("textual_basis")
        if tb:
            return tb.get("verdict")
    doc = json.loads(Path(textual_basis_file).read_text(encoding="utf-8")) if Path(textual_basis_file).exists() else {}
    return doc.get("editions", {}).get(tag, {}).get("verdict")


def spine_for_tag(tag: str, default: Path = SPINE_DB, edition_struct_dir: Path = _EDITION_STRUCT_DIR,
                  textual_basis_file: Path = _TEXTUAL_BASIS_FILE) -> tuple[Path, str]:
    """(spine_path, reason) — `reason` is the verdict that picked it, or `"default"` when the tag has
    no verdict or its verdict has no matching alternative spine (mixed/critical/indeterminate all
    correctly stay on `default`, not a gap in this module)."""
    verdict = textual_basis_verdict(tag, edition_struct_dir, textual_basis_file)
    if verdict in _SPINE_BY_VERDICT:
        spine = _SPINE_BY_VERDICT[verdict]
        if spine.exists():
            return spine, verdict
    return Path(default), "default"
