"""Roadmap E2 (internal-docs/aim1-typology-source-structure-plan.md §4R, "### edition-struct"): the
per-edition analog of `gram_struct.py` — one record per edition TAG (never the bare iso; edition-struct
answers "what is this text and how do I read it", gram-struct answers "how does this language render
source structure", see `docs/architecture.md` §5) built ADDITIVELY from files that already exist and
are already read directly by their own consumers. This module changes NO reader — every existing
`config/pins/<tag>.json` / `config/textual_basis.json` / `config/text_strip_rules.json` /
`config/dbt_catalog/` consumer keeps reading its own file exactly as before; `edition_struct.py` is a
NEW, additive merge on top, for a future reader that wants one record instead of four lookups.

Two kinds of field per record, explicit (never blurred, same rule gram-struct's `source` tag enforces):
  - `pinned` — frozen at ingest/fetch time, never recomputed: provider, version_id, name, license_url,
    sha256, book count (from `config/pins/<tag>.json`), plus helloAO's own `textDirection`/`licenseUrl`/
    `sha256` where that catalog covers the same edition (`config/dbt_catalog/helloao-translations.json`,
    joined by `id`/`version_id`).
  - `derived` — recomputed from our own analysis, changes if the analysis is redone: `textual_basis`
    (TR/NA/mixed/critical/byzantine verdict from `config/textual_basis.json`'s 15-diagnostic-verse
    check), `text_strip` (opt-in bracket/paren stripping decision from `config/text_strip_rules.json`,
    default "none" when the edition has no entry there), `script` (ISO 15924, from bcv-query's sibling
    `languages.db` `language.scripts` column, keyed by the record's own `iso` — read-only, same sibling
    file `kin_prior.py` already reads).

`iso` is a FIELD on the record, never the key (the whole point of edition-struct existing separately
from gram-struct) — resolved from `publish/compact-alignments/manifest.json`'s own `languages.<iso>.
editions.<tag>` reverse index, the same authoritative "which tag belongs to which language" source the
rest of this pipeline already treats as ground truth. Not every pinned edition has been aligned/
published yet (2,042 pins vs 1,726 tags reachable that way) — for those, `iso` is `null` with
`iso_source: "unresolved"` rather than a guess; this is the same honest-gap convention `gram_struct.py`
uses for its own no-source count, not a bug to paper over.

    python -m lexeme_aligner.edition_struct --build
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

OUT_DIR = Path("config/edition_struct")
_PINS_DIR = Path("config/pins")
_TEXTUAL_BASIS_FILE = Path("config/textual_basis.json")
_TEXT_STRIP_FILE = Path("config/text_strip_rules.json")
_HELLOAO_FILE = Path("config/dbt_catalog/helloao-translations.json")
_COMPACT_MANIFEST = Path("publish/compact-alignments/manifest.json")
_LANGUAGES_DB = Path("/home/lgunnars/dev/bcv-commons/bcv-query/resources/languages/languages.db")
_EBIBLE_TRANSLATIONS_CSV = Path("config/ebible/translations.csv")
_EBIBLE_URL_RE = re.compile(r"ebible\.org/Scriptures/details\.php\?id=([\w.-]+)")


def _load_json(path: Path) -> dict:
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_tag_to_iso(manifest_path: Path = _COMPACT_MANIFEST) -> dict[str, str]:
    """{tag_lower: iso} — inverts compact-alignments' own `languages.<iso>.editions.<tag>` index, the
    authoritative tag->iso source (same one `derive_typology.py`'s `primary_edition()` reads, in the
    other direction). Keyed by LOWERCASED tag: `config/pins/<tag>.json` filenames are lowercase
    (`aaamlt.json`), but compact-alignments' own `editions` keys are UPPERCASE (`AAAMLT`) — a real bug
    found while building this module (a naive exact-string join silently resolved only 929 of the
    possible 1,726 tags, losing 797 to casing alone) — the same class of bug roadmap D0 already found
    and fixed in `derive_typology.primary_edition()` for the same underlying reason. Callers must look
    up with `tag.lower()`, matching `build_record`'s own usage below. A tag absent here (case-
    insensitively) has simply never been aligned/published under any language — not a further bug."""
    manifest = _load_json(manifest_path)
    out: dict[str, str] = {}
    for iso, entry in manifest.get("languages", {}).items():
        for tag in entry.get("editions", {}):
            out[tag.lower()] = iso
    return out


def load_helloao_by_version_id(path: Path = _HELLOAO_FILE) -> dict[str, dict]:
    doc = _load_json(path)
    return {t["id"]: t for t in doc.get("translations", []) if t.get("id")}


def load_scripts(db_path: Path = _LANGUAGES_DB) -> dict[str, str]:
    """{iso: script} from bcv-query's sibling languages.db, read-only. Degrades to {} if the sibling
    repo/DB is absent (same posture as kin_prior.py's own optional dependency on this file)."""
    if not Path(db_path).exists():
        return {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute("select iso639_3, scripts from language where scripts is not null and scripts != ''")
        return {iso: scripts.split(",")[0].strip() for iso, scripts in rows}
    finally:
        con.close()


def load_ebible_translations(path: Path = _EBIBLE_TRANSLATIONS_CSV) -> dict[str, dict]:
    """Roadmap E5 (reduced scope, 2026-09-26 — license/script/direction enrichment only; the coverage-
    expansion and versification-spec halves of the original plan item are moot, see the plan doc).
    `{translationId: row}` from BibleNLP/ebible's `metadata/translations.csv` (1,362 rows as of
    2026-09-26, fetched from https://raw.githubusercontent.com/BibleNLP/ebible/main/metadata/
    translations.csv). Degrades to `{}` if the file is absent (same optional-dependency posture as
    `load_scripts()` above)."""
    if not Path(path).exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {row["translationId"]: row for row in csv.DictReader(f)}


def ebible_id_from_license_url(license_url: str | None) -> str | None:
    """The REAL, verified join key (found while building this): our own pins' `license_url` already
    carries ebible's own `translationId` as the `id=` query param whenever the edition is ebible-
    sourced (verified directly: hinirv.json's license_url is `.../details.php?id=hin2017`, and
    `hin2017` is exactly ebible's own `translationId` for that edition) — no iso/tag-based guessing
    needed, unlike the bcv-commons/bibles join this session built earlier today."""
    if not license_url:
        return None
    m = _EBIBLE_URL_RE.search(license_url)
    return m.group(1) if m else None


def _looks_like_a_real_iso(value: str | None) -> bool:
    """Guards against a real data-quality finding made while building this module: `config/pins/
    <tag>.json`'s own `iso` field is NOT reliably a bare ISO 639-3 code — for exactly 1,416 of 2,042
    pins (69%) it is a verbatim COPY of the tag itself (`aaamlt.json`'s `"iso"` is the string
    `"aaamlt"`, not `"aaa"`), evidently a placeholder some ingest path wrote when it had no real iso
    to record, not a genuine self-referential case. A real ISO 639-3 code is always exactly 3
    lowercase letters; the placeholder class never is (verified: 0 exceptions in the full pin set).
    The 10 pins where a 3-letter TAG happens to equal its own genuine 3-letter iso (a legitimate
    single-edition "primary tag == bare iso" language, `config/legacy_bare_iso_tags.json`'s own
    convention) are correctly NOT caught by this filter, since they pass the length/alpha check."""
    return bool(value) and len(value) == 3 and value.isalpha()


def build_record(tag: str, pin: dict, tag_to_iso: dict[str, str], helloao: dict[str, dict],
                 textual_basis: dict, text_strip: dict, scripts: dict[str, str],
                 ebible: dict[str, dict] | None = None) -> dict:
    """`iso` resolution order: (1) the pin's OWN `iso` field, but ONLY when it looks like a real ISO
    639-3 code (`_looks_like_a_real_iso` — see that function's own docstring for why this guard
    exists; 626 of 2,042 pins pass it). Where it does, this is the only source that covers a
    pinned-and-ingested edition that was never the one CHOSEN/pooled into a language's published
    output (e.g. a candidate edition superseded by another for the same language) — the compact-
    alignments reverse index misses exactly those (verified: 616 of the 626 differ from their own
    tag, e.g. `aazpkf` -> `aaz`). (2) the compact-alignments reverse index, used whenever the pin's
    own field fails the validity check OR is absent. When BOTH resolve and DISAGREE, that is a
    genuine anomaly worth surfacing, not silently picking one — recorded as `iso_conflict` alongside
    the pin's own (trusted, when valid) value."""
    pin_iso_raw = pin.get("iso")
    pin_iso = pin_iso_raw if _looks_like_a_real_iso(pin_iso_raw) else None
    compact_iso = tag_to_iso.get(tag.lower())
    if pin_iso:
        iso = pin_iso
        iso_source = "pin"
    elif compact_iso:
        iso = compact_iso
        iso_source = "compact-alignments"
    else:
        iso = None
        iso_source = "unresolved"
    iso_conflict = (pin_iso and compact_iso and pin_iso != compact_iso)
    ha = helloao.get(pin.get("version_id", ""))
    pinned = {
        "provider": pin.get("provider"),
        "version_id": pin.get("version_id"),
        "name": pin.get("name"),
        "license_url": pin.get("license_url") or (ha or {}).get("licenseUrl"),
        "sha256": pin.get("sha256") or (ha or {}).get("sha256"),
        "books": pin.get("books"),
        "language_name": pin.get("language_name"),
    }
    if ha:
        pinned["helloao"] = {"text_direction": ha.get("textDirection"), "id": ha.get("id")}
    tb = textual_basis.get(tag)
    ts = text_strip.get(tag)
    eb_id = ebible_id_from_license_url(pin.get("license_url"))
    eb = (ebible or {}).get(eb_id) if eb_id else None
    derived = {
        "textual_basis": {"verdict": tb["verdict"], "checked": tb["checked"], "present": tb["present"],
                          "bracketed": tb["bracketed"], "source": "diagnostic-verses"} if tb else None,
        "text_strip": {"strip_brackets": ts.get("strip_brackets", False),
                       "strip_parens_noise": ts.get("strip_parens_noise", False),
                       "reason": ts.get("reason"), "source": "manual"} if ts
                     else {"strip_brackets": False, "strip_parens_noise": False, "source": "default"},
        "script": {"code": scripts.get(iso), "source": "languages_db"} if iso in scripts else None,
        "ebible": {"license": eb.get("Copyright") or None,
                  "redistributable": eb.get("Redistributable") == "True",
                  "script": eb.get("script") or None,
                  "text_direction": eb.get("textDirection") or None,
                  "translation_id": eb_id,
                  "source": "ebible-translations-csv"} if eb else None,
    }
    rec = {"tag": tag, "iso": iso, "iso_source": iso_source, "pinned": pinned, "derived": derived}
    if iso_conflict:
        rec["iso_conflict"] = {"pin": pin_iso, "compact_alignments": compact_iso}
    return rec


def build(pins_dir: Path = _PINS_DIR, out_dir: Path = OUT_DIR, manifest_path: Path = _COMPACT_MANIFEST,
         helloao_file: Path = _HELLOAO_FILE, textual_basis_file: Path = _TEXTUAL_BASIS_FILE,
         text_strip_file: Path = _TEXT_STRIP_FILE, languages_db: Path = _LANGUAGES_DB,
         ebible_csv: Path = _EBIBLE_TRANSLATIONS_CSV) -> dict:
    tag_to_iso = build_tag_to_iso(manifest_path)
    helloao = load_helloao_by_version_id(helloao_file)
    textual_basis = _load_json(textual_basis_file).get("editions", {})
    text_strip = _load_json(text_strip_file).get("editions", {})
    scripts = load_scripts(languages_db)
    ebible = load_ebible_translations(ebible_csv)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = collections.Counter()
    n = 0
    for pin_fp in sorted(Path(pins_dir).glob("*.json")):
        tag = pin_fp.stem
        pin = json.loads(pin_fp.read_text(encoding="utf-8"))
        rec = build_record(tag, pin, tag_to_iso, helloao, textual_basis, text_strip, scripts, ebible)
        (out_dir / f"{tag}.json").write_text(
            json.dumps(rec, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        n += 1
        stats["resolved_iso" if rec["iso"] else "unresolved_iso"] += 1
        stats[f"iso_source:{rec['iso_source']}"] += 1
        stats["iso_conflict"] += "iso_conflict" in rec
        stats["has_textual_basis"] += bool(rec["derived"]["textual_basis"])
        stats["has_script"] += bool(rec["derived"]["script"])
        stats["has_helloao"] += "helloao" in rec["pinned"]
        stats["has_ebible"] += bool(rec["derived"]["ebible"])
    coverage = {"editions": n, **dict(stats)}
    (out_dir / "_coverage.json").write_text(json.dumps(coverage, indent=1) + "\n", encoding="utf-8")
    return coverage


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)
    if a.build:
        cov = build(out_dir=a.out)
        print(json.dumps(cov, indent=1), file=sys.stderr)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
