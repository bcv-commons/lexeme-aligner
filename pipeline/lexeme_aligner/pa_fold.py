"""part-alignments -> compact-alignments: the fold library (Python; fold.mjs is the same in JavaScript).

Standard library only, so the file can be copied out and used alone (it is published with bcv-commons/part-alignments as fold.py).

part-alignments links every SOURCE UNIT (a word part: stem, ending, prefix, preverb ...) to target words. compact-alignments links every
content TOKEN (a Greek word, a Hebrew MACULA part) to target words. A token is its units together; `_index/<BOOK>_parts.json` lists
both per verse:

    "units":  [[part id, kind, text, tok], ...]          srcOrd = position here
    "tokens": [[key, lexeme, content, carrier], ...]     carrier = srcOrd of the unit that carries the token's lexeme

The fold, per verse:
  1. A token's target words = its carrier unit's words, plus every other unit's words that no other token's carrier claims (the
     claimed-target rule: an ending linked to a word that another word already renders adds nothing to its own word).
  2. compact's srcOrd = position of the token among the verse's content tokens; tokens with no words get no entry.
  3. Provenance (meta) per token comes from its carrier unit when the carrier is linked, else from its first linked unit:
     method, conf, rend (dense: one char / id per entry), contested, rule (sparse). `parts` (target word parts) is the union over
     every unit that gave words. `bonus` (a word in an annotation that renders a gap) only for content tokens with no entry, from
     the carrier.
  4. extra (the opt-in residual layer): for content tokens with no main entry, the union of their units' extra words.
  `srcOrd:_` (an LLM layer: checked, no rendering) gives no words.

Fold rule (`fold_rule`, default "fold"): "fold" is rule 1 above. "stem" keeps only the carrier unit: a token's words are its carrier's
words, and a token whose carrier has no link gets no entry (its other units' links stay in part-alignments only). "stem" is the
convention of gold standards that leave e.g. French "de" for a Greek genitive ending unaligned; it equals the whole-word alignments
of before part-alignments within measurement noise. The rule an edition's published compact files were made with is its manifest
entry's `fold_rule`.

    python -m lexeme_aligner.pa_fold --parts DIR --out DIR [--edition PATH ...] [--fold-rule fold|stem]   # -> compact files
    python -m lexeme_aligner.pa_fold --index-only --parts DIR --out DIR            # compact's _index files from _parts.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CHANNELS = ("method", "conf", "contested", "bonus", "rule", "rend", "parts", "runs")   # compact's meta channels, in compact's order
DENSE = ("method", "conf", "rend", "runs")       # one char (rend: one id) per linked entry; runs = run_agreement.py
NONE = "_"
FOLD_RULES = ("fold", "stem")


def positions(span: str) -> list[int]:
    """'3' / '3-5' / '3,7' -> target word positions."""
    out = []
    for piece in span.split(","):
        a, _, b = piece.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def encode_span(pos: list[int]) -> str:
    """compact's span grammar: one position, a contiguous range lo-hi, or a sorted comma list."""
    if len(pos) == 1:
        return str(pos[0])
    lo, hi = min(pos), max(pos)
    if hi - lo + 1 == len(pos):
        return f"{lo}-{hi}"
    return ",".join(str(i) for i in sorted(pos))


def entries(s: str) -> dict[int, str]:
    """'0:1 3:4-5 7:_' -> {0: '1', 3: '4-5', 7: '_'}"""
    out = {}
    for e in s.split():
        o, _, sp = e.partition(":")
        out[int(o)] = sp
    return out


def _sparse(s: str) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for e in s.split():
        o, _, rest = e.partition(":")
        out.setdefault(int(o), []).append(rest)
    return out


def fold_verse(verse: dict, main: str, meta: dict[str, str] | None = None, extra: str = "",
               fold_rule: str = "fold") -> tuple[str, dict[str, str], str]:
    """One verse of part-alignments -> (compact entry, {channel: compact meta entry}, compact extra entry)."""
    if fold_rule not in FOLD_RULES:
        raise ValueError(f"fold_rule must be one of {FOLD_RULES}, not {fold_rule!r}")
    units, tokens = verse["units"], verse["tokens"]
    meta = meta or {}
    links = {o: sp for o, sp in entries(main).items() if sp != NONE}
    linked_order = [o for o, sp in sorted(entries(main).items()) if sp != NONE]       # dense channels follow the linked entries
    dense_at = {o: i for i, o in enumerate(linked_order)}
    dense = {c: (meta.get(c, "").split() if c == "rend" else list(meta.get(c, ""))) for c in DENSE if c in meta}
    sparse = {c: _sparse(meta.get(c, "")) for c in ("contested", "bonus", "rule", "parts") if c in meta}
    xl = entries(extra)
    members: dict[int, list[int]] = {}
    for o, u in enumerate(units):
        if u[3] >= 0:
            members.setdefault(u[3], []).append(o)
    owner: dict[int, set[int]] = {}
    for k, t in enumerate(tokens):
        if t[3] in links:
            for p in positions(links[t[3]]):
                owner.setdefault(p, set()).add(k)

    out, out_x = [], []
    ch: dict[str, list[str]] = {c: [] for c in CHANNELS}
    cord = -1
    for k, t in enumerate(tokens):
        if not t[2]:
            continue
        cord += 1
        c = t[3]
        units_k = [c] if fold_rule == "stem" else [c] + [o for o in members.get(k, []) if o != c]
        got, givers = [], []
        for o in units_k:
            if o not in links:
                continue
            pos = positions(links[o])
            if o != c:
                pos = [p for p in pos if not (owner.get(p, set()) - {k})]
            new = [p for p in pos if p not in got]
            if new or (o == c and pos):
                givers.append(o)
            got += new
        if got:
            src = givers[0]
            only = len(givers) == 1 and givers[0] == c and set(got) == set(positions(links[c]))
            out.append(f"{cord}:{links[c] if only else encode_span(got)}")
            for d, vals in dense.items():
                i = dense_at.get(src)
                if i is not None and i < len(vals):
                    ch[d].append(vals[i])
            for name in ("contested", "rule"):
                for rest in sparse.get(name, {}).get(src, []):
                    ch[name].append(f"{cord}:{rest}")
            if "parts" in sparse:
                tp = set()
                for o in givers:
                    for rest in sparse["parts"].get(o, []):
                        tp.update(rest.split(","))
                if tp:
                    ch["parts"].append(f"{cord}:" + ",".join(sorted(tp, key=lambda x: tuple(int(n) for n in x.split(".")) + ((-1,) if "." not in x else ()))))
        else:
            for rest in sparse.get("bonus", {}).get(c, []):
                ch["bonus"].append(f"{cord}:{rest}")
            xp = []
            for o in units_k:
                if o in xl and xl[o] != NONE:
                    xp += [p for p in positions(xl[o]) if p not in xp]
            if xp:
                only = c in xl and set(xp) == set(positions(xl[c]))
                out_x.append(f"{cord}:{xl[c] if only else encode_span(xp)}")
    sep = {"method": "", "conf": "", "rend": " ", "runs": ""}
    return " ".join(out), {c: sep.get(c, " ").join(v) for c, v in ch.items() if c in meta}, " ".join(out_x)


def fold_book(index: dict, main: list[str], meta: dict[str, list[str]] | None = None,
              extra: list[str] | None = None, fold_rule: str = "fold") -> tuple[list[str], dict[str, list[str]], list[str]]:
    """A book: `_index/<BOOK>_parts.json` + the edition's three files (lists position-parallel to the index's verses)."""
    meta = meta or {}
    refs = list(index)
    cm, cmeta, cx = [], {c: [] for c in CHANNELS if c in meta}, []
    for i, ref in enumerate(refs):
        m, md, x = fold_verse(index[ref], main[i], {c: v[i] for c, v in meta.items()}, extra[i] if extra else "", fold_rule)
        cm.append(m)
        cx.append(x)
        for c in cmeta:
            cmeta[c].append(md.get(c, ""))
    return cm, cmeta, cx


def compact_index(index: dict) -> dict[str, dict[str, list[str]]]:
    """compact's four shared index files for a book: lexemes / keys (content tokens, srcOrd), fn / fn_keys (the others, fnOrd)."""
    out = {"lexemes": {}, "keys": {}, "fn": {}, "fn_keys": {}}
    for ref, v in index.items():
        toks = v["tokens"]
        out["lexemes"][ref] = [t[1] for t in toks if t[2]]
        out["keys"][ref] = [t[0] for t in toks if t[2]]
        out["fn"][ref] = [t[1] for t in toks if not t[2]]
        out["fn_keys"][ref] = [t[0] for t in toks if not t[2]]
    return out


def _load(fp: Path):
    return json.loads(fp.read_text(encoding="utf-8"))


def _dump(fp: Path, data, compact_sep: bool = False) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, ensure_ascii=False, **({"separators": (",", ":")} if compact_sep else {})) + "\n", encoding="utf-8")


def fold_edition(parts_root: Path, edition_dir: Path, out_root: Path, fold_rule: str = "fold") -> list[str]:
    """Every book file of one part-alignments edition folder -> compact files at the same relative path under out_root."""
    rel = edition_dir.relative_to(parts_root)
    done = []
    for fp in sorted(edition_dir.glob("*.json")):
        name = fp.name
        if name.endswith((".meta.json", ".extra.json")) or name.startswith("_"):
            continue
        book = name.split("_", 1)[0]
        index = _load(parts_root / "_index" / f"{book}_parts.json")
        stem = name[:-len(".json")]
        meta_fp, extra_fp = fp.with_name(stem + ".meta.json"), fp.with_name(stem + ".extra.json")
        meta = _load(meta_fp) if meta_fp.exists() else {}
        extra = _load(extra_fp) if extra_fp.exists() else None
        cm, cmeta, cx = fold_book(index, _load(fp), meta, extra, fold_rule)
        dest = out_root / rel
        _dump(dest / name, cm)
        if any(cx):
            _dump(dest / (stem + ".extra.json"), cx)
        cmeta = {c: v for c, v in cmeta.items() if c != "parts" or any(v)}
        if any(any(v) for v in cmeta.values()):
            _dump(dest / (stem + ".meta.json"), cmeta)
        done.append(book)
    for extra_name in ("_fingerprints.txt",):
        if (edition_dir / extra_name).exists():
            (out_root / rel).mkdir(parents=True, exist_ok=True)
            (out_root / rel / extra_name).write_bytes((edition_dir / extra_name).read_bytes())
    return done


def write_compact_index(parts_root: Path, out_root: Path) -> list[str]:
    books = []
    for fp in sorted((parts_root / "_index").glob("*_parts.json")):
        book = fp.name.split("_", 1)[0]
        for name, data in compact_index(_load(fp)).items():
            _dump(out_root / "_index" / f"{book}_{name}.json", data, compact_sep=False)
        books.append(book)
    return books


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parts", type=Path, required=True, help="a part-alignments tree (has _index/)")
    ap.add_argument("--out", type=Path, required=True, help="where the compact files go (same relative paths)")
    ap.add_argument("--edition", type=Path, action="append", help="edition folder(s) (default: every one under --parts)")
    ap.add_argument("--index-only", action="store_true")
    ap.add_argument("--fold-rule", choices=FOLD_RULES, default="fold",
                    help="fold: carrier + unclaimed links of the other units (default); stem: the carrier unit only")
    a = ap.parse_args(argv)
    if a.index_only:
        print(json.dumps({"index books": len(write_compact_index(a.parts, a.out))}))
        return 0
    eds = a.edition or sorted({fp.parent for fp in a.parts.glob("?/*/*/*.json")})
    n = {str(e.relative_to(a.parts) if e.is_absolute() or e.parts[:len(a.parts.parts)] == a.parts.parts else e):
         len(fold_edition(a.parts, e if e.parts[:len(a.parts.parts)] == a.parts.parts else a.parts / e, a.out, a.fold_rule)) for e in eds}
    print(json.dumps(n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
