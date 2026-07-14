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
fallback when no USJ is available. Exact `vul`/`orgw`/`rso` tables don't exist (CDN .vrs are structure-only),
so we map to the closest exact table we hold — which is Psalm-exact in every case we ship:
  org, orgw            → hebrew  (Hebrew superscription; hebrew.tsv reproduces their Psalter exactly)
  lxx, vul, rso, catm  → septuagint (LXX Psalm renumbering; lxx.tsv, ≤6 single-verse Psalm residuals)
  eng                  → protestant (identity)
"""
from __future__ import annotations

import collections
import glob
import json
import os
from pathlib import Path

_VERSIF = Path("data/versification.json")            # manual override / fallback
_REG_DIR = Path("resources/versification/schemes")   # our exact diff tables (hebrew.tsv, lxx.tsv)
_VRS_DIR = Path("resources/versification/vrs")        # pinned CDN structure schemes (*.vrs)

_SCHEME_FILE = {"septuagint": "lxx", "lxx": "lxx", "hebrew": "hebrew"}  # aligner label → tsv basename
_IDENTITY = {"protestant", "kjv", ""}

# CDN scheme (from a .vrs fingerprint) → the aligner label / diff table to use. Multiple CDN schemes share
# a table: we only hold exact tables for hebrew + lxx, and both are Psalm-exact for their family.
_CDN_TABLE = {
    "eng": "protestant",
    "org": "hebrew", "orgw": "hebrew",
    "lxx": "septuagint", "vul": "septuagint", "rso": "septuagint", "catm": "septuagint",
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
