"""Measure the fold rules (pos_score.FOLD_RULE_ARGS) on the gold editions run on the parts route (2026-11-23).

Per gold edition, testament, rule: link precision / recall / F1 of the union of the chain's methods, content words and all words,
scored on the PARTS run's own files (run with ALIGNER_SPINE_DB = the parts spine). Writes JSON to stdout.

    ALIGNER_SPINE_DB=pipeline/lexeme-spine-macula-parts-v1.1.db python pipeline/scripts/tools/measure_fold_rules.py [--out-dir DIR]
"""
import argparse
import json
from pathlib import Path

from lexeme_aligner.compact_align import ALL_BOOKS
from lexeme_aligner.eval import pos_score as ps
from lexeme_aligner.eval.contest_rule import gold_usj_dir
from lexeme_aligner.versification import remapper

EDITIONS = (("fra", "fra_lsg"), ("eng", "bsb"), ("spa", "spa_r09"), ("hin", "hinirv"), ("arb", "arb_vdv"))
UNION = "spanext+eflomal+gloss+gapfill"

ap = argparse.ArgumentParser()
ap.add_argument("--out-dir", type=Path, default=Path("pipeline/work/out-parts"))
a = ap.parse_args()
res = {}
for iso, tag in EDITIONS:
    gold_usj = Path(gold_usj_dir(iso)).resolve()
    usj = Path(f"pipeline/work/ingest-cache/usj-{tag}").resolve()
    for testament, books in (("NT", ALL_BOOKS[39:]), ("OT", ALL_BOOKS[:39])):
        books = [b for b in books if any(usj.glob(f"*-{b}.json"))]
        gold = ps.load_gold(iso, gold_usj, books, None, remap=remapper(tag, str(usj)))[0] if books else None
        if not gold:
            continue
        spine = ps.load_spine(books, usj, tag)
        for rule in ps.FOLD_RULE_ARGS:
            ours = ps.load_spec(UNION, tag, a.out_dir, books, spine, **ps.fold_args(rule))
            for content in (True, False):
                m = ps.score(gold, ours, spine, content_only=content).row()
                res[f"{tag}|{testament}|{rule}|{'content' if content else 'all'}"] = [round(m[k], 4) for k in
                                                                                      ("link_precision", "link_recall", "link_f1")]
        print(f"[measure_fold_rules] {tag} {testament} done", flush=True, file=__import__("sys").stderr)
print(json.dumps(res, indent=1))
