"""Versification remap — bring a target Bible's verses onto the SOURCE (spine) numbering so verse-by-verse
matching lines up.

A target not numbered like the spine (Russian Synodal = LXX Psalm numbering; French/Swedish = Hebrew
superscription) has its verses shifted against the spine, so alignment silently breaks. Every scheme maps
to the KJV standard and the spine behaves as KJV, so for a spine ref R we fetch the target verse via
`from_standard`: the scheme's `standard_ref → source_ref` reverse map. protestant/unlisted → identity.

SCHEME DETECTION (2026-07-13): the scheme is **auto-detected** by fingerprinting the ingested target's
verse-count-per-chapter structure against the 7 CDN `/_vrs/` schemes (pinned in resources/versification/vrs/),
then mapping the best-matching CDN scheme to the best *available* aligner diff table. This replaces trusting
a label — the CDN's own labels can mismatch what a source actually delivers (helloAO rus_syn is labelled
`vul` but its structure matches `rso`/`lxx`). `data/versification.json` remains only as a manual override /
fallback when no USJ is available.

EXACT TABLES (updated 2026-09-26 from real bcv-commons/bibles data, `config/bibles_vrs/` — see
config/PROVENANCE.txt): fetched the real per-verse crosswalk for all 6 non-eng CDN schemes
(`cdn.bibel.wiki/_vrs/map/<scheme>-to-eng.json`) and diffed them directly against each other and against
our own existing `hebrew.tsv`/`lxx.tsv` (both already TVTMS-sourced, NOT the crude approximation this
docstring previously implied) before changing anything:
  - `catm` and `org` are BYTE-IDENTICAL (1965/1965 rows shared) — `org`'s data is in turn a full subset of
    our own `hebrew.tsv` (1965/1965 shared, 0 org-only rows). This means the PREVIOUS `catm -> septuagint`
    mapping below was a REAL BUG, found and fixed here: `catm` belongs with `hebrew`, not `septuagint`.
  - `orgw` (1861 rows) is a full subset of `org`/`catm` (1861/1861 shared) — also `hebrew`-family,
    unchanged from before.
  - `vul` (2845 rows) and `rso` (4132 rows) are NEITHER identical to each other (2647 shared) NOR subsets
    of our own `lxx.tsv` — genuinely distinct schemes, now given their own exact tables (`vul.tsv`,
    `rso.tsv`, built directly from the real bcv-commons/bibles data, same TSV shape as hebrew/lxx).
  - `lxx` itself: RESOLVED 2026-09-26, root cause confirmed by bcv-query (the actual maintainer of this
    table's build pipeline — "shoresh versification.build", the same pipeline that produces hebrew.tsv):
    our 5386-row lxx.tsv was a pre-2026-07-14 snapshot; bcv-query's commit 5cb5852 ("de-garble Greek-
    addition books") deliberately deleted 854 rows for Esther/Daniel Greek-addition content (Additions
    A-F, Song of the Three, Susanna, Bel) after finding TVTMS carries multiple INCOMPATIBLE layouts for
    those books that a naive filter had been conflating into garbled mappings. Confirmed real and NOT a
    narrow Apocrypha-only concern: our own (pre-fix) table's DAN rows landed in chapters 3/4/6 — inside
    canonical Daniel, not just non-canonical appendix material (ch.3 is where the Song of the Three
    splices into the middle of the canonical chapter, between vv.23-24). bcv-query exported their
    current, already-fixed 4532-row table directly (dated 2026-07-14, same source path as hebrew.tsv);
    verified it is an EXACT STRICT SUBSET of our old table (4532/4532 shared, 0 new/different rows) —
    a pure, safe removal, not a reconciliation with any residual ambiguity. Swapped in.
  org, orgw       → hebrew     (Hebrew superscription; hebrew.tsv, verified superset of real org data)
  catm            → hebrew     (bug fix, 2026-09-26 — was wrongly `septuagint`; catm == org exactly)
  lxx             → lxx        (RE-PULLED 2026-09-26 from bcv-query, 5386->4532 rows, Esther/Daniel
                    Greek-addition rows removed — see above)
  vul             → vul        (new exact table, 2845 rows, from real bcv-commons/bibles data)
  rso             → rso        (new exact table, 4132 rows, from real bcv-commons/bibles data)
  eng             → protestant (identity)
"""
from __future__ import annotations

import collections
import glob
import json
import os
from pathlib import Path

_VERSIF = Path("config/versification.json")            # manual override / fallback
_REG_DIR = Path("pipeline/vendor/versification/schemes")   # our exact diff tables (hebrew.tsv, lxx.tsv)
_VRS_DIR = Path("pipeline/vendor/versification/vrs")        # pinned CDN structure schemes (*.vrs)

_SCHEME_FILE = {"septuagint": "lxx", "lxx": "lxx", "hebrew": "hebrew",
               "vul": "vul", "rso": "rso"}  # aligner label → tsv basename
_IDENTITY = {"protestant", "kjv", ""}

# CDN scheme (from a .vrs fingerprint) → the aligner label / diff table to use. `org`/`orgw`/`catm` share
# `hebrew.tsv` (verified 2026-09-26: catm==org exactly, orgw and org are both real subsets of hebrew.tsv's
# own, larger TVTMS-sourced coverage). `vul`/`rso` now have their own exact tables (neither is a subset of
# our `lxx.tsv` or of each other). `lxx` keeps its own existing table.
_CDN_TABLE = {
    "eng": "protestant",
    "org": "hebrew", "orgw": "hebrew", "catm": "hebrew",
    "lxx": "lxx", "vul": "vul", "rso": "rso",
}
# Protestant-canon OT books — the only place schemes diverge (NT is identical across all schemes).
_PROT_OT = frozenset(
    "GEN EXO LEV NUM DEU JOS JDG RUT 1SA 2SA 1KI 2KI 1CH 2CH EZR NEH EST JOB PSA PRO ECC SNG ISA JER LAM "
    "EZK DAN HOS JOL AMO OBA JON MIC NAM HAB ZEP HAG ZEC MAL".split())

_DETECT_CACHE: dict[str, tuple] = {}                  # usj_dir → (aligner_label, cdn_scheme, scores)


def _parse(ref: str):
    try:
        book, cv = ref.split(" ")
        ch, v = cv.split(":")
        return (book, int(ch), int(v))
    except ValueError:
        return None


# ── structure fingerprinting (part a) ───────────────────────────────────────────────────

def _load_vrs(name: str) -> dict:
    """Parse a CDN .vrs (last-verse-per-chapter): {book: {chapter: last_verse}}."""
    out: dict[str, dict] = {}
    fp = _VRS_DIR / f"{name}.vrs"
    if not fp.exists():
        return out
    for line in fp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        chaps = {}
        for tok in parts[1:]:
            if ":" in tok:
                c, v = tok.split(":")
                try:
                    chaps[int(c)] = int(v)
                except ValueError:
                    pass
        if chaps:
            out[parts[0]] = chaps
    return out


def _usj_structure(usj_dir: str) -> dict:
    """Last-verse-per-chapter of an ingested USJ dir: {book: {chapter: last_verse}}."""
    R: dict[str, dict] = collections.defaultdict(dict)
    for fp in glob.glob(os.path.join(usj_dir, "*.json")):
        try:
            doc = json.load(open(fp, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        cur = [None, 0]

        def walk(node):
            for it in (node if isinstance(node, list) else node.get("content", [])):
                if not isinstance(it, dict):
                    continue
                if it.get("type") == "book":
                    cur[0] = it.get("code")
                if it.get("marker") == "c":
                    try:
                        cur[1] = int(it.get("number"))
                    except (TypeError, ValueError):
                        pass
                if it.get("marker") == "v":
                    try:
                        vn = int(str(it.get("number")).split("-")[0])
                    except (TypeError, ValueError):
                        vn = 0
                    if cur[0]:
                        R[cur[0]][cur[1]] = max(R[cur[0]].get(cur[1], 0), vn)
                if "content" in it:
                    walk(it["content"])
        walk(doc.get("content", doc))
    return R


def detect_scheme(usj_dir: str):
    """Fingerprint an ingested USJ's structure vs the pinned CDN schemes → (aligner_label, cdn_scheme, scores).
    Returns (None, None, {}) if the USJ is unreadable/empty. Scores = per-CDN-scheme chapter-match fraction
    over the protestant OT (the region where schemes differ)."""
    if usj_dir in _DETECT_CACHE:
        return _DETECT_CACHE[usj_dir]
    struct = _usj_structure(usj_dir)
    if not struct:
        return (None, None, {})
    scores: dict[str, float] = {}
    for name in _CDN_TABLE:
        sc = _load_vrs(name)
        if not sc:
            continue
        match = total = 0
        for bk in _PROT_OT:
            for ch, lv in struct.get(bk, {}).items():
                if ch in sc.get(bk, {}):
                    total += 1
                    if sc[bk][ch] == lv:
                        match += 1
        if total:
            scores[name] = match / total
    if not scores:
        return (None, None, {})
    best_cdn = max(scores, key=scores.get)
    result = (_CDN_TABLE[best_cdn], best_cdn, scores)
    _DETECT_CACHE[usj_dir] = result
    return result


# ── reverse-map loading + remapper ──────────────────────────────────────────────────────

def scheme_of(iso: str, usj_dir: str | None = None) -> str:
    """Resolve the aligner scheme label. Prefer auto-detection from the ingested USJ structure; fall back to
    the manual data/versification.json (then protestant) when no USJ is available."""
    if usj_dir and os.path.isdir(usj_dir):
        label, _, _ = detect_scheme(usj_dir)
        if label:
            return label
    if not _VERSIF.exists():
        return "protestant"
    cfg = {k: v for k, v in json.loads(_VERSIF.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    return cfg.get(iso, "protestant")


def load_reverse(scheme: str) -> dict:
    """{(book,ch,v)_KJV: (book,ch,v)_scheme} — from_standard. Empty (identity) for protestant/kjv/unknown."""
    if scheme in _IDENTITY:
        return {}
    fname = _SCHEME_FILE.get(scheme)
    if not fname:
        return {}
    fp = _REG_DIR / f"{fname}.tsv"
    if not fp.exists():
        return {}
    rev: dict[tuple, tuple] = {}
    with fp.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or line.startswith("source_ref"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            src, std = _parse(parts[0]), _parse(parts[1])
            if src and std:
                rev[std] = src                                # from_standard[KJV ref] = scheme's ref
    return rev


def remapper_for_scheme(scheme: str):
    """→ f(book, ch, v): spine (KJV) ref → the given scheme's ref; None if identity. Used by the eflomal
    validator to force a specific candidate scheme."""
    rev = load_reverse(scheme)
    if not rev:
        return None

    def f(book: str, ch: int, v: int):
        return rev.get((book, ch, v), (book, ch, v))
    return f


def remapper(iso: str, usj_dir: str | None = None):
    """→ f(book, ch, v) mapping a spine (KJV) ref to the target's scheme ref; None if identity (protestant).
    Auto-detects the scheme from the ingested USJ when usj_dir is given."""
    return remapper_for_scheme(scheme_of(iso, usj_dir))
