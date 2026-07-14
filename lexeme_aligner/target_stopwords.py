"""Target-side function-word filter (#3 in internal-docs/gap-fill-scaling-strategy.md) — language-
independent stopword detection from the target's OWN text. No POS model, no download, no labels.

Why: gap-fill was landing content Hebrew/Greek lexemes on target function-word scraps (French de/le/il/et)
because eflomal/gloss already consumed the real content-word rendering, leaving only stopword leftovers
untaken (`align_gap`'s `avail` pool). Excluding those from the candidate set turns a wrong fill into a
correct non-fill (precision, not recall). This is the target-side mirror of the source-side `is_content`
flag the spine already carries — the target text arrives as raw text, so the equivalent signal has to be
induced instead of read off a spine column.

Method: rank target word-forms by raw corpus frequency; keep the K most frequent whose DISPERSION (share of
books they occur in) clears a floor — the classic corpus-linguistics stopword-induction recipe. Reuses
usj_source.read_verses/tokenize, so stopwords are computed on the exact same token forms `align_gap` sees.

Frequency+dispersion alone is NOT enough: measured on fra, it false-positives on genuine high-value content
words — "dieu" (God) ranks #31 by frequency with 0.95 dispersion (it's mentioned in nearly every book), yet
73% of its occurrences render `hbo:0430`/`grc:2316` (Elohim/theos), both prior-pack CONTENT nouns
(keyness 2.58/0.97). So every candidate is cross-checked (`rescue_content_words`, axis C) against this
language's OWN taken-pool alignment + prior-pack keyness — the same content criterion `bootstrap_priors`
already uses for gloss ("keyness is not null" = content). A candidate whose dominant source lexeme is
prior-pack content is rescued (kept eligible), even if its target-side distribution looks function-word-like.
This needs `lexeme-alignments/iso=<iso>/` to already exist (an eflomal pass has run) + the prior-pack;
degrades gracefully (skips the rescue, keeps the raw frequency+dispersion set) if either is absent.

    python3 -m lexeme_aligner.target_stopwords --usj-dir data/usj-fra-lsg --iso fra --all
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT, PRIOR_PACK
from lexeme_aligner.run_pilot import _BOOK_FILE_NUM, OT_BOOKS, NT_BOOKS
from lexeme_aligner.usj_source import read_verses, tokenize

_CACHE_DIR = Path("data/stopwords")
_DEFAULT_TOP_N = 150
_DEFAULT_MIN_DISPERSION = 0.85     # must occur in ≥85% of books to count as a function word


def _raw_candidates(usj_dir: str | Path, books: list[str] | None,
                    top_n: int, min_dispersion: float) -> set[str]:
    usj_dir = Path(usj_dir)
    books = books or (OT_BOOKS + NT_BOOKS)
    freq: collections.Counter = collections.Counter()
    presence: dict[str, set] = collections.defaultdict(set)
    n_books = 0
    for book in books:
        fp = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
        if not fp.exists():
            continue
        verses = read_verses(fp)
        if not verses:
            continue
        n_books += 1
        book_words: set[str] = set()
        for text in verses.values():
            words = [w.lower() for w in tokenize(text)]
            freq.update(words)
            book_words.update(words)
        for w in book_words:
            presence[w].add(book)
    if not freq or not n_books:
        return set()
    ranked = [w for w, _ in freq.most_common(top_n)]
    return {w for w in ranked if len(presence[w]) / n_books >= min_dispersion}


def _load_content_lexemes(prior_pack: Path) -> dict[str, bool]:
    """lexeme -> True if prior-pack marks it content (keyness is not null)."""
    import pyarrow.parquet as pq
    if not Path(prior_pack).exists():
        return {}
    rows = pq.read_table(prior_pack, columns=["lexeme", "keyness"]).to_pylist()
    return {r["lexeme"]: r.get("keyness") is not None for r in rows}


_MIN_DOMINANT_SHARE = 0.4    # dominant lexeme must carry ≥40% of the surface's aligned mass to count


def _load_dominant_lexeme(aligned_root: Path, iso: str, min_share: float) -> dict[str, str]:
    """surface -> its dominant lexeme in this language's OWN eflomal-base output, IF that lexeme carries
    ≥min_share of the surface's total aligned mass. A genuine translation-equivalent concentrates on one
    partner; a function word's TRUE correspondences are fragmented across dozens of low-count relations, so
    its "biggest" partner is eflomal co-occurrence NOISE (very frequent words spuriously collide) — e.g. on
    fra, 'dieu' (God) concentrates 55.7% of its mass on hbo:0430 (Elohim), but 'de' scatters across 81
    distinct lexemes with its biggest sliver only 7.7% — noise, not a real correspondence. The share floor
    is what separates them; keyness alone is not (both dieu's and de's "dominant" lexeme can be content-
    tagged — the noise partner just happens to also be a content word)."""
    import pyarrow.parquet as pq
    fp = Path(aligned_root) / f"iso={iso}" / "data.parquet"
    if not fp.exists():
        return {}
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in pq.read_table(fp).to_pylist():
        if r.get("method", "eflomal") != "eflomal":            # union parquet -> dedupe on the eflomal base
            continue
        counts[r["surface"]][r["lexeme"]] += r["count"]
    out = {}
    for s, c in counts.items():
        total = sum(c.values())
        if not total:
            continue
        lex, n = c.most_common(1)[0]
        if n / total >= min_share:
            out[s] = lex
    return out


def rescue_content_words(candidates: set[str], iso: str, aligned_root: Path = LEX_ROOT,
                         prior_pack: Path = PRIOR_PACK, min_share: float = _MIN_DOMINANT_SHARE) -> set[str]:
    """Cross-check candidates against this language's OWN taken-pool alignment + prior-pack keyness
    (axis C): if a candidate CONCENTRATES its alignment mass (≥min_share) on one prior-pack CONTENT lexeme,
    it's a real content-word rendering, not a function word — remove it from the stopword set. Degrades to
    a no-op (keeps every candidate) if lexeme-alignments or the prior-pack isn't available yet for this iso."""
    dominant = _load_dominant_lexeme(aligned_root, iso, min_share)
    content = _load_content_lexemes(prior_pack)
    if not dominant or not content:
        return candidates
    return {w for w in candidates if not content.get(dominant.get(w, ""), False)}


def compute_stopwords(usj_dir: str | Path, iso: str | None = None, books: list[str] | None = None,
                      top_n: int = _DEFAULT_TOP_N, min_dispersion: float = _DEFAULT_MIN_DISPERSION,
                      aligned_root: Path = LEX_ROOT, prior_pack: Path = PRIOR_PACK) -> set[str]:
    """Frequency + dispersion stopword induction over the target's OWN ingested text, then rescue any
    candidate whose dominant rendering is a prior-pack content lexeme (see module docstring)."""
    candidates = _raw_candidates(usj_dir, books, top_n, min_dispersion)
    if iso and candidates:
        candidates = rescue_content_words(candidates, iso, aligned_root, prior_pack)
    return candidates


class StopwordFilter:
    """Cached per-language function-word set. `.is_function(word)` — case-insensitive lookup.
    Reads data/stopwords/<iso>.txt if present; computes + caches it from usj_dir otherwise."""

    def __init__(self, iso: str, usj_dir: str | Path | None = None, cache_dir: Path = _CACHE_DIR):
        self.iso = iso
        self._cache_fp = Path(cache_dir) / f"{iso}.txt"
        if self._cache_fp.exists():
            self.words = {w.strip() for w in
                         self._cache_fp.read_text(encoding="utf-8").splitlines() if w.strip()}
        elif usj_dir:
            self.words = compute_stopwords(usj_dir, iso)
            self._save()
        else:
            self.words = set()

    def _save(self) -> None:
        self._cache_fp.parent.mkdir(parents=True, exist_ok=True)
        self._cache_fp.write_text("\n".join(sorted(self.words)) + "\n", encoding="utf-8")

    def is_function(self, word: str) -> bool:
        return bool(word) and word.lower() in self.words


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usj-dir", required=True)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--ot", action="store_true"); ap.add_argument("--nt", action="store_true")
    ap.add_argument("--all", action="store_true", help="OT+NT (default)")
    ap.add_argument("--top-n", type=int, default=_DEFAULT_TOP_N)
    ap.add_argument("--min-dispersion", type=float, default=_DEFAULT_MIN_DISPERSION)
    ap.add_argument("--out", type=Path, default=_CACHE_DIR)
    args = ap.parse_args()

    books = (OT_BOOKS if args.ot else NT_BOOKS if args.nt else OT_BOOKS + NT_BOOKS)
    words = compute_stopwords(args.usj_dir, args.iso, books, args.top_n, args.min_dispersion)
    fp = args.out / f"{args.iso}.txt"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text("\n".join(sorted(words)) + "\n", encoding="utf-8")
    print(f"[target_stopwords] {args.iso}: {len(words)} function words (top_n={args.top_n}, "
          f"min_dispersion={args.min_dispersion}) → {fp}\n  sample: {sorted(words)[:25]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
