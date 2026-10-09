---
pretty_name: Compact per-verse alignments (position-only, content-addressed)
tags:
  - bible
  - word-alignment
  - interlinear
task_categories:
  - translation
  - token-classification
license: cc0-1.0
---

# compact-alignments — per-verse, per-book, content-addressed

The token-**position** companion to [`lexeme-alignments`](../lexeme-alignments) (which is
aggregated/type-level and can't tell you what happened in any *one* verse). This dataset restores
**position**: for a given edition's Bible book, which Hebrew/Greek content word aligned to which
target-text token, verse by verse.

> The authoritative list of what's published is always `manifest.json`, not this file.

## Original-language source editions (needed to resolve `srcOrd`)

`srcOrd` (in the compact string, below) is an ordinal among a verse's Hebrew/Greek content **lexemes**
(the same MACULA lexeme anchor `lexeme-alignments` uses — see below) — to turn it back into an actual
source word, a client needs the SAME fixed original-language edition this project aligns against, for
both testaments:

| testament | edition | source |
|---|---|---|
| Hebrew (OT) | **WLC** (Westminster Leningrad Codex) | Clear-Bible `macula-hebrew@main` |
| Greek (NT) | **Nestle1904** | Clear-Bible `macula-greek@main` |

Same editions `lexeme-alignments` anchors against — see that dataset's README for detail.

## What this is *not*

It does not contain any Bible text. Every entry is a pair of **integers** (an ordinal and a target
token position) — no words, no verse text, nothing copyrightable. To turn a compact string back into
words you need the edition's own text (tokenized the same way alignment tokenized it), obtained
separately from that edition's own source. This is deliberate: it's what keeps the dataset unambiguously
CC0 and small.

## Where the files live — three repos, one path scheme (since 2026-10-02)

This repo passed Hugging Face's recommended 100,000 files per repo, so the two optional sidecar layers are published as sibling datasets under
the **identical relative path** — `<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.<layer>.json` — so a reader that wants a sidecar only needs a second
base URL (and a reader that only wants alignments changes nothing):

| layer | file | repo |
|---|---|---|
| alignments (this repo) | `<BOOK>_<hash>.json` | `bcv-commons/compact-alignments` |
| provenance sidecar | `<BOOK>_<hash>.meta.json` | [`bcv-commons/compact-alignments-meta`](https://huggingface.co/datasets/bcv-commons/compact-alignments-meta) |
| opt-in residual layer | `<BOOK>_<hash>.extra.json` | [`bcv-commons/compact-alignments-extra`](https://huggingface.co/datasets/bcv-commons/compact-alignments-extra) |

`_index/`, `manifest.json`, `tokenize.js` and `pipeline_decisions.json` stay here. Not every book has a sidecar file — a missing one is simply a 404.

## Two kinds of file

### 1. Shared content-lexeme sequence — `_index/<BOOK>_lexemes.json`, published ONCE per book

```json
{"RUT 1:1": ["1961", "3117", "8199", "8199", "1961", "7458", "0776", ...], ...}
```

The **only** shared index — this dataset does NOT also publish a separate flat `["BOOK C:V", ...]`
ref list. That would be pure redundancy: this object's own (ordered) **keys** already are that list,
so a client derives it in one line rather than downloading a second, duplicate file (see below).

`srcOrd[i]` in a compact string (file kind #2) is the lexeme at position `i` in the value here — resolved
for you, no morphology knowledge required. Lexemes are published **without** the `hbo:`/`grc:` language
prefix that `lexeme-alignments` uses (`hbo:0430` → plain `0430`) — redundant here specifically, because
a BOOK is always entirely one testament (Hebrew OT or Greek NT, never mixed), so the book code itself
already tells you which language every entry in the file is. (Don't drop the prefix anywhere it might
mix testaments, e.g. `lexeme-alignments` — one partition there spans a whole language, both OT and NT.)

**Version stamp — `_index/_source.json`.** Records the source spine's own content pin (`spine_sha256`) and,
per book, the verse count, the content-token count and the sha256 of that book's `_lexemes.json`. The index is
checked against the spine with `python3 -m lexeme_aligner.source_index --check`, and no array is written
against an index that disagrees with the spine. Why it exists: on 2026-09-23 the spine gained one content
token in each of ten verses (DAN 5:27, DAN 5:28, ECC 6:11, EZK 7:13, EZK 16:53, EZK 30:16, ISA 24:16,
JER 12:1, NAM 3:17, PRO 26:17), so `srcOrd` values after the inserted token in those verses differ between
files built before and after that date unless they are renumbered; the files here have been (the inserted
token is unaligned in them).

**`srcOrd` cannot be resolved from file kind #2 alone** — you always need this file too. It exists
specifically so a client never has to reconstruct `is_content` themselves. Live case that motivated it:
Hebrew's direct-object marker (Strong's `H0853`) shares its bare lexeme id with a much rarer noun
homonym — nothing in the lexeme or Strong's code alone distinguishes them, in EITHER our spine or a
typical external lexicon. `is_content` in our own pipeline isn't derived from lexeme/Strong's either —
it comes from a per-occurrence morphological class tag (MACULA `class`: noun/verb/adjective vs
particle/etc.) — so we resolve it once, here, and publish the answer rather than asking every client to
independently re-derive it from their own morphology data and hope it agrees with ours. Concrete check,
`GEN 1:1` ("In the beginning God created the heavens and the earth"): the verse has 7 Hebrew tokens
including **two** occurrences of `H0853` (the object marker, before "heavens" and before "earth") —
`_index/GEN_lexemes.json["GEN 1:1"]` lists exactly **5** lexemes (`7225` beginning, `1254` create,
`0430` God, `8064` heavens, `0776` earth) — both `0853` occurrences correctly excluded, with nothing
beyond an array lookup required to know that.

Edition-independent — written once, the first time any edition publishes that book, every subsequent
edition just reuses it. See the pooled-range caveat in `compact_align.py`'s `build_source_lexemes`
docstring if you're decoding a PKF-style pooled target-verse range (rare).

### 2. Per-edition, per-book compact array — `<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json`

```json
["0:1 1:2 2:5 3:5-6 4:12 5:10 6:13 7:16 8:17 9:19 10:21 11:30 12:33 13:35 14:24 15:27",
 "0:3 1:1 2:5 3:9 4:7 5:11 6:14 7:18 8:20 9:22 10:24-25 11:27 12:29 13:32 14:34 15:36 16:38",
 "0:5 1:4 2:3 3:1 4:9 5:13",
 ...]
```

Same length as that book's `_lexemes.json`, **position-parallel to its keys** — array element `i` is
the compact string for the `i`-th verse ref (in `_lexemes.json`'s own key order). No verse-ref keys
stored per edition.

### 3. Provenance sidecar — `<BOOK>_<hash>.meta.json`

> Published in [`bcv-commons/compact-alignments-meta`](https://huggingface.co/datasets/bcv-commons/compact-alignments-meta) under the same relative path (see *Where the files live*).

Optional, additive, and **safe to ignore** — a reader that only wants alignments can stop at section 2.
It exists because an alignment string alone cannot tell you *who produced this and how sure they were*,
and because a merged file silently discards the alternative that lost.

```json
{"method":    ["eEgEEE", ...],
 "conf":      ["121222", ...],
 "contested": ["2:E:2",  ...]}
```

All three arrays are position-parallel to the same book index as the alignment file.

**`method` and `conf` are dense** — one character per aligned token, in the same order as that verse's
compact entry. Character `i` describes the `i`-th `srcOrd:span` of the alignment string, so the two are
read side by side and their lengths always match.

```
alignment  "0:1 1:2 2:5 3:5-6"
method     "EGeF"          E = eflomal, G = gloss, e = eflomal, f = gapfill
conf       "2211"          how many methods produced that identical span
```

| method char | meaning |
|---|---|
| `E` / `e` | eflomal, at its own score 0.9 / 0.6 |
| `G` / `g` | gloss, strong match (`exact`, `stem`) / weaker (`head`, `fuzzy`, `prefix`, `multi`) |
| `f` | gapfill (already restricted to the `strong` and `name` priors) |
| `r` | residual — appears only against the opt-in `.extra.json` layer |

**`contested` is sparse** — only the positions where eflomal and gloss proposed *different* spans and a
rule had to choose. Each entry is `srcOrd:method:span` naming the **loser**; the winner is in the
alignment file at the same `srcOrd`. `""` where nothing was contested.

This is the only place a discarded alternative survives; everything else in this dataset is a
winner-take-all projection. If you disagree with our choice, this is what lets you make your own.

**Do not read `conf` as an absolute guarantee.** Agreement ranks well *within* an edition, but its
availability depends on how many methods happened to work for that language — an edition whose gloss
pass ran at 20% coverage will show mostly `1` for reasons that have nothing to do with quality. Compare
within an edition, never across.

### Deriving the ref list — one line, either language

Since the ref list is just `_index/<BOOK>_lexemes.json`'s own ordered keys, don't fetch/store it
separately — derive it from the same file you already need for lexemes:

**Python:**
```python
import json
lexemes = json.loads(open("_index/RUT_lexemes.json").read())
refs = list(lexemes.keys())                          # ["RUT 1:1", "RUT 1:2", "RUT 1:3", ...]
array = json.loads(open("e/eng/eng_BSB/RUT_101a1.json").read())
by_ref = dict(zip(refs, array))

by_ref["RUT 1:1"]        # -> "0:1 1:2 2:5 3:5-6 4:12 5:10 6:13 7:16 8:17 9:19 10:21 11:30 12:33 13:35 14:24 15:27"
lexemes["RUT 1:1"][0]    # -> "1961"  (the lexeme srcOrd 0 refers to)
```

**JavaScript:**
```javascript
const lexemes = JSON.parse(await fetch("_index/RUT_lexemes.json").then(r => r.text()));
const refs = Object.keys(lexemes);                    // ["RUT 1:1", "RUT 1:2", "RUT 1:3", ...]
const array = JSON.parse(await fetch("e/eng/eng_BSB/RUT_101a1.json").then(r => r.text()));
const byRef = Object.fromEntries(refs.map((ref, i) => [ref, array[i]]));

byRef["RUT 1:1"];         // -> "0:1 1:2 2:5 3:5-6 4:12 5:10 6:13 7:16 8:17 9:19 10:21 11:30 12:33 13:35 14:24 15:27"
lexemes["RUT 1:1"][0];    // -> "1961"  (the lexeme srcOrd 0 refers to)
```

(`Object.keys()`/`for...in` iterate string keys in insertion order per the ECMAScript spec since ES2015
— this is guaranteed, not just conventional, in modern JS. Same guarantee in Python 3.7+ dicts.)

## Function-word convention (decided 2026-09-29)

**The rule.** A target word that renders a source *function* token (an article such as ὁ or Hebrew ה, a
preposition prefix, a conjunction) belongs to **that token's own alignment**. Only a target function word with
**no** source token of its own (a case ending's "of", an assimilated article, a construct "of") is folded into
the neighbouring content word's span. Rationale: every source token gets its own row, and a client that wants
the "content word plus its function words" view can rebuild it, whereas a fold cannot be split back without
extra data.

**What is true today, so you can rely on it (the data does not yet follow the rule everywhere).**
- This array indexes **content tokens only** (`_index/<BOOK>_lexemes.json`), so a function token's own row is
  not represented in this array. Their source tokens ARE published, as `_index/<BOOK>_fn.json` (since 2026-10; see
  "The function-word index" below); the function-token *alignments* are not part of this array.
- Where the chain folded a function word into a content span, that fold is **always labelled** in the
  `rule` sidecar (`'srcOrd:label:targetIdx'`), and is reversible with `published_span - {targetIdx ...}`.
  That covers exactly the words the convention says to fold *unless* the word has an own source token, in
  which case the convention would move it to that token's row.
- Measured on the current output (whole Bible, spanext-widened words only): an article token sits directly
  before the widened content token in about 10% of Hindi and 4% of English widenings; other function tokens
  (prepositions, conjunctions) precede a much larger share and have not been classified yet.

**What will change, and how it will be delivered.** Function-token alignments will be added as a separate,
additive layer with its own source index, so nothing above changes shape for existing clients; the main array
will then drop an appended function word only where that word has an own token and is published in the new
layer. Until that layer ships, treat folded function words as "attached to the content word, labelled".

## Path components

```
<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json
```

| segment | meaning | example |
|---|---|---|
| `<iso[0]>` | first character of the ISO 639-3 code — a sharding bucket, nothing more | `e` (for `eng`) |
| `<iso>` | the TRUE published language code | `eng` |
| `<edition>` | which edition/translation of that language, iso-prefixed unless it already carries one | `eng_BSB`, `arb_vdv` |
| `<BOOK>` | 3-letter USFM book code | `RUT` |
| `<hash>` | last 5 hex chars of that book's content hash (below) | `101a1` |

`<edition>` comes from the same identifier already published as `base_text` in `lexeme-alignments` — so
a consumer of both datasets can join on it directly.

## The compact string format

```
"0:1 1:2 2:5 3:5-6 4:12 5:10 6:13 7:16 8:17 9:19 10:21 11:30 12:33 13:35 14:24 15:27"
```

`srcOrd` counts **lexemes**, not raw source tokens — the SAME MACULA lexeme anchor (`lang:augmented-strong`,
e.g. `hbo:0430`) `lexeme-alignments` uses, not a bare Strong's number or a raw word count. See
[**lexeme-alignments' "The anchor: lexeme, not Strong's"**](https://huggingface.co/datasets/bcv-commons/lexeme-alignments#the-anchor-lexeme-not-strongs)
section for what that means and why (homonym/sense-split handling, the Strong's rollup, etc.) — this
dataset assumes that anchor as given rather than re-explaining it. (On GitHub, the same section lives at
[`lexeme-alignments/README.md`](https://github.com/bcv-commons/lexeme-aligner/blob/main/publish/lexeme-alignments/README.md#the-anchor-lexeme-not-strongs).)

| token | meaning |
|---|---|
| space | separates entries — one per aligned **content lexeme** (the lexeme anchor above) |
| `srcOrd:targetSpan` | `srcOrd` = 0-based ordinal among that verse's content lexemes ONLY, in source order (function words never get an ordinal) |
| `-` inside a span | a **contiguous** range of target-token positions, inclusive (`3-4` = tokens 3 and 4) |
| `,` inside a span | a **scattered**, non-contiguous list of target-token positions — see the worked example below |
| bare integer | one single target token |
| *(ordinal absent)* | that content lexeme is **unaligned** in this edition — no null placeholder |
| `""` (empty string) | the verse has no aligned content lexeme, or the edition has no text there (e.g. a non-anchor verse of a pooled translation range) |

### Contiguity is a confidence signal — and it is already in the data

A span's punctuation carries information beyond its extent. Measured against Clear-Bible gold with span
length held constant at 2 tokens, so that contiguity is the *only* variable:

| | span-2 contiguous | span-2 scattered | difference |
|---|---|---|---|
| English (BSB) | 84.5% | 62.3% | **+22.2pt** |
| Hindi (IRVHin) | 82.5% | 64.0% | **+18.5pt** |
| French (LSG) | 41.0% | 37.8% | +3.2pt |
| Russian (Synodal) | 26.0% | 26.0% | +0.0pt |

**A scattered (`,`) span is a materially weaker claim than a contiguous (`-`) one** — roughly 20 points
of token precision in languages whose reference gold can judge multi-word spans. Treat `4,6` as lower
confidence than `4-5`, and filter on it if your use needs precision over coverage. This costs nothing to
use: it is already encoded in every published partition.

Two honest caveats. Russian shows **no** contiguity effect, and it is the one language here that
Grambank codes `GB026=1` — adnominal material may occur discontinuously — so in some languages a
scattered span is simply how the language renders the phrase, not a defect. And French's gold averages
1.07 attested surfaces per source word, which caps any multi-word span by arithmetic, so its +3.2pt is
not comparable to the others.

Scattered spans are ~2-4% of spans in partitions published before 2026-09-01. From that date the
aligner keeps only the longest contiguous run of a source token's targets by default, so newer
partitions carry very few; the signal remains meaningful for everything already published.

Target token positions are addressed by **position in that verse's own tokenized text** — a consumer
tokenizes the edition's text the SAME way alignment did to resolve a position back to a word, or target
positions silently point at the wrong words. This is NOT plain whitespace/punctuation splitting — the
exact rule (`usj_source.tokenize()`) is: **a token is a maximal run of Unicode letters + combining
marks** (categories `L`/`M`). Everything else — punctuation, whitespace, AND digits (`Nd`, e.g. `40`,
`3`) — is a separator, producing NO token at all, not even a placeholder. **Numerals in the source text
are the sharpest gotcha**: a naive re-tokenizer that treats `"Selama 40 hari"` as 3 tokens (`Selama`,
`40`, `hari`) will be off-by-one from every position onward, compounding for every subsequent number in
the verse — this alone can look exactly like a systematic alignment bug when it's actually a
tokenization mismatch (verified against real client feedback on `ACT 1:3`/`ind_ags`: 8 apparent
"off-by-one" mismatches collapsed to 2 genuine ones once decoded with the correct tokenizer rule).

**Reference implementation, both languages** (verified byte-for-byte identical to `usj_source.tokenize()`
on the real `ind_ags` `ACT 1:3` text above):

**Python:**
```python
import unicodedata

def tokenize(text: str) -> list[str]:
    toks, cur = [], []
    for ch in unicodedata.normalize("NFC", text):
        if unicodedata.combining(ch):          # drop non-spacing combining marks (Hebrew niqqud,
            continue                           # Arabic harakat, ...) before the letter/mark test below
        if unicodedata.category(ch)[0] in ("L", "M"):
            cur.append(ch)
        elif cur:
            toks.append("".join(cur))
            cur = []
    if cur:
        toks.append("".join(cur))
    return toks
```

**JavaScript** (covers Latin/Cyrillic/Greek-script targets, which is the large majority — see the
caveat below for diacritic-heavy scripts):
```javascript
function tokenize(text) {
    const normalized = text.normalize("NFC");
    return normalized.match(/[\p{L}\p{M}]+/gu) || [];   // \p{L}=letter, \p{M}=mark (Unicode property escapes)
}
```

**Honest caveat on the JS version**: JavaScript has no built-in equivalent to Python's
`unicodedata.combining()` (canonical combining class), so the snippet above doesn't strip non-spacing
marks (Mn) the way the Python one does — it's exactly right for scripts without those (Latin, Cyrillic,
Greek — covers most target languages, including the `ind_ags` case here), but for a diacritic-heavy
script (Hebrew niqqud, Arabic harakat, Devanagari) it may tokenize slightly differently than
`usj_source.tokenize()`. Rather than ship a JS mark-stripping table that could itself be subtly wrong,
if you're decoding one of those scripts, treat `usj_source.py`'s Python implementation as the
authoritative reference.

### Worked example — the scattered (comma) case

Real output, `RUT 1:11`, English (BSB):

```
"0:2 1:1 2:3 3:4,6 4:10 5:16 6:19 8:23"
```

Verse text: *"But Naomi replied, "Return home, my daughters. Why would you go with me?..."*

| ordinal | Hebrew | Strong's | span | decoded |
|---|---|---|---|---|
| 0 | תֹּ֤אמֶר | H0559 | `2` | replied |
| 1 | נָעֳמִי֙ | H5281 | `1` | Naomi |
| 2 | שֹׁ֣בְנָה | H7725 | `3` | Return |
| **3** | **בְנֹתַ֔** | **H1323** | **`4,6`** | **home, daughters** (scattered — token 5, "my", is skipped in between) |
| 4 | תֵלַ֖כְנָה | H1980 | `10` | go |

This is genuine eflomal output, not a contrived case: the aligner linked "daughters" to two
non-adjacent English words. The comma form exists precisely to represent real cases like this — a
contiguous `"a-b"` range would be wrong here (it would also claim token 5, "my", which this Hebrew word
did not align to).

## The content hash — reproducible by ANY client, in any text format

**Alignment is extremely sensitive to any wording difference in a revised Bible book.** If a publisher
fixes a typo or re-versifies a chapter, an alignment computed against the OLD text is silently wrong
against the new one. The hash in the filename turns that into a fail-closed lookup: a client hashes its
OWN copy of the edition's book text and looks for a matching filename — no match means no stale
alignment can be served by accident.

**Algorithm** (`book_content_hash` in `compact_align.py` — reimplementable in any language):

1. Extract every verse's **translatable text only** (no footnotes, headings, or titles — the same rule
   alignment itself follows).
2. For each `(chapter, verse)` pair, ascending, form the string `"{chapter}:{verse}:{text}"`.
3. Join all of a book's verse-strings with `"\n"`.
4. UTF-8 encode.
5. SHA-256, hex digest.
6. The **filename** uses the last 5 hex characters (a client wanting stronger collision resistance can
   still recompute and compare the full 64-char digest — only the filename is truncated).

Including `chapter:verse` in the hashed string (not just the bare words) means a re-versification
(a verse split, merge, or renumbering) changes the hash too, even when every word stayed the same — a
re-versified book needs a fresh alignment exactly as much as a reworded one does.

This is deliberately based on the extracted **words**, never the container format (USJ/USFM/plain-text
JSON bytes) — verified: re-serializing identical content with different JSON formatting keeps the hash
unchanged; changing a single word anywhere in the book changes it.

## Layout — why the bulk data isn't in git

```
compact-alignments/
  README.md              # committed — this file
  manifest.json           # committed — per-language/edition metadata (the durable record)
  _index/                 # committed — small (~tens of KB total), generated once, never edition-specific
    RUT_lexemes.json
    GEN_lexemes.json
    ...
  e/eng/eng_BSB/          # GIT-IGNORED — bulk data, published out-of-band (HF / object storage)
    RUT_101a1.json
    ...
  a/arb/arb_vdv/
    ...
```

## Provenance

`manifest.json` lists every published `(iso, edition)` with its `tag` (the internal alignment id),
`books` (which ones are published), and a `source` pointer (`provider`/`edition`/`license_url`) — same
convention as `lexeme-alignments`. Alignment source is the additive union of eflomal + gloss + gapfill
per-verse output (`align_files.tag_files`, exact-tag matched).

## Reproducibility

Same content-addressed model as `lexeme-alignments`: eflomal is non-deterministic (seeds from
`/dev/urandom`), so a regenerated edition's spans can drift ~1% run-to-run. There's no single dataset-wide
hash to pin against here — the book-content hash in each filename pins the **source text**, not the
alignment output; two regenerations of the same edition can legitimately publish under the same filename
with slightly different span content.

## Publishing (one-time per edition)

```bash
python3 -c "from huggingface_hub import login; login()"
python3 -m lexeme_aligner.compact_align --iso bsb --publish-iso eng --usj-dir data/usj-eng \
    --publish compact-alignments
```

## The function-word index — `_index/<BOOK>_fn.json` (since 2026-10)

```json
{"RUT 1:1": ["", "5921", "...", ...], ...}
```

Same shape and key order as `_index/<BOOK>_lexemes.json`, but it lists the source tokens of each verse that have **no**
`srcOrd` slot: articles, prepositions, conjunctions, suffix pronouns, particles, in source order. The position in a verse's
list is the token's `fnOrd`. A token the source gives no lexeme is an empty string. Lexemes carry no `hbo:`/`grc:` prefix,
as in `_lexemes.json`. Nothing in the alignment arrays refers to `fnOrd`; the index is the source side for layers that
align function words (the full-alignment format uses it). It is stamped in `_index/_source.json` (`fn_sha256` per book) and
checked at publish, like the content index.

## Source token keys — `_index/<BOOK>_keys.json` and `_index/<BOOK>_fn_keys.json` (since 2026-10)

```json
{"RUT 1:1": ["080010010022", "080010010031", ..., "080010010102+080010010111", ...], ...}
```

Position-parallel to `_index/<BOOK>_lexemes.json` (`_keys.json`) and `_index/<BOOK>_fn.json` (`_fn_keys.json`): entry `k` of a verse
is the MACULA node key of the token that `srcOrd` `k` (or `fnOrd` `k`) denotes. A Hebrew key has 12 digits, `BBCCCVVVWWWM` (book,
chapter, verse, word, morpheme); a Greek key has 11 digits, one per word. A token that holds two source nodes (the Hebrew
בֵּית לֶחֶם, one lexeme) lists both joined by `+`; a token without a key is `""`. With these files a client joins any per-entry
channel (the alignment arrays, `conf`, `rend`) to its own MACULA data by key, without re-deriving the index. The keys refer to the
spine pinned in `_index/_source.json` (`spine_sha256`, built from Clear-Bible/macula-hebrew `WLC` and macula-greek `Nestle1904`,
branch `main`; MACULA publishes no numbered release). They are stamped there as `keys_sha256` / `fn_keys_sha256` per book and
checked at publish, like the other index files.

## Full alignments (since 2026-10)

The compact array keeps one winning link per CONTENT source token. A **full alignment** accounts for every word: function
words, losing alternatives, words the translator supplied, source words with no target word of their own. It is published in the
same container format, in three places:

- the **statistical** full alignment of an edition: extra keys (`fn`, `wp`, `fp`, `wx`, `rows`, `off`) in that edition's
  `.meta.json` files in [compact-alignments-meta](https://huggingface.co/datasets/bcv-commons/compact-alignments-meta), with the
  profile table in `<edition>/_layer.json` there. The main array here is not changed by it.
- **hand-made (gold)** layers in [full-alignments-manual](https://huggingface.co/datasets/bcv-commons/full-alignments-manual)
  (Clear, HELFI, ChiUns, BSB tables) and [full-alignments-manual-sa](https://huggingface.co/datasets/bcv-commons/full-alignments-manual-sa)
  (Door43, CC BY-SA), layer id `<edition>+manual+<source>`, same relative paths and `_index/` as here.

For now only editions with a hand-made layer carry the statistical full alignment. The file format is described in the
full-alignments-manual card. Function-word sources are listed in `_index/<BOOK>_fn.json` (below).

## Edition-side channel `rend` (meta repo, since 2026-10)

`<BOOK>_<hash>.meta.json` in [`compact-alignments-meta`](https://huggingface.co/datasets/bcv-commons/compact-alignments-meta)
has a `rend` array, position-parallel to the book index like the other channels. Entry `i` is ONE string of integers
separated by spaces, one per `srcOrd:span` entry of verse `i` in the main array, in the same order.

- **id** = the n-th *distinct rendering* of that entry's lexeme in this edition, numbered by first appearance in canonical
  order (books, verses, entries in order).
- **rendering** = the aligned raw target words, case-folded, with the language's published `target-stopwords` removed (exact
  match; if every word is a stopword the original words are kept), joined by one space.
- The manifest entry's `rend_stopwords` is the sha256 of the list used (`null` = nothing dropped).
- Ids are comparable only within one edition and one lexeme, and are only valid with the main array of the **same file**: they
  are renumbered on every rebuild. They carry no text. Use them to split a lexeme's occurrences by how this translation
  renders them.

### Scope of `rend`, and taking an edition down

- By default every aligned entry of an edition carries an id. The record `config/takedown.json` (in the
  [lexeme-aligner repository](https://github.com/bcv-commons/lexeme-aligner), so the history is auditable) can narrow that for one
  edition: `"rend_above_eflomal"` writes an id only for entries the statistical aligner (eflomal) produced and `0` ("withheld") for
  every entry another stage added (gloss, gap-fill, span extension); `"rend"` omits the channel for that edition. A withheld entry
  takes no id, so the numbering does not hint at it. An edition under either rule shows `rend_scope` in its manifest entry
  (`"eflomal"` or `"none"`); no `rend_scope` means every entry carries an id.
- **`rend` belongs to the compact alignments only.** It is not published for any full-alignment file (the full-alignment layers,
  such as `<edition>+manual+<source>`, which place every source and target word). If it is ever offered for those, it will be an
  opt-in per edition, announced here first. Likewise, if alignments much closer to a complete alignment are added on top of the
  compact ones, `rend` is not published for the added entries.
- **Taking an edition down.** A rights holder or a licence change can withdraw an edition: `"withdraw": "edition"` removes its
  files from this repo and the `-meta` and `-extra` repos and its manifest entry, and the pool builder no longer offers it, so a
  rebuild cannot bring it back; rows of the pooled datasets leave by `base_text`. To ask for this, open an issue on the
  lexeme-aligner repository naming the edition and the grounds; the decision, reason and date are recorded in
  `config/takedown.json`.

## Verse numbering — spine verses and the verse map (since 2026-10)

Every array here is keyed by **spine verse**: the verse numbering of the source text (Hebrew/WLC in the Old Testament,
Nestle 1904 in the New). Many editions number some verses differently (Psalm superscriptions, 1 Chronicles 6, Joel, Malachi,
Daniel, ...). Each edition's manifest entry carries `versification`, a bcv-commons/bibles scheme code (`eng`, `org`, `orgw`,
`catm`, `lxx`, `vul`, `rso`), and `_index/_versification_<scheme>.json` lists every spine verse whose *edition* verse differs,
as the edition's reference (identity everywhere not listed). `C:title` means the scheme leaves that verse unnumbered (for
example an English Psalm superscription), so the verse has no target words.

**Read a span's target words from the mapped edition verse**, not from the verse with the same number: spine `PSA 3:2` is
English `PSA 3:1`. A client that ignores the map gets correct results for every verse the scheme leaves unchanged and wrong
words for the rest.

## Verse mapping

Hebrew and English Bibles number some Old Testament verses differently (Psalm superscriptions, 1 Chronicles 6,
Joel, Malachi, Daniel 4 and 6, ...). Source and target verses are paired with the verse-mapping tables of **TVTMS**
(Translators Versification Traditions with Methodology for Standardisation), part of STEPBible Data by Tyndale
House, Cambridge — **CC BY 4.0**, https://github.com/STEPBible/STEPBible-Data — with each edition's numbering
detected from its own text (cross-checked against bcv-commons/bibles). Source verse references follow the Hebrew
(WLC) numbering of the MACULA source text.

## License

**CC0-1.0.** As noted above, no Bible text is stored anywhere in this dataset — only integer token
positions and spine verse references. There is nothing here that reproduces a source translation's
copyrightable expression. The edition's own text (needed to decode a compact string into words) keeps
its own license — see `lexeme-alignments/manifest.json`'s `sources` pointers, or this dataset's own
`manifest.json`, for the authoritative terms per edition.
