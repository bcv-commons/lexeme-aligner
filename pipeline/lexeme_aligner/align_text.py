"""`lexeme-aligner align-text` (2026-10-28): align one edition's text with our chain, on someone else's machine, writing only into one folder.

For bsb-add-books first (Kuni drafts), meant for any team. The input is the edition's text (USJ, USFM or plain text, below)
plus an edition id and the language's ISO 639-3 code. The output folder gets the same files our chain makes for that edition:

    OUT/align_<method>_<edition>_<BOOK>.jsonl      per-method links (eflomal, gloss, spanext, gapfill, residual)
    OUT/compact/<i>/<iso>/<edition>/...            compact-alignments, laid out exactly as published (+ .meta.json, manifest)
    OUT/target-stopwords/, target-morphology/      computed from THIS text (never read from our published lists)
    OUT/lexroot/                                   the eflomal-only lexeme export the gloss step bootstraps from
    OUT/align-text-run.json                        the run record: package version, git commit, sha256 of the text and of every
                                                   data/settings input, each step's command, exit code and time
    OUT/guard.log                                  only if something tried to write elsewhere (that step then fails)

Steps (the per-edition part of full_chain, word route): eflomal (NT first, then the OT with the NT bridge when nt_bridge.applies) ->
eflomal-only export -> gloss -> span extension -> gapfill -> residual -> compact. Not run: the pooled datasets (lexeme-alignments final
export, aligned_mwe, senses), the ledger, publishing. Every step runs under lexeme_aligner.write_guard with only OUT (and the system temp
folder) writable; every chain setting that has a folder is pointed inside OUT.

    lexeme-aligner align-text --text PATH --edition kvg_draft --iso kvg --out OUT [--lang-name Kuni] [--data DIR]

`--text` reads USJ, USFM, the eBible corpus layout (vref.txt) or plain text with one verse per line (lexeme_aligner.text_input);
anything but USJ is converted into OUT/text-usj first. `--usj DIR` still works for a USJ folder.

LLM checking (2026-10-29), after the chain: `--jev` asks TypeSafe's Jev about every clause (bsb-add-books' CLAUSES.md units; their source
modules, and their target module for the language when one is published, else a generic text). Paid, with YOUR key (TYPESAFE_API_KEY).
`--jev` alone only estimates the cost (nothing sent); `--jev --max-usd X` runs when the estimate is at most X and stops sending at X.
Answers are cached in OUT/jev/responses (a re-run never pays twice); the layer goes to OUT/full-alignments-llm/<i>/<iso>/<edition>+llm+jev-v4.

`--data DIR` (or `$ALIGNER_DATA`) takes the spine, prior-pack and the language's grammar facts from the folder `lexeme-aligner fetch-inputs
--iso ISO` filled, instead of their places in this checkout. The checkout is the working directory of every step (settings are read
relative to it).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_METHODS = "spanext,eflomal,gloss,gapfill"           # = full_chain._METHODS (compact's main array)
RECORD = "align-text-run.json"
# Settings and data the chain reads besides the text (the inventory run's audit log, 2026-10-27), hashed into the run record. Also the first
# draft of the future package-data list. <iso> is filled in; a missing file is recorded as missing (e.g. no Grambank entry for the language).
SETTINGS = ["config/bibles_vrs/dbt_vrs_index.json", "config/contest_rule.json", "config/fertility_flags.json", "config/fn_veto.json",
            "config/gold_langs.json", "config/grambank/features.json", "config/light_lexemes.json", "config/nt_bridge.json",
            "config/sources.json", "config/spanext_flags.json", "config/takedown.json", "config/text_strip_rules.json",
            "config/gram_struct/article_bound.json", "config/gram_struct/external/<iso>.json", "config/gram_struct/imputed/<iso>.json",
            "pipeline/vendor/versification/nt-variants.json", "pipeline/vendor/versification/ot-variants.json",
            "pipeline/vendor/versification/schemes/hebrew.tsv", "publish/compact-alignments/_index/_source.json"]


def sha256(fp: Path) -> str:
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def text_digest(usj: Path) -> tuple[str, list[str]]:
    """One sha256 over the book files (name + content, sorted by name) and the list of books."""
    h, books = hashlib.sha256(), []
    for fp in sorted(usj.glob("*.json")):
        h.update(fp.name.encode() + b"\0" + fp.read_bytes() + b"\0")
        books.append(fp.stem.split("-", 1)[-1])
    return h.hexdigest(), books


def package_version() -> dict:
    out: dict = {}
    try:
        from importlib.metadata import version
        out["package"] = version("lexeme-aligner")
    except Exception:
        out["package"] = None
    try:
        out["git_commit"] = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                                           check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no", "--", "pipeline/lexeme_aligner",
                                "config"], capture_output=True, text=True, check=True).stdout.strip()
        out["git_dirty"] = bool(dirty)          # tracked code/settings changed since the commit (the run may not be reproducible from it)
    except (OSError, subprocess.CalledProcessError):
        out["git_commit"] = None
    return out


def scope_of(books: set[str]) -> str:
    from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS
    ot, nt = bool(books & set(OT_BOOKS)), bool(books & set(NT_BOOKS))
    if not (ot or nt):
        raise SystemExit("align-text: no Bible book files (NN-BOOK.json) in the text folder")
    return "--all" if ot and nt else "--ot" if ot else "--nt"


def plan(usj: Path, edition: str, iso: str, out: Path, scope: str, bridge: bool, lang_name: str | None) -> list[tuple[str, list[str]]]:
    """[(step name, [module, args...])] — pure, the tests call it."""
    common = ["--iso", edition, "--publish-iso", iso, "--usj-dir", str(usj)]
    name = ["--lang-name", lang_name] if lang_name else []
    pilot = ["lexeme_aligner.run_pilot", "--usj-dir", str(usj), "--iso", edition, "--publish-iso", iso, "--out", str(out), *name]
    steps: list[tuple[str, list[str]]] = []
    if bridge:
        steps += [("eflomal-nt", [*pilot, "--method", "eflomal", "--nt"]), ("eflomal-ot", [*pilot, "--method", "eflomal", "--ot", "--nt-bridge"])]
    else:
        steps += [("eflomal", [*pilot, "--method", "eflomal", scope])]
    steps += [
        ("export", ["lexeme_aligner.export_lex", "--iso", edition, "--publish-iso", iso, "--methods", "eflomal", "--out", str(out),
                    "--root", str(out / "lexroot"), *name]),
        ("gloss", [*pilot, "--method", "gloss", scope]),
        ("spanext", ["lexeme_aligner.span_extension", *common, scope, "--methods", "eflomal,gloss", "--out", str(out)]),
        ("gapfill", ["lexeme_aligner.gapfill", *common, scope, "--methods", "eflomal,gloss,spanext", "--out", str(out)]),
        ("residual", ["lexeme_aligner.residual_align", *common, scope, "--methods", "eflomal,gloss,spanext", "--out", str(out)]),
        ("compact", ["lexeme_aligner.compact_align", *common, "--methods", _METHODS, "--out-dir", str(out), "--publish", str(out / "compact")]),
    ]
    return steps


def step_env(out: Path, data: Path | None) -> dict:
    """Every chain setting that names a folder or file to write, inside OUT; the data inputs from --data when given."""
    env = dict(os.environ,
               ALIGNER_OUT=str(out), ALIGNER_LEX_ROOT=str(out / "lexroot"), ALIGNER_LLM_CACHE=str(out / "llm-cache"),
               ALIGNER_DNS_CACHE=str(out / "dns_cache.json"), ALIGNER_STOPWORDS_CACHE=str(out / "target-stopwords"),
               ALIGNER_MORPH_CACHE=str(out / "target-morphology"), PYTHONDONTWRITEBYTECODE="1")
    if data is not None:
        env["ALIGNER_SPINE_DB"] = str(data / "lexeme-spine-macula.db")
        env["ALIGNER_PRIOR_PACK"] = str(data / "prior_pack.parquet")
        env["ALIGNER_GRAM_STRUCT"] = str(data / "gram_struct")
    return env


def settings_path(p: str, iso: str, data: Path | None) -> Path:
    """Where a SETTINGS entry is read from: grammar facts from the data folder when one is used, the rest from the checkout."""
    p = p.replace("<iso>", iso)
    if data is not None and p.startswith("config/gram_struct/"):
        return data / "gram_struct" / p.removeprefix("config/gram_struct/")
    return REPO / p


def data_inputs(env: dict) -> dict:
    spine = Path(env.get("ALIGNER_SPINE_DB") or REPO / "pipeline" / "lexeme-spine-macula.db")
    prior = Path(env.get("ALIGNER_PRIOR_PACK") or REPO / "pipeline" / "vendor" / "prior-pack" / "prior_pack.parquet")
    return {"spine": spine, "prior_pack": prior}


def _file_entry(fp: Path) -> dict:
    return {"path": str(fp), "sha256": sha256(fp), "bytes": fp.stat().st_size} if fp.is_file() else {"path": str(fp), "missing": True}


JEV_VARIANT, JEV_MARGIN = "A", 3          # cells jevC.<edition>.<BOOK>.v4.A.jev-<θ>; margin = the fleet's (Kuni 2026-10-28)


def jev_plan(edition: str, iso: str, usj: Path, books: list[str], workers: int) -> list[tuple[str, list[str]]]:
    """[(book, jev_clauses args without --dry-run / --max-usd)] — pure, the tests call it."""
    return [(b, ["lexeme_aligner.eval.jev_clauses", "--iso", edition, "--publish-iso", iso, "--book", b, "--margin", str(JEV_MARGIN),
                 "--conventions", "modules", "--usj-dir", str(usj), "--variant", JEV_VARIANT, "--workers", str(workers)]) for b in books]


def jev_stage(args, usj: Path, out: Path, data: Path | None, env: dict, books: list[str], record: dict, run, save) -> bool:
    """Estimate every book (no call); stop there without --max-usd or when the estimate is above it; else ask Jev book by book, each
    book capped at what is left of --max-usd, then build the LLM layer into OUT/full-alignments-llm."""
    if data is not None:
        fetched = json.loads((data / "inputs.json").read_text(encoding="utf-8"))
        if not fetched.get("jev"):
            raise SystemExit(f"align-text: run lexeme-aligner fetch-inputs --jev --iso {args.iso} first")
        record["jev_target_module"] = (fetched.get("target_modules") or {}).get(args.iso)
    jenv = dict(env, ALIGNER_JEV_WORK=str(out / "jev"), **({"ALIGNER_JEV_INPUTS": str(data)} if data is not None else {}))
    plan_ = jev_plan(args.edition, args.iso, usj, books, args.jev_workers)
    est: dict = {}
    for b, cmd in plan_:
        code, stdout = run(f"jev-estimate-{b}", [*cmd, "--dry-run"], jenv, capture=True)
        if code != 0:
            return False
        est[b] = json.loads(stdout.strip().splitlines()[-1])["estimate"]
    total = round(sum(e["usd (est., high side)"] for e in est.values()), 2)
    record["jev"] = {"estimate_usd_high_side": total, "requests_to_send": sum(e["requests to send"] for e in est.values()),
                     "per_book": est, "max_usd": args.max_usd}
    save()
    print(f"[align-text] Jev estimate: {record['jev']['requests_to_send']} requests, about ${total} (high side)", file=sys.stderr)
    if args.max_usd is None:
        print("[align-text] no --max-usd: estimate only, nothing sent", file=sys.stderr)
        return True
    if total > args.max_usd:
        print(f"[align-text] estimate ${total} is above --max-usd {args.max_usd}: nothing sent", file=sys.stderr)
        return False
    spent = 0.0
    from lexeme_aligner.eval.jev_clauses import PRICE_PER_M_INPUT
    for b, cmd in plan_:
        left = round(args.max_usd - spent, 4)
        if left <= 0:
            print(f"[align-text] --max-usd reached before {b}", file=sys.stderr)
            return False
        if run(f"jev-{b}", [*cmd, "--max-usd", str(left)], jenv)[0] != 0:
            return False
        summ = out / "jev" / "clauses" / f"jevC.{args.edition}.{b}.v4.{JEV_VARIANT}.summary.json"
        u = json.loads(summ.read_text(encoding="utf-8"))["usage"] if summ.exists() else {}
        spent += u.get("input_tokens", 0) / 1e6 * PRICE_PER_M_INPUT
        record["jev"]["spent_usd"] = round(spent, 4)
        save()
    code, _ = run("jev-layer", ["lexeme_aligner.align_text", "--layer", "--usj", str(usj), "--edition", args.edition, "--iso", args.iso,
                                "--out", str(out), "--books", ",".join(books)], jenv)
    return code == 0


def layer_main(argv: list[str]) -> int:
    """`align-text --layer ...` (run by jev_stage under the guard): the Jev cells in OUT -> OUT/full-alignments-llm."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", action="store_true"); ap.add_argument("--usj", type=Path, required=True)
    ap.add_argument("--edition", required=True); ap.add_argument("--iso", required=True); ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--books", required=True)
    a = ap.parse_args(argv)
    from lexeme_aligner.fullalign_build import build_llm_layer
    rep = build_llm_layer(a.edition, a.iso, a.usj, f"jevC.{a.edition}.{{BOOK}}.v4.{JEV_VARIANT}.jev-0", "jev-v4", books=a.books.split(","),
                          out_dir=a.out, out_root=a.out / "full-alignments-llm", compact_root=REPO / "publish" / "compact-alignments")
    print(json.dumps(rep["layers"], ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--layer" in argv:
        return layer_main(argv)
    ap = argparse.ArgumentParser(prog="lexeme-aligner align-text", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", type=Path, help="the text: a folder (or one file) of USJ (NN-BOOK.json), USFM (.usfm/.sfm), "
                     "vref.txt + one text file (eBible corpus), or .txt with one verse per line (`BOOK C:V text`)")
    src.add_argument("--usj", type=Path, help="folder of USJ book files (NN-BOOK.json); the same as --text with such a folder")
    ap.add_argument("--edition", required=True, help="edition id; names the align files and the compact edition folder")
    ap.add_argument("--iso", required=True, help="ISO 639-3 code of the language (selects its grammar facts and settings)")
    ap.add_argument("--out", type=Path, required=True, help="output folder; the only place this run writes to")
    ap.add_argument("--lang-name", default=None)
    ap.add_argument("--data", type=Path, default=None,
                    help="the folder fetch-inputs filled (default $ALIGNER_DATA; without either: this checkout's own copies)")
    ap.add_argument("--force", action="store_true", help="run even if OUT already holds a run record")
    ap.add_argument("--jev", action="store_true", help="after the chain, LLM checking by clause (TypeSafe Jev, paid, your TYPESAFE_API_KEY): "
                    "without --max-usd only the cost estimate is made; needs `fetch-inputs --jev` in the data folder")
    ap.add_argument("--max-usd", type=float, default=None, help="with --jev: the most this run may spend; refused when the estimate is above")
    ap.add_argument("--jev-workers", type=int, default=4, help="with --jev: requests in parallel")
    ap.add_argument("--books", default=None, help="with --jev: only these books (comma-separated codes, e.g. PHM,RUT); the chain still "
                    "aligns every book of the text")
    args = ap.parse_args(argv)

    given, out = (args.text or args.usj).resolve(), args.out.resolve()
    data = args.data or (Path(os.environ["ALIGNER_DATA"]).expanduser() if os.environ.get("ALIGNER_DATA") else None)
    data = data.resolve() if data else None
    if not given.exists():
        raise SystemExit(f"align-text: no text at {given}")
    if out == REPO or REPO.is_relative_to(out) or (out.is_relative_to(REPO) and not out.is_relative_to(REPO / "pipeline" / "work")):
        raise SystemExit(f"align-text: choose an output folder outside the checkout (or under pipeline/work): {out}")
    if (out / RECORD).exists() and not args.force:
        raise SystemExit(f"align-text: {out} already holds a run ({RECORD}); use another folder or --force")
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "pipeline"))
    from lexeme_aligner import text_input
    usj, text_format = text_input.prepare(given, out / "text-usj")
    from lexeme_aligner import write_guard
    write_guard.install([out])                         # this process too: it imports chain modules (config, nt_bridge, run_pilot)
    env = step_env(out, data)
    inputs = data_inputs(env)
    for name, fp in inputs.items():
        if not fp.is_file():
            raise SystemExit(f"align-text: {name} not found at {fp}" + (" (run lexeme-aligner fetch-inputs)" if data else " (pass --data DIR)"))
    if data is not None:
        fetched = json.loads((data / "inputs.json").read_text(encoding="utf-8")) if (data / "inputs.json").exists() else {}
        if args.iso not in fetched.get("isos", []):
            raise SystemExit(f"align-text: no grammar facts fetched for {args.iso}: run lexeme-aligner fetch-inputs --iso {args.iso}"
                             + (f" --data {data}" if args.data else ""))

    os.chdir(REPO)
    from lexeme_aligner import nt_bridge
    digest, books = text_digest(usj)
    scope = scope_of(set(books))
    bridge = scope == "--all" and nt_bridge.applies(usj)
    steps = plan(usj, args.edition, args.iso, out, scope, bridge, args.lang_name)

    record = {
        "tool": "lexeme-aligner align-text", "version": package_version(),
        "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "edition": args.edition, "iso": args.iso, "lang_name": args.lang_name,
        "text": {"path": str(usj), "sha256": digest, "books": books, "format": text_format,
                 **({"source": str(given), "source_sha256": text_input.source_digest(given)} if usj != given else {})},
        "scope": scope.lstrip("-"), "nt_bridge": bridge,
        "data": {k: _file_entry(fp) for k, fp in inputs.items()},
        "settings": {p.replace("<iso>", args.iso): _file_entry(settings_path(p, args.iso, data)) for p in SETTINGS},
        "steps": [],
    }

    def save() -> None:
        tmp = out / (RECORD + ".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, out / RECORD)

    save()
    log = open(out / "run.log", "a", encoding="utf-8")
    def run(name: str, cmd: list[str], step_env: dict = env, capture: bool = False) -> tuple[int, str]:
        full = [sys.executable, "-m", "lexeme_aligner.write_guard", "--allow", str(out), "--", "-m", *cmd]
        print(f"[align-text] {name}", file=sys.stderr, flush=True)
        log.write(f"\n=== {name} {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}\n{' '.join(full)}\n")
        log.flush()
        t0 = time.monotonic()
        r = subprocess.run(full, env=step_env, cwd=REPO, stdout=subprocess.PIPE if capture else log, stderr=log, text=True)
        record["steps"].append({"step": name, "cmd": cmd, "exit": r.returncode, "seconds": round(time.monotonic() - t0, 1)})
        save()
        if r.returncode != 0:
            print(f"[align-text] {name} failed (exit {r.returncode}); see {out / 'run.log'}"
                  + (f" and {out / 'guard.log'}" if (out / "guard.log").exists() else ""), file=sys.stderr)
        return r.returncode, r.stdout if capture else ""

    ok = all(run(name, cmd)[0] == 0 for name, cmd in steps)          # all() stops at the first failed step
    if ok and args.jev:
        jbooks = books
        if args.books:
            want = [b.strip().upper() for b in args.books.split(",") if b.strip()]
            missing = [b for b in want if b not in books]
            if missing:
                raise SystemExit(f"align-text: --books {','.join(missing)} not in the text ({','.join(books)})")
            jbooks = want
        ok = jev_stage(args, usj, out, data, env, jbooks, record, run, save)
    log.close()
    record["finished"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    record["ok"] = ok
    record["guard_refusals"] = len((out / "guard.log").read_text(encoding="utf-8").splitlines()) if (out / "guard.log").exists() else 0
    save()
    print(f"[align-text] {'done' if ok else 'stopped'}: {out}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
