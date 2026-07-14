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
from pathlib import Path

from lexeme_aligner.gloss_align import Normalizer
from lexeme_aligner.run_pilot import _BOOK_FILE_NUM, OT_BOOKS, NT_BOOKS
from lexeme_aligner.usj_source import read_verses, tokenize

_CACHE_DIR = Path("data/morph")
_MIN_STEM = 3           # a stem must be at least this many characters
_MIN_PREFIX = 2         # single-char prefixes are almost always orthographic noise (common first letters)
_MAX_AFFIX = 5          # affixes longer than this are not considered
_MIN_STEMS = 12         # an affix must attach to ≥ this many distinct real stems to count as productive
_TOP_AFFIX = 40         # keep at most this many productive affixes per side (guards against a long noise tail)


def _learn(usj_dir: Path, books) -> dict:
    """Learn productive suffixes + prefixes + the real-stem lexicon from the target's own token types."""
    vocab: collections.Counter = collections.Counter()
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

    suffixes = [a for a, n in suf_prod.most_common(_TOP_AFFIX) if n >= _MIN_STEMS]
    prefixes = [a for a, n in pre_prod.most_common(_TOP_AFFIX) if n >= _MIN_STEMS]
    # Stem lexicon for the "remainder must be a real word/stem" guard: suffix-side paradigm stems + attested
    # words ONLY. pre_stems is deliberately excluded — it's the orthographically-noisy side (every common
    # letter-run looks like a prefix-stem), and letting it into the guard lets garbage prefix-strips through.
    return {"suffixes": suffixes, "prefixes": prefixes, "n_types": len(vocab),
            "stems": list(real_stems | vset)}


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
