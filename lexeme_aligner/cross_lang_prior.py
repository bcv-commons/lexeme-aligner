"""Cross-target structural prior (#1 in internal-docs/gap-fill-scaling-strategy.md) — the scaling
MULTIPLIER, not a per-language fix. Every language aligns to the same MACULA lexeme anchor, so a lexeme's
STRUCTURAL SHAPE (does it usually render as one word or a phrase? how consistently?) can be aggregated
across every language we've ALREADY aligned and used to inform a gap-fill in a NEW language — no target-
language model, no per-language work. Confidence = how many independent languages agree, mirroring the
`senses_attested` cross-edition-agreement design, generalized from senses to alignment geometry.

Deliberately narrow in scope: cross-lingual RELATIVE POSITION isn't aggregated (word order differs by
language family — a French-derived position offset is not informative for Hindi), so this profile only
captures what genuinely transfers across unrelated languages: SPAN LENGTH / multi-word tendency (a
lexeme rendered by a fixed phrase — an idiom, a construct-state relation, a compound concept — tends to
need multiple target words in EVERY language, not just this one).

Edition grouping: `arb`+`arbn`, `eng`+`engy`, `swe`+`swk` are two EDITIONS of the same language, not two
languages — counting them separately would double-weight a language just for having more editions ingested.
Every statistic is computed PER LANGUAGE GROUP first (pooling its editions), then averaged EQUALLY across
language groups — so confidence genuinely means "N independent languages agree", not "N aligned files".

    python3 -m lexeme_aligner.cross_lang_prior --out resources/cross_lang_prior/profile.json
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from lexeme_aligner.run_pilot import _HI_METHODS

_OUT_DIR = Path("out")
_DEFAULT_OUT = Path("resources/cross_lang_prior/profile.json")
_METHODS = ("eflomal", "gloss")
_MIN_LANGS = 2                            # a profile entry needs ≥2 independent languages to be usable

# iso-in-jsonl -> language GROUP (dedupe editions of the same language before counting "N languages")
_LANG_GROUP = {"arbn": "arb", "engy": "eng", "swk": "swe"}


def _group(iso: str) -> str:
    return _LANG_GROUP.get(iso, iso)


def _hi(method: str, score: float) -> bool:
    return (method in _HI_METHODS
            or (method == "stat" and score >= 0.3)
            or (method == "eflomal" and score >= 0.9)
            or (method == "gapfill" and score >= 0.9))


def _isos_present(out_dir: Path) -> list[str]:
    isos = set()
    for m in _METHODS:
        for fp in glob.glob(str(out_dir / f"align_{m}_*_*.jsonl")):
            mobj = re.match(rf"align_{m}_([a-z0-9]+)_", os.path.basename(fp))
            if mobj:
                isos.add(mobj.group(1))
    return sorted(isos)


def build_profile(out_dir: Path = _OUT_DIR, min_langs: int = _MIN_LANGS) -> dict:
    """{lexeme: {n_langs, n_occ, span_mean, multiword_rate}} — per-language stats averaged EQUALLY
    across language groups (so a 2-edition language doesn't out-vote a 1-edition one)."""
    isos = _isos_present(out_dir)
    # lexeme -> lang_group -> [span_len, span_len, …]  (one entry per hi-conf occurrence, editions pooled)
    per_lex_lang: dict[str, dict[str, list]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for iso in isos:
        grp = _group(iso)
        for m in _METHODS:
            for fp in sorted(glob.glob(str(out_dir / f"align_{m}_{iso}_*.jsonl"))):
                with open(fp, encoding="utf-8") as fh:
                    for line in fh:
                        rec = json.loads(line)
                        for p in rec["pairs"]:
                            if not (p.get("content") and p.get("lexeme") and p.get("t_idx")
                                    and _hi(p.get("method", ""), p.get("score") or 0)):
                                continue
                            per_lex_lang[p["lexeme"]][grp].append(len(p["t_idx"]))

    profile = {}
    for lexeme, by_lang in per_lex_lang.items():
        langs = [g for g, spans in by_lang.items() if spans]
        if len(langs) < min_langs:
            continue
        lang_means = []
        lang_multi = []
        n_occ = 0
        for g in langs:
            spans = by_lang[g]
            n_occ += len(spans)
            lang_means.append(sum(spans) / len(spans))
            lang_multi.append(sum(1 for s in spans if s > 1) / len(spans))
        profile[lexeme] = {
            "n_langs": len(langs),
            "n_occ": n_occ,
            "span_mean": round(sum(lang_means) / len(lang_means), 3),      # equal per-language weight
            "multiword_rate": round(sum(lang_multi) / len(lang_multi), 3),  # equal per-language weight
        }
    return profile


_CARD = """---
license: cc0-1.0
tags:
- alignment
- multilingual
- bible
- interlingua
---

# cross-lingual-span-profile

A per-**MACULA-lexeme** structural profile — span length / multi-word tendency — aggregated across every
language the lexeme-aligner has aligned. Every language anchors to the same lexeme, so this is a
language-independent INTERLINGUA signal: it tells you whether a Hebrew/Greek lexeme typically needs a
single target word or a multi-word phrase (compound place names — "Kadesh Barnea" — compound numbers —
"four thousand"), based on what OTHER languages actually did, with NO target-language model for the
language you're applying it to.

`n_langs` = how many independent languages (editions of the same language pooled first, so a 2-edition
language doesn't out-vote a 1-edition one) attest the lexeme; `multiword_rate`/`span_mean` = the per-
language-averaged span statistics. Confidence scales with `n_langs` — refresh as more languages are
aligned (see the lexeme-aligner's `cross_lang_prior.py`).

**CC0-1.0** — derived alignment statistics, no source text redistributed.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, default=_OUT_DIR, help="dir with align_<method>_<iso>_*.jsonl")
    ap.add_argument("--min-langs", type=int, default=_MIN_LANGS)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    ap.add_argument("--publish", metavar="REPO_ID", default=None, help="HF dataset repo to push to")
    ap.add_argument("--create", action="store_true", help="create the HF dataset repo if missing")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    profile = build_profile(args.out_dir, args.min_langs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, sort_keys=True, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    multiword = sorted(profile.items(), key=lambda kv: -kv[1]["multiword_rate"])[:10]
    print(f"[cross_lang_prior] {len(profile)} lexemes (≥{args.min_langs} languages) → {args.out}\n"
          f"  most multi-word across languages: "
          f"{[(lx, r['multiword_rate'], r['n_langs']) for lx, r in multiword]}", file=sys.stderr)

    if args.publish:
        readme = args.out.parent / "README.md"
        manifest = args.out.parent / "manifest.json"
        readme.write_text(_CARD, encoding="utf-8")
        manifest.write_text(json.dumps(
            {"lexemes": len(profile), "min_langs": args.min_langs,
             "content_sha256": hashlib.sha256(
                 json.dumps(profile, sort_keys=True).encode()).hexdigest()},
            indent=2, sort_keys=True) + "\n", encoding="utf-8")
        files = [args.out.name, "manifest.json", "README.md"]
        if args.dry_run:
            print(f"[cross_lang_prior] dry-run → would push {files} to {args.publish}", file=sys.stderr)
            return 0
        try:
            from huggingface_hub import HfApi
        except ImportError as e:
            raise SystemExit(f"[cross_lang_prior] needs huggingface_hub — pip install -e '.[publish]' ({e})")
        api = HfApi()
        if args.create:
            api.create_repo(args.publish, repo_type="dataset", exist_ok=True)
        for f in files:
            api.upload_file(path_or_fileobj=str(args.out.parent / f), path_in_repo=f,
                            repo_id=args.publish, repo_type="dataset",
                            commit_message=f"cross-lingual-span-profile: {len(profile)} lexemes")
        print(f"[cross_lang_prior] pushed {files} to {args.publish}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
