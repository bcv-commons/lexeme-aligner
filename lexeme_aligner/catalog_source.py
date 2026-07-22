"""Cross-source language/edition discovery — cdn.bibel.wiki's catalog-index.json +
catalog-overlap.json, covering PKF (`p`), helloAO (`h`), and DBT (`d`) in one place.

WHAT THIS IS: catalog-index.json is a flat [iso, testament, source, edition_count] list across all
three sources (1,876 distinct languages). catalog-overlap.json (schema updated 2026-07-22 — see below)
groups editions that are the SAME underlying text into one entry (`ids`, e.g. `["dbt:SPNR02",
"helloao:spa_r09"]` — Reina Valera 1909, hosted by two providers), each derived from probe-verse
fingerprinting (REV15/PSA117/PSA51 — the same spirit as our own versification.py structural
fingerprinting) — the same-text grouping avoids double-counting a duplicate-hosted edition as two
"versions" when pooling.

SCHEMA CHANGE (2026-07-22): the catalog's own `defaults` top-level field is GONE; each grouped entry
now optionally carries its OWN `default` (which id to prefer within that group) plus, for singleton
entries, a `likely` classification (`distinct_translation` / `dialect_variant` /
`orthography_convention`) + `closest` + `score` — a genuine qualitative signal ("is this a truly
independent translation, or just a minor variant of one we already have") that the old binary
"identical"/"distinct" comparison didn't give. `resolve()`/`all_versions()` below were rewritten for
this shape; no top-level `defaults` lookup exists anymore.

WHAT THIS UNLOCKS: for the ~1,864 catalog languages absent from our own gold_langs/aligned set,
`resolve()` gives a ready-to-use single-edition ingest plan; `all_versions()` gives every distinct
edition found (source + exact adapter parameter each needs), for the "pool everything available"
default this project uses unless `data/language_editions.json` restricts a specific language. All
three sources are fetchable: `pkf` -> `cdn_source.py --iso`, `helloao` -> `helloao_source.py
--translation`, `dbt` -> `dbt_source.py --bible-id` (needs `BIBLE_API_KEY`, see .env; this CDN itself
only exposes DBT *discovery* metadata, not fetchable text — dbt_source.py hits Faith Comes By
Hearing's own DBP v4 API directly, verified live 2026-07-22).

No git-commit anchor exists for this data (server-generated, no `generated_at`) — pinned by content
sha256 instead (same discipline cdn_source.py already uses for its own PKF payload verification).

    python3 -m lexeme_aligner.catalog_source --fetch                    # pin the catalog locally
    python3 -m lexeme_aligner.catalog_source --resolve swa --testament nt
    python3 -m lexeme_aligner.catalog_source --all-versions spa --testament nt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

BASE = "https://cdn.bibel.wiki/dbt/_app"
_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"
_DIR = Path("data/dbt_catalog")
_FILES = {"index": "catalog-index.json", "overlap": "catalog-overlap.json"}
PRIORITY = ("pkf", "helloao", "dbt")
_SOURCE_LETTER = {"p": "pkf", "h": "helloao", "d": "dbt"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310 — fixed https CDN origin
        return r.read()


def fetch(dir_: Path = _DIR) -> dict:
    """Download + content-hash-pin both catalog files. Idempotent-ish (always re-fetches — this is a
    live service index, not a versioned release; the pin records what we got, for provenance/drift
    detection, not to skip a re-download the way commit-pinned fetches do)."""
    dir_.mkdir(parents=True, exist_ok=True)
    pin = {"provider": "cdn.bibel.wiki/dbt", "files": {}}
    for key, fname in _FILES.items():
        data = _get(f"{BASE}/{fname}")
        (dir_ / fname).write_bytes(data)
        pin["files"][fname] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        print(f"[catalog_source] {fname}: {len(data)} bytes, sha256={pin['files'][fname]['sha256'][:12]}…",
              file=sys.stderr)
    (dir_ / "pin.json").write_text(json.dumps(pin, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return pin


def load(dir_: Path = _DIR) -> tuple[dict, dict]:
    idx_fp, ov_fp = dir_ / _FILES["index"], dir_ / _FILES["overlap"]
    if not idx_fp.exists() or not ov_fp.exists():
        fetch(dir_)
    return (json.loads(idx_fp.read_text(encoding="utf-8")),
            json.loads(ov_fp.read_text(encoding="utf-8")))


def _split_id(ref: str) -> tuple[str, str]:
    """"helloao:spa_r09" -> ("helloao", "spa_r09")."""
    source, _, edition_code = ref.partition(":")
    return source, edition_code


def _param_for(source: str, iso: str, edition_code: str) -> str | None:
    if source == "pkf":
        return iso
    if source in ("helloao", "dbt"):
        return edition_code   # helloao: translation id · dbt: bible_id (dbt_source.py --bible-id)
    return None


def _edition(ref: str, iso: str, **extra) -> dict:
    source, edition_code = _split_id(ref)
    d = {
        "source": source, "fetchable": True,
        "param": _param_for(source, iso, edition_code), "edition_code": edition_code,
        "note": (None if source != "dbt" else
                 "fetch via dbt_source.py --bible-id <edition_code> — needs BIBLE_API_KEY (see .env)."),
    }
    d.update(extra)
    return d


def all_versions(iso: str, testament: str = "nt", dir_: Path = _DIR) -> list[dict]:
    """iso + testament -> every distinct edition the catalog knows about, one dict per edition (a
    grouped entry's non-default ids are expanded too — same text, different host — each flagged
    `same_text_as` the group's chosen id so a caller can skip them when pooling to avoid double-
    counting). Each dict: {source, fetchable, param, edition_code, group_default, likely, closest,
    score, same_text_as}. This is what "pool everything available" (the project default absent a
    `data/language_editions.json` restriction) should iterate over."""
    _, overlap = load(dir_)
    matches = [e for e in overlap["entries"] if e[0] == iso and e[1] == testament]
    out: list[dict] = []
    for _, _, info in matches:
        ids = info.get("ids", [])
        default_id = info.get("default", ids[0] if ids else None)
        # singleton entries (len(ids) == 1) carry likely/closest/score classifying THAT edition
        # relative to the nearest other entry, instead of a `default` pick within a group
        classification = ({"likely": info["likely"], "closest": info.get("closest"),
                            "score": info.get("score")} if "likely" in info else {})
        for ref in ids:
            out.append(_edition(
                ref, iso, group_default=(ref == default_id),
                same_text_as=(None if ref == default_id else default_id),
                **classification,
            ))
    return out


def resolve(iso: str, testament: str = "nt", dir_: Path = _DIR) -> dict | None:
    """iso + testament -> a single ingest plan for the ONE most-canonical edition (source, fetchable,
    param, edition_code). Preference: an edition recognized by multiple providers as the SAME text
    (i.e. its group's `default`) is treated as the most likely mainstream/canonical translation —
    among those, PRIORITY (pkf > helloao > dbt) picks the first candidate. Falls back to PRIORITY over
    every id in the catalog for this iso+testament if no group has a `default`. For the full set of
    editions (needed to pool everything, this project's default behavior), use `all_versions()`."""
    versions = all_versions(iso, testament, dir_)
    if not versions:
        return None

    def _priority_pick(cands: list[dict]) -> dict:
        for src in PRIORITY:
            for v in cands:
                if v["source"] == src:
                    return v
        return cands[0]

    # a version is "recognized-canonical" if it's a group's default AND some other version in that
    # same group points `same_text_as` at it (i.e. multiple providers host the same text)
    referenced = {v["same_text_as"] for v in versions if v.get("same_text_as")}
    canonical = [v for v in versions if v.get("group_default") and v["edition_code"] in referenced]

    chosen = _priority_pick(canonical) if canonical else _priority_pick(versions)
    plan = dict(chosen)
    plan["iso"], plan["testament"] = iso, testament
    plan.pop("group_default", None)
    plan.pop("same_text_as", None)
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="download + pin the catalog")
    ap.add_argument("--resolve", metavar="ISO", default=None)
    ap.add_argument("--all-versions", metavar="ISO", default=None,
                     help="list every distinct edition found for iso+testament")
    ap.add_argument("--testament", choices=["nt", "ot"], default="nt")
    ap.add_argument("--dir", type=Path, default=_DIR)
    args = ap.parse_args()

    if args.fetch:
        fetch(args.dir)
    if args.resolve:
        plan = resolve(args.resolve, args.testament, args.dir)
        if plan is None:
            print(f"[catalog_source] no {args.testament} entry for '{args.resolve}' in the catalog",
                  file=sys.stderr)
            return 1
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    if args.all_versions:
        versions = all_versions(args.all_versions, args.testament, args.dir)
        if not versions:
            print(f"[catalog_source] no {args.testament} entry for '{args.all_versions}' in the catalog",
                  file=sys.stderr)
            return 1
        print(json.dumps(versions, indent=2, ensure_ascii=False))
    if not args.fetch and not args.resolve and not args.all_versions:
        ap.error("need --fetch and/or --resolve and/or --all-versions")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
