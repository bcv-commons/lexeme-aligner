"""Fertility possessor gate, BHSA vs MACULA-only — eflomal-time replicate measurement (internal-docs/macula-only-migration-plan.md section 7, item 1).

    python pipeline/scripts/tools/measure_fertility_syntax.py --langs hin,eng,spa --reps 3 --convention-aware

The fertility priors seed eflomal itself, so unlike span_extension the result changes the alignment and eflomal is UNSEEDED (about 1% run-to-run
drift): every arm is run `--reps` times in a private scratch folder (live `out/` and config untouched) and judged against its own replicate spread.

Arms per language (the language's config/fertility_flags.json entry is used as it is — lambda, lexeme_targets, typology_fallback):
    bhsa    ALIGNER_SYNTAX_SOURCE=bhsa    fertility on  (today's behaviour)
    macula  ALIGNER_SYNTAX_SOURCE=macula  fertility on  (possessor gate = construct_role rectum, content tokens)
    off     fertility priors disabled     (what the gate is worth at all)

Verdicts (on link_f1 of `pos_score --method eflomal --ot`; tolerance = 2 x the larger replicate spread of the two arms compared, never below 0.0005):
    macula vs bhsa : NEUTRAL when |mean difference| <= tolerance, else BETTER / WORSE
    bhsa vs off    : the value of the fertility mechanism itself (HELPS / no effect / HURTS), same tolerance
Results: pipeline/work/measure/fert-<timestamp>/{results.json,table.md}. Nothing in config/ or publish/ changes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure_flag as mf                                          # noqa: E402

REPO = mf.REPO
ARMS = {"bhsa": ("bhsa", []), "macula": ("macula", []), "off": ("bhsa", ["--no-fertility-priors"])}
MIN_TOL = 0.0005
Runner = Callable[[list[str], dict], tuple[str, str]]


# ---- pure helpers (unit-tested) -------------------------------------------------------------------------------------------------
def arm_env(arm: str, base: dict | None = None) -> dict:
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; one of {tuple(ARMS)}")
    env = dict(os.environ if base is None else base)
    env["ALIGNER_SYNTAX_SOURCE"] = ARMS[arm][0]
    env["ALIGNER_SPINE_DB"] = mf.spine_for(ARMS[arm][0])         # bhsa arms need the private baseline spine (the default spine has no BHSA columns)
    return env


def pilot_cmd(py: str, tag: str, lang: str, usj: Path, out: Path, arm: str) -> list[str]:
    return [py, "-m", "lexeme_aligner.run_pilot", "--method", "eflomal", "--ot", "--usj-dir", str(usj), "--iso", tag,
            "--publish-iso", lang, "--out", str(out), *ARMS[arm][1]]


def score_cmd(py: str, tag: str, lang: str, usj: Path, out: Path, conv: bool) -> list[str]:
    return mf.pos_score_cmd(py, tag, lang, usj, out, ["eflomal"], "ot", conv)


def flagged_anchors(text: str) -> int | None:
    """'[pilot] Step 3 fertility priors: 2383 anchor(s) flagged ...' -> 2383 (None when fertility was off)."""
    m = re.search(r"fertility priors: (\d+) anchor", text)
    return int(m.group(1)) if m else None


def summarize(values: list[float]) -> dict:
    return {"n": len(values), "mean": statistics.fmean(values) if values else None,
            "spread": (max(values) - min(values)) if values else None, "values": values}


def tolerance(a: dict, b: dict) -> float:
    return max(MIN_TOL, 2 * max(a["spread"] or 0.0, b["spread"] or 0.0))


def compare(a: dict, b: dict, up: str, down: str, flat: str) -> dict:
    """b relative to a, judged against 2 x the larger replicate spread."""
    if a["mean"] is None or b["mean"] is None:
        return {"delta": None, "tolerance": None, "verdict": "no data"}
    d, tol = b["mean"] - a["mean"], tolerance(a, b)
    return {"delta": d, "tolerance": tol, "verdict": up if d > tol else down if d < -tol else flat}


def render_table(results: dict[str, dict], reps: int) -> str:
    f = lambda v: "—" if v is None else f"{v:.4f}"                  # noqa: E731
    sg = lambda v: "—" if v is None else f"{v:+.4f}"                # noqa: E731
    head = (f"### Fertility possessor gate, BHSA vs MACULA-only — OT eflomal vs gold, {reps} replicates per arm\n\n"
            "| language | gold links | off F1 (spread) | bhsa F1 (spread) | macula F1 (spread) | Δ macula−bhsa | tolerance (2×spread) | macula vs bhsa | "
            "fertility value (bhsa−off) | anchors flagged bhsa / macula |\n|---|---|---|---|---|---|---|---|---|---|\n")
    rows = []
    for lang, r in sorted(results.items()):
        if r.get("skipped"):
            rows.append(f"| {lang} | — | — | — | — | — | — | skipped: {r['skipped']} | — | — |")
            continue
        s = r["strict"]
        cell = lambda a: f"{f(s[a]['mean'])} ({f(s[a]['spread'])})"  # noqa: E731
        mb, bo = s["macula_vs_bhsa"], s["bhsa_vs_off"]
        rows.append(f"| {lang} | {r.get('gold_links')} | {cell('off')} | {cell('bhsa')} | {cell('macula')} | {sg(mb['delta'])} | {f(mb['tolerance'])} | "
                    f"**{mb['verdict']}** | {sg(bo['delta'])} → {bo['verdict']} | {r['anchors'].get('bhsa')} / {r['anchors'].get('macula')} |")
    n_neutral = sum(1 for r in results.values() if not r.get("skipped") and r["strict"]["macula_vs_bhsa"]["verdict"] == "NEUTRAL")
    n = sum(1 for r in results.values() if not r.get("skipped"))
    return head + "\n".join(rows) + f"\n\n{n} language(s) measured, {n_neutral} NEUTRAL.\n"


# ---- orchestration (runner injected, so tests need no eflomal) ------------------------------------------------------------------
def measure_language(lang: str, tag: str, usj: Path, work: Path, reps: int, conv: bool, run: Runner, py: str,
                     arms: tuple[str, ...] = tuple(ARMS)) -> dict:
    f1: dict[str, list[float]] = {a: [] for a in arms}
    f1c: dict[str, list[float]] = {a: [] for a in arms}
    anchors: dict[str, int | None] = {}
    gold_links = None
    for rep in range(reps):
        for arm in arms:                                            # interleaved, so slow drift (load, thermal) hits every arm alike
            out = work / f"{arm}-{rep}"
            shutil.rmtree(out, ignore_errors=True)
            out.mkdir(parents=True, exist_ok=True)
            try:
                stdout, stderr = run(pilot_cmd(py, tag, lang, usj, out, arm), arm_env(arm))
                anchors.setdefault(arm, flagged_anchors(stdout + stderr))
                scored = mf._json_from(run(score_cmd(py, tag, lang, usj, out, conv), arm_env(arm))[0])["results"]
            finally:
                shutil.rmtree(out, ignore_errors=True)
            row = scored.get("eflomal", {})
            gold_links = row.get("gold_links", gold_links)
            f1[arm].append(row.get("link_f1"))
            if conv:
                f1c[arm].append(scored.get("eflomal [conv]", {}).get("link_f1"))
    if not gold_links:
        return {"skipped": "no gold links in the OT for this language"}

    def block(series: dict) -> dict:
        s = {a: summarize([v for v in series[a] if v is not None]) for a in arms}
        out = dict(s)
        if "bhsa" in s and "macula" in s:
            out["macula_vs_bhsa"] = compare(s["bhsa"], s["macula"], "BETTER", "WORSE", "NEUTRAL")
        if "bhsa" in s and "off" in s:
            out["bhsa_vs_off"] = compare(s["off"], s["bhsa"], "HELPS", "HURTS", "no effect")
        return out
    res = {"tag": tag, "gold_links": gold_links, "anchors": anchors, "strict": block(f1)}
    if conv:
        res["conv"] = block(f1c)
    return res


def default_runner(cmd: list[str], env: dict) -> tuple[str, str]:
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, env=env)
    if r.returncode != 0:
        last = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
        raise RuntimeError(f"{' '.join(cmd[2:4])} exited {r.returncode}: {last}")
    return r.stdout, r.stderr


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--langs", default="hin,eng,spa", help="comma-separated gold languages (fertility is enabled for hin, eng, spa)")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--convention-aware", action="store_true")
    ap.add_argument("--work", type=Path, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    sys.path.insert(0, str(REPO / "pipeline"))
    from lexeme_aligner.eval.contest_rule import gold_edition
    work = a.work or REPO / "pipeline/work/measure" / f"fert-{time.strftime('%Y%m%d-%H%M%S')}"
    busy = mf.running_names(subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout)
    results: dict[str, dict] = {}
    for lang in [x.strip() for x in a.langs.split(",") if x.strip()]:
        tag = gold_edition(lang)
        usj = mf.INGEST / f"usj-{tag}" if tag else None
        why = None
        if not tag:
            why = "no gold edition recorded"
        elif not usj.is_dir():
            why = f"edition {tag} not ingested"
        elif (lang in busy or tag in busy) and not a.force:
            why = "a chain is running for it"
        if why:
            results[lang] = {"skipped": why}
            print(f"[measure-fert] {lang}: skipped — {why}", file=sys.stderr)
            continue
        if a.dry_run:
            print(f"[measure-fert] {lang} ({tag}): {a.reps} replicates x {len(ARMS)} arms in {work / lang}", file=sys.stderr)
            continue
        t0 = time.time()
        try:
            results[lang] = measure_language(lang, tag, usj, work / lang, a.reps, a.convention_aware, default_runner, sys.executable)
            print(f"[measure-fert] {lang}: done in {time.time() - t0:.0f}s", file=sys.stderr)
        except Exception as e:                                       # noqa: BLE001
            results[lang] = {"skipped": f"failed: {e}"}
            print(f"[measure-fert] {lang}: {e}", file=sys.stderr)
        finally:
            shutil.rmtree(work / lang, ignore_errors=True)
    if a.dry_run:
        return 0
    work.mkdir(parents=True, exist_ok=True)
    (work / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    table = render_table(results, a.reps)
    (work / "table.md").write_text(table, encoding="utf-8")
    print(table)
    print(f"[measure-fert] results: {work}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
