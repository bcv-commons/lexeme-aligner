# `prior_pack/` — language-independent leverage for the aligner (CC-BY)

One row per **original lexeme**, bundling shoresh signals the aligner's gloss/neural runs consume as
priors. Built once (language-independent) via `shoresh/macula/build_prior_pack.py`. Spec:
`internal-docs/prior-pack.md`. **CC-BY-4.0** (MACULA lexeme + lxx_bridge; label-free, no MARBLE).

| column | meaning |
|---|---|
| `lexeme` / `strong` / `testament` / `is_content` / `lemma` | the lexeme + rollup |
| `pos` | normalized POS `{noun,verb,adj,adv,pron,prep,conj,det,num,name,particle}` — dominant MACULA class; `name` = grammatical proper noun (Np) or TIPNR person/place (elohim/theos stay `noun`; YHWH/David/Jesus = `name`) |
| `translit` | romanized form (`da.vid`, `Iēsous`) — for gap-name / cross-script matching |
| `word_class` | `content` \| `function`, derived from `pos` |
| `keyness` | biblical-salience (function-word filter); null for non-content |
| `lxx_greek` / `lxx_hebrew` | cross-testament bridge (OT→Greek / NT→Hebrew), freq-ordered |
| `senses` | `[{stem, sense, share}]` — sense inventory / prior distribution (OT) |
| `neighbors` | `[{lexeme, score, relation, confidence}]` — semantic field (OT) |
| `xling_confidence` | # of published `lexeme-alignments` languages that align this lexeme with a hi_conf dominant (0–7); high=stable anchor, low=fragile |

Consumed: gloss (keyness+lxx+senses extend/clean the mined dict); neural (neighbors tie-break + senses).
Publish to `bcv-commons/strongs` as a `priors` config. `neighbors`/`senses` are OT-only for now.

`xling_confidence` is derived from the aligner's published `lexeme-alignments` (the loop-back); rebuild with `--lexeme-alignments-dir <mirror>` when partitions change.
