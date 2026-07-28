"""Cross-source language/edition discovery — cdn.bibel.wiki's catalog-index.json +
catalog-overlap.json, covering PKF (`p`), helloAO (`h`), and DBT (`d`) in one place.

WHAT THIS IS: catalog-index.json is a flat [iso, canon, source, count?] list across all three sources
— coarse existence only ("this source has SOMETHING for this language"), no edition ids. catalog-
overlap.json groups editions VERIFIED (real probe-verse text comparison, REV15/PSA117/PSA51 — the same
spirit as our own versification.py structural fingerprinting) as the SAME underlying text into one
cluster, so pooling never double-counts a duplicate-hosted edition as two independent "versions".

SCHEMA REVISION (2026-07-28 — `doc/catalog-overlap.md` in bcv-commons/bibles, full rewrite of the
2026-07-22 shape): `entries` is now a dict keyed `"<iso>:<canon>"` -> list of clusters (was a flat
[iso, testament, info] list). Ids are single-letter-prefixed (`d:`/`h:`/`p:`, not `dbt:`/`helloao:`/
`pkf:`). No more catalog-provided `default` within a cluster — picking one canonical id from a multi-id
cluster is now the CLIENT's job, via `priority` (see PRIORITY below); `all_versions()` still exposes a
`same_text_as`/`group_default` grouping signal (an arbitrary but consistent anchor) so PRIORITY-based
picking downstream (onboard.py's `editions_for()`) has something to cluster on. New `r: false` field
(replaces guessing from ingest failures) is a POSITIVE confirmation an id's text is currently
unreachable — mapped to `fetchable=False` here. New `pkf_ref`: PKF ids are always a *minted* label (PKF
has no natural per-language id of its own); the real fetchable reference is `pkf_ref`, though our own
cdn_source.py never needed this (it resolves PKF editions directly from PKF's own manifest by iso).

KNOWN GAP from this revision: a language with only ONE known candidate anywhere across all 3 sources
gets NO row in catalog-overlap.json at all (previously a bare singleton) — see `all_versions()`'s
docstring for the measured impact (274 of 924 published languages, helloAO/DBT-only single-candidate
cases, are currently unresolvable via this file; PKF-only ones are unaffected).

WHAT THIS UNLOCKS: for the ~1,864 catalog languages absent from our own gold_langs/aligned set,
`resolve()` gives a ready-to-use single-edition ingest plan; `all_versions()` gives every distinct
edition found (source + exact adapter parameter each needs), for the "pool everything available"
default this project uses unless `data/language_editions.json` restricts a specific language. All
three sources are fetchable: `pkf` -> `cdn_source.py --iso`, `helloao` -> `helloao_source.py
--translation`, `dbt` -> `dbt_source.py --bible-id` (needs `BIBLE_API_KEY`, see .env; this CDN itself
only exposes DBT *discovery* metadata, not fetchable text — dbt_source.py hits Faith Comes By
Hearing's own DBP v4 API directly).

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
_DIR = Path("config/dbt_catalog")
_FILES = {"index": "catalog-index.json", "overlap": "catalog-overlap.json"}
PRIORITY = ("pkf", "helloao", "dbt")
_SOURCE_LETTER = {"p": "pkf", "h": "helloao", "d": "dbt"}

# each source's OWN raw catalog, fetched independent of catalog-overlap.json — the fallback path for
# a single-candidate-anywhere language whose one source is helloAO or DBT (overlap.json excludes these
# entirely; see all_versions()'s docstring). Mirrors what cdn_source.py already does for PKF (fetches
# PKF's own manifest.json and resolves by iso directly, no dependency on this module at all).
_RAW_CATALOG = {
    "helloao": ("https://bible.helloao.org/api/available_translations.json", "helloao-translations.json"),
    "dbt": ("https://cdn.bibel.wiki/dbt/_catalog.json", "dbt-catalog.json"),
}


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


def _load_raw(source: str, dir_: Path = _DIR) -> dict:
    """Fetch + cache (no re-fetch if already present — unlike `load()`, these are only consulted as a
    fallback, so staying with whatever was last fetched is fine) one source's OWN full catalog."""
    url, fname = _RAW_CATALOG[source]
    fp = dir_ / fname
    if not fp.exists():
        dir_.mkdir(parents=True, exist_ok=True)
        data = _get(url)
        fp.write_bytes(data)
        print(f"[catalog_source] {fname}: {len(data)} bytes (fallback resolver cache)", file=sys.stderr)
    return json.loads(fp.read_text(encoding="utf-8"))


def _helloao_candidates(iso: str, dir_: Path = _DIR) -> list[str]:
    """Every helloAO translation id whose own `language` field matches iso — resolved directly from
    helloAO's own translation list, independent of catalog-overlap.json. No testament filtering (the
    list doesn't cleanly expose NT/OT book coverage without a per-translation follow-up fetch) — an
    over-inclusive candidate degrades gracefully the same way it already does everywhere else in this
    project (run_pilot.build_corpus skips a missing book with a warning, not a crash)."""
    doc = _load_raw("helloao", dir_)
    return sorted({t["id"] for t in doc.get("translations", []) if t.get("language") == iso})


def _dbt_candidates(iso: str, testament: str, dir_: Path = _DIR) -> list[str]:
    """Every DBT bible_id listed for this iso, matching `testament` or its Portions variant (mirrors
    catalog-index.json's own nt/ntp/ot/otp convention). Returns bare bible_ids only — actual
    fetchability (a live Bible Brain API query) is dbt_source.py's own job at fetch time, same as
    always; this function doesn't try to interpret `_catalog.json`'s own fileset-id shorthand."""
    doc = _load_raw("dbt", dir_)
    canons = {testament, f"{testament}p"}
    return sorted({row[1] for row in doc.get("versions", []) if row[0] == iso and row[2] in canons})


def _split_id(ref: str) -> tuple[str, str]:
    """"h:spa_r09" -> ("helloao", "spa_r09") — single-letter source prefix (d/h/p), per the
    2026-07-28 schema revision (was a full source name with a ':' separator before that)."""
    letter, _, edition_code = ref.partition(":")
    return _SOURCE_LETTER.get(letter, letter), edition_code


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
    """iso + testament -> every distinct edition the catalog knows about, one dict per edition.

    2026-07-28 schema revision (`doc/catalog-overlap.md` in bcv-commons/bibles): `entries` is now a
    dict keyed `"<iso>:<canon>"` -> list of CLUSTERS (was a flat [iso, testament, info] list before).
    A multi-id cluster means every id in it is VERIFIED the same text (real content comparison, not a
    catalog-provided `default` anymore — picking canonical among them is now our job, via PRIORITY;
    see onboard.py's `editions_for()`/`_priority_pick()`). A cluster's first id is used as an arbitrary
    (but consistent) `same_text_as` anchor so that downstream priority-based canonical selection still
    has a grouping signal to work with — WHICH member anchors the group no longer matters, since the
    downstream picker considers every member regardless.

    `r: false` (new field, replaces guessing from ingest failures) means the catalog has POSITIVELY
    confirmed this specific id's text is currently unreachable — `fetchable=False` here so it's
    excluded upfront rather than discovered only at actual ingest time.

    IMPORTANT KNOWN GAP: a language with only ONE known candidate anywhere (across all 3 sources) gets
    NO entry in this file at all as of 2026-07-28 (previously it appeared as a bare singleton) — see
    "What's excluded" in the doc. Measured (2026-07-28, against 924 published languages): 319 resolved
    to zero editions before the two fixes below; both fixes together account for 135 of those:
      - 119 had real catalog coverage listed only under the `ntp`/`otp` (Portions) canon — this
        function only ever queried the bare `nt`/`ot` key, never the Portions variant. Fixed: also
        query `f"{iso}:{testament}p"` and merge its clusters in.
      - 16 were genuinely single-source-anywhere with PKF as that one source — cdn_source.py CAN
        resolve a PKF edition directly from PKF's own manifest by iso alone (no overlap.json needed for
        that), but nothing here actually synthesized a candidate for that case before. Fixed: when
        catalog-index.json shows PKF as the only source and overlap.json has nothing, synthesize one
        (`param=iso`, matching what `resolve()`/`_param_for()` already does for every other PKF entry).
    The remaining ~161 (single-source-anywhere, helloAO or DBT) still need a real by-iso resolver for
    those two sources (mirroring cdn_source.resolve()) — genuinely not recoverable from this file."""
    _, overlap = load(dir_)
    clusters = list(overlap["entries"].get(f"{iso}:{testament}", []))
    clusters += overlap["entries"].get(f"{iso}:{testament}p", [])   # Portions coverage, same testament
    out: list[dict] = []

    def _own_key(ref: str) -> str:
        src, code = _split_id(ref)
        return f"{src}:{code}"

    for cluster in clusters:
        ids = cluster.get("ids", [])
        if not ids:
            continue
        anchor = ids[0]
        anchor_key = _own_key(anchor)
        unreachable = cluster.get("r") is False
        classification = {k: cluster[k] for k in ("likely", "score") if k in cluster}
        if "closest" in cluster:
            # own_key format (source:edition_code), NOT the raw ref — same_text_as/points_at/seen are
            # all keyed that way; comparing a bare single-letter-prefixed ref against them would never
            # match (this was a real bug here: fixed together with the same_text_as anchor below).
            classification["closest"] = _own_key(cluster["closest"])
        for ref in ids:
            out.append(_edition(
                ref, iso, group_default=(ref == anchor),
                same_text_as=(None if ref == anchor else anchor_key),
                fetchable=not unreachable,
                confirmed_removed=cluster.get("confirmed_removed", False),
                pkf_ref=cluster.get("pkf_ref") if ref.startswith("p:") else None,
                **classification,
            ))

    if not out:
        # nothing in catalog-overlap.json at all for this iso+testament — either single-candidate-
        # anywhere (the documented exclusion) or a pair the comparison pipeline simply hasn't reached
        # yet despite 2+ sources existing (a handful of confirmed live cases as of 2026-07-28 — see
        # internal-docs/dbt-overlap-classification-gap.md). Either way, resolve directly from
        # whichever source(s) catalog-index.json actually lists, same as cdn_source.py already does
        # independently for PKF: no `same_text_as` grouping (nothing here has been compared), no
        # dedup — if 2+ sources are both listed with zero comparison data, pool all of them rather
        # than silently returning nothing, same "pool everything available" default as elsewhere.
        index, _ = load(dir_)
        canons = {testament, f"{testament}p"}
        sources = {src for i, canon, src, *_ in index["entries"] if i == iso and canon in canons}
        if "p" in sources:
            out.append(_edition(f"p:{iso.upper()}PKF", iso, group_default=True, same_text_as=None,
                                fetchable=True))
        if "h" in sources:
            for tid in _helloao_candidates(iso, dir_):
                out.append(_edition(f"h:{tid}", iso, group_default=True, same_text_as=None,
                                    fetchable=True))
        if "d" in sources:
            for bid in _dbt_candidates(iso, testament, dir_):
                out.append(_edition(f"d:{bid}", iso, group_default=True, same_text_as=None,
                                    fetchable=True))
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
