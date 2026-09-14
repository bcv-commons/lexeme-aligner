# Generating your own compact-alignment data for a new draft (existing language)

**Audience:** a partner (translation team, tooling vendor) who has direct access to this repo and
its private inputs (see §1), has drafted some or all of a Bible translation in a language **that
already has published data in `bcv-commons/lexeme-alignments`**, and wants token-level alignment
output in the same [compact-alignment format](compact-alignments.md) — for their own local use, not
for publishing into our shared datasets.

**Not this doc:** onboarding a genuinely new language (see `onboard.py`/the Makefile's
`new-language` target instead — that path also handles catalog discovery, which you don't need
here since your text isn't catalog-sourced), or contributing your draft back into the public
`bcv-commons/compact-alignments` dataset (talk to us first — see §6).

## 0. Why "existing language" matters

Gloss alignment — the higher-precision of the two methods this pipeline runs — bootstraps its
priors from `publish/lexeme-alignments/iso=<your-lang>/data.parquet`, the aggregated union of
every **other already-published edition** of that language. A brand-new language has none of
that and starts cold; an existing language's new draft benefits immediately, even if the draft is
just a chapter or two. Check your language is there before starting:

```bash
python3 -c "
import json
langs = json.load(open('publish/lexeme-alignments/manifest.json'))['languages']
print('swk' in langs)   # replace with your ISO
"
```

## 1. Prerequisites

- This repo, cloned, with `pip install -e '.[ingest,publish]'` (`ingest` gets `usfmtc` for
  USFM→USJ conversion; `publish` gets `pyarrow`, needed to *read* the existing priors parquet —
  you are not publishing anything, but gloss's bootstrap step still reads that file locally).
  `eflomal` itself is a core dependency and builds via plain pip on most platforms; see the
  README for the one Apple-clang caveat on macOS.
- **`pipeline/lexeme-spine.db`** (the MACULA lexeme-anchored Hebrew/Greek backbone) in place, or
  `ALIGNER_SPINE_DB` pointing at it. **This is the one real blocker for a fully self-serve setup:
  it is not currently published anywhere a third party can fetch it themselves** — `config/
  PROVENANCE.txt` records its provenance and pin, but the file itself has only ever moved between
  us and shoresh directly. If you're reading this without already having it, ask us for a copy
  rather than trying to source it independently — this is worth flagging to whoever owns this
  decision (see the note at the end of this doc).
- Your drafted text, in USFM (the common case) or already in USJ.
- The **canonical ordinal index**, `config/canonical_index/whole_bible.json` — already in this
  repo, checked in, nothing to build.

## 2. Convert your draft to USJ (skip if you already have USJ)

There's no dedicated CLI for "just convert a folder of USFM" — every existing adapter in this repo
bundles conversion with a specific fetch source you don't need. It's three lines with `usfmtc`
directly, the same call every adapter here already makes internally:

```python
import usfmtc
from pathlib import Path
from lexeme_aligner.run_pilot import _BOOK_FILE_NUM   # canonical book-number map

usfm_dir = Path("my_draft/usfm")     # one .usfm file per book, named e.g. TIT.usfm
usj_dir = Path("my_draft/usj")
usj_dir.mkdir(parents=True, exist_ok=True)

for usfm_path in usfm_dir.glob("*.usfm"):
    book = usfm_path.stem.upper()
    nn = _BOOK_FILE_NUM[book]
    usfmtc.readFile(str(usfm_path)).outUsj(str(usj_dir / f"{nn}-{book}.json"))
```

## 3. Pick a tag, and keep your output OUT of this repo's own publish tree

You need two identifiers:

- **`--iso`** — a tag naming *this specific draft*, e.g. `swk_draft2026`. It's not written to any
  shared config (pins are only written by the catalog-fetch adapters, which you're not using) —
  pick anything that won't be confused with a real edition tag.
- **`--publish-iso`** — the real, existing ISO code (e.g. `swk`) whose published priors you want
  gloss to bootstrap from.

**Point every output path at a directory of your own, never at `publish/compact-alignments`.**
That tree is this repo's live, HF-published dataset — writing into it risks your draft being swept
into our next `publish-all` by accident. Two flags control this on the final step (§4d); pass both,
every time:

```
--publish  my_draft/out/compact-alignments        # NOT publish/compact-alignments
--index-root my_draft/out/compact-alignments/_index
```

(`--index-root` doesn't inherit from `--publish` — it has its own default, `publish/
compact-alignments/_index`, so it must be overridden explicitly or your first run will write into
our real tree even though `--publish` was pointed elsewhere. This is the one sharp edge in the
CLI; everything else defaults safely.)

## 4. The alignment chain

Four steps, run in order, all against your own `--usj-dir`. Scope flag (`--ot`/`--nt`/`--all`) or
repeated `--book BOOK` should match whatever you've actually drafted — align what you have, not
the whole testament if you don't need to.

```bash
USJ=my_draft/usj
TAG=swk_draft2026
ISO=swk          # the existing language
SCOPE=--nt        # or --ot / --all / --book TIT --book PHM ...

# (a) statistical pass, scratch — writes LOCAL out/align_eflomal_$TAG_*.jsonl only
python3 -m lexeme_aligner.run_pilot --method eflomal $SCOPE --usj-dir "$USJ" --iso "$TAG"

# (b) gloss pass — bootstraps from the EXISTING iso=$ISO/data.parquet, read-only
python3 -m lexeme_aligner.run_pilot --method gloss $SCOPE --usj-dir "$USJ" --iso "$TAG" \
    --publish-iso "$ISO"

# (c) gap-fill — covers what neither (a) nor (b) reached
python3 -m lexeme_aligner.gapfill --iso "$TAG" --publish-iso "$ISO" --usj-dir "$USJ" $SCOPE \
    --methods eflomal,gloss

# (d) compact-alignment output, isolated to your own directory (see §3)
python3 -m lexeme_aligner.compact_align --iso "$TAG" --publish-iso "$ISO" --usj-dir "$USJ" \
    --methods eflomal,gloss,gapfill \
    --publish my_draft/out/compact-alignments \
    --index-root my_draft/out/compact-alignments/_index
```

That's the whole chain. **Do not run `export_lex`** — it's what aggregates an edition into the
shared `lexeme-alignments` dataset, and you don't need or want that for a private draft; skipping
it means nothing you do here can touch a file this repo already publishes. An optional fifth step,
`residual_align` (same flags as gapfill), adds the opt-in `.extra.json` residual layer described in
`compact-alignments.md` if you want it — safe to skip.

Output lands at `my_draft/out/compact-alignments/<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json`,
exactly the published format (position-parallel to `_index/<BOOK>_lexemes.json`, one `.meta.json`
sidecar per book carrying method/confidence/contested-position provenance). `<edition>` defaults
to your `--iso` tag unless `config/sources.json` has an entry for it (it won't, for a private tag)
— pass `--edition` explicitly if you want a nicer name in the path.

## 5. Decoding

Identical to the published format — see `compact-alignments.md`'s "Decoding" section verbatim,
substituting your own output paths. The content-lexeme index (`_index/<BOOK>_lexemes.json`) it
lazily wrote under your `--index-root` is edition-independent and safe to reuse across every draft
tag for the same book.

## 6. If you want this published for real

This whole recipe deliberately keeps your draft invisible to `publish/compact-alignments` and any
future `publish-all`. If the draft later becomes a real, ready-for-others edition of the language,
don't just move your output tree over — talk to us. A real publish needs a `config/sources.json`
entry (license, edition label) and a place in `config/language_editions.json` so the catalog-driven
sweep can find and maintain it going forward, rather than it becoming another orphaned, hand-run
edition of the kind this repo just spent real effort cleaning up (`internal-docs/
published-language-counts.md`).
