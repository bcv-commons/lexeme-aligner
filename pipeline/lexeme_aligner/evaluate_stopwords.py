"""Cross-check our induced target-stopwords lists against an external curated resource, where one
exists — a validation/spot-check, NOT a source (see target_stopwords.py's own docstring: ours are
induced per-language from that language's own Bible text + alignment-based content-word rescue,
covering 2,000+ languages; no external list comes close to that coverage, so this is one-way
verification only, never a fallback or an input to the induction itself).

External resource: `stopwords-iso` (github.com/stopwords-iso/stopwords-iso, MIT), a single JSON file,
58 languages, ISO 639-1 keyed. Mapped here to the ISO 639-3 codes our own dataset uses (a few languages
have more than one plausible 639-3 candidate — e.g. "zh" could be `zho` or `cmn` — every candidate is
tried, whichever we actually have a local list for wins).

Two ratios, since the lists differ hugely in SIZE by design (ours is a deliberately small top-K +
dispersion-floor set; stopwords-iso's are much larger, closer to "all closed-class + common words" —
e.g. English: ours=50 words, theirs=1,298): `precision = |ours ∩ theirs| / |ours|` (of OUR words, how
many does the external list also consider a stopword? — the meaningful check, since our list is the
smaller/stricter one) and `recall = |ours ∩ theirs| / |theirs|` (structurally capped low by the size
gap alone — reported for completeness, not the number to judge quality by). A low PRECISION on a
well-resourced language (English, French, Spanish) would suggest the induction method has a real
problem; a low recall does not — that's expected from the size mismatch.

    python3 -m lexeme_aligner.evaluate_stopwords
    python3 -m lexeme_aligner.evaluate_stopwords --iso fra,spa,eng
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"
_EXTERNAL_URL = "https://raw.githubusercontent.com/stopwords-iso/stopwords-iso/master/stopwords-iso.json"
_CACHE = Path("config/external_stopwords/stopwords-iso.json")
_OURS_DIR = Path("publish/target-stopwords")

# ISO 639-1 -> candidate ISO 639-3 code(s), most-specific-first, for stopwords-iso's 58 languages.
# Only languages actually present in that file are listed — no need to cover the other ~580.
_TO_639_3 = {
    "af": ["afr"], "ar": ["arb", "ara"], "bg": ["bul"], "bn": ["ben"], "br": ["bre"], "ca": ["cat"],
    "cs": ["ces"], "da": ["dan"], "de": ["deu"], "el": ["ell"], "en": ["eng"], "eo": ["epo"],
    "es": ["spa"], "et": ["est"], "eu": ["eus"], "fa": ["pes", "fas"], "fi": ["fin"], "fr": ["fra"],
    "ga": ["gle"], "gl": ["glg"], "gu": ["guj"], "ha": ["hau"], "he": ["heb"], "hi": ["hin"],
    "hr": ["hrv"], "hu": ["hun"], "hy": ["hye"], "id": ["ind"], "it": ["ita"], "ja": ["jpn"],
    "ko": ["kor"], "ku": ["kmr", "kur"], "la": ["lat"], "lt": ["lit"], "lv": ["lav"], "mr": ["mar"],
    "ms": ["msa", "zlm"], "nl": ["nld"], "no": ["nob", "nno", "nor"], "pl": ["pol"], "pt": ["por"],
    "ro": ["ron"], "ru": ["rus"], "sk": ["slk"], "sl": ["slv"], "so": ["som"], "st": ["sot"],
    "sv": ["swe"], "sw": ["swa"], "th": ["tha"], "tl": ["tgl"], "tr": ["tur"], "uk": ["ukr"],
    "ur": ["urd"], "vi": ["vie"], "yo": ["yor"], "zh": ["cmn", "zho"], "zu": ["zul"],
}


def fetch_external(cache: Path = _CACHE, force: bool = False) -> dict[str, list[str]]:
    if force or not cache.exists():
        req = urllib.request.Request(_EXTERNAL_URL, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310 — fixed https origin
            data = r.read()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        print(f"[evaluate_stopwords] fetched {_EXTERNAL_URL} ({len(data)} bytes) → {cache}", file=sys.stderr)
    return json.loads(cache.read_text(encoding="utf-8"))


def ours(iso: str, ours_dir: Path = _OURS_DIR) -> set[str] | None:
    fp = ours_dir / f"{iso}.txt"
    if not fp.exists():
        return None
    return {w.strip().lower() for w in fp.read_text(encoding="utf-8").splitlines() if w.strip()}


def matched_isos(external: dict[str, list[str]], ours_dir: Path = _OURS_DIR) -> list[tuple[str, str]]:
    """[(iso_639_1, our_iso_639_3), ...] for every stopwords-iso language we also have a local list
    for — first matching 639-3 candidate wins."""
    out = []
    for code_1 in external:
        for code_3 in _TO_639_3.get(code_1, []):
            if (ours_dir / f"{code_3}.txt").exists():
                out.append((code_1, code_3))
                break
    return out


def report(external: dict[str, list[str]], ours_dir: Path = _OURS_DIR,
          only: set[str] | None = None) -> list[dict]:
    rows = []
    for code_1, code_3 in matched_isos(external, ours_dir):
        if only and code_3 not in only:
            continue
        theirs = {w.strip().lower() for w in external[code_1] if w.strip()}
        mine = ours(code_3, ours_dir) or set()
        inter = mine & theirs
        precision = len(inter) / len(mine) if mine else 0.0     # of OURS, how much theirs also has
        recall = len(inter) / len(theirs) if theirs else 0.0    # of THEIRS, how much we recovered
        rows.append({"iso": code_3, "iso_639_1": code_1, "n_theirs": len(theirs), "n_mine": len(mine),
                    "n_overlap": len(inter), "precision": precision, "recall": recall})
    rows.sort(key=lambda r: r["precision"])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", default=None, help="comma-sep ISO 639-3 codes to restrict to (default: every "
                    "language present in both stopwords-iso and our own published lists)")
    ap.add_argument("--refetch", action="store_true", help="re-download stopwords-iso.json even if cached")
    ap.add_argument("--ours-dir", type=Path, default=_OURS_DIR)
    args = ap.parse_args()

    external = fetch_external(force=args.refetch)
    only = {s.strip() for s in args.iso.split(",")} if args.iso else None
    rows = report(external, args.ours_dir, only)

    if not rows:
        print("[evaluate_stopwords] no overlapping languages found between stopwords-iso and "
              f"{args.ours_dir}", file=sys.stderr)
        return 1

    print(f"{'iso':<6} {'639-1':<6} {'theirs':>7} {'ours':>7} {'overlap':>8} {'precision':>10} {'recall':>7}")
    for r in rows:
        print(f"{r['iso']:<6} {r['iso_639_1']:<6} {r['n_theirs']:>7} {r['n_mine']:>7} "
              f"{r['n_overlap']:>8} {r['precision']:>9.1%} {r['recall']:>6.1%}")
    mean_precision = sum(r["precision"] for r in rows) / len(rows)
    print(f"\n{len(rows)} language(s) compared, mean precision {mean_precision:.1%}")
    print("\nNote: PRECISION (of our smaller induced list, how much does the external list agree with) is "
          "the number to judge quality by. RECALL is structurally capped low by the size gap alone (our "
          "lists are a deliberately small top-K + dispersion-floor set; stopwords-iso's are much larger) "
          "and is reported for completeness only — not a quality signal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
