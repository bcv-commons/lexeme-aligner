"""NT-to-OT bridge: eflomal priors for an edition whose OT is thin, taken from the same edition's NT alignment.

A Hebrew content lexeme -> the Greek Strong's the LXX uses for it (prior-pack `lxx_greek`) -> how this edition renders those
Greek words in its own NT eflomal alignment (single-token content links, top `top_k` renderings with share >= `min_share`)
-> a `LEX` prior <Hebrew Strong's> <target word> <share x scale>, for the Hebrew lexemes that occur in the edition's OT.

Idea from bsb-add-books (2026-10-27). Measured on 4 gold languages, see config/nt_bridge.json. The NT must be aligned first:
onboard.py runs `run_pilot --nt`, then `run_pilot --ot --nt-bridge` for an edition `applies()` says yes to.
MACULA's per-word `greekstrong` (bcv-query spine, 2026-10-27) was measured as well and did not beat the lexeme-level table.
"""
from __future__ import annotations

import collections
import gzip
import json
from pathlib import Path

from lexeme_aligner.config import PRIOR_PACK, _REPO_ROOT

CONFIG = _REPO_ROOT / "config" / "nt_bridge.json"


def load_config(path: Path = CONFIG) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"enabled": False}


def usj_books(usj_dir: str | Path) -> set[str]:
    """Book codes present in an ingested USJ folder (files `NN-BOOK.json`)."""
    return {f.stem[3:] for f in Path(usj_dir).glob("*.json")}


def applies(usj_dir: str | Path, cfg: dict | None = None) -> bool:
    from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS
    cfg = load_config() if cfg is None else cfg
    if not cfg.get("enabled"):
        return False
    books = usj_books(usj_dir)
    n_ot, n_nt = len(books & set(OT_BOOKS)), len(books & set(NT_BOOKS))
    return 1 <= n_ot <= cfg.get("max_ot_books", 10) and n_nt >= cfg.get("min_nt_books", 20)


def lxx_bridge(prior_pack: Path = PRIOR_PACK) -> dict[str, list[str]]:
    """Hebrew lexeme -> padded Greek Strong's (`G2316`)."""
    import pyarrow.parquet as pq
    t = pq.read_table(prior_pack, columns=["lexeme", "lxx_greek"]).to_pydict()
    return {lx: [f"G{int(g[1:]):04d}" for g in gs] for lx, gs in zip(t["lexeme"], t["lxx_greek"])
            if lx.startswith("hbo:") and gs}


def table_bridge(path: Path, tiers: set[str] | None = None) -> dict[str, list[str]]:
    """Hebrew Strong's (the spine's number, `H0001`) -> padded Greek Strong's, from a tiered bridge table (TSV hebrew, greek, tier,
    count, score; make-tanakh's strong_bridge_bq.tsv = bcv-query's macula + glaux LXX tiers + a Jev tier). `tiers` limits the rows.
    Experimental (ALIGNER_NT_BRIDGE_TABLE), A/B against lxx_greek in pipeline/work/nt-bridge/run5.py."""
    import csv
    out: dict[str, list[str]] = collections.defaultdict(list)
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if tiers and r["tier"] not in tiers:
                continue
            g = f"G{int(r['greek'][1:]):04d}"
            if g not in out[r["hebrew"]]:
                out[r["hebrew"]].append(g)
    return dict(out)


def table_weights(path: Path, glaux_min: float | None = None, exclude: set[str] = frozenset()) -> tuple[dict, dict]:
    """From a tiered bridge table: ({Hebrew Strong's: {Greek: share of the Hebrew word's LXX renderings}} from the `macula` tier's score,
    {Hebrew Strong's: {Greek: Dice}} from the `lxx` (GLAUx) tier with score >= glaux_min, Greek words in `exclude` left out). Experiment
    (make-tanakh's weighted bridge, 2026-10-30): ALIGNER_NT_BRIDGE_WEIGHTED."""
    import csv
    share: dict = collections.defaultdict(dict)
    extra: dict = collections.defaultdict(dict)
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            g = f"G{int(r['greek'][1:]):04d}"
            if r["tier"] == "macula":
                share[r["hebrew"]][g] = float(r["score"])
            elif r["tier"] == "lxx" and glaux_min is not None and float(r["score"]) >= glaux_min and g not in exclude:
                extra[r["hebrew"]][g] = float(r["score"])
    return dict(share), dict(extra)


def frequent_nt_greek(n: int = 50) -> set[str]:
    """The n most frequent Greek Strong's in the spine's NT (`G0846` form)."""
    import sqlite3
    from lexeme_aligner.config import SPINE_DB
    con = sqlite3.connect(f"file:{SPINE_DB}?mode=ro", uri=True)
    rows = con.execute("select strong, count(*) n from spine_words where lexeme like 'grc:%' group by strong order by n desc limit ?", (n,))
    return {f"G{int(s):04d}" for s, _n in rows}


def nt_renderings(tag: str, out_dir: Path) -> dict[str, collections.Counter]:
    """Greek Strong's -> Counter of target words, from this edition's NT eflomal files (content pairs on one target token)."""
    from lexeme_aligner.run_pilot import NT_BOOKS
    rend: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for book in NT_BOOKS:
        for f in (out_dir / f"align_eflomal_{tag}_{book}.jsonl", out_dir / f"align_eflomal_{tag}_{book}.jsonl.gz"):
            if not f.exists():
                continue
            with (gzip.open if f.suffix == ".gz" else open)(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    for p in json.loads(line)["pairs"]:
                        if p.get("content") and p.get("strong") and len(p.get("t_idx") or []) == 1:
                            rend[p["strong"]][p["target"].lower()] += 1
            break
    return rend


def build_priors(recs, tag: str, out_dir: Path, cfg: dict | None = None) -> list[tuple[str, str, float]]:
    """(Hebrew Strong's, target word, alpha) for the content tokens of `recs` (the OT verses being aligned)."""
    cfg = load_config() if cfg is None else cfg
    rend = nt_renderings(tag, out_dir)
    if not rend:
        raise SystemExit(f"[nt_bridge] {tag}: no NT eflomal alignment in {out_dir} — run `run_pilot --nt` first")
    import os
    table = os.environ.get("ALIGNER_NT_BRIDGE_TABLE")
    tiers = set(filter(None, os.environ.get("ALIGNER_NT_BRIDGE_TIERS", "").split(","))) or None
    by_strong = table_bridge(Path(table), tiers) if table else None
    weighted = os.environ.get("ALIGNER_NT_BRIDGE_WEIGHTED") or ""      # "1": weighted sum, kept >= min_share; "rank": see below
    br = {} if table and not weighted else lxx_bridge()
    top_k, min_share, scale = cfg.get("top_k", 5), cfg.get("min_share", 0.1), cfg.get("scale", 2.0)
    if weighted:
        # prior(H, w) = sum over Greek g of weight(g | H) x share(w | g), weights normalised to 1 over the g that have renderings: weight = the
        # LXX share of g among H's renderings (table `macula` score), plus GLAUx pairs (Dice >= ALIGNER_NT_BRIDGE_GLAUX) weighted by Dice.
        gmin = os.environ.get("ALIGNER_NT_BRIDGE_GLAUX")
        share, extra = table_weights(Path(os.environ["ALIGNER_NT_BRIDGE_TABLE"]), float(gmin) if gmin else None,
                                     frequent_nt_greek() if gmin else set())
        floor = 1e-4
        best: dict[tuple[str, str], float] = {}
        seen: set[str] = set()
        for r in recs:
            for t in r.heb:
                if not (t.is_content and t.strong) or t.strong in seen:
                    continue
                seen.add(t.strong)
                ws = {g: share.get(t.strong, {}).get(g, floor) for g in br.get(t.lexeme, [])}
                for g, d in extra.get(t.strong, {}).items():
                    ws[g] = max(ws.get(g, 0.0), d)
                ws = {g: w for g, w in ws.items() if rend.get(g)}
                tot_w = sum(ws.values())
                if not tot_w:
                    continue
                score: dict[str, float] = collections.Counter()
                for g, w in ws.items():
                    c = rend[g]
                    tot = sum(c.values())
                    for word, k in c.most_common(top_k):
                        if k / tot >= min_share:
                            score[word] += w / tot_w * k / tot
                ranked = sorted(score.items(), key=lambda x: -x[1])
                if weighted == "rank":       # today's candidates and strength; the weighting only ranks them (top = full strength)
                    top = ranked[0][1] if ranked else 1.0
                    for word, v in ranked:
                        best[(t.strong, word)] = v / top
                    continue
                for word, v in ranked[:top_k]:
                    if v >= min_share:
                        best[(t.strong, word)] = v
        return [(s, w, v * scale) for (s, w), v in best.items()]
    best: dict[tuple[str, str], float] = {}
    for r in recs:
        for t in r.heb:
            greek = (by_strong.get(t.strong) if by_strong is not None else br.get(t.lexeme)) if t.is_content and t.strong else None
            if not greek:
                continue
            for g in greek:
                c = rend.get(g)
                if not c:
                    continue
                tot = sum(c.values())
                for w, k in c.most_common(top_k):
                    if k / tot >= min_share and k / tot > best.get((t.strong, w), 0):
                        best[(t.strong, w)] = k / tot
    return [(s, w, share * scale) for (s, w), share in best.items()]


def ledger_entry(tags: list[str], usj_dirs: list[str | Path]) -> dict | None:
    """Ledger (pipeline_decisions) entry: which editions got the bridge. None when no edition did (no key in the ledger,
    so the ~1,800 entries of languages without a thin OT do not change)."""
    cfg = load_config()
    if not cfg.get("enabled"):
        return None
    on = sorted(t for t, d in zip(tags, usj_dirs) if d and applies(d, cfg))
    if not on:
        return None
    return {"value": True, "editions": on, "scale": cfg.get("scale"), "max_ot_books": cfg.get("max_ot_books"),
            "source": "self-derived (edition has 1..max_ot_books OT books and an NT) — see config/nt_bridge.json"}
