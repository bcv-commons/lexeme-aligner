"""Cross-target structural prior (#1 in internal-docs/gap-fill-scaling-strategy.md) — the scaling
MULTIPLIER, not a per-language fix. Every language aligns to the same MACULA lexeme anchor, so a lexeme's
STRUCTURAL SHAPE (does it usually render as one word or a phrase? how consistently?) can be aggregated
across every language we've ALREADY aligned and used to inform a gap-fill in a NEW language — no target-
language model, no per-language work. Confidence = how many independent languages agree, mirroring the
`senses_attested` cross-edition-agreement design, generalized from senses to alignment geometry.

Deliberately narrow in scope: cross-lingual RELATIVE POSITION isn't aggregated (word order differs by
language family — a French-derived position offset is not informative for Hindi), so this profile only
captures what genuinely transfers across unrelated languages: SPAN LENGTH / multi-word tendency (a
lexeme rendered by a fixed phrase — an idiom, a construct-state relation, a compound concept — tends to
need multiple target words in EVERY language, not just this one).

Edition grouping: every language in `lexeme-alignments/manifest.json` already POOLS its own editions
into one partition (the `base_text` column) — so, unlike the pre-2026-07 implementation (which had to
re-derive per-tag groups from raw alignment jsonl to avoid double-weighting a multi-edition language),
each published ISO here already IS one deduplicated language group, no extra grouping step needed. The
one thing this simplification drops: near-duplicate DIALECT variants published as separate bare isos
(e.g. `ind` vs `indala`/`indshv`/`indtsi`) are no longer collapsed into one group — a minor precision
loss, acceptable against the much bigger win below.

SOURCED FROM `lexeme-alignments` + `aligned_mwe` (both small, persisted, always-available published
datasets), NOT raw `out/` alignment jsonl (2026-07 rework). The original implementation needed every
language's raw per-verse jsonl simultaneously to compute this — but `out/` is deliberately transient
(cleaned per-language once a language's own chain finishes), so a language's contribution to this
profile would silently vanish the moment its jsonl was cleaned, even though its published data still
exists. Re-sourcing decouples the recompute from `out/`'s lifecycle entirely — it can now run any time,
not just in the narrow window before a cleanup sweep:
  - `lexeme-alignments/iso=<iso>/data.parquet` gives (surface, lexeme, count) — summed per lexeme, this
    is the occurrence TOTAL (denominator) and, via `surface.split()`'s word count, the raw span-length
    signal (though `lexeme-alignments`' multi-word surfaces are NOT guaranteed contiguous — see its own
    docs — so the union alone would inherit that noise).
  - `aligned_mwe/iso=<iso>/data.parquet` gives (lexeme, phrase, n_words, count) for CONFIRMED CONTIGUOUS
    multi-word spans only (`t_idx` contiguity already verified when that dataset was built) — the exact
    noise filter the original implementation never had (it counted `len(t_idx)` directly, contiguous or
    not). So `multiword_rate` = aligned_mwe's confirmed count ÷ lexeme-alignments' total count per
    lexeme — arguably MORE accurate than before, not just a workaround.
Caveat: `aligned_mwe` currently only covers each language's PRIMARY edition (no pooling), while
`lexeme-alignments`' denominator pools every edition — for a multi-edition language this can slightly
UNDER-estimate multiword_rate (the numerator doesn't see every edition the denominator does).

    python3 -m lexeme_aligner.cross_lang_prior --out publish/cross-lingual-span-profile/profile.json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT

_MWE_ROOT = Path("publish/aligned_mwe")
_DEFAULT_OUT = Path("publish/cross-lingual-span-profile/profile.json")
_MIN_LANGS = 2                            # a profile entry needs ≥2 independent languages to be usable
_MIN_DOM_OCC = 5                          # min occurrences before a language's dominance counts (light lexemes)


def _load_lex_counts(iso: str, lex_root: Path) -> dict[str, dict[str, int]]:
    """lexeme -> {surface: total_count}, summed across every method/base_text already unioned into
    this iso's published partition."""
    fp = lex_root / f"iso={iso}" / "data.parquet"
    if not fp.exists():
        return {}
    import pyarrow.parquet as pq
    counts: dict[str, dict[str, int]] = collections.defaultdict(lambda: collections.defaultdict(int))
    for r in pq.read_table(fp, columns=["surface", "lexeme", "count"]).to_pylist():
        counts[r["lexeme"]][r["surface"]] += r["count"]
    return counts


def _load_mwe_counts(iso: str, mwe_root: Path) -> dict[str, list[tuple[int, int]]]:
    """lexeme -> [(n_words, count), ...] for this iso's confirmed-contiguous multi-word phrases."""
    fp = mwe_root / f"iso={iso}" / "data.parquet"
    if not fp.exists():
        return {}
    import pyarrow.parquet as pq
    out: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    for r in pq.read_table(fp, columns=["lexeme", "n_words", "count"]).to_pylist():
        out[r["lexeme"]].append((r["n_words"], r["count"]))
    return out


def _lang_stats(lex_counts: dict, mwe_counts: dict) -> dict[str, dict]:
    """One language's per-lexeme stats: total occurrences, span_mean, multiword_rate, and target
    dominance (for the light-lexeme filter) — everything both profiles need, in one pass."""
    stats = {}
    for lexeme, surf_counts in lex_counts.items():
        total = sum(surf_counts.values())
        if total <= 0:
            continue
        mwe = mwe_counts.get(lexeme, [])
        mwe_total = min(sum(c for _, c in mwe), total)   # clip — different provenance basis can drift
        span_weighted = sum(n * c for n, c in mwe) + (total - mwe_total) * 1
        stats[lexeme] = {
            "total": total,
            "span_mean": span_weighted / total,
            "multiword_rate": mwe_total / total,
            "dominance": max(surf_counts.values()) / total,
        }
    return stats


def _scan_published(lex_root: Path = LEX_ROOT, mwe_root: Path = _MWE_ROOT):
    """Single pass over every published language's local parquet: per-lexeme, per-language stats
    for both the span profile (#1) and the source-scatter/light-lexeme profile.

    A language with NO aligned_mwe file contributes to `per_lex_lang_dom` (dominance needs only
    lexeme-alignments) but is EXCLUDED from `per_lex_lang_span` entirely — absence of aligned_mwe data
    means "unknown whether this lexeme is multi-word here", not "confirmed always single-word". Silently
    defaulting it to 0 would systematically drag multiword_rate down for every lexeme by counting every
    aligned_mwe-less language (736 of 999, as of 2026-07) as false negative evidence."""
    manifest_fp = lex_root / "manifest.json"
    isos = sorted(json.loads(manifest_fp.read_text(encoding="utf-8"))["languages"]) if manifest_fp.exists() else []
    per_lex_lang_span: dict[str, dict[str, tuple]] = collections.defaultdict(dict)
    per_lex_lang_dom: dict[str, dict[str, float]] = collections.defaultdict(dict)
    for iso in isos:
        lex_counts = _load_lex_counts(iso, lex_root)
        if not lex_counts:
            continue
        has_mwe = (mwe_root / f"iso={iso}" / "data.parquet").exists()
        mwe_counts = _load_mwe_counts(iso, mwe_root) if has_mwe else {}
        for lexeme, s in _lang_stats(lex_counts, mwe_counts).items():
            if has_mwe:
                per_lex_lang_span[lexeme][iso] = (s["span_mean"], s["multiword_rate"])
            if s["total"] >= _MIN_DOM_OCC:
                per_lex_lang_dom[lexeme][iso] = s["dominance"]
    return per_lex_lang_span, per_lex_lang_dom


def _build_profile_from(per_lex_lang_span: dict, min_langs: int = _MIN_LANGS) -> dict:
    profile = {}
    for lexeme, by_lang in per_lex_lang_span.items():
        if len(by_lang) < min_langs:
            continue
        means = [v[0] for v in by_lang.values()]
        multi = [v[1] for v in by_lang.values()]
        profile[lexeme] = {
            "n_langs": len(by_lang),
            "span_mean": round(sum(means) / len(means), 3),
            "multiword_rate": round(sum(multi) / len(multi), 3),
        }
    return profile


def build_profile(lex_root: Path = LEX_ROOT, mwe_root: Path = _MWE_ROOT, min_langs: int = _MIN_LANGS) -> dict:
    """{lexeme: {n_langs, span_mean, multiword_rate}} — per-language stats averaged EQUALLY across
    languages (so a language with more published editions doesn't out-vote one with fewer)."""
    per_lex_lang_span, _ = _scan_published(lex_root, mwe_root)
    return _build_profile_from(per_lex_lang_span, min_langs)


_DEFAULT_LIGHT_FLOOR = 0.30       # avg target dominance below this → "light" (semantically general)


def _build_light_lexemes_from(per_lex_lang_dom: dict, min_langs: int = _MIN_LANGS,
                              dominance_floor: float = _DEFAULT_LIGHT_FLOOR) -> dict:
    light = {}
    for lexeme, by_lang in per_lex_lang_dom.items():
        if len(by_lang) < min_langs:
            continue
        avg_dom = sum(by_lang.values()) / len(by_lang)
        if avg_dom < dominance_floor:
            light[lexeme] = round(avg_dom, 4)
    return light


def build_light_lexemes(lex_root: Path = LEX_ROOT, mwe_root: Path = _MWE_ROOT, min_langs: int = _MIN_LANGS,
                        dominance_floor: float = _DEFAULT_LIGHT_FLOOR) -> dict:
    """Light-lexeme profile: {lexeme: avg_target_dominance} for semantically general source
    lexemes whose average target-side dominance (across languages) is BELOW `dominance_floor`.
    These are light verbs (בּוֹא/come, ποιέω/do, δίδωμι/give), generic nouns (אִישׁ/man), and
    similar lexemes where no target language has a stable one-to-one rendering — a type-level
    dictionary entry is fundamentally wrong, so gloss tags them (kept but excluded from merge
    votes) and gap-fill gets a chance to attempt them with contextual priors.

    Sourced from the same published lexeme-alignments data as the span profile (#1). Gets more
    reliable as more languages are published — the signal is source-anchored and universal."""
    _, per_lex_lang_dom = _scan_published(lex_root, mwe_root)
    return _build_light_lexemes_from(per_lex_lang_dom, min_langs, dominance_floor)


_CARD = """---
license: cc0-1.0
tags:
- alignment
- multilingual
- bible
- interlingua
---

# cross-lingual-span-profile

A per-**MACULA-lexeme** structural profile — span length / multi-word tendency — aggregated across every
language the lexeme-aligner has aligned. Every language anchors to the same lexeme, so this is a
language-independent INTERLINGUA signal: it tells you whether a Hebrew/Greek lexeme typically needs a
single target word or a multi-word phrase (compound place names — "Kadesh Barnea" — compound numbers —
"four thousand"), based on what OTHER languages actually did, with NO target-language model for the
language you're applying it to.

`n_langs` = how many independent languages (editions of the same language pooled first, so a 2-edition
language doesn't out-vote a 1-edition one) attest the lexeme; `multiword_rate`/`span_mean` = the per-
language-averaged span statistics. Confidence scales with `n_langs` — refresh as more languages are
aligned (see the lexeme-aligner's `cross_lang_prior.py`).

**CC0-1.0** — derived alignment statistics, no source text redistributed.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lex-root", type=Path, default=LEX_ROOT, help="published lexeme-alignments root")
    ap.add_argument("--mwe-root", type=Path, default=_MWE_ROOT, help="published aligned_mwe root")
    ap.add_argument("--min-langs", type=int, default=_MIN_LANGS)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    ap.add_argument("--publish", metavar="REPO_ID", default=None, help="HF dataset repo to push to")
    ap.add_argument("--create", action="store_true", help="create the HF dataset repo if missing")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    per_lex_lang_span, per_lex_lang_dom = _scan_published(args.lex_root, args.mwe_root)

    profile = _build_profile_from(per_lex_lang_span, args.min_langs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, sort_keys=True, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    multiword = sorted(profile.items(), key=lambda kv: -kv[1]["multiword_rate"])[:10]
    print(f"[cross_lang_prior] {len(profile)} lexemes (≥{args.min_langs} languages) → {args.out}\n"
          f"  most multi-word across languages: "
          f"{[(lx, r['multiword_rate'], r['n_langs']) for lx, r in multiword]}", file=sys.stderr)

    light = _build_light_lexemes_from(per_lex_lang_dom, args.min_langs)
    light_path = args.out.parent / "light_lexemes.json"
    light_path.write_text(json.dumps(light, sort_keys=True, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    print(f"[cross_lang_prior] light lexemes: {len(light)} semantically general lexemes "
          f"(avg dominance <{_DEFAULT_LIGHT_FLOOR}) → {light_path}", file=sys.stderr)

    if args.publish:
        readme = args.out.parent / "README.md"
        manifest = args.out.parent / "manifest.json"
        readme.write_text(_CARD, encoding="utf-8")
        manifest.write_text(json.dumps(
            {"lexemes": len(profile), "min_langs": args.min_langs,
             "content_sha256": hashlib.sha256(
                 json.dumps(profile, sort_keys=True).encode()).hexdigest()},
            indent=2, sort_keys=True) + "\n", encoding="utf-8")
        files = [args.out.name, "manifest.json", "README.md"]
        if args.dry_run:
            print(f"[cross_lang_prior] dry-run → would push {files} to {args.publish}", file=sys.stderr)
            return 0
        try:
            from huggingface_hub import HfApi
        except ImportError as e:
            raise SystemExit(f"[cross_lang_prior] needs huggingface_hub — pip install -e '.[publish]' ({e})")
        api = HfApi()
        if args.create:
            api.create_repo(args.publish, repo_type="dataset", exist_ok=True)
        for f in files:
            api.upload_file(path_or_fileobj=str(args.out.parent / f), path_in_repo=f,
                            repo_id=args.publish, repo_type="dataset",
                            commit_message=f"cross-lingual-span-profile: {len(profile)} lexemes")
        print(f"[cross_lang_prior] pushed {files} to {args.publish}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
