"""Aggregate per-verse alignments into `aligned_mwe` — multi-word target expressions per lexeme.

`lexeme-alignments` is one row per surface TOKEN, so a lexeme rendered by a phrase (חֶסֶד → "kasih setia")
is either split across single-token rows or space-joined into an unreliable surface (scattered tokens
that merely all linked to the lexeme). This mines the REAL multi-word expressions using the `t_idx`
positions now carried in the jsonl (`run_pilot`): a pair is an MWE only when its target positions are
**contiguous** (`max−min+1 == len(t_idx)`) — a genuine adjacent span, not a join artifact.

    lexeme, strong, phrase, n_words, count, share, contig     # share = count / Σ per lexeme
Needs jsonl produced AFTER the t_idx change (re-align to populate). Same partitioned-Parquet + committed
manifest layout as lexeme-alignments; CC0 (phrases + counts + ids, no MACULA analysis).

    python3 -m lexeme_aligner.export_mwe --iso ind --method eflomal --lang-name Indonesian
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import OUT
from lexeme_aligner.export_lex import publish_to_hf

SCHEMA = ["lexeme", "strong", "phrase", "n_words", "count", "share", "contig"]


def _contiguous(t_idx: list[int]) -> bool:
    return len(t_idx) >= 2 and (max(t_idx) - min(t_idx) + 1 == len(t_idx))


def aggregate(out_dir: Path, iso: str, method: str):
    """Fold contiguous multi-token content spans into (lexeme, phrase) counts. Returns also how many
    multi-word pairs were dropped as scattered (non-contiguous) — reported, never silently ignored."""
    counts: collections.Counter = collections.Counter()      # (lexeme, strong, phrase, n_words) -> count
    lex_strong: dict[str, str] = {}
    files = tag_files(out_dir, method, iso)
    if not files:
        raise SystemExit(f"no align_{method}_{iso}_*.jsonl under {out_dir} — run the aligner first")
    seen_tidx = scattered = 0
    for fp in files:
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                for p in json.loads(line)["pairs"]:
                    if not p.get("content") or not p.get("lexeme"):
                        continue
                    ti = p.get("t_idx")
                    if ti is None:
                        continue                              # pre-t_idx jsonl — re-align to populate
                    seen_tidx += 1
                    if len(ti) < 2:
                        continue
                    if not _contiguous(ti):
                        scattered += 1
                        continue
                    phrase = (p.get("target") or "").strip().lower()
                    if not phrase:
                        continue
                    counts[(p["lexeme"], p.get("strong"), phrase, len(ti))] += 1
                    lex_strong[p["lexeme"]] = p.get("strong")
    per_lex: collections.Counter = collections.Counter()
    for (lexeme, _st, _ph, _n), n in counts.items():
        per_lex[lexeme] += n
    return counts, per_lex, len(files), seen_tidx, scattered


def build_rows(counts, per_lex, min_count: int) -> list[tuple]:
    rows = [(lx, st, ph, n_w, n, n / per_lex[lx], True)
            for (lx, st, ph, n_w), n in counts.items() if n >= min_count]
    rows.sort(key=lambda r: (r[0], -r[4]))                    # group by lexeme, most frequent phrase first
    return rows


def _render(rows) -> list[str]:
    return [f"{lx}\t{st}\t{ph}\t{n_w}\t{c}\t{sh:.4f}\t{int(cg)}" for lx, st, ph, n_w, c, sh, cg in rows]


def write_parquet(rows, dest: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as papq
    cols = list(zip(*rows)) if rows else ([],) * 7
    papq.write_table(pa.table({
        "lexeme": pa.array(cols[0], pa.string()), "strong": pa.array(cols[1], pa.string()),
        "phrase": pa.array(cols[2], pa.string()), "n_words": pa.array(cols[3], pa.int32()),
        "count": pa.array(cols[4], pa.int32()),
        "share": pa.array([round(x, 4) for x in cols[5]], pa.float32()),
        "contig": pa.array(cols[6], pa.bool_()),
    }), dest, compression="zstd")


def build_entry(rows, method: str, min_count: int, books: int, lang_name: str | None,
                rel_file: str, scattered: int, source: dict | None) -> dict:
    entry = {
        "language": lang_name, "method": method, "min_count": min_count, "books": books,
        "rows": len(rows), "lexemes": len({r[0] for r in rows}), "phrases": len({r[2] for r in rows}),
        "scattered_dropped": scattered, "file": rel_file,
        "content_sha256": hashlib.sha256("\n".join(_render(rows)).encode()).hexdigest(),
    }
    if source:
        entry["source"] = source
    return {k: v for k, v in entry.items() if v is not None}


def update_manifest(path: Path, iso: str, entry: dict) -> None:
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc["schema"] = SCHEMA
    doc.setdefault("languages", {})[iso] = entry
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", default="ind")
    ap.add_argument("--method", default="eflomal")
    ap.add_argument("--min-count", type=int, default=1)
    ap.add_argument("--lang-name", default=None)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--root", type=Path, default=Path("aligned_mwe"))
    ap.add_argument("--sources", type=Path, default=Path("data/sources.json"))
    ap.add_argument("--publish", metavar="REPO_ID", default=None)
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    counts, per_lex, n_files, seen, scattered = aggregate(args.out, args.iso, args.method)
    if seen == 0:
        print(f"[aligned_mwe] {args.iso}: no t_idx in jsonl — re-align (run_pilot) to carry positions",
              file=sys.stderr)
        return 0
    rows = build_rows(counts, per_lex, args.min_count)
    part = args.root / f"iso={args.iso}"
    part.mkdir(parents=True, exist_ok=True)
    rel_file = f"iso={args.iso}/data.parquet"
    write_parquet(rows, args.root / rel_file)

    source = json.loads(args.sources.read_text(encoding="utf-8")).get(args.iso) if args.sources.exists() else None
    entry = build_entry(rows, args.method, args.min_count, n_files, args.lang_name, rel_file, scattered, source)
    update_manifest(args.root / "manifest.json", args.iso, entry)
    print(f"[aligned_mwe] {n_files} file(s) · {len(rows)} contiguous MWEs · {entry['lexemes']} lexemes · "
          f"{entry['phrases']} phrases  ({scattered} scattered spans dropped)  → {args.root / rel_file}",
          file=sys.stderr)
    if args.publish:
        publish_to_hf(args.root, args.iso, rel_file, entry, args.publish, args.create, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
