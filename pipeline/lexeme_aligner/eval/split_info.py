"""How much do an arm's links tell about the source? A gold-free measure for words-against-parts runs (2026-11-28).

target_split_check counts links (content / function linked) and their stability. A count rewards any link, right or wrong, and more
target units give more places to link. This measure asks instead what the links are worth: on held-out verses, how well does the
target unit a source token is linked to PREDICT that source token's lexeme?

  - The predicted side is the source text, identical in every arm, so arms with different target units are comparable.
  - unit = the target word for a whole-word link; the part (slot-marked: "ti-", "-a", stem "gaamb") for a link to a part.
  - Two folds by verse: p(lexeme | unit) is counted on one half (add-ALPHA smoothing toward the half's lexeme frequencies) and scored
    on the other; a token with several units takes their mean; an unlinked token gets the lexeme frequency alone.
  - Reported per arm and run: bits per source token gained over the lexeme frequency alone (`info`), for content and function tokens,
    and the same per linked token (`info_linked`: what one link is worth) beside the share linked.

A link to a consistent rendering (Greek ou -> ti-) gains several bits wherever it recurs; a link to a rare whole word, or to a part
that means nothing, gains about none. So a split that only adds links scores no better here.

    python -m lexeme_aligner.eval.split_info --edition zin_wbt --arm words=DIR --arm hand=DIR:SPLIT.parquet [--lexemes 15]
        DIR = an align-text output folder (its runs/run* are read too); SPLIT = the arm's split file.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import sys
import zlib
from pathlib import Path

ALPHA = 1.0
LEMMA: dict[str, str] = {}


def _units(split: Path | None) -> dict:
    """{(book, chapter, verse, token, part): unit string} for the split's divided tokens"""
    if not split:
        return {}
    import pyarrow.parquet as pq
    t = pq.read_table(split)
    if "slot" not in t.column_names:
        return {}
    out = {}
    for r in t.select(["book", "chapter", "verse", "i", "part", "slot", "text"]).to_pylist():
        tx = (r["text"] or "").lower()
        out[(r["book"], r["chapter"], r["verse"], r["i"], r["part"])] = \
            tx + "-" if r["slot"] == "prefix" else "-" + tx if r["slot"] in ("suffix", "infix") else tx
    return out


def tokens(edition: str, usj: Path, run_dir: Path, units: dict, recs=None, books=None) -> list[tuple]:
    """[(verse key, lexeme, is_content, units tuple or ())] for every source token of the text's verses"""
    from lexeme_aligner.compact_align import load_contest_rule
    from lexeme_aligner.target_split_check import _final_links
    links = _final_links(edition, run_dir, books, load_contest_rule())
    out = []
    for key, rec in recs.items():
        if not rec.toks:
            continue
        for h, t in enumerate(rec.heb):
            l = links.get((*key, h))
            us = ()
            if l:
                ts, parts = l
                if parts:
                    us = tuple(units.get((key[0], rec.tref[0], rec.tref[1], i, pt), rec.toks[i].lower() if i < len(rec.toks) else "?")
                               if pt is not None else rec.toks[i].lower() for i, pt in parts)
                else:
                    us = tuple(rec.toks[i].lower() for i in ts if i < len(rec.toks))
            LEMMA.setdefault(t.lexeme, getattr(t, "lemma", None) or t.lexeme)
            out.append((key, t.lexeme, bool(t.is_content), us))
    return out


def info(toks: list[tuple]) -> dict:
    fold = lambda key: zlib.crc32(repr(key).encode()) & 1
    res = collections.defaultdict(lambda: [0.0, 0.0, 0, 0])       # class -> [bits gained, bits gained on linked, n, n linked]
    per_lex = collections.defaultdict(lambda: [0.0, 0, 0])
    for test in (0, 1):
        train = [x for x in toks if fold(x[0]) != test]
        lex = collections.Counter(x[1] for x in train)
        n = sum(lex.values())
        p0 = lambda s: (lex[s] + 1) / (n + len(lex) + 1)
        cu, cus = collections.Counter(), collections.Counter()
        for _k, s, _c, us in train:
            for u in us:
                cu[u] += 1
                cus[(u, s)] += 1
        for k, s, c, us in toks:
            if fold(k) != test:
                continue
            base = p0(s)
            p = sum((cus[(u, s)] + ALPHA * base) / (cu[u] + ALPHA) for u in us) / len(us) if us else base
            g = math.log2(p / base)
            r = res["content" if c else "function"]
            r[0] += g
            r[2] += 1
            if us:
                r[1] += g
                r[3] += 1
            if not c:
                pl = per_lex[s]
                pl[0] += g
                pl[1] += 1
                pl[2] += bool(us)
    out = {}
    for cls, (g, gl, n, nl) in res.items():
        out[cls] = {"info": round(g / max(n, 1), 4), "linked": round(nl / max(n, 1), 4), "info_linked": round(gl / max(nl, 1), 4)}
    tot = [sum(r[i] for r in res.values()) for i in range(4)]
    out["all"] = {"info": round(tot[0] / max(tot[2], 1), 4), "linked": round(tot[3] / max(tot[2], 1), 4),
                  "info_linked": round(tot[1] / max(tot[3], 1), 4)}
    out["_lexemes"] = {s: (round(g, 1), n, k) for s, (g, n, k) in per_lex.items()}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edition", required=True)
    ap.add_argument("--arm", action="append", required=True, help="NAME=DIR[:SPLIT]")
    ap.add_argument("--lexemes", type=int, default=0, help="list the N function lexemes whose info differs most from the first arm")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.target_split_check import _run_dirs
    from lexeme_aligner.versification import remapper
    results, recs = {}, None
    for spec in a.arm:
        name, rest = spec.split("=", 1)
        d, _, sp = rest.partition(":")
        d = Path(d).resolve()
        rec = json.loads((d / "align-text-run.json").read_text(encoding="utf-8"))
        usj = Path(rec["text"]["path"])
        books = sorted({p.stem.split("-", 1)[1] for p in usj.glob("*.json") if p.stem[:2].isdigit()})
        if recs is None:
            recs = {(r.book, r.ch, r.v): r for r in build_corpus(books, usj, HebrewSource(), remapper(a.edition, str(usj)))}
        units = _units(Path(sp) if sp else None)
        runs = [info(tokens(a.edition, usj, rd, units, recs, books)) for rd in _run_dirs(d)]
        avg = {c: {k: round(sum(r[c][k] for r in runs) / len(runs), 4) for k in runs[0][c]} for c in ("content", "function", "all")}
        results[name] = {"runs": [{c: r[c] for c in ("content", "function", "all")} for r in runs], "mean": avg, "_lexemes": runs[0]["_lexemes"]}
        m = avg
        spread = max(r["all"]["info"] for r in runs) - min(r["all"]["info"] for r in runs)
        print(f"{name:14} info/token all {m['all']['info']:.3f} (run spread {spread:.3f})  content {m['content']['info']:.3f}  "
              f"function {m['function']['info']:.3f} | linked content {m['content']['linked']:.1%} function {m['function']['linked']:.1%} | "
              f"info per link content {m['content']['info_linked']:.2f} function {m['function']['info_linked']:.2f}", flush=True)
    if a.lexemes and len(results) > 1:
        names = list(results)
        base = results[names[0]]["_lexemes"]
        for nm in names[1:]:
            cur = results[nm]["_lexemes"]
            diff = sorted(((cur[s][0] - base.get(s, (0, 0, 0))[0], s) for s in cur), reverse=True)
            show = lambda x: f"{LEMMA.get(x[1], x[1])} {x[0]:+.0f}b (n {cur[x[1]][1]}, linked {base.get(x[1], (0, 1, 0))[2]}->{cur[x[1]][2]})"
            print(f"\n{nm} vs {names[0]}, function lexemes, total bits gained:")
            print("  up:   " + "; ".join(show(x) for x in diff[:a.lexemes]))
            print("  down: " + "; ".join(show(x) for x in diff[-a.lexemes:][::-1]))
    if a.json:
        a.json.write_text(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "_lexemes"} for k, v in results.items()},
                                     ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
