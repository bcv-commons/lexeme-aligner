"""Universal disagreement rule — learned from PAIRWISE contested win-rates, cross-language + leave-one-out.

The lesson from eval_contested: marginal cell precision is the WRONG signal for "who wins a disagreement"
— that needs the pairwise (eflomal-tier × gloss-tier) contested win-rate. This aggregates those win-rates
across the gold languages into a universal rule (per key: believe eflomal or gloss) and proves it holds
LEAVE-ONE-OUT (rule fit without the test language).

Also does the CATEGORISATION homework: compare a tier-only rule (eflomal-tier × gloss-tier) vs a
POS-keyed rule (bcv-query pos × eflomal-tier × gloss-tier) — does the source-word category help the cutoff?

Config-driven gold set: data/gold_langs.json (add a language by aligning it + one line; auto-skipped
until its jsonl exist). Clear positional gold + karnbibeln lexicon (swk/swe).

    python3 -m lexeme_aligner.contest_rule
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.benchmark import agrees, load_gold_lexicon, norm_surface
from lexeme_aligner.config import OUT, PRIOR_PACK, RESOURCES
from lexeme_aligner.merge_align import _tier
from lexeme_aligner.score_tiers import _gold_clear, _load_pos

_KARN = Path("data/karnbibeln")
_CFG = Path("data/gold_langs.json")
_DEFAULT = {"fra": "clear", "arb": "clear", "eng": "clear", "hau": "clear", "swk": "lexicon", "swe": "lexicon"}
# Config-driven: add a gold language by aligning it (eflomal+gloss) + one line in data/gold_langs.json —
# NO code edit. Languages in the config but not yet aligned auto-skip until their jsonl exist.
_cfg = {k: v for k, v in (json.loads(_CFG.read_text(encoding="utf-8")) if _CFG.exists() else _DEFAULT).items()
        if not k.startswith("_")}
GOLD = {iso: gt for iso, gt in _cfg.items()
        if tag_files(OUT, "eflomal", iso) and tag_files(OUT, "gloss", iso)}
LANGS = list(GOLD)


def _index(iso, method, out_dir):
    idx = {}
    for fp in tag_files(out_dir, method, iso):
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                for p in rec["pairs"]:
                    if p.get("content") and p.get("strong") and (p.get("target") or "").strip():
                        idx[(rec["ref"], p["h_idx"])] = (
                            tuple(norm_surface(w) for w in p["target"].split()),
                            _tier(method, p), p["strong"], p.get("lexeme"))
    return idx


def _judge(iso, res):
    if GOLD[iso] == "clear":
        gold = _gold_clear(iso, res)
        return (lambda ref, s: (f"{ref:08d}", s) in gold,
                lambda ref, s, words: any(w in gold[(f"{ref:08d}", s)] for w in words))
    heb = load_gold_lexicon("karnbibeln", "hebrew", _KARN)
    grk = load_gold_lexicon("karnbibeln", "greek", _KARN)

    def g(s):
        return (heb if s.startswith("H") else grk).get(s)
    return (lambda ref, s: g(s) is not None,
            lambda ref, s, words: agrees([" ".join(words)], g(s)))


def collect(out_dir, res, pos_pack=PRIOR_PACK):
    """per lang → (toks=[(pos, ef_tier, gl_tier, ef_hit, gl_hit)], feats{gold-free})."""
    pos_map = _load_pos(pos_pack)
    data = {}
    for iso in LANGS:
        judged, hit = _judge(iso, res)
        ef, gl = _index(iso, "eflomal", out_dir), _index(iso, "gloss", out_dir)
        toks = []
        both = agree = ef09 = 0
        for key in set(ef) & set(gl):
            e, g = ef[key], gl[key]
            ref, strong = key[0], e[2]
            both += 1
            ef09 += e[1] == "score 0.9"
            if e[0] == g[0]:
                agree += 1
                continue
            if not judged(ref, strong):
                continue
            eh, gh = hit(ref, strong, e[0]), hit(ref, strong, g[0])
            toks.append((pos_map.get(e[3], "?"), e[1], g[1], eh, gh))
        data[iso] = (toks, {"agree_rate": agree / max(1, both), "ef09_share": ef09 / max(1, both),
                            "contested_share": len(toks) / max(1, both)})
    return data


def _key(tok, by):
    pos, et, gt, _e, _g = tok
    return (pos, et, gt) if by == "pos" else (et, gt)


def rule_from(data, by="tier", exclude=None, min_n=15):
    """per key: 'ef' | 'gl' by summed EXCLUSIVE contested wins across langs (default 'ef' — wins most)."""
    agg = collections.defaultdict(lambda: [0, 0])
    for iso, (toks, *_f) in data.items():
        if iso == exclude:
            continue
        for tok in toks:
            _p, _e, _g, eh, gh = tok
            if eh and not gh:
                agg[_key(tok, by)][0] += 1
            elif gh and not eh:
                agg[_key(tok, by)][1] += 1
    return {k: ("gl" if gw > ew and (ew + gw) >= min_n else "ef") for k, (ew, gw) in agg.items()}


def acc(toks, rule, by="tier"):
    n = c = ga = ea = orc = 0
    for tok in toks:
        _p, _e, _g, eh, gh = tok
        n += 1
        c += eh if rule.get(_key(tok, by), "ef") == "ef" else gh
        ga += gh
        ea += eh
        orc += eh or gh
    return n, c, ga, ea, orc


def gold_health(iso, res, out_dir):
    """Distinguish a BAD (positionally-shifted) gold from a real alignment miss — the rus lesson.

    For positional (clear) gold, measure our eflomal output two ways over content tokens the gold judges:
      · POSITIONAL — our surface matches the gold AT THAT EXACT VERSE  (gold[(ref, strong)]).
      · LEXICAL    — our surface is a valid rendering of that strong ANYWHERE (aggregate gold[strong]).
    A large gap (lexical ≫ positional) means our alignment is lexically right but the gold's per-verse
    strong→word pairing is scrambled — i.e. the GOLD is defective, not our alignment (rus: 40% vs 79%,
    gap 39pt; healthy langs ~1-7pt: arb 97/98, spa 88/95). Returns (pos, lex, gap, n) or None (lexicon
    gold has no verse dimension, so the gap is undefined and this diagnostic doesn't apply)."""
    if GOLD.get(iso) != "clear":
        return None
    gold = _gold_clear(iso, res)                              # {(ref8, strong): {surfaces}}
    agg = collections.defaultdict(set)
    for (_ref8, s), surfs in gold.items():
        agg[s] |= surfs
    pos = lex = n = 0
    for (ref, _h), (words, _t, strong, _lex) in _index(iso, "eflomal", out_dir).items():
        key = (f"{ref:08d}", strong)
        if key not in gold:
            continue
        n += 1
        if any(w in gold[key] for w in words):
            pos += 1
        if any(w in agg[strong] for w in words):
            lex += 1
    return (pos / n, lex / n, (lex - pos) / n, n) if n else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--resources", type=Path, default=RESOURCES)
    ap.add_argument("--write", type=Path, default=Path("data/contest_rule.json"))
    ap.add_argument("--oracle-floor", type=float, default=0.5,
                    help="exclude langs whose contested oracle < this (broken gold matching, not alignment)")
    ap.add_argument("--gap-flag", type=float, default=0.2,
                    help="gold-health: flag a lang whose lexical−positional gap ≥ this as BAD (shifted) gold")
    args = ap.parse_args()

    data = collect(args.out, args.resources)
    # SANITY FILTER: a language whose contested ORACLE (best-possible) is implausibly low has BROKEN gold
    # matching (e.g. non-Latin script mangled by norm_surface), not bad alignment — exclude it from the rule
    # rather than pollute it. Empirical guard: don't trust gold we can't even match.
    oracle = {iso: (sum(eh or gh for _p, _e, _g, eh, gh in data[iso][0]) / max(1, len(data[iso][0])))
              for iso in LANGS}
    usable = {iso: data[iso] for iso in LANGS if oracle[iso] >= args.oracle_floor and data[iso][0]}
    excluded = [iso for iso in LANGS if iso not in usable]

    # GOLD-HEALTH diagnostic (the rus lesson, generalised): for every excluded clear-gold lang, is it
    # BAD GOLD (positionally-shifted reference — lexical ≫ positional) or an unmatchable/low-quality one?
    health = {iso: gold_health(iso, args.resources, args.out) for iso in LANGS}

    def _why(iso):
        h = health.get(iso)
        if h and h[2] >= args.gap_flag:
            return (f"BAD GOLD — positionally-shifted reference (our align is right: "
                    f"positional {100*h[0]:.0f}% vs lexical {100*h[1]:.0f}%, gap {100*h[2]:.0f}pt)")
        return "unmatchable / low-quality gold (both positional & lexical low)"

    full = rule_from(usable, by="tier")
    args.write.write_text(json.dumps({f"{k[0]} | {k[1]}": v for k, v in full.items()}, indent=1), encoding="utf-8")

    # gold-health table — first-class every run, so a future defective gold auto-surfaces here.
    clear_h = [(iso, health[iso]) for iso in LANGS if health.get(iso)]
    if clear_h:
        print("\n=== gold health (clear langs) — positional vs lexical match; big gap ⇒ shifted/bad gold ===")
        print(f"  {'lang':5} {'positional':>10} {'lexical':>8} {'gap':>6}  verdict")
        for iso, (p, lx, gp, _n) in sorted(clear_h, key=lambda x: -x[1][2]):
            verdict = "⚠ BAD GOLD (shifted)" if gp >= args.gap_flag else "ok"
            print(f"  {iso:5} {100*p:>9.0f}% {100*lx:>7.0f}% {100*gp:>5.0f}pt  {verdict}")

    print(f"\n=== disagreement rule — contested accuracy (LOO), {len(usable)} usable gold langs ===")
    if excluded:
        print(f"  ⚠ EXCLUDED (oracle < {args.oracle_floor}):")
        for i in excluded:
            print(f"      {i} (oracle {100*oracle[i]:.0f}%) — {_why(i)}")
    print(f"  {'lang':5} {'contested':>9} {'tier(loo)':>10} {'POS×tier(loo)':>14} {'always-ef':>10} {'oracle':>8}")
    tot = collections.Counter()
    for iso in usable:
        toks = data[iso][0]
        n, ct, _, ea, orc = acc(toks, rule_from(usable, "tier", exclude=iso), "tier")
        _, cp, _, _, _ = acc(toks, rule_from(usable, "pos", exclude=iso), "pos")
        tot["n"] += n; tot["tier"] += ct; tot["pos"] += cp; tot["ef"] += ea; tot["orc"] += orc
        print(f"  {iso:5} {n:>9} {100*ct/n:>9.1f}% {100*cp/n:>13.1f}% {100*ea/n:>9.1f}% {100*orc/n:>7.1f}%")
    print(f"  {'ALL':5} {tot['n']:>9} {100*tot['tier']/tot['n']:>9.1f}% {100*tot['pos']/tot['n']:>13.1f}% "
          f"{100*tot['ef']/tot['n']:>9.1f}% {100*tot['orc']/tot['n']:>7.1f}%  ← token-weighted")
    print("  → does POS×tier beat tier-only (the categorisation homework)?  → " + str(args.write))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
