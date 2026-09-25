# Architecture — the five data structures

**Status:** naming decision, 2026-09-25. This document lays down the names used **going forward** for
the five data structures this repo produces or consumes. Existing code, config files and published
datasets keep their current names for now — nothing has been renamed. Each section below says what
the structure *is*, what it looks like today, where it lives, what feeds it, what consumes it, and what
is still undecided. Where a section describes a target rather than something that exists, it says so.

| name going forward | what it is | current name(s) in code / on disk | shape |
|---|---|---|---|
| **lex-lexicon** | type-level lexicon: what renders each source lexeme, across a whole language | `publish/lexeme-alignments`, `senses_attested`, `aligned_mwe`, `target-stopwords`, `target-morphology`, `cross-lingual-span-profile` (all live on HF under `bcv-commons/`) | per **language** (editions pooled) |
| **gram-struct** | per-language grammatical/typological facts, in ONE internal structure regardless of where they came from | not yet one thing — spread over `config/grambank/`, `config/typology/`, `config/constituent_order/`, `config/spanext_flags.json`, `config/fertility_flags.json`, `config/llm_conventions/` | per **language** (a fact sheet, not text data) |
| **gram-align** | per-verse, position-level alignment from the statistical chain | `publish/compact-alignments` (HF `bcv-commons/compact-alignments`), `compact_align.py` | per **edition** |
| **full-align** | gram-align plus what an LLM pass adds on top of it | `llm_align.py` output (`align_llm_<tag>_<BOOK>.jsonl`), not published | per **edition** |
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
                                                                        full-align
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

**This does not exist as one thing yet.** What exists is a set of separately-started files that are
all gram-struct-shaped; gram-struct is the design that collects them. Superset, today:

| file today | what it holds | provenance | keyed by |
|---|---|---|---|
| `config/grambank/features.json` | 27 Grambank features per ISO (`GB074`, `GB075`, …) | external (Grambank, vendored, pinned) | iso → feature code → `"0"/"1"` |
| `config/typology/directions.json` | 5 direction slots (`adposition`, `article`, `possessor`, `subject_verb`, `object_verb`) each `{direction, source, confidence}` | Grambank > WALS > lang2vec, each source validated ≥90% against Grambank before use (`typology.py`) | iso → slot |
| `config/constituent_order/<iso>.json` | `pair_order_kept`, `function_drift` — how the language reorders BHSA phrase functions | **derived from our own alignments** (`constituent_order.py`), OT-only | iso (62 files) |
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

**Target shape (proposal, not built).** One file per language, `config/gram_struct/<iso>.json`, every
fact carrying its own provenance so a consumer can gate on it:

```json
{
  "iso": "hin",
  "adposition":   {"direction": "after",  "source": "grambank",   "confidence": 1.0},
  "article":      {"direction": null,     "source": "grambank",   "confidence": 1.0},
  "possessor":    {"direction": "after",  "source": "grambank",   "confidence": 1.0},
  "subject_verb": {"direction": "before", "source": "grambank",   "confidence": 1.0},
  "case_marking":       {"present": true,  "source": "grambank"},
  "subject_indexing":   {"present": false, "source": "grambank"},
  "possession_affix":   {"present": false, "source": "grambank"},
  "constituent_order":  {"source": "derived", "n_verses": 20819, "pair_order_kept": {"Pred>Subj": 0.44, "...": 0}},
  "multiword_rates":    {"source": "derived", "name": 0.031, "noun": 0.052, "verb": 0.081},
  "mechanisms": {
    "spanext.relation_trigger": {"enabled": true,  "source": "measured", "note": "…"},
    "spanext.definite_trigger": {"enabled": false, "source": "measured"},
    "fertility_priors":         {"enabled": true,  "lambda": 2.0, "source": "measured"}
  },
  "conventions_md": "config/llm_conventions/hin.md"
}
```

Rules the proposal is meant to encode, all already in force informally:
- **provenance on every fact** — `grambank` / `wals` / `lang2vec` / `derived` / `measured` / `manual`,
  so a consumer can say "external or derived only, never a mechanism verdict";
- **direction from typology, existence from evidence** — a fact saying *which side* comes from a
  typology source; a fact saying *this language actually undershoots here* comes from our own data
  (the audit), and the two are separate keys;
- **mechanism verdicts are recorded, never inferred** — `mechanisms.*` entries exist only after a
  human has measured against gold (the `spanext_flags.json` / `fertility_flags.json` rule); no field
  in this file is ever computed from other fields in it automatically;
- **`null` is a real value** — "no article direction" (hin) is a fact, distinct from "unknown".

**Feeds:** gram-align (the statistical chain reads it at `span_extension`, `gapfill`, and — since
Step 3 — at eflomal time via fertility priors) and full-align (the LLM prompt already carries
`[construct]`/`[genitive]`/`[dative]` source tags and the language's conventions note).

**Consumes:** external typology snapshots (vendored, pinned in `config/PROVENANCE.txt`) and lex-lexicon.

**Open:** whether the derived-order profile is worth extending to the remaining slots the way Östling
2015 did (the plan's Step 2 "empirical source (D)", not built); how to version a per-language file whose
`derived` half changes on every re-alignment while its `measured` half must not.

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

**What it does *not* carry today:** per-span `method` or score (the union is flattened), and any
function-word alignment (only content lexemes get an ordinal). Both are deliberate compactness choices;
whether full-align should restore them is an open question in §4.

**Feeds:** full-align (it is the base the LLM pass reads and extends); the reverse-check / QA tooling;
any "show me verse X in translation Y" consumer.

**Consumes:** the per-edition base chain's `align_<method>_<tag>_<BOOK>.jsonl`, gram-struct (through
that chain), the canonical index.

---

## 4. full-align

**What it is.** gram-align plus what an LLM pass adds on top of it for the same edition: fills for the
residue the statistical chain left unaligned, re-checked decisions where the chain was unsure, and —
where a language has one — the grammatical conventions the LLM was given to reach them. Opt-in per
edition, paid per verse; never a precondition for gram-align or lex-lexicon to exist (the default chain
must keep working for languages with no LLM coverage at all — `gapfill_align.py`'s mission note).

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

**Undecided — the one real design question:** does full-align *include* gram-align's rows, or only
the LLM's delta?

- *Include* (a complete per-edition alignment, LLM rows layered on statistical ones): one download
  answers "show me this verse fully aligned"; provenance stays per row (`method`), so a consumer can
  still filter back to the statistical layer; it duplicates gram-align's bytes for every edition that
  has an LLM pass.
- *Delta only* (just the LLM rows, applied on top of gram-align by the reader): smaller, no
  duplication, but a consumer needs both files and the union rule (`pos_score.union` is first-wins,
  LLM listed first) to reconstruct anything.

Today's on-disk reality is the delta (`align_llm_*` beside `align_eflomal_*` / `align_gloss_*` /
`align_gapfill_*`, unioned at scoring time). Nothing downstream depends on either answer yet; the
choice can wait for the first edition that is actually published in this form. Whichever it is, the
compact string format of §3 is not sufficient for it as-is — it has no per-span provenance, and a
full-align consumer will need to tell an LLM decision from a statistical one.

**Feeds:** nothing yet (unpublished; measured only against gold).

**Consumes:** gram-align (as base and as `verify` pool), gram-struct (source tags, conventions),
lex-lexicon (seed renderings), and an LLM provider.

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
