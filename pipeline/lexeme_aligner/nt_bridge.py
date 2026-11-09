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

from lexeme_aligner.config import LEX_ROOT, PRIOR_PACK, _REPO_ROOT

CONFIG = _REPO_ROOT / "config" / "nt_bridge.json"
COMPACT_MANIFEST = _REPO_ROOT / "publish" / "compact-alignments" / "manifest.json"


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


def pool_renderings(partition: Path, exclude: set[str], lexemes: set[str], keep=None) -> dict[str, collections.Counter]:
    """{lexeme: Counter(target word)} from a published lexeme-alignments partition: eflomal rows of every edition but `exclude`
    (base_text, case-insensitive) and, when given, only base_texts `keep(base_text)` accepts; single-word surfaces, only `lexemes`.
    make-tanakh request 1 (2026-10-30); on in production since 2026-11-09 (config `pool`, see pool_sources)."""
    import pyarrow.parquet as pq
    t = pq.read_table(partition, columns=["surface", "lexeme", "method", "base_text", "count"],
                      filters=[("method", "=", "eflomal")]).to_pylist()
    ex = {e.lower() for e in exclude}
    out: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in t:
        if r["lexeme"] in lexemes and r["base_text"].lower() not in ex and " " not in r["surface"] and (keep is None or keep(r["base_text"])):
            out[r["lexeme"]][r["surface"].lower()] += r["count"]
    return dict(out)


def pool_sources(tag: str, cfg: dict, manifest: Path | dict | None = None) -> tuple[str, set[str]] | None:
    """(language iso, compact edition ids to pool from) for edition `tag`: the language's OTHER editions in the compact-alignments
    manifest with at least pool.min_ot_books OT books (an edition with a thin OT of its own has weak OT renderings to lend). None when
    the pool is off, the tag is in no language, or no such edition exists."""
    from lexeme_aligner.run_pilot import OT_BOOKS
    pc = cfg.get("pool") or {}
    if not pc.get("enabled"):
        return None
    manifest = COMPACT_MANIFEST if manifest is None else manifest
    doc = manifest if isinstance(manifest, dict) else (json.loads(Path(manifest).read_text(encoding="utf-8"))
                                                       if Path(manifest).exists() else {})
    for iso, e in doc.get("languages", {}).items():
        eds = e.get("editions", {})
        if not any((v.get("tag") or ed).lower() == tag.lower() for ed, v in eds.items()):
            continue
        ids = {ed for ed, v in eds.items() if (v.get("tag") or ed).lower() != tag.lower()
               and len(set(v.get("books", [])) & set(OT_BOOKS)) >= pc.get("min_ot_books", 39)}
        return (iso, ids) if ids else None
    return None


def compact_id(iso: str, base_text: str) -> str:
    """lexeme-alignments base_text -> compact-alignments edition id (the join rule on both cards: `BSB` -> `eng_BSB`)."""
    return base_text if base_text.lower().startswith(iso) else f"{iso}_{base_text}"


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
    pool, pool_args = os.environ.get("ALIGNER_NT_BRIDGE_POOL"), None
    if pool:          # experiment knob (run5.py): any partition, editions left out by name
        pool_args = (Path(pool), set(os.environ.get("ALIGNER_NT_BRIDGE_POOL_EXCLUDE", "").split(",")), None)
    elif (src := pool_sources(tag, cfg)) and (part := LEX_ROOT / f"iso={src[0]}" / "data.parquet").exists():
        pool_args = (part, set(), lambda bt, iso=src[0], ids=src[1]: compact_id(iso, bt) in ids)
    if pool_args:
        # make-tanakh request 1: renderings of the same Hebrew lexeme in the language's OTHER editions (published partition), as priors
        # beside the bridge's (max per pair), times the pool weight; ALIGNER_NT_BRIDGE_POOL_ONLY=1 drops the bridge's
        lex = {t.lexeme for r in recs for t in r.heb if t.is_content and t.strong}
        pr = pool_renderings(pool_args[0], pool_args[1], lex, pool_args[2])
        pw = float(os.environ.get("ALIGNER_NT_BRIDGE_POOL_WEIGHT") or (cfg.get("pool") or {}).get("weight", 0.5))
        done = set()
        for r in recs:
            for t in r.heb:
                if not (t.is_content and t.strong) or t.lexeme in done or t.lexeme not in pr:
                    continue
                done.add(t.lexeme)
                c = pr[t.lexeme]
                tot = sum(c.values())
                for w, k in c.most_common(top_k):
                    if k / tot >= min_share:
                        best[(t.strong, w)] = max(best.get((t.strong, w), 0), pw * k / tot)
        if os.environ.get("ALIGNER_NT_BRIDGE_POOL_ONLY"):
            return [(s, w, v * scale) for (s, w), v in best.items()]
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
    out = {"value": True, "editions": on, "scale": cfg.get("scale"), "max_ot_books": cfg.get("max_ot_books"),
           "source": "self-derived (edition has 1..max_ot_books OT books and an NT) — see config/nt_bridge.json"}
    pooled = {t: sorted(src[1]) for t in on if (src := pool_sources(t, cfg))}
    if pooled:        # only languages with another full-OT edition get the key
        out["pool"] = {"weight": cfg["pool"].get("weight", 0.5), "from": pooled}
    return out
