"""Prior-pack recipes — mode-1 leverage the aligner computes from data it already owns.

The shoresh handover (internal-docs/aligner-handover.md) ships ONE language-independent artifact,
`bcv-commons/prior-pack` (HF, CC-BY; one row per MACULA lexeme: keyness / lxx_greek / lxx_hebrew /
senses / neighbors / xling_confidence), plus a set of RECIPES. Each recipe joins the prior pack against
data this repo already owns (`lexeme-alignments/`, `senses_attested/`, the `lexeme-spine`) and regenerates
per language on our side — nothing per-language to pull from shoresh.

Implemented here (all mode-1 local joins):
  R1 · keyness-filter  — drop function words from a language's lexeme-alignments, rank by hi_conf → a clean
                         content-word seed dictionary (gloss run).
  R2 · sense-surface   — a language's senses_attested × prior_pack.senses (the sense INVENTORY + base
                         rates) → which senses are confirmed / MISSING / extra per lexeme.
  R3 · gap-map         — lexeme-spine lexemes MINUS a language's attested lexemes = what it hasn't
                         aligned yet; prioritise low `xling_confidence` (fragile) content lexemes.
  LXX · NT-gap         — a language's OT lexeme-alignments surfaces carried into its NT via prior_pack.lxx_greek
                         (Hebrew lexeme → LXX → Greek): candidate NT renderings, gaps first (nt_total→0).

    python3 -m lexeme_aligner.recipes --iso fra --recipe all
    → out/recipe_{r1_keyness,r2_sense,r3_gapmap,lxx_ntgap}_fra.parquet  (+ stderr preview)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT, OUT, PRIOR_PACK, SPINE_DB


def load_prior_pack(path: Path = PRIOR_PACK) -> dict[str, dict]:
    """lexeme -> {keyness, xling_confidence, testament, is_content, lemma, lxx_greek, senses, …}."""
    import pyarrow.parquet as pq
    if not Path(path).exists():
        raise SystemExit(f"prior pack not found at {path} — pull it:\n"
                         "  python -c \"from huggingface_hub import snapshot_download as d; "
                         "d(repo_id='bcv-commons/prior-pack', repo_type='dataset', "
                         "local_dir='pipeline/vendor/prior-pack')\"")
    return {r["lexeme"]: r for r in pq.read_table(path).to_pylist()}


def _attested_lexemes(iso: str, root: Path) -> dict[str, dict]:
    """A language's lexeme-alignments partition → the eflomal-base rows (attested set). Filters the
    additive union to method=eflomal so per-method rows don't double-count."""
    import pyarrow.parquet as pq
    fp = root / f"iso={iso}" / "data.parquet"
    if not fp.exists():
        raise SystemExit(f"no lexeme-alignments for {iso} at {fp} — run export_lex --iso {iso} first")
    return [r for r in pq.read_table(fp).to_pylist() if r.get("method", "eflomal") == "eflomal"]


def r1_keyness_filter(iso: str, prior: dict, aligned_root: Path, min_keyness: float | None):
    """Keyness-filtered seed dictionary: keep only content-word lexemes (non-null keyness, ≥ threshold),
    carry the keyness, rank by (hi_conf, count). Function words — null keyness — are dropped."""
    rows = _attested_lexemes(iso, aligned_root)
    kept, dropped = [], 0
    for r in rows:
        p = prior.get(r["lexeme"])
        k = p["keyness"] if p else None
        if k is None or (min_keyness is not None and k < min_keyness):
            dropped += 1                                    # function word / below salience / unknown
            continue
        kept.append({"surface": r["surface"], "lexeme": r["lexeme"], "strong": r["strong"],
                     "count": r["count"], "share": r["share"], "hi_conf": r["hi_conf"],
                     "keyness": round(float(k), 4)})
    kept.sort(key=lambda x: (x["lexeme"], -x["hi_conf"], -x["count"]))
    return kept, dropped


def r3_gap_map(iso: str, prior: dict, aligned_root: Path, spine_db: Path):
    """lexeme-spine content lexemes a language has NOT attested → prioritised by low xling_confidence
    (fragile, needs focus) then by spine frequency (impact)."""
    attested = {r["lexeme"] for r in _attested_lexemes(iso, aligned_root)}
    con = sqlite3.connect(f"file:{spine_db}?mode=ro", uri=True)
    # every content lexeme in the backbone, with its corpus frequency (impact weight)
    spine = con.execute(
        "SELECT lexeme, COUNT(*) AS freq, MAX(strong) AS strong, MAX(lemma) AS lemma "
        "FROM spine_words WHERE is_content=1 AND lexeme IS NOT NULL GROUP BY lexeme").fetchall()
    con.close()
    gaps = []
    for lexeme, freq, strong, lemma in spine:
        if lexeme in attested:
            continue
        p = prior.get(lexeme) or {}
        nt = str(lexeme).startswith("grc")
        padded = f"{'G' if nt else 'H'}{int(strong):04d}" if strong is not None else None  # spine strong is a bare int
        gaps.append({"lexeme": lexeme, "strong": padded, "lemma": lemma,
                     "testament": "NT" if nt else "OT",
                     "spine_freq": freq, "xling_confidence": p.get("xling_confidence")})
    # low xling first (fragile → focus), then high spine frequency (impact). None xling = unknown → last.
    gaps.sort(key=lambda g: (g["xling_confidence"] if g["xling_confidence"] is not None else 99,
                             -g["spine_freq"]))
    return gaps


def r2_sense_surface(iso: str, prior: dict, senses_root: Path):
    """A language's attested senses vs the prior sense INVENTORY. For each lexeme the prior knows a
    sense inventory for, mark each prior (stem, sense) confirmed | missing (prior sense the language
    hasn't attested → a disambiguation target) and surface any attested-but-not-in-inventory `extra`."""
    import pyarrow.parquet as pq
    fp = senses_root / f"iso={iso}" / "data.parquet"
    if not fp.exists():
        raise SystemExit(f"no senses_attested for {iso} at {fp} — run senses_attested --iso {iso} first")
    # attested (lexeme, stem, sense) → (total count, top surface) across whatever base_texts are pooled
    att: dict[tuple, dict] = {}
    for r in pq.read_table(fp).to_pylist():
        k = (r["lexeme"], r["stem"] or "", str(r["sense"]))
        a = att.setdefault(k, {"count": 0, "top": None, "top_n": -1})
        a["count"] += r["count"]
        if r["count"] > a["top_n"]:
            a["top_n"], a["top"] = r["count"], r["surface"]

    rows = []
    seen_att = set()
    for lexeme, p in prior.items():
        inv = p.get("senses") or []
        if not inv:
            continue
        for s in inv:                                        # prior inventory: expected senses + base rate
            stem, sense = s.get("stem") or "", str(s.get("sense"))
            k = (lexeme, stem, sense)
            a = att.get(k)
            if a:
                seen_att.add(k)
            rows.append({"lexeme": lexeme, "stem": stem, "sense": sense,
                         "prior_share": round(float(s.get("share") or 0.0), 4),
                         "status": "confirmed" if a else "missing",
                         "attested_count": a["count"] if a else 0,
                         "top_surface": a["top"] if a else None})
    for k, a in att.items():                                 # attested senses the prior inventory lacks
        if k in seen_att:
            continue
        rows.append({"lexeme": k[0], "stem": k[1], "sense": k[2], "prior_share": None,
                     "status": "extra", "attested_count": a["count"], "top_surface": a["top"]})
    order = {"missing": 0, "confirmed": 1, "extra": 2}        # missing first (where to disambiguate)
    rows.sort(key=lambda r: (order[r["status"]], -(r["prior_share"] or 0), r["lexeme"]))
    return rows


def lxx_nt_gap(iso: str, prior: dict, aligned_root: Path, top_ot: int):
    """Carry a language's OT surfaces into its NT via the LXX bridge. Group OT lexeme-alignments by Hebrew
    lexeme → prior_pack.lxx_greek gives the Greek strongs the LXX renders it into → map to Greek lexemes
    → the OT surface is a candidate NT rendering. Flag whether the language's own NT already attests it
    (nt_confirmed) and how much (nt_total); pure gaps (nt_total=0) are the point of the recipe."""
    rows_al = _attested_lexemes(iso, aligned_root)
    # OT side: Hebrew lexeme → {surface: count}; NT side: Greek lexeme → {surface: count}
    ot: dict[str, dict] = {}
    nt: dict[str, dict] = {}
    for r in rows_al:
        side = nt if str(r["lexeme"]).startswith("grc") else ot
        side.setdefault(r["lexeme"], {})[r["surface"]] = side.get(r["lexeme"], {}).get(r["surface"], 0) + r["count"]
    # Greek strong → CONTENT Greek lexeme(s). Restrict to non-null keyness: the LXX bridge otherwise
    # floods gaps with Greek function words (article ὁ, αὐτός, prepositions) rendered by common Hebrew
    # content words — the exact garbage keyness exists to kill. Content targets only = useful candidates.
    strong2lex: dict[str, list] = {}
    for lexeme, p in prior.items():
        if p.get("testament") == "NT" and p.get("strong") and p.get("keyness") is not None:
            strong2lex.setdefault(p["strong"], []).append(lexeme)

    rows = []
    for heb_lexeme, surfaces in ot.items():
        greeks = (prior.get(heb_lexeme) or {}).get("lxx_greek") or []
        if not greeks:
            continue
        tot = sum(surfaces.values()) or 1
        top = sorted(surfaces.items(), key=lambda x: -x[1])[:top_ot]   # dominant OT renderings = candidates
        for gstrong in greeks:
            for grc_lexeme in strong2lex.get(gstrong, []):
                nt_surf = nt.get(grc_lexeme, {})
                nt_total = sum(nt_surf.values())
                for surface, ot_count in top:
                    rows.append({"grc_lexeme": grc_lexeme, "grc_strong": gstrong,
                                 "candidate_surface": surface, "via_hebrew": heb_lexeme,
                                 "ot_count": ot_count, "share": round(ot_count / tot, 4),
                                 "nt_confirmed": surface in nt_surf, "nt_total": nt_total})
    # pure gaps first (nt_total=0), then strongest OT evidence
    rows.sort(key=lambda r: (r["nt_total"], -r["ot_count"]))
    return rows


def _write_parquet(rows: list[dict], dest: Path, schema: dict) -> None:
    import pyarrow as pa
    import pyarrow.parquet as papq
    cols = {name: pa.array([r.get(name) for r in rows], typ) for name, typ in schema.items()}
    papq.write_table(pa.table(cols), dest, compression="zstd")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--recipe", choices=["r1", "r2", "r3", "lxx", "all"], default="all")
    ap.add_argument("--min-keyness", type=float, default=None,
                    help="R1: extra salience floor beyond dropping null-keyness function words")
    ap.add_argument("--top-ot", type=int, default=3, help="LXX: dominant OT surfaces carried per Hebrew lexeme")
    ap.add_argument("--aligned-root", type=Path, default=LEX_ROOT)
    ap.add_argument("--senses-root", type=Path, default=Path("publish/senses_attested"))
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK)
    ap.add_argument("--spine-db", type=Path, default=SPINE_DB)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    import pyarrow as pa
    prior = load_prior_pack(args.prior_pack)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.recipe in ("r1", "all"):
        rows, dropped = r1_keyness_filter(args.iso, prior, args.aligned_root, args.min_keyness)
        dest = args.out / f"recipe_r1_keyness_{args.iso}.parquet"
        _write_parquet(rows, dest, {"surface": pa.string(), "lexeme": pa.string(), "strong": pa.string(),
                                    "count": pa.int32(), "share": pa.float32(), "hi_conf": pa.float32(),
                                    "keyness": pa.float32()})
        print(f"[R1 keyness] {args.iso}: {len(rows)} content-word entries kept, {dropped} function/unknown "
              f"dropped → {dest}", file=sys.stderr)
        for r in rows[:4]:
            print(f"    {r['lexeme']:11} {r['strong']:6} key={r['keyness']:<5} hi={r['hi_conf']:.2f} "
                  f"-> {r['surface']}", file=sys.stderr)

    if args.recipe in ("r2", "all"):
        rows = r2_sense_surface(args.iso, prior, args.senses_root)
        dest = args.out / f"recipe_r2_sense_{args.iso}.parquet"
        _write_parquet(rows, dest, {"lexeme": pa.string(), "stem": pa.string(), "sense": pa.string(),
                                    "prior_share": pa.float32(), "status": pa.string(),
                                    "attested_count": pa.int32(), "top_surface": pa.string()})
        miss = sum(1 for r in rows if r["status"] == "missing")
        conf = sum(1 for r in rows if r["status"] == "confirmed")
        print(f"[R2 sense]   {args.iso}: {conf} senses confirmed, {miss} in prior inventory but MISSING "
              f"(disambiguation targets) → {dest}", file=sys.stderr)
        for r in [x for x in rows if x["status"] == "missing"][:4]:
            print(f"    MISSING {r['lexeme']:11} {r['stem'] or '-':7} s{r['sense']} prior_share={r['prior_share']}",
                  file=sys.stderr)

    if args.recipe in ("lxx", "all"):
        rows = lxx_nt_gap(args.iso, prior, args.aligned_root, args.top_ot)
        dest = args.out / f"recipe_lxx_ntgap_{args.iso}.parquet"
        _write_parquet(rows, dest, {"grc_lexeme": pa.string(), "grc_strong": pa.string(),
                                    "candidate_surface": pa.string(), "via_hebrew": pa.string(),
                                    "ot_count": pa.int32(), "share": pa.float32(),
                                    "nt_confirmed": pa.bool_(), "nt_total": pa.int32()})
        gaps = sum(1 for r in rows if r["nt_total"] == 0)
        conf = sum(1 for r in rows if r["nt_confirmed"])
        print(f"[LXX NT-gap] {args.iso}: {len(rows)} candidate NT renderings ({gaps} for Greek lexemes the "
              f"NT never aligned = pure gaps; {conf} already NT-confirmed) → {dest}", file=sys.stderr)
        for r in [x for x in rows if x["nt_total"] == 0][:4]:
            print(f"    GAP {r['grc_lexeme']:9} {r['grc_strong']:6} <- {r['via_hebrew']:11} "
                  f"cand='{r['candidate_surface']}' (ot×{r['ot_count']})", file=sys.stderr)

    if args.recipe in ("r3", "all"):
        gaps = r3_gap_map(args.iso, prior, args.aligned_root, args.spine_db)
        dest = args.out / f"recipe_r3_gapmap_{args.iso}.parquet"
        _write_parquet(gaps, dest, {"lexeme": pa.string(), "strong": pa.string(), "lemma": pa.string(),
                                    "testament": pa.string(), "spine_freq": pa.int32(),
                                    "xling_confidence": pa.int32()})
        low = sum(1 for g in gaps if (g["xling_confidence"] or 0) <= 1)
        print(f"[R3 gap-map] {args.iso}: {len(gaps)} un-aligned content lexemes ({low} at xling≤1 = fragile, "
              f"focus first) → {dest}", file=sys.stderr)
        for g in gaps[:4]:
            print(f"    {g['lexeme']:11} {g['strong']:6} {g['testament']} freq={g['spine_freq']:<4} "
                  f"xling={g['xling_confidence']}  {g['lemma']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
