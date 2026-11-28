"""`lexeme-aligner align-text` (2026-10-28): align one edition's text with our chain, on someone else's machine, writing only into one folder.

For bsb-add-books first (Kuni drafts), meant for any team. The input is the edition's text (USJ, USFM or plain text, below)
plus an edition id and the language's ISO 639-3 code. The output folder gets the same files our chain makes for that edition:

    OUT/align_<method>_<edition>_<BOOK>.jsonl      per-method links (eflomal, gloss, spanext, gapfill, residual)
    OUT/compact/<i>/<iso>/<edition>/...            compact-alignments, laid out exactly as published (+ .meta.json, manifest)
    OUT/compact/_index/                            what those files are read with: the source index of the text's books
                                                   (<BOOK>_lexemes/_keys/_fn/_fn_keys.json, _source.json) and its verse map
                                                   (_versification_<scheme>.json), byte-identical to compact-alignments' own
    OUT/target-stopwords/, target-morphology/      computed from THIS text (never read from our published lists)
    OUT/lexroot/                                   the eflomal-only lexeme export of this text
    OUT/lexroot-pool/                              that export + the language's published pool: what the gloss step bootstraps from
    OUT/align-text-run.json                        the run record: package version, git commit, sha256 of the text and of every
                                                   data/settings input, each step's command, exit code and time
    OUT/guard.log                                  only if something tried to write elsewhere (that step then fails)

Steps (the per-edition part of full_chain, word route): eflomal (NT first, then the OT with the NT bridge when nt_bridge.applies) ->
eflomal-only export -> gloss -> span extension -> gapfill -> residual -> compact. Not run: the pooled datasets (lexeme-alignments final
export, aligned_mwe, senses), the ledger, publishing. Every step runs under lexeme_aligner.write_guard with only OUT (and the system temp
folder) writable; every chain setting that has a folder is pointed inside OUT.

    lexeme-aligner align-text --text PATH --edition kvg_draft --iso kvg --out OUT [--lang-name Kuni] [--data DIR]

Pooled priors (0.3.0, 2026-11-16): like full_chain, which aligns a language's editions together, the text borrows what the language's
published editions know: `bcv-commons/lexeme-alignments` iso=<iso> (fetch-inputs --iso fetches it) gives eflomal LEX priors for the
text's content words and joins the gloss seed (lexeme_aligner.pool_priors). Most useful for a small text. `--no-pool` = the text alone;
the run record's `pool` names the file, its sha256 and what was left out.

`--text` reads USJ, USFM, the eBible corpus layout (vref.txt) or plain text with one verse per line (lexeme_aligner.text_input);
anything but USJ is converted into OUT/text-usj first. `--usj DIR` still works for a USJ folder.

LLM checking (2026-10-29), after the chain: `--jev` asks TypeSafe's Jev about every clause (bsb-add-books' CLAUSES.md units; their source
modules, and their target module for the language when one is published, else a generic text). Paid, with YOUR key (TYPESAFE_API_KEY).
`--jev` alone only estimates the cost (nothing sent); `--jev --max-usd X` runs when the estimate is at most X and stops sending at X.
Answers are cached in OUT/jev/responses (a re-run never pays twice); the layer goes to OUT/full-alignments-llm/<i>/<iso>/<edition>+llm+jev-v4.

By word parts (2026-10-30): `--split FILE` (a bcv-split words.parquet made for THIS text; its _split.json text fingerprints are checked)
or `--split auto` (the language's published split, `fetch-inputs --split`) aligns the text by parts as full_chain's parts route does:
the parts view in OUT/parts/usj, eflomal (+ NT bridge) / part-level gloss priors / gloss / span extension / gapfill / residual on it in
OUT/parts/out, folded back to the real tokens in OUT, then compact as the MORPHED edition (`<iso>-m_<rest>`, meta `parts` channel,
manifest `parts` pin). With --jev, Jev is asked on the parts view and its layer is the morphed edition's too.

Repeated runs (2026-10-30): `--runs N` runs the chain N times (run 1 in OUT, the others in OUT/runs/run<k>) and adds to run 1's compact
`.meta.json` (on source parts: to part-alignments' per unit, and compact gets it by the fold) a `runs` channel: per aligned entry, how many of the N runs made exactly that link (lexeme_aligner.run_agreement). Links all
runs agree on are far more precise than the rest (make-tanakh, Kuni Ruth: 0.706 vs 0.232). Jev, when asked, works on run 1.

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
_RESOLVE_METHODS = _METHODS + ",fnrule"              # = full_chain._RESOLVE_METHODS (+ function_rule.py's placements)
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


PARTS = "parts"                              # OUT/parts/{usj,out,priors}: the parts view and the steps' raw output on it


def plan_parts(usj: Path, edition: str, iso: str, out: Path, scope: str, bridge: bool, lang_name: str | None,
               split: Path, index_root: Path | None = None) -> list[tuple[str, list[str], dict]]:
    """[(step name, [module, args...], extra env)] for alignment by word parts — pure, the tests call it. `index_root`: as in plan()."""
    view, vout, priors = out / PARTS / "usj", out / PARTS / "out", out / PARTS / "priors"
    venv = {"ALIGNER_OUT": str(vout)}
    common = ["--iso", edition, "--publish-iso", iso, "--usj-dir", str(view), "--out", str(vout)]
    name = ["--lang-name", lang_name] if lang_name else []
    pilot = ["lexeme_aligner.run_pilot", *common, *name]
    steps: list[tuple[str, list[str], dict]] = [
        ("parts-view", ["lexeme_aligner.target_parts", "view", "--usj", str(usj), "--split", str(split), "--view", str(view)], {})]
    if bridge:
        steps += [("eflomal-nt", [*pilot, "--method", "eflomal", "--nt"], venv),
                  ("eflomal-ot", [*pilot, "--method", "eflomal", "--ot", "--nt-bridge"], venv)]
    else:
        steps += [("eflomal", [*pilot, "--method", "eflomal", scope], venv)]
    steps += [
        ("part-priors", ["lexeme_aligner.target_parts", "priors", "--tag", edition, "--iso", iso, "--view-out", str(vout), "--view", str(view),
                         "--split", str(split), "--lex-root", str(priors)], venv),
        ("gloss", [*pilot, "--method", "gloss", scope], dict(venv, ALIGNER_LEX_ROOT=str(priors))),
        ("spanext", ["lexeme_aligner.span_extension", *common, scope, "--methods", "eflomal,gloss"], venv),
        ("gapfill", ["lexeme_aligner.gapfill", *common, scope, "--methods", "eflomal,gloss,spanext"], venv),
        ("residual", ["lexeme_aligner.residual_align", *common, scope, "--methods", "eflomal,gloss,spanext"], venv),
        ("function-rule", ["lexeme_aligner.function_rule", *common, scope, "--methods", _METHODS], venv),
        ("fold", ["lexeme_aligner.target_parts", "fold", "--tag", edition, "--view-out", str(vout), "--out", str(out), "--view", str(view),
                  "--usj", str(usj)], {}),
        ("compact", ["lexeme_aligner.compact_align", "--iso", edition, "--publish-iso", iso, "--usj-dir", str(usj), "--methods", _RESOLVE_METHODS,
                     "--out-dir", str(out), "--publish", str(out / "compact"), "--index-root", str(index_root or out / "compact" / INDEX),
                     "--morphed", "--parts-pin", str(out / "parts-pin.json")], {}),
    ]
    return steps


def resolve_split(arg: str, iso: str, data: Path | None, usj: Path) -> tuple[Path, dict]:
    """(the split file, its manifest `parts` pin). `auto` = the language's published split in the data folder (fetch-inputs --split)."""
    from lexeme_aligner import target_parts
    if arg == "auto":
        if data is None:
            raise SystemExit("align-text: --split auto reads the data folder: pass --data (or set ALIGNER_DATA)")
        rec = json.loads((data / "inputs.json").read_text(encoding="utf-8")) if (data / "inputs.json").exists() else {}
        pin = (rec.get("splits") or {}).get(iso)
        fp = data / "language-analysis" / iso / "split" / "words.parquet"
        if not pin or not fp.is_file():
            raise SystemExit(f"align-text: no published split for {iso} in {data} (run lexeme-aligner fetch-inputs --split --iso {iso})")
    else:
        fp = Path(arg).expanduser().resolve()
        if not fp.is_file():
            raise SystemExit(f"align-text: no split file {fp}")
        pin = {"format": "bcv-split", "file": fp.name, "sha256": sha256(fp)}
    if sha256(fp) != pin.get("sha256"):
        raise SystemExit(f"align-text: {fp} is not the file its pin names (sha256 {pin.get('sha256')})")
    if bad := target_parts.split_mismatch(fp, usj):
        raise SystemExit("align-text: the split was made from another text for " + ", ".join(sorted(bad))
                         + " (its _split.json text fingerprints differ from this text's); use the split made for this text")
    return fp, pin


COMPACT_DEPRECATION = ("OUT/compact is deprecated (2026-11-23): compact-alignments is frozen and will be removed one week after "
                       "part-alignments is published. Read OUT/part-alignments instead; fold.py / fold.mjs make the compact files from it. "
                       "align-text stops writing OUT/compact in the first release after compact-alignments is removed.")
INDEX = "_index"                           # OUT/compact/_index: the source index (per book) + verse map the compact files are read with
POOL_ROOT = "lexroot-pool"                  # OUT/lexroot-pool: the text's own eflomal export + the language's published pool (gloss reads it)


PART_ALIGNMENTS = "part-alignments"          # OUT/part-alignments: bcv-commons/part-alignments' layout (part_alignments.py)
WORDS = "words"                              # OUT/words: the word view of a run on source parts (parts_fold), what compact + layers read


def plan(usj: Path, edition: str, iso: str, out: Path, scope: str, bridge: bool, lang_name: str | None,
         pool: dict | None = None, index_root: Path | None = None, word_spine: Path | None = None) -> list[tuple]:
    """[(step name, [module, args...][, extra env])] — pure, the tests call it. `pool` = {"path", "exclude"}: the language's published
    lexeme-alignments partition lends eflomal priors and the gloss seed (pool_priors.py); None = the text alone. `index_root`: where
    compact writes the source index and verse map the compact files need (default OUT/compact/_index; extra runs share run 1's).
    `word_spine`: the chain runs on SOURCE PARTS (ALIGNER_SPINE_DB = the parts spine, source_parts.py); its align files are folded to
    the word view in OUT/words (parts_fold); OUT/part-alignments is written from the parts run, and compact is its fold
    (compact_align --from-parts), on this word spine."""
    common = ["--iso", edition, "--publish-iso", iso, "--usj-dir", str(usj)]
    name = ["--lang-name", lang_name] if lang_name else []
    pilot = ["lexeme_aligner.run_pilot", "--usj-dir", str(usj), "--iso", edition, "--publish-iso", iso, "--out", str(out), *name]
    ex = ",".join(pool["exclude"]) if pool else ""
    pp = ["--pool-priors", str(pool["path"]), *(["--pool-exclude", ex] if ex else [])] if pool else []
    steps: list[tuple] = []
    if bridge:
        steps += [("eflomal-nt", [*pilot, "--method", "eflomal", "--nt", *pp]),
                  ("eflomal-ot", [*pilot, "--method", "eflomal", "--ot", "--nt-bridge", *pp])]
    else:
        steps += [("eflomal", [*pilot, "--method", "eflomal", scope, *pp])]
    steps += [
        ("export", ["lexeme_aligner.export_lex", "--iso", edition, "--publish-iso", iso, "--methods", "eflomal", "--out", str(out),
                    "--root", str(out / "lexroot"), *name])]
    if pool:
        steps += [("pool-merge", ["lexeme_aligner.pool_priors", "merge", "--iso", iso, "--own", str(out / "lexroot"), "--pool", str(pool["path"]),
                                  "--out", str(out / POOL_ROOT), *(["--exclude", ex] if ex else [])]),
                  ("gloss", [*pilot, "--method", "gloss", scope], {"ALIGNER_LEX_ROOT": str(out / POOL_ROOT)})]
    else:
        steps += [("gloss", [*pilot, "--method", "gloss", scope])]
    steps += [
        ("spanext", ["lexeme_aligner.span_extension", *common, scope, "--methods", "eflomal,gloss", "--out", str(out)]),
        ("gapfill", ["lexeme_aligner.gapfill", *common, scope, "--methods", "eflomal,gloss,spanext", "--out", str(out)]),
        ("residual", ["lexeme_aligner.residual_align", *common, scope, "--methods", "eflomal,gloss,spanext", "--out", str(out)]),
        ("function-rule", ["lexeme_aligner.function_rule", *common, scope, "--methods", _METHODS, "--out", str(out)]),
    ]
    cout = out
    if word_spine is not None:
        cout = out / WORDS
        steps += [("parts-fold", ["lexeme_aligner.parts_fold", "--usj", str(usj), "--in", str(out), "--out", str(cout), "--books", "ALL",
                                  "--iso", edition, "--word-spine", str(word_spine)])]
    if word_spine is not None:                     # the part-level output itself, from the parts run's own files (parts spine)
        steps += [("part-alignments", ["lexeme_aligner.part_alignments", "--iso", edition, "--publish-iso", iso, "--usj-dir", str(usj),
                                       "--out-dir", str(out), "--dest", str(out / PART_ALIGNMENTS), "--methods", _RESOLVE_METHODS])]
    steps += [("compact", ["lexeme_aligner.compact_align", *common, "--methods", _RESOLVE_METHODS, "--out-dir", str(cout), "--publish", str(out / "compact"),
                           "--index-root", str(index_root or out / "compact" / INDEX),
                           *(["--from-parts", str(out / PART_ALIGNMENTS)] if word_spine is not None else [])],
               *([{"ALIGNER_SPINE_DB": str(word_spine)}] if word_spine is not None else []))]
    return steps


def resolve_pool(arg: str | None, iso: str, edition: str, data: Path | None, exclude: str = "") -> tuple[dict | None, dict]:
    """(plan's pool or None, its run-record entry). `auto` = the language's partition in the data folder (fetch-inputs --iso), else in
    this checkout's publish/lexeme-alignments; absent = no pool (a language we have not published), recorded, not an error. A FILE is
    used as given. The edition itself is always left out of the pool when it is one of the pooled editions (base_text or compact id)."""
    from lexeme_aligner import pool_priors
    from lexeme_aligner.nt_bridge import compact_id
    if arg is None:
        return None, {"used": False, "why": "--no-pool"}
    if arg == "auto":
        fp = (data / "lexeme-alignments" if data is not None else REPO / "publish" / "lexeme-alignments") / f"iso={iso}" / "data.parquet"
        if not fp.is_file():
            return None, {"used": False, "why": f"no published lexeme-alignments for {iso} at {fp}"}
    else:
        fp = Path(arg).expanduser().resolve()
        if not fp.is_file():
            raise SystemExit(f"align-text: no pool file {fp}")
    import pyarrow.parquet as pq
    bts = set(pq.read_table(fp, columns=["base_text"]).column("base_text").to_pylist())
    ex = sorted({e for e in exclude.split(",") if e} | {b for b in bts if edition.lower() in (b.lower(), compact_id(iso, b).lower())})
    rec = {"used": True, **_file_entry(fp), "editions": len(bts - set(ex)), "exclude": ex, **pool_priors.settings()}
    if data is not None and (data / "inputs.json").exists():
        rec["fetched"] = (json.loads((data / "inputs.json").read_text(encoding="utf-8")).get("files") or {}).get(f"lexeme-alignments/iso={iso}/data.parquet")
    return {"path": fp, "exclude": ex}, rec


def versification_choice(arg: str | None, usj: Path, warn=sys.stderr) -> dict:
    """The run record's `versification`, with `forced` = the value for $ALIGNER_VERSIFICATION when the steps must not detect it:
    given (--versification), or eng for a text with gaps, whose detection is noise (versification.complete_share)."""
    from lexeme_aligner import versification as vf
    share = vf.complete_share(str(usj))
    rec = {"complete_share": None if share is None else round(share, 3)}
    if arg:
        os.environ["ALIGNER_VERSIFICATION"] = arg
        try:
            vf.forced_scheme()                                    # refuses an unknown scheme before any step runs
        finally:
            del os.environ["ALIGNER_VERSIFICATION"]
        return {**rec, "scheme": arg, "how": "given", "forced": arg}
    if share is not None and share < vf.MIN_COMPLETE_SHARE:
        print(f"[align-text] the text has gaps (only {share:.0%} of its OT chapters are complete): its verse numbering cannot be detected, "
              "using eng (English/KJV numbering); pass --versification if it is numbered otherwise", file=warn)
        return {**rec, "scheme": "eng", "how": "default (text with gaps)", "forced": "eng"}
    return {**rec, "scheme": vf.edition_scheme("", str(usj)), "how": "detected"}


def step_env(out: Path, data: Path | None) -> dict:
    """Every chain setting that names a folder or file to write, inside OUT; the data inputs from --data when given."""
    env = dict(os.environ,
               ALIGNER_OUT=str(out), ALIGNER_LEX_ROOT=str(out / "lexroot"), ALIGNER_LLM_CACHE=str(out / "llm-cache"),
               ALIGNER_DNS_CACHE=str(out / "dns_cache.json"), ALIGNER_STOPWORDS_CACHE=str(out / "target-stopwords"),
               ALIGNER_MORPH_CACHE=str(out / "target-morphology"), PYTHONDONTWRITEBYTECODE="1")
    if data is not None:
        env["ALIGNER_SPINE_DB"] = str(data / "lexeme-spine-macula.db")
        env["ALIGNER_PARTS_SPINE_DB"] = str(data / "lexeme-spine-macula-parts-v1.1.db")
        env["ALIGNER_WORD_PARTS"] = str(data / "word-parts" / "v1.1")      # part kinds/texts for the part-alignments index
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


def jev_plan(edition: str, iso: str, usj: Path, books: list[str], workers: int, split: Path | None = None) -> list[tuple[str, list[str]]]:
    """[(book, jev_clauses args without --dry-run / --max-usd)] — pure, the tests call it. With `split`, `usj` is the parts view."""
    return [(b, ["lexeme_aligner.eval.jev_clauses", "--iso", edition, "--publish-iso", iso, "--book", b, "--margin", str(JEV_MARGIN),
                 "--conventions", "modules", "--usj-dir", str(usj), "--variant", JEV_VARIANT, "--workers", str(workers),
                 *(["--parts", str(split)] if split else [])]) for b in books]


def jev_stage(args, usj: Path, out: Path, data: Path | None, env: dict, books: list[str], record: dict, run, save,
              split: Path | None = None) -> bool:
    """Estimate every book (no call); stop there without --max-usd or when the estimate is above it; else ask Jev book by book, each
    book capped at what is left of --max-usd, then build the LLM layer into OUT/full-alignments-llm."""
    if data is not None:
        fetched = json.loads((data / "inputs.json").read_text(encoding="utf-8"))
        if not fetched.get("jev"):
            raise SystemExit(f"align-text: run lexeme-aligner fetch-inputs --jev --iso {args.iso} first")
        record["jev_target_module"] = (fetched.get("target_modules") or {}).get(args.iso)
    jenv = dict(env, ALIGNER_JEV_WORK=str(out / "jev"), **({"ALIGNER_JEV_INPUTS": str(data)} if data is not None else {}))
    if split:                                       # asked on the parts view; cells land beside the view's other raw output
        jenv["ALIGNER_OUT"] = str(out / PARTS / "out")
    plan_ = jev_plan(args.edition, args.iso, out / PARTS / "usj" if split else usj, books, args.jev_workers, split)
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
    lenv, sp = env, []
    if getattr(args, "source_parts", None):                # the layer is word level: fold the cells, read them on the word spine
        lenv = dict(env, ALIGNER_SPINE_DB=env["ALIGNER_WORD_SPINE_DB"])
        sp = ["--source-parts", str(args.source_parts)]
    code, _ = run("jev-layer", ["lexeme_aligner.align_text", "--layer", "--usj", str(usj), "--edition", args.edition, "--iso", args.iso,
                                "--out", str(out), "--books", ",".join(books), *(["--parts"] if split else []), *sp], lenv)
    if code == 0 and getattr(args, "source_parts", None):       # the part-level Jev layer (`srcOrd:_` = checked, no rendering)
        code, _ = run("jev-part-alignments", ["lexeme_aligner.part_alignments", "--iso", args.edition, "--publish-iso", args.iso,
                                              "--usj-dir", str(usj), "--out-dir", str(out), "--dest", str(out / PART_ALIGNMENTS),
                                              *[x for b in books for x in ("--book", b)],
                                              "--llm-cell", f"jevC.{args.edition}.{{BOOK}}.v4.{JEV_VARIANT}.jev-0"], env)
    if code == 0 and data is not None and (conv := modules_record(data, args.iso, books)):
        for layer_dir in (out / "full-alignments-llm").glob(f"?/{args.iso}/*+llm+jev-v4"):
            (layer_dir / "_conventions.json").write_text(json.dumps(conv, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return code == 0


def modules_record(data: Path, iso: str, books: list[str]) -> dict | None:
    """The `_conventions.json` of a layer asked with the language modules fetch-inputs --jev put in the data folder: per book the source
    module (grc for the NT, hbo for the OT; arc beside it in `files`) and the language's target module, each named `<commit8>/<path in
    the dataset>` from its download URL, with sha256 and licence. None without a data folder or without the files."""
    from lexeme_aligner.run_pilot import NT_BOOKS
    rec = json.loads((data / "inputs.json").read_text(encoding="utf-8")) if (data / "inputs.json").exists() else {}
    def name(dest: str) -> tuple[str, dict] | None:
        f = rec.get("files", {}).get(dest)
        if not f or "/resolve/" not in f.get("url", ""):
            return None
        rev, path = f["url"].split("/resolve/", 1)[1].split("/", 1)
        return f"{rev[:8]}/{path}", {"sha256": f["sha256"], "license": f.get("licence")}
    mods = {k: name(f"language-analysis/{d}/jev.md") for k, d in
            (("grc", "source/grc"), ("hbo", "source/hbo"), ("arc", "source/arc"), ("target", iso))}
    if not mods["grc"] and not mods["hbo"]:
        return None
    nt = set(NT_BOOKS)
    out = {"dataset": "bcv-commons/language-analysis", "mode": "modules", "credit": "make-tanakh (formerly bsb-add-books)", "books": {},
           "files": {n: meta for n, meta in filter(None, mods.values())}}
    for b in books:
        src = mods["grc"] if b in nt else mods["hbo"]
        out["books"][b] = {**({"source": src[0]} if src else {}), **({"target": mods["target"][0]} if mods["target"] else {})}
    return out


def layer_main(argv: list[str]) -> int:
    """`align-text --layer ...` (run by jev_stage under the guard): the Jev cells in OUT -> OUT/full-alignments-llm."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", action="store_true"); ap.add_argument("--usj", type=Path, required=True)
    ap.add_argument("--edition", required=True); ap.add_argument("--iso", required=True); ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--books", required=True)
    ap.add_argument("--parts", action="store_true", help="the cells were asked on the parts view (OUT/parts): fold them, morphed edition")
    ap.add_argument("--source-parts", type=Path, default=None, help="the cells were asked on source parts (this parts spine): fold them "
                    "to the word view in OUT/words first (this process runs on the word spine)")
    a = ap.parse_args(argv)
    from lexeme_aligner.fullalign_build import build_llm_layer
    cell = f"jevC.{a.edition}.{{BOOK}}.v4.{JEV_VARIANT}.jev-0"
    books = a.books.split(",")
    extra: dict = {}
    cells = a.out
    if a.source_parts:
        from lexeme_aligner import parts_fold
        from lexeme_aligner.config import SPINE_DB
        cells = a.out / WORDS
        print(json.dumps(parts_fold.fold_dir(a.usj, a.out, cells, books, a.source_parts, SPINE_DB, a.edition)), file=sys.stderr)
    if a.parts:
        from lexeme_aligner import target_parts
        from lexeme_aligner.compact_align import marked_edition
        cells = a.out / PARTS / "llm-folded"
        for b in books:
            target_parts.fold(cell.format(BOOK=b), a.out / PARTS / "out", cells, a.out / PARTS / "usj", a.usj, methods=("llm",))
        extra = {"edition": marked_edition(a.edition, a.iso, "m"),
                 "parts_pin": json.loads((a.out / "parts-pin.json").read_text(encoding="utf-8"))}
    rep = build_llm_layer(a.edition, a.iso, a.usj, cell, "jev-v4", books=books, out_dir=cells, out_root=a.out / "full-alignments-llm",
                          compact_root=REPO / "publish" / "compact-alignments", **extra)
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
    ap.add_argument("--split", default=None, help="align by word parts: a bcv-split words.parquet made for this text, or `auto` = the "
                    "language's published split (fetch-inputs --split); output is the morphed edition <iso>-m_<rest>")
    ap.add_argument("--pool", default="auto", help="the language's published lexeme-alignments partition lends eflomal priors and the "
                    "gloss seed (default auto: fetch-inputs' copy in --data, else this checkout's; none published = the text alone), or a "
                    "data.parquet to use")
    ap.add_argument("--no-pool", action="store_true", help="the text alone (align-text before 0.3.0)")
    ap.add_argument("--pool-exclude", default="", help="base_texts to leave out of the pool (comma-separated); the edition itself always is")
    ap.add_argument("--versification", default=None, help="the text's verse numbering: eng (English/KJV, protestant), org (Hebrew), "
                    "lxx, vul, rso, orgw, catm. Default: detected from the text; a text with gaps (most chapters missing verses) cannot be "
                    "detected and gets eng")
    ap.add_argument("--runs", type=int, default=1, help="run the chain N times; run 1's compact gets a `runs` agreement channel")
    ap.add_argument("--source-parts", type=Path, default=None, help="the parts spine (default: fetch-inputs' copy in --data, else this "
                    "checkout's pipeline/lexeme-spine-macula-parts-v1.1.db). align-text aligns on SOURCE parts (word-parts v1.1) by default "
                    "since 0.4.0: OUT/part-alignments, compact = its fold, the Jev layer from the word view (OUT/words)")
    ap.add_argument("--no-source-parts", action="store_true", help="align on whole source words (align-text before 0.4.0)")
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
    split, split_pin = (None, None)
    if args.split:
        if not args.edition.lower().startswith(args.iso.lower()):
            raise SystemExit(f"align-text: with --split the edition id must start with the iso ({args.iso}_...): it is marked {args.iso}-m_...")
        split, split_pin = resolve_split(args.split, args.iso, data, usj)
    if args.runs < 1:
        raise SystemExit("align-text: --runs must be at least 1")
    word_spine = None
    source_parts_off = "--no-source-parts" if args.no_source_parts else ("--split (source parts with target parts: not built yet)" if split else None)
    if split and args.source_parts:
        raise SystemExit("align-text: --source-parts with --split is not built yet")
    args.source_parts = None if source_parts_off else (args.source_parts or Path(env.get("ALIGNER_PARTS_SPINE_DB")
                                                                               or REPO / "pipeline" / "lexeme-spine-macula-parts-v1.1.db"))
    if args.source_parts:
        args.source_parts = args.source_parts.expanduser().resolve()
        if not args.source_parts.is_file():
            raise SystemExit(f"align-text: no parts spine at {args.source_parts}" + (" (run lexeme-aligner fetch-inputs)" if data else "")
                             + "; or pass --no-source-parts")
        word_spine = data_inputs(env)["spine"].resolve()
        env.update(ALIGNER_SPINE_DB=str(args.source_parts), ALIGNER_WORD_SPINE_DB=str(word_spine))
    if split:
        pool, pool_rec = None, {"used": False, "why": "--split: the parts route takes part-level priors from its own view"}
    else:
        pool, pool_rec = resolve_pool(None if args.no_pool else args.pool, args.iso, args.edition, data, args.pool_exclude)
    vers = versification_choice(args.versification, usj)
    run_dirs = [out] + [out / "runs" / f"run{k}" for k in range(2, args.runs + 1)]
    runs_plan = []                                   # (run dir, its env, its steps)
    for rd in run_dirs:
        rd.mkdir(parents=True, exist_ok=True)
        if split:
            (rd / "parts-pin.json").write_text(json.dumps(split_pin, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            rsteps = plan_parts(usj, args.edition, args.iso, rd, scope, bridge, args.lang_name, split, out / "compact" / INDEX)
        else:
            rsteps = plan(usj, args.edition, args.iso, rd, scope, bridge, args.lang_name, pool, out / "compact" / INDEX, word_spine)
        renv = step_env(rd, data)
        if word_spine is not None:
            renv.update(ALIGNER_SPINE_DB=str(args.source_parts), ALIGNER_WORD_SPINE_DB=str(word_spine))
        if vers.get("forced"):
            renv["ALIGNER_VERSIFICATION"] = vers["forced"]
        runs_plan.append((rd, renv, rsteps))
    steps = runs_plan[0][2]

    record = {
        "tool": "lexeme-aligner align-text", "version": package_version(),
        "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "edition": args.edition, "iso": args.iso, "lang_name": args.lang_name,
        "text": {"path": str(usj), "sha256": digest, "books": books, "format": text_format,
                 **({"source": str(given), "source_sha256": text_input.source_digest(given)} if usj != given else {})},
        "scope": scope.lstrip("-"), "nt_bridge": bridge, "pool": pool_rec, "versification": vers,
        **({"split": {"path": str(split), "pin": split_pin}} if split else {}),
        **({"runs": args.runs} if args.runs > 1 else {}),
        "source_parts": ({**_file_entry(args.source_parts), "word_spine": str(word_spine)} if word_spine is not None
                         else {"used": False, "why": source_parts_off}),
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

    ok = True
    for k, (rd, renv, rsteps) in enumerate(runs_plan, 1):
        sfx = f"-run{k}" if k > 1 else ""
        ok = ok and all(run(name + sfx, cmd, dict(renv, **(more[0] if more else {})))[0] == 0 for name, cmd, *more in rsteps)
    if ok and len(runs_plan) > 1 and word_spine is not None:
        # source parts: the agreement is counted per source unit on part-alignments, then run 1's compact is folded again from it, so
        # compact's `runs` is the fold of part-alignments' too (compact = fold(part-alignments), every channel)
        ok = run("agreement", ["lexeme_aligner.run_agreement", "--main", str(out / PART_ALIGNMENTS),
                               "--runs", *[str(rd / PART_ALIGNMENTS) for rd, _e, _s in runs_plan[1:]]])[0] == 0
        name, cmd, *more = next(st for st in steps if st[0] == "compact")
        ok = ok and run("compact-refold", cmd, dict(runs_plan[0][1], **(more[0] if more else {})))[0] == 0
    elif ok and len(runs_plan) > 1:
        ok = run("agreement", ["lexeme_aligner.run_agreement", "--main", str(out / "compact"),
                               "--runs", *[str(rd / "compact") for rd, _e, _s in runs_plan[1:]]])[0] == 0
    if ok and args.jev:
        jbooks = books
        if args.books:
            want = [b.strip().upper() for b in args.books.split(",") if b.strip()]
            missing = [b for b in want if b not in books]
            if missing:
                raise SystemExit(f"align-text: --books {','.join(missing)} not in the text ({','.join(books)})")
            jbooks = want
        ok = jev_stage(args, usj, out, data, env, jbooks, record, run, save, split)
    log.close()
    if (out / "compact").is_dir():                         # owner 2026-11-23: compact-alignments is frozen; OUT/compact goes with it
        record["deprecated"] = {"compact": COMPACT_DEPRECATION}
        print(f"[align-text] note: {COMPACT_DEPRECATION}", file=sys.stderr)
    record["finished"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    record["ok"] = ok
    record["guard_refusals"] = len((out / "guard.log").read_text(encoding="utf-8").splitlines()) if (out / "guard.log").exists() else 0
    save()
    print(f"[align-text] {'done' if ok else 'stopped'}: {out}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
