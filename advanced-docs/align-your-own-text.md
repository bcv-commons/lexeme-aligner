# Align your own text

You have a Bible translation (a draft, a book or two, or a whole Bible) in some language, and you want it word-aligned to the
Hebrew and Greek, in the same format as our published [compact alignments](../docs/compact-alignments.md). You don't need any
of our private files, and nothing you run is sent anywhere. The one exception is the optional LLM check at the end, which you
pay for with your own key.

## 1. Install

You need Python 3.10 or newer and a C compiler, because `eflomal` (the statistical aligner) builds from source.

```bash
git clone https://github.com/bcv-commons/lexeme-aligner.git
cd lexeme-aligner
python3 -m venv .venv
.venv/bin/pip install -e '.[publish]'
```

Tested on Linux with Python 3.12 and 3.14 (`pipeline/scripts/clean_install_test.sh`). On macOS with Apple clang, `eflomal` may need
`brew install libomp` first.

## 2. Fetch the inputs (once per language)

```bash
.venv/bin/lexeme-aligner fetch-inputs --iso xyz
```

`xyz` is the ISO 639-3 code of your language. This downloads about 130 MB into one data folder:
`~/.local/share/lexeme-aligner`, or the folder you give with `--data DIR` or `$ALIGNER_DATA`.

What it downloads:
- the Hebrew/Greek word spine, from bcv-query's fixed release;
- [bcv-commons/word-parts](https://huggingface.co/datasets/bcv-commons/word-parts) v1.1 (every Greek and Hebrew word cut into
  its parts), from which it then BUILDS the parts spine in the data folder (about 30 s; its content is pinned, so a build that came out
  different is refused);
- the prior pack;
- whatever grammar facts we have for your language;
- our published alignments of your language, all editions pooled
  ([bcv-commons/lexeme-alignments](https://huggingface.co/datasets/bcv-commons/lexeme-alignments), one file per language, a few MB),
  if we have the language.

Every file is checked against a pinned sha256 or comes from a pinned dataset revision, so a file that changed upstream is refused
or never fetched. The folder's `inputs.json` records
what came from where, under which licence. A language we have no facts for still works, because the facts are only hints.
Run it again with `--iso` for each further language.

## 3. Your text

`--text` takes a folder or a single file in any of these formats:

| Format | What it looks like |
|---|---|
| Plain text | one verse per line: `RUT 1:1 In the days when the judges judged, ...`. A file named after its book (`RUT.txt`, `08-RUT.txt`) may leave out the book code: `1:1 In the days ...`. Use `1:2-3` for verses that are translated together. Empty lines and lines starting with `#` are skipped. |
| eBible corpus | `vref.txt` plus one text file with the same number of lines (the [eBible corpus](https://github.com/BibleNLP/ebible) layout). An empty line means no text, and `<range>` means the verse is part of the one before. |
| USFM | `.usfm` / `.sfm` files, one book per file. The book is read from the `\id` line. |
| USJ | `NN-BOOK.json` files in our own layout, for example `08-RUT.json` (see `tests/data/sample-usj-engweb`). |

Book codes are the standard USFM ones (`GEN` … `REV`). Books outside the 66 (deuterocanon, front matter) are skipped with a
note. Any format other than USJ is converted into `OUT/text-usj` first.

## 4. Align

```bash
.venv/bin/lexeme-aligner align-text --text my-draft/ --edition xyz_draft --iso xyz --lang-name "My language" --out ~/xyz-out
```

- `--edition` is any id you choose. It names the output files and folders.
- `--out` must be a new folder outside the repo. The run writes nowhere else: a write guard refuses anything outside it and
  logs it in `OUT/guard.log`.

A few books take a few minutes; a whole Bible takes about an hour.

What you get in `OUT`:

| Path | What it holds |
|---|---|
| `part-alignments/<i>/<iso>/<edition>/` | the alignment of every Hebrew/Greek word PART (stem, ending, prefix ...) to your words, one file per book in the [part-alignments format](../publish/part-alignments/README.md), with `.meta.json` sidecars, the part index `part-alignments/_index/`, a manifest, and `fold.py` / `read_verse.py` (and `.mjs`) to read it |
| `compact/<i>/<iso>/<edition>/` | the same alignment by whole words, one file per book in the [compact format](../docs/compact-alignments.md), with `.meta.json` sidecars and a manifest: the fold of `part-alignments` (`fold.py`) |
| `compact/_index/` | what the compact files are read with: the source-word index of your text's books (`<BOOK>_lexemes.json`, `_keys`, `_fn`, `_fn_keys`, stamped in `_source.json`) and the verse map (`_versification_<scheme>.json`). The same files as compact-alignments' own `_index/`, for your books only |
| `align_<method>_<edition>_<BOOK>.jsonl` | the links from each step: `eflomal`, `gloss`, `spanext`, `gapfill`, `residual`, by source part; `words/` holds the same by whole words |
| `target-stopwords/`, `target-morphology/` | function words and affixes learned from your text |
| `align-text-run.json` | the run record: package version, git commit, sha256 of your text and of every input and setting, and each step's command, exit code and time |
| `run.log` | the steps' output |

**Verse numbers in the output are the source text's, not yours.** Every output keys a verse by the Hebrew/Greek source's
numbering (Hebrew numbering in the Old Testament): the `book`/`chapter`/`verse` of the `.jsonl` records and the verses of the compact
files. Where your text is numbered differently (English numbering: Psalm headings, DEU 29, MAL 4, ...) the verse map says which of
your verses a source verse was paired with: the compact manifest's `verse_map` names it, e.g. `_versification_eng.json`, and the run
writes it into `OUT/compact/_index/` (from 0.3.1; the same file as in
[compact-alignments](https://huggingface.co/datasets/bcv-commons/compact-alignments) `_index/`). Its `map` lists only the verses that differ, source -> yours: `"DEU 29:12": "DEU 29:13"`
(your 29:13 was aligned as source 29:12), `"PSA 3:1": "PSA 3:title"` (a heading your text does not number). Use this file rather than
a versification table: it is exactly the pairing the run used.

The quality depends on how much text there is. The statistical aligner learns from your text, so a whole New Testament aligns
much better than one book. For a language we have published, it also starts from what our editions of that language show (pooled
priors, below), which matters most for a small text.

## 5. Options

**Source words or source parts** (from version 0.4.0). The run aligns your words to the PARTS of the Hebrew and Greek words (a Greek
verb's stem and its ending, a Hebrew noun's prefix, stem and suffix), not to whole words: `OUT/part-alignments` has a link per part,
and `OUT/compact` is the same folded to whole words.

> **`OUT/compact` is deprecated (2026-11-23).** The published compact-alignments is frozen and will be removed one week after
> part-alignments is published; align-text stops writing `OUT/compact` in the first release after that. Read `OUT/part-alignments`
> instead, and if you need the compact files, make them with `python fold.py --parts OUT/part-alignments --out DIR` (`fold.mjs` the
> same). The run record `align-text-run.json` says so under `deprecated`.

`--no-source-parts` aligns to whole source words, as before 0.4.0 (then there is
no `part-alignments`). With `--split` (below) the run aligns whole source words for now.

**Confidence from repeated runs.** The statistical aligner samples, so two runs on the same text differ a little. `--runs 3` runs the
chain three times (the extra runs go in `OUT/runs/`) and adds a `runs` channel to each book's `.meta.json` (per source part in
`part-alignments`, and compact's is its fold): one digit per aligned entry,
in the same order as the entries, saying how many of the runs made exactly that link. Links that every run makes are much more often
right than the rest: on Kuni Ruth, make-tanakh measured precision 0.71 for links two runs share against 0.23 for the others. On the
WEB sample, 85% of links are made by all three runs.

**Alignment by word parts.** For a language whose words carry prefixes and suffixes, `--split` aligns the parts (prefixes, stem,
suffixes) instead of whole words. Give it a word split in the bcv-split format (see [docs/split-format.md](../docs/split-format.md))
made for exactly this text, or `--split auto` for the language's published split:

```bash
.venv/bin/lexeme-aligner fetch-inputs --iso zin --split                # the published split, if there is one (Zinza has one)
.venv/bin/lexeme-aligner align-text --text zin-nt/ --edition zin_wbt --iso zin --out ~/zin-out --split auto
```

The split's text fingerprints are checked against your text, so a split made for another text is refused. The output is the
**morphed** edition `<iso>-m_<rest>` (here `zin-m_wbt`). Its `.meta.json` has a `parts` channel saying which part of each word a link
goes to, and the manifest names the split it was made with. The edition id must start with the ISO code.

**Pooled priors** (from version 0.3.0). For a language we have published, the run borrows what our editions of it show: for each
Hebrew or Greek word in your text, how those editions render it (from `fetch-inputs`' copy of the language's lexeme-alignments
file). These renderings become starting weights for the statistical aligner and join the dictionary the gloss step aligns with.
Your text still decides: a rendering it does not use gets no link. `--no-pool` aligns with your text alone, as before 0.3.0;
`--pool FILE` uses another lexeme-alignments file; `--pool-exclude A,B` leaves editions out of the pool (an edition with your id
is always left out). The run record's `pool` names the file, its sha256 and what was left out.
Measured on four languages with gold alignments (English BSB, Spanish RV09, Hindi IRV, Arabic Van Dyck), each edition left out of its
own pool. Link F1 of the whole chain, without -> with the pool, mean of paired runs:

| Text | English | Spanish | Hindi | Arabic |
|---|---|---|---|---|
| 420 scattered verses (GEN EXO NUM DEU EZR NEH 1CH JER MAT), 3 runs | 0.458 -> 0.498 | 0.721 -> 0.753 | 0.393 -> 0.460 | 0.806 -> 0.822 |
| whole books (JOS RUT EZR MAT MRK, ~2,800 verses), 2 runs | 0.499 -> 0.525 | 0.787 -> 0.790 | 0.565 -> 0.578 | 0.785 -> 0.789 |

Every one of the 12 small-text runs gained, in precision and recall both.

**Verse numbering.** Your text's numbering (English/KJV, Hebrew, ...) is detected from which verses its chapters end on. A text with
gaps (selected verses, a film's narration) cannot be detected that way, so such a text is taken as English (KJV) numbering and the run
says so. Give `--versification org` for Hebrew numbering, or `eng`, `lxx`, `vul`, `rso`, `orgw`, `catm`
([bcv-commons/bibles](https://github.com/bcv-commons/bibles) scheme codes). The run record's `versification` says what was used.

## 6. Optional: check the links with an LLM (paid, your key)

`--jev` sends every clause, with the chain's proposal, to TypeSafe's Jev model and builds a second layer from its answers. You
need your own `TYPESAFE_API_KEY`, and you pay TypeSafe directly.

```bash
.venv/bin/lexeme-aligner fetch-inputs --iso xyz --jev        # clause layouts + language notes (once)
.venv/bin/lexeme-aligner align-text ... --jev                # estimate only: prints the cost, sends nothing
TYPESAFE_API_KEY=apikey_... .venv/bin/lexeme-aligner align-text ... --force --jev --max-usd 5
```

- **Cost:** a run sends nothing without `--max-usd`. It is refused before it starts if the estimate is higher than that amount,
  and it stops sending when the amount is reached. As a guide, the four-book WEB sample (Ruth, Jonah, Mark, Philemon) is
  estimated at about $0.66. Philemon alone cost $0.014.
- **Fewer books:** `--books PHM,RUT` limits the check to those books.
- **Cache:** answers are cached in `OUT/jev/responses`, so running again never pays twice.
- **Output:** the layer goes to `OUT/full-alignments-llm/`. With `--split`, Jev is asked about the parts, and the layer belongs to the morphed edition.
- **Language notes:** for most languages Jev gets a generic description of the task. Languages that a team has written notes
  for (currently Kuni, `kvg`) get those notes.

## 7. Licences

- **The code** is GPL-3.0 (because eflomal is GPL).
- **Your text and your output are yours.** Nothing is uploaded.
- **The inputs** carry their own licences, recorded per file in `inputs.json`:
  - the spine: CC BY 4.0 (MACULA, Clear Bible);
  - the prior pack and the Hebrew clause layout: CC BY-SA 4.0;
  - grammar facts: CC BY 4.0 (Grambank, Glottolog), CC BY-SA 4.0 (URIEL), or CC0;
  - our published alignments of your language (pooled priors): CC0.

  If you publish output, credit MACULA. If you publish anything that includes the CC BY-SA inputs, use the same licence for it.

## 8. Check your setup

`pipeline/scripts/clean_install_test.sh` does all of the above in a temporary folder with a fresh clone, a new venv and the
WEB sample, then checks the result. Use `PY=python3.12` to test with another interpreter, and `--worktree` to test your local
changes instead of a fresh clone.
