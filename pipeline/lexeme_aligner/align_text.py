"""`lexeme-aligner align-text` (2026-10-28): align one edition's text with our chain, on someone else's machine, writing only into one folder.

For bsb-add-books first (Kuni drafts), meant for any team. The input is a folder of USJ book files (our ingest layout, `NN-BOOK.json`)
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

    lexeme-aligner align-text --usj DIR --edition kvg_draft --iso kvg --out OUT [--lang-name Kuni] [--data DIR]

`--data DIR` takes the spine and prior-pack from DIR (`lexeme-spine-macula.db`, `prior_pack.parquet`; fetch-inputs will fill it) instead
of their places in this checkout. The checkout is the working directory of every step (settings are read relative to it).
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
    return env


def data_inputs(env: dict) -> dict:
    spine = Path(env.get("ALIGNER_SPINE_DB") or REPO / "pipeline" / "lexeme-spine-macula.db")
    prior = Path(env.get("ALIGNER_PRIOR_PACK") or REPO / "pipeline" / "vendor" / "prior-pack" / "prior_pack.parquet")
    return {"spine": spine, "prior_pack": prior}


def _file_entry(fp: Path) -> dict:
    return {"path": str(fp), "sha256": sha256(fp), "bytes": fp.stat().st_size} if fp.is_file() else {"path": str(fp), "missing": True}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lexeme-aligner align-text", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usj", type=Path, required=True, help="folder of USJ book files (NN-BOOK.json)")
    ap.add_argument("--edition", required=True, help="edition id; names the align files and the compact edition folder")
    ap.add_argument("--iso", required=True, help="ISO 639-3 code of the language (selects its grammar facts and settings)")
    ap.add_argument("--out", type=Path, required=True, help="output folder; the only place this run writes to")
    ap.add_argument("--lang-name", default=None)
    ap.add_argument("--data", type=Path, default=None, help="folder with lexeme-spine-macula.db and prior_pack.parquet (default: this checkout)")
    ap.add_argument("--force", action="store_true", help="run even if OUT already holds a run record")
    args = ap.parse_args(argv)

    usj, out = args.usj.resolve(), args.out.resolve()
    data = args.data.resolve() if args.data else None
    if not usj.is_dir():
        raise SystemExit(f"align-text: no text folder {usj}")
    if out == REPO or REPO.is_relative_to(out) or (out.is_relative_to(REPO) and not out.is_relative_to(REPO / "pipeline" / "work")):
        raise SystemExit(f"align-text: choose an output folder outside the checkout (or under pipeline/work): {out}")
    if (out / RECORD).exists() and not args.force:
        raise SystemExit(f"align-text: {out} already holds a run ({RECORD}); use another folder or --force")
    out.mkdir(parents=True, exist_ok=True)
    from lexeme_aligner import write_guard
    write_guard.install([out])                         # this process too: it imports chain modules (config, nt_bridge, run_pilot)
    env = step_env(out, data)
    inputs = data_inputs(env)
    for name, fp in inputs.items():
        if not fp.is_file():
            raise SystemExit(f"align-text: {name} not found at {fp}" + ("" if data else " (pass --data DIR)"))

    os.chdir(REPO)
    sys.path.insert(0, str(REPO / "pipeline"))
    from lexeme_aligner import nt_bridge
    digest, books = text_digest(usj)
    scope = scope_of(set(books))
    bridge = scope == "--all" and nt_bridge.applies(usj)
    steps = plan(usj, args.edition, args.iso, out, scope, bridge, args.lang_name)

    record = {
        "tool": "lexeme-aligner align-text", "version": package_version(),
        "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "edition": args.edition, "iso": args.iso, "lang_name": args.lang_name,
        "text": {"path": str(usj), "sha256": digest, "books": books},
        "scope": scope.lstrip("-"), "nt_bridge": bridge,
        "data": {k: _file_entry(fp) for k, fp in inputs.items()},
        "settings": {p.replace("<iso>", args.iso): _file_entry(REPO / p.replace("<iso>", args.iso)) for p in SETTINGS},
        "steps": [],
    }

    def save() -> None:
        tmp = out / (RECORD + ".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, out / RECORD)

    save()
    log = open(out / "run.log", "a", encoding="utf-8")
    ok = True
    for name, cmd in steps:
        full = [sys.executable, "-m", "lexeme_aligner.write_guard", "--allow", str(out), "--", "-m", *cmd]
        print(f"[align-text] {name}", file=sys.stderr, flush=True)
        log.write(f"\n=== {name} {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}\n{' '.join(full)}\n")
        log.flush()
        t0 = time.monotonic()
        code = subprocess.run(full, env=env, cwd=REPO, stdout=log, stderr=subprocess.STDOUT).returncode
        record["steps"].append({"step": name, "cmd": cmd, "exit": code, "seconds": round(time.monotonic() - t0, 1)})
        save()
        if code != 0:
            ok = False
            print(f"[align-text] {name} failed (exit {code}); see {out / 'run.log'}"
                  + (f" and {out / 'guard.log'}" if (out / "guard.log").exists() else ""), file=sys.stderr)
            break
    log.close()
    record["finished"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    record["ok"] = ok
    record["guard_refusals"] = len((out / "guard.log").read_text(encoding="utf-8").splitlines()) if (out / "guard.log").exists() else 0
    save()
    print(f"[align-text] {'done' if ok else 'stopped'}: {out}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
