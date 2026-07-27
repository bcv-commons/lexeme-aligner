---
pretty_name: Strong's-aligned multi-word expressions (lexeme → phrase)
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
| `count` | times this (lexeme → phrase) span was aligned |
| `share` | `count / Σ count for that lexeme` |
| `contig` | always true (contiguity is the inclusion criterion) |

## Licensing — CC0-1.0
Phrases + counts + public identifiers (Strong's / MACULA lexeme), no MACULA analytical columns — same
basis as `lexeme-alignments`. Each language's source translation keeps its own terms; see the per-language
`source` pointer in `manifest.json`.

Regenerate: `python -m lexeme_aligner.export_mwe --iso <iso> --method merged`. Bulk Parquet is
git-ignored and published out-of-band; only `manifest.json` (per-language metadata + `content_sha256`)
and this card are committed.
