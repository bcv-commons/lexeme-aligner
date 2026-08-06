"""globalbibletools/data gloss priors — real, independently-vetted gloss data feeding
gloss_priors.GlossPriors' `llm_strongs_glosses/<iso>.tsv` input (previously absent/empty). Licensed
CC0-1.0 (org-wide default, confirmed both via internal-docs/gbt-alignment-handover.md's sibling-repo
check and directly with Global Bible Tools, 2026-08).

Reads from the SAME pinned local snapshot (`pipeline/vendor/gbt/`, see gbt_fetch.py) that
gbt_align.py's occurrence-alignment/benchmark-corroboration layer reads — one fetch, one pin, two
independent consumers (this module aggregates for the ALIGNER's gloss method; gbt_align.py extracts
occurrence-level alignment for benchmarking/citation — see that module's docstring). Run
`python -m lexeme_aligner.gbt_fetch` first if `pipeline/vendor/gbt/<lang>/` isn't populated yet.

Shape: one JSON file per book per language (`<lang>/<NN>-<Code>.json`, chapters[].verses[].words[]),
each word keyed by a stable id (`BBCCCVVVWW`). The `hbo+grc` directory is the source text, carrying
`lemma` (augmented Strong's, e.g. "H1481a" — the SAME scheme lexeme-spine.db uses, no crosswalk
needed); every target-language file carries a `gloss` string for the same word id. Cross-referencing
by word id directly yields (augmented_strong, target_gloss) pairs — this module aggregates those across
a whole Bible into the shape gloss_priors.GlossPriors already expects.

    python3 -m lexeme_aligner.gbt_source --lang eng --iso eng
    python3 -m lexeme_aligner.gbt_source --lang fra --iso fra --min-count 2 --top-n 5
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from lexeme_aligner.config import RESOURCES
from lexeme_aligner.gbt_align import _DATA_DIR


def _load_book(lang: str, fname: str, data_dir: Path) -> dict | None:
    fp = data_dir / lang / fname
    if not fp.exists():
        return None
    return json.loads(fp.read_text(encoding="utf-8"))


def cross_reference_all(lang: str, data_dir: Path = _DATA_DIR,
                        progress: bool = True) -> list[tuple[str, str, str]]:
    """-> [(word_id, augmented_strong, target_gloss), ...] across every book both `lang` and the
    hbo+grc source have in the pinned snapshot (gracefully skips a book either side is missing)."""
    lang_dir = data_dir / lang
    hbo_dir = data_dir / "hbo+grc"
    if not lang_dir.exists() or not hbo_dir.exists():
        raise SystemExit(f"[gbt_source] missing data dir(s): {lang_dir} / {hbo_dir} — run "
                         f"`python -m lexeme_aligner.gbt_fetch` first")
    out: list[tuple[str, str, str]] = []
    for hbo_fp in sorted(hbo_dir.glob("*.json")):
        target = _load_book(lang, hbo_fp.name, data_dir)
        if target is None:
            continue
        source = json.loads(hbo_fp.read_text(encoding="utf-8"))
        strong_by_id = {w["id"]: w["lemma"] for ch in source["chapters"] for v in ch["verses"]
                        for w in v["words"] if w.get("lemma")}
        n_before = len(out)
        for ch in target["chapters"]:
            for v in ch["verses"]:
                for w in v["words"]:
                    strong = strong_by_id.get(w["id"])
                    gloss = w.get("gloss")
                    if strong and gloss:
                        out.append((w["id"], strong, gloss.strip()))
        if progress:
            print(f"[gbt_source] {lang}/{hbo_fp.name}: {len(out) - n_before} pairs", file=sys.stderr)
    return out


def aggregate_by_strong(pairs: list[tuple[str, str, str]]) -> dict[str, Counter]:
    agg: dict[str, Counter] = defaultdict(Counter)
    for _wid, strong, gloss in pairs:
        agg[strong][gloss] += 1
    return agg


def write_priors_tsv(agg: dict[str, Counter], dest: Path, min_count: int = 1, top_n: int = 5) -> int:
    """llm_strongs_glosses/<iso>.tsv shape gloss_priors.GlossPriors already reads: strong<TAB>lex<TAB>
    n<TAB>gloss, header `strong\\t...` (skipped by the reader), gloss cell = top-N variants joined
    with '; ' (GlossPriors' own _SPLIT regex already treats ';' as a variant separator)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with dest.open("w", encoding="utf-8") as fh:
        fh.write("strong\tsource\tn\tgloss\n")
        for strong in sorted(agg):
            counter = agg[strong]
            total = sum(counter.values())
            if total < min_count:
                continue
            top = [g for g, _ in counter.most_common(top_n)]
            fh.write(f"{strong}\tgbt\t{total}\t{'; '.join(top)}\n")
            rows += 1
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", required=True, help="GBT directory name, e.g. eng, fra, spa")
    ap.add_argument("--iso", required=True, help="our own iso to publish the priors under "
                    "(RESOURCES/llm_strongs_glosses/<iso>.tsv)")
    ap.add_argument("--min-count", type=int, default=1, help="drop a strong with fewer than N total pairs")
    ap.add_argument("--top-n", type=int, default=5, help="keep at most N gloss variants per strong")
    ap.add_argument("--data-dir", type=Path, default=_DATA_DIR)
    ap.add_argument("--out", type=Path, default=None,
                    help="override output path (default: RESOURCES/llm_strongs_glosses/<iso>.tsv)")
    args = ap.parse_args()

    pairs = cross_reference_all(args.lang, args.data_dir)
    agg = aggregate_by_strong(pairs)
    dest = args.out or (RESOURCES / "llm_strongs_glosses" / f"{args.iso}.tsv")
    rows = write_priors_tsv(agg, dest, args.min_count, args.top_n)
    print(f"[gbt_source] {args.lang} -> {args.iso}: {len(pairs)} word pairs, {rows} lexeme rows "
          f"→ {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
