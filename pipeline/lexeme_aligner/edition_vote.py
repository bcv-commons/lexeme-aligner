"""Combine one fact derived SEPARATELY from each edition of a language into a language-level fact, without
privileging any edition.

Used by `derive_typology` (word-order / marker slots) and `article_bound`. Each edition is analysed on its own,
from its own alignments; this module then decides what the LANGUAGE-level record says and records how well the
editions agreed, so disagreement is visible instead of hidden behind whichever edition happened to be analysed.

RULE. An edition "votes" when its slot carries a real value (`direction` before/after, `present`/`bound` true/
false); an edition whose slot is null ("mixed", too little data, testament conflict) abstains but is still listed.
Votes are weighted by the slot's own evidence count `n`. The winning value must hold at least `MIN_SHARE` of the
voting weight; otherwise the language record is an explicit abstention (`<field>: null, reason: "edition_conflict"`)
— the same never-silently-pick stance `derive_typology._combine_testaments` takes across testaments. When the
editions agree (or the winner clears the bar), the language record is the strongest winning edition's own slot with
two additions: `editions` ({tag: its vote, n, rate}) and `agreement` ({voting, agree, weight_share}). A language with
one edition gets `agreement` {voting: 1, agree: 1, weight_share: 1.0}: a thin basis, recorded as such.
"""
from __future__ import annotations

MIN_SHARE = 0.60
_DETAIL_KEYS = ("rate", "rate_after", "reason", "testament", "marker", "edge")


def _detail(slot: dict, field: str, weight_key: str = "n") -> dict:
    out = {field: slot.get(field), "n": slot.get(weight_key, 0)}
    out.update({k: slot[k] for k in _DETAIL_KEYS if k in slot})
    return out


def vote_field(slot: dict) -> str | None:
    """Which key carries this slot's value: `direction`, `present` or `bound`."""
    for f in ("direction", "present", "bound"):
        if f in slot:
            return f
    return None


def combine_editions(slots: dict[str, dict | None], field: str | None = None, weight_key: str = "n",
                     min_share: float = MIN_SHARE) -> dict | None:
    """`slots` = {edition tag: that edition's slot dict, or None when it had no fact}. Returns the language-level
    slot, or None when no edition produced one. See the module docstring for the rule."""
    present = {tag: s for tag, s in slots.items() if s}
    if not present:
        return None
    field = field or next((vote_field(s) for s in present.values() if vote_field(s)), "direction")
    weight = lambda s: float(s.get(weight_key) or 0) or 1.0          # noqa: E731 — a slot with no n still counts once
    detail = {tag: _detail(s, field, weight_key) for tag, s in sorted(present.items())}
    voters = {tag: s for tag, s in present.items() if s.get(field) is not None}
    if not voters:                                                   # nobody took a position: pass the strongest through
        best = dict(max(present.values(), key=weight))
        best["editions"] = detail
        best["agreement"] = {"voting": 0, "agree": 0, "weight_share": None}
        return best
    totals: dict = {}
    for s in voters.values():
        totals[s[field]] = totals.get(s[field], 0.0) + weight(s)
    winner = max(totals, key=lambda v: (totals[v], str(v)))          # deterministic tie-break
    total_w = sum(totals.values())
    share = totals[winner] / total_w
    agreeing = {tag: s for tag, s in voters.items() if s[field] == winner}
    agreement = {"voting": len(voters), "agree": len(agreeing), "weight_share": round(share, 4)}
    if share < min_share or (len(totals) > 1 and totals[winner] == max(w for v, w in totals.items() if v != winner)):
        return {field: None, "source": "derived", "reason": "edition_conflict", "editions": detail,
                "agreement": agreement}
    out = dict(max(agreeing.values(), key=weight))
    out["editions"] = detail
    out["agreement"] = agreement
    return out
