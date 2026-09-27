"""P1 (internal-docs/aim1-multiword-grammar-tools-plan.md, 2026-09-27): a TYPED function-word lexicon
per target language — {article, adposition, conjunction, pronoun, negation} → {word: share} — learned
from our own eflomal alignments of the SEPARATE source function tokens.

WHY THIS IS NOT THE SELF-REFERENTIAL TRAP: every span-length predictor learned from our own output has
failed (cross_lang_prior r=0.075, D3's spread −0.002 where +1.41 was expected) because our output has
almost no span-length variance to learn from. This lexicon learns something different — WHICH target
words render the Hebrew/Greek articles, prepositions, conjunctions and pronouns — from tokens that are
(a) their own source tokens in the MACULA spine (הַ/ὁ, וְ/καί, בְּ/ἐν, pronominal suffixes like הָ "her" are
all split out as `is_content=0` tokens), and (b) aligned 1:1 with high confidence, i.e. exactly the part
of our output that IS reliable. Measured before building (plan §8.H2, spa_r09): article class top-8
covers 80% (el 19.4, la 18.6, los 12.6, del 8.5, de 7.3, las 5.3, que 4.9, al 3.5), conjunction 89%
(y 77.7), adposition 78% (á, de, en, para, por, sobre …); pronoun is the noisy one (top-8 only 40%).

WHAT IT IS FOR: `span_extension`'s candidate gate was the flat `StopwordFilter.is_function(word)` — it
cannot tell an article from a possessive determiner from a conjunction, which is the documented root of
fra's "su/ses swept up as articles" steal and of the 85.2%-of-legitimate-members block gapfill.py's own
docstring measured. A typed gate lets an `articles` trigger take only article-class words, a
`case_marking` trigger only adposition-class words (R6 in the plan).

Contractions straddle classes on purpose (spa `del`/`al` are article+adposition): a word may belong to
several classes; membership is by per-class share floor, never exclusive.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import OUT

_CACHE_DIR = Path("pipeline/work/cache/function_classes")

# source LEMMA (as the base-chain jsonl `lemma` field spells it — MACULA/BHSA lemma strings) -> class.
# Hand-listed and small on purpose: these are the closed-class function tokens of Hebrew and Greek, not
# something to learn. Extend here, never per language.
SOURCE_CLASSES: dict[str, frozenset[str]] = {
    "article": frozenset({"הַ", "ὁ"}),
    "conjunction": frozenset({"וְ", "καί", "δέ", "ἀλλά", "γάρ", "οὖν", "אוֹ", "ἤ"}),
    "adposition": frozenset({"בְּ", "לְ", "מִן", "עַל", "אֶל", "כְּ", "עִם", "אֵת", "תַּחַת", "עַד", "אַחַר",
                             "ἐν", "εἰς", "ἐκ", "ἐπί", "πρός", "διά", "ἀπό", "κατά", "μετά", "ὑπό", "περί",
                             "σύν", "παρά", "ὑπέρ", "ἀντί", "πρό", "ἕως", "ἄχρι"}),
    "pronoun": frozenset({"הוּא", "הִיא", "אַתָּה", "אַתְּ", "אֲנִי", "אָנֹכִי", "הֵם", "הֵנָּה", "אַתֶּם", "אֲנַחְנוּ",
                          "αὐτός", "ἐγώ", "σύ", "ἡμεῖς", "ὑμεῖς", "οὗτος", "ἐκεῖνος", "ὅς"}),
    "negation": frozenset({"לֹא", "אַל", "אֵין", "οὐ", "μή", "οὐδέ", "μηδέ", "οὐκ", "οὐχ"}),
}

# which classes each span_extension trigger may take a candidate from (R6). `possession_affix` is the
# "his servant" case — a free possessive word, which in most target languages is a pronoun-class word
# (su, his, उसका) or an adposition-class genitive marker; never an article or a conjunction.
TRIGGER_CLASSES: dict[str, frozenset[str]] = {
    "articles": frozenset({"article"}),
    "case_marking": frozenset({"adposition"}),
    "struct": frozenset({"adposition"}),
    "possessor": frozenset({"adposition"}),
    "possession_affix": frozenset({"pronoun", "adposition"}),
    "definite": frozenset({"article"}),
}


def build_function_classes(tag: str, out_dir: Path = OUT, methods: tuple[str, ...] = ("eflomal",),
                           min_count: int = 3, min_share: float = 0.002) -> dict[str, dict[str, float]]:
    """{class: {target word (lowercased): share within class}} from single-target pairs whose source
    token is non-content and whose lemma is in `SOURCE_CLASSES`. `min_count`/`min_share` drop the long
    tail of one-off mis-alignments; a word can appear under several classes."""
    counts: dict[str, collections.Counter] = {c: collections.Counter() for c in SOURCE_CLASSES}
    lemma_to_class = {lem: cls for cls, lems in SOURCE_CLASSES.items() for lem in lems}
    for m in methods:
        for fp in tag_files(out_dir, m, tag):
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    for p in rec["pairs"]:
                        if p.get("content"):
                            continue
                        cls = lemma_to_class.get(p.get("lemma") or "")
                        if cls is None or len(p.get("t_idx") or []) != 1:
                            continue
                        counts[cls][p["target"].lower()] += 1
    out: dict[str, dict[str, float]] = {}
    for cls, c in counts.items():
        total = sum(c.values())
        if not total:
            out[cls] = {}
            continue
        out[cls] = {w: round(n / total, 5) for w, n in c.items() if n >= min_count and n / total >= min_share}
    return out


class FunctionClasses:
    """Cached per-language typed lexicon. `.classes_of(word)` → set of class names (empty = unknown /
    content word). Built from `tag`'s base-chain jsonl on first use and cached under `publish_iso`,
    same shape of contract as `target_stopwords.StopwordFilter`."""

    def __init__(self, publish_iso: str, tag: str | None = None, out_dir: Path = OUT,
                 cache_dir: Path = _CACHE_DIR):
        self.iso = publish_iso
        self._fp = Path(cache_dir) / f"{publish_iso}.json"
        if self._fp.exists():
            self.classes = json.loads(self._fp.read_text(encoding="utf-8"))
        elif tag:
            self.classes = build_function_classes(tag, out_dir)
            self._fp.parent.mkdir(parents=True, exist_ok=True)
            self._fp.write_text(json.dumps(self.classes, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                encoding="utf-8")
        else:
            self.classes = {}
        self._index: dict[str, set[str]] = collections.defaultdict(set)
        for cls, words in self.classes.items():
            for w in words:
                self._index[w].add(cls)

    def classes_of(self, word: str) -> set[str]:
        return set(self._index.get((word or "").lower(), ()))

    def allows(self, word: str, allowed: frozenset[str] | set[str]) -> bool:
        return bool(self.classes_of(word) & set(allowed))


def main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="edition TAG whose align_eflomal_*.jsonl to read")
    ap.add_argument("--publish-iso", required=True, help="bare iso the cache is keyed on")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args(argv)
    fc = FunctionClasses(a.publish_iso, a.iso, a.out)
    for cls, words in fc.classes.items():
        top = sorted(words.items(), key=lambda kv: -kv[1])[:a.top]
        print(f"{cls:12s} n_words={len(words):4d}  top: " +
              ", ".join(f"{w} {100*s:.1f}%" for w, s in top), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
