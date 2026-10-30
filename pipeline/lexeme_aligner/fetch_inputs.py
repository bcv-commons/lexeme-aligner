"""`lexeme-aligner fetch-inputs` (2026-10-29): download the pinned data the chain needs into ONE data folder, sha256-checked.

The list is `config/align_inputs.json` (name, URL, sha256, size, licence per file; the future package carries it). Everything lands in
the data folder (`--data`, else `$ALIGNER_DATA`, else `$XDG_DATA_HOME/lexeme-aligner`, i.e. ~/.local/share/lexeme-aligner), never in
the checkout:

    DATA/lexeme-spine-macula.db                    the source-language spine (MACULA Hebrew + Greek)
    DATA/prior_pack.parquet                        language-independent priors
    DATA/gram_struct/<partition>/<iso>.json        grammar facts for each language asked for (--iso; absent = no facts, not an error)
    DATA/gram_struct/article_bound.json            one file for all languages
    DATA/greek-nt-clause-layout/clauses.parquet    only with --jev: NT clause units for LLM (Jev) checking
    DATA/hebrew-clause-layout/clauses-<BOOK>.parquet   --jev: OT clause units
    DATA/language-analysis/source/<grc|hbo|arc>/jev.md   --jev: source-language rules (bsb-add-books)
    DATA/language-analysis/<iso>/jev.md            --jev: the language's target rules, if bsb-add-books published a module for it
    DATA/language-analysis/<iso>/split/words.parquet   --split: the language's published word split (bcv-split), for align-text --split
    DATA/inputs.json                               what was fetched: source, sha256, licence, when

A file already present with the right sha256 is not fetched again. A pinned file whose download has another sha256 is refused (the
upstream file changed: the pin in align_inputs.json must be moved on purpose, never silently), except where the entry also pins its
CONTENT (`content`: the tables and columns the chain reads, `content_sha256`): a SQLite file whose content sha matches is accepted and
recorded as such (an upstream build that only added columns or tables gives the same alignments).

    lexeme-aligner fetch-inputs --iso kvg [--iso zin] [--jev] [--data DIR]
    lexeme-aligner align-text --data DIR ...         (or set ALIGNER_DATA once)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INPUTS = REPO / "config" / "align_inputs.json"
RECORD = "inputs.json"
UA = {"User-Agent": "lexeme-aligner fetch-inputs"}


def default_data() -> Path:
    if os.environ.get("ALIGNER_DATA"):
        return Path(os.environ["ALIGNER_DATA"]).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "lexeme-aligner"


def load_list(path: Path = INPUTS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(fp: Path) -> str:
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def hf_url(repo: str, revision: str, path: str) -> str:
    return f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{path}"


def wanted(lst: dict, isos: list[str], jev: bool) -> list[dict]:
    """[{name, url, dest, sha256?, bytes?, licence, optional}] — pure, the tests call it."""
    out = []
    for f in lst["files"]:
        if f.get("group") == "jev" and not jev:
            continue
        url = f["url"] if "url" in f else hf_url(f["repo"], f["revision"], f["path"])
        out.append({"name": f["name"], "url": url, "dest": f["dest"], "sha256": f.get("sha256"), "bytes": f.get("bytes"),
                    "licence": f["licence"], "optional": False, "content": f.get("content"),
                    "compression": f.get("compression"), "archive_sha256": f.get("archive_sha256")})
    g = lst["gram_struct"]
    for iso in isos:
        for part in g["partitions"]:
            out.append({"name": f"gram_struct/{part}/{iso}", "url": hf_url(g["repo"], g["revision"], f"{part}/{iso}.json"),
                        "dest": f"gram_struct/{part}/{iso}.json", "sha256": None, "bytes": None,
                        "licence": g["licences"].get(part, g["licences"]["default"]), "optional": True})
    return out


def content_sha256(db: Path, content: dict) -> str | None:
    """sha256 over the named tables and columns of a SQLite file: per table its name, then every row of those columns in the given
    order as compact JSON, one per line. None when a table or column is missing (or the file is no SQLite database). Rule `tables-v1`."""
    h = hashlib.sha256()
    try:
        con = sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?mode=ro", uri=True)
        try:
            for t in content["tables"]:
                have = {r[1] for r in con.execute(f'pragma table_info("{t["name"]}")')}
                if not have or not set(t["columns"]) <= have:
                    return None
                cols = ",".join(f'"{c}"' for c in t["columns"])
                order = ",".join(f'"{c}"' for c in t["order"])
                h.update(f'#table {t["name"]}\n'.encode())
                for row in con.execute(f'select {cols} from "{t["name"]}" order by {order}'):
                    h.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return None
    return h.hexdigest()


def acceptable(fp: Path, w: dict) -> tuple[bool, str | None]:
    """(ok, how): `sha256` when the file is the pinned one, `content` when only its pinned content matches."""
    if not w["sha256"]:
        return True, None
    if sha256(fp) == w["sha256"]:
        return True, "sha256"
    if w.get("content") and content_sha256(fp, w["content"]) == w["content"]["sha256"]:
        return True, "content"
    return False, None


def _zstd_reader(f):
    """A decompressing reader over a zstd stream: Python 3.14's compression.zstd, else the `zstandard` package."""
    try:
        from compression import zstd
        return zstd.ZstdFile(f)
    except ImportError:
        import zstandard
        return zstandard.ZstdDecompressor().stream_reader(f)


def _decompress(src: Path, dest: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(src, "rb") as fi, _zstd_reader(fi) as r, open(dest, "wb") as fo:
        for block in iter(lambda: r.read(1 << 20), b""):
            h.update(block)
            fo.write(block)
            n += len(block)
    return h.hexdigest(), n


def download(url: str, dest: Path, want_sha: str | None, w: dict | None = None) -> tuple[str, int, str | None] | None:
    """Stream into DEST.part, check sha256, rename. None when the server has no such file (404: an optional file). A `compression: zstd`
    entry is downloaded whole, checked against `archive_sha256`, then decompressed; `sha256` is that of the decompressed file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h, n = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, open(tmp, "wb") as f:
            for block in iter(lambda: r.read(1 << 20), b""):
                h.update(block)
                f.write(block)
                n += len(block)
    except urllib.error.HTTPError as e:
        tmp.unlink(missing_ok=True)
        if e.code == 404:
            return None
        raise
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    got, how = h.hexdigest(), ("sha256" if want_sha else None)
    if w and w.get("compression") == "zstd":
        arc = dest.with_name(dest.name + ".zst.part")
        os.replace(tmp, arc)
        try:
            if w.get("archive_sha256") and got != w["archive_sha256"]:
                raise SystemExit(f"fetch-inputs: {url} has sha256 {got}, the pin says {w['archive_sha256']}: not used")
            got, n = _decompress(arc, tmp)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            arc.unlink(missing_ok=True)
    if want_sha and got != want_sha:
        if w and w.get("content") and content_sha256(tmp, w["content"]) == w["content"]["sha256"]:
            how = "content"
        else:
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"fetch-inputs: {url} has sha256 {got}, the pin says {want_sha}"
                             + (" and its content differs too" if w and w.get("content") else "")
                             + ": the upstream file changed. Not used.")
    os.replace(tmp, dest)
    return got, n, how


def target_module_entries(manifest: dict, tm: dict, isos: list[str]) -> tuple[list[dict], dict]:
    """The latest target module (jev.md) of each language in a language-analysis manifest: (wanted entries, {iso: version or None}).
    Pure, the tests call it."""
    out, versions = [], {}
    for iso in isos:
        lang = manifest.get("languages", {}).get(iso)
        ver = lang and lang.get("latest")
        files = (lang or {}).get("versions", {}).get(ver, {}).get("files", {}) if ver else {}
        if not files.get("jev.md"):
            versions[iso] = None
            continue
        versions[iso] = ver
        out.append({"name": f"target_module_{iso}", "url": hf_url(tm["repo"], tm["revision"], f"{iso}/{ver}/jev.md"),
                    "dest": f"language-analysis/{iso}/jev.md", "sha256": files["jev.md"], "bytes": None, "licence": tm["licence"],
                    "optional": False, "content": None})
    return out, versions


def split_entries(manifest: dict, tm: dict, isos: list[str]) -> tuple[list[dict], dict]:
    """The published word split (split/words.parquet + split/_split.json) of each language's latest version that has one: (wanted entries,
    {iso: pin or None}). The pin is what a compact manifest's `parts` entry names. Pure, the tests call it."""
    out, pins = [], {}
    for iso in isos:
        vers = manifest.get("languages", {}).get(iso, {}).get("versions", {})
        order = [manifest["languages"][iso].get("latest")] + sorted(vers, key=lambda v: vers[v].get("date", ""), reverse=True) if vers else []
        ver = next((v for v in order if v and vers.get(v, {}).get("files", {}).get("split/words.parquet")), None)
        if not ver:
            pins[iso] = None
            continue
        files, lic = vers[ver]["files"], vers[ver].get("licence", {})
        pins[iso] = {"dataset": tm["repo"], "revision": tm["revision"], "file": f"{iso}/{ver}/split/words.parquet",
                     "sha256": files["split/words.parquet"], "format": "bcv-split", "licence": lic.get("split/", tm["licence"])}
        for f in ("split/words.parquet", "split/_split.json"):
            if f in files:
                out.append({"name": f"split_{iso}", "url": hf_url(tm["repo"], tm["revision"], f"{iso}/{ver}/{f}"),
                            "dest": f"language-analysis/{iso}/{f}", "sha256": files[f], "bytes": None,
                            "licence": pins[iso]["licence"], "optional": False, "content": None})
    return out, pins


def _get(w: dict, data: Path, record: dict, log) -> None:
    dest = data / w["dest"]
    prev = record["files"].get(w["dest"])
    if dest.exists() and w["sha256"]:
        ok, how = acceptable(dest, w)
        if ok:
            print(f"[fetch-inputs] ok      {w['dest']}" + (" (same content as the pin)" if how == "content" else ""), file=log)
            return
    if w["optional"] and prev and prev.get("url") == w["url"] and (prev.get("absent") or dest.exists()):
        print(f"[fetch-inputs] ok      {w['dest']}" + (" (none upstream)" if prev.get("absent") else ""), file=log)
        return
    print(f"[fetch-inputs] fetch   {w['dest']}  <- {w['url']}", file=log, flush=True)
    got = download(w["url"], dest, w["sha256"], w)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if got is None:
        if not w["optional"]:
            raise SystemExit(f"fetch-inputs: {w['url']} not found")
        record["files"][w["dest"]] = {"url": w["url"], "absent": True, "fetched": now}
        return
    record["files"][w["dest"]] = {"url": w["url"], "sha256": got[0], "bytes": got[1], "licence": w["licence"], "fetched": now,
                                  **({"accepted_by": got[2]} if got[2] else {})}


def fetch(data: Path, isos: list[str], jev: bool, lst: dict | None = None, log=sys.stderr, split: bool = False) -> dict:
    lst = lst or load_list()
    data.mkdir(parents=True, exist_ok=True)
    rec_fp = data / RECORD
    record = json.loads(rec_fp.read_text(encoding="utf-8")) if rec_fp.exists() else {"files": {}, "isos": []}
    for w in wanted(lst, isos, jev):
        _get(w, data, record, log)
    if (jev or split) and lst.get("target_modules") and isos:
        tm = lst["target_modules"]
        _get({"name": "language_analysis_manifest", "url": hf_url(tm["repo"], tm["revision"], "manifest.json"),
              "dest": "language-analysis/manifest.json", "sha256": None, "bytes": None, "licence": tm["licence"], "optional": False,
              "content": None}, data, record, log)
        manifest = json.loads((data / "language-analysis/manifest.json").read_text(encoding="utf-8"))
    if split and lst.get("target_modules") and isos:
        entries, pins = split_entries(manifest, tm, isos)
        for w in entries:
            _get(w, data, record, log)
        record.setdefault("splits", {}).update(pins)
        for iso, pin in pins.items():
            print(f"[fetch-inputs] split {iso}: " + (pin["file"] if pin else "none published"), file=log)
    if jev and lst.get("target_modules") and isos:
        entries, versions = target_module_entries(manifest, tm, isos)
        for w in entries:
            _get(w, data, record, log)
        record.setdefault("target_modules", {}).update(versions)
        for iso, ver in versions.items():
            print(f"[fetch-inputs] target module {iso}: " + (f"{tm['repo']} {iso}/{ver}" if ver else "none (generic conventions)"), file=log)
    record["isos"] = sorted(set(record.get("isos", [])) | set(isos))
    if jev:
        record["jev"] = True
    record["list_sha256"] = sha256(INPUTS) if INPUTS.exists() else None
    tmp = rec_fp.with_name(RECORD + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, rec_fp)
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lexeme-aligner fetch-inputs", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=None, help="data folder (default $ALIGNER_DATA, else ~/.local/share/lexeme-aligner)")
    ap.add_argument("--iso", action="append", default=[], help="language(s) whose grammar facts to fetch (ISO 639-3); repeatable")
    ap.add_argument("--jev", action="store_true", help="also what LLM (Jev) checking needs: clause layouts, source modules, the "
                    "language's target module when bsb-add-books has published one")
    ap.add_argument("--split", action="store_true", help="also the language's published word split (language-analysis), for "
                    "align-text --split (alignment by word parts)")
    args = ap.parse_args(argv)
    data = (args.data or default_data()).expanduser().resolve()
    if data == REPO or data.is_relative_to(REPO) and not data.is_relative_to(REPO / "pipeline" / "work"):
        raise SystemExit(f"fetch-inputs: choose a data folder outside the checkout (or under pipeline/work): {data}")
    fetch(data, args.iso, args.jev, split=args.split)
    print(f"[fetch-inputs] done: {data}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
