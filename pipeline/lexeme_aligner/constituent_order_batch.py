"""Batch-run constituent_order.py across every currently-published language's PRIMARY edition. Purely
READ-ONLY over existing align_<method>_<tag>_*.jsonl (no re-alignment) — cheap, safe to re-run anytime.
Also builds the combined manifest.json + README for a future publish (never publishes itself — same
discipline as gapfill_batch.py/onboard_batch.py: publishing is always a separate, deliberate step).

    python3 -m lexeme_aligner.constituent_order_batch --dry-run
    python3 -m lexeme_aligner.constituent_order_batch
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT, OUT
from lexeme_aligner.constituent_order import profile
from lexeme_aligner.gapfill_batch import USJ_DIR_OVERRIDES, discover_tags, has_eflomal

_OUT_ROOT = Path("config/constituent_order")
_CARD = """---
license: cc0-1.0
tags:
- typology
- word-order
- multilingual
- bible
---

# constituent-order-profile

Per-language constituent-order statistics — how a language reorders Hebrew's phrase-level syntax
(BHSA `function`: Subject/Predicate/Object/Time/Location/Adjunct/Complement/...), measured directly
from alignment data, not asserted from grammar references. Every language aligns to the same Hebrew
source, so each aligned OT verse is a small parallel-order observation; aggregated over ~20-30k verses
per language this yields real, per-language word-order fingerprints — validated against known typology
(Arabic preserves Hebrew's verb-first `Pred>Subj` order 94% of the time; English flips it 56% of the
time to Subject-first; Indonesian sits near 50%, consistent with its verb-initial narrative register).

`pair_order_kept`: for each ADJACENT source-order phrase-function pair (a before b in the Hebrew), the
share of aligned verses where the target preserved that order. `function_drift`: mean normalized
target-position minus source-position per function type — which constituents a language systematically
fronts or defers. Both computed only from cells with >=30 observations (see `constituent_order.py`).

OT-only (BHSA has no Greek phrase layer). **CC0-1.0** — derived alignment statistics, no source text
redistributed.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", default=None, help="comma-separated isos to restrict to (default: all published)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--manifest", type=Path, default=LEX_ROOT / "manifest.json")
    ap.add_argument("--usj-root", type=Path, default=Path("pipeline/work/ingest-cache"))
    ap.add_argument("--out-root", type=Path, default=_OUT_ROOT)
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    all_isos = sorted(manifest["languages"])
    isos = [i.strip() for i in args.iso.split(",")] if args.iso else all_isos

    print(f"[constituent_order_batch] {len(isos)} language(s) to check", file=sys.stderr)
    results: dict[str, dict] = {}
    skipped = []
    for iso in isos:
        # first tag with actual data, NOT necessarily the catalog's is_primary=True one — a catalog
        # can list an edition first that was never ingested (live case: arb's catalog leads with
        # ARBASV, but the ingested/aligned edition is arb_vdv). Same fix as gapfill_batch.py's export.
        tags = [t for t, _ in discover_tags(iso) if has_eflomal(t)]
        if not tags:
            skipped.append(iso)
            continue
        if args.dry_run:
            print(f"  ▶ {iso}/{tags[0]}  (+{len(tags)-1} fallback(s) if OT-empty)", file=sys.stderr)
            continue
        # the first tag with ANY eflomal data isn't always OT-covered — a language's editions can mix
        # NT-only and NT+OT sources (live case: quc's first tag qucbla's own USJ dir is NT-only, but
        # a sibling edition has the OT text). Try each tag in order until one yields real OT verses.
        tag = prof = None
        for cand in tags:
            usj_dir = args.usj_root / USJ_DIR_OVERRIDES.get(cand, f"usj-{cand}")
            try:
                cand_prof = profile(cand, usj_dir, OUT)
            except Exception as e:
                print(f"  !! {iso}/{cand}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            if cand_prof["verses_measured"] >= 30:
                tag, prof = cand, cand_prof
                break
        if prof is None:
            skipped.append(iso)
            continue
        out_fp = args.out_root / f"{iso}.json"
        out_fp.parent.mkdir(parents=True, exist_ok=True)
        out_fp.write_text(json.dumps(prof, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        results[iso] = {"tag": tag, "verses_measured": prof["verses_measured"],
                        "pairs": len(prof["pair_order_kept"]), "functions": len(prof["function_drift"])}
        print(f"  ✓ {iso}/{tag}: {prof['verses_measured']} verses, "
              f"{len(prof['pair_order_kept'])} pairs", file=sys.stderr)

    if args.dry_run:
        print(f"[constituent_order_batch] dry-run — {len(isos) - len(skipped)} would run, "
              f"{len(skipped)} skipped (no primary tag/eflomal): {skipped[:10]}", file=sys.stderr)
        return 0

    args.out_root.mkdir(parents=True, exist_ok=True)
    # rebuild the manifest from EVERY profile file on disk, not just this run's `results` — a scoped
    # --iso run (e.g. re-running a handful of languages) must not clobber entries from a prior full run.
    all_results = {}
    for fp in sorted(args.out_root.glob("*.json")):
        if fp.name == "manifest.json":
            continue
        iso = fp.stem
        prof = json.loads(fp.read_text(encoding="utf-8"))
        all_results[iso] = results.get(iso) or {
            "tag": prof["tag"], "verses_measured": prof["verses_measured"],
            "pairs": len(prof["pair_order_kept"]), "functions": len(prof["function_drift"]),
        }
    manifest_doc = {
        "schema": ["pair_order_kept", "function_drift"],
        "languages": all_results,
    }
    manifest_fp = args.out_root / "manifest.json"
    manifest_fp.write_text(json.dumps(manifest_doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    (args.out_root / "README.md").write_text(_CARD, encoding="utf-8")
    content_sha = hashlib.sha256(json.dumps(all_results, sort_keys=True).encode()).hexdigest()
    print(f"\n[constituent_order_batch] done — {len(results)} language(s) profiled this run "
          f"({len(all_results)} total on disk), {len(skipped)} skipped, "
          f"content_sha256={content_sha[:12]} → {args.out_root}/", file=sys.stderr)
    print("[constituent_order_batch] nothing published — that's a separate, deliberate step", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
