"""A/B-measure one span_extension flag on the gold languages (grammar-phase protocol, step 4 — 2026-10-03).

    python pipeline/scripts/tools/measure_flag.py --flag typology_fallback --langs spa,ben,asm,hin --convention-aware
    python pipeline/scripts/tools/measure_flag.py --flag typology_fallback --all-gold --dry-run        # print the plan, run nothing

For every gold language: take the language's finished eflomal + gloss alignment files, copy them into a PRIVATE scratch folder (the live
`pipeline/work/out` is only read), run `span_extension` there with the flag off and with it on, score each result against the gold with `pos_score`
(`--json`), and report the difference. Span extension is deterministic given its inputs, so one run per variant is enough — the
"two baseline replicates" of the protocol are only needed for changes made at eflomal time (fertility priors).

A flag is called a WIN for a language when link_f1 goes up by at least --min-gain AND exact_span goes up AND link precision does not fall by more
than --max-precision-drop; a LOSS when link_f1 goes down by at least --min-gain; otherwise a WASH. With --convention-aware the verdict also reports the
`[conv]` rows (gold that never credits function words is neutral there), which is the fair number for spa/fra/ben/asm-style gold.
NOT automated here (still a manual step of the protocol): splitting a regression into gold-unclaimed vs real conflict; for a prior-shaped change a null
control and an inverted placebo.

Results go to `pipeline/work/measure/<flag>-<timestamp>/{results.json,table.md}`. Nothing in the repo's config is changed — a verdict is only a
number until the owner writes it into `config/spanext_flags.json`.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[3]
FLAGS = ("typology_fallback", "typology_fallback_articles", "definite_trigger", "relation_trigger")
LIVE_OUT = REPO / "pipeline/work/out"
INGEST = REPO / "pipeline/work/ingest-cache"
BASE_METHODS = ("eflomal", "gloss")
BASELINE_SPINE = REPO / "pipeline/lexeme-spine-bhsa-baseline.db"      # private BHSA arm — never published, never the source of a published artifact
MACULA_SPINE = REPO / "pipeline/lexeme-spine-macula.db"


def spine_for(syntax_source: str) -> str:
    """ALIGNER_SPINE_DB for an A/B arm: the BHSA arm reads the private baseline spine, the MACULA arm the MACULA-only spine."""
    return str(BASELINE_SPINE if syntax_source == "bhsa" else MACULA_SPINE)


# ---- pure helpers (unit-tested) -------------------------------------------------------------------------------------------------
def flag_arg(flag: str, on: bool) -> str:
    """The span_extension CLI spelling (argparse.BooleanOptionalAction): --typology-fallback / --no-typology-fallback."""
    if flag not in FLAGS:
        raise ValueError(f"unknown flag {flag!r}; one of {FLAGS}")
    return ("--" if on else "--no-") + flag.replace("_", "-")


def scope_arg(scope: str) -> str:
    if scope not in ("all", "nt", "ot"):
        raise ValueError(scope)
    return f"--{scope}"


def span_extension_cmd(py: str, tag: str, lang: str, usj: Path, scratch: Path, flag: str, on: bool, scope: str) -> list[str]:
    return [py, "-m", "lexeme_aligner.span_extension", "--iso", tag, "--publish-iso", lang, "--usj-dir", str(usj), scope_arg(scope),
            "--methods", ",".join(BASE_METHODS), "--out", str(scratch), flag_arg(flag, on)]


def pos_score_cmd(py: str, tag: str, lang: str, usj: Path, scratch: Path, specs: list[str], scope: str, conv: bool) -> list[str]:
    cmd = [py, "-m", "lexeme_aligner.eval.pos_score", "--iso", tag, "--publish-iso", lang, "--usj-dir", str(usj), scope_arg(scope),
           "--out", str(scratch), "--json"]
    for s in specs:
        cmd += ["--method", s]
    return cmd + (["--convention-aware"] if conv else [])


def running_names(ps_text: str) -> set[str]:
    """Languages / edition tags a running aligner process works on (--iso X, --publish-iso X)."""
    return set(re.findall(r"--(?:publish-)?iso ([A-Za-z0-9_]+)", ps_text))


def delta(off: dict, on: dict) -> dict:
    """on - off for the numbers the verdict uses (None when either side is missing)."""
    def d(k):
        return None if off.get(k) is None or on.get(k) is None else on[k] - off[k]
    return {"link_f1": d("link_f1"), "link_precision": d("link_precision"), "link_recall": d("link_recall"), "exact_span": d("exact_span"),
            "overlap": d("overlap"), "aer": d("aer")}


def verdict(dl: dict, min_gain: float = 0.002, max_precision_drop: float = 0.01) -> str:
    f1, ex, pr = dl.get("link_f1"), dl.get("exact_span"), dl.get("link_precision")
    if f1 is None or ex is None:
        return "no data"
    if f1 >= min_gain and ex > 0 and (pr is None or pr >= -max_precision_drop):
        return "WIN"
    if f1 <= -min_gain:
        return "LOSS"
    return "wash"


def render_table(results: dict[str, dict], flag: str, scope: str, min_gain: float) -> str:
    """Markdown: one row per language, strict rows and (when present) [conv] rows."""
    hdr = (f"### `{flag}` — off vs on, {scope}, vs gold (span_extension is deterministic: one run per variant)\n\n"
           f"| language | tag | baseline F1 | off F1 | on F1 | ΔF1 | Δexact_span | Δprecision | verdict | Δ[conv] F1 | [conv] verdict |\n"
           f"|---|---|---|---|---|---|---|---|---|---|---|\n")
    rows = []
    for lang, r in sorted(results.items()):
        if r.get("skipped"):
            rows.append(f"| {lang} | — | — | — | — | — | — | — | skipped: {r['skipped']} | — | — |")
            continue
        s, c = r["strict"], r.get("conv")
        f = lambda v: "—" if v is None else f"{v:.4f}"          # noqa: E731
        sg = lambda v: "—" if v is None else f"{v:+.4f}"        # noqa: E731
        cdl = c["delta"] if c else None
        rows.append(f"| {lang} | {r['tag']} | {f(s['baseline'].get('link_f1'))} | {f(s['off'].get('link_f1'))} | {f(s['on'].get('link_f1'))} | "
                    f"{sg(s['delta']['link_f1'])} | {s['delta']['exact_span'] if s['delta']['exact_span'] is not None else '—'} | "
                    f"{sg(s['delta']['link_precision'])} | **{s['verdict']}** | {sg(cdl['link_f1']) if cdl else '—'} | {c['verdict'] if c else '—'} |")
    wins = sum(1 for r in results.values() if not r.get("skipped") and r["strict"]["verdict"] == "WIN")
    loss = sum(1 for r in results.values() if not r.get("skipped") and r["strict"]["verdict"] == "LOSS")
    n = sum(1 for r in results.values() if not r.get("skipped"))
    return hdr + "\n".join(rows) + f"\n\n{n} language(s) measured: {wins} win, {loss} loss, {n - wins - loss} wash (min_gain {min_gain}).\n"


# ---- orchestration (the runner is injected, so tests need no chain) --------------------------------------------------------------
def _json_from(stdout: str) -> dict:
    i = stdout.index("{")
    return json.loads(stdout[i:])


def measure_language(lang: str, tag: str, usj: Path, scratch: Path, flag: str, scope: str, conv: bool,
                     run: Callable[[list[str]], str], py: str, min_gain: float, max_drop: float) -> dict:
    spec_base, spec_ext = "eflomal+gloss", "spanext+eflomal+gloss"
    # variant OFF (+ the no-spanext baseline in the same scoring call)
    run(span_extension_cmd(py, tag, lang, usj, scratch, flag, False, scope))
    off = _json_from(run(pos_score_cmd(py, tag, lang, usj, scratch, [spec_base, spec_ext], scope, conv)))["results"]
    for fp in scratch.glob(f"align_spanext_{tag}_*"):
        fp.unlink()                                                      # the next run must not read the previous variant
    # variant ON
    run(span_extension_cmd(py, tag, lang, usj, scratch, flag, True, scope))
    on = _json_from(run(pos_score_cmd(py, tag, lang, usj, scratch, [spec_ext], scope, conv)))["results"]

    def block(suffix: str) -> dict:
        b, o, n = off.get(spec_base + suffix, {}), off.get(spec_ext + suffix, {}), on.get(spec_ext + suffix, {})
        dl = delta(o, n)
        return {"baseline": b, "off": o, "on": n, "delta": dl, "verdict": verdict(dl, min_gain, max_drop)}
    out = {"tag": tag, "strict": block("")}
    if conv:
        out["conv"] = block(" [conv]")
    return out


def prepare_scratch(tag: str, scratch: Path, live_out: Path = LIVE_OUT) -> int:
    """Copy the language's finished eflomal + gloss files (gz or plain) into the private scratch folder; returns how many files."""
    sys.path.insert(0, str(REPO / "pipeline"))
    from lexeme_aligner.align_files import tag_files
    scratch.mkdir(parents=True, exist_ok=True)
    n = 0
    for method in BASE_METHODS:
        for ap in tag_files(live_out, method, tag):
            src = Path(str(ap))
            shutil.copy2(src, scratch / src.name)
            n += 1
    return n


def default_runner(cmd: list[str]) -> str:
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}… exited {r.returncode}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ''}")
    return r.stdout


def gold_languages() -> list[str]:
    cfg = json.loads((REPO / "config/gold_langs.json").read_text(encoding="utf-8"))
    return [k for k, v in cfg.items() if not k.startswith("_") and isinstance(v, dict) and v.get("edition")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flag", required=True, choices=FLAGS)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--langs", help="comma-separated gold languages")
    g.add_argument("--all-gold", action="store_true", help="every language in config/gold_langs.json that has an edition")
    ap.add_argument("--scope", choices=("all", "nt", "ot"), default="all")
    ap.add_argument("--convention-aware", action="store_true", help="also score the [conv] rows (fair for gold that never credits function words)")
    ap.add_argument("--min-gain", type=float, default=0.002)
    ap.add_argument("--max-precision-drop", type=float, default=0.01)
    ap.add_argument("--work", type=Path, default=None, help="scratch/results folder (default pipeline/work/measure/<flag>-<timestamp>)")
    ap.add_argument("--force", action="store_true", help="measure a language even if a chain for it is running right now")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    a = ap.parse_args(argv)

    sys.path.insert(0, str(REPO / "pipeline"))
    from lexeme_aligner.eval.contest_rule import gold_edition
    langs = gold_languages() if a.all_gold else [x.strip() for x in a.langs.split(",") if x.strip()]
    work = a.work or REPO / "pipeline/work/measure" / f"{a.flag}-{time.strftime('%Y%m%d-%H%M%S')}"
    busy = running_names(subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout)
    results: dict[str, dict] = {}
    for lang in langs:
        tag = gold_edition(lang)
        usj = INGEST / f"usj-{tag}" if tag else None
        why = None
        if not tag:
            why = "no gold edition recorded"
        elif not usj.is_dir():
            why = f"edition {tag} not ingested"
        elif (lang in busy or tag in busy) and not a.force:
            why = "a chain is running for it (files being rewritten); use --force or wait"
        if why:
            results[lang] = {"skipped": why}
            print(f"[measure] {lang}: skipped — {why}", file=sys.stderr)
            continue
        if a.dry_run:
            print(f"[measure] {lang} ({tag}): would run span_extension --no/--{a.flag.replace('_', '-')} and pos_score in {work / lang}", file=sys.stderr)
            continue
        scratch = work / lang
        n = prepare_scratch(tag, scratch)
        if n == 0:
            results[lang] = {"skipped": f"no finished eflomal/gloss files for {tag}"}
            print(f"[measure] {lang}: skipped — no eflomal/gloss files for {tag}", file=sys.stderr)
            continue
        print(f"[measure] {lang} ({tag}): {n} input files copied; running both variants", file=sys.stderr)
        try:
            results[lang] = measure_language(lang, tag, usj, scratch, a.flag, a.scope, a.convention_aware, default_runner, sys.executable,
                                             a.min_gain, a.max_precision_drop)
        except Exception as e:                                          # noqa: BLE001 — one language must not stop the table
            results[lang] = {"skipped": f"failed: {e}"}
            print(f"[measure] {lang}: {e}", file=sys.stderr)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)                  # the inputs were copies; results are kept below
    if a.dry_run:
        return 0
    work.mkdir(parents=True, exist_ok=True)
    (work / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    table = render_table(results, a.flag, a.scope, a.min_gain)
    (work / "table.md").write_text(table, encoding="utf-8")
    print(table)
    print(f"[measure] results: {work}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
