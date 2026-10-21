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

For an alignment array at `a/arb/arb_vdv/GEN_1a2b3c4d.json` in the main repo, this layer's file is `a/arb/arb_vdv/GEN_1a2b3c4d.meta.json` here. The `<hash>`
is the main file's text fingerprint, so the two always belong together. Not every book has this layer; a missing file is a 404, not an error.

Content: `{"method": [...], "conf": [...], "contested": [...], "bonus": [...], "rule": [...], "rend": [...]}`, every array position-parallel to the book index. The full
format — what each method letter and confidence digit means — is section 3 of the main repo's README.

`rend` (since 2026-10) is one string of space-separated integers per verse, one per aligned entry of the main array, in the same
order: the n-th *distinct rendering* of that entry's lexeme in this edition, numbered by first appearance in canonical order.
A rendering is the aligned raw target words, case-folded, minus the language's published target-stopwords, joined by one space
(if every word is a stopword the original words are kept). The edition's `rend_stopwords` in the main repo's `manifest.json` is
the sha256 of the list used. Ids are comparable only within one edition and lexeme, are valid only with the main array of the
same file, are renumbered on every rebuild, and carry no text. An edition under a takedown rule may carry `0` for withheld entries or no `rend` array at all (manifest `rend_scope`). `rend` is published for the compact alignments only, never for full-alignment files. Full definition and the takedown policy: main repo README, "Edition-side channel `rend`".

Spans are keyed by spine verse; read target words from the edition's mapped verse (main README, "Verse numbering").

## The text-fingerprint map `_fingerprints.txt` (since 2026-10)

Each edition folder also holds `<iso[0]>/<iso>/<edition>/_fingerprints.txt`: one line per book for the LATEST version of that book —
book, the 8-hex file hash (the main file's name), the date that version was built, then the first 5 hex characters of each chapter
fingerprint in chapter order (`n:xxxxx` where the chapter sequence breaks). It is optional: a client that fingerprints its own text
finds its file without it. It tells which version is current, and comparing chapter fingerprints shows where a text differs. The
definition of the fingerprint is the main repo's README, "The text fingerprint".

Each language folder also holds `<iso[0]>/<iso>/_fingerprints.txt` (since 2026-10-21): the lines of all its edition maps, each
prefixed by the edition folder name, editions in name order:
```
ENGEVD GEN c5958b5f 2026-10-15 0779b 3770d ...
eng_BSB 3JN fa0a526b 2026-10-15 73644
```
A client that does not know the edition id fetches this one file, looks for its book's file hash and gets the edition (or learns that
no edition matches its text), without listing folders through the Hub API.

Editions that have only a full-alignment layer and no compact alignment (for example the Door43 texts) are listed after the compact
editions, with `<dataset>/<layer id>` in place of the edition folder:
```
full-alignments-manual-sa/hin_higlt+manual+door43 RUT 0b69d368 2026-10-16 87ab3 5611d 263fa 32795
```
The files of such a line are in `bcv-commons/<dataset>`, folder `<iso[0]>/<iso>/<layer id>/` (the same `<BOOK>_<hash>.json` names).
The first field of a compact edition never contains `/`. Only layers that are published are listed.

## Full-alignment channels (since 2026-10)

For the editions that also have a hand-made (gold) alignment, the `.meta.json` additionally carries the **statistical full
alignment**: every row of every method (losing alternatives included), and function words. New keys `fn`, `wp`, `fp`, `wx`, `rows`,
`off`, decoded with the edition's `<iso[0]>/<iso>/<edition>/_layer.json` (in this repo). Every existing key keeps its meaning and
the main array is unchanged, so a reader that ignores the new keys sees exactly what it saw before. The format is described in
[full-alignments-manual](https://huggingface.co/datasets/bcv-commons/full-alignments-manual), "File format"; the hand-made layers of
the same editions are in that repo (and Door43's in full-alignments-manual-sa).

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
