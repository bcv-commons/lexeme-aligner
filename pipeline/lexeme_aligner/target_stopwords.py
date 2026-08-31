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

    python3 -m lexeme_aligner.target_stopwords --usj-dir pipeline/work/ingest-cache/usj-fra-lsg --iso fra --all
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT, PRIOR_PACK
from lexeme_aligner.run_pilot import _BOOK_FILE_NUM, OT_BOOKS, NT_BOOKS
from lexeme_aligner.usj_source import read_verses, tokenize

_CACHE_DIR = Path("publish/target-stopwords")
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


_MIN_DOMINANT_SHARE = 0.4    # dominant lexeme (POOLED, see _load_bridge) must carry ≥40% of the mass
_MIN_ALIGNED_MASS = 25       # ...and the surface needs this much aligned mass before that share is trusted
_LIGHT_FILE = Path("config/light_lexemes.json")


def _load_light_lexemes(path: Path = _LIGHT_FILE) -> set[str]:
    """Source-side (axis C) veto set — see config/light_lexemes.json's own _doc for the rationale."""
    if not Path(path).exists():
        return set()
    return set(json.loads(Path(path).read_text(encoding="utf-8")).get("lexemes", {}))


def _load_bridge(prior_pack: Path) -> dict[str, frozenset]:
    """lexeme -> lexemes linked across testaments by prior-pack's LXX bridge (`lxx_greek`/`lxx_hebrew`).

    OFF BY DEFAULT — measured 2026-08-31 and NOT worth enabling; kept for a future tighter bridge. The
    idea was that a target content word's mass legitimately SPLITS across cross-testament equivalents and
    then clears no single-lexeme floor (ind 'allah' 0.346 on grc:2316 alone but 0.928 pooled). It does fix
    those, but the LXX bridge is a TRANSLATION-CORRESPONDENCE relation (what Greek word rendered this
    Hebrew somewhere in the LXX), NOT concept identity, so the groups are over-broad by construction:
    grc:2316 θεός pools with hbo:0001 אָב 'father', hbo:1952 הוֹן 'wealth' and hbo:6697 צוּר 'rock';
    hbo:2416 חַי 'living' pools with grc:2342 θηρίον 'wild beast'; hbo:3605 כֹּל 'all' pools with
    grc:3588 ὁ, the definite article.

    Measured on 36 externally-covered languages, identical candidate sets (kept words / precision /
    true function words found): veto-only 2190/0.6954/1523 vs veto+pool 2126/0.7008/1490 — pooling COSTS
    33 true function words for +0.005 precision. Its own old-vs-new disagreement audit put it at ~53%
    precision over the 73 words it acts on, with real misfires (ind 'hanya' = 'only'). A bridge restricted
    to mutual/top-1 correspondences might be worth revisiting; this one is not."""
    import pyarrow.parquet as pq
    if not Path(prior_pack).exists():
        return {}
    out: dict[str, frozenset] = {}
    for r in pq.read_table(prior_pack, columns=["lexeme", "lxx_greek", "lxx_hebrew"]).to_pylist():
        grp = {r["lexeme"]}
        for g in (r.get("lxx_greek") or []):
            grp.add("grc:" + str(g)[1:])                       # bridge ids are 'G2316'/'H0430'-shaped
        for h in (r.get("lxx_hebrew") or []):
            grp.add("hbo:" + str(h)[1:])
        out[r["lexeme"]] = frozenset(grp)
    return out


def _load_dominant_lexeme(aligned_root: Path, iso: str, min_share: float,
                          bridge: dict[str, frozenset] | None = None,
                          light: set[str] | None = None,
                          min_mass: int = _MIN_ALIGNED_MASS) -> dict[str, str]:
    """surface -> its dominant lexeme in this language's OWN eflomal-base output, IF that lexeme's CONCEPT
    GROUP (itself + its LXX-bridge equivalents) carries ≥min_share of the surface's total aligned mass AND
    the group is not semantically light. A genuine translation-equivalent concentrates on one concept; a
    function word's TRUE correspondences are fragmented across dozens of low-count relations, so its
    "biggest" partner is eflomal co-occurrence NOISE (very frequent words spuriously collide) — e.g. on
    fra, 'dieu' (God) pools 89.9% onto the Elohim/theos group, but 'de' scatters across 233 distinct
    lexemes with its biggest sliver only 6.1% — noise, not a real correspondence. The pooled share floor is
    what separates them; keyness alone is not (both dieu's and de's "dominant" lexeme are content-tagged —
    the noise partner just happens to also be content)."""
    import pyarrow.parquet as pq
    fp = Path(aligned_root) / f"iso={iso}" / "data.parquet"
    if not fp.exists():
        return {}
    bridge = {} if bridge is None else bridge
    light = set() if light is None else light
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in pq.read_table(fp, columns=["surface", "lexeme", "count", "method"]).to_pylist():
        if (r.get("method") or "eflomal") != "eflomal":         # union parquet -> dedupe on the eflomal base
            continue
        counts[r["surface"]][r["lexeme"]] += r["count"]
    out = {}
    for s, c in counts.items():
        total = sum(c.values())
        if total < min_mass:
            # Too little evidence to read a share off at all. Both a statistical and a linguistic guard:
            # (1) a proportion cannot be estimated from a handful of observations — deu 'dass' had ONE
            #     aligned occurrence, landing on hbo:0157 אָהֵב 'love' at share 1.000, which sailed past
            #     the floor and pulled a plain conjunction out of the stopword list (eng 'unto' likewise);
            #     15% of all rescues rested on <10 occurrences, 22% on <50.
            # (2) function words are systematically UNDER-aligned (eflomal leaves most of their
            #     occurrences unaligned), so small aligned mass is itself evidence of function-word-hood,
            #     independent of corpus frequency — eng 'them' occurs in the thousands but carries only
            #     137 aligned mass vs 'people' 1,619. This is what separates a spurious dominant from a
            #     real one: ita 'ad'→elohim 26 and swe 'honom'→melek 20, against spa 'dios'→elohim 13,258
            #     and swe 'herren'→YHWH 4,364. (PMI does NOT separate them — measured: herren 3.59 vs
            #     honom 3.58 — so raw mass, not an association measure, is the discriminating signal.)
            continue
        lex, _ = c.most_common(1)[0]
        grp = bridge.get(lex, frozenset({lex}))
        if grp & light:                     # light concept -> renders function words; proves nothing
            continue
        if sum(n for l, n in c.items() if l in grp) / total >= min_share:
            out[s] = lex
    return out


def rescue_available(iso: str, aligned_root: Path = LEX_ROOT, prior_pack: Path = PRIOR_PACK) -> bool:
    """True if BOTH rescue inputs exist for this iso. The rescue degrades to a no-op without them, which
    silently ships a raw frequency+dispersion list (see rescue_content_words) — callers that persist the
    result should check this first."""
    return (Path(aligned_root) / f"iso={iso}" / "data.parquet").exists() and Path(prior_pack).exists()


def rescue_content_words(candidates: set[str], iso: str, aligned_root: Path = LEX_ROOT,
                         prior_pack: Path = PRIOR_PACK, min_share: float = _MIN_DOMINANT_SHARE) -> set[str]:
    """Cross-check candidates against this language's OWN taken-pool alignment + prior-pack keyness
    (axis C): if a candidate CONCENTRATES its alignment mass (≥min_share) on one prior-pack CONTENT lexeme,
    it's a real content-word rendering, not a function word — remove it from the stopword set. Degrades to
    a no-op (keeps every candidate) if lexeme-alignments or the prior-pack isn't available yet for this iso.

    The degradation is WARNED, not silent: it shipped 22.3% of the published lists as unrescued raw
    frequency+dispersion sets (content words like fra 'dieu', spa 'dios', ind 'anak' left in) because
    nothing surfaced that the rescue hadn't run. See `repair_cached` for the retroactive fix."""
    # bridge=None: LXX pooling is off by default — see _load_bridge for the measurement that retired it.
    # The light veto is what carries the gain (+288 true function words at flat precision).
    dominant = _load_dominant_lexeme(aligned_root, iso, min_share, None, _load_light_lexemes())
    content = _load_content_lexemes(prior_pack)
    if not dominant or not content:
        missing = "lexeme-alignments" if not dominant else "prior-pack"
        print(f"[target_stopwords] WARNING {iso}: content-word rescue SKIPPED ({missing} unavailable) — "
              f"keeping {len(candidates)} raw frequency+dispersion candidates, content words may remain",
              file=sys.stderr)
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


def repair_cached(iso: str, cache_dir: Path = _CACHE_DIR, aligned_root: Path = LEX_ROOT,
                  prior_pack: Path = PRIOR_PACK, min_share: float = _MIN_DOMINANT_SHARE,
                  write: bool = False) -> tuple[set[str], set[str]] | None:
    """Retroactively apply the content-word rescue to an ALREADY-WRITTEN `<iso>.txt`. Returns
    (removed, kept), or None if there's nothing to repair (no cache file, or rescue inputs absent).

    Valid because the cached file IS the raw frequency+dispersion candidate set: `compute_stopwords` is
    `_raw_candidates` (deterministic from the target text) followed by the rescue (a pure filter over that
    set). So re-filtering the persisted candidates is equivalent to having rescued at generation time — no
    re-ingest, no target text needed. Idempotent: an already-rescued list re-filters to itself, which also
    makes this its own integrity check (a non-empty `removed` means that list was stale)."""
    fp = Path(cache_dir) / f"{iso}.txt"
    if not fp.exists() or not rescue_available(iso, aligned_root, prior_pack):
        return None
    words = {w.strip() for w in fp.read_text(encoding="utf-8").splitlines() if w.strip()}
    if not words:
        return None
    kept = rescue_content_words(words, iso, aligned_root, prior_pack, min_share)
    removed = words - kept
    if removed and write:
        fp.write_text("\n".join(sorted(kept)) + "\n", encoding="utf-8")
    return removed, kept


_INGEST_CACHE = Path("pipeline/work/ingest-cache")


def usj_dir_for(iso: str, ingest_cache: Path = _INGEST_CACHE) -> Path | None:
    """Locate an ingested USJ dir for a BARE iso. Dirs are named `usj-<edition tag>` and the tag is the
    iso plus an edition suffix (onboard._tag), so a prefix glob resolves it; every ISO 639-3 code is
    exactly 3 chars, so `usj-<iso>*` cannot collide with a different language.

    A language may have several. Pick the one with the MOST book files, not the lexicographically first:
    the bare-iso dir (`usj-ayz`, `usj-gef`) is often a leftover EMPTY shell from a partial/failed ingest
    and sorts ahead of the real edition (`usj-ayzyss`, `usj-gefsgv`), which silently produced 0 candidates
    and wiped those languages' lists. Ties break alphabetically so the choice stays deterministic."""
    hits = [p for p in Path(ingest_cache).glob(f"usj-{iso}*") if p.is_dir()]
    if not hits:
        return None
    return max(hits, key=lambda p: (len(list(p.glob("*.json"))), [-ord(c) for c in p.name]))


def recompute_cached(iso: str, cache_dir: Path = _CACHE_DIR, ingest_cache: Path = _INGEST_CACHE,
                     top_n: int = _DEFAULT_TOP_N, min_dispersion: float = _DEFAULT_MIN_DISPERSION,
                     write: bool = False) -> tuple[set[str], set[str]] | None:
    """Rebuild `<iso>.txt` from the target's own ingested USJ text under the CURRENT rescue rule.
    Returns (old, new), or None if the text or the rescue inputs are unavailable.

    Distinct from `repair_cached`, and necessary: repair re-filters the PERSISTED candidate set, so it can
    only ever remove more words. When the rule changes such that something should be KEPT that a previous
    rule removed (the light-lexeme veto restoring fra est/sont/tous/a, wrongly rescued by the old
    top-1 rule), only a recompute from the raw text can restore it."""
    d = usj_dir_for(iso, ingest_cache)
    if d is None or not rescue_available(iso):
        return None
    fp = Path(cache_dir) / f"{iso}.txt"
    old = ({w.strip() for w in fp.read_text(encoding="utf-8").splitlines() if w.strip()}
           if fp.exists() else set())
    new = compute_stopwords(d, iso, None, top_n, min_dispersion)
    if write and new != old:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text("\n".join(sorted(new)) + "\n", encoding="utf-8")
    return old, new


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


def _main_repair(args) -> int:
    """--repair: re-apply the rescue to already-written lists (see repair_cached)."""
    isos = [args.iso] if args.iso else sorted(p.stem for p in Path(args.out).glob("*.txt"))
    n_checked = n_stale = n_removed = 0
    skipped: list[str] = []
    for iso in isos:
        res = repair_cached(iso, args.out, write=not args.dry_run)
        if res is None:
            skipped.append(iso)
            continue
        removed, kept = res
        n_checked += 1
        if removed:
            n_stale += 1
            n_removed += len(removed)
            if args.verbose:
                print(f"  {iso}: -{len(removed):3d} → {len(kept):3d} kept   {sorted(removed)[:8]}")
    verb = "would remove" if args.dry_run else "removed"
    print(f"[target_stopwords] repair: {n_checked} checked, {n_stale} stale, {verb} {n_removed} content "
          f"words; {len(skipped)} skipped (no cache file or rescue inputs absent)", file=sys.stderr)
    return 0


def _main_recompute(args) -> int:
    """--recompute: rebuild lists from the target's own USJ text under the current rule (see
    recompute_cached) — the only path that can RESTORE a word a previous rule wrongly removed."""
    isos = [args.iso] if args.iso else sorted(p.stem for p in Path(args.out).glob("*.txt"))
    n_ok = n_changed = n_added = n_dropped = 0
    skipped: list[str] = []
    for iso in isos:
        res = recompute_cached(iso, args.out, top_n=args.top_n, min_dispersion=args.min_dispersion,
                               write=not args.dry_run)
        if res is None:
            skipped.append(iso)
            continue
        old, new = res
        n_ok += 1
        if new != old:
            n_changed += 1
            n_added += len(new - old)
            n_dropped += len(old - new)
            if args.verbose:
                print(f"  {iso}: {len(old):3d} → {len(new):3d}  +{len(new - old)} -{len(old - new)}  "
                      f"restored={sorted(new - old)[:6]}")
    print(f"[target_stopwords] recompute: {n_ok} rebuilt, {n_changed} changed "
          f"(+{n_added} restored, -{n_dropped} removed); {len(skipped)} skipped (no USJ text or rescue "
          f"inputs absent)", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usj-dir")
    ap.add_argument("--iso")
    ap.add_argument("--repair", action="store_true",
                    help="re-apply the content-word rescue to already-written <iso>.txt lists "
                         "(all of --out, or just --iso); idempotent, but can only REMOVE words")
    ap.add_argument("--recompute", action="store_true",
                    help="rebuild <iso>.txt lists from the target's own ingested USJ text under the "
                         "current rule — slower than --repair, but the only path that can RESTORE a word "
                         "a previous rule wrongly removed")
    ap.add_argument("--dry-run", action="store_true", help="--repair: report only, write nothing")
    ap.add_argument("--verbose", action="store_true", help="--repair: list what each language loses")
    ap.add_argument("--ot", action="store_true"); ap.add_argument("--nt", action="store_true")
    ap.add_argument("--all", action="store_true", help="OT+NT (default)")
    ap.add_argument("--top-n", type=int, default=_DEFAULT_TOP_N)
    ap.add_argument("--min-dispersion", type=float, default=_DEFAULT_MIN_DISPERSION)
    ap.add_argument("--out", type=Path, default=_CACHE_DIR)
    args = ap.parse_args()

    if args.repair:
        return _main_repair(args)
    if args.recompute:
        return _main_recompute(args)
    if not args.usj_dir or not args.iso:
        ap.error("--usj-dir and --iso are required unless --repair is given")

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
