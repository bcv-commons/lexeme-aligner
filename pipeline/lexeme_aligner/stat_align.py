"""Stage (b′): statistical alignment — Strong's-anchored IBM Model 1 (pure-Python EM).

The 'universal spine' from docs/aligner-plan.md — needs ONLY the parallel text + Strong's,
no glosses, no encoder, no LLM. So it reaches languages nothing else can (see the ensemble
table in the plan). This is the portable pure-Python core of what eflomal does (eflomal adds
Bayesian priors + an HMM distortion model — a drop-in upgrade later).

Anchored on **Strong's**, not raw Hebrew surface: the source token of occurrence *n* of a
lexeme is its code (H0559), so co-occurrence pools across every occurrence corpus-wide — far
more data-efficient than aligning inflected surface forms, and it yields P(target | Strong's)
directly (= the statistical lexeme-alignments). Learns from the whole corpus, so run it on as much
parallel text as exists (the full OT here), not a single book.
"""
from __future__ import annotations

import collections
import math
import sys
from dataclasses import dataclass

NULL = "<NULL>"          # the source "null" that generates untranslatable target words


@dataclass
class StatMatch:
    h_idx: int
    t_idx: list[int]
    score: float
    method: str = "stat"


class IBM1:
    """P(target_word | strong). Train with EM over (strongs, target_tokens) sentence pairs."""

    def __init__(self, iters: int = 6):
        self.iters = iters
        self.t: dict[str, dict[str, float]] = {}     # strong -> {target: prob}

    def train(self, pairs: list[tuple[list[str], list[str]]], warn=sys.stderr) -> None:
        # vocab: which targets co-occur with which source (incl. NULL for every sentence)
        co: dict[str, set[str]] = collections.defaultdict(set)
        for src, tgt in pairs:
            for s in [NULL] + src:
                co[s].update(tgt)
        self.t = {s: {w: 1.0 / len(ws) for w in ws} for s, ws in co.items()}
        print(f"[stat] IBM-1: {len(pairs)} verse pairs, {len(self.t)} source codes, "
              f"{sum(len(v) for v in self.t.values())} co-occurrence cells", file=warn)

        for it in range(self.iters):
            count: dict[str, dict[str, float]] = collections.defaultdict(lambda: collections.defaultdict(float))
            total: dict[str, float] = collections.defaultdict(float)
            loglik = 0.0
            for src, tgt in pairs:
                srcs = [NULL] + src
                for w in tgt:
                    denom = sum(self.t[s].get(w, 0.0) for s in srcs)
                    if denom <= 0:
                        continue
                    loglik += math.log(denom)
                    for s in srcs:
                        p = self.t[s].get(w, 0.0)
                        if p > 0:
                            c = p / denom
                            count[s][w] += c
                            total[s] += c
            for s, ws in count.items():
                tot = total[s]
                self.t[s] = {w: c / tot for w, c in ws.items()}
            print(f"[stat]  iter {it + 1}/{self.iters}  avg loglik/target "
                  f"{loglik / max(1, sum(len(t) for _, t in pairs)):.3f}", file=warn)

    def prob(self, strong: str, word: str) -> float:
        return self.t.get(strong, {}).get(word, 0.0)

    def decode(self, heb, tokens: list[str], norm, threshold: float = 0.05) -> list[StatMatch]:
        """Per-verse alignment: each Strong's-bearing Hebrew token → its best target token by
        P(target|strong), greedy one-to-one, above a probability threshold."""
        tnorm = [norm.forms(t)[0] for t in tokens]
        cands: list[tuple[float, int, int]] = []      # (prob, h_idx, t_idx)
        for h in heb:
            if not h.strong:
                continue
            row = self.t.get(h.strong)
            if not row:
                continue
            for j, w in enumerate(tnorm):
                p = row.get(w, 0.0)
                if p >= threshold:
                    cands.append((p, h.idx, j))
        cands.sort(reverse=True)
        used_h: set[int] = set()
        used_t: set[int] = set()
        out: list[StatMatch] = []
        for p, hi, j in cands:
            if hi in used_h or j in used_t:
                continue
            used_h.add(hi)
            used_t.add(j)
            out.append(StatMatch(hi, [j], round(p, 3)))
        return sorted(out, key=lambda m: m.h_idx)
