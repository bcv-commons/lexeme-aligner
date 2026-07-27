---
pretty_name: Attested target renderings per lexeme sense
tags:
  - bible
  - word-sense
  - lexeme
  - hebrew
license: cc-by-4.0
configs:
  - config_name: default
    data_files:
      - split: train
        path: iso=*/data.parquet
---

# senses_attested — attested target renderings per lexeme sense

The empirical **evidence** layer produced for shoresh (bcv-query data-contract): for a lexeme in a
disambiguated *(binyan, sense)*, which target-language words attest it, with counts. It is the *supply*
that fills shoresh's `senses_i18n/_gaps` demand and cross-checks the `llm_strongs_glosses` predictions —
it **does not replace** shoresh's curated `senses_i18n/<iso>.tsv`; consumed as an HF Parquet dataset.

## Schema (per row)
| column | meaning |
|---|---|
| `lexeme` | MACULA lexeme (the anchor), e.g. `hbo:0006` |
| `stem` | MACULA binyan (`qal/piel/hiphil/…`); empty for non-verbs |
| `sense` | sense **number** (ordinal) — see licensing |
| `surface` | attested target rendering (lowercased) |
| `count` | times this `(lexeme, stem, sense)` → surface was aligned |
| `share` | `count / Σ count for that (lexeme, stem, sense)` **within one `base_text`** |
| `method` | alignment method (`eflomal`) |
| `source_corpus` | the original Hebrew corpus (e.g. `WLC`) |
| `base_text` | the **target edition** attested (e.g. `ind_C01`) — the per-row provenance dimension |

Key: **`(lexeme, stem, sense)`** — MACULA lexeme (anchor; BHSA `lex` dropped) + MACULA binyan + sense
number, read inline from the enriched `lexeme-spine.db`. OT/Hebrew only (senses are Hebrew; Greek tokens
carry none).

**Multi-version:** `base_text` is per-row, so several translations of a language are **pooled into one
`iso=<lang>` partition** — a union of per-edition runs, each row tagged by edition; `share` stays
per-edition. **Cross-edition agreement** (how many `base_text`s attest a given `(lexeme,stem,sense)→
surface`) is the confidence signal, derivable directly from the rows. (Swedish `iso=swe` pools
`swe_fol` Folkbibeln + `swe_svk` Kärnbibeln.)

## Removal / takedown policy
Each row is a **per-edition attestation** carrying its `base_text`, so a rights-holder can request
removal and it is a **clean row-drop** + republish (the dataset is content-addressed via each
partition's `content_sha256`). Because most `(lexeme,stem,sense)→surface` facts are attested by
**more than one edition**, dropping one edition typically leaves the linguistic fact intact via the
others — properly attributed. Rows are **never** re-emitted with provenance stripped: a removed
attestation is removed, not anonymized.

Removals are driven by a committed, auditable config: **`data/senses_exclude.json`** (read
automatically on every build). A row is dropped if it matches any rule; a rule matches when all its
stated fields equal the row's — fields `lexeme, stem, sense, surface, base_text`, omit to wildcard:

```json
{"exclude": [
  {"base_text": "swe_fol"},                       // drop a whole edition
  {"base_text": "swe_fol", "surface": "herren"}   // drop one surface within an edition
]}
```

After exclusion, survivor `share`s **renormalise** (per edition), so a removed row leaves no residue;
the manifest records `excluded: {rules, rows_dropped}` for the audit trail. To action a takedown: add
a rule, re-run `senses_attested` for the affected language, republish.

## Licensing — CC-BY-4.0, deliberately label-free
The **key is MACULA-derived** (`lexeme` + binyan), so this dataset is **CC-BY-4.0** — attribute
Clear-Bible MACULA. We carry the sense **number** only and **no English sense label**: shoresh's sense
labels are UBS-MARBLE "used with permission" (not redistributable), so the payload is pure attestation
`(lexeme, stem, sense#, surface, count)` — CC-BY clean. Regenerate:
`python -m lexeme_aligner.senses_attested --iso <iso> --method eflomal`.
Same git-ignored-Parquet + committed-`manifest.json` layout as `lexeme-alignments`.
