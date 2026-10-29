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
- the prior pack;
- whatever grammar facts we have for your language.

Every file is checked against a pinned sha256, so a file that changed upstream is refused. The folder's `inputs.json` records
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
| `compact/<i>/<iso>/<edition>/` | the alignment, one file per book in the [compact format](../docs/compact-alignments.md), with `.meta.json` sidecars and a manifest |
| `align_<method>_<edition>_<BOOK>.jsonl` | the links from each step: `eflomal`, `gloss`, `spanext`, `gapfill`, `residual` |
| `target-stopwords/`, `target-morphology/` | function words and affixes learned from your text |
| `align-text-run.json` | the run record: package version, git commit, sha256 of your text and of every input and setting, and each step's command, exit code and time |
| `run.log` | the steps' output |

The quality depends on how much text there is. The statistical aligner learns from your text alone, so a whole New Testament
aligns much better than one book.

## 5. Optional: check the links with an LLM (paid, your key)

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
- **Output:** the layer goes to `OUT/full-alignments-llm/`.
- **Language notes:** for most languages Jev gets a generic description of the task. Languages that a team has written notes
  for (currently Kuni, `kvg`) get those notes.

## 6. Licences

- **The code** is GPL-3.0 (because eflomal is GPL).
- **Your text and your output are yours.** Nothing is uploaded.
- **The inputs** carry their own licences, recorded per file in `inputs.json`:
  - the spine: CC BY 4.0 (MACULA, Clear Bible);
  - the prior pack and the Hebrew clause layout: CC BY-SA 4.0;
  - grammar facts: CC BY 4.0 (Grambank, Glottolog), CC BY-SA 4.0 (URIEL), or CC0.

  If you publish output, credit MACULA. If you publish anything that includes the CC BY-SA inputs, use the same licence for it.

## 7. Check your setup

`pipeline/scripts/clean_install_test.sh` does all of the above in a temporary folder with a fresh clone, a new venv and the
WEB sample, then checks the result. Use `PY=python3.12` to test with another interpreter, and `--worktree` to test your local
changes instead of a fresh clone.
