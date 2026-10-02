"""Unsupervised target morphology (#2 in internal-docs/gap-fill-scaling-strategy.md) — a language-
independent, dependency-free, download-free stemmer learned from the target's OWN text.

Why: gloss's `_word_score` matches a prior rendering against a verse token; its `Normalizer` plug-point is
what lets a prior `berkata` match text `kata` (stem tier, 0.9). But the ONLY hand-coded normalizer is
Indonesian — every other language falls back to the lowercase-only default, so in a morphologically rich
target (Indic, Bantu, Turkic — the actual tail) an inflected form never matches its dictionary stem and
gloss collapses (Indic ~15%). This learns the target's affixes instead of hand-coding them, so gloss's
stem-matching fires for EVERY language with no per-language work — the generalized replacement for the
hand-coded Normalizer. Same output shape (`forms(token) -> [token, stem…]`, token FIRST so eflomal, which
reads `forms()[0]`, is untouched — only gloss's fuzzy match uses the stem candidates).

Method (signature-based, "Linguistica"-lite, MDL-free): from the target's own token types, split every word
at every point; a STEM is a prefix seen with ≥2 distinct suffixes (a paradigm); a SUFFIX is PRODUCTIVE if it
attaches to ≥`min_stems` distinct real stems (a true inflectional affix attaches to hundreds — a coincidental
ending to a handful). Prefixes symmetrically. `stem(word)` strips one productive affix when the remainder is
itself a known stem. Isolating languages (zh/vi) learn no productive affixes → forms() ≈ identity → no-op,
never a regression. Cached to data/morph/<iso>.json.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import unicodedata
from pathlib import Path

from lexeme_aligner.gloss_align import Normalizer
from lexeme_aligner.run_pilot import _BOOK_FILE_NUM, OT_BOOKS, NT_BOOKS
from lexeme_aligner.usj_source import read_verses, tokenize

_CACHE_DIR = Path("publish/target-morphology")
_MIN_STEM = 3           # a stem must be at least this many characters
_MIN_PREFIX = 2         # single-char prefixes are almost always orthographic noise (common first letters)
_MAX_AFFIX = 5          # affixes longer than this are not considered
_MIN_STEMS = 12         # an affix must attach to ≥ this many distinct real stems to count as productive
_TOP_AFFIX = 40         # keep at most this many productive affixes per side (guards against a long noise tail)
_RATIO_SAMPLE_TOKENS = 100_000  # see sample_tokens_per_type()'s docstring for why this is capped at all


def _has_letter(affix: str) -> bool:
    """A real affix carries at least one LETTER. An affix made purely of combining marks is an artifact of
    the split-at-every-point search, not a morpheme — in scripts whose vowels are written as trailing marks
    the search reads consonant|vowel-sign as stem|suffix, and since each such mark attaches to hundreds of
    stems it sails past the `_MIN_STEMS` productivity test. Measured on Myanmar: 39 of kht's 40 "productive
    suffixes" were bare vowel signs and tone marks (ူ ု း ႈ ွ) plus a variation selector, so `stem()` was
    stripping PHONEMIC vowels; priors and target tokens then normalised differently and gloss collapsed to
    4.9% while eflomal held 84.9%. Chinese was unaffected only because it learns no affixes at all (0) and
    the normaliser degrades to identity — which is exactly the behaviour this restores for Myanmar.

    Same spirit as `_MIN_PREFIX` ("single-char prefixes are orthographic noise"), and a no-op for the
    alphabetic-script languages whose affixes (fra s/e/es/nt, ind nya/an/lah) all contain letters."""
    return any(unicodedata.category(c)[0] == "L" for c in affix)


def _learn(usj_dirs: Path | str | list[Path | str], books) -> dict:
    """Learn productive suffixes + prefixes + the real-stem lexicon from the target's own token types.
    `usj_dirs`: one directory (single-edition, the original contract) OR a LIST of directories — R10
    follow-up (2026-09-28): pooling every available edition's text for a language gives a more robust
    estimate than any one arbitrary edition, and removes any dependence on WHICH edition's scratch
    files happen to still exist on disk when this runs (see pipeline_decisions.py's own docstring for
    why that mattered)."""
    if isinstance(usj_dirs, (str, Path)):
        usj_dirs = [usj_dirs]
    vocab: collections.Counter = collections.Counter()
    for usj_dir in usj_dirs:
        usj_dir = Path(usj_dir)
        for book in books:
            fp = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
            if not fp.exists():
                continue
            for text in read_verses(fp).values():
                vocab.update(w.lower() for w in tokenize(text))
    types = [w for w in vocab if len(w) >= _MIN_STEM]

    # stem -> set of affixes it is seen with (the bare form contributes "")
    suf_of: dict[str, set] = collections.defaultdict(set)     # stem=w[:k], suffix=w[k:]
    pre_of: dict[str, set] = collections.defaultdict(set)     # stem=w[k:], prefix=w[:k]
    vset = set(vocab)
    for w in types:
        suf_of[w].add("")                                    # attested bare
        pre_of[w].add("")
        for k in range(_MIN_STEM, len(w)):
            if len(w) - k <= _MAX_AFFIX:
                suf_of[w[:k]].add(w[k:])
        for k in range(_MIN_PREFIX, len(w) - _MIN_STEM + 1):  # ≥2: single-char prefixes are orthographic noise
            if k <= _MAX_AFFIX:
                pre_of[w[k:]].add(w[:k])

    real_stems = {s for s, sufs in suf_of.items() if len(sufs) >= 2}
    pre_stems = {s for s, pres in pre_of.items() if len(pres) >= 2}

    suf_prod: collections.Counter = collections.Counter()
    for s in real_stems:
        for suf in suf_of[s]:
            if suf:
                suf_prod[suf] += 1
    pre_prod: collections.Counter = collections.Counter()
    for s in pre_stems:
        for pre in pre_of[s]:
            if pre:
                pre_prod[pre] += 1

    # Bug found + fixed 2026-09-28: `Counter.most_common(_TOP_AFFIX)` breaks ties (common — many rare
    # affixes tie at the minimum qualifying count right at the _TOP_AFFIX cutoff) by INSERTION ORDER,
    # which came from iterating `real_stems`/`pre_stems` — plain `set`s, whose iteration order Python
    # randomizes per-PROCESS (string hash randomization, PYTHONHASHSEED) for security reasons unrelated
    # to this code. Proven directly: three separate process runs on byte-identical input/code produced
    # three different top-40 lists; fixing PYTHONHASHSEED=0 made two separate runs agree exactly. This
    # made the published cache non-reproducible for as long as this function has existed — nothing had
    # reason to call it a second time for most languages, so the drift went unnoticed until
    # pipeline_decisions.py did. Fixed by sorting explicitly (count descending, affix STRING ascending
    # as the tie-break) instead of relying on Counter's insertion-order tie-break at all.
    suffixes = [a for a, n in sorted(suf_prod.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_AFFIX]
               if n >= _MIN_STEMS and _has_letter(a)]
    prefixes = [a for a, n in sorted(pre_prod.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_AFFIX]
               if n >= _MIN_STEMS and _has_letter(a)]
    # Stem lexicon for the "remainder must be a real word/stem" guard: suffix-side paradigm stems + attested
    # words ONLY. pre_stems is deliberately excluded — it's the orthographically-noisy side (every common
    # letter-run looks like a prefix-stem), and letting it into the guard lets garbage prefix-strips through.
    # n_tokens (added R10, 2026-09-28): total corpus token COUNT (vocab is already a Counter, so this is
    # free) — n_types alone can't tell a rich-morphology language (many surface forms, each rare) from a
    # small corpus (few forms, also each rare); the RATIO of the two is what tokens_per_type() below uses.
    return {"suffixes": suffixes, "prefixes": prefixes, "n_types": len(vocab), "n_tokens": sum(vocab.values()),
            "stems": sorted(real_stems | vset)}


# R10 (2026-09-28): target-side stemming for eflomal (`EflomalAligner(stem=True)`, `run_pilot
# --eflomal-stem`) pools a language's own inflected surface forms into one training type, which only
# helps when the language actually HAS enough of those forms to matter — affix COUNT turned out useless
# as that signal (nearly every language, including English, hits the hard _TOP_AFFIX=40 cap, so it
# doesn't discriminate at all). tokens/type (corpus size / vocabulary size) does: a rich-morphology
# target spreads its content words over many distinct surface forms, so each form is individually
# rarer relative to corpus size — exactly the sparsity stemming fixes.
#
# CORRECTED 2026-09-28, same day: the FIRST calibration pass (whose numbers this comment used to carry)
# had TWO confounds, both found while building pipeline_decisions.json's full-catalog pass:
#   1. Corpus-size confound — the ratio is NOT scale-invariant (Heaps' law: vocabulary grows
#      sub-linearly with corpus size, for any language). guj's own ratio was 2.57 on its 92k-token
#      single Door43 edition but 24.44 once ALL its real production editions were pooled (1.36M
#      tokens) — same language, 9.5x jump. Fixed by `sample_tokens_per_type()` capping at a fixed
#      token budget instead of reading a language's full pooled corpus.
#   2. A SEPARATE, worse bug: the first pass measured ratios via `LearnedNormalizer(iso, usj_dir=...)`,
#      but `LearnedNormalizer.__init__` reads an EXISTING cache at `<cache_dir>/<iso>.json` FIRST,
#      before ever consulting `usj_dir` — for every one of guj/kan/mar/npi/ory/tel (real, already-
#      onboarded catalog languages), a production cache already existed, so the "Door43 edition" ratio
#      the first pass reported was actually silently read from each language's UNRELATED real
#      production cache, never the Door43 text at all. Confirmed directly: orglt's own text, read
#      fresh (bypassing any cache), gives ratio 10.45 — not the originally-reported 3.23.
#      `sample_tokens_per_type()` never reads or writes any cache at all, so it is immune by
#      construction (this is also why `tokens_per_type()` no longer takes a meaningful `cache_dir`).
# Recalibrated ratios (fresh, cache-free, capped at 100k tokens) against the SAME real gold F1 deltas
# measured earlier (those deltas are unaffected by either bug — they came from real eflomal reruns
# against real gold, not from this ratio):
#   kan 4.23 +.037 | tel 5.09 +.029 | arb 5.82 +.008 | fin 6.78 +.014 | mar 7.25 +.034 | npi 7.74 +.024 |
#   guj 9.08 +.031 | asm 10.44 +.009 | ory 10.45 +.016 | ben 12.44 +.011 | spa 14.02 +.005 |
#   fra 16.95 +.014 | hin 18.54 +.004 | eng 21.76 -.004 | cmn 51.81 -.002 (isolating-language control,
#   0 productive affixes ever learned — a mathematical no-op; its own small negative swing is pure
#   eflomal run-to-run noise, the floor the other small deltas near zero are judged against).
# Still not perfectly monotonic (arb's +.008 is smaller than fin/mar's despite a lower ratio) but
# CLEANLY separates into two bands this time: every language at ratio <= 12.44 (kan through ben) is a
# real, unambiguous positive (+.008 to +.037); everything at or above 14.02 is weak-to-negative
# (spa/hin barely positive, eng/cmn negative). Threshold set at the gap between them — conservative
# (excludes fra's own real +.014 rather than guess a rule from one point at a higher ratio), same
# fail-closed philosophy as before: auto-enable only where the measured band says so, leave individual
# higher-ratio languages (fra included) to their own dedicated measurement, same discipline
# config/spanext_flags.json already uses per-language.
STEM_RATIO_THRESHOLD = 13.0


def sample_tokens_per_type(usj_dirs: str | Path | list[str | Path],
                          books=None, max_tokens: int = _RATIO_SAMPLE_TOKENS) -> tuple[int, int]:
    """(tokens_seen, distinct_types_seen) over AT MOST `max_tokens` tokens of `usj_dirs`' pooled text —
    the fix (2026-09-28) for a real, found-in-production confound: tokens/type computed over a
    language's FULL pooled corpus is NOT scale-invariant (Heaps' law — vocabulary size grows
    sub-LINEARLY with corpus size, for ANY language, regardless of morphology), so the SAME language's
    ratio drifts upward just from pooling more editions together, independent of how agglutinative it
    actually is. Measured proof: guj's own ratio was 2.57 on a 92k-token single Door43 edition (the
    corpus STEM_RATIO_THRESHOLD was originally calibrated against) but 24.44 once ALL of guj's real
    production editions were pooled into a 1.36M-token corpus — a 9.5x jump for the exact same
    language. Capping at a FIXED token budget makes every language's ratio comparable regardless of how
    much text happens to be available/pooled — the reason this is a SEPARATE, cheap function (no affix
    induction at all, just tokenize-and-count, stopping the moment the cap is hit) rather than reusing
    `_learn()`'s own full-corpus `n_types`/`n_tokens`, which stay full-corpus on purpose (more data is
    strictly better for the actual induced suffix/prefix/stem model `LearnedNormalizer` uses — only the
    GATE's comparison needs to be scale-controlled, not the model itself). Directories are sorted for a
    deterministic pooling order (which tokens are "first" up to the cap must not depend on argument
    order); book order is `OT_BOOKS + NT_BOOKS` (spine order) by default, also deterministic."""
    if isinstance(usj_dirs, (str, Path)):
        usj_dirs = [usj_dirs]
    if books is None:
        books = OT_BOOKS + NT_BOOKS
    vocab: set[str] = set()
    total = 0
    for usj_dir in sorted(str(d) for d in usj_dirs):
        usj_dir = Path(usj_dir)
        for book in books:
            fp = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
            if not fp.exists():
                continue
            for text in read_verses(fp).values():
                for w in tokenize(text):
                    vocab.add(w.lower())
                    total += 1
                    if total >= max_tokens:
                        return total, len(vocab)
    return total, len(vocab)


def tokens_per_type(iso: str, usj_dir: str | Path | list[str | Path] | None = None,
                    cache_dir: Path = _CACHE_DIR) -> float | None:
    """Corpus tokens / distinct vocabulary types for `iso`, over a FIXED-SIZE sample — see
    `sample_tokens_per_type`'s own docstring for why this must be scale-controlled rather than read off
    the language's full pooled corpus (a real confound found in production, 2026-09-28). `usj_dir`: a
    single directory, or a LIST of directories to pool every available edition's text. Returns `None`
    (never a guess) when `usj_dir` isn't given at all, or the sample is empty; callers must treat `None`
    as "leave stemming off". `iso`/`cache_dir` are accepted for call-signature compatibility with the
    pre-2026-09-28 cache-reading version but are no longer used — this always computes fresh, which is
    cheap by construction (stops at `max_tokens`) and, not incidentally, means this function can no
    longer mutate the shared `LearnedNormalizer` cache as a side effect (the bug the original,
    cache-reading version had — see git history / this module's changelog for the full incident)."""
    if not usj_dir:
        return None
    total, n_types = sample_tokens_per_type(usj_dir)
    if not n_types:
        return None
    return total / n_types


def should_stem(iso: str, usj_dir: str | Path | list[str | Path] | None = None,
                cache_dir: Path = _CACHE_DIR) -> tuple[bool, float | None]:
    """(decision, ratio) — the single function `run_pilot.py`'s `--eflomal-stem` auto-gate and
    `pipeline_decisions.py`'s ledger both call, so the two can never disagree about what "auto" means.
    `decision` is `False` whenever `ratio` is `None` (fail closed, never guess) or the ratio sits at or
    above `STEM_RATIO_THRESHOLD` — see that constant's own docstring for the real calibration data."""
    ratio = tokens_per_type(iso, usj_dir=usj_dir, cache_dir=cache_dir)
    return (ratio is not None and ratio <= STEM_RATIO_THRESHOLD), ratio


class LearnedNormalizer(Normalizer):
    """Unsupervised morphological normalizer. forms(token) -> [token, stem candidates] (token first)."""

    def __init__(self, iso: str, usj_dir: str | Path | None = None, cache_dir: Path = _CACHE_DIR):
        self.iso = iso
        self._cache_fp = Path(cache_dir) / f"{iso}.json"
        model = None
        if self._cache_fp.exists():
            model = json.loads(self._cache_fp.read_text(encoding="utf-8"))
        elif usj_dir:
            model = _learn(Path(usj_dir), OT_BOOKS + NT_BOOKS)
            self._cache_fp.parent.mkdir(parents=True, exist_ok=True)
            self._cache_fp.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        model = model or {"suffixes": [], "prefixes": [], "stems": []}
        # longest affix first so we strip the maximal one
        self.suffixes = sorted(model["suffixes"], key=len, reverse=True)
        self.prefixes = sorted(model["prefixes"], key=len, reverse=True)
        self.stems = set(model["stems"])
        self._cache: dict[str, list] = {}

    def _known(self, s: str) -> bool:
        return len(s) >= _MIN_STEM and s in self.stems

    def forms(self, token: str) -> list[str]:
        t = token.lower()
        if t in self._cache:
            return self._cache[t]
        stems = {t}
        for suf in self.suffixes:                            # strip one productive suffix
            if suf and t.endswith(suf) and self._known(t[: -len(suf)]):
                stems.add(t[: -len(suf)])
                break
        for base in list(stems):                             # then optionally one productive prefix
            for pre in self.prefixes:
                if pre and base.startswith(pre) and self._known(base[len(pre):]):
                    stems.add(base[len(pre):])
                    break
        out = [t] + [s for s in sorted(stems, key=len, reverse=True) if s != t]
        self._cache[t] = out
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usj-dir", required=True)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--out", type=Path, default=_CACHE_DIR)
    ap.add_argument("--sample", nargs="*", default=[], help="words to show stemming for")
    args = ap.parse_args()
    model = _learn(Path(args.usj_dir), OT_BOOKS + NT_BOOKS)
    fp = args.out / f"{args.iso}.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    print(f"[target_morph] {args.iso}: {model['n_types']} types → "
          f"{len(model['suffixes'])} productive suffixes, {len(model['prefixes'])} prefixes → {fp}\n"
          f"  suffixes: {model['suffixes'][:20]}\n  prefixes: {model['prefixes'][:20]}", file=sys.stderr)
    if args.sample:
        norm = LearnedNormalizer(args.iso)
        for w in args.sample:
            print(f"  {w} → {norm.forms(w)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
