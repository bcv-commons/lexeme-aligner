"""MACULA-only counterparts of the BHSA-derived syntax statistics (internal-docs/macula-only-migration-plan.md, P2/P3).

The D0 typology slots and the constituent-order profile used BHSA `phrase_id` / `function` / `rela`. The MACULA treebank columns give the same
questions a cleaner unit:

  * `head_idx` is the idx of the token's CLAUSE VERB (NULL on the verb itself; verified 100% of 266,393 rows point at a `phrase_role == "v"` token),
    so a clause is "a verb plus the tokens that point at it" — no subordinator window is needed to avoid crossing into the next clause.
  * `phrase_role` (v s o o2 p pp adv) is the token's own or nearest enclosing phrase role (Hebrew only).
  * `construct_role` (regens | rectum | regens+rectum) marks construct chains; when the spine predates it, `construct_group` + `state` is the fallback.

A CONSTITUENT is a maximal run of consecutive tokens (by idx) with the same (head_idx, phrase_role); every `v` token is its own constituent. Runs are
split where another role intervenes (a subject separated from its coordinated second half by a prepositional phrase is two runs, not one averaged
position). Only aligned CONTENT tokens (`strong`, `is_content`, in the anchors) place a constituent: source order = its first such token, target
position = the mean of their anchors — the same convention the BHSA code used for phrases.

All functions are pure over `HebToken` lists + the `{h_idx: target_pos}` anchors of a verse, so they are unit-tested without any spine or alignment.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass

CLAUSE_ROLES = ("s", "o", "o2", "p", "pp", "adv")      # every non-verb phrase_role value
_REGENS = {"regens", "regens+rectum"}
_RECTUM = {"rectum", "regens+rectum"}


@dataclass
class Unit:
    src: int            # source order: first aligned content token's idx
    label: str          # phrase_role ("v", "s", "o", ...)
    ident: int | None   # the clause verb's idx (the verb's own idx for a "v" unit; head_idx otherwise)
    tpos: float         # mean target position of the aligned content members


def _placed(t, anch: dict) -> bool:
    return bool(t.strong and t.is_content and t.idx in anch)


def clause_units(heb: list, anch: dict) -> list[Unit]:
    """Constituents of one verse in source order (see module docstring). Tokens without a phrase_role (clause-initial conjunctions) end a run."""
    runs: list[tuple[tuple, str, list]] = []
    cur_key = None
    for t in sorted(heb, key=lambda x: x.idx):
        role = getattr(t, "phrase_role", None)
        if not role:
            cur_key = None
            continue
        key = (t.idx, "v") if role == "v" else (t.head_idx, role)
        if key != cur_key:
            runs.append((key, role, []))
            cur_key = key
        runs[-1][2].append(t)
    out = []
    for (ident, _), role, members in runs:
        placed = [t for t in members if _placed(t, anch)]
        if placed:
            out.append(Unit(min(t.idx for t in placed), role, ident, sum(anch[t.idx] for t in placed) / len(placed)))
    return sorted(out, key=lambda u: u.src)


def clause_role_stat(recs, anchors: dict, target_role: str) -> dict:
    """Same return shape as derive_typology._hebrew_clause_role_stat: for every clause verb, is each `target_role` constituent of THAT clause
    (head_idx == the verb) before or after the verb in the target? `rate_after` = n_after / (n_before + n_after); fused = same mean position."""
    from lexeme_aligner.refs import encode          # local import: keeps this module importable without the package config
    n_before = n_after = n_fused = n_opp = 0
    for r in recs:
        anch = anchors.get(encode(r.book, r.ch, r.v))
        if not anch:
            continue
        units = clause_units(r.heb, anch)
        by_clause: dict = collections.defaultdict(list)
        for u in units:
            if u.label == target_role:
                by_clause[u.ident].append(u)
        for v in (u for u in units if u.label == "v"):
            for m in by_clause.get(v.ident, ()):
                n_opp += 1
                if m.tpos == v.tpos:
                    n_fused += 1
                elif m.tpos < v.tpos:
                    n_before += 1
                else:
                    n_after += 1
    n_res = n_before + n_after
    return {"n_opportunities": n_opp, "n_fused": n_fused, "n_before": n_before, "n_after": n_after, "n_resolved": n_res,
            "rate_after": (n_after / n_res) if n_res else None}


def construct_role_of(t) -> str | None:
    """regens / rectum / regens+rectum from `construct_role`; falls back to construct_group + state (a construct-state member is a regens, the
    other members of the group are rectum) on a spine that has no `construct_role`."""
    cr = getattr(t, "construct_role", None)
    if cr:
        return cr
    if getattr(t, "construct_group", None):
        return "regens" if getattr(t, "state", None) == "construct" else "rectum"
    return None


def is_rectum(t) -> bool:
    """The MACULA replacement for BHSA `rela == "rec"` in per-token gates (fertility possessor): a CONTENT token that is the rectum (or a middle link)
    of a construct chain. Content-only on purpose: `construct_role` also marks possessive-suffix tokens and articles inside the rectum phrase (that is
    why its precision against BHSA `rec` is .72 although recall is .84), and a per-anchor fertility count must not credit those function tokens."""
    return bool(t.is_content and construct_role_of(t) in _RECTUM)


def construct_after_stat(recs, anchors: dict, min_n: int = 50) -> dict:
    """MACULA version of gapfill.compute_order_stats' rec_after_rate: across every construct chain, for each adjacent (regens, rectum) link where
    BOTH are aligned content tokens, does the rectum's target come after the regens'? None below `min_n` observations."""
    from lexeme_aligner.refs import encode
    after = total = 0
    for r in recs:
        anch = anchors.get(encode(r.book, r.ch, r.v))
        if not anch:
            continue
        groups: dict = collections.defaultdict(list)
        for t in sorted(r.heb, key=lambda x: x.idx):
            if getattr(t, "construct_group", None) and _placed(t, anch):
                groups[t.construct_group].append(t)
        for members in groups.values():
            for a, b in zip(members, members[1:]):
                if construct_role_of(a) in _REGENS and construct_role_of(b) in _RECTUM:
                    total += 1
                    after += anch[b.idx] > anch[a.idx]
    return {"rec_after_rate": (after / total) if total >= min_n else None, "rec_after_n": total}


def profile_pairs(recs, anchors: dict) -> tuple[dict, dict, int]:
    """Constituent-order profile ingredients (macula counterpart of constituent_order.profile's loops): adjacent constituent pairs in source order
    -> {(label_a, label_b): [kept, total]}, positional drift per label -> {label: [drifts]}, number of verses with >= 2 constituents."""
    from lexeme_aligner.refs import encode
    pair_keep: dict = collections.defaultdict(lambda: [0, 0])
    drift: dict = collections.defaultdict(list)
    n_verses = 0
    for r in recs:
        anch = anchors.get(encode(r.book, r.ch, r.v))
        if not anch or not r.toks:
            continue
        units = clause_units(r.heb, anch)
        if len(units) < 2:
            continue
        n_verses += 1
        n_src, n_trg = max(len(r.heb), 1), max(len(r.toks), 1)
        for a, b in zip(units, units[1:]):
            cell = pair_keep[(a.label, b.label)]
            cell[1] += 1
            cell[0] += a.tpos < b.tpos
        for u in units:
            drift[u.label].append(u.tpos / n_trg - u.src / n_src)
    return pair_keep, drift, n_verses
