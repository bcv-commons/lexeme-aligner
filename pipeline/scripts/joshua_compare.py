"""Regenerate the "Joshua Alignment Compare" page: every content word of Joshua 1-4 (ASV target) under four views.

  chain now      the CURRENT chain for the ASV edition (`eng_asv`), decoded exactly the way the published
                 compact-alignments sidecar is built (`compact_align.build_compact`), so each word carries the
                 method that won it (eflomal / gloss / gapfill / spanext, plus the opt-in residual layer), its
                 confidence, the losing alternative of a contested position and the named rule behind a widening.
  earlier chain  the chain column of the previous version of this page (plain statistical chain), kept only as
                 the baseline that "improved / regressed" is measured against.
  LLM            Sonnet 5 whole-verse pass with the lexeme-verify review overlaid. NOT re-run here (no model calls).
  ASV example    the American-Standard-Version-Bible-Alignment-Data project's own alignment of the same verses.
  BSB            Bible Hub's BSB interlinear table (a different translation, corroboration only).

The last three external columns and the earlier chain come from `--external` (a JSON list of rows, extracted from
the previous page); everything about OUR current side is recomputed from the repo and `pipeline/work/out`.

    python3 pipeline/scripts/joshua_compare.py                    # writes pipeline/work/joshua-page/{data.json,index.html}
                                                                  # (template: joshua_compare_template.html, footer change log lives there)
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CHAPTERS = (1, 2, 3, 4)
TAG = "eng_asv"
LLM_FULL = "align_llm_asvjos.full.sonnet5.cli_JOS.jsonl"
LLM_REVIEW = "align_llm_asvjos.full.sonnet5.cli.lexverify_JOS.jsonl"
METHODS = ("spanext", "eflomal", "gloss", "gapfill")
CLITICS = {"s", "d", "t", "ve", "re", "ll", "m"}
STOP = {"the", "of", "and", "to", "in", "a", "an", "his", "her", "your", "my", "their", "our", "that", "is", "be", "for",
        "with", "on", "by", "it", "this", "thou", "thee", "thy", "he", "they", "them", "you", "i", "me", "we", "as", "at",
        "from", "shall", "will", "have", "had", "hath", "not", "but", "or"}
METHOD_NAME = {"E": "eflomal, both directions agree", "e": "eflomal, one direction", "G": "gloss, exact or stem match",
               "g": "gloss, weaker match", "f": "gap-fill", "r": "residual layer (opt-in)", "x": "span extension",
               "s": "statistical", "l": "LLM"}


def join_words(words: list[str]) -> str:
    """Words as shown: an English clitic fragment left by the tokenizer ("father" + "s") is re-attached with an apostrophe."""
    out: list[str] = []
    for w in words:
        if out and w in CLITICS:
            out[-1] += "'" + w
        else:
            out.append(w)
    return " ".join(out)


def canon(text: str | None) -> frozenset[str]:
    """Word set with case and every apostrophe variant removed, so "father's" equals "fathers"."""
    return frozenset(re.sub(r"[’'`]", "", w) for w in (text or "").lower().split() if w)


def verdict(a: str | None, b: str | None) -> str:
    ca, cb = canon(a), canon(b)
    if not ca or not cb:
        return "no_data"
    if ca == cb:
        return "exact"
    return "overlap" if ca & cb else "differ"


def content_overlap(a: str | None, b: str | None) -> bool | None:
    ca, cb = canon(a) - STOP, canon(b) - STOP
    if not canon(a) or not canon(b):
        return None
    return bool((ca or canon(a)) & (cb or canon(b)))


def decode_chain(heb, out_dir: Path, ingest: Path):
    """{(ref, h_idx): cell dict} for the current chain, plus {(ref,h_idx): residual text}."""
    from lexeme_aligner.compact_align import build_compact, build_layer, load_contest_rule
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM, pooled_verse_groups
    from lexeme_aligner.usj_source import read_verse_ranges, tokenize
    from lexeme_aligner.versification import remapper

    usj = ingest / f"usj-{TAG}"
    contest = load_contest_rule()
    with contextlib.redirect_stderr(io.StringIO()):
        by_ref, side = build_compact(TAG, usj, heb, out_dir, ["JOS"], METHODS, contest, cross_edition_iso="eng")
        layer = build_layer(TAG, usj, heb, out_dir, ["JOS"], base=by_ref)
    ranges = read_verse_ranges(usj / f"{_BOOK_FILE_NUM['JOS']}-JOS.json", rules={})
    remap = remapper(TAG, str(usj))

    def words(span: str, raw: list[str]) -> list[str]:
        if "-" in span:
            lo, hi = map(int, span.split("-"))
            idx = list(range(lo, hi + 1))
        elif "," in span:
            idx = [int(x) for x in span.split(",")]
        else:
            idx = [int(span)]
        return [raw[i].lower() for i in idx if 0 <= i < len(raw)]

    chain: dict[tuple, dict] = {}
    resid: dict[tuple, str] = {}
    for ch in CHAPTERS:
        for anchor_v, vs, ve, text, members in pooled_verse_groups("JOS", ch, heb, ranges, remap):
            ref = f"JOS {ch}:{anchor_v}"
            raw = tokenize(text)
            content = [t for ov, t in members if ov == anchor_v and t.strong and t.is_content]
            parts = by_ref.get(ref, "").split()
            meth = side["method"].get(ref, "")
            conf = side["conf"].get(ref, "")
            rules = {}
            for item in side["rule"].get(ref, "").split():
                o, _, rest = item.partition(":")
                rules[int(o)] = rest
            contested = {}
            for item in side["contested"].get(ref, "").split():
                o, _, rest = item.partition(":")
                m, _, sp = rest.partition(":")
                contested[int(o)] = (m, sp)
            for k, part in enumerate(parts):
                o, _, span = part.partition(":")
                o = int(o)
                if o >= len(content):
                    continue
                cell = {"text": join_words(words(span, raw)), "m": meth[k] if k < len(meth) else "?",
                        "c": conf[k] if k < len(conf) else ""}
                if o in rules:
                    cell["rule"] = rules[o]
                if o in contested:
                    m, sp = contested[o]
                    cell["alt"] = f"{METHOD_NAME.get(m[:1], m)} proposed: {join_words(words(sp, raw))}"
                chain[(ref, content[o].idx)] = cell
            for item in (layer.get(ref, "") or "").split():
                o, _, span = item.partition(":")
                if int(o) < len(content):
                    resid[(ref, content[int(o)].idx)] = join_words(words(span, raw))
    return chain, resid


def read_llm(out_dir: Path) -> dict[tuple, str]:
    d: dict[tuple, str] = {}
    for name in (LLM_FULL, LLM_REVIEW):                      # the review pass overrides the full pass where it spoke
        for line in (out_dir / name).read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            for p in r["pairs"]:
                d[(f"JOS {r['chapter']}:{r['verse']}", p["h_idx"])] = join_words(p["target"].lower().split())
    return d


def build_rows(external: Path, out_dir: Path, ingest: Path) -> list[dict]:
    from lexeme_aligner.hebrew_source import HebrewSource
    heb = HebrewSource()
    ext = {(r["ref"], r["h_idx"]): r for r in json.loads(external.read_text(encoding="utf-8"))}
    chain, resid = decode_chain(heb, out_dir, ingest)
    llm = read_llm(out_dir)
    rows = []
    for ch in CHAPTERS:
        for v in heb.verses("JOS", ch):
            ref = f"JOS {ch}:{v}"
            for pos, t in enumerate(heb.verse_tokens("JOS", ch, v)):
                if not (t.strong and t.is_content):
                    continue
                e = ext.get((ref, pos), {})
                c = chain.get((ref, pos))
                row = {"ref": ref, "h": pos, "surface": t.surface, "lemma": t.lemma, "strong": t.strong,
                       "gloss": t.gloss_en, "now": c, "resid": resid.get((ref, pos)),
                       "before": e.get("chain") or None, "llm": llm.get((ref, pos)),
                       "asv": e.get("asv_example") or None, "bsb": e.get("bsb") or None}
                now_text = (c or {}).get("text") or row["resid"]
                row["v_now"] = verdict(now_text, row["asv"])
                row["v_before"] = verdict(row["before"], row["asv"])
                row["v_llm"] = verdict(row["llm"], row["asv"])
                rank = {"differ": 0, "overlap": 1, "exact": 2}
                a, b = rank.get(row["v_now"]), rank.get(row["v_before"])
                row["delta"] = 0 if a is None or b is None else (a > b) - (a < b)
                rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    def share(key: str) -> dict:
        c = collections.Counter(r[key] for r in rows)
        judged = c["exact"] + c["overlap"] + c["differ"]
        return {"exact": c["exact"], "overlap": c["overlap"], "differ": c["differ"], "no_data": c["no_data"],
                "judged": judged, "exact_pct": 100 * c["exact"] / judged if judged else 0,
                "shared_pct": 100 * (c["exact"] + c["overlap"]) / judged if judged else 0}

    def bsb(getter) -> tuple[int, int]:
        res = [content_overlap(getter(r), r["bsb"]) for r in rows]
        res = [x for x in res if x is not None]
        return sum(res), len(res)

    by_method: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        m = (r["now"] or {}).get("m") or ("r" if r["resid"] else "-")
        by_method[m][r["v_now"]] += 1
        by_method[m]["n"] += 1
    methods = []
    for m, c in sorted(by_method.items(), key=lambda kv: -kv[1]["n"]):
        judged = c["exact"] + c["overlap"] + c["differ"]
        methods.append({"m": m, "name": METHOD_NAME.get(m, "no alignment"), "n": c["n"], "judged": judged,
                        "exact_pct": 100 * c["exact"] / judged if judged else 0,
                        "shared_pct": 100 * (c["exact"] + c["overlap"]) / judged if judged else 0})
    return {"rows": len(rows), "verses": len({r["ref"] for r in rows}),
            "now": share("v_now"), "before": share("v_before"), "llm": share("v_llm"),
            "improved": sum(1 for r in rows if r["delta"] > 0), "regressed": sum(1 for r in rows if r["delta"] < 0),
            "unaligned_now": sum(1 for r in rows if not r["now"] and not r["resid"]),
            "bsb_now": bsb(lambda r: (r["now"] or {}).get("text") or r["resid"]),
            "bsb_before": bsb(lambda r: r["before"]), "bsb_llm": bsb(lambda r: r["llm"]),
            "bsb_asv": bsb(lambda r: r["asv"]), "methods": methods}


def render_page(payload: dict, template: Path, built: str) -> str:
    """The template with the data and build date substituted in (`</` escaped so the JSON is safe inside <script>)."""
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = template.read_text(encoding="utf-8")
    return html.replace("/*DATA*/null", blob, 1).replace('/*BUILT*/""', json.dumps(built), 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--external", type=Path, default=Path("pipeline/work/joshua-page/old_page_data.json"))
    ap.add_argument("--out-dir", type=Path, default=Path("pipeline/work/out"))
    ap.add_argument("--ingest", type=Path, default=Path("pipeline/work/ingest-cache"))
    ap.add_argument("--dest", type=Path, default=Path("pipeline/work/joshua-page"))
    a = ap.parse_args(argv)
    rows = build_rows(a.external, a.out_dir, a.ingest)
    stats = summarize(rows)
    a.dest.mkdir(parents=True, exist_ok=True)
    payload = {"stats": stats, "rows": rows}
    (a.dest / "data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    import datetime
    (a.dest / "index.html").write_text(
        render_page(payload, Path(__file__).with_name("joshua_compare_template.html"), datetime.date.today().isoformat()),
        encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k != "methods"}, indent=1), file=sys.stderr)
    for m in stats["methods"]:
        print(f"  {m['m']} {m['name']:32s} n={m['n']:4d} exact {m['exact_pct']:.0f}% shared {m['shared_pct']:.0f}%",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
