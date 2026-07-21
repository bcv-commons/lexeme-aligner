"""Merge / ensemble — reconcile per-method alignments into `method=merged` with agreement confidence.

Reads `align_<m>_<iso>_*.jsonl` for each present method and, per (verse, Hebrew token), takes the UNION
of what the methods aligned: the target with the most method-votes wins (ties → method priority), and
agreement (≥2 methods on the same target) marks the high-confidence core (score→0.97, so it counts as
`hi_conf` downstream). Singletons keep their own method's score.

  union coverage ≥ any single method  ·  agreement = the precise subset  ·  disagreement is visible
  (`voters`/`agree` on each pair). Writes `align_merged_<iso>_*.jsonl` (standard schema) → consumable by
  `export_lex --method merged` and `benchmark --method merged` with no further plumbing.

    python3 -m lexeme_aligner.merge_align --iso fra                 # auto-detect present methods
    python3 -m lexeme_aligner.merge_align --iso fra --methods eflomal,gloss
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.config import OUT, PRIOR_PACK

# trust order for tie-breaks (higher wins): eflomal's intersection core > gloss dict > gapfill > IBM-1
PRIORITY = {"eflomal": 3, "gloss": 2, "gapfill": 1, "stat": 0}
_AGREE_SCORE = 0.97          # ≥2 methods agree → high-confidence (≥ export_lex _HI_SCORE 0.9)


def _present_methods(iso: str, out_dir: Path) -> list[str]:
    found = {p.name.split("_")[1] for p in out_dir.glob(f"align_*_{iso}_*.jsonl")}
    return [m for m in ("eflomal", "gloss", "gapfill", "stat") if m in found]


def _norm(target: str | None) -> str:
    return (target or "").strip().lower()


def _tier(mode: str, p: dict) -> str:
    if mode == "eflomal":
        return f"score {p.get('score')}"
    if mode == "gloss":
        return p.get("method", "?")
    if mode == "gapfill":
        return p.get("prior", "?")                          # 'strong' or 'name' — no embedding tier now
    return "all"


def load_trust(path: Path, pos_pack: Path):
    """→ (weight[(mode,pos,tier)], mode_default[mode], pos_map[lexeme]). Empirical universal weights."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    weight = {(r["mode"], r["pos"], r["tier"]): r["weight"] for r in rows}
    md: dict[str, list] = collections.defaultdict(list)               # backoff: mode's median-ish weight
    for r in rows:
        md[r["mode"]].append(r["weight"])
    mode_default = {m: (sorted(ws)[len(ws) // 2] if ws else 0.1) for m, ws in md.items()}
    pos_map = {}
    if Path(pos_pack).exists():
        import pyarrow.parquet as pq
        if "pos" in pq.read_schema(pos_pack).names:
            pos_map = {r["lexeme"]: r["pos"] for r in pq.read_table(pos_pack, columns=["lexeme", "pos"]).to_pylist()}
    return weight, mode_default, pos_map


def load_contest_rule(path: Path) -> dict:
    """{(eflomal_tier, gloss_tier): 'ef'|'gl'} — the LOO-proven universal disagreement rule."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {tuple(k.split(" | ")): v for k, v in raw.items()}


def _contest_pick(mp: dict, rule: dict):
    """Proven standard: eflomal+gloss agree → trust; disagree → rule[(ef_tier,gl_tier)] (default ef);
    only one present → it; neither → the gapfill. Light-lexeme gloss pairs (semantically general
    source lexemes with low cross-lingual target dominance) don't vote — kept in the gloss pool
    but not used for merge decisions. Returns (winning_pair, voters, score)."""
    ef, gl = mp.get("eflomal"), mp.get("gloss")
    if gl and gl.get("light"):
        gl = None
    if ef and gl:
        if _norm(ef.get("target")) == _norm(gl.get("target")):
            return ef, ["eflomal", "gloss"], _AGREE_SCORE
        side = rule.get((_tier("eflomal", ef), _tier("gloss", gl)), "ef")
        win = ef if side == "ef" else gl
        return win, ["eflomal", "gloss"], (win.get("score") or 0.5)
    if ef:
        return ef, ["eflomal"], (ef.get("score") or 0.5)
    if gl:
        return gl, ["gloss"], (gl.get("score") or 0.5)
    gf = mp.get("gapfill")                                       # neither eflomal nor gloss had a signal
    return gf, ["gapfill"], (gf.get("score") or 0.0)


def merge(iso: str, methods: list[str], out_dir: Path, trust=None, pos_map=None, mode_default=None,
          contest=None):
    # verses[ref] = {book, chapter, verse, h: {h_idx: {method: pair}}}
    verses: dict[int, dict] = {}
    per_method_pairs: collections.Counter = collections.Counter()   # content pairs each method aligned
    for m in methods:
        files = sorted(out_dir.glob(f"align_{m}_{iso}_*.jsonl"))
        for fp in files:
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    v = verses.setdefault(rec["ref"], {"book": rec["book"], "chapter": rec["chapter"],
                                                        "verse": rec["verse"], "h": {}})
                    for p in rec["pairs"]:
                        v["h"].setdefault(p["h_idx"], {})[m] = p
                        if p.get("content"):
                            per_method_pairs[m] += 1

    by_book: dict[str, list] = collections.defaultdict(list)
    agree_n = collections.Counter()                                 # agreement histogram (content)
    merged_content = 0
    for ref, v in verses.items():
        pairs = []
        for h_idx, mp in v["h"].items():
            if contest is not None:                             # PROVEN standard (tier-only, LOO-validated)
                win, voters, sc = _contest_pick(mp, contest)
                if win is None:
                    continue
                wp = dict(win)
                wp.update(method="merged", agree=len(voters), voters=voters, score=round(sc, 3))
                pairs.append(wp)
                if wp.get("content"):
                    merged_content += 1
                    agree_n[len(voters)] += 1
                continue
            if trust is not None:
                # TRUST-WEIGHTED vote (universal rules): each mode's pair votes with its empirical
                # (mode × pos × tier) weight; agreement sums weights. name/noun-hi-conf dominate;
                # eflomal-0.6 / gloss-head / gapfill barely count — the cutoff, learned not hand-set.
                pos = (pos_map or {}).get(next(iter(mp.values())).get("lexeme"), "?")
                wsum: collections.Counter = collections.Counter()
                contrib: dict[str, list] = collections.defaultdict(list)
                for m, p in mp.items():
                    w = trust.get((m, pos, _tier(m, p)), (mode_default or {}).get(m, 0.1))
                    t = _norm(p.get("target"))
                    wsum[t] += w
                    contrib[t].append((m, w, p))
                best = max(wsum, key=lambda t: wsum[t])
                win_m, _w, wp0 = max(contrib[best], key=lambda x: x[1])   # strongest single backer's fields
                voters = sorted(m for m, _, _ in contrib[best])
                wp = dict(wp0)
                wp.update(method="merged", agree=len(contrib[best]), voters=voters,
                          score=round(min(0.99, wsum[best]), 3))
                pairs.append(wp)
                if wp.get("content"):
                    merged_content += 1
                    agree_n[len(contrib[best])] += 1
                continue
            votes: collections.Counter = collections.Counter()
            for m, p in mp.items():
                votes[_norm(p.get("target"))] += 1
            # winner: most votes; DISAGREEMENT broken by the confidence of the backing pair, then method
            # priority. (Empirically the dictionary/gloss exact-match top-1 is ≥ eflomal's, so blindly
            # preferring eflomal on a tie discards a real gain — let the score decide first.)
            def _tgt_key(t):
                backing = [(m, p) for m, p in mp.items() if _norm(p.get("target")) == t]
                return (votes[t], max((p.get("score") or 0.0) for _, p in backing),
                        max(PRIORITY.get(m, 0) for m, _ in backing))
            best = max(votes, key=_tgt_key)
            voters = sorted(m for m, p in mp.items() if _norm(p.get("target")) == best)
            win_m = max(voters, key=lambda m: PRIORITY.get(m, 0))
            wp = dict(mp[win_m])                                     # inherit the winning method's fields
            agree = votes[best]
            wp.update(method="merged", agree=agree, voters=voters,
                      score=_AGREE_SCORE if agree >= 2 else (wp.get("score") or 0.0))
            pairs.append(wp)
            if wp.get("content"):
                merged_content += 1
                agree_n[agree] += 1
        by_book[v["book"]].append({"ref": ref, "book": v["book"], "chapter": v["chapter"],
                                   "verse": v["verse"], "pairs": pairs})

    for book, recs in by_book.items():
        recs.sort(key=lambda r: (r["chapter"], r["verse"]))
        dest = out_dir / f"align_merged_{iso}_{book}.jsonl"
        with dest.open("w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return per_method_pairs, merged_content, agree_n, len(by_book)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--methods", default=None, help="comma-sep (default: auto-detect present methods)")
    ap.add_argument("--trust", type=Path, default=None,
                    help="trust matrix json (trust_profile) → empirical (mode×pos×tier)-WEIGHTED vote")
    ap.add_argument("--contest-rule", type=Path, default=None,
                    help="the PROVEN standard: LOO-validated tier-only disagreement rule (contest_rule.json)")
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK, help="pos lookup for --trust")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    methods = ([m.strip() for m in args.methods.split(",")] if args.methods
               else _present_methods(args.iso, args.out))
    if len(methods) < 2:
        raise SystemExit(f"merge needs ≥2 methods; found {methods or 'none'} for {args.iso} "
                         f"— run more of `run_pilot --method …` first")
    trust = pos_map = mode_default = contest = None
    if args.contest_rule:
        contest = load_contest_rule(args.contest_rule)
        print(f"[merge] contest-rule ({len(contest)} tier-pairs from {args.contest_rule})", file=sys.stderr)
    elif args.trust:
        trust, mode_default, pos_map = load_trust(args.trust, args.prior_pack)
        print(f"[merge] trust-weighted ({len(trust)} cells from {args.trust})", file=sys.stderr)
    per_method, merged, agree_n, nbooks = merge(args.iso, methods, args.out, trust, pos_map, mode_default,
                                                contest)

    print(f"[merge] {args.iso}: methods {methods} over {nbooks} book(s)", file=sys.stderr)
    for m in methods:
        print(f"   {m:9} {per_method[m]:>7} content pairs", file=sys.stderr)
    base = max((per_method[m] for m in methods), default=0)
    gain = f"+{100*(merged-base)/base:.1f}%" if base else "n/a"
    hi = sum(n for a, n in agree_n.items() if a >= 2)
    print(f"   {'merged':9} {merged:>7} content pairs  (union coverage {gain} vs best single; "
          f"{hi} agreement-backed = {100*hi/max(1,merged):.1f}% hi-conf core)", file=sys.stderr)
    print(f"   agreement histogram (n methods → pairs): {dict(sorted(agree_n.items()))}", file=sys.stderr)
    print(f"   → align_merged_{args.iso}_*.jsonl  (export_lex/benchmark --method merged)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
