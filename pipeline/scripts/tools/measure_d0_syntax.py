"""D0 typology slots under BHSA vs MACULA-only syntax, on the known-answer languages (internal-docs/macula-only-migration-plan.md, P2 step 4/5).

    python pipeline/scripts/tools/measure_d0_syntax.py --langs eng,fra,rus,cmn,hin,arb

For each language: derive the language's GOLD edition only (not every edition) with ALIGNER_SYNTAX_SOURCE=bhsa and =macula, everything written to a
private scratch folder (config/constituent_order and config/gram_struct are NOT touched), print the three OT-derived slots (possessor, subject_verb,
object_verb: direction, rate, n) side by side, and run derive_typology's hard known-answer gate on each mode. A mode PASSES when no known slot resolves
to a confident direction that contradicts the textbook answer; abstentions are reported (honest, not failures).
Output: pipeline/work/measure/d0-<timestamp>/{bhsa,macula}/<iso>.json, constituent_order/<mode>/<tag>.json, table.md, results.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure_flag as mf                                          # noqa: E402 — spine_for()

REPO = Path(__file__).resolve().parents[3]
SLOTS = ("possessor", "subject_verb", "object_verb")
MODES = ("bhsa", "macula")


def slot_cell(doc: dict, slot: str) -> str:
    s = doc.get(slot)
    if not s:
        return "—"
    d = s.get("direction") or f"null({s.get('reason', '?')})"
    rate = s.get("rate_after", s.get("rate"))
    n = s.get("n", s.get("n_resolved"))
    return f"{d} {rate:.3f} n={n}" if isinstance(rate, (int, float)) else d


def agree(a: dict, b: dict, slot: str) -> str:
    """Same confident direction in both modes / differs / one abstains."""
    da = (a.get(slot) or {}).get("direction")
    db = (b.get(slot) or {}).get("direction")
    if da is None and db is None:
        return "both abstain"
    if da is None or db is None:
        return "one abstains"
    return "same" if da == db else "DIFFERENT"


def render(results: dict, gates: dict) -> str:
    rows = ["| language | slot | bhsa | macula | expected | comparison |", "|---|---|---|---|---|---|"]
    from lexeme_aligner.derive_typology import KNOWN_ANSWERS
    for iso, r in sorted(results.items()):
        for slot in SLOTS:
            exp = KNOWN_ANSWERS.get(slot, {}).get(iso, "")
            rows.append(f"| {iso} ({r['tag']}) | {slot} | {slot_cell(r['bhsa'], slot)} | {slot_cell(r['macula'], slot)} | {exp} | "
                        f"{agree(r['bhsa'], r['macula'], slot)} |")
    g = ["", "### Known-answer gate", ""]
    for m in MODES:
        x = gates[m]
        g.append(f"- **{m}**: {'PASSED' if x['passed'] else 'FAILED'} — {x['matches']} matched, {len(x['abstentions'])} abstained, "
                 f"{len(x['violations'])} violated of {x['checked']} checked"
                 + (f"; violations: {[(v['iso'], v['slot'], v['actual']) for v in x['violations']]}" if x['violations'] else "")
                 + (f"; abstained: {[(v['iso'], v['slot']) for v in x['abstentions']]}" if x['abstentions'] else ""))
    return "\n".join(rows + g) + "\n"


def derive_mode(mode: str, langs: list[str], work: Path) -> dict[str, dict]:
    import lexeme_aligner.derive_typology as dt
    from lexeme_aligner.eval.contest_rule import gold_edition
    os.environ["ALIGNER_SYNTAX_SOURCE"] = mode
    assert os.environ.get("ALIGNER_SPINE_DB") == mf.spine_for(mode), "derive_mode must run in a process started with this mode's spine"
    dt._CONSTITUENT_DIR = work / "constituent_order" / mode                 # derive_one writes the per-edition profile here
    (work / mode).mkdir(parents=True, exist_ok=True)
    manifest = dt._load_json(dt._COMPACT_MANIFEST).get("languages", {})
    out: dict[str, dict] = {}
    for iso in langs:
        eds = dt.editions_of(iso, manifest)
        if not eds:
            out[iso] = {"tag": None, "doc": {"_derived_meta": {"reason": "no_edition"}}}
            continue
        want = gold_edition(iso)
        pick = next((e for e in eds if e[0] == want or e[0].lower() == (want or "").lower()), eds[0])
        tag, _code, books = pick
        ot = [b for b in books if b in dt.OT_BOOKS]
        t0 = time.time()
        d = dt.derive_one(iso, tag, ot, books, dt.OUT, with_diagnose=False)
        doc = dt.combine_language(iso, {tag: d})
        (work / mode / f"{iso}.json").write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
        print(f"[d0-syntax] {mode} {iso} ({tag}): {time.time() - t0:.0f}s", file=sys.stderr)
        out[iso] = {"tag": tag, "doc": doc}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--langs", default="eng,fra,rus,cmn,hin,arb")
    ap.add_argument("--work", type=Path, default=None)
    ap.add_argument("--only-mode", choices=MODES, help=argparse.SUPPRESS)       # internal: the per-mode child process
    a = ap.parse_args(argv)
    sys.path.insert(0, str(REPO / "pipeline"))
    os.chdir(REPO)
    langs = [x.strip() for x in a.langs.split(",") if x.strip()]
    work = a.work or REPO / "pipeline/work/measure" / f"d0-{time.strftime('%Y%m%d-%H%M%S')}"
    if a.only_mode:                                                             # child: lexeme_aligner.config has read ALIGNER_SPINE_DB at import
        res = derive_mode(a.only_mode, langs, work)
        (work / a.only_mode / "_tags.json").write_text(json.dumps({iso: r["tag"] for iso, r in res.items()}), encoding="utf-8")
        return 0
    # parent: one subprocess per mode, because the spine path and the syntax source are fixed per process (config.SPINE_DB is bound at import)
    work.mkdir(parents=True, exist_ok=True)
    for m in MODES:
        env = dict(os.environ, ALIGNER_SYNTAX_SOURCE=m, ALIGNER_SPINE_DB=mf.spine_for(m))
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--langs", ",".join(langs), "--work", str(work), "--only-mode", m],
                           env=env, cwd=str(REPO))
        if r.returncode != 0:
            raise SystemExit(f"[d0-syntax] the {m} arm failed (exit {r.returncode})")
    from lexeme_aligner.derive_typology import check_known_answers
    docs, tags = {}, {}
    for m in MODES:
        tags[m] = json.loads((work / m / "_tags.json").read_text(encoding="utf-8"))
        docs[m] = {iso: json.loads((work / m / f"{iso}.json").read_text(encoding="utf-8")) if (work / m / f"{iso}.json").exists() else {}
                   for iso in langs}
    results = {iso: {"tag": tags["bhsa"].get(iso), "bhsa": docs["bhsa"][iso], "macula": docs["macula"][iso]} for iso in langs}
    gates = {m: check_known_answers({iso: docs[m][iso] for iso in langs}) for m in MODES}
    table = render(results, gates)
    (work / "table.md").write_text(table, encoding="utf-8")
    (work / "results.json").write_text(json.dumps({"gates": gates}, indent=1, default=str), encoding="utf-8")
    print(table)
    print(f"[d0-syntax] results: {work}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
