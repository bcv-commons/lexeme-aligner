"""The eflomal-vs-gloss contest, decided and judged per source WORD (measurement only; nothing here is written to config).

`contest_rule` decides per spine token and judges by dictionary hits (is one of our target words among the gold's surfaces for that
Strong's). The spine splits a Hebrew word into morphemes, and the gold puts a phrase on whichever morpheme it likes, so both the unit
of the decision and the judge are grain-dependent. Here a CONTEST is a source word whose pooled eflomal span and pooled gloss span
differ, the key is the pair of confidence tiers of the word's first content token under each method, and a method WINS a contest when
its span is closer (Jaccard) to the gold's word span. Leave-one-out over the positional gold languages, the same discipline as
`contest_rule`; the committed token-level rule is applied to the same contests for comparison.

    .venv/bin/python -m lexeme_aligner.eval.contest_words --langs eng,spa,fra --ot
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.compact_align import _merge_tier as _tier
from lexeme_aligner.config import OUT
from lexeme_aligner.eval.pos_score import gold_by_word, load_gold, load_spine, tokens_by_ref

RULE = Path("config/contest_rule.json")


def word_spans(tag: str, method: str, out_dir: Path, books: list[str], spine) -> dict[int, dict[str, tuple[set[int], str | None]]]:
    """{ref: {word id: (union of the target positions of its tokens, tier of its first content token)}}."""
    from lexeme_aligner.refs import BOOK_NUMBERS
    wanted = {BOOK_NUMBERS[b] for b in books}
    out: dict[int, dict[str, tuple[set[int], str | None]]] = {}
    for fp in tag_files(out_dir, method, tag):
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                ref = rec["ref"]
                if ref // 1_000_000 not in wanted or ref not in spine.key_of:
                    continue
                wo = spine.word_of[ref]
                verse: dict[str, list] = {}
                for p in sorted(rec["pairs"], key=lambda p: p["h_idx"]):
                    key = spine.key_of[ref].get(p["h_idx"])
                    if key is None or not p.get("t_idx"):
                        continue
                    wid = wo.get(key)
                    if wid is None:
                        continue
                    cur = verse.setdefault(wid, [set(), None])
                    cur[0] |= set(p["t_idx"])
                    if cur[1] is None and p.get("content"):
                        cur[1] = _tier(method, p)
                out[ref] = {w: (v[0], v[1]) for w, v in verse.items()}
    return out


def jaccard(a: set[int], b: set[int]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def contests(iso: str, tag: str, usj_dir: Path, base_text: str, books: list[str], out_dir: Path, drop_function: bool,
             publish_iso: str) -> list[dict]:
    """One dict per contested judged word: the two tiers (`et`, `gt`), the two Jaccards to the gold (`je`, `jg`) and the gold-free
    features a rule could key on: `nm` morphemes of the word (1/2/3+), `rel` how the two spans relate (ef_in_gl / gl_in_ef / overlap /
    disjoint), `ne`/`ng` span sizes (1 / 2 / 3+), `va` share of the verse's judged words where the methods agree (low/mid/high),
    `cont` whether the word is the only content token of the word group."""
    from lexeme_aligner.versification import remapper
    gold, _stats = load_gold(publish_iso, usj_dir, books, base_text, remap=remapper(tag, str(usj_dir)))
    spine = load_spine(books, usj_dir, tag)
    ef = word_spans(tag, "eflomal", out_dir, books, spine)
    gl = word_spans(tag, "gloss", out_dir, books, spine)
    fn: dict[int, set[int]] = {}
    if drop_function:
        from lexeme_aligner.target_stopwords import StopwordFilter
        stop = StopwordFilter(publish_iso, str(usj_dir))
        from lexeme_aligner.eval.pos_score import spine_ref_of_target
        inv = spine_ref_of_target(books, remapper(tag, str(usj_dir)))          # target verse -> spine verse (gold is keyed by spine verse)
        toks = tokens_by_ref(usj_dir, books)
        fn = {inv.get(tref, tref): {i for i, w in enumerate(t) if stop.is_function(w)} for tref, t in toks.items()}
    rows = []
    for ref, gv in gold.items():
        if ref not in spine.key_of or ref not in ef or ref not in gl:
            continue
        gw, judged, _amb = gold_by_word(gv, ref, spine, content_only=True)
        drop = fn.get(ref, set()) if drop_function else set()
        n_morph = collections.Counter(spine.word_of[ref].values())
        n_content = collections.Counter(spine.word_of[ref][k] for k in spine.content[ref] if k in spine.word_of[ref])
        both = [w for w in judged if w in ef[ref] and w in gl[ref]]
        agree = sum(1 for w in both if ef[ref][w][0] - drop == gl[ref][w][0] - drop)
        va = agree / len(both) if both else 0.0
        va_b = "low" if va < 0.5 else "mid" if va < 0.8 else "high"
        for wid in judged:
            e, g = ef[ref].get(wid), gl[ref].get(wid)
            if not e or not g:
                continue
            ep, gp, goldp = e[0] - drop, g[0] - drop, gw[wid] - drop
            if ep == gp or not goldp or not (ep or gp) or e[1] is None or g[1] is None:
                continue
            rel = "ef_in_gl" if ep < gp else "gl_in_ef" if gp < ep else "overlap" if ep & gp else "disjoint"
            size = lambda x: min(len(x), 3)                                  # noqa: E731
            rows.append({"et": e[1], "gt": g[1], "je": jaccard(ep, goldp), "jg": jaccard(gp, goldp),
                         "nm": min(n_morph[wid], 3), "rel": rel, "ne": size(ep), "ng": size(gp), "va": va_b,
                         "cont": n_content[wid] == 1})
    return rows


def learn(data: dict[str, list], exclude: str | None = None, min_n: int = 15, feats: tuple[str, ...] = ()) -> dict:
    """Rule per key = (ef tier, gloss tier, *feature values): 'gl' when gloss wins more exclusive contests (Jaccard) and the cell holds
    at least `min_n` decided contests, else the coarser tier-pair decision (sparse cells fall back, never guess)."""
    fine = collections.defaultdict(lambda: [0, 0])
    coarse = collections.defaultdict(lambda: [0, 0])
    for iso, rows in data.items():
        if iso == exclude:
            continue
        for r in rows:
            w = 0 if r["je"] > r["jg"] else 1 if r["jg"] > r["je"] else None
            if w is None:
                continue
            fine[(r["et"], r["gt"], *(r[f] for f in feats))][w] += 1
            coarse[(r["et"], r["gt"])][w] += 1
    pick = lambda c, ok: "gl" if c[1] > c[0] and c[0] + c[1] >= ok else "ef"           # noqa: E731
    rule = {k: pick(c, min_n) for k, c in coarse.items()}
    if feats:
        rule = {"coarse": rule, **{k: pick(c, min_n) for k, c in fine.items() if c[0] + c[1] >= min_n}}
    return rule


def decide(rule: dict, r: dict, feats: tuple[str, ...] = ()) -> str:
    if feats:
        return rule.get((r["et"], r["gt"], *(r[f] for f in feats)), rule["coarse"].get((r["et"], r["gt"]), "ef"))
    return rule.get((r["et"], r["gt"]), "ef")


def evaluate(rows: list, pick) -> tuple[float, float]:
    """(mean Jaccard of the pick, share of picks that equal the gold exactly)."""
    j = x = 0.0
    for r in rows:
        v = r["je"] if pick(r) == "ef" else r["jg"]
        j += v
        x += v == 1.0
    return j / max(1, len(rows)), x / max(1, len(rows))


FEATURE_SETS = [(), ("nm",), ("rel",), ("ne", "ng"), ("va",), ("cont",), ("rel", "va"), ("rel", "ne", "ng"), ("rel", "nm", "va")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--langs", help="comma list of gold languages (config/gold_langs.json keys)")
    ap.add_argument("--ot", action="store_true"); ap.add_argument("--nt", action="store_true")
    ap.add_argument("--keep-function", action="store_true", help="do not drop the target's function words before comparing spans")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--json", type=Path, help="write the per-language contest rows here")
    ap.add_argument("--from-json", type=Path, help="read rows written by --json instead of recomputing them")
    a = ap.parse_args(argv)
    if a.from_json:
        data = json.loads(a.from_json.read_text(encoding="utf-8"))
    else:
        from lexeme_aligner.eval.contest_rule import _TAG, gold_base_text, gold_usj_dir
        from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS
        books = (OT_BOOKS if a.ot else []) + (NT_BOOKS if a.nt else [])
        if not books or not a.langs:
            ap.error("pass --langs and --ot and/or --nt (or --from-json)")
        data = {}
        for iso in a.langs.split(","):
            data[iso] = contests(iso, _TAG[iso], Path(gold_usj_dir(iso)), gold_base_text(iso), books, a.out, not a.keep_function, iso)
            print(f"  {iso}: {len(data[iso])} contested words", flush=True)
        if a.json:
            a.json.write_text(json.dumps(data), encoding="utf-8")
    committed = {tuple(k.split(" | ")): v for k, v in json.loads(RULE.read_text(encoding="utf-8")).items()}
    n_all = sum(len(r) for r in data.values())
    print(f"\n{n_all} contested words in {len(data)} languages. Leave-one-out, contested-word weighted (mean Jaccard / exact share):")
    base = {"always-ef": lambda r: "ef", "committed": lambda r: committed.get((r["et"], r["gt"]), "ef")}
    res: dict[str, dict[str, tuple[float, float]]] = {}
    for name, fn in base.items():
        res[name] = {iso: evaluate(rows, fn) for iso, rows in data.items()}
    for feats in FEATURE_SETS:
        name = "tiers re-fit" if not feats else "tiers + " + "+".join(feats)
        res[name] = {}
        for iso, rows in data.items():
            rule = learn(data, exclude=iso, feats=feats)
            res[name][iso] = evaluate(rows, lambda r, rule=rule, feats=feats: decide(rule, r, feats))
    res["oracle"] = {iso: (sum(max(r["je"], r["jg"]) for r in rows) / max(1, len(rows)), 0.0) for iso, rows in data.items()}
    langs = list(data)
    print(f"  {'rule':26}" + "".join(f"{i:>7}" for i in langs) + f"{'ALL J':>8}{'ALL exact':>10}{'worse langs':>12}")
    for name, per in res.items():
        j = sum(per[i][0] * len(data[i]) for i in langs) / n_all
        x = sum(per[i][1] * len(data[i]) for i in langs) / n_all
        worse = sum(per[i][0] < res["committed"][i][0] - 0.002 for i in langs) if name != "oracle" else 0
        print(f"  {name:26}" + "".join(f"{per[i][0]:>7.3f}" for i in langs) + f"{j:>8.3f}{x:>10.3f}{worse:>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
