"""Verse-local verification — signals computable only once every method's output is in hand.

Placed BEFORE both exports on purpose. compact-alignments is where these signals are most useful, but
computing them there would make compact-alignments and lexeme-alignments disagree about the same verse,
which breaks the provenance model in docs/publishing-principles.md. So this annotates the shared jsonl
and both exports derive from one corrected source.

CROSS-METHOD AGREEMENT (`agree`) — how many methods independently produced the IDENTICAL target span for
a source token. Measured against Clear gold (fra/hin/eng, NT, 2026-09-01):

    1 method   40.8% / 81.5% / 56.1%
    2 methods  75.7% / 94.9% / 94.8%        +34.9 / +13.4 / +38.7 pt

and — the reason it is worth publishing rather than just knowing — it adds information the existing
`hi_conf` (score >= 0.9) flag does not carry. Cross-tabulated:

                                     fra     eng     hin
    hi_conf=T  agree>=2            75.7%   94.9%   95.1%
    hi_conf=T  agree<2             40.8%   58.7%   88.2%
    hi_conf=F  agree>=2            71.6%   70.4%   74.3%
    hi_conf=F  agree<2             41.8%   44.8%   54.3%

The tier we already publish as high-confidence is internally heterogeneous by 7-36 points, and on
fra/eng a NON-hi_conf token that has agreement (70-72%) beats a hi_conf token that does not (41-59%) —
i.e. the current flag mis-ranks those two groups. Agreement fixes that at one byte per token.

Caveat worth carrying: gloss bootstraps from eflomal's own export, so these two are not fully
independent and their agreement is a weaker guarantee than, say, gap-fill agreeing with the residual
pass. It is nonetheless strongly discriminative, which is what the numbers above measure.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

from lexeme_aligner.align_files import tag_files

METHODS = ("eflomal", "gloss", "gapfill", "residual")


def agreement(out_dir: Path, iso: str, methods=METHODS) -> dict:
    """{(chapter, verse): {h_idx: n_methods_sharing_the_most_common_span}} over the methods present.

    Keyed on the EXACT span, not on "both aligned this token somehow": two methods that pick different
    targets are disagreement, and counting them as corroboration is precisely the error that would make
    the signal meaningless."""
    spans: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for m in methods:
        for fp in tag_files(out_dir, m, iso):
            for line in fp.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                for p in rec["pairs"]:
                    ti = p.get("t_idx")
                    if ti:
                        spans[(rec["chapter"], rec["verse"])][p["h_idx"]].append(tuple(sorted(ti)))
    return {v: {h: collections.Counter(s).most_common(1)[0][1] for h, s in per.items()}
            for v, per in spans.items()}


def annotate(out_dir: Path, iso: str, methods=METHODS, write: bool = True) -> collections.Counter:
    """Write `agree` onto every pair of every method's jsonl. Idempotent — recomputed from scratch each
    time, so re-running after adding a method simply refreshes the counts."""
    agree = agreement(out_dir, iso, methods)
    tally: collections.Counter = collections.Counter()
    for m in methods:
        for fp in tag_files(out_dir, m, iso):
            out, changed = [], False
            for line in fp.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                for p in rec["pairs"]:
                    n = agree.get((rec["chapter"], rec["verse"]), {}).get(p["h_idx"])
                    if n is not None and p.get("agree") != n:
                        p["agree"] = n
                        changed = True
                    if p.get("t_idx"):
                        tally[p.get("agree", 1)] += 1
                out.append(json.dumps(rec, ensure_ascii=False))
            if write and changed:
                fp.write_text("\n".join(out) + "\n", encoding="utf-8")
    return tally
