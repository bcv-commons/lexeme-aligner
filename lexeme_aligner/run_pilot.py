"""Pilot runner — align books with method (a) gloss-anchored and/or (b) statistical (IBM-1),
report coverage/precision, and compare. Corpus is loaded ONCE and shared.

  # (a) gloss-anchored on Ruth+Genesis
  python3 -m lexeme_aligner.run_pilot --method gloss --book RUT --book GEN --usj-dir <dir>
  # (b) statistical, TRAINED ON THE FULL OT (pass all books — eflomal-style needs the corpus)
  python3 -m lexeme_aligner.run_pilot --method stat --book <all 39 OT> --usj-dir <dir>
  # both + comparison
  python3 -m lexeme_aligner.run_pilot --method both --book RUT --book GEN --usj-dir <dir>

Outputs under aligner/out/ (gitignored): align_<method>_<iso>_<BOOK>.jsonl + report_<method>_<iso>.md
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from lexeme_aligner.gloss_align import NORMALIZERS, Normalizer, align_verse
from lexeme_aligner.gloss_priors import GlossPriors
from lexeme_aligner.hebrew_source import HebToken, HebrewSource, encode
from lexeme_aligner.stat_align import IBM1
from lexeme_aligner.usj_source import read_verses, tokenize

from lexeme_aligner.config import OUT
_HI_METHODS = {"exact", "stem", "name", "multi"}

_BOOK_FILE_NUM = {"GEN": "01", "EXO": "02", "LEV": "03", "NUM": "04", "DEU": "05",
                  "JOS": "06", "JDG": "07", "RUT": "08", "1SA": "09", "2SA": "10",
                  "1KI": "11", "2KI": "12", "1CH": "13", "2CH": "14", "EZR": "15",
                  "NEH": "16", "EST": "17", "JOB": "18", "PSA": "19", "PRO": "20",
                  "ECC": "21", "SNG": "22", "ISA": "23", "JER": "24", "LAM": "25",
                  "EZK": "26", "DAN": "27", "HOS": "28", "JOL": "29", "AMO": "30",
                  "OBA": "31", "JON": "32", "MIC": "33", "NAM": "34", "HAB": "35",
                  "ZEP": "36", "HAG": "37", "ZEC": "38", "MAL": "39"}
OT_BOOKS = list(_BOOK_FILE_NUM)          # 39 OT (Hebrew spine) — captured before NT is merged in
# NT (Greek spine). Clear-Bible gold attestations are NT/SBLGNT-based, so the gold benchmark
# runs here. Numbering continues the internal sequential scheme (filenames are ours).
_NT_FILE_NUM = {"MAT": "40", "MRK": "41", "LUK": "42", "JHN": "43", "ACT": "44", "ROM": "45",
                "1CO": "46", "2CO": "47", "GAL": "48", "EPH": "49", "PHP": "50", "COL": "51",
                "1TH": "52", "2TH": "53", "1TI": "54", "2TI": "55", "TIT": "56", "PHM": "57",
                "HEB": "58", "JAS": "59", "1PE": "60", "2PE": "61", "1JN": "62", "2JN": "63",
                "3JN": "64", "JUD": "65", "REV": "66"}
NT_BOOKS = list(_NT_FILE_NUM)
_BOOK_FILE_NUM.update(_NT_FILE_NUM)       # full map used for target-USJ filename lookup


@dataclass
class VerseRec:
    book: str
    ch: int
    v: int
    heb: list[HebToken]
    text: str
    toks: list[str] = field(default_factory=list)


def build_corpus(books: list[str], usj_dir: Path, heb: HebrewSource, remap=None) -> list[VerseRec]:
    """`remap`: optional versification map (spine KJV ref → target scheme ref) so a non-KJV target (e.g.
    Russian Synodal) is fetched at the right verse. None/protestant → identity (existing langs unchanged)."""
    recs: list[VerseRec] = []
    for book in books:
        usj_path = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
        if not usj_path.exists():
            print(f"[pilot] skip {book}: no target USJ at {usj_path}", file=sys.stderr)
            continue
        target = read_verses(usj_path)
        for ch in heb.chapters(book):
            for v in heb.verses(book, ch):
                tc, tv = (remap(book, ch, v)[1:] if remap else (ch, v))   # target verse for this spine ref
                text = target.get((tc, tv), "")
                recs.append(VerseRec(book, ch, v, heb.verse_tokens(book, ch, v),
                                     text, tokenize(text) if text else []))
    return recs


def _hi(m) -> bool:
    return (m.method in _HI_METHODS
            or (m.method == "stat" and m.score >= 0.3)
            or (m.method == "eflomal" and m.score >= 0.9)    # intersection-backed core
            or (m.method == "gapfill" and m.score >= 0.9))   # always 0.9 — only strong/name ever fire


def run_method(recs: list[VerseRec], align_fn, iso: str, tag: str, out_dir: Path) -> list[dict]:
    by_book: dict[str, list[VerseRec]] = collections.defaultdict(list)
    for r in recs:
        by_book[r.book].append(r)

    results = []
    for book, brecs in by_book.items():
        stats = collections.Counter()
        methods = collections.Counter()
        lex_agg: collections.Counter = collections.Counter()
        sense_agg = collections.defaultdict(collections.Counter)
        samples: list[dict] = []
        missing = 0
        out_path = out_dir / f"align_{tag}_{iso}_{book}.jsonl"
        with out_path.open("w", encoding="utf-8") as fh:
            for rec in brecs:
                content = [t for t in rec.heb if t.is_content and t.strong]
                if not rec.toks:
                    missing += 1
                    continue
                matches = align_fn(rec)
                by_h = {m.h_idx: m for m in matches}
                stats["verses"] += 1
                stats["heb_content"] += len(content)
                stats["aligned_content"] += sum(1 for t in content if t.idx in by_h)
                stats["aligned_content_hi"] += sum(
                    1 for t in content if t.idx in by_h and _hi(by_h[t.idx]))
                stats["target_tokens"] += len(rec.toks)
                stats["target_covered"] += sum(len(m.t_idx) for m in matches)
                for m in matches:
                    methods[m.method] += 1

                pairs = []
                for t in rec.heb:
                    m = by_h.get(t.idx)
                    if not m:
                        continue
                    rendering = " ".join(rec.toks[j] for j in m.t_idx)
                    pairs.append({"h_idx": t.idx, "lexeme": t.lexeme, "strong": t.strong,
                                  "lemma": t.lemma, "stem": t.stem,
                                  "surface": t.surface, "gloss_en": t.gloss_en, "sense": t.sense,
                                  "target": rendering, "t_idx": list(m.t_idx), "score": m.score,
                                  "method": m.method, "content": bool(t.is_content)})
                    if t.is_content:
                        lex_agg[(rendering.lower(), t.strong)] += 1
                    # Anchor is the MACULA `lexeme` (CC-BY/CC0-safe), never BHSA `lex` (CC-BY-NC-SA):
                    # senses_attested must not carry a BHSA-derived key. `t.lexeme` is the MACULA
                    # lang:augmented-strong on the enriched spine, else the derived <strong>|<lemma>.
                    if t.lexeme and t.sense:
                        sense_agg[(t.lexeme, t.stem or "", t.sense)][rendering.lower()] += 1
                fh.write(json.dumps({"ref": encode(book, rec.ch, rec.v), "book": book,
                                     "chapter": rec.ch, "verse": rec.v, "pairs": pairs},
                                    ensure_ascii=False) + "\n")
                if len(samples) < 2 and len(pairs) >= 6:
                    samples.append({"ref": f"{book} {rec.ch}:{rec.v}", "text": rec.text,
                                    "pairs": pairs})
        results.append({"book": book, "stats": stats, "methods": methods, "lex_agg": lex_agg,
                        "sense_agg": sense_agg, "samples": samples, "missing": missing})
    return results


def write_report(results: list[dict], iso: str, tag: str, out_dir: Path) -> Path:
    L = [f"# Aligner pilot — method `{tag}`, `{iso}`", "",
         "| book | verses | Heb content | aligned | **coverage** | hi-conf | target covered |",
         "|---|---|---|---|---|---|---|"]
    for r in results:
        s = r["stats"]
        cov = 100 * s["aligned_content"] / max(1, s["heb_content"])
        hi = 100 * s["aligned_content_hi"] / max(1, s["heb_content"])
        tc = 100 * s["target_covered"] / max(1, s["target_tokens"])
        L.append(f"| {r['book']} | {s['verses']} | {s['heb_content']} | {s['aligned_content']} "
                 f"| **{cov:.1f}%** | {hi:.1f}% | {tc:.1f}% |")
    L.append("")
    for r in results:
        L += [f"## {r['book']}", f"- methods: {dict(r['methods'].most_common())}", ""]
        for smp in r["samples"][:1]:
            L += [f"### sample — {smp['ref']}", f"> {smp['text'][:180]}", "",
                  "| Heb | Strong | sense | EN gloss | → target | method | p |",
                  "|---|---|---|---|---|---|---|"]
            for p in smp["pairs"][:12]:
                L.append(f"| {p['surface']} | {p['strong']} | {p['sense'] or ''} | "
                         f"{p['gloss_en'] or ''} | **{p['target']}** | {p['method']} | {p['score']} |")
            L.append("")
        L.append("### lexeme-alignments preview (surface → strong ×count)")
        L += [f"- `{w}` → {s} ×{n}" for (w, s), n in r["lex_agg"].most_common(10)]
        poly = [(k, c) for k, c in r["sense_agg"].items() if k[2] not in ("", "1")]
        if poly:
            L += ["", "### sense-mining preview (non-dominant senses → attested renderings)"]
            L += [f"- `{lex}` {stem or '-'} sense {sense} → {dict(c.most_common(3))}"
                  for (lex, stem, sense), c in poly[:8]]
        L.append("")
    path = out_dir / f"report_{tag}_{iso}.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def _totals(results: list[dict]) -> tuple[int, int, int]:
    hc = sum(r["stats"]["heb_content"] for r in results)
    al = sum(r["stats"]["aligned_content"] for r in results)
    hi = sum(r["stats"]["aligned_content_hi"] for r in results)
    return hc, al, hi


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--method", choices=["gloss", "stat", "eflomal", "both", "all"], default="gloss")
    ap.add_argument("--eflomal-priors", action="store_true",
                    help="seed eflomal with gloss high-confidence alignments (semi-supervised)")
    ap.add_argument("--book", action="append")
    ap.add_argument("--ot", action="store_true", help="use all 39 OT books (Hebrew spine)")
    ap.add_argument("--nt", action="store_true", help="use all 27 NT books (Greek spine) — gold benchmark")
    ap.add_argument("--all", action="store_true", help="whole Bible (OT then NT — 66 books)")
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--iso", default="ind")
    ap.add_argument("--lang-name", default="Indonesian")
    ap.add_argument("--stat-iters", type=int, default=6)
    ap.add_argument("--gloss-signals", default="morph",
                    help="language-independent signals folded into gloss (comma-sep subset of "
                         "morph=#2 unsupervised morphology, stopwords=#3 target function-word filter, "
                         "cross=#1 cross-lingual span). DEFAULT morph-only: the gold ablation (fra/hin/arb) "
                         "showed #2 is a clean win (coverage +5pt, precision flat) but #3 CRATERS gold-"
                         "coverage in gloss (blocks legit function↔function alignments; it belongs in "
                         "content-only gap-fill) and #1 has no measurable effect (gloss already spans "
                         "multi-word priors). Enable them only to re-ablate.")
    ap.add_argument("--cross-lang", type=Path, default=Path("resources/cross_lang_prior/profile.json"),
                    help="#1 cross-lingual span profile (cross_lang_prior.py)")
    ap.add_argument("--eflomal-stem", action="store_true",
                    help="#2 for eflomal: feed STEMMED target tokens (learned morphology) so inflected "
                         "variants pool into one co-occurrence type; output surfaces stay the raw tokens "
                         "(eflomal aligns by position). A/B this vs the surface default before adopting.")
    ap.add_argument("--anchor", choices=["strong", "lexeme"], default="strong",
                    help="eflomal source-side key: strong (rollup) or lexeme (finer, separates homonyms)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    books = (OT_BOOKS + NT_BOOKS if args.all else OT_BOOKS if args.ot else NT_BOOKS if args.nt
             else [b.upper() for b in (args.book or ["RUT"])])
    args.out.mkdir(parents=True, exist_ok=True)

    heb = HebrewSource()
    from lexeme_aligner.versification import remapper, scheme_of
    usj_dir = str(args.usj_dir)
    remap = remapper(args.iso, usj_dir)                       # scheme auto-detected from the ingested USJ
    if remap:
        print(f"[pilot] versification: {args.iso} = {scheme_of(args.iso, usj_dir)} (auto-detected) "
              f"→ remapping verses to source", file=sys.stderr)
    print(f"[pilot] loading corpus: {len(books)} book(s) …", file=sys.stderr)
    recs = build_corpus(books, args.usj_dir, heb, remap)
    print(f"[pilot] {len(recs)} verses loaded", file=sys.stderr)
    want = {"gloss": args.method in ("gloss", "both", "all"),
            "stat": args.method in ("stat", "both", "all"),
            "eflomal": args.method in ("eflomal", "all")}
    signals = {s.strip() for s in args.gloss_signals.split(",") if s.strip()}
    # #2 learned morphology — ONE stemmer, TWO consumers: gloss's Normalizer AND eflomal's stemmed input.
    # Registered up front so eflomal can reach it too; forms()[0] is the original token, so eflomal in its
    # default (surface) mode is untouched — only --eflomal-stem switches it to the stem.
    if (args.eflomal_stem or (want["gloss"] and "morph" in signals)) and args.iso not in NORMALIZERS:
        from lexeme_aligner.target_morph import LearnedNormalizer
        lm = LearnedNormalizer(args.iso, str(args.usj_dir))
        NORMALIZERS[args.iso] = lm
        print(f"[pilot] #2 morphology (learned): {len(lm.suffixes)} suffixes, {len(lm.prefixes)} prefixes",
              file=sys.stderr)
    norm: Normalizer = NORMALIZERS.get(args.iso, Normalizer())
    runs = {}
    if want["gloss"]:
        gloss_sw = None
        if "stopwords" in signals:                       # #3: target function-word filter
            from lexeme_aligner.target_stopwords import StopwordFilter
            gloss_sw = StopwordFilter(args.iso, str(args.usj_dir))
            print(f"[pilot] gloss #3 stopwords: {len(gloss_sw.words)} target function-words", file=sys.stderr)
        gloss_xl = None
        if "cross" in signals and args.cross_lang.exists():  # #1: cross-lingual span profile
            gloss_xl = json.loads(args.cross_lang.read_text(encoding="utf-8"))
            print(f"[pilot] gloss #1 cross-lingual: {len(gloss_xl)} lexeme span profiles", file=sys.stderr)
        csv_priors = GlossPriors(args.lang_name, args.iso)
        if csv_priors.perstem or csv_priors.by_strong:
            priors = csv_priors                          # external per-language gloss CSVs (legacy)
            print(f"[pilot] gloss priors (CSV): {len(priors.perstem)} lex, {len(priors.by_strong)} strong",
                  file=sys.stderr)
        else:                                            # none → bootstrap from own lexeme-alignments + prior-pack (R1/LXX)
            from lexeme_aligner.bootstrap_priors import BootstrapPriors
            priors = BootstrapPriors(args.iso)
            if priors.missing:
                print(f"[pilot] gloss: no lexeme-alignments ({priors.missing}) — run eflomal + export_lex "
                      f"first; gloss will be sparse", file=sys.stderr)
            else:
                print(f"[pilot] gloss priors (bootstrap): {priors.stats['lexemes']} lexemes, "
                      f"{priors.stats['strongs']} strongs, {priors.stats['lxx']} LXX-bridged", file=sys.stderr)
        runs["gloss"] = run_method(recs, lambda r: align_verse(r.heb, r.toks, priors, args.iso,
                                                               stopwords=gloss_sw, cross_lang=gloss_xl),
                                   args.iso, "gloss", args.out)
    if want["stat"]:
        ibm = IBM1(iters=args.stat_iters)
        ibm.train([([t.strong for t in r.heb if t.strong and t.is_content], r.toks)
                   for r in recs if r.toks])
        runs["stat"] = run_method(recs, lambda r: ibm.decode(r.heb, r.toks, norm),
                                  args.iso, "stat", args.out)
    if want["eflomal"]:
        from lexeme_aligner.eflomal_align import EflomalAligner
        prior_pairs = (_eflomal_priors(recs, GlossPriors(args.lang_name, args.iso), args.iso, norm)
                       if args.eflomal_priors else None)
        if prior_pairs:
            print(f"[pilot] eflomal priors: {len(prior_pairs)} gloss anchors", file=sys.stderr)
        eflo = EflomalAligner(anchor=args.anchor, stem=args.eflomal_stem)
        if args.eflomal_stem:
            print(f"[pilot] #2 eflomal: aligning on STEMMED target tokens", file=sys.stderr)
        eflo.run(recs, norm, priors_pairs=prior_pairs)
        runs["eflomal"] = run_method(recs, lambda r: eflo.decode(r), args.iso, "eflomal", args.out)

    for tag, results in runs.items():
        report = write_report(results, args.iso, tag, args.out)
        hc, al, hi = _totals(results)
        print(f"[{tag}] overall content coverage {100*al/max(1,hc):.1f}% "
              f"(hi-conf {100*hi/max(1,hc):.1f}%)  → {report}", file=sys.stderr)
    if len(runs) >= 2:
        print("\n[compare] content coverage:", file=sys.stderr)
        for tag, results in runs.items():
            hc, al, hi = _totals(results)
            print(f"   {tag:9} {100*al/max(1,hc):5.1f}%   (hi-conf {100*hi/max(1,hc):.1f}%)",
                  file=sys.stderr)
    return 0


def _eflomal_priors(recs, priors, iso, norm) -> list[tuple[str, str, int]]:
    """Gloss high-confidence alignments → eflomal lexical priors (strong, target, count).
    Turns method (a) into supervision for eflomal (semi-supervised)."""
    counts: dict[tuple[str, str], int] = collections.Counter()
    for r in recs:
        if not r.toks:
            continue
        for m in align_verse(r.heb, r.toks, priors, iso):
            if m.method in _HI_METHODS:
                htok = next((t for t in r.heb if t.idx == m.h_idx), None)
                if htok and htok.strong:
                    tgt = norm.forms(r.toks[m.t_idx[0]])[0]
                    counts[(htok.strong, tgt)] += 1
    return [(s, t, c) for (s, t), c in counts.items()]


if __name__ == "__main__":
    raise SystemExit(main())
