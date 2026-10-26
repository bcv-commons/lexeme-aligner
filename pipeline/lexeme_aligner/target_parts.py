"""Target word PARTS: run the alignment chain on a target word's prefixes, stem and suffixes instead of the whole word.

WHY (2026-10-23). The chain aligns whole target tokens. In a Hebrew translation one token often carries several source words:
ובני = ו (καί) + בני (υἱοί), בבית = ב (ἐν) + ה (τῷ) + בית (οἴκῳ), אביו = אב (πατήρ) + יו (αὐτοῦ). The aligners then let those source
words compete for one token and link at most a part of them.

HOW. For an edition with a part analysis pinned in config/target_parts.json (`for_edition`), full_chain writes a PARTS VIEW of the
edition (`write_view`: the same books and verses, each analysed word written as its parts, the verse-number token left out, plus
`_units.json` mapping every unit to (token, part)), runs eflomal, gloss (on `content_priors`), span extension, gap-fill and residual on the
view unchanged in a private out dir, and `fold`s their links back to the edition's real tokens: `t_idx` = tokens as everywhere else, plus
`t_parts` = [[token, part], ...] (part None = whole word) for the exports that can say which part a link covers. Everything after
(exports, compact) reads the folded files and needs no change. Measurements: full_chain's comment at this step, `content_priors`.

The analysis is a `words.parquet` in bcv-commons/hebrew-nt-analysis's shape: one row per part, `i` = our token number in the edition's
verse (tokenizer v2), `part`, `text` (pointed letters; empty for the article hidden after a preposition), `lemma`, `class`, `source`
(macula | model), `p_split` (the tagger's confidence in the split, on part 0). A word is used whole when the tagger is unsure
(`p_split` below the threshold), when it has one part, or when its token differs from ours. A word of class `verse-number` (Salkinson
writes each verse's number as Hebrew letters, א, יז ..., as its first token) is left out of the view altogether, so it never gets a link.

Since 2026-10-26 the analysis may also be a split file in the standard format (`bcv-split/0.1`, docs/split-format.md; first: bsb-add-books'
Zinza, pinned as a local file with `"format": "bcv-split"`). There a part's class comes from its `slot` (prefix/suffix/infix -> `affix`,
a function part for `content_priors`; stem -> `stem`), a tone part has no letters and is left out, a `whole` row (also the never-split
words, `p_split` 0) keeps the word whole, and `p_split`, when the file has it, is the threshold as above.
"""
from __future__ import annotations

import collections
import json
import re
import unicodedata as ud
from pathlib import Path

from lexeme_aligner.usj_source import strip_marks

CONFIG = Path(__file__).resolve().parents[2] / "config/target_parts.json"
VENDOR = Path(__file__).resolve().parents[2] / "pipeline/vendor/hebrew-nt-analysis"


def for_edition(tag: str, config: Path = CONFIG, vendor: Path = VENDOR) -> tuple[Path, dict] | None:
    """The part analysis config/target_parts.json pins for this edition tag: (local file, entry), fetched once from HF at the
    pinned tag into pipeline/vendor/ and checked against the pinned sha256 (a mismatch is an error, never silently used).
    None when the edition has no entry."""
    import hashlib
    entry = json.loads(Path(config).read_text(encoding="utf-8")).get(tag) if Path(config).exists() else None
    if not entry:
        return None
    local = Path(vendor) / entry["revision"] / entry["file"] if "local" not in entry else Path(__file__).resolve().parents[2] / entry["local"]
    if not local.exists() and "local" in entry:
        raise SystemExit(f"[target_parts] {tag}: the pinned local file {entry['local']} is missing ({config})")
    if not local.exists():
        from huggingface_hub import hf_hub_download
        local.parent.mkdir(parents=True, exist_ok=True)
        got = Path(hf_hub_download(entry["dataset"], entry["file"], repo_type="dataset", revision=entry["revision"]))
        local.write_bytes(got.read_bytes())
    sha = hashlib.sha256(local.read_bytes()).hexdigest()
    if sha != entry["sha256"]:
        raise SystemExit(f"[target_parts] {local}: sha256 {sha[:12]}… is not the pinned {entry['sha256'][:12]}… ({config})")
    return local, entry


def _plain(s: str) -> str:
    return strip_marks(ud.normalize("NFC", s or ""))


SLOT_CLASS = {"prefix": "affix", "suffix": "affix", "infix": "affix", "stem": "stem", "whole": "whole", "tone": "tone"}


def _rows(words_parquet: Path | str) -> list[dict]:
    """The analysis rows with `class`, `lemma`, `source`, `p_split` filled for both formats: hebrew-nt-analysis as is; a bcv-split file
    (has `slot`) with class from the slot (unless it has its own `class`), lemma = `chain_id` or `form`, source = `basis`."""
    import pyarrow.parquet as pq
    t = pq.read_table(words_parquet)
    rows = t.to_pylist()
    if "slot" not in t.column_names:
        return rows
    for r in rows:
        r["class"] = r.get("class") or SLOT_CLASS.get(r["slot"], r["slot"])
        r["lemma"] = r.get("lemma") or r.get("chain_id") or r.get("form") or ""
        r["source"] = r.get("basis")
        r.setdefault("p_split", None)
    return rows


def load_parts(words_parquet: Path | str, min_p_split: float = 0.9) -> dict[tuple, dict]:
    """(book, chapter, verse, i) -> {"token": str, "parts": [(part, letters, lemma, class)]} for every word that splits into at least
    two parts with letters and that the threshold lets through. Parts without letters are left out (eflomal has nothing to see)."""
    words: dict[tuple, list] = collections.defaultdict(list)
    for r in _rows(words_parquet):
        words[(r["book"], r["chapter"], r["verse"], r["i"])].append(r)
    out = {}
    for k, rows in words.items():
        rows.sort(key=lambda r: r["part"])
        head = rows[0]
        if head["class"] == "verse-number":
            out[k] = {"token": head["token"], "parts": []}
            continue
        if head["source"] == "model" and (head["p_split"] or 0.0) < min_p_split:
            continue
        if head["class"] == "whole" or (head["p_split"] is not None and head["p_split"] < min_p_split and head["source"] != "macula"):
            continue
        parts = [(r["part"], _plain(r["text"]), _plain(r["lemma"]), r["class"]) for r in rows if _plain(r["text"])]
        if len(parts) >= 2:
            out[k] = {"token": head["token"], "parts": parts}
    return out


# ---------------------------------------------------------------- the parts view: the whole chain on parts

VIEW_MAP = "_units.json"
VIEW_ROOT = Path("pipeline/work/parts-view")      # <tag>/usj (the view) and <tag>/out (the steps' raw output, folded into OUT)


def view_verse(book: str, ch: int, vs: int, text: str, parts: dict[tuple, dict]) -> tuple[str, list[tuple[int, int | None]]]:
    """One verse of the parts view: (text whose tokens are the units, unit -> (token, part)). A word with a split becomes its parts,
    others stay whole, a verse number is left out. Falls back to the whole tokens when the units would not tokenize back to
    themselves (never seen on heb_scg; a guard, not a path)."""
    from lexeme_aligner.usj_source import tokenize
    toks = tokenize(text)
    units, umap = [], []
    for i, w in enumerate(toks):
        p = parts.get((book, ch, vs, i))
        if p and p["token"] == w and not p["parts"]:
            continue                                      # verse number
        if p and p["token"] == w:
            for part, letters, _lemma, _cls in p["parts"]:
                units.append(letters)
                umap.append((i, part))
        else:
            units.append(w)
            umap.append((i, None))
    if tokenize(" ".join(units)) != units:
        return " ".join(toks), [(i, None) for i in range(len(toks))]
    return " ".join(units), umap


def write_view(usj_dir: Path, view_dir: Path, parts: dict[tuple, dict]) -> dict:
    """Write the parts view of an ingested edition: one USJ per book with the same file name and verse structure, whose verse
    texts are the units (view_verse), plus `_units.json` {BOOK: {"C:V": [[token, part|null], ...]}} to fold links back with.
    Every per-edition step of the chain runs on it unchanged; `fold()` maps their output to the real tokens."""
    from lexeme_aligner.usj_source import read_verse_ranges
    view_dir = Path(view_dir)
    view_dir.mkdir(parents=True, exist_ok=True)
    maps: dict = {}
    for usj in sorted(Path(usj_dir).glob("*.json")):
        num, _, rest = usj.stem.partition("-")
        if not num.isdigit():
            continue
        book = rest[:3]
        content: list = [{"type": "book", "marker": "id", "code": book, "content": []}]
        cur_ch, para = None, None
        bmap = maps.setdefault(book, {})
        for (ch, vs), info in sorted(read_verse_ranges(usj).items()):
            if ch != cur_ch:
                content.append({"type": "chapter", "marker": "c", "number": str(ch)})
                para = {"type": "para", "marker": "p", "content": []}
                content.append(para)
                cur_ch = ch
            text, umap = view_verse(book, ch, vs, info["text"], parts)
            ve = info["verse_end"]
            para["content"] += [{"type": "verse", "marker": "v", "number": str(vs) if ve == vs else f"{vs}-{ve}"}, " " + text + " "]
            bmap[f"{ch}:{vs}"] = [list(u) for u in umap]
        (view_dir / usj.name).write_text(json.dumps({"type": "USJ", "version": "3.0", "content": content}, ensure_ascii=False),
                                         encoding="utf-8")
    (view_dir / VIEW_MAP).write_text(json.dumps(maps, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return maps


_PRIOR_POS = re.compile(r":(\d+)$")


def fold(tag: str, view_out: Path, out: Path, view_dir: Path, usj_dir: Path, methods: tuple[str, ...] = ("eflomal", "gloss", "spanext",
         "gapfill", "residual"), trefs: dict | None = None) -> collections.Counter:
    """Map every align_<method>_<tag>_<BOOK>.jsonl(.gz) a parts-view run wrote into `view_out` back to the edition's real tokens,
    written to `out` under the same names: `t_idx` = the tokens the units belong to, `t_parts` = [[token, part], ...] (part None =
    whole word), `target` re-rendered from the real tokens, a span-extension prior's target position (`spanext_x:<n>`) mapped too.
    Every existing align_<method>_<tag>_* file of these methods in `out` (either spelling) is removed first: a book a method wrote
    nothing for in the view must not keep an older whole-word file beside the folded ones (align_files.unlink_stale_gz)."""
    import gzip
    from lexeme_aligner.align_files import tag_files
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.usj_source import read_verse_ranges, tokenize
    from lexeme_aligner.versification import remapper
    maps = json.loads((Path(view_dir) / VIEW_MAP).read_text(encoding="utf-8"))
    books = sorted(maps)
    if trefs is None:                                     # spine verse -> the edition verse each jsonl record was aligned against
        trefs = {(r.book, r.ch, r.v): r.tref for r in build_corpus(books, Path(view_dir), HebrewSource(), remapper(tag, Path(view_dir)))}
    real = {}
    for usj in Path(usj_dir).glob("*.json"):
        num, _, rest = usj.stem.partition("-")
        if num.isdigit() and rest[:3] in maps:
            real[rest[:3]] = {k: tokenize(v["text"]) for k, v in read_verse_ranges(usj).items()}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stats = collections.Counter()
    for m in methods:
        for old in tag_files(out, m, tag):
            Path(str(old)).unlink()
            stats["replaced"] += 1
    for m in methods:
        for fp in tag_files(Path(view_out), m, tag):
            src = Path(str(fp))
            opener = gzip.open if src.name.endswith(".gz") else open
            dest = out / src.name.removesuffix(".gz")
            with opener(src, "rt", encoding="utf-8") as fh, dest.open("w", encoding="utf-8") as w:
                for line in fh:
                    if not line.strip():
                        continue
                    r = json.loads(line)
                    tref = trefs.get((r["book"], r["chapter"], r["verse"]))
                    umap = maps.get(r["book"], {}).get(f"{tref[0]}:{tref[1]}") if tref else None
                    toks = real.get(r["book"], {}).get(tuple(tref)) if tref else None
                    pairs = []
                    for p in r["pairs"]:
                        if umap is None or toks is None or any(u >= len(umap) for u in p.get("t_idx") or ()):
                            stats["dropped"] += 1
                            continue
                        units = [tuple(umap[u]) for u in p["t_idx"]]
                        p = dict(p, t_idx=sorted({t for t, _ in units}),
                                 t_parts=sorted([t, pt] for t, pt in units),
                                 target=" ".join(toks[t] for t in sorted({t for t, _ in units})))
                        if isinstance(p.get("prior"), str) and p["prior"].startswith("spanext") and (mm := _PRIOR_POS.search(p["prior"])):
                            u = int(mm.group(1))
                            if u < len(umap):
                                p["prior"] = p["prior"][:mm.start()] + f":{umap[u][0]}"
                        pairs.append(p)
                        stats[m] += 1
                    w.write(json.dumps(dict(r, pairs=pairs), ensure_ascii=False) + "\n")
    return stats


FUNCTION_CLASSES = frozenset({"cj", "prep", "art", "rel", "pron", "om", "verse-number", "ptcl", "ij", "x", "-", "affix"})


def content_priors(tag: str, publish_iso: str, view_out: Path, view_dir: Path, analysis: Path, lex_root: Path,
                   trefs: dict | None = None) -> collections.Counter:
    """Gloss priors for the parts view: a lexeme-alignments-shaped `<lex_root>/iso=<publish_iso>/data.parquet` built from the view's own
    eflomal links, keeping a link only when the Greek lexeme is a content word (prior-pack keyness not null) and every linked unit is a
    content part (analysis class not in FUNCTION_CLASSES). BootstrapPriors reads it when ALIGNER_LEX_ROOT points at `lex_root`.
    Measured on heb_scg (2026-10-23, pipeline/work/parts-ab arm CVQ): against the language's whole-word priors, gloss content coverage
    56% -> 75%, coverage, stability and class fit of the final links unchanged, hand check of changed links 8:6 (content) and ~9:3
    (function, mostly μή no longer forced onto the preposition אל); unfiltered part priors (every link) added coverage but lost the hand
    check badly (content 3:7, function 5:13)."""
    import gzip
    import pyarrow as pa
    import pyarrow.parquet as pq
    from lexeme_aligner.align_files import tag_files
    from lexeme_aligner.config import PRIOR_PACK
    maps = json.loads((Path(view_dir) / VIEW_MAP).read_text(encoding="utf-8"))
    if trefs is None:
        from lexeme_aligner.hebrew_source import HebrewSource
        from lexeme_aligner.run_pilot import build_corpus
        from lexeme_aligner.versification import remapper
        trefs = {(r.book, r.ch, r.v): r.tref for r in build_corpus(sorted(maps), Path(view_dir), HebrewSource(), remapper(tag, Path(view_dir)))}
    cls: dict[tuple, str] = {}
    word_cls: dict[tuple, set] = collections.defaultdict(set)
    for r in _rows(analysis):
        cls[(r["book"], r["chapter"], r["verse"], r["i"], r["part"])] = r["class"]
        word_cls[(r["book"], r["chapter"], r["verse"], r["i"])].add(r["class"])
    content = {r["lexeme"] for r in pq.read_table(PRIOR_PACK, columns=["lexeme", "keyness"]).to_pylist() if r["keyness"] is not None}
    counts, stats = collections.Counter(), collections.Counter()
    for fp in tag_files(Path(view_out), "eflomal", tag):
        src = Path(str(fp))
        with (gzip.open if src.name.endswith(".gz") else open)(src, "rt", encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                tref = trefs.get((r["book"], r["chapter"], r["verse"]))
                umap = maps.get(r["book"], {}).get(f"{tref[0]}:{tref[1]}") if tref else None
                for p in r["pairs"]:
                    if p.get("lexeme") not in content or umap is None or any(u >= len(umap) for u in p["t_idx"]):
                        stats["skipped"] += 1
                        continue
                    key = (r["book"],) + tuple(tref)
                    ks = [cls.get(key + (t, pt)) if pt is not None else (word_cls.get(key + (t,)) or {"noun"}) for t, pt in (umap[u] for u in p["t_idx"])]
                    if any((k & FUNCTION_CLASSES) if isinstance(k, set) else (k in FUNCTION_CLASSES) for k in ks):
                        stats["skipped"] += 1
                        continue
                    counts[(p["lexeme"], p["strong"], p["target"])] += 1
                    stats["kept"] += 1
    rows = [dict(surface=t, lexeme=lx, strong=s, method="eflomal", base_text=tag, count=n) for (lx, s, t), n in counts.items()]
    part = Path(lex_root) / f"iso={publish_iso}"
    part.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema([("surface", pa.string()), ("lexeme", pa.string()), ("strong", pa.string()),
                                                              ("method", pa.string()), ("base_text", pa.string()), ("count", pa.int64())])),
                   part / "data.parquet")
    stats["rows"] = len(rows)
    return stats
