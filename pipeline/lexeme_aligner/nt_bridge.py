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
    br = lxx_bridge()
    top_k, min_share, scale = cfg.get("top_k", 5), cfg.get("min_share", 0.1), cfg.get("scale", 2.0)
    best: dict[tuple[str, str], float] = {}
    for r in recs:
        for t in r.heb:
            if not (t.is_content and t.strong and t.lexeme in br):
                continue
            for g in br[t.lexeme]:
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
