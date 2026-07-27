# Compact per-verse alignments — the token-level companion to `publish/lexeme-alignments`

**What this is not:** a replacement for `publish/lexeme-alignments`. That dataset is *type-level* — aggregated
across every verse and every pooled edition ("across the whole Bible, what words render this lexeme, how
often, how confidently"), and it's what the internal recipes (R1 keyness, gloss bootstrap,
`cross_lang_prior`, `publish/senses_attested`) and downstream consumers (bcv-query) already depend on. Aggregation
is lossy in exactly one dimension: **position** — which occurrence, in which verse, aligned to what.
That's what this format restores. Neither format substitutes for the other; you cannot derive one from
the other in either direction (aggregation throws away position; position alone can't tell you a
lexeme's dominant cross-corpus rendering without re-aggregating the whole thing yourself).

**Who this is for:** a reader who wants "show me verse X aligned, for translation Y" — a small,
self-contained download, no pipeline to run. Contributors/researchers who want to *generate* or *verify*
it should read on; this doc is the how-to for that side.

## Two files, not one

### 1. The canonical ordinal index — shared, published ONCE

`config/canonical_index/whole_bible.json` — a flat JSON array, `index[i] == "BOOK C:V"`, in raw spine
verse order (`OT_BOOKS` then `NT_BOOKS`, per `run_pilot.py`), covering **every** spine verse — including
ones with zero content lexemes — so the index never changes shape and any language's array is always the
same length, same meaning at each position, regardless of that language's own book coverage or
versification.

```bash
python3 -m lexeme_aligner.compact_align --build-index --out config/canonical_index/whole_bible.json
```

31,156 entries, ~375 KiB. Rebuild only if the underlying spine's verse structure itself changes (a
deliberate spine re-pin) — not per language, not per publish.

**No reverse index (ref → ordinal) is published.** It's one line to derive client-side
(`{ref: i for i, ref in enumerate(index)}`) and publishing it would just duplicate every `"BOOK C:V"`
string a second time for free.

### 2. Per-language compact array — position-parallel to the index

```bash
python3 -m lexeme_aligner.compact_align --iso ind --usj-dir pipeline/work/ingest-cache/usj-ind \
    --index config/canonical_index/whole_bible.json --out pipeline/work/out/compact_ind.json
```

Output: a plain JSON array, same length as the index, `array[i]` = compact string for `index[i]`'s verse.
Alignment source is the additive union of `eflomal + gloss + gapfill` (override with `--methods`) —
whatever's actually on disk under `--out-dir` (default `pipeline/work/out/`), matched by **exact tag**
(`align_files.tag_files`, not a raw glob — see the sibling-tag bug this session fixed).

## The string format

```
"0:1 1:3,5 2:4 3:6,7,8,10 4:12"
```

| token | meaning |
|---|---|
| space | separates entries — one per aligned **content**-lexeme ordinal |
| `srcOrd:targetSpan` | `srcOrd` = 0-based index among that verse's content lexemes ONLY, in spine order (function words never get a slot) |
| `-` inside a span | a **contiguous** range of target-token indices, inclusive (`3-4` = tokens 3 and 4) |
| `,` inside a span | a **scattered**, non-contiguous list of target-token indices — real eflomal output occasionally does this |
| single int | one target token |
| *(ordinal absent)* | that content lexeme is **unaligned** — no null placeholder, the gap costs nothing |
| `""` (empty string) | the verse has no aligned content lexeme, or no target text at all |

Target tokens are addressed by **position in that verse's own tokenized text** — never by repeating the
word — so a consumer needs the EXACT SAME tokenization `usj_source.tokenize()` used, or positions
silently resolve to the wrong words. NOT plain whitespace/punctuation splitting: a token is a maximal
run of Unicode letters + combining marks (categories `L`/`M`); punctuation, whitespace, AND digits
(`Nd`) are all separators and produce no token — a bare number like `40` is invisible to this tokenizer,
not its own token. Live case (client feedback, `ACT 1:3`/`ind_ags`): a naive re-tokenizer that counts
`"40"` as a token is off-by-one from that point on, compounding for every later number in the verse —
8 apparent mismatches collapsed to 2 genuine ones once re-decoded with the correct rule.

**Range-pooled verses** (PKF-style `"3-4"` target verse markers — see `run_pilot.pooled_verse_groups()`):
the ANCHOR verse's string covers every content lexeme across the WHOLE pooled range (ordinals span
multiple original verses); non-anchor member verses are `""` — their translation lives in the anchor's
combined block, not a real gap.

**Psalm superscriptions** are automatically excluded from `srcOrd` numbering — `HebToken.is_superscription`
forces `is_content=False`, so e.g. PSA 23:1's "a psalm of David" never competes for a target-word slot
against the verse's real content (see `hebrew_source.py`; live spine flag as of the 2026-07-24 shoresh
update, heuristic fallback for older spine snapshots).

## Decoding — minimal example

```python
import json
from pathlib import Path
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.run_pilot import pooled_verse_groups, _BOOK_FILE_NUM
from lexeme_aligner.usj_source import read_verse_ranges, tokenize
from lexeme_aligner.versification import remapper

lex_seq = json.loads(Path("publish/compact-alignments/_index/RUT_lexemes.json").read_text())  # published once, shared
index = list(lex_seq.keys())     # the ref list IS this file's own key order — no separate index file
array = json.loads(Path("publish/compact-alignments/e/eng/eng_BSB/RUT_101a1.json").read_text())  # this edition's own file
by_ref = dict(zip(index, array))

def _decode_span(span: str) -> list[int]:
    if "-" in span:
        lo, hi = map(int, span.split("-"))
        return list(range(lo, hi + 1))
    if "," in span:
        return [int(x) for x in span.split(",")]
    return [int(span)]

def decode(book, ch, v, iso="ind", usj_dir=Path("pipeline/work/ingest-cache/usj-ind")):
    """Yields (HebToken, target_words_or_None) for every content lexeme in the verse."""
    compact = by_ref[f"{book} {ch}:{v}"]
    ranges = read_verse_ranges(usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json")
    remap = remapper(iso, str(usj_dir))
    heb = HebrewSource()
    for anchor_v, vs, ve, text, members in pooled_verse_groups(book, ch, heb, ranges, remap):
        if anchor_v != v:
            continue
        tokens = tokenize(text)
        content = [t for orig_v, t in members if orig_v == vs and t.strong and t.is_content]
        pairs = {int(ordv): span for ordv, span in
                (part.split(":") for part in compact.split())}
        for ordinal, tok in enumerate(content):
            span = pairs.get(ordinal)
            if span is None:
                yield tok, None
            else:
                yield tok, " ".join(tokens[i] for i in _decode_span(span))
```

The `decode()` above assumes you have this pipeline's own spine (`HebrewSource`) installed. An external
client with only the PUBLISHED files doesn't need that at all — `_index/<BOOK>_lexemes.json` already
resolves `srcOrd` to a lexeme id directly, no `is_content` re-derivation required:

```python
lexemes = json.loads(Path("publish/compact-alignments/_index/RUT_lexemes.json").read_text())["RUT 1:1"]
for part in by_ref["RUT 1:1"].split():
    ordinal, span = part.split(":")
    print(lexemes[int(ordinal)], "->", _decode_span(span))   # e.g. "0802" -> [24]  (his wife)
```

## Size

For `ind` (Indonesian, whole Bible, single edition):

| form | bytes |
|---|---|
| raw per-verse jsonl (eflomal+gloss+gapfill, GEN only, for comparison) | 8,873,913 |
| compact JSON, whole Bible | 1,414,464 |
| compact JSON, gzip -9 | 361,261 (**3.9x** smaller than raw compact) |
| compact JSON as parquet (zstd) | 497,402 (**worse** than plain gzip) |

Parquet loses here because its strength — dictionary/RLE encoding of highly *repeated* categorical
values — doesn't apply: each verse's compact string is close to unique. That's the opposite regime from
`publish/lexeme-alignments`, where the same lexeme/surface/base_text values repeat thousands of times and parquet
wins decisively. Ship the plain `.json`; a `.json.gz` sibling is a cheap, worthwhile addition if bandwidth
matters (many CDNs also gzip-transport plain JSON automatically, so check before assuming a dedicated
`.gz` artifact is needed).

## Publish layout — per-book, content-addressed (the decided HF path)

The whole-bible array above is a dev/debug convenience (`--out`). The **published** artifact is
per-book, per-edition, with the filename itself carrying a content hash of that book's text — so a
client can verify (and locate) the RIGHT file without downloading anything first:

```
<iso[0]>/<iso>/<edition>/<BOOK>_<last-5-hex-of-book-content-hash>.json
```

e.g. `e/eng/eng_BSB/RUT_101a1.json` — `iso` is the TRUE published language code (`eng`), never the
internal alignment tag (`bsb`, which is edition-specific — see `onboard.py`'s tag-vs-iso split). The
`<edition>` segment (`eng_BSB`) is derived from `config/sources.json`'s `source.edition` field — the SAME
identifier already published as `base_text` in `publish/lexeme-alignments` — iso-prefixed unless it already
carries one (a helloAO-sourced tag like `arb_vdv` already does, giving `a/arb/arb_vdv/...` with no
double prefix; see `edition_id()` in `compact_align.py`).

```bash
python3 -m lexeme_aligner.compact_align --iso bsb --publish-iso eng --usj-dir pipeline/work/ingest-cache/usj-eng \
    --publish compact-alignments --methods eflomal,gloss,gapfill
```

Each per-edition file is a **plain array** of compact strings — `["...", "...", ...]` — position-parallel
to `publish/compact-alignments/_index/<BOOK>_lexemes.json`'s own (ordered) keys — `{"BOOK C:V": [lexeme, ...],
...}`, the content-lexeme sequence `srcOrd` indexes into, built by `build_source_lexemes()` directly from
the raw spine (edition-independent, "publish once" like the old whole-bible index, just scoped per book
— written lazily by `publish_compact()`, only if missing). There is deliberately NO separate flat
`["BOOK C:V", ...]` ref-index file — it would be a pure, lossless subset of `_lexemes.json`'s own key
order (verified byte-identical), so publishing both would just duplicate every ref string a second time
for free; a client derives the ref list as `list(lexemes.keys())` (Python) / `Object.keys(lexemes)`
(JS — key order is spec-guaranteed since ES2015) in one line instead. This also means a client resolves
`srcOrd` to an actual lexeme via array lookup, without re-deriving `is_content` from their own morphology
data (live motivating case: `H0853`, Hebrew's direct-object marker, shares its bare lexeme id with a
rarer noun homonym — nothing in lexeme/Strong's alone distinguishes them; `is_content` here comes from
MACULA's per-occurrence `class` tag, not from the lexeme, so we resolve and publish it rather than
asking every client to replicate that join themselves):

```
_index/RUT_lexemes.json               {"RUT 1:1": ["1961", "3117", ...]}     <- published once
e/eng/eng_BSB/RUT_101a1.json          ["0:1 1:2 ...", "0:3 1:1 ...", "0:5 1:4 ...", ...]
a/arb/arb_vdv/RUT_dd972.json          ["0:0 1:2 ...", "0:2 1:3 ...", "0:4 1:0 ...", ...]
```

### The content hash — why, and the exact algorithm

**Alignment is extremely sensitive to any word difference in a revised Bible book** — if a publisher
fixes a typo or re-versifies a chapter, an alignment computed against the OLD text is silently wrong
against the new one. Baking a content hash into the filename turns that into a fail-closed lookup: a
client hashes ITS OWN copy of the book's text and looks for a matching filename. No match → no stale
alignment gets served by accident.

The hash (`book_content_hash` in `compact_align.py`) is computed over the **translatable words**, not
the JSON container, so it's reproducible from ANY source format (USJ, USFM, a plain verse dump) and
doesn't spuriously change on irrelevant re-serialization (verified: re-saving the identical USJ with
different formatting keeps the hash identical; changing one word anywhere in the book changes it):

1. Extract each verse's translatable text exactly as the aligner does (`usj_source.read_verse_ranges` —
   footnotes/headings/titles excluded, same rule as alignment itself).
2. For each `(chapter, verse)` in ascending order, form the string `"{chapter}:{verse}:{text}"`.
3. Join all of a book's verse-strings with `"\n"`.
4. UTF-8 encode, SHA-256, hex digest.
5. The filename uses the **last 5 hex characters** (a client wanting more collision resistance can
   still recompute and check the full digest — only the filename is truncated).

Including `chapter:verse` in the hashed string (not just the bare text) means a re-versification
(verse split/merge, renumbering) changes the hash too, even if every word stayed the same — a
re-versified book needs a fresh alignment just as much as a reworded one does.

## Multi-edition languages

Alignment is edition-specific — a pooled language (e.g. `ind` = 6 editions) needs one compact array
**per edition** (per tag, not per iso):

```bash
for tag in ind indala indshv indtsi ind_ags ind_ayt; do
  python3 -m lexeme_aligner.compact_align --iso "$tag" --usj-dir "pipeline/work/ingest-cache/usj-$tag" \
      --index config/canonical_index/whole_bible.json --out "pipeline/work/out/compact_${tag}.json"
done
```

The published `publish/lexeme-alignments/iso=ind/data.parquet` already bundles all 6 editions into one
aggregated file; the compact equivalent stays six separate per-edition files by design (a reader who
wants "PKF specifically" shouldn't have to download the other five editions' worth of data to get it).
