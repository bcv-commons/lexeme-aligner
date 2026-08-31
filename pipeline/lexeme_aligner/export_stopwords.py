"""Publish the #3 target function-word lists as a STANDALONE resource — independent of any alignment.

Each `publish/target-stopwords/<iso>.txt` (from target_stopwords.compute_stopwords) is a per-language
function-word list induced from that language's own Bible text: frequency + dispersion, then RESCUED
against this language's own taken-pool alignment + prior-pack keyness so genuine content words that
happen to be frequent ("God", "Lord") are never dropped. The rescue is gated four ways — minimum aligned
mass, dominant-lexeme share, prior-pack content, and the source-side light-lexeme veto
(config/light_lexemes.json); see target_stopwords.py for what each one is for. Many of the target languages have NO existing curated stopword
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

from lexeme_aligner.config import HF_CHUNK_SIZE
from lexeme_aligner.hf_bulk_publish import publish_chunked

_SW_DIR = Path("publish/target-stopwords")

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
("God", "Lord") are never dropped.

A candidate word is rescued out of the list (judged a real content word, not a function word) only when
**all four** hold — see the lexeme-aligner's `target_stopwords.py`:

1. it carries at least **25 aligned occurrences** — a share read off one or two observations is noise,
   and function words are systematically under-aligned, so thin aligned mass is itself evidence;
2. its **dominant Hebrew/Greek lexeme holds ≥40%** of its aligned mass — a true function word instead
   scatters thinly across dozens or hundreds of distinct lexemes;
3. that lexeme is marked **content** in the source-side prior pack;
4. that lexeme is not itself **semantically light** (`config/light_lexemes.json`). Copulas, *have*,
   quantifiers, negators, possessives, modals and the Hebrew nouns grammaticalised into prepositions
   (*panim* → "before", *yad* → "by") are all correctly rendered by target FUNCTION words, so aligning
   to one proves nothing about content-hood — without this the lists lost *is/are*, *all*, *one*, *no*,
   *can*, *before*.

Many of the covered languages have **no existing curated stopword list anywhere** — this is a reusable
resource for search, IR, topic modeling, or any NLP task needing one in these languages.

Languages written in scripts without whitespace word separation (Han, Japanese, Myanmar) are segmented
by rule — Han per character, Japanese at script boundaries, Myanmar per syllable — with no segmenter
model or download, so the method still runs on any language that has a Bible and nothing else.

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
    ap.add_argument("--chunk-size", type=int, default=HF_CHUNK_SIZE,
                    help="files per HF commit, with --publish (default from ALIGNER_HF_CHUNK_SIZE)")
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
        # chunked, not one upload_file() commit per file — HF caps commits at 128/hour/repo, and this
        # dataset alone can have 900+ files, so a naive per-file loop hits that wall almost immediately.
        publish_chunked(args.sw_dir, args.publish, files, args.create, args.dry_run,
                        args.chunk_size, label="target-stopwords")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
