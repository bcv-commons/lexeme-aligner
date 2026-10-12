"""Versification remap — bring a target Bible's verses onto the SOURCE (spine) numbering so verse-by-verse
matching lines up.

The spine's OT is HEBREW-numbered (WLC; `_SPINE_OT_TABLE`), so for a spine ref R we go spine → KJV standard
(`hebrew.tsv`) → the target scheme (that scheme's `standard_ref → source_ref` reverse map; identity for English-numbered
targets). A Hebrew-numbered target needs no remap. A verse the target leaves unnumbered (a Psalm superscription in an
English-numbered text) maps to verse 0 and gets no target text. The crosswalk tables are bcv-commons/bibles' (and, for the
LXX, bcv-query's), vendored and sha-pinned in config/PROVENANCE.txt; TVTMS (STEPBible Data, CC BY 4.0) is their source.

SCHEME DETECTION is an AUDIT of the label bibles owns (internal-docs/repo-responsibilities.md): the scheme is found by
fingerprinting the ingested target's verse-count-per-chapter structure against the pinned CDN `/_vrs/` schemes
(`pipeline/vendor/versification/vrs/`) and mapping the best match to a diff table:
  org             → hebrew   (Hebrew numbering = the spine: identity)
  orgw, catm      → orgw, catm (bibles' own tables since 2026-10-12: orgw numbers EXO 22, 1SA 21, 2CH 2, HOS 14 and
                    ~600 other OT verses as English while keeping Hebrew Psalm titles; catm differs from org in 1CH 12, NEH 7)
  lxx             → lxx      (bcv-query's table)
  vul             → vul      (bibles)
  rso             → rso      (bibles; Russian Synodal and kin)
  eng             → protestant (identity)
Without an ingested text there is nothing to detect, so the scheme is `protestant` (identity); every caller in the chain
passes the ingested USJ directory.

NEW TESTAMENT (2026-10-12). The schemes above differ only in the OT; NT numbering differs per EDITION, in a handful of
chapters. bcv-commons/bibles' `nt-variants.json` lists each variant (e.g. `2CO13-13`: 2 Corinthians 13 has 13 verses, 13:12
holds English 13:12-13) with its map to eng.vrs and a detect rule ("2CO 13 has 13 verses"). The spine (Nestle 1904) follows
their `greek` profile (2CO13-13, ACT19-40), found by applying the same detect rules to the spine. An NT spine verse goes
spine -> eng (the spine's variants) -> the edition (the edition's variants, inverted; identity where none applies; the
scheme's own NT rows only when the edition has no variant). An edition's variants are detected from its ingested text;
REV12-17 and REV12-17esv share one detect rule, and bibles' index (`nt` entry) decides between them, else REV12-17. Where one
source verse covers two target verses (spine 2CO 13:12 = English 13:12-13) the source verse is paired with the FIRST.
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

_REG_DIR = Path("pipeline/vendor/versification/schemes")   # our exact diff tables (hebrew.tsv, lxx.tsv)
_VRS_DIR = Path("pipeline/vendor/versification/vrs")        # pinned CDN structure schemes (*.vrs)

_SCHEME_FILE = {"septuagint": "lxx", "lxx": "lxx", "hebrew": "hebrew",
               "vul": "vul", "rso": "rso", "orgw": "orgw", "catm": "catm"}  # aligner label → tsv basename
_IDENTITY = {"protestant", "kjv", ""}

# CDN scheme (from a .vrs fingerprint) → the aligner label / diff table to use. `org`/`orgw`/`catm` share
# `hebrew.tsv` (verified 2026-09-26: catm==org exactly, orgw and org are both real subsets of hebrew.tsv's
# own, larger TVTMS-sourced coverage). `vul`/`rso` now have their own exact tables (neither is a subset of
# our `lxx.tsv` or of each other). `lxx` keeps its own existing table.
_CDN_TABLE = {
    "eng": "protestant",
    "org": "hebrew", "orgw": "orgw", "catm": "catm",
    "lxx": "lxx", "vul": "vul", "rso": "rso",
}
# Protestant-canon OT books — the only place schemes diverge (NT is identical across all schemes).
_PROT_OT = frozenset(
    "GEN EXO LEV NUM DEU JOS JDG RUT 1SA 2SA 1KI 2KI 1CH 2CH EZR NEH EST JOB PSA PRO ECC SNG ISA JER LAM "
    "EZK DAN HOS JOL AMO OBA JON MIC NAM HAB ZEP HAG ZEC MAL".split())

_DETECT_CACHE: dict[str, tuple] = {}                  # usj_dir → (aligner_label, cdn_scheme, scores)


def _parse(ref: str):
    """'PSA 3:2' -> ('PSA', 3, 2). A superscription ('PSA 3:title', a verse the scheme leaves unnumbered) is verse 0."""
    try:
        book, cv = ref.split(" ")
        ch, v = cv.split(":")
        return (book, int(ch), 0 if v == "title" else int(v))
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



# ── New Testament variants (bcv-commons/bibles nt-variants.json) ────────────────────────────────

_NT_VARIANTS_FILE = Path("pipeline/vendor/versification/nt-variants.json")
_BIBLES_INDEX = Path("config/bibles_vrs/dbt_vrs_index.json")
_PINS_DIR = Path("config/pins")
_NT_BOOKS = frozenset(
    "MAT MRK LUK JHN ACT ROM 1CO 2CO GAL EPH PHP COL 1TH 2TH 1TI 2TI TIT PHM HEB JAS 1PE 2PE 1JN 2JN 3JN JUD REV".split())
_DETECT_RE = re.compile(r"^(\w{3}) (\d+) has (\d+) verses$")
_REV_PAIR = ("REV12-17", "REV12-17esv")          # same detect rule; bibles' index decides, else the first
_NT_CACHE: dict[str, tuple] = {}


@lru_cache(maxsize=None)
def nt_variant_table() -> dict:
    """{variant name: {"chapter", "detect", "kind", "map": [{"s": ref, "t": [ref, ...]}]}} — edition verse s -> eng.vrs verses t."""
    if not _NT_VARIANTS_FILE.exists():
        return {}
    return json.loads(_NT_VARIANTS_FILE.read_text(encoding="utf-8")).get("variants", {})


def detect_nt_variants(last_verses: dict) -> tuple[str, ...]:
    """The variants whose detect rule ("<BOOK> <C> has <N> verses") matches a {book: {chapter: last verse}} structure, sorted.
    Both REV12-17 forms can match; the caller resolves that pair."""
    found = []
    for name, var in sorted(nt_variant_table().items()):
        m = _DETECT_RE.match(var.get("detect", ""))
        if m and last_verses.get(m.group(1), {}).get(int(m.group(2))) == int(m.group(3)):
            found.append(name)
    return tuple(found)


def _usj_last_verses(usj_dir: str) -> dict:
    """{book: {chapter: last verse}} of an ingested USJ dir, NT books only, a bridge counting by its END ("12-13" -> 13)."""
    out: dict[str, dict] = collections.defaultdict(dict)
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
                if it.get("marker") == "v" and cur[0] in _NT_BOOKS:
                    m = re.match(r"^(\d+)(?:\D+(\d+))?", str(it.get("number") or ""))
                    if m:
                        vn = int(m.group(2) or m.group(1))
                        out[cur[0]][cur[1]] = max(out[cur[0]].get(cur[1], 0), vn)
                if "content" in it:
                    walk(it["content"])
        walk(doc.get("content", doc))
    return out


def _bibles_nt_entry(tag: str) -> dict | None:
    """bibles' dbt/_vrs/index.json `nt` entry for an edition (keyed helloao:<id>, pkf:<id>, <iso>/<bible_id>), via its pin."""
    pin_fp = _PINS_DIR / f"{tag}.json"
    if not (pin_fp.exists() and _BIBLES_INDEX.exists()):
        return None
    pin = json.loads(pin_fp.read_text(encoding="utf-8"))
    nt = _bibles_index().get("nt", {})
    prov = pin.get("provider", "")
    if "helloao" in prov:
        return nt.get(f"helloao:{pin.get('version_id')}")
    if prov == "cdn.bibel.wiki":
        return nt.get(f"pkf:{pin.get('version_id')}")
    if pin.get("bible_id"):
        suffix = "/" + pin["bible_id"]
        return next((v for k, v in nt.items() if k.endswith(suffix)), None)
    return None


@lru_cache(maxsize=None)
def _bibles_index() -> dict:
    return json.loads(_BIBLES_INDEX.read_text(encoding="utf-8")) if _BIBLES_INDEX.exists() else {}


def _resolve_rev(found: tuple, hint: dict | None) -> tuple[str, ...]:
    if all(n in found for n in _REV_PAIR):
        hinted = [n for n in _REV_PAIR if n in ((hint or {}).get("variants") or [])]
        drop = _REV_PAIR[1] if not hinted or hinted[0] == _REV_PAIR[0] else _REV_PAIR[0]
        found = tuple(n for n in found if n != drop)
    return found


def edition_nt_variants(tag: str, usj_dir: str | None) -> tuple[str, ...] | None:
    """The edition's NT numbering variants detected from its ingested text; None when it has no NT text (nothing to map)."""
    if not usj_dir or not os.path.isdir(usj_dir):
        return None
    if usj_dir not in _NT_CACHE:
        last = _usj_last_verses(usj_dir)
        _NT_CACHE[usj_dir] = (_resolve_rev(detect_nt_variants(last), _bibles_nt_entry(tag)) if last else None,)
    return _NT_CACHE[usj_dir][0]


@lru_cache(maxsize=None)
def spine_nt_variants(spine_db: str | None = None) -> tuple[str, ...]:
    """The spine's own NT variants (the same detect rules on its verse structure). Nestle 1904 = bibles' `greek` profile."""
    from lexeme_aligner import config
    con = sqlite3.connect(f"file:{spine_db or config.SPINE_DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT book, chapter, MAX(verse) FROM spine_words GROUP BY book, chapter").fetchall()
    finally:
        con.close()
    last: dict[str, dict] = collections.defaultdict(dict)
    for b, c, v in rows:
        if b in _NT_BOOKS:
            last[b][int(c)] = int(v)
    return _resolve_rev(detect_nt_variants(last), None)


def _nt_to_eng(variants) -> dict:
    """{edition verse: first eng verse} over the variants' rows."""
    out = {}
    for name in variants:
        for row in nt_variant_table().get(name, {}).get("map", []):
            s, ts = _parse(row["s"]), [_parse(t) for t in row["t"]]
            if s and ts and ts[0]:
                out[s] = ts[0]
    return out


def _eng_to_nt(variants) -> dict:
    """{eng verse: edition verse} — the variants' rows inverted (an eng verse claimed twice keeps the first claim)."""
    out = {}
    for name in variants:
        for row in nt_variant_table().get(name, {}).get("map", []):
            s = _parse(row["s"])
            for t in row["t"]:
                t = _parse(t)
                if s and t and t not in out:
                    out[t] = s
    return out

# ── reverse-map loading + remapper ──────────────────────────────────────────────────────

def scheme_of(iso: str, usj_dir: str | None = None) -> str:
    """Resolve the aligner scheme label by fingerprinting the ingested USJ structure; `protestant` (identity) when there is
    no ingested text to fingerprint."""
    if usj_dir and os.path.isdir(usj_dir):
        label, _, _ = detect_scheme(usj_dir)
        if label:
            return label
    return "protestant"


_REF_RE = __import__("re").compile(r"^(\w{3}) (\d+):(\d+)(?:-(?:(\d+):)?(\d+))?$")


def expand_ref(ref: str) -> list[tuple]:
    """'PSA 13:5-6' -> [(PSA,13,5),(PSA,13,6)]; 'NUM 25:19-26:1' -> the two endpoints (a cross-chapter range keeps only its ends);
    a single verse -> one tuple. A superscription ('title') is verse 0."""
    m = _REF_RE.match(ref.strip())
    if not m:
        r = _parse(ref)
        return [r] if r else []
    book, ch, v1, ch2, v2 = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4), m.group(5)
    if v2 is None:
        return [(book, ch, v1)]
    if ch2:
        return [(book, ch, v1), (book, int(ch2), int(v2))]
    return [(book, ch, v) for v in range(v1, int(v2) + 1)]


def multiverse_pairs(scheme: str) -> list[tuple]:
    """[(scheme verse, standard verse)] from the scheme's optional `<table>.multiverse.json` (bcv-commons/bibles' multi-verse
    relations: {"map": [{"s": "LEV 14:55", "t": "LEV 14:55-56"}, ...]}, `s` in the scheme's numbering, `t` in the standard's).
    A relation with ONE verse on one side reduces to plain rows: a single scheme verse covering several standard verses
    (every standard verse -> that scheme verse), or several scheme verses covering one standard verse (every scheme verse ->
    it). Relations with several verses on BOTH sides are skipped (nothing in the data needs them)."""
    fname = _SCHEME_FILE.get(scheme)
    fp = _REG_DIR / f"{fname}.multiverse.json" if fname else None
    if not fp or not fp.exists():
        return []
    out = []
    for rel in json.loads(fp.read_text(encoding="utf-8")).get("map", []):
        src, std = expand_ref(rel["s"]), expand_ref(rel["t"])
        if len(src) == 1 or len(std) == 1:
            out += [(a, b) for a in src for b in std]
    return out


def load_reverse_all(scheme: str) -> dict:
    """{(book,ch,v)_KJV: [(book,ch,v)_scheme, ...]} — every scheme verse that maps to a KJV verse, in table order.
    More than one only where the scheme splits what the standard keeps together (e.g. a two-line Psalm superscription
    that is verses 1-2 in the scheme and the single unnumbered 'title' in the standard)."""
    if scheme in _IDENTITY:
        return {}
    fname = _SCHEME_FILE.get(scheme)
    if not fname:
        return {}
    fp = _REG_DIR / f"{fname}.tsv"
    if not fp.exists():
        return {}
    rev: dict[tuple, list] = {}
    with fp.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or line.startswith("source_ref"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            src, std = _parse(parts[0]), _parse(parts[1])
            if src and std:
                rev.setdefault(std, []).append(src)
    for src, std in multiverse_pairs(scheme):
        if src not in rev.setdefault(std, []):
            rev[std].append(src)
    return rev


def load_reverse(scheme: str) -> dict:
    """{(book,ch,v)_KJV: (book,ch,v)_scheme} — from_standard (the LAST scheme verse wins where several map to one KJV
    verse; `load_reverse_all` keeps them all). Empty (identity) for protestant/kjv/unknown."""
    return {std: srcs[-1] for std, srcs in load_reverse_all(scheme).items()}


# The SPINE's own numbering. The MACULA spine's OT is the WLC, i.e. HEBREW numbering (1CH 5 has 41 verses, JOL 4
# chapters, MAL 3, a Psalm superscription is verse 1) — NOT the KJV standard this module originally assumed ("the
# spine behaves as KJV" held for the old STEPBible spine only). Found 2026-10-05: every OT edition was paired by
# the wrong assumption in the ~2,000 verses where Hebrew and English numbering differ. `hebrew.tsv` (TVTMS) is used
# rather than bcv-commons/bibles' org-to-eng.json because it is a strict superset (it also has Daniel 3:31-6:29).
_SPINE_OT_TABLE = "hebrew"


_SPINE_OT_SCHEME = "org"          # the bcv-commons/bibles code for the numbering `hebrew.tsv` starts from
_SPINE_CHECKED: dict[str, str | None] = {}


def check_spine_numbering(spine_db=None) -> str | None:
    """Refuse to remap when the spine DECLARES an OT numbering other than the one this module assumes. bcv-query writes
    `versification_ot` into spine_meta since 2026-10-05 (the spine's numbering is its fact to state — the July 2026
    verse-pairing bug came from assuming it). A spine without the key (an older pinned file) is accepted unchecked."""
    from lexeme_aligner import config
    path = str(spine_db or config.SPINE_DB)
    if path not in _SPINE_CHECKED:
        import sqlite3
        declared = None
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            row = con.execute("SELECT value FROM spine_meta WHERE key = 'versification_ot'").fetchone()
            con.close()
            declared = row[0] if row else None
        except sqlite3.Error:
            pass
        _SPINE_CHECKED[path] = declared
    declared = _SPINE_CHECKED[path]
    if declared and declared != _SPINE_OT_SCHEME:
        raise RuntimeError(f"spine {path} declares versification_ot={declared!r}, but versification.py maps from "
                           f"{_SPINE_OT_SCHEME!r} ({_SPINE_OT_TABLE}.tsv) — update _SPINE_OT_TABLE before aligning")
    return declared


def load_forward(table: str) -> dict:
    """{(book,ch,v)_scheme: (book,ch,v)_KJV} from one diff table (the `to_standard` direction); titles = verse 0."""
    fp = _REG_DIR / f"{table}.tsv"
    fwd: dict[tuple, tuple] = {}
    if not fp.exists():
        return fwd
    with fp.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or line.startswith("source_ref"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            src, std = _parse(parts[0]), _parse(parts[1])
            if src and std:
                fwd[src] = std
    return fwd


def remapper_for_scheme(scheme: str, nt_variants: tuple[str, ...] | None = None):
    """→ f(book, ch, v): SPINE ref → the given scheme's ref; None if identity (the target is numbered like the
    spine). Spine (Hebrew numbering) → KJV standard via `hebrew.tsv`, then KJV → the target scheme via that
    scheme's own table (identity for protestant). A spine verse the standard leaves unnumbered (a Psalm
    superscription → 'title') maps to verse 0, which an English-numbered text does not have, so it gets no
    target text instead of the wrong one. Also used by the eflomal validator to force a candidate scheme.
    NT: spine → eng via the spine's variants, then eng → the edition via `nt_variants` (the edition's own, see
    `edition_nt_variants`; () = eng.vrs numbering, None = unknown, the NT is left as the spine numbers it); with no edition
    variants the scheme's own NT rows apply (rso has a few)."""
    check_spine_numbering()
    hebrew_ot = _SCHEME_FILE.get(scheme) == _SPINE_OT_TABLE
    spine_nt = spine_nt_variants()
    ed_nt = spine_nt if nt_variants is None else tuple(nt_variants)   # None = unknown (no NT text): keep spine numbering
    nt_s2e, nt_e2ed = _nt_to_eng(spine_nt), _eng_to_nt(ed_nt)
    rev_all = load_reverse_all(scheme)
    rev_nt = {k: v for k, v in rev_all.items() if k[0] in _NT_BOOKS}
    if hebrew_ot and set(spine_nt) == set(ed_nt) and not rev_nt:
        return None

    def nt(key):
        std = nt_s2e.get(key, key)
        if ed_nt:
            return nt_e2ed.get(std, std)
        srcs = rev_all.get(std)
        return srcs[-1] if srcs else std

    if hebrew_ot:
        return lambda book, ch, v: nt((book, ch, v)) if book in _NT_BOOKS else (book, ch, v)
    to_std = load_forward(_SPINE_OT_TABLE)
    # A standard verse that the scheme splits over SEVERAL verses (Synodal 50:1-2 for the English 51 'title'; Synodal 1SA 20:42-43
    # for English 20:42) while the same number of spine verses fold into it: pair them IN ORDER instead of letting the last
    # source win (spine PSA 51:1 AND 51:2 both used to land on Synodal 50:2, leaving 50:1 unpaired).
    spine_of: dict[tuple, list] = collections.defaultdict(list)
    for src_ref, std_ref in to_std.items():
        spine_of[std_ref].append(src_ref)
    rank: dict[tuple, tuple] = {}                       # spine verse -> its scheme verse
    for std_ref, srcs in rev_all.items():
        if len(srcs) < 2:
            continue
        members = list(spine_of.get(std_ref, ()))
        if std_ref[2] != 0 and std_ref not in to_std:   # the spine verse with the standard's own number (identity), if it stays put
            members.append(std_ref)
        members.sort()
        if len(members) == len(srcs):
            for m, sv in zip(members, sorted(srcs)):
                rank[m] = sv

    def f(book: str, ch: int, v: int):
        key = (book, ch, v)
        if book in _NT_BOOKS:
            return nt(key)
        if key in rank:
            return rank[key]
        std = to_std.get(key, key)
        srcs = rev_all.get(std)
        return srcs[-1] if srcs else std
    return f


# aligner label -> bcv-commons/bibles scheme code (the vocabulary clients can look up there)
_LABEL_TO_CDN = {"protestant": "eng", "kjv": "eng", "": "eng", "hebrew": "org", "lxx": "lxx", "septuagint": "lxx",
                 "vul": "vul", "rso": "rso", "orgw": "orgw", "catm": "catm"}


def edition_scheme(iso: str, usj_dir: str | None = None) -> str:
    """The edition's numbering as a bcv-commons/bibles scheme code (eng, org, orgw, catm, lxx, vul, rso)."""
    if usj_dir and os.path.isdir(usj_dir):
        _, cdn, _ = detect_scheme(usj_dir)
        if cdn:
            return cdn
    return _LABEL_TO_CDN.get(scheme_of(iso, usj_dir), "eng")


def verse_map_name(cdn: str, nt_variants=()) -> str:
    """File name of the published spine -> edition verse map: `_versification_<scheme>.json`, plus `+<variant>` per NT
    variant the edition has (`_versification_eng+REV12-17.json`)."""
    return f"_versification_{cdn}" + "".join(f"+{v}" for v in sorted(nt_variants or ())) + ".json"


def verse_map(cdn: str, spine_verses, nt_variants=()) -> dict[str, str]:
    """{"BOOK C:V" spine ref: "BOOK C:V" target ref} for every spine verse whose target verse DIFFERS, exactly as the
    aligner pairs them (`remapper_for_scheme`); a verse the target leaves unnumbered (a Psalm superscription) maps to
    "BOOK C:title". Published as compact-alignments/_index/_versification_<scheme>.json so a client knows which
    target verse a spine-keyed span indexes. `spine_verses`: iterable of (book, ch, v). For Hebrew numbering only NT rows (if any)."""
    f = remapper_for_scheme(_CDN_TABLE.get(cdn, "protestant"), tuple(nt_variants or ()))
    out: dict[str, str] = {}
    if f is None:
        return out
    for book, ch, v in spine_verses:
        _, tc, tv = f(book, ch, v)
        if (tc, tv) != (ch, v):
            out[f"{book} {ch}:{v}"] = f"{book} {tc}:{tv if tv else 'title'}"
    return out


def remapper(iso: str, usj_dir: str | None = None):
    """→ f(book, ch, v) mapping a SPINE ref to the target's scheme ref; None if identity (Hebrew-numbered target whose NT numbering is the spine's).
    Auto-detects the scheme from the ingested USJ when usj_dir is given."""
    return remapper_for_scheme(scheme_of(iso, usj_dir), edition_nt_variants(iso, usj_dir))
