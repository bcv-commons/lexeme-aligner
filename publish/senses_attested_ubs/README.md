---
pretty_name: Attested target renderings per UBS Hebrew sense
tags:
  - bible
  - word-sense
  - lexeme
  - hebrew
license: cc-by-sa-4.0
configs:
  - config_name: default
    data_files:
      - split: train
        path: iso=*/data.parquet
---

# senses_attested_ubs — attested target renderings per UBS sense

For each Hebrew lexeme and each sense of the **UBS Dictionary of Biblical Hebrew**, which target-language words render
it in practice, with counts, mined from the word alignments of `bcv-commons/lexeme-alignments`. Columns: `lexeme`
(MACULA anchor), `stem` (binyan, empty for non-verbs), `ubs_sense` (UBS sense id, see `senses.tsv`), `surface`, `count`,
`share` (within lexeme, stem, sense, method and edition), `method`, `source_corpus`, `base_text` (the target edition).

**Why UBS senses.** The senses are the manually built, academically maintained sense inventory of the United Bible
Societies; each sense id is bound to individual tokens through the dictionary's own per-occurrence Scripture references.
Function words (prepositions, conjunctions) carry senses too and are included. `senses.tsv` names every id used here
(entry id, lemma, Strong's codes, short English gloss, lexical domain codes).

**License and credit — CC BY-SA 4.0 (share-alike).** This dataset is derived from the UBS Dictionary of Biblical Hebrew,
(c) United Bible Societies 2023, adapted from the Semantic Dictionary of Biblical Hebrew (c) 2000-2023 United Bible
Societies, released under CC BY-SA 4.0 (https://github.com/ubsicap/ubs-open-license). Anything you build from these
files must be released under the same or a compatible license with this credit. It is deliberately a SEPARATE dataset
from `bcv-commons/senses-attested-bhsa` (the retired legacy set: sense numbers from a BHSA-derived clustering, CC BY-NC-SA) so the two licenses never mix.

**Known limits.** Hebrew Bible only (the UBS Greek dictionary is not used yet); the UBS Hebrew dictionary covers about
90% of Old Testament words; ids are bound to tokens by verse-level matching (unique / anchored / nearest, see
`lexeme_aligner/ubs_senses.py`), validated against the spine's own glosses (61% word overlap for unique bindings against
13% for shuffled senses). Tokens in pooled verse ranges are dropped rather than guessed.
