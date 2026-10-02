"""Check our spine sense numbers (lexeme, stem, sense) against the UBS Dictionary of Biblical Hebrew (CC BY-SA 4.0,
github.com/ubsicap/ubs-open-license/dictionaries/hebrew) — a per-occurrence sense-tagged OT (288k references).

The UBS file is READ from --ubs (default pipeline/work/ubs-dictionary/, gitignored); nothing of it is copied into the repo
or any published tree. Only aggregate numbers are reported/written.

Reference format: 14 digits BBBCCCVVV + 5-digit word slot W (book numbers = Protestant OT order). SDBH numbers WORDS with
prefixes split but pronominal suffixes NOT split, while our spine also splits suffixes, and W is 2 x the 1-based slot, so
W cannot be used as an index into our tokens. Instead a reference is bound to the token(s) of the SAME VERSE whose Strong's
rollup is in the entry's StrongCodes; `unique` = exactly one such token (used for the headline), otherwise the candidate
nearest the expected slot (idx >= n-1, n = W/2) is used and reported separately.

    python3 pipeline/scripts/adhoc/check_senses_vs_ubs.py
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import random
import re
import sqlite3
import sys
from pathlib import Path

BOOKS = "GEN EXO LEV NUM DEU JOS JDG RUT 1SA 2SA 1KI 2KI 1CH 2CH EZR NEH EST JOB PSA PRO ECC SNG ISA JER LAM EZK DAN HOS JOL AMO OBA JON MIC NAM HAB ZEP HAG ZEC MAL".split()


def load_refs(path: Path):
    """[(book, ch, v, n_slot, strongs, main_id, lex_id)] for every 14-digit reference."""
    d = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for e in d:
        S = {int(re.sub(r"\D", "", s)) for s in (e["StrongCodes"] or []) if re.sub(r"\D", "", s)}
        for bf in e["BaseForms"] or []:
            for m in bf["LEXMeanings"] or []:
                for r in m.get("LEXReferences") or []:
                    if len(r) != 14:
                        continue
                    W = int(r[9:])
                    out.append((BOOKS[int(r[:3]) - 1], int(r[3:6]), int(r[6:9]), W, S, e["MainId"], m["LEXID"]))
    return out


def load_spine(db_path: Path):
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    verse = collections.defaultdict(list)
    for b, c, v, i, lx, st, stem, sense, ic in db.execute(
            "select book,chapter,verse,idx,lexeme,strong,stem,sense,is_content from spine_words where lexeme like 'hbo:%'"):
        verse[(b, c, v)].append({"idx": i, "lexeme": lx, "strong": st, "stem": stem or "", "sense": sense, "content": ic})
    return verse


def bind_shared(ubs_path, spine_path, allowed=("unique", "anchored")):
    """Bind with lexeme_aligner.ubs_senses (the production binding) and attach OUR sense for the comparison.
    key -> (lexeme, (stem, sense), main id, sense id, unique?). Tokens without a sense of ours are counted and skipped."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from lexeme_aligner import ubs_senses as us
    senses, refs, entry = us.load_dictionary(ubs_path)
    bound, st = us.bind_all(refs, entry, spine_path)
    ours = {}
    db = sqlite3.connect(f"file:{spine_path}?mode=ro", uri=True)
    for b, c, v, i, lx, stem, sense in db.execute(
            "select book,chapter,verse,idx,lexeme,stem,sense from spine_words where lexeme like 'hbo:%'"):
        ours[(b, c, v, i)] = (lx, stem or "", sense)
    out, stats = {}, collections.Counter(st)
    for key, (lex_id, main_id, how) in bound.items():
        o = ours.get(key)
        if how not in allowed:
            stats["skipped: how=" + how] += 1
            continue
        if not o or o[2] in (None, ""):
            stats["token has no sense of ours (function words and unsensed content)"] += 1
            continue
        out[key] = (o[0], (o[1], o[2]), main_id, lex_id, how == "unique")
    return out, stats


def bind(refs, verse):
    """token key -> (lexeme, our (stem, sense), UBS main id, UBS sense id, unique?)."""
    bound, stats = {}, collections.Counter()
    for b, c, v, W, S, main_id, lex_id in refs:
        toks = verse.get((b, c, v))
        if not toks:
            stats["verse missing"] += 1
            continue
        cand = [t for t in toks if t["strong"] in S and t["content"]]
        if not cand:
            if any(t["strong"] in S for t in toks):
                stats["unmatched: only a non-content token (particle/preposition) has that Strong's"] += 1
            else:
                stats["unmatched: no token with that Strong's in the verse"] += 1
            continue
        n = W // 2
        if len(cand) == 1:
            t, uniq = cand[0], True
            stats["unique"] += 1
            stats[f"slot check: idx-(n-1) in [0,8]" if 0 <= t["idx"] - (n - 1) <= 8 else "slot check: outside [0,8]"] += 1
        else:
            ok = [t for t in cand if t["idx"] >= n - 1] or cand
            t, uniq = min(ok, key=lambda t: abs(t["idx"] - (n - 1))), False
            stats["ambiguous (nearest to slot)"] += 1
        if t["sense"] in (None, ""):
            stats["token has no sense"] += 1
            continue
        key = (b, c, v, t["idx"])
        if key in bound and bound[key][3] != lex_id:
            stats["token claimed by two UBS senses"] += 1
            continue
        bound[key] = (t["lexeme"], (t["stem"], t["sense"]), main_id, lex_id, uniq)
    return bound, stats


def _entropy(counts):
    n = sum(counts)
    return -sum(c / n * math.log(c / n) for c in counts if c) if n else 0.0


def cluster_scores(ours, ubs):
    """homogeneity, completeness (label-agnostic, V-measure ingredients) and the adjusted Rand index of two partitions."""
    n = len(ours)
    cont = collections.Counter(zip(ours, ubs))
    a = collections.Counter(ours)
    b = collections.Counter(ubs)
    h_c, h_k = _entropy(a.values()), _entropy(b.values())
    h_c_given_k = -sum(v / n * math.log(v / b[k]) for (c, k), v in cont.items())
    h_k_given_c = -sum(v / n * math.log(v / a[c]) for (c, k), v in cont.items())
    hom = 1.0 if h_c == 0 else 1 - h_c_given_k / h_c          # each of OUR clusters holds one UBS sense
    com = 1.0 if h_k == 0 else 1 - h_k_given_c / h_k          # each UBS sense sits in one of OUR clusters
    comb2 = lambda x: x * (x - 1) / 2
    sum_c = sum(comb2(v) for v in cont.values())
    sum_a, sum_b = sum(comb2(v) for v in a.values()), sum(comb2(v) for v in b.values())
    tot = comb2(n)
    exp = sum_a * sum_b / tot if tot else 0
    mx = (sum_a + sum_b) / 2
    ari = 1.0 if mx == exp else (sum_c - exp) / (mx - exp)
    return hom, com, ari


def pair_counts(ours, ubs):
    """(pairs together in both, pairs together in ours, pairs together in UBS, all pairs) — same-sense pair relation."""
    cont = collections.Counter(zip(ours, ubs))
    c2 = lambda x: x * (x - 1) // 2
    return (sum(c2(v) for v in cont.values()), sum(c2(v) for v in collections.Counter(ours).values()),
            sum(c2(v) for v in collections.Counter(ubs).values()), c2(len(ours)))


def within_stem(bound, only_unique=True, min_tokens=10):
    """Group by (lexeme, stem): compare OUR sense number with the UBS sense inside each group — the sense numbers on
    their own, with binyan held fixed. Returns pooled pair precision/recall and token-weighted hom/com/ARI."""
    groups = collections.defaultdict(list)
    for key, (lx, (stem, sense), main_id, lex_id, uniq) in bound.items():
        if only_unique and not uniq:
            continue
        groups[(lx, stem)].append((sense, lex_id))
    tp = op = up = allp = 0
    rows = []
    for g, items in groups.items():
        if len(items) < min_tokens:
            continue
        ours = [i[0] for i in items]
        ubs = [i[1] for i in items]
        if len(set(ours)) < 2 and len(set(ubs)) < 2:
            continue
        t, o, u, a_ = pair_counts(ours, ubs)
        tp, op, up, allp = tp + t, op + o, up + u, allp + a_
        hom, com, ari = cluster_scores(ours, ubs)
        rows.append({"group": "/".join(g), "n": len(items), "our_senses": len(set(ours)), "ubs_senses": len(set(ubs)),
                     "hom": hom, "com": com, "ari": ari})
    tot = sum(r["n"] for r in rows)
    w = lambda k: sum(r[k] * r["n"] for r in rows) / tot if tot else float("nan")
    # baseline: what if we had assigned EVERY token the same sense (our current 97% behaviour)?
    return {"groups": len(rows), "tokens": tot,
            "pair_precision_same_ours_is_same_ubs": tp / op if op else None,
            "pair_recall_same_ubs_is_same_ours": tp / up if up else None,
            "share_of_pairs_ubs_says_same": up / allp if allp else None,
            "share_of_pairs_ours_says_same": op / allp if allp else None,
            # the other side of the same table: pairs WE put in different senses — how many does UBS also separate?
            "pair_precision_different_ours_is_different_ubs": ((allp - op - up + tp) / (allp - op)) if allp > op else None,
            "share_of_pairs_ubs_says_different": (allp - up) / allp if allp else None,
            "hom": w("hom"), "com": w("com"), "ari": w("ari")}, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ubs", type=Path, default=Path("pipeline/work/ubs-dictionary/UBSHebrewDic-v0.9.3-en.JSON"))
    ap.add_argument("--spine", type=Path, default=Path("pipeline/lexeme-spine.db"))
    ap.add_argument("--out", type=Path, default=Path("pipeline/work/ubs-dictionary/sense_check.json"))
    a = ap.parse_args(argv)
    verse = load_spine(a.spine)
    bound, stats = bind_shared(a.ubs, a.spine)
    print(f"bound tokens with a sense of ours {len(bound)}  {dict(stats)}", file=sys.stderr)

    by_lex = collections.defaultdict(list)
    for key, (lx, ours, main_id, lex_id, uniq) in bound.items():
        by_lex[lx].append((ours, lex_id, uniq))
    # our whole-spine sense inventory per lexeme (for the inventory comparison)
    inv = collections.defaultdict(set)
    for toks in verse.values():
        for t in toks:
            if t["content"] and t["sense"] not in (None, ""):
                inv[t["lexeme"]].add((t["stem"], t["sense"]))
    res = {"stats": dict(stats), "bound_tokens": len(bound)}
    rng = random.Random(0)
    for label, only_unique in (("unique_only", True), ("all_bound", False)):
        rows = []
        for lx, items in by_lex.items():
            if only_unique:
                items = [i for i in items if i[2]]
            if len(items) < 10:
                continue
            ours = [o for o, _u, _q in items]
            ubs = [u for _o, u, _q in items]
            stems = [o[0] for o in ours]
            n_ours, n_ubs = len(set(ours)), len(set(ubs))
            if n_ubs < 2 and n_ours < 2:
                continue                                     # nothing to compare on either side
            hom, com, ari = cluster_scores(ours, ubs)
            shuf = ubs[:]
            rng.shuffle(shuf)
            _h, _c, ari0 = cluster_scores(ours, shuf)
            _h2, com_stem, ari_stem = cluster_scores(stems, ubs)
            rows.append({"lexeme": lx, "n": len(items), "our_senses": n_ours, "ubs_senses": n_ubs,
                         "hom": hom, "com": com, "ari": ari, "ari_shuffled": ari0, "ari_stem_only": ari_stem})
        def wmean(k, sel=rows):
            tot = sum(r["n"] for r in sel)
            return sum(r[k] * r["n"] for r in sel) / tot if tot else float("nan")
        multi = [r for r in rows if r["our_senses"] >= 2 and r["ubs_senses"] >= 2]
        under = [r for r in rows if r["our_senses"] == 1 and r["ubs_senses"] >= 2]
        over = [r for r in rows if r["our_senses"] >= 2 and r["ubs_senses"] == 1]
        res[label] = {
            "lexemes_compared": len(rows), "tokens": sum(r["n"] for r in rows),
            "we_one_sense_UBS_split": {"lexemes": len(under), "tokens": sum(r["n"] for r in under)},
            "we_split_UBS_one_sense": {"lexemes": len(over), "tokens": sum(r["n"] for r in over)},
            "both_split": {"lexemes": len(multi), "tokens": sum(r["n"] for r in multi),
                           "hom": wmean("hom", multi), "com": wmean("com", multi), "ari": wmean("ari", multi),
                           "ari_shuffled": wmean("ari_shuffled", multi), "ari_stem_only": wmean("ari_stem_only", multi)},
            "all": {"hom": wmean("hom"), "com": wmean("com"), "ari": wmean("ari")},
        }
        if label == "unique_only":
            top = sorted(multi, key=lambda r: -r["n"])[:25]
            res["both_split_top25_by_tokens"] = top
    for label, only_unique in (("within_lexeme_stem_unique", True), ("within_lexeme_stem_all", False)):
        summary, rows = within_stem(bound, only_unique)
        res[label] = summary
        if only_unique:
            both = [r for r in rows if r["our_senses"] >= 2 and r["ubs_senses"] >= 2]
            res["within_stem_both_split"] = {
                "groups": len(both), "tokens": sum(r["n"] for r in both),
                "hom": sum(r["hom"] * r["n"] for r in both) / max(1, sum(r["n"] for r in both)),
                "com": sum(r["com"] * r["n"] for r in both) / max(1, sum(r["n"] for r in both)),
                "ari": sum(r["ari"] * r["n"] for r in both) / max(1, sum(r["n"] for r in both))}
            res["within_stem_top20"] = sorted(both, key=lambda r: -r["n"])[:20]
    a.out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("both_split_top25_by_tokens", "within_stem_top20")},
                     indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
