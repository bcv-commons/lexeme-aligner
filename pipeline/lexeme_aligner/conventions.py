"""Alignment conventions as attributed rules -> bcv-commons/alignment-conventions (2026-10-26).

A conventions text is what an LLM aligner (Jev) is told about a language besides the verse. Published here as RULES, each with a
derivation record, so a reader can tell a measured fact from a rule an LLM wrote:

  kind              derivation                                                                   licence
  measured          counted on a hand-made alignment (gold, script, books, date, figures)        the gold's (Clear: CC BY 4.0)
  by-construction   true because of how our tokenizer / data are built                           CC0
  llm-authored      written by an LLM in a lexeme-aligner session (file, first/last commit,       CC0
                    the evidence the text cites); `check` says whether a measured rule backs it

Tree (publish/alignment-conventions/):
  <lang>/<name>/<sha8>/conventions.md   the exact text sent to the aligner (sha8 = its sha256 prefix: every version kept;
                                       earlier texts a layer used live in config/llm_conventions/versions/<name>.<sha8>.md)
  <lang>/<name>/<sha8>/rules.json       the same text as attributed rules
  <lang>/measured/<profile>/rules.json  measured rules (derive_conventions profile)
  manifest.json, README.md
Each LLM layer in full-alignments-llm carries `_conventions.json`: which version each book was aligned with, plus those rules.

    .venv/bin/python -m lexeme_aligner.conventions --build                       # every config/llm_conventions/*.md + measured profiles
    .venv/bin/python -m lexeme_aligner.conventions --pin-layer <layer dir> --name heb [--book-map map.json]
    .venv/bin/python -m lexeme_aligner.conventions --publish [--push]             # dry run unless --push
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONV_DIR = REPO / "config/llm_conventions"
OUT = REPO / "publish/alignment-conventions"
HF_REPO = "bcv-commons/alignment-conventions"
PROFILES = REPO / "config/llm_conventions/measured"          # <lang>.<profile>.json written by derive_conventions --json
LLM_AUTHOR = "LLM (Claude, Anthropic) in a lexeme-aligner working session"
TOKENIZER = "lexeme_aligner.usj_source.tokenize (letter/mark runs, tokenizer v2)"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def file_history(path: Path) -> dict:
    log = _git("log", "--follow", "--format=%h %ad", "--date=short", "--", str(path.relative_to(REPO))).splitlines()
    return {"file": str(path.relative_to(REPO)), "first_commit": log[-1] if log else None, "last_commit": log[0] if log else None}


def blocks(md: str) -> list[str]:
    """Paragraphs and top-level bullets (a bullet runs until the next top-level bullet or a blank line)."""
    out: list[str] = []
    cur: list[str] = []
    for line in md.splitlines():
        if not line.strip():
            if cur:
                out.append("\n".join(cur)); cur = []
        elif line.startswith("- ") and cur:
            out.append("\n".join(cur)); cur = [line]
        else:
            cur.append(line)
    if cur:
        out.append("\n".join(cur))
    return out


def _title(block: str) -> str:
    m = re.match(r"-\s+\*\*(.+?)\*\*", block, re.S)
    return re.sub(r"\s+", " ", m.group(1) if m else block[:80]).strip().rstrip(":")


def rules_from_text(name: str, lang: str, text: str, history: dict, overrides: dict | None = None) -> list[dict]:
    """One rule per paragraph / bullet. The tokenization paragraph is by-construction; the rest is LLM-authored unless
    `overrides` (config/llm_conventions/<name>.attribution.json: title substring -> fields) says more."""
    rules = []
    for n, b in enumerate(blocks(text), 1):
        title = _title(b)
        if b.startswith("Target positions"):
            kind, der = "by-construction", {"source": TOKENIZER}
        else:
            kind, der = "llm-authored", {"author": LLM_AUTHOR, **history,
                                         "evidence": "as cited in the rule text (e.g. a gold comparison or verse examples)"}
        r = {"id": f"{name}.{n:02d}", "scope": {"language": lang, "set": name}, "title": title, "text": b, "kind": kind,
             "derivation": der}
        if kind == "llm-authored":
            r["check"] = {"status": "unchecked"}
        for key, extra in (overrides or {}).items():
            if key.lower() in b.lower():
                r.update({k: v for k, v in extra.items() if k != "derivation"})
                r["derivation"] = {**r["derivation"], **extra.get("derivation", {})}
        rules.append(r)
    return rules


def measured_rules(prof: dict, lang: str, profile_name: str, provenance: dict, min_n: int = 40) -> list[dict]:
    from lexeme_aligner.eval.derive_conventions import source_hint, target_hint
    rules = []
    for side, label, hint in (("target", "translation word", target_hint), ("source", "source word", source_hint)):
        for testament, d in sorted(prof.get(side, {}).items()):
            for key, e in sorted(d.items(), key=lambda kv: -kv[1]["n"]):
                if e["n"] < min_n:
                    continue
                text = hint(e)
                if not text:
                    continue
                rules.append({"id": f"{lang}.measured.{profile_name}.{testament}.{side}.{key}",
                              "scope": {"language": lang, "testament": testament, side: key},
                              "title": f"{label} {key!r} ({testament})", "text": f"{key}: {text}", "kind": "measured",
                              "figures": e, "derivation": {**provenance, "testament": testament}})
    return rules


def link_checks(rules: list[dict], measured: list[dict]) -> None:
    """For an LLM-authored rule, list the measured rules about the same words (`backticked` in its text). Only a pointer: the
    verdict (confirmed / contradicted) is left to a reader, or to a later check that records who made it."""
    by_word: dict[str, list[str]] = {}
    for m in measured:
        w = m["scope"].get("target")
        if w:
            by_word.setdefault(w, []).append(m["id"])
    for r in rules:
        if r["kind"] != "llm-authored":
            continue
        words = {w.lower() for w in re.findall(r"`([^`\s]+)`", r["text"])}
        related = sorted({i for w in words for i in by_word.get(w, [])})
        if related:
            r["check"] = {"status": "unchecked", "related_measured": related}


def _lang_of(name: str) -> str:
    return name.split("_")[0].split(".")[0]


def build(out: Path = OUT, extra_versions: dict[str, list[Path]] | None = None) -> dict:
    """Every config/llm_conventions/<name>.md (+ older versions given in `extra_versions`, e.g. texts a published layer used) and
    every measured profile -> the tree. Returns the manifest."""
    manifest = {"dataset": HF_REPO, "built": dt.date.today().isoformat(), "git_head": _git("rev-parse", "--short", "HEAD"),
                "sets": {}, "measured": {}}
    measured_all: dict[str, list[dict]] = {}
    for fp in sorted(PROFILES.glob("*.json")) if PROFILES.exists() else []:
        prof = json.loads(fp.read_text(encoding="utf-8"))
        lang, pname = fp.stem.split(".", 1)
        prov = {"source": prof.get("gold_source"), "license": prof.get("gold_license"), "base_text": prof.get("base_text"),
                "books": prof.get("books"), "method": prof.get("method"), "script": prof.get("script"), "date": prof.get("date")}
        ms = measured_rules(prof, lang, pname, prov)
        measured_all.setdefault(lang, []).extend(ms)
        d = out / lang / "measured" / pname
        d.mkdir(parents=True, exist_ok=True)
        (d / "rules.json").write_text(json.dumps({"license": prov["license"], "rules": ms}, ensure_ascii=False, indent=1) + "\n",
                                      encoding="utf-8")
        manifest["measured"][f"{lang}/measured/{pname}"] = {"rules": len(ms), "license": prov["license"], "source": prov["source"]}
    for fp in sorted(CONV_DIR.glob("*.md")):
        name = fp.stem
        versions = [fp] + sorted((CONV_DIR / "versions").glob(f"{name}.*.md")) + list((extra_versions or {}).get(name, []))
        ov_fp = CONV_DIR / f"{name}.attribution.json"
        overrides = json.loads(ov_fp.read_text(encoding="utf-8")) if ov_fp.exists() else None
        for v in versions:
            text = v.read_text(encoding="utf-8")
            h = sha256(text)
            lang = _lang_of(name)
            hist = file_history(fp) if v == fp else {"file": str(fp.relative_to(REPO)), "earlier_text": str(v.relative_to(REPO))
                                                      if v.is_relative_to(REPO) else str(v), **{f"earlier_text_{k}": x for k, x in
                                                      file_history(v).items() if k != "file"}}
            rules = rules_from_text(name, lang, text, hist, overrides)
            link_checks(rules, measured_all.get(lang, []))
            d = out / lang / name / h[:8]
            d.mkdir(parents=True, exist_ok=True)
            (d / "conventions.md").write_text(text, encoding="utf-8")
            (d / "rules.json").write_text(json.dumps({"license": "CC0-1.0", "sha256": h, "rules": rules}, ensure_ascii=False,
                                                     indent=1) + "\n", encoding="utf-8")
            kinds: dict[str, int] = {}
            for r in rules:
                kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
            manifest["sets"][f"{lang}/{name}/{h[:8]}"] = {"name": name, "sha256": h, "current": v == fp, "rules": kinds,
                                                          "related_measured": sum(1 for r in rules if r.get("check", {}).get("related_measured"))}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return manifest


def pin_layer(layer_dir: Path, by_book: dict[str, str], out: Path = OUT) -> Path:
    """Write <layer>/_conventions.json: for each book the conventions version (a path in this dataset) it was aligned with, and
    the rules of those versions, so the layer is self-contained."""
    sets = sorted(set(by_book.values()))
    payload = {"dataset": HF_REPO, "books": by_book, "sets": {}}
    for s in sets:
        d = out / s
        if not (d / "rules.json").exists():
            raise SystemExit(f"[conventions] {s} not built (run --build first)")
        payload["sets"][s] = json.loads((d / "rules.json").read_text(encoding="utf-8"))
    fp = layer_dir / "_conventions.json"
    fp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return fp


def set_path(name: str, text: str) -> str:
    return f"{_lang_of(name)}/{name}/{sha256(text)[:8]}"


def publish(push: bool, out: Path = OUT) -> None:
    from lexeme_aligner.hf_bulk_publish import install_timeout, publish_chunked
    install_timeout()
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file() and not p.name.startswith("."))
    publish_chunked(out, HF_REPO, files, create=True, dry_run=not push, label="alignment-conventions", detect_deletions=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--version", action="append", default=[], metavar="NAME=PATH",
                    help="also publish an older text of NAME that a layer used (repeatable)")
    ap.add_argument("--pin-layer", type=Path)
    ap.add_argument("--name", help="with --pin-layer: the conventions name every book used")
    ap.add_argument("--book-map", type=Path, help="with --pin-layer: JSON {BOOK: path to the exact .md text that book used}")
    ap.add_argument("--publish", action="store_true"); ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)
    if a.build:
        extra: dict[str, list[Path]] = {}
        for nv in a.version:
            n, p = nv.split("=", 1)
            extra.setdefault(n, []).append(Path(p))
        man = build(extra_versions=extra)
        print(json.dumps({k: v for k, v in man.items() if k in ("sets", "measured")}, ensure_ascii=False, indent=1))
    if a.pin_layer:
        books = sorted(p.name.split("_")[0] for p in a.pin_layer.glob("*.json") if not p.name.startswith("_") and "." not in p.stem)
        if a.book_map:
            m = json.loads(a.book_map.read_text(encoding="utf-8"))
            name = a.name or a.pin_layer.name.split("+")[0]
            by_book = {b: set_path(a.name, Path(p).read_text(encoding="utf-8")) for b, p in m.items()}
        else:
            text = (CONV_DIR / f"{a.name}.md").read_text(encoding="utf-8")
            by_book = {b: set_path(a.name, text) for b in books}
        print(pin_layer(a.pin_layer, by_book))
    if a.publish:
        publish(a.push)
    return 0


if __name__ == "__main__":
    sys.exit(main())
