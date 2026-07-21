"""General gold benchmark — score any alignment MODE against a manual reference.

Two gold backends (`--gold`), both **method-aware** (`--method` reads `align_<method>_<iso>_*.jsonl`,
so gloss / stat / eflomal / a merged tag are all scorable on the same footing):

  clear    — Clear-Bible attestations (`$ALIGNER_RESOURCES/strongs/attestations/<iso>.{tsv,parquet}`,
             surface→Strong's, per-occurrence manual). Metric: **top-1 accuracy** of the aligner's
             argmax key per surface, unweighted + token-weighted, plus gold-vocab coverage. `--grain`
             picks the key: `strong` (bare rollup, default) or `lexeme` (Strong's+lemma — validates
             whether the aligner distinguishes the homonyms one Strong's conflates; both sides carry
             the source lemma, so no id-mapping).
  lexicon  — a hand-curated **Strong's→translation** lexicon (e.g. karnbibeln.se for Swedish). Metric:
             **stem-overlap agreement** between our top-k surface(s) per Strong's and the manual gloss
             (Snowball SV stemmer via the `[validate]` extra; graceful fallback). Reports the
             disagreements, sorted by count, for review.

Both are **type/lexicon level, ref-agnostic** — we compare the learned dictionary, not per-occurrence
positions (sidestepping versification + tokenization differences across editions).

  python -m lexeme_aligner.benchmark --gold clear   --iso fra --method eflomal          # vs Clear gold
  python -m lexeme_aligner.benchmark --gold lexicon --iso swk --method eflomal --testament greek
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

from lexeme_aligner.config import OUT, RESOURCES

# ───────────────────────────── shared ─────────────────────────────
_STRIP = re.compile(r"^\W+|\W+$", re.UNICODE)


def norm_surface(s: str) -> str:
    """NFC + drop combining marks (harakat/points — match the tokenizer) + casefold + strip edge
    punctuation — reconcile edition/tokenizer/diacritization surface noise."""
    s = "".join(c for c in unicodedata.normalize("NFC", s or "") if not unicodedata.combining(c))
    return _STRIP.sub("", s.casefold())


def norm_strong(raw, prefix: str = "H") -> str | None:
    """Normalize a Strong's key to `<prefix><4 digits>`, dropping any suffix letter."""
    if raw is None or raw == "":
        return None
    m = re.search(r"(\d+)", str(raw))
    if not m:
        return None
    letter = "".join(c for c in str(raw) if c.isalpha())[:1].upper() or prefix
    return f"{letter}{int(m.group(1)):04d}"


def _norm_lemma(lemma) -> str:
    # drop combining marks (Hebrew niqqud / Greek accents) — the Clear gold and MACULA vocalize lemmas
    # differently (רִבּוֹ vs רִבֹּוא), so matching on the consonantal/unaccented skeleton is far cleaner.
    s = "".join(c for c in unicodedata.normalize("NFC", lemma or "") if not unicodedata.combining(c))
    return s.strip().casefold()


def strong_key(strong, lemma, grain: str) -> str | None:
    """The comparison key at the chosen grain. `strong` = bare Strong's (top-1 over the rollup).
    `lexeme` = (Strong's, source lemma) — homonyms one Strong's conflates are distinguished by lemma,
    which both our pairs and the Clear gold carry, so no id-mapping is needed."""
    k = norm_strong(strong)
    if k is None:
        return None
    return k if grain == "strong" else f"{k}|{_norm_lemma(lemma)}"


def load_produced(iso: str, tag: str, out_dir: Path, grain: str = "strong",
                  morph_remap: dict | None = None, gbt_morph: dict | None = None):
    """surface -> Counter(key) from the aligner's jsonl (any method `tag`), at the chosen grain.
    Content pairs, single-token targets only (multi-word renderings are names/phrases, not 1:1
    lexicon entries).

    `morph_remap` + `gbt_morph` (both optional, both-or-neither): the greek_morph_strong.py bridge —
    for Greek pairs, translate our lemma-level Strong's to Clear's traditional tense/case-specific
    numbering via the source word's own morphology, BEFORE building the surface->key aggregate. This
    only affects the comparison key returned here, never the pair's own `strong` field on disk."""
    lex: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    files = sorted(out_dir.glob(f"align_{tag}_{iso}_*.jsonl"))
    if not files:
        sys.exit(f"[benchmark] no produced files: {out_dir}/align_{tag}_{iso}_*.jsonl "
                 f"— run the aligner first (run_pilot --method {tag} --ot --iso {iso} …)")
    verses = 0
    for f in files:
        for line in f.open(encoding="utf-8"):
            verses += 1
            rec = json.loads(line)
            ref = rec.get("ref")
            for p in rec["pairs"]:
                if not p.get("content"):
                    continue
                tgt = p.get("target") or ""
                if not tgt or " " in tgt.strip():        # single-token surfaces only
                    continue
                strong = p.get("strong")
                if morph_remap is not None and gbt_morph is not None and strong and strong.startswith("G"):
                    m = gbt_morph.get(int(ref) * 100 + p.get("h_idx", -1) + 1)
                    if m:
                        remapped = morph_remap.get(f"{norm_strong(strong)}|{m[1]}")
                        if remapped:
                            strong = remapped
                s = norm_surface(tgt)
                k = strong_key(strong, p.get("lemma"), grain)
                if s and k:
                    lex[s][k] += 1
    return lex, len(files), verses


# ───────────────────────── gold backend: clear ─────────────────────────
def _open_gold(iso: str, res_dir: Path):
    base = res_dir / "strongs" / "attestations"
    tsv, pq = base / f"{iso}.tsv", base / f"{iso}.parquet"
    if tsv.exists():
        return ("tsv", tsv)
    if pq.exists():
        return ("parquet", pq)
    sys.exit(f"[benchmark] no Clear gold for '{iso}' under {base} (need <iso>.tsv or .parquet)")


def load_gold_clear(iso: str, res_dir: Path, grain: str = "strong", base_text: str | None = None):
    """surface -> set(key) from Clear gold, at the chosen grain (strong, or strong|lemma).

    `base_text` (parquet only) restricts to a single edition when the gold pools several (e.g. eng gold
    holds BSB + YLT rows) — needed to benchmark a second edition against its OWN gold rows, not the mix."""
    kind, path = _open_gold(iso, res_dir)
    gold: dict[str, set] = collections.defaultdict(set)
    counts: collections.Counter = collections.Counter()

    def add(surface, strong, lemma):
        k = strong_key(strong, lemma, grain)
        if k is None:
            return
        s = norm_surface(surface)
        if s:
            gold[s].add(k)
            counts[s] += 1

    if kind == "tsv":
        if base_text:
            sys.exit("[benchmark] --base-text needs the parquet gold (tsv carries no base_text column)")
        rows = (r for r in path.open(encoding="utf-8") if not r.startswith("#"))
        for row in csv.DictReader(rows, delimiter="\t"):
            add(row.get("surface"), row.get("strong"), row.get("lemma"))
    else:
        import pyarrow.parquet as pq                       # optional dep, parquet path only
        cols = ["surface", "strong", "lemma"] + (["base_text"] if base_text else [])
        t = pq.read_table(path, columns=cols).to_pydict()
        bt = t.get("base_text")
        for i, (surface, strong, lemma) in enumerate(zip(t["surface"], t["strong"], t["lemma"])):
            if base_text and bt[i] != base_text:
                continue
            add(surface, strong, lemma)
        if base_text and not counts:
            sys.exit(f"[benchmark] no gold rows with base_text={base_text!r} in {path.name}")
    return gold, counts


def score_clear(iso: str, tag: str, grain: str = "strong", gold_iso: str | None = None,
                base_text: str | None = None, morph_remap: bool = True):
    morph_table, gbt_morph = None, None
    if morph_remap:
        from lexeme_aligner.greek_morph_strong import load_table, _load_gbt_morphology
        morph_table = load_table()
        gbt_morph = _load_gbt_morphology(Path("data/gbt/hbo+grc"))
    produced, nbooks, _ = load_produced(iso, tag, OUT, grain, morph_table, gbt_morph)
    gold, gold_counts = load_gold_clear(gold_iso or iso, RESOURCES, grain, base_text=base_text)
    # Testament(s) the gold judges, from its own strong prefixes ({'G'} NT / {'H'} OT). A whole-Bible
    # produced lexicon carries BOTH per surface ("dieu" → G2316 and H0430); scoring its cross-testament
    # top-1 against a single-testament gold tanks it. So restrict the produced top-1 to the gold's side.
    gold_prefixes = {k[0] for ks in gold.values() for k in ks if k}
    shared = [s for s in produced if s in gold]
    n_types = correct_types = tok_total = tok_correct = 0
    misses: list[tuple] = []
    for s in shared:
        cand = [(k, n) for k, n in produced[s].items() if k and k[0] in gold_prefixes]
        if not cand:                                         # produced has no strong on the gold's side
            continue
        top_strong, top_n = max(cand, key=lambda x: x[1])
        n_types += 1
        ok = top_strong in gold[s]
        correct_types += ok
        tok_total += top_n
        tok_correct += top_n if ok else 0
        if not ok:
            misses.append((s, top_strong, sorted(gold[s]), top_n))
    gold_tok = sum(gold_counts.values())
    return {
        "iso": iso, "tag": tag, "grain": grain, "books": nbooks,
        "produced_surfaces": len(produced), "gold_surfaces": len(gold), "shared_surfaces": n_types,
        "top1_acc_types": correct_types / max(1, n_types),
        "top1_acc_weighted": tok_correct / max(1, tok_total),
        "coverage_types": n_types / max(1, len(gold)),
        "coverage_weighted": sum(gold_counts[s] for s in shared) / max(1, gold_tok),
        "misses": sorted(misses, key=lambda m: -m[3])[:15],
    }


def report_clear(r: dict, show_misses: bool):
    print(f"\n=== gold benchmark (clear, grain={r['grain']}) — {r['iso']} / {r['tag']} ({r['books']} books) ===")
    print(f"surfaces: produced {r['produced_surfaces']}  gold {r['gold_surfaces']}  "
          f"shared {r['shared_surfaces']}")
    print(f"TOP-1 ACCURACY  (aligner's surface→{'Strong+lemma' if r['grain']=='lexeme' else 'Strong'} matches gold)")
    print(f"    per word type : {r['top1_acc_types']:.1%}")
    print(f"    token-weighted: {r['top1_acc_weighted']:.1%}   <- headline")
    print(f"COVERAGE of gold vocabulary")
    print(f"    types         : {r['coverage_types']:.1%}")
    print(f"    token-weighted: {r['coverage_weighted']:.1%}")
    if show_misses:
        print("\n  top wrong calls (surface | produced | gold-options | n):")
        for s, got, want, n in r["misses"]:
            print(f"    {s!r:22} {got}  (gold {want})  ×{n}")


# ──────────────────────── gold backend: lexicon ────────────────────────
# A manual Strong's→translation lexicon. Add more named sources here (per-testament page URLs).
LEX_SOURCES = {"karnbibeln": {"greek": "https://karnbibeln.se/lexikon-grekiska",
                              "hebrew": "https://karnbibeln.se/lexikon-hebreiska"}}
_LEX_PREFIX = {"greek": "G", "hebrew": "H"}
_ENTRY = re.compile(r"value:'(\d+)',\s*label:'([^']*)',\s*description:'([^']*)',\s*type:'([GH])'")
_GLOSS = re.compile(r"\)\s*-\s*(.*?)\s*(?:\(([^()]*)\))?\s*$")
_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"


def _norm(w: str) -> str:
    return "".join(c for c in w.lower() if c.isalpha())


try:                                                         # optional (the [validate] extra)
    import snowballstemmer
    _SB = snowballstemmer.stemmer("swedish")

    def _stem(w: str) -> str:
        return _SB.stemWord(_norm(w))
except ImportError:                                          # heuristic fallback — coarser
    def _stem(w: str) -> str:
        w = _norm(w)
        for suf in ("erna", "arna", "ande", "else", "aren", "orna", "het", "ets", "ens",
                    "er", "ar", "en", "et", "or", "na", "ns", "s", "a", "e", "t", "n"):
            if len(w) > len(suf) + 2 and w.endswith(suf):
                return w[: -len(suf)]
        return w


def _toks(text: str) -> list[str]:
    return [w for w in (_norm(t) for t in re.split(r"[\s,/;()]+", text)) if w]


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def agrees(surfaces: list[str], gloss: str) -> bool:
    """Swedish-inflection-tolerant lexical overlap: exact token equality (är, en, se), Snowball-stem
    match / 3+ char stem prefix (säger/säga→säg, judar/judarna), or a raw 4+ char shared prefix
    (fientlig/fiende). Vowel/consonant alternations (man/män, himmel/himlen) can still slip through."""
    g = _toks(gloss)
    for a in (t for surf in surfaces for t in _toks(surf)):
        for b in g:
            if a == b or _common_prefix(a, b) >= 4:
                return True
            sa, sb = _stem(a), _stem(b)
            lo, hi = sorted((sa, sb), key=len)
            if len(lo) >= 3 and hi.startswith(lo):
                return True
    return False


def load_gold_lexicon(lexicon: str, which: str, cache_dir: Path) -> dict[str, str]:
    """{strong: swedish gloss} for one testament — fetched + parsed once, cached as tsv."""
    tsv = cache_dir / f"{lexicon}_{which}.tsv"
    if tsv.exists():
        with tsv.open(encoding="utf-8") as fh:
            return {r["strong"]: r["gloss"] for r in csv.DictReader(fh, delimiter="\t")}
    url = LEX_SOURCES[lexicon][which]
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=90) as r:       # noqa: S310 — fixed https origin
        html = r.read().decode("utf-8", "ignore")
    pref, seen = _LEX_PREFIX[which], {}
    for num, label, _desc, typ in _ENTRY.findall(html):      # page embeds the full G+H index
        if typ != pref:
            continue
        strong = f"{typ}{int(num):04d}"
        if strong in seen:
            continue
        m = _GLOSS.search(label)
        seen[strong] = m.group(1).strip() if m else label.strip()
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tsv.open("w", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t"); w.writerow(["strong", "gloss"]); w.writerows(sorted(seen.items()))
    return seen


def produced_by_strong(iso: str, tag: str, out_dir: Path, prefix: str, topk: int, min_count: int):
    """{strong: [(surface, count), …]} top-k by count, restricted to this testament's prefix."""
    lex, nbooks, _ = load_produced(iso, tag, out_dir)        # surface -> Counter(strong)
    by: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for surface, strongs in lex.items():
        for strong, n in strongs.items():
            if strong.startswith(prefix):
                by[strong][surface] += n
    return {s: [(w, n) for w, n in c.most_common(topk) if n >= min_count]
            for s, c in by.items() if c.most_common(1)[0][1] >= min_count}, nbooks


def score_lexicon(iso: str, tag: str, which: str, lexicon: str, topk: int, min_count: int,
                  cache_dir: Path):
    gold = load_gold_lexicon(lexicon, which, cache_dir)
    ours, nbooks = produced_by_strong(iso, tag, OUT, _LEX_PREFIX[which], topk, min_count)
    both = sorted(set(gold) & set(ours))
    agree = [s for s in both if agrees([w for w, _n in ours[s]], gold[s])]
    differ = sorted((set(both) - set(agree)), key=lambda s: -ours[s][0][1])
    return {
        "iso": iso, "tag": tag, "which": which, "lexicon": lexicon, "books": nbooks,
        "gold": len(gold), "ours": len(ours), "shared": len(both),
        "agree": len(agree), "differ": len(differ),
        "agreement": len(agree) / max(1, len(both)),
        "only_gold": len(set(gold) - set(ours)), "only_ours": len(set(ours) - set(gold)),
        "differ_rows": [(s, ours[s][0][1], gold[s], ours[s]) for s in differ],
    }


def report_lexicon(r: dict, out: Path | None):
    print(f"\n=== gold benchmark (lexicon:{r['lexicon']}/{r['which']}) — {r['iso']} / {r['tag']} ===")
    print(f"lexicon Strong's {r['gold']} · our aligned {r['shared']} shared")
    print(f"AGREEMENT (stem overlap): {r['agree']}/{r['shared']} = {r['agreement']:.1%}   <- headline")
    print(f"    differ {r['differ']} · in lexicon but unaligned {r['only_gold']} · "
          f"aligned but not in lexicon {r['only_ours']}")
    lines = [f"# benchmark lexicon:{r['lexicon']}/{r['which']} — `{r['iso']}` / `{r['tag']}`", "",
             f"- agreement (stem overlap): **{r['agree']}/{r['shared']} = {r['agreement']:.1%}** · "
             f"differ {r['differ']} · in-lexicon-unaligned {r['only_gold']}",
             "", "## Differences (our top surface vs manual gloss) — highest count first", "",
             "| Strong | count | manual gloss | our top surface(s) |", "|---|---|---|---|"]
    for s, n, gloss, surfs in r["differ_rows"]:
        lines.append(f"| {s} | {n} | {gloss} | " + " · ".join(f"{w} ×{c}" for w, c in surfs) + " |")
    dest = out or (OUT / f"benchmark_{r['lexicon']}_{r['iso']}_{r['tag']}_{r['which']}.md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"    → {dest}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score an alignment mode against a manual gold reference.")
    ap.add_argument("--gold", choices=["clear", "lexicon"], default="clear")
    ap.add_argument("--iso", required=True, help="target language / aligned iso (e.g. fra, hau, swk)")
    ap.add_argument("--method", "--tag", dest="tag", default="eflomal",
                    help="alignment mode of the produced jsonl (gloss|stat|eflomal|…; default eflomal)")
    # clear
    ap.add_argument("--grain", choices=["strong", "lexeme"], default="strong",
                    help="[clear] compare at bare Strong's (default) or lexeme grain (Strong's+lemma)")
    ap.add_argument("--misses", action="store_true", help="[clear] print the top wrong surface→strong calls")
    ap.add_argument("--gold-iso", default=None,
                    help="[clear] use this iso's gold file for a differently-named produced iso "
                         "(e.g. produced eng_ylt scored vs eng gold)")
    ap.add_argument("--base-text", default=None,
                    help="[clear] restrict gold to one edition/base_text (e.g. YLT) when it pools several")
    ap.add_argument("--morph-remap", action="store_true",
                    help="[clear] translate our lemma-level Greek Strong's (e.g. G1510) to Clear's "
                         "traditional tense/case-specific numbering (e.g. G2258) via source morphology "
                         "before scoring — see greek_morph_strong.py. Corrects an artificial accuracy "
                         "gap on irregular/suppletive Greek words; does not affect published data.")
    # lexicon
    ap.add_argument("--testament", choices=["greek", "hebrew"], help="[lexicon] required")
    ap.add_argument("--lexicon", choices=list(LEX_SOURCES), default="karnbibeln", help="[lexicon] source")
    ap.add_argument("--topk", type=int, default=3, help="[lexicon] compare our top-k surfaces")
    ap.add_argument("--min-count", type=int, default=1, help="[lexicon] confidence floor")
    ap.add_argument("--cache", type=Path, default=Path("data/karnbibeln"))
    ap.add_argument("--out", type=Path, default=None, help="[lexicon] diff report md")
    a = ap.parse_args(argv)

    if a.gold == "clear":
        r = score_clear(a.iso, a.tag, a.grain, gold_iso=a.gold_iso, base_text=a.base_text,
                        morph_remap=a.morph_remap)
        report_clear(r, a.misses)
    else:
        if not a.testament:
            ap.error("--gold lexicon requires --testament greek|hebrew")
        r = score_lexicon(a.iso, a.tag, a.testament, a.lexicon, a.topk, a.min_count, a.cache)
        report_lexicon(r, a.out)
    return r


if __name__ == "__main__":
    main()
