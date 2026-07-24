"""Aggregate per-verse alignment `.jsonl` into the published `lexeme-alignments` dataset.

Lexeme-anchored, provenance-honest, additive (docs/publishing-principles.md). Data contract:
    surface, lexeme, method, base_text, count, hi_conf
The anchor is the **lexeme** (MACULA `lang:augmented-strong`). `strong` and `share` are DELIBERATELY
NOT stored — both are exact, lossless derivations from the stored columns, so storing them is pure
duplication (measured 2026-07: ~32% smaller Parquet with them dropped, zero information lost):
  - `strong` from `lexeme`: split on `:` → strip any trailing augment letter → zero-pad to 4 digits →
    prefix H (hbo) or G (grc). e.g. "hbo:6498a" -> "H6498".
  - `share` (P(lexeme | surface) WITHIN a (method, base_text) group) from `count`: group rows by
    (surface, method, base_text), sum their `count`, then share = count / that sum.
See `scripts/strongs_view.py` for a ready-made Strong's-keyed derived view that does this for you.

Rows are the ADDITIVE UNION of the methods, each tagged with its source `method` (eflomal/gloss/gapfill)
— a pair attested by two methods is two rows, nothing merged away (principles 3 + 5). `hi_conf` =
fraction of the pair's occurrences that were intersection-backed (score >= 0.9). Content tokens only.

Scaling: the bulk data does NOT live in git (thousands of regenerated per-language files would
bloat history forever). Instead this writes an `iso=<iso>/`-**partitioned Parquet dataset** under
the dataset root (git-ignored) and updates a small, deterministic **`manifest.json`** (committed)
with per-language metadata + a content hash. Publish the Parquet partitions to a data channel
(e.g. a Hugging Face dataset or object storage); the manifest is git's durable record. See
`lexeme-alignments/README.md`. Derived Strong's-keyed / merged-best-pick views: scripts/ + that README.

    python3 -m lexeme_aligner.export_lex --iso ind --lang-name Indonesian   # auto-unions present methods
    → lexeme-alignments/iso=ind/data.parquet  (git-ignored)  +  lexeme-alignments/manifest.json  (committed)

    # ...then push the partition + manifest + card to a HF dataset. Authenticate once (cached login):
    #   python3 -c "from huggingface_hub import login; login()"
    python3 -m lexeme_aligner.export_lex --iso ind --publish bcv-commons/lexeme-alignments --create
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import LEX_ROOT, OUT, SPINE_DB

SCHEMA = ["surface", "lexeme", "method", "base_text", "count", "hi_conf"]   # the PUBLISHED columns
_HI_SCORE = 0.9   # eflomal intersection-backed link (both directions agree) — the reliable core
_METHODS = ("eflomal", "gloss", "gapfill")   # union order; a method absent for an iso is simply skipped

# Internal row shape keeps `strong` + `share` (needed for build_entry's testament/stat computation) —
# they're dropped only at the write_parquet/write_tsv/_render boundary, never stored on disk.
Row = tuple[str, str, str, str, str, int, float, float]


def _present_methods(out_dir: Path, iso: str) -> list[str]:
    """Which of the known methods have align_<method>_<iso>_*.jsonl for this language."""
    return [m for m in _METHODS if tag_files(out_dir, m, iso)]


def aggregate(out_dir: Path, editions: list[tuple[str, str]], methods: list[str],
              min_count: int) -> tuple[list[Row], int, list[str]]:
    """Fold the ADDITIVE UNION of the methods' align_<method>_<align_iso>_<BOOK>.jsonl content pairs into
    (surface, lexeme, strong, method, base_text, count, share, hi_conf). The anchor is the LEXEME; `strong`
    is its rollup. Rows are keyed by (surface, lexeme, METHOD, BASE_TEXT) — two honest provenance axes:
    `method` (how aligned: eflomal/gloss/gapfill) and `base_text` (which edition). A pair attested by two
    methods or two editions is separate rows — nothing merged away. `editions` is a list of
    (align_iso, base_text): POOLING several editions of one language into a single partition keeps every
    row tagged by its base_text, with `share` = P(lexeme | surface) computed WITHIN (method, base_text) —
    so cross-edition agreement (a surface→lexeme attested by >1 base_text) stays derivable from the rows.
    Falls back to strong-as-lexeme for pre-lexeme jsonl."""
    counts: collections.Counter = collections.Counter()          # (surface, lexeme, method, base_text) -> count
    hi: collections.Counter = collections.Counter()              # ... -> hi-conf count
    strong_of: dict[str, str] = {}                               # lexeme -> its Strong's rollup
    present: set[str] = set()
    books: set[str] = set()                                      # distinct BOOK names
    for align_iso, base_text in editions:
        for method in methods:
            files = tag_files(out_dir, method, align_iso)
            if not files:
                continue
            present.add(method)
            books.update(fp.stem.rsplit("_", 1)[-1] for fp in files)
            for fp in files:
                with fp.open(encoding="utf-8") as fh:
                    for line in fh:
                        for p in json.loads(line)["pairs"]:
                            if not p.get("content") or not p.get("strong") or not p.get("target"):
                                continue
                            lexeme = p.get("lexeme") or p["strong"]  # pre-lexeme jsonl → strong is the key
                            key = (p["target"].strip().lower(), lexeme, method, base_text)
                            counts[key] += 1
                            strong_of[lexeme] = p["strong"]
                            if (p.get("score") or 0) >= _HI_SCORE:
                                hi[key] += 1
    present_ordered = [m for m in methods if m in present]
    if not present_ordered:
        raise SystemExit(f"no align_<{'|'.join(methods)}>_<{','.join(i for i, _ in editions)}>_*.jsonl "
                         f"under {out_dir} — run the aligner first")

    # share is WITHIN (method, base_text): P(lexeme | surface) among that edition's rows for that method
    per_surface: collections.Counter = collections.Counter()      # (surface, method, base_text) -> total
    for (surface, _lexeme, method, base_text), n in counts.items():
        per_surface[(surface, method, base_text)] += n

    rows: list[Row] = [(surface, lexeme, strong_of[lexeme], method, base_text, n,
                        n / per_surface[(surface, method, base_text)], hi[(surface, lexeme, method, base_text)] / n)
                       for (surface, lexeme, method, base_text), n in counts.items() if n >= min_count]
    # group each surface's candidates, strongest first, then by method + edition — deterministic
    rows.sort(key=lambda r: (r[0], -r[5], r[1], r[3], r[4]))
    return rows, len(books), present_ordered


def _render(rows: list[Row]) -> list[str]:
    """Canonical per-row text — the format-independent basis for the content hash and TSV body.
    `strong` + `share` (row indices 2, 6) are excluded — see the module docstring for their exact,
    lossless derivation from `lexeme`/`count`."""
    return [f"{s}\t{lx}\t{m}\t{bt}\t{c}\t{hc:.4f}" for s, lx, _g, m, bt, c, _sh, hc in rows]


def write_parquet(rows: list[Row], dest: Path) -> None:
    import pyarrow as pa                                         # optional dep — see [publish] extra
    import pyarrow.parquet as papq
    cols = list(zip(*rows)) if rows else ([], [], [], [], [], [], [], [])
    table = pa.table({
        "surface": pa.array(cols[0], pa.string()),
        "lexeme": pa.array(cols[1], pa.string()),
        "method": pa.array(cols[3], pa.string()),
        "base_text": pa.array(cols[4], pa.string()),
        "count": pa.array(cols[5], pa.int32()),
        "hi_conf": pa.array([round(x, 4) for x in cols[7]], pa.float32()),
    })
    papq.write_table(table, dest, compression="zstd")


def write_tsv(rows: list[Row], dest: Path) -> None:
    dest.write_text("\t".join(SCHEMA) + "\n" + "\n".join(_render(rows)) + "\n", encoding="utf-8")


def _spine_tags() -> dict:
    """Provenance of the original backbone (uhb/ugnt tags), best-effort — omitted if spine absent."""
    try:
        con = sqlite3.connect(f"file:{SPINE_DB}?mode=ro", uri=True)
        tags = dict(con.execute("SELECT key, value FROM spine_meta").fetchall())
        con.close()
        return {k: tags[k] for k in ("uhb_tag", "ugnt_tag") if k in tags}
    except sqlite3.Error:
        return {}


def build_entry(rows: list[Row], iso: str, methods: list[str], min_count: int, books: int,
                lang_name: str | None, rel_file: str, sources: dict | None = None) -> dict:
    strongs = {r[2] for r in rows}
    testaments = {"NT" if s.startswith("G") else "OT" for s in strongs}
    by_method: collections.Counter = collections.Counter(r[3] for r in rows)
    by_base_text: collections.Counter = collections.Counter(r[4] for r in rows)
    entry = {
        "language": lang_name,
        "methods": methods,                          # the additive union present in this partition
        "by_method": {m: by_method[m] for m in methods},   # rows contributed per method (provenance)
        "base_texts": sorted(by_base_text),          # the pooled editions in this language partition
        "by_base_text": dict(by_base_text),          # rows contributed per edition (provenance)
        "min_count": min_count,
        "testament": "+".join(sorted(testaments)),   # OT / NT / NT+OT
        "books": books,
        "rows": len(rows),
        "surfaces": len({r[0] for r in rows}),
        "lexemes": len({r[1] for r in rows}),
        "strongs": len(strongs),
        "hi_conf_ge_0.9": sum(1 for r in rows if r[7] >= _HI_SCORE),
        "file": rel_file,
        # content hash over the canonical rows — stable across formats and library versions, so the
        # manifest only changes in git when the DATA actually changes (not on a pyarrow bump).
        "content_sha256": hashlib.sha256("\n".join(_render(rows)).encode()).hexdigest(),
    }
    spine = _spine_tags()
    if spine:
        entry["spine"] = spine
    # `sources` maps each pooled base_text → a POINTER to where that edition's license authoritatively
    # lives (provider/edition + license_url) — we never copy the licence text. Catalogue data is CC0;
    # each source keeps its own terms at that link. See data/sources.json.
    if sources:
        entry["sources"] = sources
    return {k: v for k, v in entry.items() if v is not None}


_COMPANION_RESOURCES = ["light_lexemes.json", "hebrew_lexeme_strong.json", "greek_morph_strong.json",
                        "contest_rule.json"]   # global (not per-iso) — re-uploaded each publish so they
                                                # never drift out of sync; see README's "Companion reference
                                                # resources" section for what each one is


def _publish_state_path(root: Path) -> Path:
    return root / ".publish_state.json"


def _sha256_file(fp: Path) -> str:
    return hashlib.sha256(fp.read_bytes()).hexdigest()


def publish_to_hf(root: Path, iso: str, rel_file: str, entry: dict,
                  repo_id: str, create: bool, dry_run: bool) -> None:
    """Upload this language's partition + the manifest + the dataset card + the companion reference
    resources to a HF dataset repo. Other isos' local partitions (git-ignored) are untouched.

    The shared files (manifest/README/companions) are identical across every language in a batch —
    without a cache, each language's call re-attempts to upload all of them, and the Hub silently
    no-ops the unchanged ones server-side ("No files have been modified... skipping to prevent empty
    commit"), wasting a network round trip per file per language. `.publish_state.json` (local,
    gitignored, one per dataset root) caches the sha256 last successfully pushed per (repo, path) —
    skip the upload_file call entirely when the local content still matches what's already up there."""
    uploads = [rel_file, "manifest.json", "README.md", *_COMPANION_RESOURCES]
    present = [f for f in uploads if (root / f).exists()]
    print(f"[publish] → dataset '{repo_id}'  files: {present}", file=sys.stderr)
    if dry_run:
        print("[publish] dry-run — nothing pushed", file=sys.stderr)
        return
    try:
        from huggingface_hub import CommitOperationAdd, HfApi     # optional dep — see [publish] extra
        from huggingface_hub.errors import HfHubHTTPError
    except ImportError:
        raise SystemExit("[publish] needs huggingface_hub — pip install -e '.[publish]'")
    api = HfApi()
    try:
        api.whoami()                                             # uses HF_TOKEN env or cached login
    except Exception:
        raise SystemExit("[publish] not authenticated — run `huggingface-cli login` or set HF_TOKEN")
    if create:
        api.create_repo(repo_id, repo_type="dataset", exist_ok=True)
    msg = f"lexeme-alignments {iso}: {entry['rows']} rows ({entry['content_sha256'][:12]})"
    state_fp = _publish_state_path(root)
    state = json.loads(state_fp.read_text(encoding="utf-8")) if state_fp.exists() else {}
    repo_state = state.setdefault(repo_id, {})

    digests = {rel: _sha256_file(root / rel) for rel in present}
    changed = [rel for rel in present if repo_state.get(rel) != digests[rel]]
    skipped = len(present) - len(changed)
    if not changed:
        print(f"[publish] 0 file(s) changed ({skipped} unchanged, cache-skipped) — no commit needed "
              f"for {repo_id}", file=sys.stderr)
        return
    # ONE commit for every changed file (not one upload_file() call per file) — HF's commit-rate limit
    # is per-COMMIT (128/hour), not per-file, so bundling directly multiplies effective batch throughput
    # and is what the Hub's own 429 error message recommends ("upload entire folders at once").
    ops = [CommitOperationAdd(path_in_repo=rel, path_or_fileobj=str(root / rel)) for rel in changed]
    try:
        api.create_commit(repo_id=repo_id, repo_type="dataset", operations=ops, commit_message=msg)
    except HfHubHTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            raise SystemExit(
                f"[publish] HIT HF COMMIT RATE LIMIT (429) while publishing {iso} — "
                f"HF caps commits at 128/hour per repo. Nothing was corrupted; the local "
                f".publish_state.json only records what actually succeeded, so re-running the same "
                f"batch command later will resume from here, not restart. Wait ~1 hour, then re-run."
            ) from e
        raise
    for rel in changed:
        repo_state[rel] = digests[rel]
    state_fp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[publish] pushed {len(changed)} file(s) in 1 commit ({skipped} unchanged, cache-skipped) "
          f"to {repo_id}", file=sys.stderr)


def update_manifest(path: Path, iso: str, entry: dict) -> None:
    """Merge one language's entry into the deterministic (sorted, timestamp-free) manifest."""
    doc = {"schema": SCHEMA, "languages": {}}
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
    doc["schema"] = SCHEMA
    doc.setdefault("languages", {})[iso] = entry
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iso", default="ind", help="language partition (ISO 639-3); also the primary edition")
    ap.add_argument("--pool", default=None,
                    help="comma-sep additional align isos to POOL into this one language partition "
                         "(e.g. --iso eng --pool engy → base_texts BSB+eng_ylt, each row tagged; "
                         "cross-edition agreement derivable). Their base_text comes from data/sources.json.")
    ap.add_argument("--base-text", default=None,
                    help="override the PRIMARY iso's edition tag (default: its source.edition)")
    ap.add_argument("--methods", default=None,
                    help="comma-sep methods to UNION into the partition (default: auto-detect present, "
                         "e.g. eflomal,gloss,gapfill). Each row is tagged with its source method.")
    ap.add_argument("--min-count", type=int, default=1,
                    help="drop (surface,lexeme,method,base_text) below this count")
    ap.add_argument("--lang-name", default=None, help="human language name, recorded in the manifest")
    ap.add_argument("--format", choices=["parquet", "tsv"], default="parquet")
    ap.add_argument("--out", type=Path, default=OUT, help="dir holding the align_*.jsonl (input)")
    ap.add_argument("--root", type=Path, default=LEX_ROOT,
                    help="dataset root — partitions go to <root>/iso=<iso>/, manifest to <root>/manifest.json")
    ap.add_argument("--sources", type=Path, default=Path("data/sources.json"),
                    help="per-iso source pointers (provider/edition/license_url) for manifest provenance")
    ap.add_argument("--publish", metavar="REPO_ID", default=None,
                    help="HF dataset repo to upload this partition + manifest to (e.g. bcv-commons/lexeme-alignments)")
    ap.add_argument("--create", action="store_true",
                    help="create the HF dataset repo if missing (with --publish)")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --publish, print the upload plan without pushing")
    args = ap.parse_args()

    # editions pooled into this ONE language partition: (align_iso, base_text). base_text per iso comes
    # from data/sources.json's `edition` (falls back to the iso). The primary iso's tag can be overridden.
    all_sources = json.loads(args.sources.read_text(encoding="utf-8")) if args.sources.exists() else {}
    pool_isos = [args.iso] + [s.strip() for s in (args.pool.split(",") if args.pool else []) if s.strip()]
    editions: list[tuple[str, str]] = []
    sources: dict[str, dict] = {}                                 # base_text -> license pointer (per edition)
    for i, al_iso in enumerate(pool_isos):
        src = all_sources.get(al_iso)
        bt = (args.base_text if i == 0 and args.base_text else None) or (src or {}).get("edition") or al_iso
        editions.append((al_iso, bt))
        if src:
            sources[bt] = src

    methods = ([m.strip() for m in args.methods.split(",") if m.strip()] if args.methods
               else _present_methods(args.out, args.iso))
    rows, n_files, present = aggregate(args.out, editions, methods, args.min_count)
    part = args.root / f"iso={args.iso}"
    part.mkdir(parents=True, exist_ok=True)
    rel_file = f"iso={args.iso}/data.{'parquet' if args.format == 'parquet' else 'tsv'}"
    dest = args.root / rel_file
    (write_parquet if args.format == "parquet" else write_tsv)(rows, dest)

    entry = build_entry(rows, args.iso, present, args.min_count, n_files, args.lang_name,
                        rel_file, sources or None)
    update_manifest(args.root / "manifest.json", args.iso, entry)

    print(f"[export_lex] {n_files} books · union{present} · base_texts{entry['base_texts']} · "
          f"{entry['rows']} rows · {entry['surfaces']} surfaces · {entry['lexemes']} lexemes · "
          f"{entry['strongs']} Strong's · {entry['hi_conf_ge_0.9']} hi-conf  → {dest}", file=sys.stderr)

    if args.publish:
        publish_to_hf(args.root, args.iso, rel_file, entry, args.publish, args.create, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
