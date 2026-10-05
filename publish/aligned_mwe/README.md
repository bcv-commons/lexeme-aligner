---
pretty_name: Lexeme-anchored multi-word expressions (lexeme → phrase, Strong's-bridged)
tags:
  - bible
  - word-alignment
  - strongs
  - multiword-expression
  - interlinear
task_categories:
  - translation
  - token-classification
license: cc0-1.0
configs:
  - config_name: default
    data_files:
      - split: train
        path: iso=*/data.parquet
---

# aligned_mwe — multi-word target expressions per lexeme

Where [`lexeme-alignments`](https://huggingface.co/datasets/bcv-commons/lexeme-alignments) is one row per surface
*token*, this is one row per lexeme rendered by a **contiguous multi-word phrase** (חֶסֶד → "kasih setia",
בֵּית → "tempat pengirikan"). Mined from the aligner's per-verse `t_idx` positions: only spans whose
target token positions are **contiguous** (`max−min+1 == len`) qualify — scattered tokens that merely all
linked to a lexeme are dropped (and counted in the manifest as `scattered_dropped`, never silently). Rides
on eflomal's grow-diag-final-and symmetrised alignment.

## Schema (per row)
| column | meaning |
|---|---|
| `lexeme` | MACULA lexeme anchor (`hbo:2545` / `grc:0026`) |
| `strong` | rollup Strong's (`H2545` / `G0026`) |
| `phrase` | the attested contiguous multi-word rendering (lowercased) |
| `n_words` | tokens in the span |
| `source_corpus` | which original-language text the span was aligned against (`WLC`, `Nestle1904`, ...) |
| `base_text` | the target **edition** this phrase was attested in (added 2026-09-30) |
| `count` | times this (lexeme → phrase) span was aligned **in that edition** |
| `share` | `count / Σ count for that lexeme` **within the same `base_text`** |
| `contig` | always true (contiguity is the inclusion criterion) |

**Every edition of a language is pooled into the one `iso=<lang>` partition** — there is no primary edition.
A phrase found in several editions appears once per edition, so a (lexeme, phrase) pair is **not** unique across
rows: group by `(lexeme, phrase)` and sum `count` for a language-level frequency, or filter on `base_text` for one
edition. `share` is a per-edition probability, so it does not sum to 1 across editions. The manifest lists each
language's `base_texts`, rows per edition (`by_base_text`) and a per-edition `sources` licence pointer.
Partitions written before 2026-09-30 have no `base_text` column and cover only the first edition of the language.

## Licensing — CC0-1.0
Phrases + counts + public identifiers (Strong's / MACULA lexeme), no MACULA analytical columns — same
basis as `lexeme-alignments`. Each language's source translation keeps its own terms; see the per-language
`source` pointer in `manifest.json`.

Regenerate: `python -m lexeme_aligner.export_mwe --iso <first-tag> --pool <other-tags> --method all` (the chain does this). Bulk Parquet is
git-ignored and published out-of-band; only `manifest.json` (per-language metadata + `content_sha256`)
and this card are committed.

## Verse mapping

Hebrew and English Bibles number some Old Testament verses differently (Psalm superscriptions, 1 Chronicles 6,
Joel, Malachi, Daniel 4 and 6, ...). Source and target verses are paired with the verse-mapping tables of **TVTMS**
(Translators Versification Traditions with Methodology for Standardisation), part of STEPBible Data by Tyndale
House, Cambridge — **CC BY 4.0**, https://github.com/STEPBible/STEPBible-Data — with each edition's numbering
detected from its own text (cross-checked against bcv-commons/bibles). Source verse references follow the Hebrew
(WLC) numbering of the MACULA source text.
