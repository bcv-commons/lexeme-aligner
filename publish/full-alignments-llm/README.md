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
| `ell_GreVamvas+llm+jev-v4` | Neophytos Vamvas 1850, Modern Greek (public domain; CrossWire GreVamvas via bcv-commons/bibles), 66 books | TypeSafe Jev (`jev-1.13.0`), design v4 | 605,084 |
| `kvg_wbt+llm+jev-v4` | Mbumbukiam-qa Manqat Mbomambaq, Kuni-Boazi (© 2015 Wycliffe Switzerland, CC BY-NC-ND 4.0; eBible `kvg` via bible.helloao.org), New Testament + Ruth + Jonah, 29 books | TypeSafe Jev (`jev-1.14.0`), design v4 | 140,497 |

**Design v4:** for each verse the model is asked about every target function word and every source function word, and about each
content link the chain flagged as doubtful: which source word a target word belongs to, or which target word carries a source word.
Each row records in its profile (`_layer.json`) whether the link was **kept** from the chain (`llm_edit_kept`, with the chain method
that proposed it), **edited** by the model (`llm_edit_edited`) or is a function word carried **inside** another word, for example by a
prefix or suffix (`llm_edit_inside`). A source word the model leaves unrendered is an explicit no-rendering (`!`). Verses with nothing to
ask (mostly name lists) or no target text keep the chain's proposal. Model version, design and threshold are in each layer's
`_layer.json`. The questions are built by `lexeme_aligner.eval.jev_check` and the layer by `lexeme_aligner.fullalign_build`
(github.com/bcv-commons/lexeme-aligner).

## File format

```
<iso[0]>/<iso>/<layer id>/<BOOK>_<hash>.json        main: "srcOrd:span ..." per spine verse, as in compact-alignments
<iso[0]>/<iso>/<layer id>/<BOOK>_<hash>.meta.json   channels (function words, profiles, rows), see full-alignments-manual
<iso[0]>/<iso>/<layer id>/_layer.json               version, flags, spine index sha, attribution, profile table
```

`<hash>` = the first 8 hex characters of the book's text fingerprint (compact-alignments README, "The text fingerprint"), the same
name the compact file of that text has, so a file only matches the exact text it was built on. No file holds a word of any text.

## Verse mapping

Spans are keyed by the source verse (Hebrew WLC numbering in the OT); read the target words from the edition's mapped verse
(compact-alignments README, "Verse numbering"). Source and target verses are paired with the verse-mapping tables of **TVTMS**
(STEPBible Data, Tyndale House, Cambridge — CC BY 4.0, https://github.com/STEPBible/STEPBible-Data).

## License

The alignments are CC0 1.0. They hold no text; the edition named by each layer keeps its own licence (Vamvas: public domain; Kuni-Boazi `kvg_wbt`: CC BY-NC-ND 4.0, Wycliffe Switzerland).
