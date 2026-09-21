# LLM alignment experiment (opt-in)

> **Audience:** contributors who want to measure what an LLM can add on top of this repo's statistical
> alignment, on a language that already has published data. If you only want to *use* the published
> datasets you don't need this — see the top-level [`README.md`](../README.md).

## What this is

The default pipeline (eflomal → gloss → gapfill → …) aligns Hebrew/Greek source words to a translation
without any LLM, so it works for every language in the catalog. This experiment adds an **optional** pass,
`llm_align.py`, that asks an LLM about *only the parts the statistical chain left open or was unsure of*, and
scores the answers against gold so that precision and cost can be compared on equal terms. It is never part
of the default chain and never publishes anything by itself.

The stepping stone is the language's **whole published alignment data**. For a language we already publish,
the chain resolves almost every token deterministically — on Matthew, Mark, Romans and 1 Corinthians only
**1.4 % (French) / 3.8 % (Hindi)** of content tokens are left unaligned. Sending an LLM the whole verse would
re-buy work the statistics already did; sending it the residue (plus a hint list of that word's known
renderings from the published data) makes the task small, cheap, and easier.

## Five strategies

| `--strategy` | The LLM decides… | Why it exists |
|---|---|---|
| `gap` | the content words eflomal + gloss left unaligned, onto the target positions still free | cheapest; the residue after the dictionary |
| `gap-seeded` | as `gap`, plus per-lexeme **seeds**: renderings of that lexeme attested elsewhere in this language, with counts | do the hints raise precision / cut output tokens? |
| `lexeme-grouped` | one lexeme across up to N verses per call (`--group-size`) | amortizes the lexeme card; cross-verse consistency |
| `verify` | eflomal pairs scored below 0.9: confirm, correct or reject | the largest judgeable pool (thousands of pairs vs. hundreds of gap tokens) |
| `full` | every content word of the verse, nothing pre-aligned | reference point: is an LLM-only pass ever worth it? most expensive |

A **lexeme** is the MACULA-anchored original-language word (`grc:1078`, `hbo:0430`); see the
[`lexeme-alignments`](https://huggingface.co/datasets/bcv-commons/lexeme-alignments) dataset card.

## Running it

Everything below is read-only with respect to published data; outputs go to `pipeline/work/out/` (git-ignored).

```bash
# 1. $0 self-check of the whole write → validate → score loop (the mock answers with gapfill's own fills):
python3 -m lexeme_aligner.llm_align --iso hinirv --publish-iso hin \
    --usj-dir pipeline/work/ingest-cache/usj-hinirv --nt --strategy gap-seeded --provider mock

# 2. see exactly what a model would be sent, and an estimate, without calling anything:
python3 -m lexeme_aligner.llm_align ... --strategy gap-seeded --model claude-sonnet-5 --dry-run [--ref 40001003]

# 3. a real cell — Anthropic API (needs ANTHROPIC_API_KEY in .env; prompt caching + optional --batch at 50% off):
python3 -m lexeme_aligner.llm_align ... --nt --strategy gap-seeded --provider anthropic \
    --model claude-sonnet-5 --effort low --batch --max-usd 10
#    …or through your Claude subscription (no API key; `claude -p` with tools and customizations off):
python3 -m lexeme_aligner.llm_align ... --provider cli

# 4. score it against gold and append a comparable row to pipeline/work/out/llm_report.md:
python3 -m lexeme_aligner.llm_align --report --iso hinirv --publish-iso hin \
    --out-tag hinirv.gap-seeded.sonnet5 --gold-iso hin
```

`make llm-align` / `make llm-score` wrap the same commands (see the Makefile header). Install the optional
dependency with `pip install -e ".[llm]"` (only the API route needs it).

**Prerequisite — the base chain must exist under the gold edition's tag.** `--iso` is the tag whose
`align_<method>_<iso>_*.jsonl` are read (eflomal, gloss, gapfill; residual is optional). For a gold language,
`--usj-dir` must be the edition the gold was built from (`config/gold_langs.json`); the tool refuses otherwise,
because scoring one Bible against another Bible's gold reads as a quality problem, not a setup error
(measured on Spanish: 10.9 % vs 54.8 % gap-fill precision).

## Reading a report row

Each cell is scored on the **same books and the same judgeable subset** as the gapfill and residual baselines
that ran on those tokens (a fill is *correct* when one of its words is in the gold's set for that verse and
Strong's number; tokens the gold has no truth for are *not judgeable* — neither right nor wrong).

| column | meaning |
|---|---|
| `to decide` / `fills` / `gap cov` | tokens sent to the model / pairs it produced / fills ÷ to-decide |
| `judgeable` / `correct` / `precision` | fills the gold can judge / of those, correct / correct ÷ judgeable |
| `net cov (pt)` | fills as a share of *all* content tokens (0 for `verify`, which adds no coverage) |
| `tokens in / cached / out` | uncached input / prompt-cache reads / output |
| `$`, `$/1k correct` | run cost; cost ÷ judgeable-correct × 1000 (a lower bound on cost per correct fill) |

For `verify` the report adds a second table: the precision of the **original eflomal proposals on exactly the
tokens the model was shown** against the precision of what it kept (confirmed + corrected).

## Output format

`align_llm_<out-tag>_<BOOK>.jsonl`, in gapfill's per-verse shape, so the existing scorers read it unchanged
(`score_gapfill --method llm`, `benchmark --method llm`). Each pair carries `method: "llm"`,
`prior: "llm_<strategy>"` (verify: `llm_verify_confirmed` / `llm_verify_corrected`), a `score` (0.75 by default;
0.9 when gapfill/residual independently picked the same span, or the model confirmed eflomal's own proposal),
a short `note`, and any `repairs` validation applied. Tokens the model declined (`unrepresented`, `rejected`)
appear in `llm_skipped`, never as pairs. `llm_usage_<out-tag>.json` is the cost ledger (tokens, cache reads,
$ with and without prompt caching, decisions, repairs); every run also appends a line to `llm_runs.jsonl`.

Nothing consumes `method="llm"` yet. Wiring it in is mechanical when the numbers justify it: add it to
`export_lex._METHODS` (additive union — the schema needs no change), ship it in `compact_align` as an opt-in
`.extra.json` layer like `residual` (the default chain must keep working for languages with no LLM coverage),
and add a priority in `merge_align`.

## Safety rails

- `--max-usd` is a hard stop (checked between calls; for `--batch`, against the up-front estimate). Prices in
  `llm_providers.PRICES` are **assumptions** from Anthropic's published list prices — override with `--prices`.
- Every request is cached on disk (`pipeline/work/llm-cache/`) under a hash of route + model + effort + prompt
  version + prompt + schema. Re-running or re-scoring a finished cell costs $0.
- The model's answer is validated against the packet before anything is written: every source id answered
  exactly once, only available target positions, no position claimed twice, spans contiguous unless
  `--allow-scattered`, never a function-words-only span. Every repair is counted in the ledger.
- The `claude -p` route strips `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` from the subprocess environment: if
  either is present Claude Code bills the API instead of your subscription, silently, at several times the rate.

## Known limits

- **"Taken" positions can be wrong.** In the packet, positions held by already-aligned words are marked `*`.
  When the base pass mis-aligned (e.g. Matthew 1:3, where two names are swapped), the `gap` strategies cannot
  fix it — that is what `verify` is for.
- The target words are the aligner's tokens, with non-spacing marks (Indic viramas, Arabic harakat, Hebrew
  points) removed, so the model sees `उतपनन` for `उत्पन्न`. The prompt says so.
- eflomal is not deterministic (~1 % run-to-run), so baselines from separate chain runs differ slightly.
- The gold judges only about half of the gap tokens; judge precision from the judgeable subset, and check
  `judgeable` before reading anything into a number.

## Results (Sonnet 5, effort low, `claude -p` route, MAT/MRK/ROM/1CO)

Precision is on gold-judgeable tokens only; the same-token table in `llm_report.md` compares each system on
the *same* tokens.

| cell | correct / all judgeable gap tokens | baseline (gapfill, else residual) | cost |
|---|---|---|---|
| fra `gap-seeded` | 42 of 133 (31.6%), 30 wrong | 36 (27.1%), 62 wrong | $2.97 |
| hin `gap-seeded` | 194 of 367 (52.9%), 32 wrong | 109 (29.7%), 114 wrong | $6.86 |

| cell | eflomal pairs < 0.9 shown | precision of the original proposals | precision of what the LLM kept |
|---|---|---|---|
| fra `verify` | 2,199 (1,558 judgeable) | 44.9% | 66.2% (459 rejected, 740 corrected) |
| hin `verify` | 3,276 | 61.6% (Matthew sample) | 94.5% (Matthew sample) |

Two caveats that matter more than the numbers. First, these are **dictionary** metrics: they say whether
the right word was chosen for a lexeme, not whether a verse is correctly and completely aligned — so they
support improving the `lexeme-alignments` lists, and say nothing about the trustworthiness of a per-verse
(compact) alignment; that needs a positional metric, which this repo does not have yet. Second, the French
gold used here has a known surface defect after hyphenated/apostrophe words (upstream in the Clear release),
which depresses every French figure slightly.

## Where this is going

The measured question above turned out to be the smaller one. The ASV project is not a better dictionary
builder — it is a **verse aligner**: every source and target token accounted for, one-to-many both ways.
That is a second product for this repo, for LLM-capable languages only, with its own contract (whole verse,
statistics as evidence rather than constraints), its own measuring stick — `pos_score.py`, link
precision/recall/AER and exact-span rate against Clear gold mapped onto our token positions (already usable:
on the same four books the chain scores F1 0.88 for French but only 0.63 for Hindi, where the gold's
multi-word spans are the whole difference); structural validity, review and consistency where there is no
gold — and an extended `compact-alignments` format. The statistical chain and the published lists for all ~1,600 languages are
unaffected. The `gap`/`verify` strategies here remain useful as cheap improvers of the lists.
