"""CDN ingest adapter — fetch target Bible text from `cdn.bibel.wiki` (PKF), by pin.

Recipe layer (docs/bibles-recipe-layer.md): the text stays at its origin; we hold a **pin**
(hashed pkf filename + `sha256` of the consumed bytes) and a **recipe**. On rebuild we re-fetch and
verify the hash — a mismatch means upstream drifted, so we re-pin deliberately.

Two stages:
  1. **discover + fetch + pin** (pure Python, verified live) — resolve iso → manifest collection,
     download the `.pkf` (+ catalog + app-config), verify `pkf_bytes`, hash it, write the pin, and
     refresh the `data/sources.json` licence pointer from the CDN's own `copyright` block.
  2. **decode → USJ** (`--to-usj`, the one Node edge) — PKF→USFM via a Proskomma converter (Node),
     then USFM→USJ via `usfmtc` (Python). Feature-detected: if no converter is available it stops
     after the pin with guidance, so stage 1 is always usable on its own.

    python3 -m lexeme_aligner.cdn_source --iso ind                 # fetch + pin + provenance
    python3 -m lexeme_aligner.cdn_source --iso ind --to-usj data/usj-ind \
            --converter ../bcv-query/example/scripts/export_usfm.mjs
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CDN = "https://cdn.bibel.wiki/pkf"


# the CDN is Cloudflare-fronted and 403s the default python-urllib UA — send a normal one.
_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"


def _get(url: str, retries: int = 5) -> bytes:
    """GET with backoff — retry transient errors (resets, timeouts, 5xx/429) but fail fast on a
    real 4xx like 404. Was a bare one-shot fetch until an MX-region onboarding batch hit several
    RemoteDisconnected resets on this CDN — same retry discipline dbt_source.py/helloao_source.py
    already had, just missing here."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    err: Exception = RuntimeError("no attempt")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310 — fixed https CDN origin
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                raise
            err = e
        except (urllib.error.URLError, OSError) as e:              # incl. RemoteDisconnected, timeouts
            err = e
        if attempt < retries - 1:
            print(f"[cdn] retry {attempt + 1}/{retries - 1} after {err} — {url}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise err


def _get_json(url: str) -> dict:
    return json.loads(_get(url))


def resolve(iso: str, collection: int, manifest: dict | None = None) -> tuple[dict, dict]:
    """Return (manifest, chosen collection dict) for iso — the pin material."""
    manifest = manifest or _get_json(f"{CDN}/manifest.json")
    langs = manifest.get("languages", manifest)          # live shape is iso-keyed
    entry = langs.get(iso)
    if not entry:
        raise SystemExit(f"[cdn] '{iso}' not on the CDN (manifest has {len(langs)} langs)")
    cols = entry.get("collections") or []
    if not cols:
        raise SystemExit(f"[cdn] '{iso}' has no text collection (media-only, codex={entry.get('codex')!r})")
    if collection >= len(cols):
        raise SystemExit(f"[cdn] '{iso}' has {len(cols)} collection(s); --collection {collection} out of range")
    return manifest, cols[collection]


def fetch(iso: str, col: dict, pool: Path) -> tuple[Path, str]:
    """Download the pinned .pkf, verify byte length, return (path, sha256)."""
    data = _get(f"{CDN}/{iso}/{col['pkf']}")
    if len(data) != col.get("pkf_bytes"):
        raise SystemExit(f"[cdn] {iso}: pkf_bytes mismatch — manifest {col.get('pkf_bytes')} vs got {len(data)}")
    dest = pool / iso / col["pkf"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest, hashlib.sha256(data).hexdigest()


def build_pin(iso: str, manifest: dict, col: dict, cfg: dict, sha: str) -> dict:
    """The reproducibility record — hashed filename is the version, plus provenance + licence."""
    coll = cfg.get("collection", {})
    cr = cfg.get("copyright", {})
    langs = manifest.get("languages", manifest)
    return {
        "iso": iso,
        "provider": "cdn.bibel.wiki",
        "language_name": langs.get(iso, {}).get("nm"),   # the manifest's own language display name
        "version_id": coll.get("versionId") or Path(col["pkf"]).name.split(".")[0],
        "pkf": col["pkf"],
        "pkf_bytes": col["pkf_bytes"],
        "sha256": sha,
        "catalog": col.get("catalog"),
        "books": col.get("books"),
        "codex": col.get("codex"),
        "manifest_updated_at": manifest.get("updated_at"),
        "license": cr.get("license"),                    # CDN's own stated licence (for the record)
        "license_url": f"{CDN}/{iso}/app-config.json",   # authoritative, machine-readable pointer
    }


def write_pin(pin: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pin, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def update_sources(pin: dict, path: Path) -> None:
    """Refresh this iso's licence pointer in data/sources.json (feeds export_lex → manifest)."""
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc[pin["iso"]] = {"provider": pin["provider"], "edition": pin["version_id"],
                       "license_url": pin["license_url"]}
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def decode_to_usj(iso: str, converter: Path, usfm_tmp: Path, usj_dir: Path) -> int:
    """PKF → USFM (Node/Proskomma converter) → USJ (usfmtc). The one Node edge, feature-detected.

    The converter reads data/_pool/<iso>/*.pkf and writes per-book USFM into usfm_tmp; we then
    re-number to <NN>-<BOOK>.json from each book's \\id via the aligner's own map, so run_pilot
    always finds them regardless of the converter's file-number scheme."""
    if not converter or not Path(converter).exists():
        print(f"[cdn] decode skipped — converter not found at {converter}. The .pkf is fetched +\n"
              f"      pinned; to decode, provide a Proskomma PKF→USFM script via --converter (the\n"
              f"      vendored pkf2usfm/export_usfm.mjs needs `cd pkf2usfm && npm install` first).",
              file=sys.stderr)
        return 0
    if not (Path(converter).parent / "node_modules").exists():
        print(f"[cdn] decode skipped — {Path(converter).parent}/node_modules missing. "
              f"Run `cd {Path(converter).parent} && npm install`, then re-run with --to-usj.",
              file=sys.stderr)
        return 0
    try:
        import usfmtc
    except ImportError:
        raise SystemExit("[cdn] USFM→USJ needs usfmtc — pip install -e '.[ingest]'")

    usfm_tmp.mkdir(parents=True, exist_ok=True)
    # Node edge: `node export_usfm.mjs <iso> --out <usfm_tmp>` — reads data/_pool/<iso> (cwd-relative).
    subprocess.run(["node", str(converter), iso, "--out", str(usfm_tmp)], check=True)

    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM   # single source of the NN-BOOK numbering
    usj_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for uf in sorted(usfm_tmp.glob("*.usfm")):
        book = _book_id(uf)
        nn = _BOOK_FILE_NUM.get(book)
        if not nn:
            print(f"[cdn] skip {uf.name}: book '{book}' not in NN map", file=sys.stderr)
            continue
        usfmtc.readFile(str(uf)).outUsj(str(usj_dir / f"{nn}-{book}.json"))
        n += 1
    print(f"[cdn] decoded {n} book(s) → {usj_dir}", file=sys.stderr)
    return n


def _book_id(usfm_path: Path) -> str:
    """Book code from the USFM \\id line (robust to the converter's filenames); fall back to stem."""
    for line in usfm_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("\\id "):
            return line[4:].strip().split()[0].upper()
    return usfm_path.stem.upper()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--collection", type=int, default=0, help="index into the iso's collections[]")
    ap.add_argument("--pool", type=Path, default=Path("data/_pool"), help="PKF download cache (git-ignored)")
    ap.add_argument("--pin", type=Path, default=None, help="pin file (default data/pins/<iso>.json)")
    ap.add_argument("--sources", type=Path, default=Path("data/sources.json"))
    ap.add_argument("--to-usj", type=Path, default=None, metavar="DIR",
                    help="also decode PKF→USJ into DIR (needs --converter + usfmtc)")
    ap.add_argument("--converter", type=Path, default=Path("pkf2usfm/export_usfm.mjs"),
                    help="Proskomma PKF→USFM Node script (default: vendored pkf2usfm/export_usfm.mjs)")
    ap.add_argument("--usfm-tmp", type=Path, default=None, help="scratch dir for intermediate USFM")
    args = ap.parse_args()

    manifest, col = resolve(args.iso, args.collection)
    _pkf, sha = fetch(args.iso, col, args.pool)   # cached under --pool; decode reads it by iso
    cfg = _get_json(f"{CDN}/{args.iso}/app-config.json")
    pin = build_pin(args.iso, manifest, col, cfg, sha)

    write_pin(pin, args.pin or Path("data/pins") / f"{args.iso}.json")
    if args.sources:
        update_sources(pin, args.sources)
    print(f"[cdn] {args.iso}: {pin['pkf']} ({pin['pkf_bytes']} B, {pin['books']} books, "
          f"codex={pin['codex']}) · license={pin['license']!r} · sha256={sha[:12]}…", file=sys.stderr)

    if args.to_usj:
        decode_to_usj(args.iso, args.converter,
                      args.usfm_tmp or Path("out") / f"usfm-{args.iso}", args.to_usj)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
