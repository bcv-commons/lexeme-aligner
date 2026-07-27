# Data contracts — how lexeme-aligner talks to shoresh, the CDNs, and Hugging Face

**Principle: asynchronous, versioned, file-based data contracts — no live service coupling for the
core flows.** Every hop is a **pinned artifact** (a dataset/DB with a version + `sha256`), so each
project rebuilds on its own schedule and re-pins deliberately. No repo imports another; no service has
to be running when another runs. This is what keeps this repo standalone *and* avoids re-implementing
logic that another project already owns.

## The flows — three inputs, one output

```
  shoresh (MACULA) ──[lexeme-spine artifact, pinned]──►┐
    occasional, on refresh                             │
                                                        ├─►  lexeme-aligner  ──[publish/lexeme-alignments/<iso>]──►  Hugging Face / bcv-commons
  cdn.bibel.wiki PKF / helloAO JSON ──[text, sha256]──►┘        (align + export)     per language, CC0        │
    per language, on demand                                                                                   ▼
                                                                                                   bcv-query monorepo
                                                                                          consumes as external resources/  (the loop-back)
```

The return direction is **not** a direct loop back to shoresh: our output goes to **HF / bcv-commons**,
and the **bcv-query monorepo** (which *contains* shoresh) consumes those published datasets. Both hops
are pinned published artifacts, so there is **no in-repo circular dependency**.

---

## 1. shoresh → lexeme-aligner — the backbone (occasional, pinned)

**What:** the original-language backbone — one row per original token with its lexical identity. Today
this is `spine.db` (STEPBible-derived, bare-int Strong's). The **target** is a lexeme-anchored spine
from MACULA (`shoresh/macula/macula-spine.db`), which carries the finer grain (augmented Strong's ≈
lemma, plus gloss/role/frames/coref).

**Medium:** a **pinned DB/parquet artifact**, copied in and git-ignored (like `spine.db` now), with a
one-line provenance note. *Not* an HTTP API and *not* a code import — this is offline bulk data
(~600k tokens) consumed every alignment run; an artifact keeps it decoupled and fast, and keeps the
MACULA→lexeme logic in **one place (shoresh)**.

**Cadence:** occasional — re-pinned only on a deliberate MACULA/STEPBible upstream bump or a change to
the lexeme-id/tokenization contract. On a re-pin we re-align deliberately.

**The ask to shoresh (a producer feature, not new API endpoints):** add a build target — alongside
the existing `spine.db` build — that exports a **lexeme-anchored `spine_words`** from `macula-spine.db`:

```
spine_words(
  book, chapter, verse, idx, surface,
  lexeme,        -- ANCHOR: canonical MACULA lexeme id = (lang + augmented Strong's), e.g. hbo:0871a
  strong,        -- derived rollup: bare Strong's (augment letter stripped) — an attribute, not the key
  lemma,         -- pointed dictionary headword (human label)
  is_content,    -- from MACULA `class` (noun/verb/adj/…)
  morph,         -- optional
  gloss, role    -- optional: carrying these unlocks projection channel #1 (sense/frame-role) for free
)
spine_meta(key, value)   -- version tags (macula rev, etc.) + a build sha256
```

Contract notes shoresh owns and decides **once**:
- **lexeme id definition** — `(lang, augmented-strong)` is canonical and join-able; `lemma` is the label.
- **prefix tokenization** — MACULA splits prefixes into separate word tokens (`וְ`, `בְּ`) where the
  STEPBible spine fuses them. shoresh picks the tokenization; the aligner consumes whatever it emits.
- **versification** — carry the numbering used, so the target→spine hop is unambiguous.
- **pin** — `spine_meta` version tags + a `sha256` of the artifact, recorded in this repo's
  `data/PROVENANCE.txt`.

**Consumer side (this repo):** `hebrew_source` becomes a thin `SELECT` over the pinned artifact —
anchoring on `lexeme`, exposing `strong` as a rollup. No lexeme-derivation logic here.

## 2. CDN / helloAO → lexeme-aligner — the target texts (on demand, pinned)

**What:** each language's translation. **Not from shoresh.** Two sources behind the USJ seam:
`cdn.bibel.wiki` PKF (`cdn_source`, Node edge) and `bible.helloao.org` JSON (`helloao_source`, pure
Python — reaches beyond the 589 PKF languages).

**Medium + pin:** the text stays at origin; we fetch by pin, verify `sha256`, and record
`config/pins/<iso>.json` (version + hash). The source's **license is linked, never copied** —
`config/sources.json` holds a `license_url` pointer per language (see `publish/lexeme-alignments/README.md` §License).

**Cadence:** per language, on demand (when a language is added, or its source is corrected → re-pin +
re-align that language). Recipe-layer detail in `bibles-recipe-layer.md`.

## 3. lexeme-aligner → Hugging Face / bcv-commons — the output (per language)

**What:** `publish/lexeme-alignments/iso=<iso>/data.parquet` (`surface, strong, count, share, hi_conf` → lexeme-
primary once the backbone lands) + a committed `manifest.json` + the dataset card.

**Medium:** an `iso=<iso>/`-partitioned Parquet dataset published to a Hugging Face dataset (or object
storage); the bulk data is git-ignored, only the small deterministic `manifest.json` is committed. See
`publish/lexeme-alignments/README.md`.

**Licensing:** the catalogue is **CC0-1.0** (derived factual data); each partition's manifest `source`
block links to where the source translation's own license lives.

**Consumers:** anyone (public CC0), and specifically the **bcv-query monorepo** ingests it as external
`resources/`. Gated behind the benchmark (`docs/benchmark.md`) before wide publication.

---

## Optional live flow — semantic enrichment (low volume)
The one place an HTTP call fits: per-word semantic lookups (`coref` / `frame` / `participants`,
`sense`) during LLM/manual **raise passes**, against shoresh's *existing* API. This is occasional and
interactive — **not** the bulk backbone path, which is always the pinned artifact.

## Pin points at a glance

| flow | artifact | pinned by | changes when | action |
|---|---|---|---|---|
| shoresh → us | lexeme spine (DB/parquet) | `spine_meta` tags + `sha256` | MACULA/STEPBible bump; contract change | re-pin + re-align |
| CDN/helloAO → us | target USJ / PKF | `config/pins/<iso>.json` `sha256` | source corrected/added | re-pin + re-align that lang |
| us → HF | `publish/lexeme-alignments/iso=<iso>` | manifest `content_sha256` | method/model/spine/text improves | re-export that partition |

## Why this shape
- **No live coupling** — offline batch producer; nothing has to be running.
- **Single owner per concern** — shoresh owns MACULA→lexeme; this repo owns alignment; the CDNs own the
  texts. No double implementations.
- **Standalone** — this repo depends on *files with hashes*, not on another codebase or service.
- **Deliberate refresh** — a hash mismatch means an upstream drifted, so we re-pin on purpose and
  re-run — never a silent stale cache.
