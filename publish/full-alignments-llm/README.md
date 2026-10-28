---
pretty_name: Full alignments — model-checked (LLM) layers
tags:
  - bible
  - word-alignment
  - interlinear
license: cc0-1.0
---

# full-alignments-llm — full word alignments checked by a language model

Every word of a verse, linked to the Hebrew or Greek source word it translates, **function words included** (articles,
prepositions, particles, clitic pronouns), and the source words that have no target word of their own marked explicitly.
Each layer starts from the statistical alignment of
[compact-alignments](https://huggingface.co/datasets/bcv-commons/compact-alignments) and has every verse checked by a language
model, which keeps, changes or adds links. These are **machine** alignments: no person has checked them. Hand-made alignments are in
[full-alignments-manual](https://huggingface.co/datasets/bcv-commons/full-alignments-manual).

The files use the format of compact-alignments (same paths, same `_index/`, same tokenizer), so a compact reader works on them
unchanged. The extra channels and the decision-list encoding are those of full-alignments-manual, section "File format".

## Layers

A layer id is `<edition>+llm+<name>`, where `<edition>` is the compact-alignments edition id of the same text, e.g.
`e/ell/ell_GreVamvas+llm+jev-v4/`. The list of layers, with rows and books, is in `manifest.json`.

| layer | text | model and method | rows |
|---|---|---|---|
| `ell_GreVamvas+llm+jev-v4` | Neophytos Vamvas 1850, Modern Greek (public domain; CrossWire GreVamvas via bcv-commons/bibles), 66 books | TypeSafe Jev (`jev-1.13.0`), design v4 | 624,632 |
| `kvg_wbt+llm+jev-v4` | Mbumbukiam-qa Manqat Mbomambaq, Kuni-Boazi (© 2015 Wycliffe Switzerland, CC BY-NC-ND 4.0; eBible `kvg` via bible.helloao.org), New Testament + Ruth + Jonah, 29 books | TypeSafe Jev (`jev-1.14.0`), design v4, asked by clause with bsb-add-books' language modules (since v1.1; v1.0 was asked by verse) | 148,352 |
| `kvg-m_wbt+llm+jev-v4` | the same Kuni-Boazi text, **morphed** (aligned by word parts, see below), 29 books | as above, on the text split into parts with bsb-add-books' Kuni segmenter | 148,398 |
| `eng_kjv+llm+jev-v4` | King James Version, 1769 text (public domain outside the UK; eBible `eng-kjv2006` via bible.helloao.org), **Old Testament**, 39 books. Its New Testament was aligned to the Textus Receptus and is in [alignments-by-id](https://huggingface.co/datasets/bcv-commons/alignments-by-id) (`tr/e/eng/eng_kjv`) | TypeSafe Jev (`jev-1.14.0`), design v4; the KJV's own Strong's tagging cleared a word's doubt where the proposal matched it, and function-word links Jev kept at least 97% of the time on Ruth, Jonah, Esther and 1 Samuel were not asked again in the other books | 483,700 |

**Design v4:** for each verse the model is asked about every target function word and every source function word, and about each
content link the chain flagged as doubtful: which source word a target word belongs to, or which target word carries a source word.
Each row records in its profile (`_layer.json`) whether the link was **kept** from the chain (`llm_edit_kept`, with the chain method
that proposed it), **edited** by the model (`llm_edit_edited`) or is a function word carried **inside** another word, for example by a
prefix or suffix (`llm_edit_inside`). A source word the model leaves unrendered is an explicit no-rendering (`!`). A **target** word the
model says renders no source word is a `+` word with no owner (profile `llm_edit_none`; e.g. RUT 1:1 KJV `+1-3,...`: "it came to").
A target word that is in no link and no `+` entry was **not decided**: the model was not asked about it (v4 asks about every target
function word, but about a content word only when the chain flagged it or the link holding it as doubtful). Added 2026-10-26 (asked for by
bsb-add-books); the links are unchanged, the answers were already given and are only recorded now. Verses with nothing to
ask (mostly name lists) or no target text keep the chain's proposal. Model version, design and threshold are in each layer's
`_layer.json`. The questions are built by `lexeme_aligner.eval.jev_check` and the layer by `lexeme_aligner.fullalign_build`
(github.com/bcv-commons/lexeme-aligner).

**By clause (Kuni, v1.1):** one request covers one clause, or 2-3 short clauses of one sentence, as bsb-add-books' CLAUSES.md lays
them out (NT: greek-nt-clause-layout; Ruth and Jonah: bcv-commons/hebrew-clause-layout v0.37). The checker is given bsb-add-books'
language modules (bcv-commons/language-analysis: the Greek / Hebrew source module and the Kuni module `kvg/d8fdb53e`) instead of our
conventions text; `_conventions.json` names them (below). Built by `lexeme_aligner.eval.jev_clauses`.

**Morphed editions:** `<iso>-m_<rest>` (e.g. `kvg-m_wbt`) is an edition aligned by word PARTS: each word split into prefixes, stem,
tone and suffixes with a pinned segmenter, aligned part by part and folded back to the words. Its main file and channels read like any
other layer (links to whole tokens); the meta file adds a `parts` channel, one string per spine verse, `srcSlot:tok.part[,tok.part ...]`
for each source word linked to a part (`c<n>` content word, `f<n>` function word, `p<n>` pooled; a token linked whole is written
bare). It holds part NUMBERS only, no text. The manifest entry's `parts` says how to rebuild the parts: segmenter (dataset, revision,
module, sha256 of its files, unicode version), numbering rule and credit. Kuni: bcv-commons/language-analysis kvg/d8fdb53e
(bsb-add-books). A Greek negative (οὐ, μή ...) is linked to a Kuni negation suffix (-eav, -ak, -av) in 23% of its occurrences in the
morphed layer against 3% in the whole-word one.

## File format

```
<iso[0]>/<iso>/<layer id>/<BOOK>_<hash>.json        main: "srcOrd:span ..." per spine verse, as in compact-alignments
<iso[0]>/<iso>/<layer id>/<BOOK>_<hash>.meta.json   channels (function words, profiles, rows), see full-alignments-manual
<iso[0]>/<iso>/<layer id>/_layer.json               version, flags, spine index sha, attribution, profile table
<iso[0]>/<iso>/<layer id>/_conventions.json         the conventions text each book was checked with, as attributed rules
```

`_conventions.json`: `books` maps every book to the version (`<lang>/<name>/<sha8>`) of the conventions text the checker was given
for it, and `sets` repeats those versions as rules, each marked `measured`, `by-construction` or `llm-authored` with its derivation.
All versions, and the measured rules, are in [alignment-conventions](https://huggingface.co/datasets/bcv-commons/alignment-conventions).
A layer checked with bsb-add-books' modules has `"mode": "modules"`, `dataset` bcv-commons/language-analysis, `books` mapping each book
to `{"source": ..., "target": ...}` (`<commit>/<path in that dataset>`) and `files` with each module's sha256.

`<hash>` = the first 8 hex characters of the book's text fingerprint (compact-alignments README, "The text fingerprint"), the same
name the compact file of that text has, so a file only matches the exact text it was built on. No file holds a word of any text.

## Versions

Each release is a git tag of this repo (`v1.0`, …); pin a tag (`revision="v1.0"`) rather than `main`. A tag is never moved: a
rebuild becomes a new tag. `v1.0` (2026-10-26) is the first tag; it adds the owner-less `+` words to all three layers. `v1.1`
(2026-10-28): Kuni-Boazi asked again by clause, and the morphed Kuni layer `kvg-m_wbt+llm+jev-v4`.

## Verse mapping

Spans are keyed by the source verse (Hebrew WLC numbering in the OT); read the target words from the edition's mapped verse
(compact-alignments README, "Verse numbering"). Source and target verses are paired with the verse-mapping tables of **TVTMS**
(STEPBible Data, Tyndale House, Cambridge — CC BY 4.0, https://github.com/STEPBible/STEPBible-Data).

## License

The alignments are CC0 1.0. They hold no text; the edition named by each layer keeps its own licence (Vamvas: public domain; Kuni-Boazi `kvg_wbt`: CC BY-NC-ND 4.0, Wycliffe Switzerland).
