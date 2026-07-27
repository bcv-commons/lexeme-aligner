---
license: cc-by-4.0
tags: [bible, versification, kjv, hebrew, septuagint]
---

# Versification (V1) — KJV-standard scheme registry

Maps any Bible tradition's verse references to **one standard: KJV**. Versification is a per-version
property — a Bible not numbered like KJV carries a `versification` scheme, and its refs normalize to
KJV via that scheme's diffs. Built by `shoresh/versification/build.py` from STEPBible **TVTMS**
(CC-BY, the "Expanded Version": `SourceType | SourceRef | StandardRef(=KJV) | Action`).

## Files
- **`schemes.tsv`** — the registry: `scheme, tvtms_sourcetype, n_diffs, standard`. `kjv` is the
  standard (0 diffs).
- **`schemes/<scheme>.tsv`** — one file per non-standard scheme: `source_ref, standard_ref, action`.
  **Diffs only** — a ref not listed maps to KJV by identity. Refs are `BOOK ch:v` (USFM codes); a
  Psalm superscription is the verse token `title` (Hebrew `PSA 3:1 → PSA 3:title`).

Schemes built now (the ones our corpora + consumers need):
| scheme | tradition | our corpus | diffs |
|---|---|---|---|
| `hebrew` | Masoretic / WLC | BHSA spine | 2,031 |
| `lxx` | Septuagint / Rahlfs | `lxx.db` | 5,386 |

## Resolver (shoresh `data.py`)
`to_standard(scheme, ref)` / `from_standard(scheme, ref)` — identity for `kjv`/unlisted; endpoint
`/versify/{scheme}/{book}/{chapter}/{verse}`.

## Consumers
- **X1 quotations** — OT refs normalized to KJV (e.g. LXX `PSA 15:8 → PSA 16:8`, `JOL 3:1 → JOL 2:28`);
  retired the old hardcoded Psalm map + the `vrs=lxx?` flag (0 remaining).
- **Aligner** — tag each target Bible with its scheme; Psalm superscription offset (`hebrew`) lets
  Psalm verses align.

## For the aligner (build_corpus): target → Hebrew-source composition
The aligner's source is Hebrew (WLC); KJV is the pivot standard. To bring a **target Bible verse
(scheme X)** onto the **Hebrew source numbering** so verse-by-verse matching lines up:
1. `kjv_ref = to_standard(X, target_ref)` — apply scheme X's forward map (`source_ref→standard_ref`;
   identity if the target is KJV-numbered, or use `lxx.tsv` if it follows LXX numbering).
2. `hebrew_ref = from_standard(hebrew, kjv_ref)` — apply `hebrew.tsv` **in reverse**
   (`standard_ref → source_ref`).

So `target_ref → kjv → hebrew_ref`. Concretely for Psalms: a titled Psalm's superscription is Hebrew
v1, so KJV `PSA 3:1` ↔ Hebrew `PSA 3:2` (the `hebrew.tsv` row `PSA 3:2 → PSA 3:1`, read in reverse).
Identity for any ref not listed. This unblocks Psalm alignment for **all** methods (eflomal/gloss/neural),
not just neural. Tables are UTF-8 TSV, `BOOK ch:v` (USFM); `title` = a superscription verse token.

## Scope (v1)
Single-verse remaps only (identity elsewhere). **Out of scope, flagged for a later pass:** ranges /
LXX sub-verses / concatenations (`;` `-` `!` in TVTMS — long verses, merges/splits like the LXX 9|10
Psalm merge), the Latin/other traditions, and the NT sub-verse micro-differences. The single-verse
map covers the Psalm superscriptions + OT chapter/verse offsets, which is what X1 + the aligner need.

## Per-version metadata (external — the design point)
Each Bible **version** declares its `versification` scheme; this is external metadata, not derivable
from content. Our own corpora auto-tag (`hebrew`, `lxx`, else `kjv`); externally-sourced Bibles
(the aligner's targets, helloAO/PKF) must supply it (default `kjv`, flag unknowns). Lives in the
bibles recipe layer — see `internal-docs/bibles-recipe-layer.md`.

## Rebuild
```bash
python -m versification.build     # downloads pinned TVTMS -> resources/versification/
```
