"""Publish the #2 unsupervised morphology models as a STANDALONE resource — independent of any alignment.

Each `data/morph/<iso>.json` (from target_morph._learn) is a per-language unsupervised morphological
segmentation model: productive suffixes + prefixes + the stem lexicon, learned MDL-free from that
language's own Bible text — no labels, no download, no alignment. It has value on its own (a reusable
morphology resource for any downstream NLP), so it ships as its own CC0 HF dataset, not buried in the
aligner. Mirrors export_lex: writes a deterministic committed `manifest.json` (per-lang stats +
content_sha256) + a card, and optionally pushes the models to a HF dataset.

    # build all cached models + manifest, then push (credentialed):
    python3 -m lexeme_aligner.export_morph --publish bcv-commons/target-morphology --create
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_MORPH_DIR = Path("data/morph")

_CARD = """---
license: cc0-1.0
task_categories:
- token-classification
tags:
- morphology
- unsupervised
- multilingual
- bible
---

# target-morphology

Per-language **unsupervised morphology models** — productive suffixes, prefixes, and a stem lexicon,
each learned MDL-free ("Linguistica"-style: a suffix is productive if it attaches to many paradigm stems)
from that language's own Bible text. No labels, no pretrained model, no download — so it runs on any
language with a translation, including those with zero LLM/encoder coverage.

`stem(word)` strips one productive affix when the remainder is a known stem; inflected variants collapse to
a shared stem (e.g. Hindi बोला/बोलता → बोल). Built for the lexeme-aligner (it fills gloss's normalizer and
optionally stems eflomal's input), but published standalone because unsupervised segmentation is reusable.

**CC0-1.0** — models are derived statistics (affix inventories + stem lists), no source text redistributed.
See `manifest.json` for per-language stats + content hashes.
"""


def _entry(model: dict) -> dict:
    payload = json.dumps({k: model[k] for k in ("suffixes", "prefixes")}, sort_keys=True, ensure_ascii=False)
    return {
        "n_types": model.get("n_types"),
        "n_suffixes": len(model.get("suffixes", [])),
        "n_prefixes": len(model.get("prefixes", [])),
        "n_stems": len(model.get("stems", [])),
        "content_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def build_manifest(morph_dir: Path = _MORPH_DIR) -> dict:
    langs = {}
    for fp in sorted(morph_dir.glob("*.json")):
        if fp.name == "manifest.json":
            continue
        langs[fp.stem] = _entry(json.loads(fp.read_text(encoding="utf-8")))
    return {"schema": {"suffixes": "productive suffixes (longest-first)",
                       "prefixes": "productive prefixes", "stems": "stem lexicon"},
            "languages": langs}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--morph-dir", type=Path, default=_MORPH_DIR)
    ap.add_argument("--publish", metavar="REPO_ID", default=None, help="HF dataset repo to push to")
    ap.add_argument("--create", action="store_true", help="create the HF dataset repo if missing")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    manifest = build_manifest(args.morph_dir)
    (args.morph_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.morph_dir / "README.md").write_text(_CARD, encoding="utf-8")
    print(f"[export_morph] {len(manifest['languages'])} language models → {args.morph_dir}/manifest.json\n"
          f"  {', '.join(f'{k}({v['n_suffixes']}suf/{v['n_prefixes']}pre)' for k, v in manifest['languages'].items())}",
          file=sys.stderr)

    if args.publish:
        files = ["manifest.json", "README.md"] + [fp.name for fp in sorted(args.morph_dir.glob("*.json"))
                                                  if fp.name != "manifest.json"]
        if args.dry_run:
            print(f"[export_morph] dry-run → would push {len(files)} files to {args.publish}", file=sys.stderr)
            return 0
        try:
            from huggingface_hub import HfApi
        except ImportError as e:
            raise SystemExit(f"[export_morph] needs huggingface_hub — pip install -e '.[publish]' ({e})")
        api = HfApi()
        if args.create:
            api.create_repo(args.publish, repo_type="dataset", exist_ok=True)
        for f in files:
            api.upload_file(path_or_fileobj=str(args.morph_dir / f), path_in_repo=f,
                            repo_id=args.publish, repo_type="dataset",
                            commit_message=f"target-morphology: {len(manifest['languages'])} language models")
        print(f"[export_morph] pushed {len(files)} files to {args.publish}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
