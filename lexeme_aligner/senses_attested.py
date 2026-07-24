"""Aggregate the per-verse jsonl into `senses_attested` — attested target renderings per BHSA sense.

The empirical **evidence** layer shoresh ingests (bcv-query data-contract): for a BHSA lexeme in a
disambiguated sense+binyan, which target words attest it, with counts — the *supply* that fills
shoresh's `senses_i18n/_gaps` demand and validates the LLM glosses. It does NOT replace shoresh's
curated `senses_i18n/<iso>.tsv`; it's consumed as an HF Parquet dataset.

Key: **(lexeme, stem, sense)** — MACULA lexeme (the anchor; BHSA `lex` dropped) + MACULA binyan +
sense number, read inline from the enriched `lexeme-spine.db`. OT/Hebrew only (senses are Hebrew;
Greek tokens carry none, so `sense` presence is the OT filter).

    lexeme, stem, sense, surface, count, share, method, source_corpus, base_text
`share = count / Σ count for that (lexeme, stem, sense)` **within one `base_text`** (target edition).
`base_text` is the per-row provenance dimension, so **multi-version = a union of per-edition runs**
(pool N translations of a language → N sets of rows tagged by edition; cross-edition agreement — how
many editions attest a sense→surface — becomes the confidence signal). `source_corpus` = the original
Hebrew corpus (constant for OT). **Licensing: CC-BY** — the key is MACULA-derived (CC-BY, attribute
Clear-Bible), and we carry the sense *number* only — NO English sense label (that's UBS-MARBLE).

    python3 -m lexeme_aligner.senses_attested --iso ind --method eflomal --lang-name Indonesian
    → senses_attested/iso=ind/data.parquet  (git-ignored)  +  senses_attested/manifest.json  (committed)

Multi-version — POOL several editions of one language into a single language partition, each row
tagged by `base_text` (cross-edition agreement then derivable from the rows; a rights-holder takedown
is a clean `base_text` row-drop, never an anonymized re-emit):

    python3 -m lexeme_aligner.senses_attested --iso swe --pool swk --lang-name Swedish
    → iso=swe/data.parquet with base_texts [swe_fol (Folkbibeln), swe_svk (Kärnbibeln)]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import OUT, SPINE_DB
from lexeme_aligner.export_lex import publish_to_hf   # reuse the HF uploader (generic)

SCHEMA = ["lexeme", "stem", "sense", "surface", "count", "share", "method", "source_corpus", "base_text"]
Row = tuple[str, str, str, str, int, float, str, str, str]


def hebrew_corpus() -> str:
    """The spine's Hebrew original corpus (WLC…) from spine_meta — the `source_corpus` for OT senses."""
    try:
        con = sqlite3.connect(f"file:{SPINE_DB}?mode=ro", uri=True)
        meta = dict(con.execute("SELECT key, value FROM spine_meta").fetchall())
        con.close()
        m = re.search(r"\b(WLC|BHSA|BHS)\b", meta.get("source_hebrew", ""))
        return m.group(1) if m else "WLC"
    except sqlite3.Error:
        return "WLC"


def aggregate(out_dir: Path, editions: list[tuple[str, str]], method: str):
    """Fold OT content pairs (lexeme, stem, sense) into attested target renderings, per edition.

    `editions` is a list of (align_iso, base_text). Pooling several editions of ONE language into a
    single partition keeps every row tagged by its `base_text`, with `share` computed WITHIN that
    edition — so cross-edition agreement (a sense→surface attested by >1 base_text) stays derivable
    from the rows, without laundering provenance. A takedown is then a clean `base_text` row-drop."""
    counts: collections.Counter = collections.Counter()      # (lexeme, stem, sense, surface, base_text) -> count
    n_files = 0
    for align_iso, base_text in editions:
        files = tag_files(out_dir, method, align_iso)
        if not files:
            raise SystemExit(f"no align_{method}_{align_iso}_*.jsonl under {out_dir} — run the aligner first")
        n_files += len(files)
        for fp in files:
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    for p in json.loads(line)["pairs"]:
                        lexeme, se, tgt = p.get("lexeme"), p.get("sense"), p.get("target")
                        if not (p.get("content") and lexeme and se and tgt):  # sense ⇒ OT/Hebrew only
                            continue
                        counts[(lexeme, p.get("stem") or "", str(se), tgt.strip().lower(), base_text)] += 1

    return counts, n_files


def _totals(counts) -> collections.Counter:
    """Σ count per (lexeme, stem, sense, base_text) — the `share` denominator (WITHIN edition).
    Computed AFTER any exclusion so survivor shares renormalise and a removed row leaves no trace."""
    per_key: collections.Counter = collections.Counter()
    for (lexeme, stem, se, _su, bt), n in counts.items():
        per_key[(lexeme, stem, se, bt)] += n
    return per_key


_EXCL_FIELDS = ("lexeme", "stem", "sense", "surface", "base_text")


def load_excludes(path: Path | None) -> list[dict]:
    """Read the optional exclusion config: a rights-holder takedown record (auditable, committed).

    `{"exclude": [ {<field>: <value>, …}, … ]}` — a row is dropped if it matches ANY rule, where a
    rule matches when ALL its stated fields equal the row's (fields: lexeme, stem, sense, surface,
    base_text; omit a field to wildcard it — e.g. `{"base_text": "swe_fol"}` drops a whole edition).
    Absent file → no exclusions. `surface` is compared lowercased (that's how rows are stored)."""
    if not path or not Path(path).exists():
        return []
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = doc.get("exclude", []) if isinstance(doc, dict) else doc
    rules = []
    for r in raw:
        rule = {k: (str(v).strip().lower() if k == "surface" else str(v))
                for k, v in r.items() if k in _EXCL_FIELDS}
        if rule:
            rules.append(rule)
    return rules


def apply_excludes(counts, rules: list[dict]):
    """Drop every count whose (lexeme, stem, sense, surface, base_text) matches a rule. Returns
    (kept_counts, n_dropped). Removal is total — the row is not re-emitted anonymized elsewhere."""
    if not rules:
        return counts, 0
    kept: collections.Counter = collections.Counter()
    dropped = 0
    for (lx, stem, se, su, bt), n in counts.items():
        row = {"lexeme": lx, "stem": stem, "sense": se, "surface": su, "base_text": bt}
        if any(all(row[k] == v for k, v in rule.items()) for rule in rules):
            dropped += 1
            continue
        kept[(lx, stem, se, su, bt)] = n
    return kept, dropped


def build_rows(counts, per_key, method: str, source_corpus: str, min_count: int) -> list[Row]:
    rows: list[Row] = [(lx, stem, se, su, n, n / per_key[(lx, stem, se, bt)], method, source_corpus, bt)
                       for (lx, stem, se, su, bt), n in counts.items() if n >= min_count]
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[8], -r[4]))  # group by (lex, stem, sense, base_text); top first
    return rows


def _render(rows: list[Row]) -> list[str]:
    return [f"{lx}\t{st}\t{se}\t{su}\t{c}\t{sh:.4f}\t{m}\t{sc}\t{bt}"
            for lx, st, se, su, c, sh, m, sc, bt in rows]


def write_parquet(rows: list[Row], dest: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as papq
    cols = list(zip(*rows)) if rows else ([],) * 9
    papq.write_table(pa.table({
        "lexeme": pa.array(cols[0], pa.string()), "stem": pa.array(cols[1], pa.string()),
        "sense": pa.array(cols[2], pa.string()), "surface": pa.array(cols[3], pa.string()),
        "count": pa.array(cols[4], pa.int32()),
        "share": pa.array([round(x, 4) for x in cols[5]], pa.float32()),
        "method": pa.array(cols[6], pa.string()), "source_corpus": pa.array(cols[7], pa.string()),
        "base_text": pa.array(cols[8], pa.string()),
    }), dest, compression="zstd")


def write_tsv(rows: list[Row], dest: Path) -> None:
    dest.write_text("\t".join(SCHEMA) + "\n" + "\n".join(_render(rows)) + "\n", encoding="utf-8")


def build_entry(rows: list[Row], method: str, min_count: int, books: int, lang_name: str | None,
                rel_file: str, sources: dict | None) -> dict:
    entry = {
        "language": lang_name, "method": method, "min_count": min_count, "testament": "OT",
        "books": books, "rows": len(rows), "source_corpus": rows[0][7] if rows else None,
        "base_texts": sorted({r[8] for r in rows}),          # the edition(s) attested (multi-version)
        "lexemes": len({r[0] for r in rows}), "lexeme_stem_senses": len({(r[0], r[1], r[2]) for r in rows}),
        "surfaces": len({r[3] for r in rows}), "file": rel_file,
        "content_sha256": hashlib.sha256("\n".join(_render(rows)).encode()).hexdigest(),
    }
    if sources:                                              # {base_text: license pointer} — per edition
        entry["sources"] = sources
    return {k: v for k, v in entry.items() if v is not None}


def update_manifest(path: Path, iso: str, entry: dict) -> None:
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc["schema"] = SCHEMA
    doc.setdefault("languages", {})[iso] = entry
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iso", default="ind", help="language partition (ISO 639-3); also the primary edition")
    ap.add_argument("--pool", default=None, help="comma-sep additional align isos to POOL into this one "
                    "language partition (e.g. --iso swe --pool swk → base_texts swe_fol+swe_svk, each row "
                    "tagged; cross-edition agreement derivable). Their base_text comes from data/sources.json.")
    ap.add_argument("--method", default="eflomal")
    ap.add_argument("--min-count", type=int, default=1)
    ap.add_argument("--lang-name", default=None)
    ap.add_argument("--base-text", default=None, help="override the PRIMARY iso's edition tag (default: source.edition)")
    ap.add_argument("--format", choices=["parquet", "tsv"], default="parquet")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--root", type=Path, default=Path("senses_attested"))
    ap.add_argument("--sources", type=Path, default=Path("data/sources.json"))
    ap.add_argument("--exclude", type=Path, default=Path("data/senses_exclude.json"),
                    help="optional takedown/exclusion config (committed, auditable); absent → no-op. "
                         "Rows matching any rule are DROPPED (not anonymized) + shares renormalise.")
    ap.add_argument("--publish", metavar="REPO_ID", default=None)
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    all_sources = json.loads(args.sources.read_text(encoding="utf-8")) if args.sources.exists() else {}
    pool_isos = [args.iso] + [s.strip() for s in (args.pool.split(",") if args.pool else []) if s.strip()]
    editions: list[tuple[str, str]] = []     # (align_iso, base_text) — one per edition pooled here
    sources: dict[str, dict] = {}            # base_text -> license pointer (per edition)
    for i, al_iso in enumerate(pool_isos):
        src = all_sources.get(al_iso)
        bt = (args.base_text if i == 0 and args.base_text else None) or (src or {}).get("edition") or al_iso
        editions.append((al_iso, bt))
        if src:
            sources[bt] = src
    counts, n_files = aggregate(args.out, editions, args.method)
    rules = load_excludes(args.exclude)
    counts, n_excl = apply_excludes(counts, rules)
    if rules:
        print(f"[senses_attested] exclude: {len(rules)} rule(s) from {args.exclude} → dropped {n_excl} "
              f"(lexeme,stem,sense,surface,base_text) row(s)", file=sys.stderr)
    per_key = _totals(counts)                                 # share denominator AFTER exclusion
    rows = build_rows(counts, per_key, args.method, hebrew_corpus(), args.min_count)
    if not rows:
        print(f"[senses_attested] {args.iso}: no sensed OT pairs (needs the enriched spine + OT books)",
              file=sys.stderr)
        return 0
    part = args.root / f"iso={args.iso}"
    part.mkdir(parents=True, exist_ok=True)
    rel_file = f"iso={args.iso}/data.{'parquet' if args.format == 'parquet' else 'tsv'}"
    dest = args.root / rel_file
    (write_parquet if args.format == "parquet" else write_tsv)(rows, dest)

    entry = build_entry(rows, args.method, args.min_count, n_files, args.lang_name, rel_file, sources)
    if rules:                                                # record the takedown application (auditable)
        entry["excluded"] = {"rules": len(rules), "rows_dropped": n_excl}
    update_manifest(args.root / "manifest.json", args.iso, entry)
    print(f"[senses_attested] {n_files} file(s) · {entry['rows']} rows · {entry['lexemes']} lexemes · "
          f"{entry['lexeme_stem_senses']} (lexeme,stem,sense) · base_texts={entry['base_texts']}  → {dest}",
          file=sys.stderr)

    if args.publish:
        publish_to_hf(args.root, args.iso, rel_file, entry, args.publish, args.create, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
