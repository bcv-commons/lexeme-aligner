"""Function-word window rule: a free pass that settles function-word links after the statistical chain (2026-11-28).

After bible-song-builder's window rule (their note of 2026-11-27: conjunctions placed between the links of the content words around
them, with a hand-made word list per language), generalised to every source function token and to lists learned from the edition's
own links, so it needs nothing per language.

For each run of source function tokens between two linked content tokens (or a verse edge), in source order:
  window  = the target tokens between the two content links (exclusive), not held by any content link; empty when the links cross.
  list    = per function lexeme, the target forms the chain links it to most often in this edition and scope: >= MIN_N links,
            >= MIN_SHARE of the lexeme's links, and the form linked to function tokens in >= MIN_FN of all its links.
  per lexeme in the run: as many listed forms in the window as the run has tokens of it -> link them in order; otherwise no answer.
The rule never removes a link ("no form of its own" is usually a function unit inside a token a content link already holds, and
taking that as "none" cost 15 points of gold function F1 on Arabic and Finnish).

Output: align_fnrule_<tag>_<BOOK>.jsonl, one pair per placed token (method "fnrule", prior "window", `agrees` = the chain's resolved
link was the same). compact_align._resolve: where the chain's link is the same, the chain's pair stays the winner and the rule's
agreement shows in `agree` (conf); where it differs, the rule wins (method char 'w') and the chain's pair becomes the alternative.

Measured (pipeline/work/split-measure/fn_pass.py, internal-docs/split-generator-plan-2026-11-28.md): function-word stability across
runs +0.7..+5 points; gold function F1 +0.1..+0.5 points (Arabic Clear, Finnish HELFI; whole words and split); where the rule differs
from the chain, gold backs the rule 42-57% vs the chain 27-29%; where they agree, 88-92% exact.

On a parts view (target_parts) the tokens are the view's parts, so affix units are placed directly; on a source-parts run the
spine's function parts (endings, augments ...) are function tokens like any other. Run it after residual, on the same out folder.

    python -m lexeme_aligner.function_rule --iso TAG --usj-dir DIR (--all|--ot|--nt|--book B) [--methods spanext,eflomal,gloss,gapfill]
        [--out DIR]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.config import OUT

MIN_N = 3
MIN_SHARE = 0.1
MIN_FN = 0.5
BASE_METHODS = "spanext,eflomal,gloss,gapfill"
VERSION = "1"


def learn(recs: dict, links: dict) -> dict[str, set[str]]:
    """{function lexeme: set of target forms} from the resolved links (see the module docstring)"""
    by = collections.defaultdict(collections.Counter)
    fn_f, all_f = collections.Counter(), collections.Counter()
    for (b, c, v, h), ts in links.items():
        rec = recs.get((b, c, v))
        if rec is None or h >= len(rec.heb):
            continue
        for i in ts:
            if i >= len(rec.toks):
                continue
            f = rec.toks[i].lower()
            all_f[f] += 1
            if not rec.heb[h].is_content:
                fn_f[f] += 1
                by[rec.heb[h].lexeme][f] += 1
    out = {}
    for lex, cf in by.items():
        n = sum(cf.values())
        out[lex] = {f for f, k in cf.items() if k >= MIN_N and k / n >= MIN_SHARE and fn_f[f] / all_f[f] >= MIN_FN}
    return out


def place(rec, L: dict[int, tuple], lists: dict[str, set[str]]) -> dict[int, int]:
    """{h_idx: target token} the rule places in one record. L = {h_idx: resolved target tokens}."""
    heb, n = rec.heb, len(rec.heb)
    taken = {i for h, ts in L.items() if heb[h].is_content for i in ts}
    out = {}
    h = 0
    while h < n:
        if heb[h].is_content:
            h += 1
            continue
        j = h
        while j < n and not heb[j].is_content:
            j += 1
        run = list(range(h, j))
        left = next((k for k in range(h - 1, -1, -1) if heb[k].is_content and L.get(k)), None)
        right = next((k for k in range(j, n) if heb[k].is_content and L.get(k)), None)
        h = j
        lo = max(L[left]) if left is not None else -1
        hi = min(min(L[right]) if right is not None else len(rec.toks), len(rec.toks))   # a link past the text (pooled verses) bounds nothing
        if lo >= hi:
            continue
        win = [i for i in range(lo + 1, hi) if i not in taken]
        used = set()
        for lex in dict.fromkeys(heb[x].lexeme for x in run):
            lst = lists.get(lex)
            if not lst:
                continue
            fs = [x for x in run if heb[x].lexeme == lex]
            cand = [i for i in win if i not in used and rec.toks[i].lower() in lst]
            if len(cand) == len(fs):
                for x, i in zip(fs, cand):
                    out[x] = i
                    used.add(i)
    return out


def run(tag: str, usj_dir: Path, books: list[str], out_dir: Path, methods: tuple[str, ...]) -> collections.Counter:
    from lexeme_aligner.align_files import unlink_stale_gz
    from lexeme_aligner.compact_align import _merged_pairs, load_contest_rule
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.refs import encode
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.versification import remapper
    contest = load_contest_rule()
    recs = {(r.book, r.ch, r.v): r for r in build_corpus(books, usj_dir, HebrewSource(), remap=remapper(tag, str(usj_dir)))}
    links = {}
    for b in books:
        for (c, v), hs in _merged_pairs(tag, b, out_dir, methods, contest).items():
            for h, d in hs.items():
                links[(b, c, v, h)] = tuple(d["t_idx"])
    lists = learn(recs, links)
    stat = collections.Counter(lexemes_with_list=sum(1 for v in lists.values() if v))
    by_book = collections.defaultdict(list)
    for key, rec in recs.items():
        if not rec.toks or not rec.heb:
            continue
        L = {h: links[(*key, h)] for h in range(len(rec.heb)) if (*key, h) in links}
        placed = place(rec, L, lists)
        if not placed:
            continue
        pairs = []
        for h, i in sorted(placed.items()):
            t = rec.heb[h]
            agrees = L.get(h) == (i,)
            stat["agrees" if agrees else "changed" if h in L else "filled"] += 1
            pairs.append({"h_idx": h, "lexeme": t.lexeme, "strong": t.strong, "lemma": getattr(t, "lemma", None), "surface": t.surface,
                          "target": rec.toks[i], "t_idx": [i], "score": 0.9 if agrees else 0.6, "method": "fnrule", "content": False,
                          "prior": "window", "agrees": agrees})
        by_book[key[0]].append({"ref": encode(*key), "book": key[0], "chapter": key[1], "verse": key[2], "pairs": pairs})
    stat["function tokens"] = sum(1 for r in recs.values() if r.toks for t in r.heb if not t.is_content)
    for b in books:
        dest = out_dir / f"align_fnrule_{tag}_{b}.jsonl"
        unlink_stale_gz(dest)
        if not by_book.get(b):
            dest.unlink(missing_ok=True)
            continue
        with dest.open("w", encoding="utf-8") as fh:
            for r in by_book[b]:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        stat["files"] += 1
    return stat


def main(argv=None) -> int:
    from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="the edition tag (names the align files)")
    ap.add_argument("--publish-iso", default=None, help="accepted for symmetry with the other chain steps; unused")
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--ot", action="store_true")
    ap.add_argument("--nt", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--book", action="append")
    ap.add_argument("--methods", default=BASE_METHODS, help="the chain's methods whose resolved links the rule reads")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    books = (OT_BOOKS + NT_BOOKS if a.all else OT_BOOKS if a.ot else NT_BOOKS if a.nt else [b.upper() for b in (a.book or ["RUT"])])
    books = [b for b in books if any(Path(a.usj_dir).glob(f"*-{b}.json"))]
    st = run(a.iso, a.usj_dir, books, a.out, tuple(m.strip() for m in a.methods.split(",") if m.strip()))
    print(f"[function_rule] {a.iso}: {dict(st)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
