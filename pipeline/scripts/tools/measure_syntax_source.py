"""A/B the syntax source (BHSA vs MACULA-only) on the gold languages — step P2 of internal-docs/macula-only-migration-plan.md.

    python pipeline/scripts/tools/measure_syntax_source.py --langs hin,spa,arb --convention-aware
    python pipeline/scripts/tools/measure_syntax_source.py --all-gold --dry-run

For every gold language: copy the finished eflomal + gloss files into a PRIVATE scratch folder (the live `pipeline/work/out` is only read), then run
the two chain steps that read BHSA-derived spine columns — `span_extension` (relation_trigger, P2 window) and `gapfill` (phrase tier, function-order
prior, construct head/dependent) — once per ARM, and score each arm against the gold with `pos_score --json`:

    bhsa           ALIGNER_SYNTAX_SOURCE=bhsa    (today's behaviour; the baseline)
    macula         ALIGNER_SYNTAX_SOURCE=macula  (BHSA columns ignored; whatever the MACULA-only code paths can do)
    bhsa-nophrase  ALIGNER_SYNTAX_SOURCE=bhsa + gapfill --no-phrase --no-func-order   (how much does the phrase tier add AT ALL?)

Reading the table: macula vs bhsa is the cost (or gain) of switching with the code as it is now; bhsa-nophrase vs bhsa is the value of the phrase
tier, i.e. the most a MACULA replacement could ever recover. Both are deterministic given the inputs, so one run per arm is enough. The eflomal-time
BHSA use (fertility possessor gate) is NOT covered here — it changes the alignment itself and needs its own replicate-based measurement.

A change is a WIN when link_f1 goes up by >= --min-gain and exact_span goes up and link precision does not fall by more than --max-precision-drop;
a LOSS when link_f1 goes down by >= --min-gain; otherwise a WASH (same thresholds as measure_flag.py). Results: pipeline/work/measure/syntax-<ts>/.
Nothing in config/ or publish/ is changed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure_flag as mf                                          # noqa: E402 — shared helpers (verdict, delta, scratch, runner)

REPO = mf.REPO
ARMS = {                                                           # arm -> (ALIGNER_SYNTAX_SOURCE, extra gapfill flags)
    "bhsa": ("bhsa", []),
    "macula": ("macula", []),
    "bhsa-nophrase": ("bhsa", ["--no-phrase", "--no-func-order"]),
}
SPEC_BASE = "eflomal+gloss"
SPEC_FULL = "spanext+gapfill+eflomal+gloss"
Runner = Callable[[list[str], dict], str]


# ---- pure helpers (unit-tested) -------------------------------------------------------------------------------------------------
def arm_env(arm: str, base: dict | None = None) -> dict:
    """Environment for one arm: ALIGNER_SYNTAX_SOURCE set, everything else inherited."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; one of {tuple(ARMS)}")
    env = dict(os.environ if base is None else base)
    env["ALIGNER_SYNTAX_SOURCE"] = ARMS[arm][0]
    env["ALIGNER_SPINE_DB"] = mf.spine_for(ARMS[arm][0])         # bhsa arms need the private baseline spine (the default spine has no BHSA columns)
    return env


def gapfill_cmd(py: str, tag: str, lang: str, usj: Path, scratch: Path, scope: str, arm: str) -> list[str]:
    return [py, "-m", "lexeme_aligner.gapfill", "--iso", tag, "--publish-iso", lang, "--usj-dir", str(usj), mf.scope_arg(scope),
            "--methods", "eflomal,gloss,spanext", "--out", str(scratch), *ARMS[arm][1]]


def _clear(scratch: Path, tag: str) -> None:
    for kind in ("spanext", "gapfill"):                            # the next arm must not read the previous arm's outputs
        for fp in scratch.glob(f"align_{kind}_{tag}_*"):
            fp.unlink()


def has_gold(score: dict) -> bool:
    """A scoring block that found gold links in scope. Zero gold links (e.g. an NT-only gold scored with --ot) gives F1 0.0000 in every arm,
    which would otherwise read as a 'wash' — it is NO DATA."""
    return bool(score.get("gold_links"))


def verdict_row(base: dict, other: dict, min_gain: float, max_drop: float) -> dict:
    if not has_gold(base) or not has_gold(other):
        return {"delta": mf.delta({}, {}), "verdict": "no gold"}
    dl = mf.delta(base, other)
    return {"delta": dl, "verdict": mf.verdict(dl, min_gain, max_drop)}


def render_table(results: dict[str, dict], scope: str, min_gain: float, conv: bool) -> str:
    f = lambda v: "—" if v is None else f"{v:.4f}"                  # noqa: E731
    sg = lambda v: "—" if v is None else f"{v:+.4f}"                # noqa: E731
    head = (f"### BHSA vs MACULA-only syntax, {scope}, vs gold (spanext+gapfill over eflomal+gloss; deterministic: one run per arm)\n\n"
            "| language | tag | no-spanext/gapfill F1 | bhsa F1 | macula F1 | Δ macula−bhsa | Δexact_span | Δprec | verdict | "
            "no-phrase F1 | Δ nophrase−bhsa | verdict |\n|---|---|---|---|---|---|---|---|---|---|---|---|\n")
    rows, measured = [], {}
    for lang, r in sorted(results.items()):
        if r.get("skipped"):
            rows.append(f"| {lang} | — | — | — | — | — | — | — | skipped: {r['skipped']} | — | — | — |")
            continue
        s = r["strict"]
        m, n = s["macula_vs_bhsa"], s["nophrase_vs_bhsa"]
        if m["verdict"] == "no gold":
            rows.append(f"| {lang} | {r['tag']} | — | — | — | — | — | — | **no gold in this scope** (0 gold links) | — | — | — |")
            continue
        measured[lang] = (m["verdict"], n["verdict"])
        rows.append(f"| {lang} | {r['tag']} | {f(s['baseline'].get('link_f1'))} | {f(s['bhsa'].get('link_f1'))} | {f(s['macula'].get('link_f1'))} | "
                    f"{sg(m['delta']['link_f1'])} | {m['delta']['exact_span'] if m['delta']['exact_span'] is not None else '—'} | "
                    f"{sg(m['delta']['link_precision'])} | **{m['verdict']}** | {f(s['bhsa-nophrase'].get('link_f1'))} | "
                    f"{sg(n['delta']['link_f1'])} | {n['verdict']} |")
    n_ok = len(measured)
    cnt = lambda i, v: sum(1 for x in measured.values() if x[i] == v)          # noqa: E731
    n_nogold = sum(1 for r in results.values() if not r.get("skipped") and r["strict"]["macula_vs_bhsa"]["verdict"] == "no gold")
    foot = (f"\n\n{n_ok} language(s) measured" + (f", {n_nogold} with no gold in this scope (not counted)" if n_nogold else "") + f". macula vs bhsa: {cnt(0, 'WIN')} win, {cnt(0, 'LOSS')} loss, {cnt(0, 'wash')} wash. "
            f"phrase tier switched off vs bhsa: {cnt(1, 'WIN')} win, {cnt(1, 'LOSS')} loss, {cnt(1, 'wash')} wash (min_gain {min_gain}).\n")
    if conv:
        foot += "\n[conv] rows (gold that never credits function words is neutral there) are in results.json under `conv`.\n"
    return head + "\n".join(rows) + foot


# ---- orchestration (runner injected, so tests need no chain) --------------------------------------------------------------------
def measure_language(lang: str, tag: str, usj: Path, scratch: Path, scope: str, conv: bool, run: Runner, py: str,
                     min_gain: float, max_drop: float, arms: tuple[str, ...] = tuple(ARMS)) -> dict:
    scored: dict[str, dict] = {}
    for arm in arms:
        env = arm_env(arm)
        _clear(scratch, tag)
        run(mf.span_extension_cmd(py, tag, lang, usj, scratch, "relation_trigger", True, scope)[:-1], env)   # flag left at the config default
        run(gapfill_cmd(py, tag, lang, usj, scratch, scope, arm), env)
        specs = [SPEC_BASE, SPEC_FULL] if arm == arms[0] else [SPEC_FULL]
        scored[arm] = mf._json_from(run(mf.pos_score_cmd(py, tag, lang, usj, scratch, specs, scope, conv), env))["results"]
        if arm == arms[0]:
            scored["_baseline"] = scored[arm]
    _clear(scratch, tag)

    def block(suffix: str) -> dict:
        base = scored["_baseline"].get(SPEC_BASE + suffix, {})
        got = {a: scored[a].get(SPEC_FULL + suffix, {}) for a in arms}
        out = {"baseline": base, **got}
        if "bhsa" in got and "macula" in got:
            out["macula_vs_bhsa"] = verdict_row(got["bhsa"], got["macula"], min_gain, max_drop)
        if "bhsa" in got and "bhsa-nophrase" in got:
            out["nophrase_vs_bhsa"] = verdict_row(got["bhsa"], got["bhsa-nophrase"], min_gain, max_drop)
        return out
    res = {"tag": tag, "strict": block("")}
    if conv:
        res["conv"] = block(" [conv]")
    return res


def default_runner(cmd: list[str], env: dict) -> str:
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, env=env)
    if r.returncode != 0:
        last = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
        raise RuntimeError(f"{' '.join(cmd[2:4])} exited {r.returncode}: {last}")
    return r.stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--langs", help="comma-separated gold languages")
    g.add_argument("--all-gold", action="store_true")
    ap.add_argument("--scope", choices=("all", "nt", "ot"), default="all")
    ap.add_argument("--convention-aware", action="store_true")
    ap.add_argument("--min-gain", type=float, default=0.002)
    ap.add_argument("--max-precision-drop", type=float, default=0.01)
    ap.add_argument("--work", type=Path, default=None)
    ap.add_argument("--force", action="store_true", help="measure a language even if a chain for it is running")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    sys.path.insert(0, str(REPO / "pipeline"))
    from lexeme_aligner.eval.contest_rule import gold_edition
    langs = mf.gold_languages() if a.all_gold else [x.strip() for x in a.langs.split(",") if x.strip()]
    work = a.work or REPO / "pipeline/work/measure" / f"syntax-{time.strftime('%Y%m%d-%H%M%S')}"
    busy = mf.running_names(subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout)
    results: dict[str, dict] = {}
    for lang in langs:
        tag = gold_edition(lang)
        usj = mf.INGEST / f"usj-{tag}" if tag else None
        why = None
        if not tag:
            why = "no gold edition recorded"
        elif not usj.is_dir():
            why = f"edition {tag} not ingested"
        elif (lang in busy or tag in busy) and not a.force:
            why = "a chain is running for it (files being rewritten); use --force or wait"
        if why:
            results[lang] = {"skipped": why}
            print(f"[measure-syntax] {lang}: skipped — {why}", file=sys.stderr)
            continue
        if a.dry_run:
            print(f"[measure-syntax] {lang} ({tag}): would run arms {', '.join(ARMS)} in {work / lang}", file=sys.stderr)
            continue
        scratch = work / lang
        n = mf.prepare_scratch(tag, scratch)
        if n == 0:
            results[lang] = {"skipped": f"no finished eflomal/gloss files for {tag}"}
            print(f"[measure-syntax] {lang}: skipped — no eflomal/gloss files for {tag}", file=sys.stderr)
            continue
        print(f"[measure-syntax] {lang} ({tag}): {n} input files copied; running {len(ARMS)} arms", file=sys.stderr)
        t0 = time.time()
        try:
            results[lang] = measure_language(lang, tag, usj, scratch, a.scope, a.convention_aware, default_runner, sys.executable,
                                             a.min_gain, a.max_precision_drop)
            print(f"[measure-syntax] {lang}: done in {time.time() - t0:.0f}s", file=sys.stderr)
        except Exception as e:                                       # noqa: BLE001 — one language must not stop the table
            results[lang] = {"skipped": f"failed: {e}"}
            print(f"[measure-syntax] {lang}: {e}", file=sys.stderr)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    if a.dry_run:
        return 0
    work.mkdir(parents=True, exist_ok=True)
    (work / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    table = render_table(results, a.scope, a.min_gain, a.convention_aware)
    (work / "table.md").write_text(table, encoding="utf-8")
    print(table)
    print(f"[measure-syntax] results: {work}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
