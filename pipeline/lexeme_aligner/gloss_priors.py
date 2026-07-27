"""Target-language gloss priors for the gloss-anchored strategy — generic, per language.

Sources (all already shipped in resources/):
- word_glosses/hbo/<LangName>.csv   lex-keyed, per-binyan columns (default,qal,nif,…)
- llm_strongs_glosses/<iso>.tsv     strong-keyed single glosses (tw/llm gap-fill)

lookup(lex, stem, strong) returns an ordered candidate list — most specific first
(per-stem → per-lex default → per-strong). Each candidate is a tuple of normalized
words (multi-word glosses stay multi-word so they can match token spans).
"""
from __future__ import annotations

import csv
import re

from lexeme_aligner.config import RESOURCES as RES

_SPLIT = re.compile(r"[;,/]| atau ")          # variant separators (incl. Indonesian "or")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _variants(cell: str) -> list[tuple[str, ...]]:
    """A gloss cell → list of normalized word-tuples ('membawa kembali' → ('membawa','kembali'))."""
    out = []
    for var in _SPLIT.split(cell or ""):
        words = tuple(w.lower() for w in _WORD.findall(var))
        if words and words not in out:
            out.append(words)
    return out


class GlossPriors:
    def __init__(self, lang_name: str, iso: str):
        self.perstem: dict[str, dict[str, list]] = {}
        p = RES / "word_glosses" / "hbo" / f"{lang_name}.csv"
        if p.exists():
            with p.open(encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    lex = (row.get("lex") or "").strip()
                    if not lex:
                        continue
                    cells = {col: _variants(val) for col, val in row.items()
                             if col and col != "lex" and val and _variants(val)}
                    if cells:
                        self.perstem[lex] = cells

        self.by_strong: dict[str, list] = {}
        p = RES / "llm_strongs_glosses" / f"{iso}.tsv"
        if p.exists():
            with p.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("#") or line.startswith("strong\t"):
                        continue
                    c = line.rstrip("\n").split("\t")
                    if len(c) >= 4 and c[0]:
                        vs = _variants(c[3])
                        if vs:
                            self.by_strong.setdefault(c[0], []).extend(
                                v for v in vs if v not in self.by_strong.get(c[0], []))

        # Translation-Words article TITLES in the target language (keyterm names like
        # Allah, Israel) — strongs_tw.tsv (strong → article) × tw_articles/<iso>.json.
        self.by_strong_tw: dict[str, list] = {}
        tw_map = RES / "strongs_tw.tsv"
        tw_articles = RES / "tw_articles" / f"{iso}.json"
        if tw_map.exists() and tw_articles.exists():
            import json
            titles = {k: v.get("title", "") for k, v in
                      json.loads(tw_articles.read_text(encoding="utf-8")).items()}
            with tw_map.open(encoding="utf-8") as fh:
                for line in fh:
                    c = line.rstrip("\n").split("\t")
                    if len(c) >= 2 and c[0] != "strong" and titles.get(c[1]):
                        vs = _variants(titles[c[1]])
                        if vs:
                            self.by_strong_tw.setdefault(c[0], []).extend(
                                v for v in vs if v not in self.by_strong_tw.get(c[0], []))

    def lookup(self, tok) -> list[tuple[str, ...]]:
        lex, stem, strong = tok.lex, tok.stem, tok.strong   # legacy BHSA-lex CSV path (tok-keyed now)
        out: list[tuple[str, ...]] = []
        row = self.perstem.get(lex or "")
        if row:
            if stem and row.get(stem):
                out += [v for v in row[stem] if v not in out]
            if row.get("default"):
                out += [v for v in row["default"] if v not in out]
        if strong and self.by_strong.get(strong):
            out += [v for v in self.by_strong[strong] if v not in out]
        if strong and self.by_strong_tw.get(strong):
            out += [v for v in self.by_strong_tw[strong] if v not in out]
        return out
