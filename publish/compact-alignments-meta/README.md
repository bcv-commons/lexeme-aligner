---
pretty_name: Compact alignments — provenance sidecar
tags:
  - bible
  - word-alignment
  - interlinear
license: cc0-1.0
---

# compact-alignments-meta — provenance sidecar (method, confidence, contested alternatives)

An optional, additive layer of [`compact-alignments`](https://huggingface.co/datasets/bcv-commons/compact-alignments). It holds ONLY the
`<BOOK>_<hash>.meta.json` files, under the **same relative path** as in the main repo:

```
<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.meta.json
```

For an alignment array at `a/arb/arb_vdv/GEN_1a2b3.json` in the main repo, this layer's file is `a/arb/arb_vdv/GEN_1a2b3.meta.json` here. The `<hash>`
is the main file's content hash, so the two always belong together. Not every book has this layer; a missing file is a 404, not an error.

Content: `{"method": [...], "conf": [...], "contested": [...], "bonus": [...], "rule": [...], "rend": [...]}`, every array position-parallel to the book index. The full
format — what each method letter and confidence digit means — is section 3 of the main repo's README.

`rend` (since 2026-10) is one string of space-separated integers per verse, one per aligned entry of the main array, in the same
order: the n-th *distinct rendering* of that entry's lexeme in this edition, numbered by first appearance in canonical order.
A rendering is the aligned raw target words, case-folded, minus the language's published target-stopwords, joined by one space
(if every word is a stopword the original words are kept). The edition's `rend_stopwords` in the main repo's `manifest.json` is
the sha256 of the list used. Ids are comparable only within one edition and lexeme, are valid only with the main array of the
same file, are renumbered on every rebuild, and carry no text. An edition under a takedown rule may carry `0` for withheld entries or no `rend` array at all (manifest `rend_scope`). `rend` is published for the compact alignments only, never for full-alignment files. Full definition and the takedown policy: main repo README, "Edition-side channel `rend`".

Spans are keyed by spine verse; read target words from the edition's mapped verse (main README, "Verse numbering").

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
