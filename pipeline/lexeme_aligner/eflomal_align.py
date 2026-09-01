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


def _content_priority(sym, union, src_toks, inter=frozenset(), displace_weak: bool = False):
    """Reallocate target positions held ONLY by non-content source tokens to content source tokens
    that got nothing (protection #1, docs/pipeline-overview.md).

    Every spine token carries a Strong's, so eflomal's source line includes the 312,853 non-content
    tokens (waw, article, ὁ, καί, prepositions) and they compete for target positions on equal footing
    with real content — then get deleted at export, four steps later, after the slots are spent.
    Measured on fra NT against Clear gold: 12.6% of judgeable content tokens fail because a
    function-word source token holds the target word they needed and gold does not credit it there.

    This does NOT touch the model — eflomal still trains on the full source line, so the co-occurrence
    statistics that make the alignment work are unchanged. It changes only ALLOCATION, and only in the
    one direction that can help: a content source token with NO link at all may claim a position that
    the union already proposed for it and that only a non-content token currently holds. An
    already-aligned content token is not allowed to grow (that is span extension, a different question
    with its own measurements), and a position held by another CONTENT token is never taken.

    `displace_weak` (level 2) reaches the COMMON theft case level 1 cannot: the content token is not
    unaligned, it just holds the WRONG position while the article holds the right one. It lets a content
    token whose every current link is outside the forward∩reverse intersection — the unreliable half —
    trade that link set for a single position the union also proposed and only a non-content token
    contests. This DISPLACES rather than adds, so it cannot inflate spans (the metric trap that made
    the content-only variant look good); it is nonetheless the riskiest change in this area, since a
    correct-but-weak link can be traded away. Judge it on token precision, never on `hit`."""
    content_s = {i for i, tok in enumerate(src_toks) if tok.is_content}
    holders: dict[int, set] = {}
    for s, t in sym:
        holders.setdefault(t, set()).add(s)
    aligned_s = {s for s, _ in sym}
    # candidate positions: held, but by non-content sources only
    reassignable = {t for t, ss in holders.items() if not (ss & content_s)}
    if not reassignable:
        return sym, 0
    # level 2 only: content tokens holding nothing but weak (non-intersection) links are displaceable
    weak_s: set = set()
    if displace_weak:
        by_src: dict[int, set] = {}
        for s, t in sym:
            by_src.setdefault(s, set()).add(t)
        weak_s = {s for s, ts in by_src.items()
                  if s in content_s and not any((s, t) in inter for t in ts)}
    moved = 0
    out = set(sym)
    for s, t in sorted(union):
        if s not in content_s or t not in reassignable:
            continue
        if s in aligned_s:
            if s not in weak_s:
                continue
            out -= {(s, t2) for t2 in list(by_src.get(s, ()))}     # trade the weak link set away
            weak_s.discard(s)
        out -= {(s2, t) for s2 in holders[t]}
        out.add((s, t))
        aligned_s.add(s)
        reassignable.discard(t)
        moved += 1
    return out, moved


def _longest_contiguous(ts: list[int], inter_ts: set[int]) -> list[int]:
    """Reduce a source token's target positions to its longest CONTIGUOUS run (protection #2).

    eflomal's decode groups every target linked to a source token with no length or contiguity
    constraint, so a source token can hold a scattered handful of positions. Measured against Clear
    gold on span-2 pairs — same span size, same gold entries, contiguity the only variable — a
    SCATTERED span is far less precise than a contiguous one: hin 64.0% vs 82.5% (+18.5pt for
    contiguity), eng 62.3% vs 84.5% (+22.2pt). `export_mwe` already refuses to publish scattered spans
    for exactly this reason; the aligner kept claiming their target positions anyway.

    Ties are broken toward the run holding an INTERSECTION-backed link (both alignment directions
    agreed there) and then toward the longer/earlier run — never arbitrarily."""
    runs: list[list[int]] = []
    for t in ts:
        if runs and t == runs[-1][-1] + 1:
            runs[-1].append(t)
        else:
            runs.append([t])
    return max(runs, key=lambda r: (bool(inter_ts & set(r)), len(r), -r[0]))


class EflomalAligner:
    def __init__(self, anchor: str = "strong", stem: bool = False,
                 content_only: bool = False, content_priority: bool = False,
                 contiguous_only: bool = True, displace_weak: bool = False):
        # source-side key eflomal learns co-occurrence over: "strong" (coarse rollup) or "lexeme"
        # (finer — separates homonyms one Strong's conflates). Decode is positional either way, so the
        # output pairs carry both regardless; only the statistical model's granularity changes.
        # stem: #2 — feed the target's STEM (norm.stem) instead of its surface, so inflected variants of a
        # word pool into one co-occurrence type. Decode stays positional → output surface = the raw token.
        # content_only  — drop non-content tokens from the source line ENTIRELY (they never train,
        #                 never align). The blunt instrument: it also removes the co-occurrence
        #                 evidence eflomal uses, and leaves target function words with no partner.
        # content_priority — keep them in the model, reallocate at decode (see _content_priority).
        self.by_verse: dict[tuple, dict] = {}
        self.anchor = anchor
        self.stem = stem
        self.content_only = content_only
        self.content_priority = content_priority
        # contiguous_only — #2, ON BY DEFAULT since 2026-09-01: keep only the longest contiguous run
        # of a source token's targets, releasing the scattered outliers. Measured token-precision gain
        # vs Clear gold: fra +1.3, hin +2.2, eng +0.9, for a coverage cost of -0.6/-0.5/-0.2; the
        # released tokens were only 23-46% gold-correct against a 68-90% baseline. See
        # docs/pipeline-overview.md "Protection #2". Opt out per run with --eflomal-allow-scattered
        # (the languages where scatter is legitimate are Grambank GB026=1, ~6% — currently UNVALIDATED,
        # so the opt-out is manual, never automatic).
        self.contiguous_only = contiguous_only
        self.displace_weak = displace_weak
        self.n_reassigned = 0
        self.n_scatter_dropped = 0

    def run(self, recs, norm, priors_pairs=None) -> None:
        src_lines, trg_lines, meta = [], [], []
        tgt_form = (lambda w: norm.stem(w)) if self.stem else (lambda w: norm.forms(w)[0])
        for rec in recs:
            src_toks = [t for t in rec.heb
                        if getattr(t, self.anchor) and (t.is_content or not self.content_only)]
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
            if self.content_priority:
                sym, moved = _content_priority(sym, fwd | rev, src_toks, inter,
                                               displace_weak=self.displace_weak)
                self.n_reassigned += moved
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
            ts = sorted(ts)
            if self.contiguous_only and len(ts) > 1:
                kept = _longest_contiguous(ts, {t for t in ts if (s, t) in info["inter"]})
                self.n_scatter_dropped += len(ts) - len(kept)
                ts = kept
            out.append(EMatch(htok.idx, ts, score, "eflomal"))
        return sorted(out, key=lambda m: m.h_idx)
