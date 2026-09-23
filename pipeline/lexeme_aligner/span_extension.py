"""Widen a name/noun span by one adjacent, unclaimed target-language function word — narrowly gated by
analyze_language.py's own phase-1 audit (a real per-language-confirmed anomaly, not a blind cap) plus a
Grambank-derived DIRECTION telling which side of the span to check.

WHY THIS EXISTS: eflomal is statistical — it learns "this source word needs N target words" from repeated
co-occurrence. Common nouns recur often enough to learn a correct multi-word span; a specific proper NAME
may occur only a handful of times, so the model has far less evidence and defaults to the single most
literal word, silently dropping an adjacent case-marker/article/etc. it should have included. This mirrors
exactly the base-chain undershoot found by hand for por/hin/arb/eng this session (config/llm_conventions/
*.md) — this module is that same finding turned into an actual pipeline fix, not just an LLM prompt caveat.

NOT the general "blind extension" already measured harmful (grambank_fetch.py's own docstring: marginal
token precision decayed 22.5% -> 12.8% -> 10.5% as an untargeted extension cap rose, against a 24.4%
no-extension baseline). This is narrow on three axes at once: (1) only the POS a RISK_RULES entry already
names, (2) only languages/POS analyze_language.py's phase-1 audit ACTUALLY flags for THIS base-chain run
(same anomaly threshold as the audit, not "any Grambank-flagged language"), (3) only in the Grambank-
derived DIRECTION for that category — never both directions, never a guess when the language's own order
is mixed/ambiguous.

MEASURED (this session, real manual Clear gold, name/noun lexemes only, before -> after):
  hin case_marking (postpositional, extend forward):  exact 1,941->2,091  F1 .748->.786  AER .252->.214
  arb case_marking (prepositional,  extend backward): exact 44,913->45,280 F1 .881->.886 AER .119->.114
  eng articles      (prenominal,    extend backward): exact 34,445->50,624 F1 .632->.741 AER .368->.259
All three: precision held roughly flat (a modest, expected cost) while recall/F1/AER improved — net gain,
not a wash, in three languages with opposite word orders. See internal-docs/llm-align-experiment-plan.md.

DIRECTION_FEATURES only lists categories with a *validated* direction signal AND a *validated* real
undershoot (checked this session). `possession_affix`/`tam_auxiliary`/`tam_affix`/`subject_indexing` are
deliberately absent — extending those needs its own gold check first, not an assumption this mechanism
generalizes automatically just because the Grambank shape is similar.

Ships as its OWN opt-in method layer (`align_spanext_<iso>_<BOOK>.jsonl`), the same additive-union
precedent as `residual`/`llm` (compact_align.py's LAYER_METHODS, export_lex.py's `_METHODS`) — never
touches the base chain's own eflomal/gloss files, and a consumer that ignores it keeps today's exact
behavior. Wire it in explicitly (e.g. `merge_align --methods eflomal,gloss,spanext`,
`export_lex --methods eflomal,gloss,gapfill,spanext`) once you've decided to trust it for a language.

    python3 -m lexeme_aligner.span_extension --iso hinirv --publish-iso hin --usj-dir <dir> --nt \\
        --methods eflomal,gloss
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.analyze_language import analyze
from lexeme_aligner.config import OUT, PRIOR_PACK
from lexeme_aligner.gapfill import load_priors
from lexeme_aligner.grambank_fetch import FEATURES as GRAMBANK_FEATURES
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.refs import encode
from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS, build_corpus
from lexeme_aligner.target_stopwords import StopwordFilter
from lexeme_aligner.usj_source import tokenize
from lexeme_aligner.versification import remapper

# risk_key (from analyze_language.RISK_RULES) -> the grambank_fetch.FEATURES direction pair that tells us
# which side of the span the missing word falls on. [0] = "before" (extend backward from the span's own
# minimum position), [1] = "after" (extend forward from the span's own maximum position).
DIRECTION_FEATURES = {
    "case_marking": "adposition_order",
    "articles": "article_order",
}


def load_grambank_raw(publish_iso: str, path=None) -> dict[str, str] | None:
    from lexeme_aligner.analyze_language import load_grambank
    return load_grambank(publish_iso, path)


def direction_for(grambank: dict[str, str], feature_group: str) -> str | None:
    """"before" / "after" / None (mixed, ambiguous, or absent — never guess)."""
    before_id, after_id = GRAMBANK_FEATURES[feature_group]
    before = grambank.get(before_id) == "1"
    after = grambank.get(after_id) == "1"
    if before and not after:
        return "before"
    if after and not before:
        return "after"
    return None


def _books(a) -> list[str]:
    return (OT_BOOKS + NT_BOOKS if a.all else OT_BOOKS if a.ot else NT_BOOKS if a.nt
            else [b.upper() for b in (a.book or ["MAT"])])


def extend_spans(iso: str, publish_iso: str, usj_dir: Path, books: list[str], out_dir: Path = OUT,
                 methods: tuple[str, ...] = ("eflomal", "gloss"), prior_pack: Path = PRIOR_PACK
                 ) -> tuple[dict[str, list[dict]], dict]:
    """{BOOK: [verse record, ...]} of ONLY the pairs that got widened, plus stats. Never mutates the base
    chain's own jsonl — this is a separate, additive layer (see module docstring)."""
    lex_pos, _ = load_priors(prior_pack)
    grambank = load_grambank_raw(publish_iso)
    stats: collections.Counter = collections.Counter()
    if grambank is None:
        return {}, {"skipped": "no Grambank coverage for this language"}

    # Which (risk, pos) combinations are ACTUALLY flagged for this base-chain run, and in which direction —
    # reuses analyze_language's own anomaly detection so this mechanism never fires on a category the
    # phase-1 audit itself wouldn't flag as worth checking.
    report = analyze(iso, publish_iso, out_dir, prior_pack, method=methods[0])
    active: dict[str, str] = {}                          # pos -> direction (first matching finding wins)
    for f in report.get("findings", []):
        risk, pos = f.get("risk"), f.get("pos")
        if risk not in DIRECTION_FEATURES or pos in active:
            continue
        d = direction_for(grambank, DIRECTION_FEATURES[risk])
        if d:
            active[pos] = d
    if not active:
        return {}, {"skipped": "no flagged (pos, direction) combination for this language", **dict(stats)}

    heb = HebrewSource()
    recs = build_corpus(books, usj_dir, heb, remap=remapper(iso, str(usj_dir)))
    lexeme_of: dict[int, dict[int, str]] = {}             # ref -> h_idx -> lexeme (for POS lookup)
    verse_toks: dict[int, list[str]] = {}
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        verse_toks[ref] = list(r.toks)
        lexeme_of[ref] = {t.idx: t.lexeme for t in r.heb}

    stop = StopwordFilter(publish_iso, str(usj_dir))

    # Build ONE unified per-(ref, h_idx) view across methods first (first method in `methods` order
    # wins a contested h_idx — the same "first wins" convention merge_align/compact_align already use),
    # THEN decide extensions and "claimed" positions from that single view. Scanning each method's own
    # jsonl independently (an earlier version of this function did) computes "claimed" separately per
    # method and can process the same h_idx twice under two different claimed-sets when eflomal and
    # gloss both cover it — an inconsistent, method-order-dependent result, not a real per-language
    # finding. Caught this live comparing an isolated single-method test against the real multi-method
    # run: they disagreed for Arabic specifically because of this bug, not because nouns behave
    # differently from names.
    unioned: dict[int, dict[int, dict]] = collections.defaultdict(dict)   # ref -> h_idx -> pair
    meta: dict[int, dict] = {}                                            # ref -> {book, chapter, verse}
    for m in methods:
        for fp in tag_files(out_dir, m, iso):
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    ref = rec["ref"]
                    meta.setdefault(ref, {"book": rec["book"], "chapter": rec["chapter"], "verse": rec["verse"]})
                    verse = unioned[ref]
                    for p in rec["pairs"]:
                        if p.get("t_idx") and p["h_idx"] not in verse:
                            verse[p["h_idx"]] = p

    out: dict[str, list[dict]] = collections.defaultdict(list)
    for ref, verse in unioned.items():
        toks = verse_toks.get(ref)
        if not toks:
            continue
        claimed: set[int] = set()
        for p in verse.values():
            claimed |= set(p["t_idx"])
        widened = []
        for p in verse.values():
            if not p.get("content"):
                continue
            pos = lexeme_of.get(ref, {}).get(p["h_idx"]) and lex_pos.get(lexeme_of[ref][p["h_idx"]])
            direction = active.get(pos)
            if not direction:
                continue
            t_idx = sorted(p["t_idx"])
            cand = t_idx[0] - 1 if direction == "before" else t_idx[-1] + 1
            if cand < 0 or cand >= len(toks) or cand in claimed:
                continue
            if not stop.is_function(toks[cand]):
                continue
            new_t_idx = sorted(t_idx + [cand])
            new_target = " ".join(toks[j] for j in new_t_idx)
            ext = dict(p)
            ext.update(t_idx=new_t_idx, target=new_target, method="spanext",
                      prior=f"spanext_{pos}_{direction}")
            widened.append(ext)
            claimed.add(cand)
            stats[f"extended_{pos}"] += 1
        if widened:
            m_ref = meta[ref]
            out[m_ref["book"]].append({"ref": ref, "book": m_ref["book"], "chapter": m_ref["chapter"],
                                       "verse": m_ref["verse"], "pairs": widened})
    stats["active_pos_direction"] = len(active)
    return dict(out), dict(stats)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="base-chain edition tag (align_<method>_<iso>_*.jsonl)")
    ap.add_argument("--publish-iso", required=True, help="bare published iso, for the Grambank lookup")
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--book", action="append")
    ap.add_argument("--nt", action="store_true")
    ap.add_argument("--ot", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--methods", default="eflomal,gloss",
                    help="base-chain methods to read pairs from (comma-sep)")
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)

    books = _books(a)
    methods = tuple(m.strip() for m in a.methods.split(","))
    by_book, stats = extend_spans(a.iso, a.publish_iso, a.usj_dir, books, a.out, methods, a.prior_pack)
    if "skipped" in stats:
        print(f"[span_extension] {a.iso}: {stats['skipped']}", file=sys.stderr)
        return 0
    n_pairs = sum(len(rec["pairs"]) for recs in by_book.values() for rec in recs)
    for book, recs in by_book.items():
        recs.sort(key=lambda r: (r["chapter"], r["verse"]))
        dest = a.out / f"align_spanext_{a.iso}_{book}.jsonl"
        with dest.open("w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[span_extension] {a.iso}: {dict(stats)} · {n_pairs} pair(s) widened across {len(by_book)} "
         f"book(s) → align_spanext_{a.iso}_<BOOK>.jsonl", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
