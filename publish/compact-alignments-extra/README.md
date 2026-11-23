---
pretty_name: Compact alignments — opt-in residual layer
tags:
  - bible
  - word-alignment
  - interlinear
license: cc0-1.0
---

# compact-alignments-extra — opt-in residual layer

> **FROZEN on 2026-11-23.** This dataset is no longer updated. It will be removed one week after its replacement is published (the date will be given here). It is replaced by
> [bcv-commons/part-alignments](https://huggingface.co/datasets/bcv-commons/part-alignments), which links every Greek and Hebrew
> word PART (stem, ending, prefix ...) instead of whole words, for every language published here. To keep reading files in this
> format, make them from part-alignments with its `fold.py` / `fold.mjs` (same paths, same file names):
> `python fold.py --parts PART_ALIGNMENTS_DIR --out COMPACT_DIR` (`--fold-rule stem` keeps the stem's links only).

An optional, additive layer of [`compact-alignments`](https://huggingface.co/datasets/bcv-commons/compact-alignments). It holds ONLY the
`<BOOK>_<hash>.extra.json` files, under the **same relative path** as in the main repo:

```
<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.extra.json
```

For an alignment array at `a/arb/arb_vdv/GEN_1a2b3c4d.json` in the main repo, this layer's file is `a/arb/arb_vdv/GEN_1a2b3c4d.extra.json` here. The `<hash>`
is the main file's text fingerprint, so the two always belong together. Not every book has this layer; a missing file is a 404, not an error.

Content: the same compact string format as the main array, for target words a second, residual alignment pass could still explain after
eflomal, gloss, span extension and gap-fill. It is deliberately NOT merged into the main array. Format: see the main repo's README (the `r` method
letter).

Everything else — the README that defines the formats, `manifest.json`, `_index/`, `tokenize.js`, the decisions ledger — stays in the main repo. The
authoritative list of editions and books is its `manifest.json`. The layer was split out because the single repo had passed Hugging Face's
recommended 100,000 files.

## Verse mapping

Hebrew and English Bibles number some Old Testament verses differently (Psalm superscriptions, 1 Chronicles 6,
Joel, Malachi, Daniel 4 and 6, ...). Source and target verses are paired with the verse-mapping tables of **TVTMS**
(Translators Versification Traditions with Methodology for Standardisation), part of STEPBible Data by Tyndale
House, Cambridge — **CC BY 4.0**, https://github.com/STEPBible/STEPBible-Data — with each edition's numbering
detected from its own text (cross-checked against bcv-commons/bibles). Source verse references follow the Hebrew
(WLC) numbering of the MACULA source text.

## License

CC0-1.0, like the main dataset. Each edition's own source and licence pointer is in the main repo's `manifest.json`.
