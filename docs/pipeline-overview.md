# Pipeline overview — what each step does, and what it extracts

Step-by-step reference for the 9-step per-language chain (`full_chain.py`), written to answer one
question at every stage: **who is allowed to claim a target token here, and what stops the wrong
source token from claiming it?**

A target verse has a fixed number of word positions. Every stage that emits an alignment *consumes*
some of them, and a consumed position is unavailable to the stages that follow. So the pipeline is
best read as a **slot ledger**: each step is a claim on a scarce resource. The recurring failure the
project keeps hitting — a semantically light source word (copula, quantifier, article, "be"/"have"/
"all"/"one") taking a slot that belonged to a real content match — is a slot-allocation failure, and
it can only be fixed at the step where the allocation actually happens.

Measurements below are from `fra` (LSG), NT, eflomal pass, 7,942 verses, run 2026-09-01.

---

## The source spine: what is even eligible to claim a slot

`pipeline/lexeme-spine.db` (MACULA: WLC + Nestle1904), read by `hebrew_source.py`.

| | tokens |
|---|---|
| total spine words | 607,255 |
| `is_content = 1` | 294,402 |
| `is_content = 0` | 312,853 |
| **carrying a Strong's number** | **607,255 — all of them** |

Per token, `HebToken` carries: `idx, surface, strong, lexeme, lemma, morph, is_content` from the
spine, plus (where the spine has the columns) `stem` (binyan), `gloss_en`, `sense`, `sp`,
`is_superscription`, and the BHSA phrase layer `phrase_id / function / rela` (OT only — all `None`
across the NT).

Two normalisations happen at read time:

- **Fused same-Strong's tokens are merged** — בֵּית לֶחֶם is two spine words, one Strong's, one place;
  they become a single alignment unit, so they cannot double-consume target tokens.
- **Psalm superscriptions are forced `is_content = False`** — most target editions print them as an
  unnumbered heading, so aligning against them consumes verse-1 positions that belong to real content.

**The eligibility fact that drives everything below:** `is_content` is a *label*, not a gate. Every
one of the 312,853 non-content tokens has a Strong's, and both aligners select their source tokens on
"has a Strong's", not on `is_content`. The article הַ, the conjunction וְ, ὁ and καί are full
competitors for target positions at alignment time. They are removed only at **export**, four steps
later — after they have already spent the slots.

The largest non-content lexemes: `hbo:2050b` וְ (50,272), `hbo:1886a` הַ (23,887), `hbo:3807a` לְ
(20,378), `grc:3588` ὁ (19,783), `hbo:0871a` בְּ (15,539), `hbo:0853` אֵת (10,939), `grc:2532` καί
(8,978).

---

## Step 1 — Ingest (`onboard.py` → `cdn_source` / `helloao_source` / `dbt_source`)

**Does:** resolves every edition of the language from `config/language_editions.json` + the
cross-source catalogue, fetches each, converts to USJ, writes `config/pins/<tag>.json` (URL, sha256,
license pointer).

**Extracts:** `pipeline/work/ingest-cache/usj-<tag>/<NN>-<BOOK>.json` — one file per book, verse text
only. Plus verse **range markers** (`"3-4"`) which `read_verse_ranges` preserves.

**Slot relevance:** this step *defines* the slots. `usj_source.tokenize` (TOKENIZER_VERSION 2) splits
each verse into `[\p{L}\p{M}]` runs after `strip_marks`, with syllable segmentation for space-free
scripts. Target token index `j` means "position in this tokenization" everywhere downstream — the
compact-alignments contract, the gap-fill candidate pool, all of it.

**Protections here:** none needed — no claims are made yet.

---

## Step 2 — eflomal (`run_pilot --method eflomal` → `eflomal_align.py`)

**Does:** the statistical base pass. `build_corpus` pairs each spine verse with its target text
(pooling verse ranges and renumbering `idx` so `h_idx` means "position in the pooled group"), then
eflomal trains over the whole corpus at once and symmetrises forward+reverse with
grow-diag-final-and.

**Extracts:** `out/align_eflomal_<tag>_<BOOK>.jsonl` — one record per verse, one `pair` per aligned
source token:

```
h_idx, lexeme, strong, lemma, stem, surface, gloss_en, sense,
target, t_idx, score, method, content
```

**Slot allocation — this is the first and largest claim.**

- Source side fed to eflomal: `[t for t in rec.heb if getattr(t, anchor)]` where anchor is `strong`
  → **every token, content and non-content alike**.
- `decode` groups by source token: `by_s[s].append(t)` over the symmetrised set, `sorted(ts)`. A
  source token may claim **any number of target positions, contiguous or not**. There is no cap.
- Score: `0.9` if any of its links is in the forward∩reverse intersection, else `0.6`.

Measured on fra NT:

| | pairs | target slots consumed |
|---|---|---|
| content source tokens | 62,576 | 66,477 |
| **non-content source tokens** | **62,857** | **68,129 (50.6% of all)** |
| slots claimed by both a content and a non-content pair | | 719 |

Half of every target verse is spent on source tokens that will be deleted at export. Much of that is
correct and harmless (καί→*et*, ὁ→*la/le/les*), but it is spent inside the same greedy allocation the
content tokens are competing in, and nothing prefers content when they collide.

The final stage of `_grow_diag_final_and` is where a leftover claims a leftover:

```python
for (s, t) in union:
    if s not in src_al and t not in trg_al:
        aligned.add((s, t))
```

It iterates a Python **set** — arbitrary order — and applies no content preference, no score
threshold, and no distance limit. Whichever unaligned source token comes up first takes the free
target position.

**Existing protections:** the HMM distortion model penalises long-distance links, and the
intersection/score split marks (but does not block) the weaker half. That is all.

**Measured leak:** 6.3% of content-source pairs (3,962 of 62,576) end up with a target span made
*entirely* of words on this language's own stopword list. Top offenders, in order:

| lexeme | | target taken | count |
|---|---|---|---|
| `grc:1510` | εἰμί *be* | est / sont / y | 702 |
| `grc:3956` | πᾶς *all* | tous / tout / toutes / toute | 1,066 |
| `grc:3004` | λέγω *say* | **je** | 126 |
| `grc:1520` | εἷς *one* | un | 121 |
| `grc:2192` | ἔχω *have* | a | 98 |

The first four rows are already listed in `config/light_lexemes.json`. `grc:3004` λέγω is not — it is
an ordinary content verb absorbing the French subject pronoun instead of the verb form, which is the
subject-fusion span problem from `internal-docs/subject-fusion-span-prior.md` showing up as a
misallocated slot.

---

## Step 3 — Export, eflomal-only (`export_lex`)

**Does:** aggregates step 2's jsonl into `publish/lexeme-alignments/iso=<iso>/data.parquet`. Run here
because step 4's priors read exactly this file.

**Extracts:** `surface, lexeme, method, base_text, source_corpus, count, hi_conf`
(`strong` and `share` are derivable and deliberately not stored).

**Slot relevance — this is where `is_content` finally acts:**

```python
if not p.get("content") or not p.get("strong") or not p.get("target"):
    continue
```

The 68,129 slots spent by non-content tokens vanish from the output here. They are not *reclaimed* —
the target positions stay consumed in the jsonl, which is what every later step reads. Export is a
**presentation** filter, not an allocation one.

`hi_conf` = fraction of a pair's occurrences with `score >= 0.9` **and** `coherent is not False`
(BHSA phrase-scatter, OT only).

---

## Step 4 — Gloss (`run_pilot --method gloss` → `gloss_align.align_verse`)

**Does:** a second, deterministic dictionary pass. Priors come from `BootstrapPriors`, built from step
3's own parquet: per lexeme, its top-5 target renderings with share ≥ 4%, **keyness-filtered** —
a lexeme the prior-pack marks with `keyness is None` (a known function word) is dropped and never
becomes a gloss anchor. Plus a Strong's rollup tier and an LXX bridge for NT lexemes.

**Extracts:** `out/align_gloss_<tag>_<BOOK>.jsonl`, same pair schema, plus `light: true` on some pairs.

**Slot allocation — greedy, one target token per claim, first-come:**

```python
cands.sort(key=lambda m: (-m.score, pos_penalty(m)))
for m in cands:
    if m.h_idx in used_h or any(j in used_t for j in m.t_idx): continue
```

Candidate tiers and their scores:

| tier | score | what it is |
|---|---|---|
| `name` | 0.98 / 0.92 / 0.82 / 0.75 | English gloss vs target surface, edit distance |
| `multi` | 0.95 | consecutive span, every word ≥ 0.7 against the prior |
| `exact` | 1.0 | normalised forms identical |
| `stem` | 0.9 | match via an affix-stripped form |
| `prefix` | 0.7 | shared 4+ char prefix |
| `head` | 0.65 | multiword prior matching only its head word |
| `fuzzy` | 0.6 | Levenshtein ≤ 1 on 5+ chars |

Source side is again `for h in heb: if not h.strong: continue` — **all tokens, content and
non-content**. A non-content lexeme with a prior entry competes in the same sort.

**Existing protections and their real state:**

- **Source-side keyness filter (active).** The strongest protection in the pipeline: function-word
  lexemes never get priors, so they cannot raise a candidate at all. It comes from the prior-pack,
  so it covers only lexemes the prior-pack knows.
- **`#3` target stopword gate — OFF by default.** `align_verse` accepts a `stopwords=` filter that
  builds `blocked = {j for j in ... if stopwords.is_function(tokens[j])}` and excludes those
  positions from every single-token tier. But `run_pilot --gloss-signals` defaults to `morph,scatter`
  — `stopwords` is not in it, and `full_chain` does not pass the flag. The gate is dead code in the
  shipped chain. It was disabled because it *craters* gloss (it blocks legitimate function↔function
  alignments, which gloss does need to make).
- **`#4` light-lexeme filter (`scatter`) — ON, but inert.** The 19 lexemes in
  `config/light_lexemes.json` get `m.light = True`, `run_pilot` writes `"light": true` into the pair,
  and **nothing in the production chain reads it**. The only consumer is `merge_align.py:78`, which
  `full_chain` never calls. Its docstring's promise — "don't vote in the merge and are opened to
  gap-fill" — is not what happens: the pair is emitted normally, consumes its target position
  normally, and counts as `covered` in step 5, which is precisely what closes the token to gap-fill.
  17 of the 19 light lexemes are `is_content = 1` in the spine (~19,200 occurrences), so they also
  survive the export filter.
- **`#1` cross-lingual span extension — off** (`cross_lang` is `None` unless passed; the shipped
  profile was measured to carry no signal).

---

## Step 5 — Gap-fill (`gapfill.py` → `gapfill_align.GapFiller`)

**Does:** the only step that reasons explicitly about leftovers. Fills content source tokens that
eflomal *and* gloss both missed, onto target positions those two left untaken.

**Extracts first (`load_covered`, from the eflomal+gloss jsonl):**

| signal | how |
|---|---|
| `covered_h[ref]` | source `h_idx` already aligned → the complement is the gap set |
| `taken_t[ref]` | target positions already consumed → the untaken constraint |
| `anchors[ref]` | `{covered h_idx: first target pos}` → positional/diagonal interpolation |
| `strong_surf` | `{strong: top-5 target words}`, each ≥ 10% of that Strong's own aligned mass |
| `target_pos` | `{target word: majority source POS}` → bootstrapped target POS |
| `rec_after_rate` | P(construct dependent's target follows its head's), n ≥ 50 |
| `func_order` | P(target keeps source order) per adjacent BHSA function pair, n ≥ 100, deviation ≥ 0.20 |
| `morph_surf` | `{(strong, number/gender, value): surfaces}`, n ≥ 3 |

**Critical asymmetry — `load_covered` filters on `p.get("content")`.** The 68,129 target slots that
non-content source tokens consumed in step 2 are **not** in `taken_t`. Gap-fill therefore treats them
as free and may re-claim them. Two consequences, in opposite directions:

- good: a slot wrongly spent on הַ or καί can be recovered by a real content fill;
- bad: a *correct* function-word alignment gets silently overwritten by a fill, and the output then
  carries two source tokens claiming one target position with nothing recording the conflict.

**Slot allocation:**

```python
avail = [j for j in range(len(tokens))
         if j not in taken and not (stopwords and stopwords.is_function(tokens[j]))]
```

The `#3` stopword gate **is** active here — unlike in gloss — and it is the pipeline's only
unconditional target-side protection. Then only four priors may *fire* a fill:

| prior | boost | fill score | measured token precision |
|---|---|---|---|
| `strong` (rollup back-off) | 0.60 | 0.9 | 28.6% |
| `name` (transliteration) | 0.60 | 0.9 | — |
| `cross_edition` (another edition's vocab for this lexeme) | 0.50 | 0.9 | 24.0% |
| `phrase` / `phrase_xorder` (BHSA adjacency, last-resort pass) | 0.40 | 0.75 | 13.7% / 18.1% |

Plus two tie-breaks that never qualify a candidate alone: `pos_boost` 0.15, `morph_boost` 0.10 (both
measured as effective no-ops — by gap-fill time the candidate pool is usually down to one option).
A distance penalty applies against either the phrase-mate expectation (`0.6 × |j − expected| / n`) or
the diagonal (`0.2 × …`).

Vocabulary priors are assigned greedily by score; the phrase tier runs as a **second pass** that may
only take source tokens and target slots no vocabulary prior wanted — fired greedily alongside them it
was measured stealing slots from higher-precision `cross_edition` fills.

**Span extension (post-hoc, additive):** each fill may absorb `--max-extend` (default 1) further
tokens, trying right then left, skipping any position that is taken or is a stopword (unless
`--extend-over-stopwords`). Gold says a source content token takes 2.18 target words on average and is
multi-word 76.6% of the time, so this is where under-filling is meant to be corrected — but 85.2% of
gold span members are words our own stopword lists flag, so the gate blocks most legitimate span
material. Raising the cap was measured to buy coverage at ~24% marginal precision.

---

## The stopword list itself (`target_stopwords.py`)

Not a chain step — a cached artifact (`publish/target-stopwords/<iso>.txt`) that steps 4 and 5 read.

**Induction:** rank word-forms by raw corpus frequency, keep the top 150 whose **dispersion** (share
of books they appear in) is ≥ 0.85.

**Content-word rescue** (removes a candidate from the list): a candidate is rescued if its dominant
lexeme in this language's own eflomal output is a prior-pack **content** lexeme, gated by three
conditions:

- dominant lexeme carries ≥ 40% of the surface's aligned mass;
- the surface has ≥ 25 aligned occurrences at all (a proportion off 1 observation is not a
  proportion — and function words are systematically under-aligned, so low mass is itself evidence);
- the dominant lexeme is **not** in `config/light_lexemes.json` (19 lexemes — copulas, quantifiers,
  negators, possessives, numerals, grammaticalised body-part nouns).

The LXX bridge pooling was built and retired (over-broad: θεός pools with "father", "wealth", "rock").

`fra` ends up with 81 words. Coverage of the rescue inputs is a real dependency — without
`lexeme-alignments/iso=<iso>/` or the prior-pack it degrades to the raw frequency list, which is how
914 published lists once shipped with content words in them.

---

## Steps 6–9 — Export and derived datasets

Nothing here allocates a slot; all four read the union of the three jsonl and filter to
`content = True`.

| step | module | extracts | extra filter |
|---|---|---|---|
| 6 | `export_lex` | `surface, lexeme, method, base_text, source_corpus, count, hi_conf` | content; additive union across methods and pooled editions, nothing merged |
| 7 | `export_mwe` | `lexeme, strong, phrase, n_words, source_corpus, count, share, contig` | content **and contiguous** (`max−min+1 == len`) — scattered spans dropped and counted |
| 8 | `senses_attested` | `lexeme, stem, sense, surface, count, share, method, source_corpus, base_text` | OT only, needs a spine `sense` |
| 9 | `compact_align` | per-verse `"srcOrd:span ..."`, position-parallel to the canonical index | content lexemes only; ordinals are among *content* lexemes in spine order |

Note step 9's consequence: the compact format numbers source tokens by their ordinal **among content
lexemes**. A non-content token's claim is invisible in it, so a client decoding compact-alignments
cannot tell that a target word was already spoken for.

---

## Slot ledger — the whole chain in one table

| stage | source tokens eligible | target positions eligible | cap per claim | who wins a contested slot |
|---|---|---|---|---|
| eflomal | all 607k (Strong's-bearing) | all | unbounded, non-contiguous allowed | GDFA: intersection, then diagonal growth, then arbitrary set order |
| gloss | all Strong's-bearing, *but* only lexemes with a keyness-passing prior can raise a candidate | all (stopword gate available, **off**) | 1, or a prior's full span | highest score, then positional proximity |
| gap-fill | content tokens missed by both | untaken-by-**content** minus stopwords | 1 + `max_extend` (default 1) | highest prior score; phrase tier only on leftovers |
| export ×4 | — | — | — | nothing; content filter is presentational |

---

## Protection #1 — measured (2026-09-01)

Two designs were built (`run_pilot --eflomal-content-only` / `--eflomal-content-priority`, both
default OFF) and scored against Clear gold on three languages, NT, with `tests/test_slot_allocation.py`
locking the allocation logic.

Two metrics, because one of them is gameable: `hit` = did the content token get *any* target word gold
credits to it (span-inflation rewards this); `tokP` = of the target words our content pairs claim, what
fraction gold credits (span-inflation punishes this). Read them together.

| | fra hit | fra **tokP** | hin hit | hin **tokP** | eng hit | eng **tokP** |
|---|---|---|---|---|---|---|
| baseline | 70.3% | **67.7%** | 86.7% | **85.8%** | 87.6% | **89.4%** |
| B content-only source line | 71.1% | **43.6%** | 84.2% | **66.5%** | 86.4% | **67.7%** |
| D content-priority realloc | 70.6% | **67.9%** | 87.1% | **85.7%** | 87.7% | **89.0%** |

Baseline theft rate (gold-correct word held by a function-word source token, gold not crediting it
there): fra 12.6%, hin 4.8%, eng 3.0%. D reduces it to 12.2 / 4.4 / 2.9.

**B (drop non-content source tokens) is REFUTED.** Its apparent `hit` gain on fra is entirely span
inflation: average target words per content pair goes 1.06 → 1.66 (+56%), and token precision collapses
by 19-24 points on all three languages. The mechanism is now clear and worth stating, because it is
counter-intuitive: **the non-content source tokens are load-bearing — they are the sink that absorbs
target function words.** Removing them does not free *le/la/और/the* for the right content word; it makes
content words swallow them instead. Do not retry this.

**D (content-priority reallocation) is safe, honest, and too weak to adopt.** It does not inflate
(+0.16% words claimed on fra) and it does reduce theft on all three languages — but it moves only
214 / 284 / 233 positions, because it fires solely for content tokens with *no* link at all and
coverage is already 95-96%. Result: `hit` +0.3 / +0.4 / +0.1, `tokP` +0.2 / −0.1 / −0.4. That is a
wash inside noise, and it costs precision on eng. **Not adopted as a default** — kept behind the flag
with these numbers recorded, so the next attempt starts from the measurement rather than repeating it.

The theft case D misses is the common one: the content token *has* a link, just the wrong one, while
the word it needed is held by the article. Reaching that case means displacing an existing link, which
is the first genuinely risky change in this area and must be judged on `tokP`.

**Grambank does NOT predict where this helps.** The natural hypothesis — a language with no definite
article (GB020=0) has nothing for ὁ/הַ to legitimately claim, so removing them should help most —
is contradicted: `hin` (GB020=0) was harmed *most* by B (−19.3 tokP), `fra` (GB020=1) least. The
article feature is real and correctly coded; it just does not govern this. Grambank's role at this step
is not the gate it looked like.

**D2 — displacement (the risky tier) — also REFUTED.** `--eflomal-displace-weak` lets a content token
whose links are *all* outside the forward∩reverse intersection trade that link set for a position the
union proposed and only a non-content token holds. It displaces rather than adds, so it cannot inflate
spans. Measured on top of #2 (contiguity), the correct incremental baseline:

| | fra hit / **tokP** / theft | hin hit / **tokP** / theft | eng hit / **tokP** / theft |
|---|---|---|---|
| #2 only | 69.7 / **69.0** / 12.0% | 86.2 / **88.0** / 5.0% | 87.4 / **90.3** / 3.1% |
| #2 + displace | 69.8 / **68.8** / 11.7% | 86.2 / **87.7** / 4.3% | 87.3 / **90.2** / 2.8% |

It does exactly what it was designed to do — theft falls on all three (−0.3 / −0.7 / −0.3) — and token
precision falls with it (−0.2 / −0.3 / −0.1). The `other-miss` column shows why: 8,629→8,735,
3,372→3,623, 5,406→5,622. **Taking the position away from the article does not hand it to the right
content token.** It moves the token out of the "stolen" bucket and into the "wrong for some other
reason" bucket, at the cost of a weak link that was sometimes correct.

### What #1 taught us, across all three designs

The theft is real and measured (12.6% / 4.8% / 3.0% of judgeable content tokens). But **every attempt to
fix it in the allocation layer failed**: removing the competitors (B) collapses precision by 19-24
points, prioritising content (D1) is a wash, and displacing weak links (D2) trades precision for a
better-looking theft number.

The reason is now visible and worth stating: eflomal's output is already near the best allocation *its
model supports*. A target position sitting on ὁ is not there because the allocator preferred ὁ — it is
there because the model's parameters put it there. Freeing it does not make the model want to give it
to the right content token.

**So the lesson for the remaining steps: theft at step 2 is a symptom, not a lever.** The lever is what
the model is given to work with — the priors at step 4 and the candidate gating at step 5, where a
decision is made from an explicit dictionary rather than from learned co-occurrence. A "reduce the theft
metric" objective is actively misleading here; token precision is the only trustworthy target.

### What this step would need to extract for later steps

Inducing a target language's article/adposition typology from our own data requires knowing what ὁ,
καί, ἐν, εἰς actually render as — and in `fra` that signal is strong and correct (ὁ → *la/le/les/l*,
38.5% top-3 concentration over 160 distinct forms; καί → *et*, 73.8%). But **`export_lex` drops every
non-content row**, so this has already been discarded for all 1,537 published languages. Any use of an
induced (rather than Grambank-supplied) typology requires extracting a small per-language function-word
rendering profile at step 2/3, *before* the content filter runs.

---

## Protection #2 — measured (2026-09-01), ADOPTABLE

`run_pilot --eflomal-contiguous-only` (`_longest_contiguous`, default OFF): keep only the longest
contiguous run of a source token's target positions; release the scattered outliers. Ties break toward
the run holding an intersection-backed link, then length, then earliest.

**The controlled measurement.** Comparing "core vs outlier" directly is biased (gold often lists one
surface, so the outlier is wrong by arithmetic). The clean test holds span size constant at 2 and varies
only contiguity, within the same gold entries:

| | span-2 contiguous | span-2 scattered | contiguity effect |
|---|---|---|---|
| fra | 41.0% | 37.8% | +3.2pt |
| hin | 82.5% | 64.0% | **+18.5pt** |
| eng | 84.5% | 62.3% | **+22.2pt** |

fra cannot discriminate here — its Clear gold averages **1.07** surfaces per (ref, strong) entry, so any
multi-word span is capped by arithmetic. hin (2.32) and eng (2.54) can, and both show a ~20pt effect.
**This is why fra's absolute `tokP` is not comparable to hin/eng's** — a fact worth remembering for
every future measurement on this gold.

**End-to-end result:**

| | fra hit / **tokP** | hin hit / **tokP** | eng hit / **tokP** |
|---|---|---|---|
| baseline | 70.3 / **67.7** | 86.7 / **85.8** | 87.6 / **89.4** |
| contiguous-only | 69.7 / **69.0** | 86.2 / **88.0** | 87.4 / **90.3** |

Token precision rises on all three (+1.3 / +2.2 / +0.9) for a small coverage cost (−0.6 / −0.5 / −0.2).
The material dropped is genuinely low-grade: of the 1,561 / 2,307 / 1,302 target tokens released, only
23.4% / 44.3% / 46.0% were gold-correct, against kept-token precision of 68–90%.

Unlike #1 this is a real trade, not a free win — but it moves in the direction the pipeline is for, and
it makes the aligner consistent with `export_mwe`, which has always refused to publish scattered spans
for exactly this reason. **Recommended as the default**, pending the owner's call, since flipping it
changes every published partition.

### Grambank at this step — a usable exception list

`GB026` "Can adnominal property words occur **discontinuously**?" is precisely the feature that says
whether a scattered span can be legitimate. It is coded for 1,681 ISOs, covers **586 of ours**, and is
positive in only 105 (6.2%) — so contiguity is the right global default and GB026=1 is a narrow, principled
opt-out. `GB136` (is core-argument order fixed?) covers 670 of ours and is a plausible second signal.

Values for our gold languages: fra 0, eng 0, arb 0, ind 0, por 0 — none licenses discontinuity. The one
gold language flagged `GB026=1` (and `GB136=0`, free order) is **rus**, which is quarantined for bad gold.
So the exception list is well-motivated typologically but **cannot currently be validated on any gold
language** — it should ship as an opt-out that only ever *disables* a protection, never as a predictor.

Note also `GB137` (clause-final negation) = 1 for fra: French *ne … pas* is genuinely discontinuous, which
may be part of why fra's numbers behave differently throughout.

---

## Protection #3 — measured (2026-09-01): a real bug found, but the fix itself is INERT

The `light` flag was computed, written to every jsonl, and read by nothing in the production chain.
Two changes were made, and only one of them mattered.

**The bug (real, now fixed).** Gloss's `#4 scatter` signal was reading
`publish/cross-lingual-span-profile/light_lexemes.json` — a **691-lexeme** set auto-built by
`cross_lang_prior` — not the hand-curated 19 in `config/light_lexemes.json`. The two sets overlap in
**2 lexemes**. Measured share of a set's content pairs landing ENTIRELY on target stopwords:

| set | fra | hin | eng | vs base rate |
|---|---|---|---|---|
| `config/light_lexemes.json` (19) | 55.7% | 76.0% | 62.2% | **~9x** |
| auto-built (691) | 14.4% | 27.9% | 17.7% | ~2.5x |
| all content pairs (base rate) | 6.3% | 10.3% | 6.6% | — |

The auto set is barely light and spans ~15% of all content pairs — same root cause as the retired span
profile (it is derived from OUR OWN alignments, so it measures our inconsistency, not semantic
lightness). Had the release below been switched on against *that* set it would have freed the slots of
~9,700 pairs per language, most of them ordinary content words. `run_pilot --light-lexemes` now defaults
to the curated file.

**The fix (inert).** `gapfill.load_covered(release_light=True)` now keeps a light pair in `covered_h`
(never wasting a fill re-aligning it) while withholding its target positions from `taken_t` — exactly
the asymmetry the project wants: we do not care about getting light words right, only about them not
holding a slot a real match needed. It releases 4,773 / 2,440 / 5,251 positions… and changes almost
nothing:

| | released | gap fills off → on | hit | tokP |
|---|---|---|---|---|
| fra | 4,773 | 256 → 260 | 72.6 → 72.6 | 68.9 → 68.9 |
| hin | 2,440 | 669 → 669 | 87.2 → 87.2 | 83.5 → 83.5 |
| eng | 5,251 | 163 → 165 | 89.4 → 89.4 | 89.4 → 89.4 |

**Why it cannot work where it was placed.** 55-76% of light pairs sit on target *stopwords*, and gap-fill
already excludes stopword positions from its candidate pool — so most of what is released is material
gap-fill refuses to use anyway. The rest only pays off if a freed position coincides with a gap token
that has a firing prior, and gap-fill's whole reach is small (1,092 / 2,229 / 1,255 gap tokens, of which
13-30% get filled, overwhelmingly by `cross_edition`).

Kept anyway (it is correct, costs nothing, and is now honest about what it does), with
`--no-release-light` to ablate. But **the light-lexeme leverage is not at step 5.** These lexemes take
their slots at steps 2 and 4, and by the time gap-fill runs the allocation is long settled — the same
lesson #1 taught, arriving from the other end.

---

## Protection #4 — measured (2026-09-01): ADOPTED, the largest win so far

Two designs, both aimed at a light or function source lexeme taking a slot in gloss's greedy assignment.

**`swsrc` — the source-gated stopword filter. ADOPTED, now in the default `--gloss-signals`.**
The plain `stopwords` gate blocks every single-token tier from landing on a target function word, and
it craters gloss — because it also blocks legitimate **function↔function** matches (ὁ→*le* is correct).
Gating it on the SOURCE instead — block only when the source is a **content, non-light** lexeme —
removes exactly that failure mode and keeps the case it was built for:

| | fra hit / **tokP** | hin hit / **tokP** | eng hit / **tokP** |
|---|---|---|---|
| gloss baseline | 72.5 / **69.0** | 86.8 / **83.7** | 89.4 / **89.4** |
| **+ swsrc** | 72.5 / **70.6** | 86.7 / **86.8** | 89.4 / **90.8** |

**+1.6 / +3.1 / +1.4 token precision with coverage flat** (`hit` moves −0.0 / −0.1 / 0.0). Unlike #2 this
is not a trade — it is close to free, and it is the only change in this whole pass that improves precision
without costing coverage. It works because gloss decides from an explicit dictionary: when the source is
known content and the only candidate target is a known function word, the match is wrong essentially by
construction, and gloss has the information to know it. eflomal never did.

**`light_last` — light lexemes claim only leftovers. INERT, left OFF.**
Light candidates sort behind every non-light candidate at any score, so they can only take positions no
real match wanted (deliberately not a ban — εἰμί→*est* is correct and is kept when uncontested; same
last-resort shape that fixed gap-fill's phrase tier). Measured: fra 69.0→69.0, hin 83.7→83.6,
eng 89.4→89.4. Kept behind the flag.

### Grambank at this step — the best fit found, and it explains the inert result

The light lexemes are copulas, existentials, *have*, *one*, possessives — and Grambank codes exactly
whether a language **has a word for each**:

| feature | covers ours | value=0 ("no such word") | maps to |
|---|---|---|---|
| `GB117` copula for predicate nominals | **692** | 43% | `grc:1510` εἰμί, `hbo:1961` היה |
| `GB126` existential verb | 601 | 34% | `hbo:3426` יֵשׁ |
| `GB250` transitive *habeo* 'have' | 477 | **57%** | `grc:2192` ἔχω |
| `GB313` special possessive pronouns | 598 | **63%** | `grc:1699/4674/2251/5212` |

This is the best Grambank fit in the pipeline: high coverage, high discrimination, a one-to-one mapping
onto specific lexemes, and it works as a per-(language, lexeme) gate rather than a global predictor.

It also **explains why `light_last` measured inert**: `fra`, `eng` and `por` code **1 on all four** — they
genuinely have a copula, a *have* verb and possessive pronouns, so their light-lexeme alignments are
largely legitimate and there is nothing for the protection to catch. The languages where it should bite
are the 43%/57%/63% coding 0, and **our Clear gold set contains none of them**. So `light_last` is
unproven rather than disproven, and the gate it needs is available for ~500-700 of our languages the
moment a gold language with GB117=0 or GB250=0 exists to validate it on.

---

## Protection #5 — measured (2026-09-01): real signal, too small to act on. Default OFF.

`gapfill --reserve-function-slots`: stop gap-fill claiming a target position a non-content source token
already holds. `load_covered` used to skip non-content pairs entirely, so their positions looked FREE and
two source tokens could claim one position with nothing recording the conflict.

**The direction is right.** Gap-fill fills that land on a function-word-held slot are less precise than
fills onto a genuinely free one, in all three languages:

| | onto a function-word-held slot | onto a free slot |
|---|---|---|
| fra | 46.2% (52 fills) | 47.8% (69) |
| hin | **51.9%** (129) | **65.3%** (193) |
| eng | 43.9% (57) | 44.8% (29) |

**The magnitude is not.** Reserving cuts gap fills by 29-42% (fra 286→204, hin 717→459, eng 174→101) for
a token-precision change of **+0.0 / +0.1 / +0.0**:

| | fills OFF → ON | hit | **tokP** |
|---|---|---|---|
| fra | 286 → 204 | 72.7 → 72.6 | 70.5 → **70.5** |
| hin | 717 → 459 | 87.1 → 87.0 | 86.5 → **86.6** |
| eng | 174 → 101 | 89.5 → 89.5 | 90.8 → **90.8** |

Gap fills are only ~0.2-0.7% of all claimed target tokens, so removing even their worst tier cannot move
the aggregate. And the double-claim it prevents is **internal only** — non-content rows are dropped at
export and compact-alignments indexes content lexemes exclusively, so no consumer ever sees the conflict.
Losing a third of gap-fill's coverage to tidy an invisible wart is a bad trade. Left OFF behind the flag.

### Grambank at this step — contradicted again, in the same way as #1

The relevant features are #1's (GB020 articles, GB074/GB075 adpositions): if a language has no articles,
ὁ/הַ holding a target word is spurious, so its slot should NOT be reserved. `hin` codes GB020=0 — and
`hin` shows the **largest** collision penalty (51.9% vs 65.3%), i.e. reserving helps most exactly where
the typology says the hold is meaningless. The refinement is contradicted; the global rule is the right
one. Same shape as #1: Grambank fails when used to predict eflomal's *allocation behaviour*, and
succeeds (#2 GB026, #4 GB117/GB250) when used to say whether a target word for a concept **exists**.

---

## Summary of the pass (2026-09-01)

Five protections, measured end to end against Clear gold on fra / hin / eng, NT, two metrics
(`hit`, and the inflation-proof `tokP`), with `tests/test_slot_allocation.py` locking each mechanism.

| | protection | verdict | tokP effect (fra / hin / eng) |
|---|---|---|---|
| #1 | non-content sources compete for slots | **refuted ×3** | −24.1/−19.3/−21.7 (drop) · +0.2/−0.1/−0.4 (prioritise) · −0.2/−0.3/−0.1 (displace) |
| #2 | unbounded, non-contiguous spans | **ADOPTED, default ON** | **+1.3 / +2.2 / +0.9** |
| #3 | light-lexeme slots never released | corrected (wrong source file), inert | 0.0 / 0.0 / 0.0 |
| #4 | gloss's disabled target-side gate | **ADOPTED, default ON** | **+1.6 / +3.1 / +1.4** |
| #5 | gap-fill blind to function-word slots | real but negligible, default OFF | +0.0 / +0.1 / +0.0 |

End-to-end, eflomal+gloss+gapfill: **fra 68.9 → 70.5, hin 83.5 → 86.6, eng 89.4 → 90.8** token precision,
with coverage flat (`hit` 72.6→72.6, 87.2→87.0, 89.4→89.5).

**The through-line.** Every protection placed in an *allocation* layer failed or was negligible (#1, #3,
#5). Both that worked act where a decision is made from **explicit knowledge** rather than learned
co-occurrence: #2 applies a structural fact (a span should be contiguous) and #4 applies a dictionary
fact (a content lexeme's only candidate being a known function word means the match is wrong). eflomal's
output is already near the best allocation its model supports — so the recurring "light word took the
slot" pattern is not fixable by moving slots around after the fact. It is fixable where the pipeline
knows something the model does not.

**Grambank's real role, after five tests.** It fails as a predictor of allocation behaviour (#1 GB020,
#5 GB020) and succeeds as an existence gate — GB026 at #2 (does discontinuity occur?), GB117/GB126/GB250/
GB313 at #4 (is there a word for this concept at all?), covering 477-692 of our languages with 34-63%
negative. Both successes are **exception lists that only ever disable a protection**, which is why sparse
coverage is acceptable there and fatal in a predictor. Neither is wired into the default path: no gold
language can currently validate either (the GB026=1 candidate is quarantined `rus`; every gold language
codes 1 on the #4 features), so both wait on gold coverage, not on implementation.

---

## Where a protection could go

*(Status after 2026-09-01: #1 refuted in all three forms; #2 adopted (default ON); #3 corrected but
inert; #4 adopted (`swsrc` default ON, `light_last` unproven for want of a suitable gold language).
#5 remains.)*

Reading the ledger, there are exactly five places a light or function word can take a slot from a real
match, and they are not equally addressable:

1. **eflomal's non-content source tokens (largest, currently unprotected).** 50.6% of consumed slots.
   No gate exists; `is_content` is available on every token and is simply not consulted until export.
   Anything from "excluded from the final-and pass" to "excluded from the source line entirely" is
   possible here, and each has a real cost — eflomal's co-occurrence model uses those tokens, and
   removing them changes what it learns, so this needs measuring rather than assuming.
2. **eflomal's unbounded, non-contiguous span per source token.** A source token can claim a scattered
   handful of positions with no length or contiguity constraint. `export_mwe` already treats scattered
   spans as untrustworthy and drops them; the aligner does not.
3. **The `light` flag that does nothing (cheapest fix in the pipeline).** It is computed, written to
   every jsonl, and read by no production module. Making step 5 treat a light pair as *not* covered
   would do exactly what its own docstring already claims, and reopen ~19,200 occurrences to gap-fill.
4. **Gloss's disabled target-side gate.** Off for a good reason (it blocks legitimate function↔function
   matches). A gate conditioned on the *source* being a content lexeme — rather than on the target
   being a stopword — would not have that failure mode.
5. **Gap-fill's blindness to non-content `taken_t`.** It cannot see the slots eflomal spent on
   function words. Whether that should be fixed depends on which direction is worse: silent
   double-claims, or gap-fill unable to recover a slot eflomal misspent.

The measured leak — 6.3% of content pairs landing entirely on stopwords — is concentrated in a short
head (`grc:1510`, `grc:3956`, `grc:1520`, `grc:2192` = 1,987 of 3,962), all of which are *already*
identified in `config/light_lexemes.json`. The identification is done. What is missing is any step
that acts on it.
