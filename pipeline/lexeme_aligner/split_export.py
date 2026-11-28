"""Write an edition's bcv-split/0.1 file (split/words.parquet + _split.json) from a bcv-segfacts bundle, with the split engine
(pipeline/split_engine). Generalised from pipeline/work/kvg-parts/export_split.py (2026-10-28, Kuni), same numbering:
seg.S.split(written token, tones=True), part = list index, a hyphen word split joined with each piece numbered from 0.

    python -m lexeme_aligner.split_export --tag TAG --usj DIR --bundle DIR --out DIR [--source TEXT] [--text-licence TEXT]

The file holds the edition's words: it is for the chain (target_parts), and published only where the text licence allows."""
import argparse, hashlib, json, re, sys, unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "pipeline/split_engine"), str(REPO / "pipeline/vendor/bibles")]
import bcv_segment, bcv_written  # noqa: E402
import pyarrow as pa, pyarrow.parquet as pq  # noqa: E402
from lexeme_aligner import usj_source  # noqa: E402
from lexeme_aligner.text_fingerprint import usj_verses, book_file_hash  # noqa: E402

seg = None
nfc = lambda s: unicodedata.normalize("NFC", s)


def clusters(s):
    """NFD base-letter clusters: [(start, end)] of each letter plus the combining marks after it."""
    out = []
    for k, c in enumerate(s):
        if unicodedata.combining(c) and out:
            out[-1][1] = k + 1
        else:
            out.append([k, k + 1])
    return out


def parts_of(word):
    """[(slot, text, form, position, chain_id, tone)] in the segmenter's own order and numbering (seg.S.split(word, tones=True);
    part = list index, so a reader who runs the pinned segmenter on the written token gets the same part numbers), or None (keep
    it whole: not divided, or the parts do not rebuild the word). A tone part has no text; its marks come back as bcv-split `tone`
    entries "<pos>:<mark>", pos counted from the word's END (1 = last cluster). The segmenter counts 0-based from the START
    (`ndø̀go᷄` -> 2:U+0300 4:U+1DC4); converted here, question 3 of the note to bsb-add-books."""
    ms = seg.S.split(word, tones=True)
    if len(ms) < 2:
        return None
    d = unicodedata.normalize("NFD", nfc(word))
    cl = clusters(d)
    drop, tone_of = set(), {}
    for k, m in enumerate(ms):
        if m.slot != "tone":
            continue
        entries = []
        for item in m.text.split():
            pos, _, code = item.partition(":")
            mark = "".join(chr(int(h, 16)) for h in re.findall(r"U\+([0-9A-Fa-f]{4,6})", code)) or code
            pos = int(pos)
            if not 0 <= pos < len(cl):
                return None
            a, b = cl[pos]
            at = d.find(mark, a, b)
            if at < 0:
                return None
            drop.update(range(at, at + len(mark)))
            entries.append(f"{len(cl) - pos}:{mark}")
        tone_of[k] = entries
    base = nfc("".join(c for k, c in enumerate(d) if k not in drop))
    texts = [nfc(m.text) for m in ms if m.slot != "tone"]
    if "".join(texts) != base.lower():
        return None
    res, at = [], 0
    for k, m in enumerate(ms):                          # slice the written word: the segmenter lowercases
        if m.slot == "tone":
            res.append(("tone", "", m.form, None, seg.ids.get((m.form, m.position)), tone_of[k]))
            continue
        t = nfc(m.text)
        res.append((m.slot, base[at:at + len(t)], m.form, m.position, seg.ids.get((m.form, m.position)) if m.slot != "stem" else None, None))
        at += len(t)
    if "".join(r[1] for r in res) != base or not all(r[1] for r in res if r[0] != "tone"):
        return None
    return res


def hyphen_groups(text, n):
    """Runs of token numbers joined by a single '-' with nothing else between them ("ndø-omét" -> [k, k+1])."""
    sp = usj_source._token_spans(text)
    assert len(sp) == n
    groups, cur = [], [0] if n else []
    for k in range(1, n):
        if text[sp[k - 1][1]:sp[k][0]] == "-":
            cur.append(k)
        else:
            groups.append(cur)
            cur = [k]
    if cur:
        groups.append(cur)
    return [g for g in groups if len(g) > 1]


def group_parts(words):
    """bsb-add-books 2026-10-28 (answer 2): a hyphen word is split JOINED ("ndø-omét" -> ndø- + omét) and each piece takes the parts
    inside it, numbered from 0 within its token. {piece: [(slot, text, form, position, chain_id, tone)]}, or None when the joined
    split has a tone part, does not rebuild, a part crosses the hyphen, or no piece gets an affix (then each piece is split alone)."""
    joined = "-".join(nfc(w) for w in words)
    ms = seg.S.split(joined, tones=True)
    if len(ms) < 2 or any(m.slot == "tone" for m in ms) or "".join(nfc(m.text) for m in ms) != joined.lower():
        return None
    bounds, at = [], 0                                  # piece k = joined[bounds[k][0]:bounds[k][1]]
    for w in words:
        bounds.append((at, at + len(nfc(w))))
        at += len(nfc(w)) + 1
    out, at = {k: [] for k in range(len(words))}, 0
    for m in ms:
        a, b = at, at + len(nfc(m.text))
        at = b
        a2 = a + 1 if joined[a] == "-" else a          # a hyphen at the part's edge belongs to no piece
        b2 = b - 1 if joined[b - 1] == "-" else b
        hit = [k for k, (x, y) in enumerate(bounds) if x <= a2 and b2 <= y]
        if not hit or b2 <= a2:
            return None
        k = hit[0]
        out[k].append((m.slot, joined[a2:b2], m.form, m.position, seg.ids.get((m.form, m.position)) if m.slot != "stem" else None, None))
    if any(not v for v in out.values()) or not any(p[0] != "stem" for v in out.values() for p in v):
        return None
    for k, v in out.items():
        if "".join(p[1] for p in v) != nfc(words[k]):
            return None
    return out


def export(tag: str, usj: Path, bundle: Path, out: Path, source: str, text_licence: str) -> dict:
    global seg
    seg = bcv_segment.load(str(bundle))
    rows, stats, books, fps = [], Counter(), [], {}
    for f in sorted(usj.glob("*.json")):
        book = f.stem.split("-", 1)[1]
        books.append(book)
        fps[book] = book_file_hash(f)
        for (ch, vs), text in sorted(usj_verses(f).items()):
            toks, words = usj_source.tokenize(text), bcv_written.tokenize_written(text)
            assert len(toks) == len(words), (book, ch, vs)
            joined = {}
            for g in hyphen_groups(text, len(toks)):
                if (gp := group_parts([words[k] for k in g])) is not None:
                    stats["hyphen groups"] += 1
                    for j, k in enumerate(g):
                        joined[k] = (gp[j], g[j + 1] - k if j + 1 < len(g) else g[j - 1] - k)
            for i, (tok, word) in enumerate(zip(toks, words)):
                base = dict(book=book, chapter=ch, verse=vs, i=i, token=tok, word=nfc(word), written=None, host=None)
                ps, host_off = joined.get(i, (parts_of(word), None))
                stats["tokens"] += 1
                if ps is not None and len(ps) == 1 and ps[0][0] in ("prefix", "suffix") and host_off is not None:
                    slot, t, form, pos, cid, _ = ps[0]        # a hyphen-written affix: a token of its own (bcv-split `written: hyphen`)
                    stats[f"hyphen {slot}"] += 1
                    rows.append({**base, "part": 0, "slot": slot, "text": t, "form": form, "position": pos, "chain_id": cid, "tone": None,
                                 "label": None, "p_split": None, "written": "hyphen", "host": host_off})
                    continue
                if ps is not None and len(ps) == 1:
                    ps = None
                if ps is None:
                    rows.append({**base, "part": 0, "slot": "whole", "text": nfc(word), "form": None, "position": None, "chain_id": None,
                             "tone": None, "label": None, "p_split": None})
                    continue
                stats["divided"] += 1
                for k, (slot, t, form, pos, cid, tone) in enumerate(ps):
                    stats[slot] += 1
                    rows.append({**base, "part": k, "slot": slot, "text": t, "form": form if slot not in ("stem", "tone") else None,
                                 "position": pos if slot not in ("stem", "tone") else None, "chain_id": cid, "tone": tone,
                                 "label": form if slot == "tone" else None, "p_split": 1.0 if k == 0 else None})
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), out / "words.parquet")
    sha = hashlib.sha256((out / "words.parquet").read_bytes()).hexdigest()
    head = {"spec": "bcv-split/0.1", "edition": tag, "tokenizer_version": usj_source.TOKENIZER_VERSION,
            "source": source,
            "basis": "derived", "numbering": "seg.S.split(written token, tones=True); part = list index; a hyphen word is split joined, each piece numbered from 0", "sha256": sha, "text_licence": text_licence, "books": books, "text_fp": fps,
            "text_fp_version": 1, "date": date.today().isoformat()}
    (out / "_split.json").write_text(json.dumps(head, ensure_ascii=False, indent=1) + "\n")
    return {**dict(stats), "rows": len(rows), "sha256": sha}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--usj", type=Path, required=True)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--source", default=None)
    ap.add_argument("--text-licence", default="see the edition's licence pointer")
    a = ap.parse_args(argv)
    src = a.source or f"bundle {a.bundle} via the split engine {bcv_segment.VERSION} (lexeme-aligner)"
    print(json.dumps(export(a.tag, a.usj, a.bundle, a.out, src, a.text_licence)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
