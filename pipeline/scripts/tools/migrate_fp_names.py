"""Rename the LOCAL compact-alignments and full-align layer files from the legacy content-hash names (<BOOK>_<5 hex>) to text fingerprint v1
names (<BOOK>_<8 hex>), and write every compact edition's _fingerprints.txt (plan: internal-docs/compact-text-fingerprint-plan-2026-10-12.md,
step 2). Arrays are not touched: only file names, the full-align manifests' `books` maps and the compact manifest's `text_fp_version` change.

A file is renamed only when its old hash is the legacy hash of the edition's CURRENT ingested text (so the new name is provably that text's
fingerprint). A file whose old hash is not (an older version whose text is gone) is MOVED to pipeline/work/fp-migration/stale/<same path>,
never deleted. Every decision goes to pipeline/work/fp-migration/rename_map.tsv (tree, dir, book, old, new, action): the input for the
server-side rename on Hugging Face (plan step 4).

Dry run unless --apply. Do not run while a chain writes compact files.

    .venv/bin/python pipeline/scripts/tools/migrate_fp_names.py [--apply] [--workers 6] [--iso fra,eng]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pipeline"))

from lexeme_aligner import text_fingerprint as tf  # noqa: E402
from lexeme_aligner.compact_align import book_content_hash  # noqa: E402
from lexeme_aligner.manifest_io import update_json  # noqa: E402

COMPACT = REPO / "publish/compact-alignments"
LAYER_TREES = {"full-alignments-manual": REPO / "publish/full-alignments-manual",
               "full-alignments-manual-sa": REPO / "publish/full-alignments-manual-sa",
               "full-alignments-llm": REPO / "pipeline/work/full-alignments-llm"}
INGEST = REPO / "pipeline/work/ingest-cache"
WORK = REPO / "pipeline/work/fp-migration"
NAME = re.compile(r"^([0-9A-Z]{3})_([0-9a-f]{5}|[0-9a-f]{8})(\.json|\.meta\.json|\.extra\.json)$")


def book_hashes(usj_dir: Path) -> dict[str, dict]:
    """{book: {"legacy": 5 hex, "fp": full book fingerprint, "chapters": {...}}} for one ingested edition."""
    out = {}
    for f in sorted(usj_dir.glob("*-*.json")):
        book = f.stem.split("-", 1)[1]
        fp, chapters = tf.usj_fingerprints(f)
        out[book] = {"legacy": book_content_hash(f)[-5:], "fp": fp, "chapters": chapters}
    return out


def plan_dir(job: tuple[str, str, str]) -> dict:
    """One edition/layer folder -> its actions (pure: reads only)."""
    tree, rel, usj = job
    root = COMPACT if tree == "compact" else LAYER_TREES[tree]
    d = root / rel
    hashes = book_hashes(Path(usj)) if usj else {}
    groups: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for p in d.iterdir():
        m = NAME.match(p.name)
        if m:
            groups[(m[1], m[2])].append(p.name)
    actions = []
    for (book, h), names in sorted(groups.items()):
        main = d / f"{book}_{h}.json"
        built = datetime.fromtimestamp((main if main.exists() else d / names[0]).stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")
        info = hashes.get(book)
        if info is None:
            actions.append({"book": book, "old": h, "new": "", "action": "no-text", "names": names})
        elif h == tf.file_hash(info["fp"]):
            actions.append({"book": book, "old": h, "new": h, "action": "current", "names": names, "built": built})
        elif h == info["legacy"]:
            actions.append({"book": book, "old": h, "new": tf.file_hash(info["fp"]), "action": "rename", "names": names, "built": built})
        elif tree == "compact":
            actions.append({"book": book, "old": h, "new": "", "action": "stale", "names": names})
        else:                                     # a full-align layer built on an older text: rebuild the layer (fullalign_build replaces it)
            actions.append({"book": book, "old": h, "new": "", "action": "stale-layer", "names": names})
    have = {a["book"] for a in actions if a["action"] in ("rename", "current")}
    for a in actions:
        if a["action"] == "stale" and a["book"] not in have:
            a["action"] = "stale-only"            # the book's ONLY file is stale: keep it, the edition needs a compact re-run
    return {"tree": tree, "rel": rel, "usj": usj, "actions": actions,
            "map": {a["book"]: tf.map_line(a["book"], hashes[a["book"]]["fp"], a["built"], hashes[a["book"]]["chapters"])
                    for a in actions if a["action"] in ("rename", "current")} if tree == "compact" else {}}


def jobs(isos: set[str] | None) -> tuple[list[tuple[str, str, str]], list[str]]:
    out, missing = [], []
    comp = json.loads((COMPACT / "manifest.json").read_text(encoding="utf-8"))["languages"]
    tag_of = {(iso, ed): info.get("tag", ed) for iso, e in comp.items() for ed, info in e.get("editions", {}).items()}

    def usj_for(iso: str, ed: str) -> str:
        tag = tag_of.get((iso, ed))
        bare = ed[len(iso) + 1:] if ed.lower().startswith(iso.lower() + "_") else ed      # Door43 layer hin_higlt -> text usj-higlt
        for cand in ([tag] if tag else []) + [ed, ed.lower(), bare, bare.lower()]:
            if (INGEST / f"usj-{cand}").is_dir():
                return str(INGEST / f"usj-{cand}")
        return ""

    for d in sorted(p for p in COMPACT.glob("?/*/*") if p.is_dir()):
        iso, ed = d.parent.name, d.name
        if isos and iso not in isos:
            continue
        usj = usj_for(iso, ed)
        if not usj:
            missing.append(f"compact {iso}/{ed}")
        out.append(("compact", str(d.relative_to(COMPACT)), usj))
    for tree, root in LAYER_TREES.items():
        mf = root / "manifest.json"
        layers = json.loads(mf.read_text(encoding="utf-8"))["languages"] if mf.exists() else {}
        for iso, e in layers.items():
            if isos and iso not in isos:
                continue
            for lid, info in e.get("layers", {}).items():
                d = root / iso[0] / iso / lid
                if not d.is_dir():
                    continue
                usj = usj_for(iso, info.get("edition") or lid.split("+")[0])
                if not usj:
                    missing.append(f"{tree} {iso}/{lid}")
                out.append((tree, str(d.relative_to(root)), usj))
    return out, missing


def apply(plan: dict) -> None:
    tree, rel = plan["tree"], plan["rel"]
    root = COMPACT if tree == "compact" else LAYER_TREES[tree]
    d = root / rel
    for a in plan["actions"]:
        if a["action"] == "rename":
            for n in a["names"]:
                dst = d / n.replace(f"{a['book']}_{a['old']}.", f"{a['book']}_{a['new']}.", 1)
                if dst.exists():
                    raise SystemExit(f"[migrate] {dst} exists already; nothing overwritten, stop")
                os.rename(d / n, dst)
        elif a["action"] == "stale":
            for n in a["names"]:
                dst = WORK / "stale" / tree / rel / n
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(d / n), str(dst))
    if plan["map"]:
        tf.update_map(d, plan["map"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="rename / move (default: dry run, report only)")
    ap.add_argument("--iso", default="", help="comma-separated languages (default: all)")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    isos = {x.strip() for x in a.iso.split(",") if x.strip()} or None
    todo, missing = jobs(isos)
    print(f"[migrate] {len(todo)} folders ({collections.Counter(t for t, _, _ in todo)}); {len(missing)} without ingested text", file=sys.stderr)
    with ProcessPoolExecutor(a.workers) as ex:
        plans = list(ex.map(plan_dir, todo, chunksize=8))
    counts = collections.Counter((p["tree"], x["action"]) for p in plans for x in p["actions"])
    for k, v in sorted(counts.items()):
        print(f"   {k[0]:26} {k[1]:8} {v:7d}", file=sys.stderr)
    rebuild = sorted({(p["tree"], p["rel"]) for p in plans for x in p["actions"] if x["action"] in ("stale-layer", "stale-only")})
    for r in rebuild[:40]:
        print(f"   needs a rebuild (kept as is): {r[0]} {r[1]}", file=sys.stderr)
    for m in missing[:20]:
        print(f"   no ingested text: {m}", file=sys.stderr)
    WORK.mkdir(parents=True, exist_ok=True)
    log = WORK / ("rename_map.tsv" if a.apply else "rename_map.dryrun.tsv")
    with open(log, "w", encoding="utf-8") as f:
        f.write("tree\tdir\tbook\told\tnew\taction\n")
        for p in plans:
            for x in p["actions"]:
                f.write(f"{p['tree']}\t{p['rel']}\t{x['book']}\t{x['old']}\t{x['new']}\t{x['action']}\n")
    print(f"[migrate] decisions -> {log.relative_to(REPO)}", file=sys.stderr)
    if not a.apply:
        print("[migrate] dry run: nothing renamed (add --apply)", file=sys.stderr)
        return 0
    for p in plans:
        apply(p)
    # full-align manifests: the books maps name the files
    renamed = {(p["tree"], p["rel"], x["book"]): x["new"] for p in plans if p["tree"] != "compact"
               for x in p["actions"] if x["action"] in ("rename", "current")}
    for tree, root in LAYER_TREES.items():
        if not (root / "manifest.json").exists():
            continue

        def fix(doc: dict, tree=tree, root=root) -> None:
            for iso, e in doc.get("languages", {}).items():
                for lid, info in e.get("layers", {}).items():
                    rel = str((root / iso[0] / iso / lid).relative_to(root))
                    for book in list(info.get("books", {})):
                        new = renamed.get((tree, rel, book))
                        if new:
                            info["books"][book] = f"{book}_{new}.json"
        update_json(root / "manifest.json", fix)
    update_json(COMPACT / "manifest.json", lambda doc: doc.__setitem__("text_fp_version", tf.TEXT_FP_VERSION))
    print("[migrate] applied", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
