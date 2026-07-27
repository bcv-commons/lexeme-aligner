# The aligner — plan for a Strong's word-alignment factory

> **Status: planning; first experiment scoped (Indonesian — see below).** The `aligner/`
> folder exists but is empty; nothing is implemented yet. This document is the design — and
> an open invitation to help build it. It's the single biggest force-multiplier on the
> project's roadmap. Built **generic from day one**: a pluggable *Bible-source adapter* + an
> `iso639-3` code is all it takes to run for **any language from any Bible repository**
> (bcv-commons/bibles, helloAO, eBible, …). Alignment is a **multi-resource harvest across four
> projection channels**, and coverage is a dial — an auto pass gets ~50–80%, LLM/manual passes
> raise it, and each bump re-enables more mining.

## The one-sentence idea

Take *any* Bible translation, automatically **word-align it to the original**
Hebrew/Greek (which carries Strong's numbers), and out comes:

1. a **word-level, Strong's-tagged interlinear** for that translation, and
2. an entry in **[`publish/lexeme-alignments/<lang>.tsv`](../publish/lexeme-alignments)**
   — a table of `surface word → Strong's number, count, share` for the whole
   language.

The aligner is a **producer of `resources/`, not a service** — it runs offline,
writes shared data, and both [bcv-RAG](bcv-RAG.md) and [shoresh](shoresh.md)
consume the result.

## Why this matters

`publish/lexeme-alignments` is already the backbone of the project's multilingual support:
concept expansion, function-word filtering, and the name-bridge (localized name →
Strong's → entity) all read from it. Today it covers ~10 languages because those
alignments were produced **by hand** (the Clear-Bible manual set). The aligner
**generates the same artifact statistically/automatically** — so any language with
a translation can join.

And because everything is keyed on Strong's, alignment unlocks a chain reaction:

> align a translation → get Strong's per word → and the multilingual glosses
> (`llm_strongs_glosses`) and semantic domains (UBS SDBH/SDGNT) **attach for free**.

That's the flywheel: *"any translation in → Strong's + glosses + domains +
interlinear, in that language."*

## How alignment works — three methods that team up

No single method covers every language. The aligner runs them as an **ensemble**:
where they agree, confidence is high; where they disagree, flag for review.

| Method | How it works | Needs | Best for |
|---|---|---|---|
| **Statistical** (co-occurrence / EM, e.g. `eflomal`, `fast_align`) | Counts which target words recur with which Strong's across all verses; normalizes against chance (Dice / PMI / IBM-1) so function words don't dominate. | parallel text + Strong's only | **any language** with a full Bible — the universal spine |
| **LLM gloss** | An LLM *generates* the expected target word for each Strong's; the alignment then matches translation words to those glosses (exact + fuzzy). | an LLM competent in the language | high-resource languages; precise biblical senses; cheap type-level lexicon |
| **Neural aligner** (SimAlign / awesome-align) | A multilingual encoder aligns tokens by embedding similarity, in context, cross-script, no training. | an encoder that covers the language (LaBSE ~109, Glot500 ~500+) | low-resource languages an LLM can't generate for; catches polysemy |

**The rule of thumb:** statistical is the spine (it comes straight from the text);
the LLM gloss and neural aligner are *priors and cross-checks* that help where the
data is sparse. Confidence comes from agreement across methods — exactly the
`share` column already in `publish/lexeme-alignments` (`P(Strong | surface)`).

> A note on glosses vs. surfaces: glosses *decide* an alignment, but
> `publish/lexeme-alignments` records the **real attested translation word** with its counts and
> share — not the gloss.

## The English prototype (the seed to generalize)

There's a working **English/NT prototype** (developed separately, kept as a local
seed — not part of this public repo) that converts a verse-level English
translation into a word-level, Greek-aligned, Strong's-tagged interlinear using a
**deterministic, gloss-anchored, $0** strategy chain (no models): translator-
addition detection → exact gloss match → learned Strong's→English patterns →
lexicon gloss → fuzzy gloss → multi-word grouping.

About **70% of that prototype is language-agnostic** (the source parser, the
interlinear schema, the strategy framework, the string algorithms). The
English-specific part is concentrated in **one swappable place** — the gloss
table. Point it at `resources/llm_strongs_glosses/<lang>.tsv` instead of the
English lexicon and the gloss-anchored strategy becomes multilingual immediately.
The plan is to **absorb that prototype as the English/NT core**, then generalize.

## What a parse yields — the four harvest channels

An alignment isn't one output — it opens **four projection channels** of increasing cost. A target
word maps to an original occurrence, and through that occurrence you carry data onto the target from
four directions. **Two of them don't even need the word alignment — just the verse map** — so they
pay off from day one, before a single word is aligned.

| Channel | Projects | Needs | Cost |
|---|---|---|---|
| **1. Occurrence-direct** | the aligned token's own attributes | the word alignment | free (it *is* the alignment) |
| **2. Strong's join** | per-type data keyed on the code | +1 lookup / word | one parse |
| **3. Verse-correspondence** | verse / range-keyed data | **only the verse map** | one parse — available *before* word alignment |
| **4. Name-bridge** | entity identity (surface→Strong's→entity) | a resolution pass | further pass |

**Channel 1 — per-token, differs word-by-word** (richest; mostly CC-BY): full morphology (POS ·
gender · number · person · state · **binyan/voice** · tense · suffix) · lemma · gloss · translit ·
surface · **Hebrew per-occurrence sense + confidence** (`occurrences/hbo.db`) · **syntactic function**
(Subj / Obj / Pred / Cmpl / Adv) · **PropBank semantic role** (MACULA frames: A0 agent / A1 patient
/ …) · **coreference / participant** pointer (MACULA refs → antecedent word) · per-occurrence
context-clause embedding.

**Channel 2 — Strong's join** (one lookup each): multilingual glosses · keyness · frequency +
function-word flag · **semantic domain** (Louw-Nida / SDBH) · sense distribution · **TW keyterm
article + is_kt** · concept id · LXX-equivalent Strong's · topic membership (`topic_strongs`) ·
lexicon articles (BDB / LSJ / Abbott-Smith).

**Channel 3 — verse-correspondence** (verse map only, no word alignment): speaker / red-letter /
quote-type · cross-references (TSK + parallels) · Nave's topics · **entities-mentioned-in-verse**
(`entity_passages` + ACAI) · section / pericope headings · **figures-of-speech / translation-issue
tags** (`figs-*`).

**Channel 4 — name-bridge:** entity identity + genealogy relations.

The occurrence-level join is what makes Channel 1 sense-precise: aligning at the *occurrence* level
(not just type-level surface→Strong's) and grouping target words by the occurrence's `sense` yields
the **binyan- and sense-precise** `senses_i18n` gloss — attested, in-context, no LLM translation. The
concrete `resources/` artifacts one harvest drops: `publish/lexeme-alignments/<lang>.tsv` (surface→Strong's), the
per-word **interlinear**, `senses_i18n/<lang>.tsv`, `concept_surfaces/<lang>.tsv` (invert lexeme-alignments),
`stopwords/<lang>.tsv` — plus glosses / domains / keyness attaching for free via the Strong's key.

## Coverage is a dial — align, raise, re-harvest

A first automatic pass will **not** align every word — expect **~50–80%** confident coverage
(morphology-rich Hebrew, fused affixes, free word order, and translation additions all cost
alignment). That's a success, not a failure: **harvest what's aligned, then raise coverage and
re-harvest.** It's a loop, not a single shot:

1. **Automatic first pass** (statistical + neural + gloss-anchored ensemble) → ~50–80% at high
   confidence. Harvest Channels 1–2 for the aligned words immediately; **Channel 3 is already ~100%**
   (it needs only the verse map, so verse-level data lands regardless of word-alignment coverage).
2. **Raise coverage** on the unaligned remainder — either or both:
   - **LLM-assisted alignment** — for languages an LLM handles well, have it align the leftover target
     words against the verse's Strong's / gloss set (the gloss-anchored strategy escalated to a
     model). Cheap per verse, high recall on the tail.
   - **Manual alignment / review** — a human resolves the hard/ambiguous remainder, and is the QA gate
     that promotes machine alignments to "trusted." (`strongs/attestations/` already carries a
     `method = manual | transfer` flag for exactly this.)
3. **Re-harvest** — every coverage bump re-enables the harvest: more aligned words → more `publish/lexeme-alignments`
   rows, more `senses_i18n` filled, higher `share` confidence. Re-run Channels 1–2 over the newly
   aligned words.
4. **Then the deeper derivations** (below) run on the *improved* alignment — they mine more the higher
   coverage climbs.

So "one parse vs further passes" is really **harvest → raise coverage (LLM / manual) → re-harvest →
derive.** Confidence rides the `share` column throughout; a `method` / `confidence` flag marks each
row's provenance (auto / llm / manual) so consumers can threshold.

## Further passes — mine deeper (each benefits from higher coverage)

- **Aggregate / invert your own output** → `concept_surfaces` (invert lexeme-alignments), `stopwords`
  (function-word surfaces), surface families, and the **`senses_i18n` sub-sense glosses** (group
  aligned target words by `(lex, stem, sense)` → dominant rendering).
- **Derive Greek per-occurrence senses** — Greek has *no* occurrence-sense DB (Hebrew does). Aligning
  several translations lets you **infer** Greek senses from how renderings diverge — a genuinely new
  artifact the aligner *creates*, not just projects.
- **Multi-language consensus / back-translation** — once N languages are aligned, triangulate meaning
  (consensus glosses; sense clustering from cross-lingual agreement).
- **MWE detection** — consecutive target words aligning to one Strong's (set) → "Espíritu Santo" →
  G4151 + G0040.
- **Morphology-transfer lemmatizer** — derive a target-language lemmatizer from aligned inflections.
- **Graph derivations** — coref-chain resolution (walk MACULA ref pointers → name the participant) ·
  frame aggregation ("every clause where X is the agent") · LXX cross-projection (pull Greek
  domains / senses onto Hebrew via `lxx_bridge`) · cross-ref / synoptic propagation · entity
  disambiguation (TIPNR homonyms — the six Marys).

## Design gotchas (decide these up front)

1. **Two occurrence-id systems.** BHSA `node` (`occurrences/hbo.db`, the CF engine) vs Clear/MACULA
   token id (`spine`, `publish/lexeme-alignments`, `strongs/attestations`). Pick **MACULA/Clear** as the alignment
   id space (it *is* the alignment token id) and keep a bridge to the BHSA node for the Hebrew sense DB.
2. **A gold alignment already exists.** `resources/strongs/attestations/<lang>.tsv` is a per-occurrence
   **manual + machine-transfer** alignment for ~10 languages. Don't align from scratch — use it as
   **warm-start · gold reference · validation set** (and its `method` flag as the coverage/quality model).
3. **License split.** MACULA morphology / frames / coref / speaker are **CC-BY** (redistributable);
   UBS-MARBLE-derived **domains + senses** are **reference-only** (don't redistribute those
   projections). And **Greek has no per-occurrence sense DB** (Hebrew only) — Greek senses are a
   *derivation*, not a projection.

## How to build it (suggested sequence)

Full cross-edition alignment is **gated on a versification map** (roadmap item **V1**) — you
have to line verses up before aligning words across editions. But a **single translation vs the
spine** only needs verse *correspondence*, which is 1:1 for most books — so the first experiment
starts **now** on clean-versification books and defers V1 to scaling. Sequence:

0. **First experiment (now)** — the generic pipeline (adapter → SimAlign → aggregate → occurrence-
   join), pointed at **one language + one book** (Indonesian / Genesis, above). Proves the
   flywheel end-to-end on real data and produces sample `publish/lexeme-alignments` + mined `senses_i18n`.
1. **Absorb the English/NT prototype** as the deterministic, gloss-anchored core (the $0 strategy
   chain), behind the same source-adapter interface.
2. **Extend to the OT** — Hebrew via STEPBible **TAHOT** + **TBESH** (same method, clone the parser).
3. **Go multilingual, generically** — the pipeline is already language-agnostic; each language is a
   `(adapter, iso639-3)` pair. Swap the gloss table to `llm_strongs_glosses/<lang>.tsv`, add the
   **neural** strategy (SimAlign) and the **statistical** spine (eflomal) as cross-checks.
4. **Benchmark** against the hand-made `publish/lexeme-alignments` gold (the ~10 Clear-manual languages); tune the
   `share` threshold and add a **confidence flag** before trusting new languages.
5. **Versification map (V1)** — lift the clean-book restriction so the whole canon + odd editions align.

## Generic input — any Bible, any source, any format

Two orthogonal, pluggable concerns, so the alignment core never changes: a **canonical internal
format** (what the pipeline reads) and **source adapters** (where the bytes come from).

### 1. Canonical internal format — USJ

The aligner works on **USJ** (Unified Scripture JSON) throughout. It's JSON (robust `json.load`),
**Python-native** (this repo already writes USJ and already depends on `usfmtc`), a standard in the
**USFM ↔ USX ↔ USJ** family, and it separates translatable text from apparatus by element `type`.
Deliberately **not** Proskomma/PERF/SOFRIA — those are a JavaScript ecosystem and this pipeline is
all-Python; routing through them would mean a JS subprocess dependency.

**Any format → USJ, in Python:**
```
USFM ─(usfmtc / USFM→USJ)─┐
USX  ─(usfmtc)────────────┼─► USJ ─► one adapter ─► iterator[(ref_bbcccvvv, [word tokens])]
USJ  ─(direct, fast path)─┘        (+ declares versification)
(PERF / SOFRIA only if ever needed → convert to USJ)
```
"Support format X later" = "can we get X to USJ?" — and USFM/USX are solved Python conversions. The
**USJ adapter** is the only format-specific code: walk the `content` tree tracking chapter/verse,
collect word/text from translatable elements (`char:w`, `para:p/q/…`), **exclude apparatus by type**
(`note:*` footnotes/cross-refs, `para:s*` section headings, verse/chapter-number text), preserve
surface + order (and any `\w …|strong=…|lemma=…` attributes if the text is already tagged — a free bonus).

### 2. Source adapters — where the bytes come from

A thin fetcher per repository; each hands its bytes + format + versification to the format layer:

| Adapter | Source | Notes |
|---|---|---|
| `bcv_commons` | **bcv-commons/bibles** (our published dataset) | canonical home for texts we vendor, packaged as **Scripture Burrito** (see below); the default sink once a text is fetched |
| `helloao` | **helloAO** (helloao.org free-use Bible API) | **1000+ translations** — the broad multi-language on-ramp; its `available_translations.json` is a ready-made catalog (iso639-3, license, `availableFormats`). **Fetch its USFM, not its rendered JSON** (see the heading/versification note below); records versification as `protestant` |
| `ebible` / `open_bibles` | **eBible.org**, seven1m/open-bibles | USFX/OSIS dumps, hundreds of languages |
| `local` | a file you hand it | USFM / USX / USJ (or verse-per-line) |

Each source → format layer → USJ → the pipeline. Everything downstream — tokenize · align ·
aggregate · mine — is **100% source-, format-, and language-agnostic**. To run a new language: pick a
source, pass the `iso639-3` code. Add one small adapter (or one format converter) and every language
that source carries becomes alignable — the core never changes.

**Packaging for `bcv-commons/bibles` — Scripture Burrito (USJ).** Rather than invent a manifest, use
**Scripture Burrito**, the standard scripture metadata + packaging format (a `metadata.json` + content
*ingredients*). The spec supports **USFM, USX, *or* USJ** as scripture-text ingredients, so:
- **Content ingredient = USJ** — our canonical format, so the adapter reads it with **zero conversion**;
  optionally also carry the **USFM** as a source ingredient (lossless, good provenance).
- **Metadata carries** language (iso639-3), license, attribution, and — the field that matters most
  here — **versification** (⚠ confirm at implementation whether it's a metadata field or a separate
  `.vrs` ingredient; either way it ships *inside* the burrito, which **closes the helloAO versification
  gap** — the vrs the aligner needs travels with the text).
- **Flexible packaging** (per the spec: a burrito can be "a zip file, a directory, a GitHub repository,
  a database, or delivered via API") → publish burrito **zips to a CDN** (helloAO's model) and/or
  **GitHub Releases** (versioned, large files out of git — matches the bcv-commons HF-full +
  GitHub-shop-window strategy).

The `bcv_commons` adapter is then just: read the burrito `metadata.json` → language / license /
**versification** + ingredient list → load the USJ ingredient → tokens. Burrito folds the *source* and
*manifest* concerns into one recognized standard, and USJ being a native burrito format means no
impedance mismatch with the pipeline.

### 3. Versification & the heading trap (esp. helloAO)

**helloAO is worth the care** — 1000+ translations across hundreds of languages, free for any use,
with USFM available and a ready-made catalog. It's the single biggest multi-language on-ramp. But
its rendered JSON has one trap the parser must resolve.

**What helloAO does** (measured on Psalm 51, `deu_l12` vs `BSB`): it **normalizes every translation
to Protestant versification**, so verse numbers align 1:1 *across translations* — but it does **not**
expose the source versification, and it handles **psalm superscriptions inconsistently**:
- English (BSB) pulls the title **out** as an unnumbered `hebrew_subtitle` (clean).
- German (`deu_l12`, `deu_elo`) **embeds** the title **inside verse 1** ("*Ein Psalm Davids…* Gott,
  sei mir gnädig…"), glued to the content.

Tokenizing that German v1 would feed the superscription words ("Ein Psalm Davids, vorzusingen…") into
the aligner, where they'd mis-map onto the Hebrew (whose superscription sits at a *different* verse).
**This is not German-specific** — any translation helloAO didn't cleanly separate embeds it the same
way, so treat it as a general rule.

**Parser handling (in order):**
1. **Prefer USFM → USJ.** In USFM the title is a `\d` marker → USJ `para:d`, cleanly separable for
   *every* language regardless of how the rendered JSON treated it. This alone resolves most of it —
   another reason to fetch a source's USFM over its rendered JSON.
2. **Detect + quarantine embedded titles** (JSON-fallback path, when no USFM). A chapter has a
   superscription iff a reference edition emits a `hebrew_subtitle` (or the USJ has `para:d`). For a
   text that instead embeds it in v1, **don't try to heuristically split title-from-content** — mark
   those verse-1 tokens as `title`/low-confidence and **exclude them from alignment**. Under-covering a
   handful of superscription words is far safer than mis-aligning them.
3. **Record versification = `protestant`** for helloAO texts and apply the single well-known
   **Protestant↔Hebrew `vrs` map (V1)** for the target→spine hop — because the spine is Hebrew-numbered
   (Ps 51 spine: title = v1, "have mercy" = v3). helloAO aligning translations *to each other* doesn't
   align them *to the Hebrew original*. **Psalms stay V1-gated** — the first experiment skips them
   until the vrs map lands; everywhere else, Protestant≈Hebrew and it's a non-issue.

Net: harvest helloAO's breadth freely, take its **USFM**, and let the `\d`/`para:d` separation +
the Protestant↔Hebrew map handle the psalm edge cases — don't trust the rendered JSON's per-language
heading treatment.

## First experiment — Indonesian, runnable now

A concrete first run, against data already in hand on the original side:

- **Original side (have it):** `resources/occurrences/hbo.db` gives every OT word's
  `ref · lex · stem · strong · sense · sense_conf`; `shoresh/macula/macula-spine.db` gives the
  Hebrew surface tokens per verse. (NT: the Greek occurrence store, same shape.)
- **Target side (to deliver):** the Indonesian full text into **bcv-commons/bibles** as **USFM**
  (or USJ, if handy) via the `bcv_commons` / `local` adapter — normalized to USJ on read (`usfmtc`),
  with its **license** + **versification** (Protestant vs Hebrew numbering).
- **Method:** **SimAlign** (LaBSE / Glot500) — no training, cross-script (Latin ↔ Hebrew), Indonesian
  well-covered, aligns *in context*. Runs **local on the Mac GPU** (no paid API). Seeded/validated by
  priors we already ship — `word_glosses/hbo/Indonesian.csv` + `llm_strongs_glosses/ind.tsv` (the
  gloss-anchored strategy) — so the neural alignment has a lexicon prior to agree/disagree with.
- **Scope:** one **clean-versification book** first (e.g. Genesis or Ruth) to sidestep V1, then widen.
- **Pipeline:** adapter → verse-parallel (Indonesian ↔ Hebrew tokens) → SimAlign → aggregate to
  `publish/lexeme-alignments/ind.tsv` → occurrence-join to `hbo.db` sense → `senses_i18n/ind.tsv`.
- **Deliverables:** a sample `publish/lexeme-alignments/ind.tsv` + a mined `senses_i18n/ind.tsv` slice + a quality
  read (benchmark against a known-lexeme gold; sub-sense coverage of the ~4.1k tail).
- **Why Indonesian first:** already onboarded (book names, TW, glosses, domains), so the experiment
  slots into a full stack and pays off immediately — and it retires the sub-sense translation
  residual with **attested** renderings instead of LLM translation.

The experiment is deliberately the *generic* pipeline pointed at one language: the only
Indonesian-specific inputs are the adapter choice and `iso639-3 = ind`. Prove it on Genesis, then
the same code runs Spanish, Swahili, Tagalog… by swapping the adapter + code.

## Inputs & sources (all CC-BY or free)

- **STEPBible** TAGNT (Greek NT) / TAHOT (Hebrew OT) — extended Strong's +
  morphology + per-word glosses (CC BY 4.0); TBESG/TBESH lexicons (CC BY).
- **Parallel Bible text** (the target side, via a source adapter) — **bcv-commons/bibles**
  (ours), **helloAO** (helloao.org API), **eBible.org**, seven1m/open-bibles (hundreds of
  languages). See *Generic input* above.
- **`resources/occurrences/hbo.db`** (+ the Greek NT store) — per-occurrence
  `ref · lex · stem · strong · sense`; the join that turns an alignment into mined
  `senses_i18n` and per-word interlinear.
- **`resources/llm_strongs_glosses/`** — per-language glosses we already produce,
  for the gloss-anchored strategy (an alignment prior).
- A **versification map** to align verses first (roadmap **V1**) — only needed to lift the
  clean-book restriction; the first experiment runs without it.

## Where it fits

```
aligner/  (offline producer — generic; one run per (source-adapter, iso639-3))
   reads:  original+Strong's+sense (occurrences/hbo.db, spine) · parallel text (any adapter:
           bcv-commons/bibles · helloAO · eBible · local) · llm_strongs_glosses (prior)
   writes (ONE parse): publish/lexeme-alignments/<lang>.tsv · per-word interlinear · senses_i18n/<lang>.tsv
           · concept_surfaces/<lang>.tsv · stopwords/<lang>.tsv
                 │  (+ for free via Strong's: glosses · domains · keyness · name-bridge)
                 ▼ consumed by
   shoresh (gloss/concept/senses go multilingual, interlinear) · bcv-RAG (concept expansion, name-bridge)
```

The project is **shoresh-first**: shoresh supplies the original-language +
Strong's input and is the primary consumer of the interlinear and `publish/lexeme-alignments`.

## How to help

This is a great place to contribute, especially if you know NLP/word-alignment:

- Stand up the **statistical aligner** (eflomal/fast_align + Dice/IBM-1 scoring)
  on one full-Bible language and compare its `publish/lexeme-alignments` output to the manual
  gold set.
- Wire a **neural aligner** (SimAlign) as one strategy in the chain and measure
  where it beats the gloss-anchored method.
- Help build the **versification map** (V1) that everything here depends on.

If you want to take one of these on, open an issue describing the language and
method you'd start with.

---

## Out of scope (for now): audio forced-alignment

A *separate*, future concern — aligning a Bible **audio** recording to its text to
get per-word **timing** (read-along UX; also the backbone behind a speaker /
red-letter index). The method (Meta **MMS-FA** + **Whisper**, fused) and a mature
local prototype exist, but this is its **own** future subfolder, not part of the
text aligner. It will be designed when the audio resources are brought in.
