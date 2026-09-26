# Architecture — the five data structures

**Status:** naming decision, 2026-09-25; gram-struct generator built the same day (`gram_struct.py`);
full-align's record, layout and gold-conversion path settled the same day (§4). This document lays
down the names used **going forward** for the five data structures this repo produces or consumes.
Existing code, config files and published datasets keep their current names for now — nothing has
been renamed. Each section below says what
the structure *is*, what it looks like today, where it lives, what feeds it, what consumes it, and what
is still undecided. Where a section describes a target rather than something that exists, it says so.

| name going forward | what it is | current name(s) in code / on disk | shape |
|---|---|---|---|
| **lex-lexicon** | type-level lexicon: what renders each source lexeme, across a whole language | `publish/lexeme-alignments`, `senses_attested`, `aligned_mwe`, `target-stopwords`, `target-morphology`, `cross-lingual-span-profile` (all live on HF under `bcv-commons/`) | per **language** (editions pooled) |
| **gram-struct** | per-language grammatical/typological facts, in ONE internal structure regardless of where they came from | generated: `gram_struct.py --build` → `config/gram_struct/` (gitignored) from `config/grambank/`, `config/typology/`, `config/constituent_order/`, `config/spanext_flags.json`, `config/fertility_flags.json`, `config/llm_conventions/` — nothing reads the merged view yet | per **language** (a fact sheet, not text data) |
| **gram-align** | per-verse, position-level alignment from the statistical chain, compact | `publish/compact-alignments` (HF `bcv-commons/compact-alignments`), `compact_align.py` | per **edition** |
| **full-align** | the complete per-edition alignment as provenance-tagged rows: statistical layer + manual (gold) layer + LLM layer | `align_<method>_<tag>_<BOOK>.jsonl` (statistical), `align_llm_<tag>_<BOOK>.jsonl` (LLM), the gold parquets under `pipeline/vendor/resources/strongs/attestations/` (manual) — not yet one artifact, not published | per **edition** |
| **edition-struct** | per-edition facts about one *text*: where it came from, what it is, how to read it | not yet one thing — spread over `config/pins/`, `textual_basis.json`, `text_strip_rules.json`, `versification.json`, `language_editions.json`, `legacy_bare_iso_tags.json` | per **edition** (a fact sheet, not text data) |

Two are *language*-shaped (lex-lexicon, gram-struct), three are *edition*-shaped (gram-align, full-align,
edition-struct). The two fact sheets pair off with each other the way the lexicon and the alignments do:
gram-struct says how a *language* renders source structure, edition-struct says what one *text* of that
language is and how to read it. gram-struct is the hinge for alignment quality: built from lex-lexicon
statistics plus external typology, read by both alignment structures.

```
                 external typology                lex-lexicon (own alignments, aggregated)
        (Grambank · WALS · lang2vec · …)          lexeme-alignments · aligned_mwe · stopwords · morphology
                        \                                   /
                         v                                 v
                            gram-struct  (one per-LANGUAGE fact sheet)
                                   |                    |
        edition-struct ----------> v                    v
   (one per-EDITION fact sheet:   statistical chain per edition  --->  gram-align   (compact, positional)
    provider · pin · versification ·                                        |
    textual basis · strip rules · books)                                    v   + LLM pass (residue / verify / full)
                                                                        full-align  <--- manual gold, converted to the
                                                                       (rows, 3 layers)    same rows (Clear · SWORD · HELFI · gbt)
```

---

## 1. lex-lexicon

**What it is.** The type-level view: for one language, aggregated over every verse and every pooled
edition, *what target words render this source lexeme, how often, how confidently*. This is the "aim 1"
output, published to Hugging Face, and the only one of the four that downstream consumers (bcv-query,
the gloss bootstrap, the internal recipes) already depend on. Position is deliberately thrown away —
that is what makes it a lexicon and not an alignment.

**What exists today** (all under `publish/`, each with a `manifest.json` + `README.md` dataset card,
`iso=<iso>/data.parquet` partitions, one per language):

| dataset | rows are | schema | languages |
|---|---|---|---|
| `lexeme-alignments` | (surface → lexeme) attestations | `surface, lexeme, method, base_text, source_corpus, count, hi_conf` | 1,629 |
| `senses_attested` | (lexeme, stem, sense → surface) attestations, OT/BHSA sense numbers | `lexeme, stem, sense, surface, count, share, method, source_corpus, base_text` | 462 |
| `aligned_mwe` | contiguous multi-word renderings of one lexeme | `lexeme, strong, phrase, n_words, source_corpus, count, share, contig` | 1,629 |
| `target-stopwords` | induced function-word list | `<iso>.txt`, one word per line | 1,628 |
| `target-morphology` | induced stems / productive affixes | `prefixes, stems, suffixes` | 1,627 |
| `cross-lingual-span-profile` | per-lexeme span-length tendency across all languages | one `profile.json` | (cross-language) |

**Invariants worth keeping** (from `advanced-docs/publishing-principles.md` and the module docstrings):
- the anchor is the MACULA `lexeme` (`hbo:0430`, `grc:2424`), never bare Strong's and never BHSA `lex`;
- rows are an **additive union** across two provenance axes, `method` (eflomal / gloss / gapfill / …)
  and `base_text` (edition) — nothing is merged away, `share` is within (method, base_text);
- several editions of one language pool into one `iso=<lang>` partition; cross-edition agreement is the
  confidence signal, not a merge.

**Feeds:** gram-struct (see §2 — the statistically-derived facts are computed *from* these), the gloss
method's own bootstrap priors, gapfill's seed dictionary, span_extension's identity guard
(`build_surface_identity` rebuilds exactly this table in memory from the base-chain output).

**Consumes:** the per-edition base chain (`eflomal → gloss → span_extension → gapfill → residual`) via
`export_lex` / `export_mwe` / `senses_attested` / `export_stopwords` / `export_morph`.

---

## 2. gram-struct

**What it is.** One per-language fact sheet of grammatical / typological properties, in a single
internal structure, *regardless of provenance*: taken from Grambank where Grambank covers the language;
otherwise from another external source (WALS, lang2vec/URIEL, …); otherwise derived statistically from
our own lex-lexicon and alignments; otherwise absent — and every entry says which of those it is. It is
the one place the two alignment structures look to learn *how this language renders source structure*
(which side a postposition sits, whether there are articles, whether a verb needs a free subject
pronoun, whether fertility priors help, …).

**Status: the merged view is generated, nothing reads it yet.** `python -m lexeme_aligner.gram_struct
--build` (built 2026-09-25, `tests/test_gram_struct.py`) writes the four provenance partitions and the
merged per-language file into `config/gram_struct/` — **gitignored, regenerated from committed inputs,
never hand-edited**. First real build over the 1,629 published languages: external 963 · imputed 279 ·
derived 57 (`config/constituent_order/<iso>.json` only) · measured 12 · merged 1,128; 501 published
languages had no source at all. **Roadmap item D0** (§4R of the plan; `derive_typology.py`, built
2026-09-25) then persisted the statistics the pipeline already computes for every language into
`derived/`, gated by alignment quality (≥0.80 eflomal coverage, ≥0.40 per-occurrence hi-conf share,
≥3,000 aligned verses, gold health ≥0.6 where a gold exists — never a mechanism verdict, never
`spanext`/`gapfill`/`residual`/`llm` output, only `eflomal`+`gloss`): of all 1,629, **262 failed the
gate** (get a `_derived_meta`-only stub with the reason, no facts), **1,367 passed**, of which **273
had enough OT phrase data to derive ≥1 real slot** (possessor / subject_verb / object_verb from
`gapfill`'s `rec_after_rate`/`func_order`, refactored out of `gapfill.main()`'s own inline computation
into a reusable `compute_order_stats()`) and the remaining 1,094 (mostly NT-only languages, which this
OT-only pass cannot give a slot to) still got their audit facts (`multiword_rates`) persisted.
`config/constituent_order/<iso>.json` itself grew from 62 files to **301** as a side effect (D0's own
scope target was "~181"; the real gate passed more languages than estimated). Net after D0: **derived
273 with a real slot · merged 1,179 · 450 published languages still have no gram-struct fact at all**
(down from 501). **First derived-vs-Grambank agreement numbers, whole corpus** (not the 18-language
gold sample — every published language with both a derived and an external/imputed direction for the
slot): **possessor (GB065) 99/111 = 89.2%, subject_verb (GB131/133) 50/54 = 92.6%, object_verb
(GB131/133) 73/79 = 92.4%** — two of three clear the plan's ≥90% bar on a real sample size; possessor
sits just under it (see `internal-docs/review-2026-09-25/derived_typology.md` for the
expected-accuracy literature this is tested against).

**Roadmap item D1** (Greek-first slots, same day, extending `derive_typology.py`) then reached the
1,211 NT-only languages for the first time. Of its four planned slots, only `adposition`/
`adposition_word` shipped — `article_word`, `possessive_word`, and `negation` **failed their own
mandatory known-answer check** (hin, which has no articles by Grambank, read as 76% "has articles")
and were withheld entirely rather than published flagged `experimental` as a workaround; a known-
answer failure means the selector is wrong, not that the fact is merely uncertain. `adposition` passed
its known-answer check on eng/hin/arb/fra/spa/fin/cmn/rus (hin correctly postpositional at a 79.2%
after-rate; fin correctly ambiguous, matching Grambank's own GB074=GB075=1 coding) and reached every
gate-passed language with an NT: **derived 273→1,367 with a real slot, merged 1,179→1,543, no
gram-struct fact at all 450→86** — the largest single coverage jump in this whole roadmap, from one
slot. A real bug was found and fixed mid-build: the anchors D0 already used silently drop non-content
pairs, but all four D1 markers (prepositions, articles, pronouns, negators) are non-content by
definition, so the first implementation produced near-uniform garbage for every language — fixed with
a from-scratch anchor scan that keeps them.

**Roadmap item X3** (Glottolog kin prior, same day) closed most of the remaining gap without any new
alignment or corpus work — a SQLite join against bcv-query's sibling `languages.db` (7,925 languages,
a `relatedness` table of 171,818 rows, genetic-tree distance 0–13, CC-BY-4.0). For a language with no
external/imputed/derived fact for a slot, `kin_prior.py` takes the nearest relative that DOES have one.
New fifth partition, `kin/`, added last and lowest-priority in `gram_struct.py`'s merge — it can only
ever fill a slot key genuinely absent from every other partition, never a resolved-null and never
override anything, which is enforced structurally (the function itself only emits missing keys) rather
than by a special-case exception. Leave-one-out validation is real and distance-decaying exactly as
genuine relatedness should behave, not noise: adposition 96.1%→86.7% agreement from distance band 0-1
to 7-13, object_verb 96.4%→87.5%, subject_verb 92.9%→77.8%, possessor 91.2%→88.9%, article the weakest
at 93.1%→63.4% (consistent with lang2vec's own article-slot weakness in Step 2). **76 of the 86
languages with no fact now get one from a relative; only 10 published languages remain with zero
gram-struct fact at all** at this point in the roadmap.

**Roadmap item I1** (URIEL+ as a second `imputed` source, same day, `pipeline/lexeme_aligner/
uriel_plus.py`) closed two more. Validated with Step 2's own protocol (≥90% held-out agreement vs
Grambank) on the real `urielplus` 1.3.1 package (`mean_imputation()`; a real packaging finding of its
own — the base install lacks its imputation dependencies, `pip install "urielplus[imputation]"` pulls
~20 packages including scikit-learn and cvxpy): **adposition 98.2% (646/658) and object_verb 97.4%
(680/698) shipped, both beating lang2vec's own agreement on the same slots**; article 90.5% on too
small a sample (n=21) to trust over lang2vec's own prior 73.8% failure, possessor 66.0%, and
subject_verb 82.5% all correctly withheld. Of the then-10 no-fact languages, only **`ktm` and `kze`**
get a validated URIEL+ fact; beyond those, URIEL+ adds a genuinely new fact for **150** published
languages lang2vec's own ISO list never reached at all. Final coverage: external 963 · imputed
**429** (was 279) · derived 1,367 · measured 14 · kin **727** (was 717 — kin's own neighbour pool grew
once imputed had more resolved values to draw from) · merged 1,629 · **no-source 8**
(`bux, flh, jen, khj, kql, lng, njd, zbu`). 471 tests passing.

*Process note on this pair of roadmap items:* both X3 and I1's delegated work staged its own changes
and edited this file directly, against explicit instructions not to — in both cases the substance was
independently re-verified afterward and found accurate (I1's first attempt, separately, did the
opposite failure: it reported completion having done no real work at all — re-run before anything in
its report was trusted). Every number in this section was re-derived directly from the committed code
and the real `config/gram_struct/` output, not taken from either report at face value.

Every existing reader still reads the separately-started files below; folding readers
over to the merged view is additive, one at a time. Superset of inputs, today:

| file today | what it holds | provenance | keyed by |
|---|---|---|---|
| `config/grambank/features.json` | 27 Grambank features per ISO (`GB074`, `GB075`, …) | external (Grambank, vendored, pinned) | iso → feature code → `"0"/"1"` |
| `config/typology/directions.json` | 5 direction slots (`adposition`, `article`, `possessor`, `subject_verb`, `object_verb`) each `{direction, source, confidence}` | Grambank > WALS > lang2vec, each source validated ≥90% against Grambank before use (`typology.py`) | iso → slot |
| `config/constituent_order/<iso>.json` | `pair_order_kept`, `function_drift` — how the language reorders BHSA phrase functions | **derived from our own alignments** (`constituent_order.py`, refreshed by `derive_typology.py`), OT-only | iso (301 files, was 62) |
| `config/spanext_flags.json` | per-language verdicts for span_extension's opt-in triggers | **measured against gold** by a human, recorded once | iso → flag → bool |
| `config/fertility_flags.json` | per-language `enabled` / `lambda` for eflomal fertility priors | measured against gold, recorded once | iso → `{enabled, lambda}` |
| `config/llm_conventions/<iso>.md` | free-text grammar notes fed to the LLM prompt (postpositions, auxiliaries, light verbs, name spelling) | hand-written from real examples; `analyze_language.py` proposes candidates | iso (9 files) |
| *(not persisted)* `analyze_language.analyze()` report | per-POS multiword-rate anomalies (`findings: [{risk, pos, …}]`) | derived from own alignments, recomputed every run | iso |
| *(not persisted)* gapfill's `rec_after_rate`, `func_order`, `morph_surf`, `target_pos` | learned order/agreement statistics | derived from own alignments, recomputed inline | iso |
| `span_extension.diagnose()` output | block rates + high-volume stopword table (triage, gold-free) | derived, on demand | iso |

**Not gram-struct** (config that happens to live next to it): everything edition-shaped — the edition
catalog (`language_editions.json`, `sources.json`, `pins/`, `dbt_catalog/`) and per-text handling
(`text_strip_rules.json`, `textual_basis.json`, `versification.json`) — belongs to **edition-struct**
(§5); gold registration (`gold_langs.json`) is evaluation config; source-side (Hebrew/Greek) tables
(`light_lexemes.json`, `hebrew_lexeme_strong.json`, `greek_morph_strong.json`, `canonical_index/`) are
neither.

**Relationship to Grambank — borrow the taxonomy and the codes, not the format.** Grambank (v1.0,
**CC-BY-4.0**; Glottolog, used for the Glottocode→ISO mapping, likewise) is the largest single input
and more of it will be pulled in over time. But its atomic unit is a yes/no question per language
(`GB074 "Are there prepositions?" = 0/1/?`) and what every consumer here reads is a *slot with a value*
(`adposition: before | after | null`). Every slot folds two to four Grambank questions into one answer
(GB074+GB075 → adposition direction; GB020–023 → article existence *and* order; GB131/133 → verb
position) and `direction_for` / `typology.py` exist precisely to do that folding once, with the
ambiguous cases (`both = 1` → `null`) handled in one place. A Grambank-lookalike schema would push
that logic back out into every consumer. It also cannot carry the other sources natively: WALS 86A is
three-valued, lang2vec is a probability, `constituent_order` is a rate over *n* observations, a
mechanism verdict is a human-measured boolean. So: **two layers, never merged** — the raw snapshots
stay exactly as vendored (`config/grambank/features.json` keeps Grambank's own codes and `0/1/2/3/?`
semantics; WALS and lang2vec likewise), and gram-struct is the derived, slot-shaped layer above them.
Every fact that came from Grambank cites its codes (`"codes": ["GB074", "GB075"]`) so it is auditable
back to the codebook, and slots reuse Grambank's own names and domain grouping (nominal / verbal /
clause) wherever a slot maps onto its questions — adding the next Grambank feature is then a same-
shaped one-line addition, and new vocabulary is invented only for what Grambank does not have (rates,
verdicts, `null`-as-fact).

**Publication is planned, so the layout is publication-shaped from the start.** gram-struct is
internal config today and will very likely be published. Three license classes then coexist in it:
Grambank/WALS-derived facts are CC-BY-4.0 (attribution only); lang2vec-derived facts are
**CC-BY-SA-4.0** (share-alike — the owner decision already keeps them in their own file with their own
license line); our own derived statistics and measured verdicts follow the repo's precedent for
derived alignment data, **CC0-1.0** (`constituent_order/README.md`). Nothing NC-licensed (taggedPBC)
ever enters it. To keep licensing a *directory boundary* rather than a per-fact filter — and to publish
the layer that is actually new (the fused, provenance-tagged slot view plus alignment-derived facts
nobody else has) rather than re-ship Grambank's and WALS's own cells — the target layout is one
dataset partitioned by provenance class:

```
gram-struct/
  README.md                 # dataset card: one license line per partition
  external/<iso>.json       # CC-BY-4.0    Grambank/WALS-derived slots, source codes cited
  imputed/<iso>.json        # CC-BY-SA-4.0 lang2vec-derived slots ONLY, physically apart
  derived/<iso>.json        # CC0-1.0      our alignment statistics (order profile, rates, block rates)
  measured/<iso>.json       # CC0-1.0      mechanism verdicts — dated, gold named, human-recorded
```

The internal convenience view, `config/gram_struct/<iso>.json`, is the **merge of the four partitions,
built by `gram_struct.py` and never hand-edited** (the merge asserts no key is set by two partitions),
so the internal file and the publishable partitions cannot
drift. Its shape (what the generator writes; values are hin's real ones — `multiword_rates` is the one
key not emitted yet, because `analyze_language.analyze()` never persists them):

```json
{
  "iso": "hin",
  "adposition":   {"direction": "after",  "source": "grambank", "codes": ["GB074","GB075"], "confidence": 1.0},
  "article":      {"direction": null,     "source": "grambank", "codes": ["GB022","GB023"], "confidence": 1.0},
  "possessor":    {"direction": "before", "source": "grambank", "codes": ["GB065"],         "confidence": 1.0},
  "subject_verb": {"direction": "before", "source": "grambank", "codes": ["GB133","GB131"], "confidence": 1.0},
  "case_marking":     {"present": true,  "source": "grambank", "codes": ["GB070","GB072","GB074","GB075"]},
  "subject_indexing": {"present": false, "source": "grambank", "codes": ["GB089","GB090"]},
  "possession_affix": {"present": false, "source": "grambank", "codes": ["GB430","GB431","GB432","GB433"]},
  "constituent_order": {"source": "derived", "n_verses": 20819, "content_sha256": "…",
                        "pair_order_kept": {"Pred>Subj": 0.44}},
  "multiword_rates":   {"source": "derived", "content_sha256": "…", "name": 0.031, "noun": 0.052, "verb": 0.081},
  "mechanisms": {
    "spanext.relation_trigger":  {"enabled": true,  "source": "measured", "date": "2026-09-24", "gold": "clear/IRVHin"},
    "spanext.definite_trigger":  {"enabled": false, "source": "measured", "date": "2026-09-24", "gold": "clear/IRVHin"},
    "spanext.typology_fallback": {"enabled": false, "source": "measured", "date": "2026-09-24", "gold": "clear/IRVHin"},
    "fertility_priors":          {"enabled": true,  "lambda": 2.0, "source": "measured", "date": "2026-09-24", "gold": "clear/IRVHin"}
  },
  "conventions_md": "config/llm_conventions/hin.md"
}
```

Rules the layout encodes — all already in force informally, made contractual because the file will be
read by people outside this repo:
- **provenance on every fact** — `grambank` / `wals` / `lang2vec` / `derived` / `measured` / `manual`
  — is a hard requirement, not a nicety: it is what lets a publisher split or drop by license class
  mechanically, and what lets a consumer say "external or derived only, never a mechanism verdict";
- **direction from typology, existence from evidence** — a fact saying *which side* comes from a
  typology source; a fact saying *this language actually undershoots here* comes from our own data
  (the audit); separate keys, separate partitions;
- **mechanism verdicts are recorded, never inferred** — `measured/*` entries exist only after a human
  has scored against gold (the `spanext_flags.json` / `fertility_flags.json` rule), carry the date and
  the gold they were scored against, and no field anywhere in the file is computed from other fields
  in it;
- **`null` is a value, absence is "unknown"** — `"article": {"direction": null, "source": "grambank"}`
  (hin genuinely has no article direction) is a different published statement from the key being
  absent (no source covers this language for this slot); consumers gate on the difference;
- **`derived` changes on every re-alignment, `measured` must not** — derived partitions carry the
  same `content_sha256` discipline as the other published datasets, so a republish that moves a rate
  is visible, while a measured verdict only changes when someone re-measures and re-dates it.

**Feeds:** gram-align (the statistical chain reads it at `span_extension`, `gapfill`, and — since
Step 3 — at eflomal time via fertility priors) and full-align (the LLM prompt already carries
`[construct]`/`[genitive]`/`[dative]` source tags and the language's conventions note).

**Consumes:** external typology snapshots (vendored, pinned in `config/PROVENANCE.txt`) and lex-lexicon.

**Open:** whether the derived-order profile is worth extending to the remaining slots the way Östling
2015 did (the plan's Step 2 "empirical source (D)", not built); whether `config/llm_conventions/<iso>.md`
(hand-written prose, `manual` provenance) is published alongside `measured/` or stays internal —
it is the one part of gram-struct a downstream aligner would want and the one part that is not data.

---

## 3. gram-align

**What it is.** The per-verse, position-level alignment produced by the statistical chain for one
*edition*: for each verse, which target-token positions each source content lexeme claims. This is
today's `compact-alignments`, unchanged — gram-align is its name going forward. It complements
lex-lexicon and cannot be derived from it (aggregation discards position) nor replace it (position
alone can't give a lexeme's dominant cross-corpus rendering).

**What exists today** (`publish/compact-alignments`, HF `bcv-commons/compact-alignments`, 1,629
languages; `docs/compact-alignments.md` is the format reference):
- a shared verse index, published once per book: `_index/<BOOK>.json = ["BOOK C:V", ...]`;
- per edition, per book: `<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json = ["srcOrd:span ...", ...]`,
  position-parallel to that book's index;
- the string format: `"0:1 1:3,5 2:4 3:6-8"` — `srcOrd` is the 0-based ordinal among the verse's
  *content* lexemes in spine order; a span is one index, a contiguous `a-b` range, or a scattered
  `a,b,c` list; an unaligned lexeme is simply absent; `""` is a verse with nothing aligned;
- **contiguity is a published confidence signal**: `-` vs `,` measures ~20pt of token precision apart on
  languages whose gold can judge it (eng +22.2, hin +18.5);
- the alignment source is the additive union `eflomal + gloss + gapfill` (exact-tag matched), the same
  "what is actually aligned" definition used everywhere else;
- the manifest records per language → per edition → books, source/license pointer, `tokenizer_version`.

**What it does *not* carry:** per-span `method` or score (the union is flattened), and any
function-word alignment (only content lexemes get an ordinal). Both are deliberate compactness choices,
and both are exactly what full-align (§4) restores — which is why full-align is a row format and
gram-align stays the compact string: two shapes for two jobs, not one format with a flag.

**Feeds:** full-align (it is the base the LLM pass reads and extends); the reverse-check / QA tooling;
any "show me verse X in translation Y" consumer.

**Consumes:** the per-edition base chain's `align_<method>_<tag>_<BOOK>.jsonl`, gram-struct (through
that chain), the canonical index.

---

## 4. full-align

**What it is.** The *complete* per-edition alignment, as provenance-tagged **rows** (not the compact
string), in up to three layers for the same text:
1. **statistical** — the base chain's own rows (`eflomal`/`gloss`/`spanext`/`gapfill`), the same
   decisions gram-align flattens, here with `method`, score and function-word links kept;
2. **manual** — gold: a human-made per-occurrence alignment of the same edition (Clear, SWORD tags,
   HELFI, gbt), converted into the *identical* record so it is no longer a separate format per source
   — "gold" is a **role** a consumer assigns from the attribution, not a distinct data shape;
3. **model** — what an LLM pass adds: fills for the residue the chain left unaligned, re-checked
   decisions where the chain was unsure (`verify`), or a whole-verse reference (`full`). Opt-in per
   edition, paid per verse; never a precondition for the other two layers or for gram-align/lex-lexicon
   to exist (the default chain must keep working for languages with no LLM coverage at all —
   `gapfill_align.py`'s mission note).
Any layer may be absent; an edition with only layer 1 is a valid full-align, and so is one with only
layer 2. The layers are never merged into one another: agreement between them is a *derived* signal a
consumer computes, exactly the additive-union rule lex-lexicon already follows.

**What exists today** (`llm_align.py`, `llm_prompt.py`, `llm_providers.py`, `llm_report.py`;
experiment design in `internal-docs/llm-align-experiment-plan.md`; not published anywhere):
- output `align_llm_<out_tag>_<BOOK>.jsonl`, in gapfill's record shape with `method="llm"` and
  `prior="llm_<strategy>"`, so the existing scorers (`pos_score --method llm:<tag>`, `score_gapfill`,
  `benchmark`) evaluate it with no new code; plus a cost ledger `llm_usage_<out_tag>.json`;
- strategies: `gap` / `gap-seeded` / `lexeme-grouped` (only the residue — on MAT/MRK/ROM/1CO the chain
  leaves 1.4% fra / 3.8% hin of content tokens unaligned), `verify` (re-check eflomal pairs below score
  0.9 — a much larger pool), `full` (whole verse, as a reference point), plus a review pass over a
  completed `full` run;
- the prompt already consumes gram-struct: hard source tags from the spine (`[construct]`, `[genitive]`,
  `[dative]`, `[passive·aorist·3sg]`, construct partners, `{clause-verb: hN}` from `head_idx`), the
  language's whole-lexicon seed renderings (lex-lexicon), and `config/llm_conventions/<iso>.md`;
- provenance is preserved: a `verify` decision that overrides an eflomal pair is a new row with
  `method="llm"`, the eflomal row is not rewritten.

**Settled 2026-09-25 — include, rows, attribution.** The include-vs-delta question is closed by the
manual layer: gold needs per-row provenance and carries function-word links, neither of which the
compact string can hold, so full-align is the row format and it *includes* every layer for the edition
(one download answers "show me this verse fully aligned, and by whom"). The duplication of gram-align's
bytes is accepted; gram-align stays the small download for readers who want positions only.

**The record.** The base chain's existing pair record (`run_pilot`/`gapfill` shape), unchanged, plus
one `attribution` block. `method` remains the *how* axis and keeps lex-lexicon's vocabulary —
`eflomal` / `gloss` / `spanext` / `gapfill` / `llm` / `manual` / `transfer` (Clear's own parquet already
uses `manual` and `transfer`) — so no new `gold` value is invented; who did it and under what terms is
the attribution's job:

```json
{"h_idx": 3, "lexeme": "grc:0011", "strong": "G0011", "t_idx": [0, 1], "target": "अबराहम से",
 "content": true, "method": "manual", "score": null,
 "attribution": {"source": "clear", "kind": "manual", "base_text": "IRVHin", "license": "CC-BY-4.0",
                 "source_ids": ["n40001002003"], "target_ids": [4000100200, 4000100201]}}
```

- statistical rows: `{"source": "lexeme-aligner", "kind": "statistical", "license": "CC0-1.0"}`;
- model rows: `{"source": "anthropic/claude-sonnet-5", "kind": "model", "prompt_version": 11, "strategy": "verify"}`;
- manual rows keep the gold source's own raw ids (`source_ids` / `target_ids`) so nothing the source
  knows is lost — including the occurrence ambiguity the conversion could not resolve (see below),
  and HELFI's morpheme boundaries for fin as an extra `morphemes` field.

**Layout — partitioned by attribution so license is a directory boundary** (the same move as
gram-struct's partitions), per edition, per book, Parquet:

```
full-align/<iso>/<edition>/statistical/<BOOK>.parquet              CC0-1.0
full-align/<iso>/<edition>/manual/<base_text>/<BOOK>.parquet       the gold source's license (Clear CC-BY-4.0 · ChiUns PD · HELFI CC-BY-4.0 · gbt CC0)
full-align/<iso>/<edition>/llm/<cell>/<BOOK>.parquet               CC0-1.0
manifest.json                                                      per edition: layers present, per-layer license + attribution,
                                                                   coverage (verses mapped / refused / ambiguous), gold health
```

The `<base_text>` and `<cell>` levels were not in the first draft of this layout; the first real build
forced them — one edition can carry several golds (fra: LSG + Segond1910; rus: RUSSYN + RusVZh; spa:
RV09 + RV1910; eng: BSB + gbt) and hin alone has 14 LLM cells (strategy × model × prompt version), and
layers must never be merged, so each gets its own directory.

**Built 2026-09-25** (`gold_to_fullalign.py`, `tests/test_gold_to_fullalign.py`; one additive field on
`pos_score.GoldVerse.raw` so the gold's own ids survive the conversion — scoring never reads it). First
`--all-gold` run: **15 languages, 2,053 Parquet files, 300 MB under `publish/full-align/`** (bulk
gitignored, `manifest.json` + `README.md` tracked) plus **17 MB under `pipeline/work/full-align/`** for
the three vendor-only golds. No edition lacked base-chain output. Three things did not map onto the
spec as written and are recorded rather than papered over: (1) **gbt is a gloss layer, not a positional
one** — its rows keep the gloss in `target` and get a `t_idx` only where the gloss phrase matches our
edition's text uniquely (spa 109k of 316k; hun 4k of 13k), the rest carry `t_idx: null`; (2) HELFI's
morpheme boundaries are not recoverable from the attestation parquet (the word projection already
happened in `helfi_source.py`), so the planned `morphemes` field is not emitted until that module
keeps them; (3) `gold_health` had to be generalized inside the converter because `contest_rule`'s is
Clear-hardcoded — the generalized one should move back into `contest_rule`. Per-edition health ranges
from .96 (YLT, AVD) down to .567 (por/JFA11, `transfer`) and .385 (rus/RUSSYN, published
`quarantined: true` as decided above).

The edition tag is the *same* tag the statistical run uses — a gold edition is not a different edition,
it is another layer on the same text. The manifest's gold-health gate (`contest_rule`'s positional-vs-
lexical agreement) is recorded per manual partition, not used to hide one: Clear's rus (RUSSYN, ~57% of
per-verse pairings positionally wrong) is published *as data* with `health` and `quarantined: true`
rather than silently omitted — but never counted as gold by anything in this repo.

**Gold inventory → what converts** (from `config/gold_langs.json` and the attestation parquets):

| gold source | languages / editions | per-occurrence? | publishable? |
|---|---|---|---|
| Clear manual | arb (AVD **+ ONAV**), asm, ben, eng (BSB **+ YLT**), fra LSG, hau, hin, spa RV09 | yes, positional | CC-BY-4.0 |
| Clear transfer | por JFA11 | yes, machine-projected (`method: transfer`) | CC-BY-4.0 |
| Clear rus | RUSSYN | yes — **defective**, published quarantined | CC-BY-4.0 |
| SWORD | cmn ChiUns | yes | PD ✓ — spa RV1909 / fra Segond1910 / rus RusVZh are CrossWire-restricted: **vendor-only, never published** |
| HELFI | fin | yes, morpheme-level → word | CC-BY-4.0 (alignment data only; the AIKA morphology dir is NC-ND and unused) |
| gbt | hun, njm, tam, tel, rus-NT (+ thin eng/spa/por/hin) | gloss phrase per source word | CC0 |
| karnbibeln lexicon | swe, swk | **no** — a type-level dictionary | stays lex-lexicon-shaped gold; cannot be a full-align layer |

Twelve publishable per-occurrence gold editions across eleven languages; three stay internal; two are
not alignments at all.

**Conversion mechanics** — this is what `pos_score.load_gold` already does at scoring time, made
persistent instead of discarded: each gold row's `(verse, strong, k-th occurrence in verse)` resolves
to the spine token, which supplies `h_idx` *and the MACULA lexeme* (every gold is bare Strong's; the
spine lookup is the crosswalk, no id map needed); `target_id` maps to our token positions through the
Clear-tokenization reconstruction over our own edition text (validated 98.6–100% per language). gbt
differs only on the target side (its source ids are MACULA ids → `h_idx` directly; its targets are its
own text's ids → text match). The known losses are reported in the manifest, never silent: **refused
verses** (tokenization mismatch — hin 3,640 of 29,019, 12.5%), **ambiguous verses** (a Strong's
occurring a different number of times on the two sides — hin 7,406 links), and rows whose target falls
outside our text. Module: `gold_to_fullalign.py` (in progress); the same health gate as `gold_langs.json`.

**What this captures that nothing in the repo keeps today:** function-word links (Clear tags them,
gram-align drops them), the ONAV and YLT second-edition golds, HELFI's morpheme boundaries, gbt's
`kind` (1:1 / 1:many / suffix_pending), and Clear's raw `source_id`s.

**Round-trip source: the BSB Translation Tables** (`bsb_tables.py`, built 2026-09-25). The Berean
Standard Bible's master interlinear (`https://bereanbible.com/bsb_tables.tsv`, 85.5 MB, 23 columns, one
table — 39 OT books Hebrew then 27 NT books Greek, sorted by BSB Sort; Public Domain per
berean.bible/licensing.htm, pinned by sha256 in `pipeline/vendor/bsb/tables/bsb_tables.pin.json` +
`config/PROVENANCE.txt`) is upstream of everything in `bsb-data-output`, whose `display/` JSON keeps
only `[text, strong]` pairs. The rule here is **every column is kept as data; only HTML tags are
replaced by their structured meaning**: the three sort keys (they *are* the alignment — source order
vs English order), the base word and its textual-witness brackets (`{TR} ⧼RP⧽ (WH) 〈NE〉 [NA] ‹SBL›
[[ECM]]` — edition-struct's textual-basis input), transliteration, short parsing plus one short→long
table (3,819 entries; 5 non-function exceptions reported, not forced), the English span with its
spaces verbatim, punctuation / quotes / spacing / end-text, headings as `{tag, class, text}`,
cross-references as reference lists, `Par` as `{tag, class}` (so `span red` = red-letter is kept),
footnotes with italics markers, and the **311,021 sort-key-only padding rows out of 754,647 — kept as
rows**, because dropping them makes the file irreproducible. Output is a second manual layer for
`engbsb` (`attribution.source: "bsb-tables"`, CC0) with a `bsb` extension object, plus a source-side
sidecar keyed by spine `h_idx` (translit / parsing / base / witnesses) so those facts are reusable
without going through the alignment. First build: OT 22,883 of 23,145 verses target-mapped (128
refused), NT 7,936 of 7,957 (4 refused); 22,009 OT and 777 NT BSB source rows found no spine token
(`source_none`, reported, not guessed). **Reverse direction exists** (`--emit-tsv`, experimental):
regenerates all 23 columns; the round-trip test on a 3,239-row sample is byte-exact on every column,
HTML included (and a full-book `--emit-tsv RUT` is byte-exact on all 2,144 rows). **Folded into
`publish/full-align/eng/engbsb/manual/BSB-tables/`** (66 row files, 21.5 MB, 754,647 rows; `sidecar/`
66 files, 551,533 rows; `parsing_table.json`) beside Clear's `manual/BSB/` — the same edition now has
two independent manual annotators, plus gbt. The `bsb` extension is a proper 30-field Parquet struct
(`hdg: struct<tag,cls,text>`, `crossref: list<struct<text,href>>`, `par: list<struct<tag,cls>>`,
`end_text: list<struct<text,close>>`, each with an `*_unparsed` sibling for the handful of rows the
HTML parser could not structure: 33 headings, 18 par, 31 footnotes, 4 crossrefs), not JSON strings.
The `source_none` rows were broken down before folding rather than after: OT 22,009 of 305,494 source
rows (7.2%) — concentrated in PSA 7,067 / DAN 3,605 / EZR 1,213, i.e. superscription-and-verse-offset
and Aramaic, **0% witness-bracketed**, 2.8% with no Strong's; NT 777 of 138,131 (0.6%), **38% of them
witness-bracketed** (words present only in a witness our Nestle1904 spine lacks). The README section
for the layer is rendered from the manifest by `gold_to_fullalign.py` (one README writer), which also
learned to carry a partition it does not own across its own reruns.

**Feeds:** any consumer wanting a verse fully aligned with provenance; agreement-between-layers
confidence; the gold side of every scorer in this repo, once they read it (they read the attestation
parquets today).

**Consumes:** the statistical chain's `align_*` rows, the gold parquets / gbt jsonl, gram-struct (source
tags, conventions), lex-lexicon (seed renderings), and an LLM provider for layer 3.

---

## 5. edition-struct

**What it is.** One per-edition fact sheet about a single *text*: where it came from and how it was
pinned, what kind of text it is, and how it has to be read. The per-edition sibling of gram-struct —
gram-struct answers "how does this *language* render source structure", edition-struct answers "what
is this *edition* and what do I need to know to align it". Keyed by the **edition tag** (`hinirv`,
`arb_vdv`, `aaamlt`), never by the bare language iso; it carries the bare iso as a field.

**This does not exist as one thing yet.** What exists is already more scattered than gram-struct's
inputs, and — the actual problem — inconsistently keyed:

| file today | keyed by | holds |
|---|---|---|
| `config/pins/<tag>.json` (2,042) | edition tag | `provider`, `version_id`, edition `name`, `sha256` of the fetched text, `books`, `license_url`, `language_name` |
| `config/textual_basis.json` | edition tag | TR-vs-NA verdict from 15 diagnostic verses, per-verse counts, `second_spine` |
| `config/text_strip_rules.json` | edition tag | opt-in bracket/paren stripping decisions with reasons (1 entry: swk) |
| `config/language_editions.json` | **bare iso** | which editions to pool for a language, source adapter + param, primary-first order |
| `config/versification.json` | **bare iso** | scheme (12 entries) — a property of the *text*, not the language; the file's own `_doc` calls it a stopgap for a per-text field |
| `config/legacy_bare_iso_tags.json` | bare iso | the 199 grandfathered editions whose tag *is* the bare iso (the `_tag()` rule changed 2026-07-25) |
| `config/gold_langs.json` | bare iso | `edition` + `base_text` — gold is per edition too |
| compact-alignments manifest | iso → edition | books actually aligned, `tokenizer_version`, source/license pointer |
| *(not persisted)* | — | auto-detected versification (`versification.scheme_of(iso, usj_dir)`), PKF verse-range pooling (`read_verse_ranges`), the `_tag()` derivation itself — all recomputed every run |

Three of those files key an edition-level fact by the language iso (`versification`, `gold.base_text`,
pooling membership), and the tag↔iso relationship is itself a config file because the rule changed
mid-project. That is the exact class of defect this repo has already hit twice — the sibling-tag glob
(`align_files.py`) and the stopword/morphology cache mis-keyed on tag vs iso (commit `a12f871`) — and
it is the reason edition-struct earns a name even though it adds no new capability.

**Target shape (proposal, not built).** `config/edition_struct/<tag>.json`, pinned facts and derived
facts side by side, each with provenance:

```json
{
  "tag": "hinirv",
  "language": "hin",
  "primary": true,
  "source": {"provider": "bible.helloao.org", "version_id": "HINIRV", "name": "इंडियन रिवाइज्ड वर्जन",
             "license_url": "https://ebible.org/Scriptures/details.php?id=hin2017", "sha256": "81164e…"},
  "books": 66,
  "versification":  {"scheme": "protestant", "source": "detected"},
  "textual_basis":  {"verdict": "tr", "source": "diagnostic-verses", "checked": 15, "present": 10, "bracketed": 5},
  "text_strip":     {"strip_brackets": false, "strip_parens_noise": false, "source": "default"},
  "verse_ranges":   {"pooled": 0, "source": "derived"},
  "tokenizer_version": 2,
  "gold": {"base_text": "IRVHin", "method": "clear"}
}
```

Rules the proposal encodes:
- **the tag is the key, the iso is a field** — nothing edition-level is ever looked up by bare iso
  again; `legacy_bare_iso_tags.json` becomes a `primary`/`tag` pair inside each file and retires;
- **pinned vs derived is explicit** — `sha256`, license, provider, strip rules are pinned (change only
  on a deliberate re-fetch/decision); versification, verse ranges, tokenizer version, textual basis are
  derived (recomputed, and the file says from what);
- **versification moves here from `versification.json`** — and when `bcv-commons/bibles` publishes its
  per-text `versification` field, it is sourced from there with `"source": "bibles"`, exactly the
  handover `versification.json`'s own `_doc` already promises;
- **additive migration only** — every current reader keeps working off the old files; a file is folded
  in when it is next touched, never in one sweep (~2,000 pin entries with working readers is a large
  diff for zero user-visible gain if done at once).

**Feeds:** the per-edition base chain (ingest → align: which text, which scheme, which stripping),
gram-align and full-align manifests (source/license pointer, books, tokenizer version), gold scoring
(`base_text`).

**Consumes:** the source adapters (`cdn_source`, `helloao_source`, `dbt_source`, `catalog_source`),
`usj_source` (tokenizer version, ranges, strip rules), `versification`, `textual_basis`.

**Open:** whether the per-edition files should also carry the per-edition *statistics* the exports
compute (row counts, coverage, `content_sha256` per dataset) or whether those stay in the dataset
manifests, which are the publication record; and whether `language_editions.json`'s pooling order is
an edition fact (`primary: true` on one file) or a language fact (an ordered list in gram-struct) — the
proposal above picks the former.

---

## Where the names bind

Going forward, new modules, configs, dataset cards and plan documents use these five names. Existing
identifiers (`compact_align`, `lexeme-alignments`, `spanext_flags.json`, `llm_align`, …) are left as
they are until there is a reason to touch each one; when one is renamed, this table is the mapping.
Related references: `docs/compact-alignments.md` (gram-align format), `advanced-docs/publishing-principles.md`
(lex-lexicon invariants), `config/PROVENANCE.txt` (every external input behind gram-struct),
`internal-docs/aim1-typology-source-structure-plan.md` (how the gram-struct-driven mechanisms were
measured), `internal-docs/llm-align-experiment-plan.md` (full-align).
