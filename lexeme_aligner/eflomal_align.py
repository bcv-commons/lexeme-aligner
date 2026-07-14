"""Statistical alignment via eflomal (option 2) — IBM/HMM with a distortion model.

The upgrade over the pure-Python IBM-1 (stat_align): eflomal adds Bayesian priors + an HMM
distortion model, so it penalizes aligning a content word to a far-off frequent function word —
exactly the noise the plain IBM-1 tail had. Strong's-anchored (source token = the code), trained
on the whole corpus at once. Optionally seeded with LEXICAL PRIORS (e.g. our gloss high-confidence
alignments) → semi-supervised.

eflomal is a build-time C tool (like the pkf converter) driven from Python; never a runtime dep.
Aligns the FULL corpus in one call, then we symmetrize fwd+rev with grow-diag-final-and.
"""
from __future__ import annotations

import collections
import tempfile
from dataclasses import dataclass

from eflomal import Aligner


@dataclass
class EMatch:
    h_idx: int
    t_idx: list[int]
    score: float
    method: str = "eflomal"


def _parse(line: str) -> set[tuple[int, int]]:
    out = set()
    for pair in line.split():
        s, t = pair.split("-")
        out.add((int(s), int(t)))
    return out


def _grow_diag_final_and(fwd, rev, n_src, n_trg):
    """Standard Moses/fast_align symmetrization. Intersection (high precision) grown along the
    diagonal from the union, then final-and adds union points whose BOTH ends are still free."""
    inter = fwd & rev
    union = fwd | rev
    aligned = set(inter)
    src_al = {s for s, _ in aligned}
    trg_al = {t for _, t in aligned}
    NEIGH = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
    added = True
    while added:
        added = False
        for (s, t) in list(aligned):
            for ds, dt in NEIGH:
                ns, nt = s + ds, t + dt
                if 0 <= ns < n_src and 0 <= nt < n_trg and (ns, nt) in union \
                        and (ns not in src_al or nt not in trg_al):
                    aligned.add((ns, nt))
                    src_al.add(ns)
                    trg_al.add(nt)
                    added = True
    for (s, t) in union:
        if s not in src_al and t not in trg_al:
            aligned.add((s, t))
            src_al.add(s)
            trg_al.add(t)
    return aligned, inter


class EflomalAligner:
    def __init__(self, anchor: str = "strong", stem: bool = False):
        # source-side key eflomal learns co-occurrence over: "strong" (coarse rollup) or "lexeme"
        # (finer — separates homonyms one Strong's conflates). Decode is positional either way, so the
        # output pairs carry both regardless; only the statistical model's granularity changes.
        # stem: #2 — feed the target's STEM (norm.stem) instead of its surface, so inflected variants of a
        # word pool into one co-occurrence type. Decode stays positional → output surface = the raw token.
        self.by_verse: dict[tuple, dict] = {}
        self.anchor = anchor
        self.stem = stem

    def run(self, recs, norm, priors_pairs=None) -> None:
        src_lines, trg_lines, meta = [], [], []
        tgt_form = (lambda w: norm.stem(w)) if self.stem else (lambda w: norm.forms(w)[0])
        for rec in recs:
            src_toks = [t for t in rec.heb if getattr(t, self.anchor)]
            if not src_toks or not rec.toks:
                continue
            src_lines.append(" ".join(getattr(t, self.anchor) for t in src_toks))
            trg_lines.append(" ".join(tgt_form(w) for w in rec.toks))
            meta.append((rec.book, rec.ch, rec.v, src_toks, rec.toks))

        with tempfile.NamedTemporaryFile("w+", suffix=".src") as sf, \
             tempfile.NamedTemporaryFile("w+", suffix=".trg") as tf, \
             tempfile.NamedTemporaryFile("w+", suffix=".pri") as pf, \
             tempfile.NamedTemporaryFile("r", suffix=".fwd") as ff, \
             tempfile.NamedTemporaryFile("r", suffix=".rev") as rf:
            sf.write("\n".join(src_lines) + "\n"); sf.flush(); sf.seek(0)
            tf.write("\n".join(trg_lines) + "\n"); tf.flush(); tf.seek(0)
            priors_input = None
            if priors_pairs:
                # eflomal lexical prior format: "LEX\tsrcword\ttrgword\talpha" (weight last)
                for s, t, c in priors_pairs:
                    pf.write(f"LEX\t{s}\t{t}\t{float(c)}\n")
                pf.flush(); pf.seek(0)
                priors_input = pf
            Aligner().align(sf, tf, links_filename_fwd=ff.name, links_filename_rev=rf.name,
                            priors_input=priors_input, quiet=True)
            fwds = [_parse(l) for l in ff]
            rf.seek(0)
            revs = [_parse(l) for l in rf]

        for i, (book, ch, v, src_toks, toks) in enumerate(meta):
            fwd = fwds[i] if i < len(fwds) else set()
            rev = revs[i] if i < len(revs) else set()
            sym, inter = _grow_diag_final_and(fwd, rev, len(src_toks), len(toks))
            self.by_verse[(book, ch, v)] = {"src": src_toks, "sym": sym, "inter": inter}

    def decode(self, rec) -> list[EMatch]:
        info = self.by_verse.get((rec.book, rec.ch, rec.v))
        if not info:
            return []
        by_s: dict[int, list[int]] = collections.defaultdict(list)
        for s, t in info["sym"]:
            by_s[s].append(t)
        out = []
        for s, ts in by_s.items():
            if s >= len(info["src"]):
                continue
            htok = info["src"][s]
            # intersection points are the reliable core → higher score
            score = 0.9 if any((s, t) in info["inter"] for t in ts) else 0.6
            out.append(EMatch(htok.idx, sorted(ts), score, "eflomal"))
        return sorted(out, key=lambda m: m.h_idx)
