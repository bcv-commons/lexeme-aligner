---
license: cc-by-sa-4.0
pretty_name: "Prior pack: language-independent lexeme priors for alignment"
language:
  - hbo
  - grc
tags:
  - biblical-hebrew
  - koine-greek
  - word-alignment
configs:
  - config_name: default
    data_files: "prior_pack.parquet"
---

# `prior_pack/` — language-independent leverage for the aligner (CC BY-SA)

One row per **original lexeme**, bundling shoresh signals the aligner's gloss/neural runs consume as
priors. Built once (language-independent) via `shoresh/macula/build_prior_pack.py`. Spec:
`internal-docs/prior-pack.md`. **CC BY-SA 4.0** (MACULA lexeme + lxx_bridge, CC BY 4.0; `keyness` uses modern-Hebrew word frequencies from
[wordfreq](https://github.com/rspeer/wordfreq), whose data is CC BY-SA 4.0; label-free, no MARBLE).

## Changed 2026-10-04: licence CC BY-SA 4.0

Previously labelled CC BY 4.0. The `keyness` column (biblical frequency minus general-language frequency) uses
modern-Hebrew frequencies from `wordfreq`, whose data is CC BY-SA 4.0, so the pack carries share-alike. No data
changed. Attribution: wordfreq (Robyn Speer), data derived from sources including Wikipedia, OpenSubtitles and
Google Books Ngrams; see its README.

## Changed 2026-10-03: BHSA-free

The ETCBC BHSA database is licensed CC BY-NC-SA, which does not fit this openly licensed pack. Two columns changed:

- **`senses` is removed.** It was our own sense clustering, built on BHSA clauses. For a sense inventory
  use [`bcv-commons/senses-attested`](https://huggingface.co/datasets/bcv-commons/senses-attested)
  (CC BY-SA 4.0, keyed on UBS Dictionary of Biblical Hebrew senses).
- **`neighbors` comes from the BHSA-free semantic-neighbors pack**, the same build behind
  [`bcv-commons/semantic-neighbors`](https://huggingface.co/datasets/bcv-commons/semantic-neighbors). It is
  keyed on MACULA lexemes like the rest of this pack, which fixes an old key mismatch: Hebrew lexemes with
  neighbors went from 3,012 to 4,783.

Also: 34 Hebrew proper nouns (e.g. H11 Abaddon, H435 Eliezer) now have `pos = name` instead of `noun`.

## Columns

| column | meaning |
|---|---|
| `lexeme` / `strong` / `testament` / `is_content` / `lemma` | the lexeme + rollup |
| `pos` | normalized POS `{noun,verb,adj,adv,pron,prep,conj,det,num,name,particle}` — dominant MACULA class; `name` = grammatical proper noun (Np) or TIPNR person/place (elohim/theos stay `noun`; YHWH/David/Jesus = `name`) |
| `translit` | romanized form (`da.vid`, `Iēsous`) — for gap-name / cross-script matching |
| `word_class` | `content` \| `function`, derived from `pos` |
| `keyness` | biblical-salience (function-word filter); null for non-content |
| `lxx_greek` / `lxx_hebrew` | cross-testament bridge (OT→Greek / NT→Hebrew), freq-ordered |
| `neighbors` | `[{lexeme, score, relation, confidence}]` — semantic field (OT); `confidence` is `high`, `prior` or `recall` |
| `xling_confidence` | # of published `aligned-lex` languages that align this lexeme with a hi_conf dominant (0–7); high=stable anchor, low=fragile |

Consumed: gloss (keyness+lxx extend/clean the mined dict); neural (neighbors tie-break).
`neighbors` is OT-only for now.

`xling_confidence` is derived from the aligner's published `aligned-lex` (the loop-back); rebuild with `--aligned-lex-dir <mirror>` when partitions change.
