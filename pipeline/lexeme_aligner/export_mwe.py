"""Aggregate per-verse alignments into `aligned_mwe` — multi-word target expressions per lexeme.

`lexeme-alignments` is one row per surface TOKEN, so a lexeme rendered by a phrase (חֶסֶד → "kasih setia")
is either split across single-token rows or space-joined into an unreliable surface (scattered tokens
that merely all linked to the lexeme). This mines the REAL multi-word expressions using the `t_idx`
positions now carried in the jsonl (`run_pilot`): a pair is an MWE only when its target positions are
**contiguous** (`max−min+1 == len(t_idx)`) — a genuine adjacent span, not a join artifact.

    lexeme, strong, phrase, n_words, source_corpus, base_text, count, share, contig
    # share = count / Σ per (base_text, lexeme): WITHIN one edition, like lexeme-alignments/senses_attested
EVERY edition of a language is pooled into the one `iso=<lang>` partition (`--pool`), each row tagged by the
`base_text` it came from; there is no "primary" edition. Needs jsonl produced AFTER the t_idx change (re-align to populate). Same partitioned-Parquet + committed
manifest layout as lexeme-alignments; CC0 (phrases + counts + ids, no MACULA analysis).

`--method all` (the default) UNIONS every method present for this tag (eflomal/gloss/gapfill) — a phrase
either method found contributes to the same (lexeme, phrase) count, same "additive coverage, not a
single winner" spirit as lexeme-alignments' method union (though here counts blend rather than staying
split per method — this dataset doesn't track per-row provenance). Pass an explicit method name (or a
comma-separated list) to restrict to just that.

    python3 -m lexeme_aligner.export_mwe --iso ind --lang-name Indonesian
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import methods_present, tag_files, tag_files_any_method
from lexeme_aligner.hebrew_source import spine_corpus
from lexeme_aligner.config import HF_CHUNK_SIZE, OUT
from lexeme_aligner.export_lex import publish_to_hf
from lexeme_aligner.manifest_io import update_json

SCHEMA = ["lexeme", "strong", "phrase", "n_words", "source_corpus", "base_text", "count", "share", "contig"]
# `source_corpus` = which ORIGINAL-language text these spans were aligned against
# (hebrew_source.spine_corpus). Same role and name as in lexeme-alignments/senses_attested: a phrase
# attested against Nestle1904 and one attested against a Byzantine spine both key on the same bare
# `grc:<strong>` lexeme, so without it they are indistinguishable once pooled. `base_text` is the target
# half: which edition a phrase was attested in (every edition of the language is pooled; none is primary).
# This dataset still carries no per-row `method` (counts blend the methods present).
_CANDIDATE_METHODS = ["eflomal", "gloss", "gapfill"]


def publish_all_to_hf(root: Path, repo_id: str, create: bool, dry_run: bool,
                      chunk_size: int = HF_CHUNK_SIZE) -> None:
    """Bulk-publish EVERY already-exported language partition + manifest/README in one chunked batch —
    same pattern as export_lex.publish_all_to_hf. Assumes every partition already exists locally."""
    from lexeme_aligner.hf_bulk_publish import publish_chunked
    partitions = sorted(str(fp.relative_to(root)) for fp in root.glob("iso=*/data.parquet"))
    shared = [f for f in ["manifest.json", "README.md"] if (root / f).exists()]
    publish_chunked(root, repo_id, partitions + shared, create, dry_run, chunk_size, label="aligned_mwe")


def _contiguous(t_idx: list[int]) -> bool:
    return len(t_idx) >= 2 and (max(t_idx) - min(t_idx) + 1 == len(t_idx))


def _resolve_files(out_dir: Path, iso: str, method: str) -> tuple[list[Path], str]:
    """method="all" -> every method present for this tag (union); otherwise a comma-separated list of
    specific method names. Returns (files, methods-label-for-the-manifest)."""
    if method == "all":
        found = methods_present(out_dir, iso, _CANDIDATE_METHODS)
        return tag_files_any_method(out_dir, iso), ("+".join(found) if found else "all")
    names = [m.strip() for m in method.split(",") if m.strip()]
    files = [fp for m in names for fp in tag_files(out_dir, m, iso)]
    return sorted(files), "+".join(names)


def aggregate(out_dir: Path, editions, method: str = "all"):
    """Fold contiguous multi-token content spans into (base_text, lexeme, phrase) counts, across every method
    present for each edition by default (method="all"). `editions` is a list of (align tag, base_text), one
    per edition pooled into this language (a bare tag string means a single edition labelled by the tag).
    Returns also how many multi-word pairs were dropped as scattered (non-contiguous) — reported, never
    silently ignored."""
    if isinstance(editions, str):
        editions = [(editions, editions)]
    counts: collections.Counter = collections.Counter()      # (base_text, lexeme, strong, phrase, n_words) -> count
    lex_strong: dict[str, str] = {}
    seen_tidx = scattered = n_files = 0
    methods_seen: list[str] = []
    for tag, base_text in editions:
        files, methods_label = _resolve_files(out_dir, tag, method)
        if not files:
            print(f"[aligned_mwe] skip edition {tag}: no align_*_{tag}_*.jsonl under {out_dir}", file=sys.stderr)
            continue
        n_files += len(files)
        methods_seen += [m for m in methods_label.split("+") if m not in methods_seen]
        for fp in files:
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    for p in json.loads(line)["pairs"]:
                        if not p.get("content") or not p.get("lexeme"):
                            continue
                        ti = p.get("t_idx")
                        if ti is None:
                            continue                          # pre-t_idx jsonl — re-align to populate
                        seen_tidx += 1
                        if len(ti) < 2:
                            continue
                        if not _contiguous(ti):
                            scattered += 1
                            continue
                        phrase = (p.get("target") or "").strip().lower()
                        if not phrase:
                            continue
                        counts[(base_text, p["lexeme"], p.get("strong"), phrase, len(ti))] += 1
                        lex_strong[p["lexeme"]] = p.get("strong")
    if not n_files:
        raise SystemExit(f"no align_*_<tag>_*.jsonl under {out_dir} for any of "
                         f"{[t for t, _ in editions]} — run the aligner first")
    per_lex: collections.Counter = collections.Counter()
    for (bt, lexeme, _st, _ph, _n), n in counts.items():
        per_lex[(bt, lexeme)] += n
    return counts, per_lex, n_files, seen_tidx, scattered, "+".join(methods_seen) or "all"


def build_rows(counts, per_lex, min_count: int) -> list[tuple]:
    corpus = spine_corpus()                                   # source-side provenance — see SCHEMA
    rows = [(lx, st, ph, n_w, corpus, bt, n, n / per_lex[(bt, lx)], True)
            for (bt, lx, st, ph, n_w), n in counts.items() if n >= min_count]
    rows.sort(key=lambda r: (r[5], r[0], -r[6]))              # edition, then lexeme, most frequent phrase first
    return rows


def _render(rows) -> list[str]:
    return [f"{lx}\t{st}\t{ph}\t{n_w}\t{sc}\t{bt}\t{c}\t{sh:.4f}\t{int(cg)}"
            for lx, st, ph, n_w, sc, bt, c, sh, cg in rows]


def write_parquet(rows, dest: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as papq
    cols = list(zip(*rows)) if rows else ([],) * 9
    papq.write_table(pa.table({
        "lexeme": pa.array(cols[0], pa.string()), "strong": pa.array(cols[1], pa.string()),
        "phrase": pa.array(cols[2], pa.string()), "n_words": pa.array(cols[3], pa.int32()),
        "source_corpus": pa.array(cols[4], pa.string()),
        "base_text": pa.array(cols[5], pa.string()),
        "count": pa.array(cols[6], pa.int32()),
        "share": pa.array([round(x, 4) for x in cols[7]], pa.float32()),
        "contig": pa.array(cols[8], pa.bool_()),
    }), dest, compression="zstd")


def build_entry(rows, method: str, min_count: int, books: int, lang_name: str | None,
                rel_file: str, scattered: int, sources: dict | None) -> dict:
    by_bt = collections.Counter(r[5] for r in rows)
    entry = {
        "language": lang_name, "method": method, "min_count": min_count, "books": books,
        "rows": len(rows), "lexemes": len({r[0] for r in rows}), "phrases": len({r[2] for r in rows}),
        "base_texts": sorted(by_bt), "by_base_text": dict(sorted(by_bt.items())),
        "scattered_dropped": scattered, "file": rel_file,
        "content_sha256": hashlib.sha256("\n".join(_render(rows)).encode()).hexdigest(),
    }
    if sources:
        entry["sources"] = sources                            # base_text -> license pointer, one per edition
    return {k: v for k, v in entry.items() if v is not None}


def update_manifest(path: Path, iso: str, entry: dict) -> None:
    def _merge(doc: dict) -> None:
        doc["schema"] = SCHEMA
        doc.setdefault("languages", {})[iso] = entry

    update_json(path, _merge)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--publish-all", metavar="REPO_ID", default=None,
                    help="bulk-publish EVERY already-exported iso=*/data.parquet in one chunked batch, "
                         "instead of exporting one language")
    ap.add_argument("--chunk-size", type=int, default=HF_CHUNK_SIZE,
                    help="files per HF commit, with --publish-all (default from ALIGNER_HF_CHUNK_SIZE)")
    ap.add_argument("--iso", default="ind", help="an alignment TAG to read align_*.jsonl from (with --pool, the "
                    "first of the editions pooled into this language — an argument position, not a privileged edition)")
    ap.add_argument("--pool", default=None, help="comma-separated further alignment TAGS of the SAME language, "
                    "pooled into one partition, each row tagged by its own base_text")
    ap.add_argument("--base-text", default=None, help="label for the --iso tag's rows (default: its sources.json "
                    "edition, else the tag)")
    ap.add_argument("--publish-iso", default=None,
                    help="the bare published iso, when --iso is an edition TAG that differs from it "
                         "(e.g. --iso arb_vdv --publish-iso arb) — the output partition lands at "
                         "iso=arb/ (not iso=arb_vdv/), consistent with lexeme-alignments/"
                         "senses_attested. Defaults to --iso.")
    ap.add_argument("--method", default="all", help="'all' (default) unions every method present; "
                    "or a specific name / comma-separated list to restrict to")
    ap.add_argument("--min-count", type=int, default=1)
    ap.add_argument("--lang-name", default=None)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--root", type=Path, default=Path("publish/aligned_mwe"))
    ap.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    ap.add_argument("--publish", metavar="REPO_ID", default=None)
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.publish_all:
        publish_all_to_hf(args.root, args.publish_all, args.create, args.dry_run, args.chunk_size)
        return 0

    publish_iso = args.publish_iso or args.iso
    all_sources = json.loads(args.sources.read_text(encoding="utf-8")) if args.sources.exists() else {}
    pool_tags = [args.iso] + [t.strip() for t in (args.pool.split(",") if args.pool else []) if t.strip()]
    editions: list[tuple[str, str]] = []      # (align tag, base_text) — one per edition pooled here
    sources: dict[str, dict] = {}             # base_text -> license pointer
    for i, tag in enumerate(pool_tags):
        src = all_sources.get(tag)
        bt = (args.base_text if i == 0 and args.base_text else None) or (src or {}).get("edition") or tag
        editions.append((tag, bt))
        if src:
            sources[bt] = src
    counts, per_lex, n_files, seen, scattered, methods_label = aggregate(args.out, editions, args.method)
    if seen == 0:
        print(f"[aligned_mwe] {args.iso}: no t_idx in jsonl — re-align (run_pilot) to carry positions",
              file=sys.stderr)
        return 0
    rows = build_rows(counts, per_lex, args.min_count)
    part = args.root / f"iso={publish_iso}"
    part.mkdir(parents=True, exist_ok=True)
    rel_file = f"iso={publish_iso}/data.parquet"
    write_parquet(rows, args.root / rel_file)

    entry = build_entry(rows, methods_label, args.min_count, n_files, args.lang_name, rel_file, scattered, sources)
    update_manifest(args.root / "manifest.json", publish_iso, entry)
    print(f"[aligned_mwe] {n_files} file(s) · {len(rows)} contiguous MWEs · {entry['lexemes']} lexemes · "
          f"{entry['phrases']} phrases  ({scattered} scattered spans dropped)  → {args.root / rel_file}",
          file=sys.stderr)
    if args.publish:
        publish_to_hf(args.root, publish_iso, rel_file, entry, args.publish, args.create, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
