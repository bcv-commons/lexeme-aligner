"""Publish the #3 target function-word lists as a STANDALONE resource — independent of any alignment.

Each `data/stopwords/<iso>.txt` (from target_stopwords.compute_stopwords) is a per-language function-word
list induced from that language's own Bible text: frequency + dispersion, then RESCUED against this
language's own taken-pool alignment + prior-pack keyness so genuine content words that happen to be
frequent ("God", "Lord") are never dropped (see target_stopwords.py docstring for the concentration-share
mechanism that makes the rescue precise). Many of the target languages have NO existing curated stopword
list anywhere — this is a reusable NLP resource beyond the aligner (search, IR, topic modeling all need
one), so it ships as its own CC0 HF dataset. Mirrors export_morph.py.

    python3 -m lexeme_aligner.export_stopwords --publish bcv-commons/target-stopwords --create
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_SW_DIR = Path("data/stopwords")

_CARD = """---
license: cc0-1.0
task_categories:
- text-classification
tags:
- stopwords
- unsupervised
- multilingual
- bible
---

# target-stopwords

Per-language **function-word lists**, induced from that language's own Bible text — frequency +
dispersion (the classic corpus-linguistics stopword-induction recipe), then RESCUED against the
language's own alignment output + a source-anchored content signal so genuinely frequent CONTENT words
("God", "Lord") are never dropped. See the lexeme-aligner's `target_stopwords.py` for the mechanism: a
candidate is rescued if it concentrates most of its aligned mass on one prior-pack content lexeme (≥40%
share) — a true function word instead scatters thinly across dozens of distinct lexemes.

Many of the covered languages have **no existing curated stopword list anywhere** — this is a reusable
resource for search, IR, topic modeling, or any NLP task needing one in these languages.

**CC0-1.0** — derived word-frequency statistics, no source text redistributed. See `manifest.json` for
per-language stats + content hashes.
"""


def _entry(words: list[str]) -> dict:
    payload = "\n".join(sorted(words))
    return {"n_words": len(words), "content_sha256": hashlib.sha256(payload.encode()).hexdigest()}


def build_manifest(sw_dir: Path = _SW_DIR) -> dict:
    langs = {}
    for fp in sorted(sw_dir.glob("*.txt")):
        words = [w.strip() for w in fp.read_text(encoding="utf-8").splitlines() if w.strip()]
        langs[fp.stem] = _entry(words)
    return {"schema": {"file": "<iso>.txt — one function word per line, sorted"}, "languages": langs}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sw-dir", type=Path, default=_SW_DIR)
    ap.add_argument("--publish", metavar="REPO_ID", default=None, help="HF dataset repo to push to")
    ap.add_argument("--create", action="store_true", help="create the HF dataset repo if missing")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    manifest = build_manifest(args.sw_dir)
    (args.sw_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.sw_dir / "README.md").write_text(_CARD, encoding="utf-8")
    summary = ", ".join(f"{k}({v['n_words']})" for k, v in manifest["languages"].items())
    print(f"[export_stopwords] {len(manifest['languages'])} language lists → {args.sw_dir}/manifest.json\n"
          f"  {summary}", file=sys.stderr)

    if args.publish:
        files = ["manifest.json", "README.md"] + [fp.name for fp in sorted(args.sw_dir.glob("*.txt"))]
        if args.dry_run:
            print(f"[export_stopwords] dry-run → would push {len(files)} files to {args.publish}",
                  file=sys.stderr)
            return 0
        try:
            from huggingface_hub import HfApi
        except ImportError as e:
            raise SystemExit(f"[export_stopwords] needs huggingface_hub — pip install -e '.[publish]' ({e})")
        api = HfApi()
        if args.create:
            api.create_repo(args.publish, repo_type="dataset", exist_ok=True)
        for f in files:
            api.upload_file(path_or_fileobj=str(args.sw_dir / f), path_in_repo=f,
                            repo_id=args.publish, repo_type="dataset",
                            commit_message=f"target-stopwords: {len(manifest['languages'])} language lists")
        print(f"[export_stopwords] pushed {len(files)} files to {args.publish}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
