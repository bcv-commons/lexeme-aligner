"""Track 1 / R1 (internal-docs/aim1-three-track-evaluation-plan.md §1.4): CROSS-EDITION TRANSFER — a
candidate low-precision recall layer, published (if it ever passes its gate) under its own
`method="xfer_edition"` and never blended into the main array/table, same attribution pattern as the
opt-in `residual` layer.

IDEA. For a language with several editions, a source content token that is UNALIGNED in a test edition T
after every existing layer, but aligned with high confidence (base layers eflomal/gloss/gapfill, score >=
0.9) in a reference edition R, may be filled by PROJECTING R's target span onto T through an
edition-to-edition word alignment T<->R. Same language, near-monotonic, so that alignment is far easier
than source->target. Several references that project to the SAME T span agree; the agreement count is the
confidence tier (`prior = "xedition_n<agreeing refs>"`).

WHAT IS IN HERE. Pure, unit-testable functions (`project_span`, `vote`, `transfer`, `to_pair`,
`text_similarity`) plus one eflomal-driving function (`edition_links`, the only part needing the C tool;
its Aligner is injectable so the symmetrization/parsing plumbing is testable offline). Nothing here writes
into pipeline/work/out or publish/ — measurement lives in the caller (see the plan's exploration ledger).

KEYING. Cross-edition alignment identity is the source token's `(strong, k-th occurrence)` key (the same key
pos_score uses), and a verse is only usable when T and R agree on the verse's token keys — a PKF-style range
pooled differently in the two editions gives different anchor blocks, and transferring across that would
silently compare unlike things, so such verses are skipped and counted, never guessed.
"""
from __future__ import annotations

import collections
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Hashable, Iterable

METHOD = "xfer_edition"
HI_CONF = 0.9          # a reference pair only counts if its own score is at least this


# --- projection ---------------------------------------------------------------------------------------------
def project_span(ref_positions: Iterable[int], links_r2t: dict[int, set[int]], taken_t: set[int],
                 require_all: bool = False) -> tuple[int, ...] | None:
    """Map a reference edition's target positions onto the test edition through `links_r2t`
    ({r_idx: {t_idx, ...}}). Returns the projected T span (sorted) or None when it is unusable:
    nothing maps, the projection is not one contiguous run (the aligner's own `contiguous_only` default
    is the standing precedent — a scattered span is measurably weaker), or it collides with a position T
    already holds. `require_all`: every reference position must itself map (a stricter variant — a
    two-word reference span whose second word T dropped would otherwise shrink to one word)."""
    ref_positions = list(ref_positions)
    if not ref_positions:
        return None
    mapped: set[int] = set()
    for r in ref_positions:
        hit = links_r2t.get(r)
        if not hit:
            if require_all:
                return None
            continue
        mapped |= hit
    if not mapped or (mapped & taken_t):
        return None
    ordered = sorted(mapped)
    if ordered[-1] - ordered[0] + 1 != len(ordered):
        return None
    return tuple(ordered)


def vote(projections: list[tuple[int, ...] | None]) -> tuple[tuple[int, ...], int, int] | None:
    """(winning span, n references agreeing on it, n references that produced ANY projection), or None when
    no reference projected. Deterministic: most agreement first, then the lexicographically smaller span."""
    counted = collections.Counter(p for p in projections if p)
    if not counted:
        return None
    span, n = min(counted.items(), key=lambda kv: (-kv[1], kv[0]))
    return span, n, sum(counted.values())


@dataclass
class RefEdition:
    """One reference edition as `transfer` needs it. `spans[ref][key]` are its HIGH-CONFIDENCE base-layer
    positions; `links_r2t[ref]` is {r_idx: {t_idx}} from the T<->R alignment; `usable` is the set of verse
    refs where R's token keys equal T's (same pooling) — everything else is skipped, not guessed."""
    name: str
    spans: dict[int, dict[Hashable, set[int]]]
    links_r2t: dict[int, dict[int, set[int]]]
    usable: set[int] = field(default_factory=set)


@dataclass
class Candidate:
    ref: int
    key: Hashable
    positions: tuple[int, ...]
    n_agree: int
    n_projecting: int
    n_refs_total: int
    sources: tuple[str, ...] = ()

    @property
    def prior(self) -> str:
        return f"xedition_n{self.n_agree}"


def transfer(gap_keys: dict[int, list[Hashable]], t_taken: dict[int, set[int]],
             editions: list[RefEdition], require_all: bool = False, min_agree: int = 1
             ) -> tuple[list[Candidate], dict[str, int]]:
    """Candidates for every (verse, gap key) some reference can project. Within one verse candidates are
    accepted in (-agreement, key order) order and a later one that would overlap an already-accepted
    candidate or a position T holds is dropped, so the layer can never double-claim a target word.
    Second return: skip/coverage counters for the report."""
    stats: collections.Counter = collections.Counter()
    out: list[Candidate] = []
    for ref, keys in gap_keys.items():
        taken = set(t_taken.get(ref, ()))
        pending: list[Candidate] = []
        for key in keys:
            projections, names = [], []
            for ed in editions:
                if ref not in ed.usable:
                    stats["ref_verse_unusable"] += 1
                    continue
                pos = ed.spans.get(ref, {}).get(key)
                if not pos:
                    continue
                stats["ref_pairs_seen"] += 1
                span = project_span(pos, ed.links_r2t.get(ref, {}), taken, require_all)
                projections.append(span)
                names.append(ed.name)
                stats["projected" if span else "projection_rejected"] += 1
            voted = vote(projections)
            if not voted:
                continue
            span, n_agree, n_proj = voted
            if n_agree < min_agree:
                stats["below_min_agree"] += 1
                continue
            pending.append(Candidate(ref, key, span, n_agree, n_proj, len(editions),
                                     tuple(sorted(n for n, p in zip(names, projections) if p == span))))
        pending.sort(key=lambda c: (-c.n_agree, str(c.key)))
        claimed = set(taken)
        for c in pending:
            if claimed & set(c.positions):
                stats["dropped_overlap"] += 1
                continue
            claimed |= set(c.positions)
            out.append(c)
    stats["candidates"] = len(out)
    return out, dict(stats)


def to_pair(cand: Candidate, h_idx: int, lexeme: str, strong: str, lemma: str, surface: str,
            toks: list[str]) -> dict:
    """The gapfill-record pair shape (compact_align/export_lex/pos_score all read it), attributed
    `method="xfer_edition"`. Score stays BELOW HI_CONF at every tier (0.3/0.4/0.5 for 1/2/3+ agreeing
    references) so nothing downstream can mistake it for a high-confidence base decision."""
    return {"h_idx": h_idx, "lexeme": lexeme, "strong": strong, "lemma": lemma, "stem": None,
            "surface": surface, "gloss_en": None, "sense": None,
            "target": " ".join(toks[j] for j in cand.positions if j < len(toks)),
            "t_idx": list(cand.positions), "score": round(0.2 + 0.1 * min(cand.n_agree, 3), 2),
            "method": METHOD, "content": True, "prior": cand.prior}


# --- near-duplicate screen ----------------------------------------------------------------------------------
def text_similarity(a: dict[int, list[str]], b: dict[int, list[str]]) -> dict[str, float]:
    """Per-verse comparison of two editions' token lists over the verses both have: share of verses whose
    token sequence is IDENTICAL, and mean Jaccard of the verse token sets. A reference that is a
    near-duplicate of T makes transfer trivially easy and is not evidence about independent renderings."""
    common = [r for r in a if r in b and a[r] and b[r]]
    if not common:
        return {"verses": 0, "identical_share": 0.0, "mean_jaccard": 0.0}
    ident = sum(1 for r in common if a[r] == b[r])
    jac = []
    for r in common:
        sa, sb = set(a[r]), set(b[r])
        jac.append(len(sa & sb) / len(sa | sb))
    return {"verses": len(common), "identical_share": ident / len(common),
            "mean_jaccard": sum(jac) / len(jac)}


# --- edition <-> edition word alignment ---------------------------------------------------------------------
def _parse(line: str) -> set[tuple[int, int]]:
    out = set()
    for pair in line.split():
        s, t = pair.split("-")
        out.add((int(s), int(t)))
    return out


def edition_links(t_toks: dict[int, list[str]], r_toks: dict[int, list[str]],
                  aligner_factory: Callable | None = None, tmp_dir: Path | None = None
                  ) -> dict[int, dict[int, set[int]]]:
    """ONE eflomal run over every verse both editions have (source=T, target=R), symmetrized with the same
    grow-diag-final-and the main chain uses; returned as {ref: {r_idx: {t_idx, ...}}} — the direction
    `project_span` needs. `t_toks`/`r_toks` are {encoded verse ref: token list} as `build_corpus` yields
    (lowercased here). `aligner_factory` defaults to eflomal's Aligner; injectable for offline tests.
    Callers running next to other eflomal jobs should set OMP_NUM_THREADS themselves (eflomal is OpenMP)."""
    from lexeme_aligner.eflomal_align import _grow_diag_final_and
    if aligner_factory is None:
        from eflomal import Aligner as aligner_factory  # noqa: N813
    refs = [r for r in t_toks if r in r_toks and t_toks[r] and r_toks[r]]
    if not refs:
        return {}
    with tempfile.NamedTemporaryFile("w+", suffix=".src", dir=tmp_dir) as sf, \
         tempfile.NamedTemporaryFile("w+", suffix=".trg", dir=tmp_dir) as tf, \
         tempfile.NamedTemporaryFile("r", suffix=".fwd", dir=tmp_dir) as ff, \
         tempfile.NamedTemporaryFile("r", suffix=".rev", dir=tmp_dir) as rf:
        sf.write("\n".join(" ".join(w.lower() for w in t_toks[r]) for r in refs) + "\n"); sf.flush(); sf.seek(0)
        tf.write("\n".join(" ".join(w.lower() for w in r_toks[r]) for r in refs) + "\n"); tf.flush(); tf.seek(0)
        aligner_factory().align(sf, tf, links_filename_fwd=ff.name, links_filename_rev=rf.name, quiet=True)
        fwds = [_parse(line) for line in ff]
        rf.seek(0)
        revs = [_parse(line) for line in rf]
    out: dict[int, dict[int, set[int]]] = {}
    for ref, fwd, rev in zip(refs, fwds, revs):
        sym, _inter = _grow_diag_final_and(fwd, rev, len(t_toks[ref]), len(r_toks[ref]))
        m: dict[int, set[int]] = collections.defaultdict(set)
        for t, r in sym:
            m[r].add(t)
        out[ref] = dict(m)
    return out
