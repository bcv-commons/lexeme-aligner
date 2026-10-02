"""Publish a SUBSET of languages to Hugging Face without making the dataset lie or losing anything (2026-10-01).

Why this exists. The stock publishers are made for "publish everything": `publish_lang.py`/`export_lex.publish_to_hf` upload the LOCAL
manifest.json whole, and the local manifest already describes languages whose new data is not on Hugging Face yet — a partial publish
would leave the HF manifest claiming rows/hashes that the HF partitions do not have. `publish_all_to_hf` detects "deleted" files from
a local cache and, pointed at a subset, would delete the languages left out. This wrapper avoids both BY CONSTRUCTION:

  * MERGE-ON-HF-MANIFEST: it downloads the manifest that is on Hugging Face NOW and replaces ONLY the selected languages' entries with the
    local ones. Every other language's entry stays exactly as published.
  * a persistent STAGING root per dataset holds hard links to just the selected files + the merged manifest, so only those are pushed;
  * `detect_deletions=False` always: nothing on HF is ever removed;
  * PREFLIGHT per language (a failing language is left out and reported): not mid-rewrite by a running chain, partition rows == manifest
    rows, ledger entry present, every edition has compact-alignments with all its book files, not stale (unchanged since the last publish),
    and the dataset schema equals the one on HF (a schema change must go out for ALL languages at once);
  * DRY-RUN unless `--push`; after a push it VERIFIES: HF manifest entries == local for the pushed languages and unchanged for all others,
    and each pushed partition's SHA-256 on HF == the local file.
  * aligned_mwe is excluded unless `--include-mwe` (its base_text schema is not on HF yet: the schema guard would refuse anyway).

    .venv/bin/python pipeline/scripts/publish_safe.py --iso tgl,ind                       # dry run: shows exactly what would happen
    .venv/bin/python pipeline/scripts/publish_safe.py --iso tgl,ind --push                # publish (needs `hf auth login` / HF_TOKEN)
    .venv/bin/python pipeline/scripts/publish_safe.py --ready-file pipeline/work/logs/publish_ready_latest.json --push
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pipeline"))

DATASETS = {                                   # local dir name -> (HF repo, kind)
    "lexeme-alignments": ("bcv-commons/lexeme-alignments", "partition"),
    "senses_attested": ("bcv-commons/senses-attested", "partition"),
    "compact-alignments": ("bcv-commons/compact-alignments", "compact"),
    "aligned_mwe": ("bcv-commons/aligned-mwe", "partition"),
}
DEFAULT = ["lexeme-alignments", "senses_attested", "compact-alignments"]
STAGING = REPO / "pipeline/work/publish-staging"
STALE_BEFORE = time.mktime(time.strptime("2026-09-28", "%Y-%m-%d"))


# ---- pure helpers (unit-tested) -------------------------------------------------------------------------------------------------
def merge_manifest(hf: dict, local: dict, isos: list[str]) -> dict:
    """HF's manifest with ONLY `isos` replaced by the local entries; top-level keys other than `languages` come from `hf` (so the
    published schema stays exactly as published). A selected language missing locally is an error, never silently dropped."""
    out = {k: v for k, v in hf.items() if k != "languages"}
    langs = dict(hf.get("languages", {}))
    for iso in isos:
        if iso not in local.get("languages", {}):
            raise KeyError(f"{iso}: no local manifest entry")
        langs[iso] = local["languages"][iso]
    out["languages"] = dict(sorted(langs.items()))
    return out


def schema_conflict(hf: dict, local: dict, kind: str = "partition") -> str | None:
    """None when a partial publish cannot mix schemas; else a description.
    `partition` datasets (parquet): the column list must be IDENTICAL — an extra column changes the file schema, so it goes out for ALL
    languages at once. `compact` datasets: `schema` is a list of documentation paragraphs about the sidecar channels, so a purely ADDITIVE
    change (every paragraph on HF still present unchanged) is allowed; `tokenizer_version` must always match (it changes decoding)."""
    if hf.get("tokenizer_version") != local.get("tokenizer_version"):
        return "tokenizer_version differs between Hugging Face and local (it changes how positions decode)"
    a, b = hf.get("schema"), local.get("schema")
    if a == b:
        return None
    if kind == "compact" and isinstance(a, list) and isinstance(b, list) and all(x in b for x in a):
        return None
    return "schema differs between Hugging Face and local"


def merged_top_level(hf: dict, local: dict, kind: str) -> dict:
    """Top-level (non-language) keys for the merged manifest: HF's, except a purely additive compact documentation change, which is local's."""
    top = {k: v for k, v in hf.items() if k != "languages"}
    if kind == "compact" and top.get("schema") != local.get("schema"):
        top["schema"] = local["schema"]
    return top


def running_isos(ps_text: str) -> set[str]:
    """Language isos / edition tags that a running aligner process is working on (--iso X, --publish-iso X)."""
    return set(re.findall(r"lexeme_aligner\.\w+ .*?--(?:publish-)?iso ([A-Za-z0-9_]+)", ps_text)) | \
        set(re.findall(r"--publish-iso ([A-Za-z0-9_]+)", ps_text))


def stage_links(src_root: Path, staging: Path, rel_paths: list[str]) -> None:
    """Hard-link `rel_paths` (relative to src_root) into `staging`; stale links from an earlier selection are removed first."""
    if staging.exists():
        for p in sorted(staging.rglob("*"), reverse=True):
            if p.name.startswith(".publish_"):                 # keep the sha cache and the commit-rate memory
                continue
            (p.unlink() if p.is_file() or p.is_symlink() else None)
        for d in sorted((p for p in staging.rglob("*") if p.is_dir()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
    for rel in rel_paths:
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.link(src_root / rel, dst)


def sha256_file(fp: Path) -> str:
    h = hashlib.sha256()
    with open(fp, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- preflight ----------------------------------------------------------------------------------------------------------------
def preflight(iso: str, ledger: dict, busy: set[str], lex: dict, comp: dict, allow_stale: bool) -> list[str]:
    """Reasons this language must NOT be published now (empty = fine)."""
    why = []
    e = lex.get("languages", {}).get(iso)
    if not e:
        return ["no lexeme-alignments manifest entry"]
    f = REPO / f"publish/lexeme-alignments/iso={iso}/data.parquet"
    if not f.exists():
        return ["partition file missing"]
    tags = {t.lower() for t in e.get("base_texts", [])}
    if iso in busy or tags & {b.lower() for b in busy}:
        why.append("a chain is running for it right now (its files are being rewritten)")
    try:
        import pyarrow.parquet as pq
        rows = pq.ParquetFile(f).metadata.num_rows
        if rows != e["rows"]:
            why.append(f"partition has {rows} rows but the manifest says {e['rows']}")
    except Exception as ex:                                                  # noqa: BLE001
        why.append(f"partition unreadable ({ex!r})")
    if iso not in ledger:
        why.append("no pipeline_decisions ledger entry")
    eds = comp.get("languages", {}).get(iso, {}).get("editions", {})
    have = {x.get("tag", "").lower() for x in eds.values()} | {k.lower() for k in eds}
    miss = [t for t in e.get("base_texts", []) if t.lower() not in have]
    if miss:
        why.append(f"no compact-alignments for edition(s) {miss}")
    for ed, info in eds.items():
        if not info.get("books"):
            why.append(f"compact edition {ed} has 0 books")
            continue
        d = REPO / "publish/compact-alignments" / iso[0] / iso / ed
        got = {p.name.split("_")[0] for p in d.glob("*.json")} if d.is_dir() else set()
        if set(info["books"]) - got:
            why.append(f"compact edition {ed} is missing book files {sorted(set(info['books']) - got)[:3]}")
    if not allow_stale and f.stat().st_mtime < STALE_BEFORE:
        why.append("stale (unchanged since the 14 Sept publish and about to be rewritten by the fleet refresh)")
    return why


# ---- publishing ---------------------------------------------------------------------------------------------------------------
def hf_manifest(repo: str, dest: Path) -> dict:
    from huggingface_hub import hf_hub_download
    dest.mkdir(parents=True, exist_ok=True)
    return json.loads(Path(hf_hub_download(repo, "manifest.json", repo_type="dataset", local_dir=str(dest),
                                           force_download=True)).read_text(encoding="utf-8"))


def selected_files(name: str, kind: str, isos: list[str], local_root: Path) -> list[str]:
    from lexeme_aligner.export_lex import _COMPANION_RESOURCES
    if kind == "partition":
        return [f"iso={i}/data.parquet" for i in isos if (local_root / f"iso={i}/data.parquet").exists()] + \
               [f for f in ["README.md", *_COMPANION_RESOURCES] if (local_root / f).exists()]
    files = [str(p.relative_to(local_root)) for i in isos for p in (local_root / i[0] / i).rglob("*.json")]
    files += [str(p.relative_to(local_root)) for p in (local_root / "_index").glob("*.json")]
    files += [f for f in ("README.md", "tokenize.js", "tokenizer_sensitive_languages.json", "pipeline_decisions.json")
              if (local_root / f).exists()]
    return files


def paths_info_batched(api, repo: str, paths: list[str], batch: int = 100, retries: int = 8, sleep=time.sleep) -> dict:
    """{path: info} for every path that exists on HF. One API call per `batch` paths (2026-10-02: the per-language loop made 1,068
    calls and hit HF's 1,000-requests-per-5-minutes limit AFTER a successful push). A 429 waits out the server's Retry-After and retries."""
    from huggingface_hub.errors import HfHubHTTPError
    found: dict = {}
    for k in range(0, len(paths), batch):
        chunk = paths[k:k + batch]
        for attempt in range(retries):
            try:
                for info in api.get_paths_info(repo, chunk, repo_type="dataset"):
                    found[info.path] = info
                break
            except HfHubHTTPError as e:
                resp = getattr(e, "response", None)
                if getattr(resp, "status_code", None) == 429 and attempt < retries - 1:
                    try:
                        wait = float(resp.headers.get("Retry-After", 30))
                    except (TypeError, ValueError):
                        wait = 30.0
                    print(f"[publish_safe] HF rate limit — waiting {wait + 5:.0f}s ({attempt + 1}/{retries - 1})", file=sys.stderr)
                    sleep(wait + 5)
                    continue
                raise
    return found


def publish_dataset(name: str, isos: list[str], push: bool, chunk: int, scratch: Path) -> dict:
    from lexeme_aligner.hf_bulk_publish import publish_chunked
    repo, kind = DATASETS[name]
    local_root = REPO / "publish" / name
    local = json.loads((local_root / "manifest.json").read_text(encoding="utf-8"))
    isos = [i for i in isos if i in local.get("languages", {})]
    if not isos:
        print(f"[publish_safe] {name}: none of the selected languages has a local entry — nothing to do", file=sys.stderr)
        return {"dataset": name, "pushed": []}
    hf = hf_manifest(repo, scratch / name)
    conflict = schema_conflict(hf, local, kind)
    if conflict:
        raise SystemExit(f"[publish_safe] {name}: REFUSING a partial publish — {conflict}. A schema change must go out for ALL languages "
                         f"at once (see the aligned_mwe note).")
    merged = merge_manifest(hf, local, isos)
    merged.update(merged_top_level(hf, local, kind))
    stage = STAGING / name
    rel = selected_files(name, kind, isos, local_root)
    stage_links(local_root, stage, rel)
    (stage / "manifest.json").write_text(json.dumps(merged, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    files = sorted(set(rel) | {"manifest.json"})
    new = [i for i in isos if i not in hf.get("languages", {})]
    print(f"[publish_safe] {name} -> {repo}: {len(isos)} language(s) ({len(new)} new to HF), {len(files)} file(s); the HF manifest keeps "
          f"{len(hf['languages']) - len(isos) + len(new)} other entries untouched", file=sys.stderr)
    publish_chunked(stage, repo, files, create=False, dry_run=not push, chunk_size=chunk, label=name, detect_deletions=False)
    result = {"dataset": name, "pushed": isos if push else [], "files": len(files)}
    if push:
        after = hf_manifest(repo, scratch / f"{name}-after")
        bad = [i for i in isos if after["languages"].get(i) != local["languages"][i]]
        moved = [i for i in hf["languages"] if i not in isos and after["languages"].get(i) != hf["languages"][i]]
        if kind == "partition":
            from huggingface_hub import HfApi
            infos = paths_info_batched(HfApi(), repo, [f"iso={i}/data.parquet" for i in isos])
            for i in isos:
                info = infos.get(f"iso={i}/data.parquet")
                if info is None:
                    bad.append(f"{i} (partition missing on HF)")
                    continue
                sha = getattr(getattr(info, "lfs", None), "sha256", None)
                if sha and sha != sha256_file(local_root / f"iso={i}/data.parquet"):
                    bad.append(f"{i} (partition sha differs on HF)")
        result.update(verified=not bad and not moved, entry_mismatch=bad, other_entries_changed=moved)
        print(f"[publish_safe] {name}: VERIFY {'OK' if not bad and not moved else 'FAILED'} — mismatching {bad[:5]}, other entries changed {moved[:5]}",
              file=sys.stderr)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", default="", help="comma-separated languages")
    ap.add_argument("--ready-file", type=Path, help="JSON list of languages (e.g. pipeline/work/logs/publish_ready_latest.json)")
    ap.add_argument("--datasets", default=",".join(DEFAULT))
    ap.add_argument("--include-mwe", action="store_true", help="also publish aligned_mwe (schema guard still applies)")
    ap.add_argument("--allow-stale", action="store_true", help="also publish languages unchanged since 14 Sept")
    ap.add_argument("--push", action="store_true", help="actually publish (default is a dry run)")
    ap.add_argument("--chunk-size", type=int, default=int(os.environ.get("ALIGNER_HF_CHUNK_SIZE", "200")))
    args = ap.parse_args()
    isos = [i.strip() for i in args.iso.split(",") if i.strip()]
    if args.ready_file:
        isos += json.loads(args.ready_file.read_text())
    isos = sorted(set(isos))
    names = [n.strip() for n in args.datasets.split(",") if n.strip()]
    if args.include_mwe and "aligned_mwe" not in names:
        names.append("aligned_mwe")
    unknown = [n for n in names if n not in DATASETS]
    if unknown or not isos:
        ap.error(f"unknown dataset(s) {unknown}" if unknown else "no languages selected")

    ledger = json.loads((REPO / "config/pipeline_decisions.json").read_text())
    busy = running_isos(subprocess.run(["ps", "-eo", "cmd"], capture_output=True, text=True).stdout)
    lex = json.loads((REPO / "publish/lexeme-alignments/manifest.json").read_text())
    comp = json.loads((REPO / "publish/compact-alignments/manifest.json").read_text())
    ok, skipped = [], {}
    for iso in isos:
        why = preflight(iso, ledger, busy, lex, comp, args.allow_stale)
        (skipped.__setitem__(iso, why) if why else ok.append(iso))
    print(f"[publish_safe] {len(isos)} requested -> {len(ok)} pass preflight, {len(skipped)} left out", file=sys.stderr)
    for iso, why in list(skipped.items())[:15]:
        print(f"   left out {iso}: {'; '.join(why)}", file=sys.stderr)
    if len(skipped) > 15:
        print(f"   ... and {len(skipped) - 15} more (see the log file)", file=sys.stderr)
    if not ok:
        return 1
    scratch = REPO / "pipeline/work/publish-scratch"
    results = [publish_dataset(n, ok, args.push, args.chunk_size, scratch) for n in names]
    log = REPO / f"pipeline/work/logs/publish_safe_{time.strftime('%Y%m%d_%H%M%S')}.json"
    log.write_text(json.dumps({"pushed": args.push, "languages": ok, "left_out": skipped, "results": results}, indent=1), encoding="utf-8")
    print(f"[publish_safe] {'PUSHED' if args.push else 'dry run only — nothing pushed (add --push)'}; log {log.relative_to(REPO)}", file=sys.stderr)
    return 0 if all(r.get("verified", True) for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
