"""Bootstrap gloss priors — the seed dictionary the handover's recipes feed the gloss run.

The external per-language gloss CSVs (`resources/word_glosses/…`) are absent, and on the lexeme spine
`HebToken.lex` is None — so the legacy `GlossPriors` yields nothing. This supplies the same interface
(`lookup(tok) -> [word-tuple, …]`) from data this repo owns instead:

  • recipe R1  — the language's OWN `lexeme-alignments`, keyness-filtered: per lexeme, its dominant target
                 renderings (content words only; prior-pack keyness drops known function words).
  • recipe LXX — OT surfaces carried to Greek lexemes via `prior_pack.lxx_greek` → NT candidates for
                 lexemes the NT alignment missed (the cross-testament gap-fill).

Keyed on the MACULA `lexeme` (strong rollup as fallback). Needs `lexeme-alignments/iso=<iso>/` to exist —
so the gloss run is a SECOND pass after eflomal (bootstrap). The prior-pack is optional (skips the
keyness gate + LXX bridge if absent).
"""
from __future__ import annotations

import collections
import re
from pathlib import Path

from lexeme_aligner.benchmark import norm_strong    # lexeme "hbo:0871a" -> strong "H0871"
from lexeme_aligner.config import LEX_ROOT, PRIOR_PACK

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _tuple(surface: str) -> tuple[str, ...]:
    return tuple(w.lower() for w in _WORD.findall(surface or ""))


def _top_surfaces(ctr: collections.Counter, topk: int, min_share: float) -> list[tuple[str, ...]]:
    total = sum(ctr.values()) or 1
    out: list[tuple[str, ...]] = []
    for surface, n in ctr.most_common(topk):
        if n / total >= min_share:
            t = _tuple(surface)
            if t and t not in out:
                out.append(t)
    return out


class BootstrapPriors:
    def __init__(self, iso: str, aligned_root: Path = LEX_ROOT,
                 prior_pack: Path = PRIOR_PACK, min_share: float = 0.04, topk: int = 5):
        import pyarrow.parquet as pq
        self.by_lexeme: dict[str, list] = {}
        self.by_strong: dict[str, list] = {}
        self.lxx: dict[str, list] = {}                    # Greek lexeme -> LXX-bridged OT candidates
        self.stats = {"lexemes": 0, "strongs": 0, "lxx": 0}

        prior_rows: dict[str, dict] = {}
        pp = Path(prior_pack)
        if pp.exists():
            prior_rows = {r["lexeme"]: r for r in pq.read_table(pp).to_pylist()}

        fp = Path(aligned_root) / f"iso={iso}" / "data.parquet"
        if not fp.exists():
            self.missing = str(fp)                        # gloss run will be near-empty; run eflomal first
            return
        self.missing = None
        per_lex: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        per_str: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for r in pq.read_table(fp).to_pylist():
            if r.get("method", "eflomal") != "eflomal":   # union parquet → bootstrap from the eflomal base
                continue
            strong = r.get("strong") or norm_strong(r["lexeme"])   # `strong` dropped from storage 2026-07
            per_lex[r["lexeme"]][r["surface"]] += r["count"]
            if strong:
                per_str[strong][r["surface"]] += r["count"]

        for lexeme, ctr in per_lex.items():
            k = prior_rows.get(lexeme, {}).get("keyness") if prior_rows else "n/a"
            if prior_rows and lexeme in prior_rows and k is None:
                continue                                  # known function word — not a gloss anchor
            cands = _top_surfaces(ctr, topk, min_share)
            if cands:
                self.by_lexeme[lexeme] = cands
        for strong, ctr in per_str.items():
            cands = _top_surfaces(ctr, topk, min_share)
            if cands:
                self.by_strong[strong] = cands

        # LXX bridge: Hebrew lexeme's dominant OT surfaces → the Greek lexemes the LXX renders it into
        if prior_rows:
            strong2lex: dict[str, list] = collections.defaultdict(list)
            for lx, r in prior_rows.items():
                if r.get("testament") == "NT" and r.get("strong") and r.get("keyness") is not None:
                    strong2lex[r["strong"]].append(lx)
            for heb_lexeme, ctr in per_lex.items():
                if not heb_lexeme.startswith("hbo"):
                    continue
                greeks = (prior_rows.get(heb_lexeme) or {}).get("lxx_greek") or []
                if not greeks:
                    continue
                top = _top_surfaces(ctr, 3, min_share)
                for g in greeks:
                    for grc in strong2lex.get(g, []):
                        bucket = self.lxx.setdefault(grc, [])
                        bucket += [t for t in top if t not in bucket]

        self.stats = {"lexemes": len(self.by_lexeme), "strongs": len(self.by_strong), "lxx": len(self.lxx)}

    def lookup(self, tok) -> list[tuple[str, ...]]:
        """Candidate target renderings, most-specific first: lexeme → strong rollup → LXX-bridged."""
        lexeme = getattr(tok, "lexeme", None) or ""
        out: list[tuple[str, ...]] = []
        for src in (self.by_lexeme.get(lexeme), self.by_strong.get(tok.strong or ""), self.lxx.get(lexeme)):
            if src:
                out += [t for t in src if t not in out]
        return out
