"""gram-struct builder — docs/architecture.md §2. ADDITIVE: reads only files that already exist, writes
only under `--out` (default `config/gram_struct/`), changes no existing reader, config or module.

One per-language grammatical fact sheet, fused from every source this repo already has, split into
four provenance partitions so licensing is a directory boundary (§2 "Publication is planned"):

  external/<iso>.json   CC-BY-4.0     direction slots from Grambank (computed here from
                                      config/grambank/features.json via span_extension.direction_for /
                                      possession_direction_for — the SAME folding every consumer uses)
                                      or WALS (from config/typology/directions.json), plus existence
                                      facts computed with analyze_language.RISK_RULES' own polarities.
  imputed/<iso>.json    CC-BY-SA-4.0  lang2vec-sourced direction slots from directions.json, kept apart.
  derived/<iso>.json    CC0-1.0       config/constituent_order/<iso>.json, sha256-pinned.
  measured/<iso>.json   CC0-1.0       config/spanext_flags.json + config/fertility_flags.json verdicts,
                                      dated, gold named; conventions_md path when one exists.
  <iso>.json                          the merge of the four — no key may come from two partitions.

Contract (§2): a slot with `"direction": null` and a `source` is a FACT ("Grambank knows both codes
and they resolve to no single side"); an absent key means UNKNOWN (no source covers it). Existence
facts carry both `flagged` (RISK_RULES' own meaning for that polarity) and `present` (the plain
reading: any_one -> flagged; not_all_one / all_zero -> not flagged), so neither has to be inferred.

    python3 -m lexeme_aligner.gram_struct --build            # published languages (lexeme-alignments manifest)
    python3 -m lexeme_aligner.gram_struct --build --all      # every iso any source knows
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

from lexeme_aligner import typology
from lexeme_aligner.analyze_language import RISK_RULES
from lexeme_aligner.grambank_fetch import FEATURES as GRAMBANK_FEATURES
from lexeme_aligner.span_extension import _TYPOLOGY_SLOT, direction_for, possession_direction_for

OUT_DIR = Path("config/gram_struct")
PARTITIONS = ("external", "imputed", "derived", "measured")

_FEATURES_FILE = Path("config/grambank/features.json")
_DIRECTIONS_FILE = typology._OUT
_CONSTITUENT_DIR = Path("config/constituent_order")
_SPANEXT_FLAGS = Path("config/spanext_flags.json")
_FERTILITY_FLAGS = Path("config/fertility_flags.json")
_GOLD_LANGS = Path("config/gold_langs.json")
_CONVENTIONS_DIR = Path("config/llm_conventions")
_PUBLISHED_MANIFEST = Path("publish/lexeme-alignments/manifest.json")

# Every spanext_flags.json / fertility_flags.json verdict in the tree today was measured on this one
# day (the session that built both files); the date is recorded per fact so a later re-measurement
# can move ONE entry's date without touching the rest.
_MEASURED_DATE = "2026-09-24"

# grambank_fetch.FEATURES group -> typology slot (span_extension's own mapping, reversed here).
_SLOT_TO_GROUP = {slot: group for group, slot in _TYPOLOGY_SLOT.items()}
# Existence groups: RISK_RULES' own polarity where it names the group, else the documented default.
_EXISTENCE_POLARITY = {risk: polarity for risk, _pos, _thr, _desc, polarity, _hint in RISK_RULES}
_EXISTENCE_GROUPS = [g for g in GRAMBANK_FEATURES if g not in _SLOT_TO_GROUP.values()]


def _load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_grambank_languages(path: Path = _FEATURES_FILE) -> dict[str, dict[str, str]]:
    """grambank_fetch --build writes the per-iso map under a top-level "languages" key beside its
    "_coverage"/"_doc"/"_features"/"_pins" metadata; accept a bare iso->codes map too (tests)."""
    doc = _load_json(path)
    if isinstance(doc.get("languages"), dict):
        return doc["languages"]
    return {k: v for k, v in doc.items() if not k.startswith("_") and isinstance(v, dict)}


# ── external ──────────────────────────────────────────────────────────────────────────────────────
def grambank_slot(grambank: dict[str, str], slot: str) -> dict | None:
    """One direction slot from Grambank alone, via the SAME helpers every consumer uses. Returns the
    §2 record, a null-direction record when every code is known but no side resolves, or None when
    Grambank does not know the codes (unknown, key absent)."""
    group = _SLOT_TO_GROUP[slot]
    codes = list(GRAMBANK_FEATURES[group])
    if slot == "possessor":
        direction = possession_direction_for(grambank)
    else:
        direction = direction_for(grambank, group)
    if direction is None and not all(c in grambank for c in codes):
        return None
    return {"direction": direction, "source": "grambank", "codes": codes, "confidence": 1.0}


def existence_fact(grambank: dict[str, str], group: str) -> dict | None:
    """Existence fact for one FEATURES group, using analyze_language.analyze()'s own polarity
    expressions verbatim (any_one / not_all_one / all_zero). None when Grambank knows none of the
    group's codes for this language."""
    codes = list(GRAMBANK_FEATURES[group])
    known = [c for c in codes if c in grambank]
    if not known:
        return None
    polarity = _EXISTENCE_POLARITY.get(group, "any_one")
    if polarity == "not_all_one":
        flagged = not all(grambank.get(f) == "1" for f in codes)
        matched = [f for f in codes if grambank.get(f) != "1"]
    elif polarity == "all_zero":
        flagged = all(grambank.get(f) == "0" for f in codes)
        matched = list(codes)
    else:
        flagged = any(grambank.get(f) == "1" for f in codes)
        matched = [f for f in codes if grambank.get(f) == "1"]
    present = flagged if polarity == "any_one" else not flagged
    return {"present": present, "flagged": flagged, "polarity": polarity, "source": "grambank",
            "codes": codes, "known": known, "matched": matched}


def build_external(iso: str, grambank: dict[str, str] | None, directions: dict, stats: collections.Counter
                   ) -> dict:
    out: dict = {}
    dir_entry = directions.get(iso, {})
    for slot in typology.SLOTS:
        rec = grambank_slot(grambank, slot) if grambank is not None else None
        table = dir_entry.get(slot)
        if rec is not None:
            if table and table.get("source") == "grambank" and table.get("direction") != rec["direction"]:
                stats["grambank_direction_mismatch"] += 1
            out[slot] = rec
        elif table and table.get("source") == "wals":
            param = typology._WALS_PARAMS.get(slot)
            out[slot] = {"direction": table["direction"], "source": "wals",
                         "codes": [f"WALS:{param[0]}"] if param else [],
                         "confidence": table.get("confidence")}
        elif table and table.get("source") == "grambank":
            # directions.json knows a Grambank direction we could not recompute (features.json pin
            # drift) — keep the table's answer, cite the codes, count it.
            stats["grambank_direction_table_only"] += 1
            out[slot] = {"direction": table["direction"], "source": "grambank",
                         "codes": list(GRAMBANK_FEATURES[_SLOT_TO_GROUP[slot]]),
                         "confidence": table.get("confidence", 1.0)}
        if slot in out:
            stats[f"slot:{slot}:{out[slot]['source']}"] += 1
            if out[slot]["direction"] is None:
                stats[f"slot:{slot}:null"] += 1
    if grambank is not None:
        for group in _EXISTENCE_GROUPS:
            fact = existence_fact(grambank, group)
            if fact is not None:
                out[group] = fact
                stats[f"existence:{group}:{'present' if fact['present'] else 'absent'}"] += 1
    return out


# ── imputed ───────────────────────────────────────────────────────────────────────────────────────
def build_imputed(iso: str, directions: dict, external: dict, stats: collections.Counter) -> dict:
    out: dict = {}
    for slot, table in directions.get(iso, {}).items():
        if table.get("source") == "lang2vec" and slot not in external:
            out[slot] = {"direction": table["direction"], "source": "lang2vec",
                         "confidence": table.get("confidence")}
            stats[f"slot:{slot}:lang2vec"] += 1
    return out


# ── derived ───────────────────────────────────────────────────────────────────────────────────────
def build_derived(iso: str, constituent_dir: Path = _CONSTITUENT_DIR) -> dict:
    fp = Path(constituent_dir) / f"{iso}.json"
    if not fp.exists():
        return {}
    doc = _load_json(fp)
    rec = {"source": "derived", "content_sha256": _sha256(fp)}
    for key in ("tag", "verses_measured", "pair_order_kept", "function_drift"):
        if key in doc:
            rec[key] = doc[key]
    # TODO(multiword_rates): the per-POS multiword-rate audit is analyze_language.analyze()'s
    # `rates`, computed from a language's out/ jsonl on every run and never persisted; it joins this
    # partition once analyze() writes it somewhere pinned.
    return {"constituent_order": rec}


# ── measured ──────────────────────────────────────────────────────────────────────────────────────
def _gold_name(iso: str, gold_langs: dict) -> str | None:
    g = gold_langs.get(iso)
    if not isinstance(g, dict) or "gold" not in g:
        return None
    return f"{g['gold']}/{g['base_text']}" if g.get("base_text") else g["gold"]


def build_measured(iso: str, spanext: dict, fertility: dict, gold_langs: dict,
                   conventions_dir: Path = _CONVENTIONS_DIR) -> dict:
    mechanisms: dict = {}
    gold = _gold_name(iso, gold_langs)
    sx = spanext.get(iso, {})
    # Note routing: a flag-specific `_<prefix>_note` (e.g. `_definite_note`) belongs to that flag; the
    # entry-wide `_note` describes the entry's PRIMARY verdict, which spanext_flags.json lists FIRST
    # (hin: relation_trigger's measurement, not typology_fallback's) — so it attaches to the first
    # boolean flag only, never to every flag in the entry.
    bool_flags = [f for f, v in sx.items() if isinstance(v, bool)]
    for flag in bool_flags:
        value = sx[flag]
        note = sx.get(f"_{flag.split('_')[0]}_note")
        if note is None and flag == bool_flags[0]:
            note = sx.get("_note")
        rec = {"enabled": value, "source": "measured", "date": _MEASURED_DATE}
        if gold:
            rec["gold"] = gold
        if note:
            rec["note"] = note
        mechanisms[f"spanext.{flag}"] = rec
    fe = fertility.get(iso, {})
    if isinstance(fe.get("enabled"), bool):
        rec = {"enabled": fe["enabled"], "source": "measured", "date": _MEASURED_DATE}
        if isinstance(fe.get("lambda"), (int, float)):
            rec["lambda"] = float(fe["lambda"])
        if gold:
            rec["gold"] = gold
        if fe.get("_note"):
            rec["note"] = fe["_note"]
        mechanisms["fertility_priors"] = rec
    out: dict = {}
    if mechanisms:
        out["mechanisms"] = mechanisms
    conv = Path(conventions_dir) / f"{iso}.md"
    if conv.exists():
        out["conventions_md"] = str(conv)
    return out


# ── merge / build ─────────────────────────────────────────────────────────────────────────────────
def merge_partitions(iso: str, parts: dict[str, dict]) -> dict:
    """external -> imputed -> derived -> measured; a key set by two partitions is a build error."""
    merged: dict = {"iso": iso}
    for name in PARTITIONS:
        for key, value in parts.get(name, {}).items():
            if key in merged:
                raise ValueError(f"{iso}: key {key!r} set by two partitions (second: {name})")
            merged[key] = value
    return merged


def language_set(all_isos: bool, grambank_langs: dict, directions: dict, constituent_dir: Path,
                 spanext: dict, fertility: dict, manifest: Path = _PUBLISHED_MANIFEST) -> list[str]:
    if not all_isos:
        return sorted(_load_json(manifest).get("languages", {}))
    isos = set(grambank_langs) | set(directions)
    isos |= {p.stem for p in Path(constituent_dir).glob("*.json")}
    isos |= {k for k in spanext if not k.startswith("_")} | {k for k in fertility if not k.startswith("_")}
    return sorted(isos)


def build(out_dir: Path = OUT_DIR, all_isos: bool = False, *, features_file: Path = _FEATURES_FILE,
          directions_file: Path = _DIRECTIONS_FILE, constituent_dir: Path = _CONSTITUENT_DIR,
          spanext_file: Path = _SPANEXT_FLAGS, fertility_file: Path = _FERTILITY_FLAGS,
          gold_file: Path = _GOLD_LANGS, conventions_dir: Path = _CONVENTIONS_DIR,
          manifest: Path = _PUBLISHED_MANIFEST, isos: list[str] | None = None) -> dict:
    grambank_langs = load_grambank_languages(features_file)
    directions = {k: v for k, v in _load_json(directions_file).items() if not k.startswith("_")}
    spanext = _load_json(spanext_file)
    fertility = _load_json(fertility_file)
    gold_langs = _load_json(gold_file)
    if isos is None:
        isos = language_set(all_isos, grambank_langs, directions, constituent_dir, spanext, fertility, manifest)

    out_dir = Path(out_dir)
    for name in PARTITIONS:
        (out_dir / name).mkdir(parents=True, exist_ok=True)
    stats: collections.Counter = collections.Counter()
    written = collections.Counter()
    for iso in isos:
        parts = {
            "external": build_external(iso, grambank_langs.get(iso), directions, stats),
        }
        parts["imputed"] = build_imputed(iso, directions, parts["external"], stats)
        parts["derived"] = build_derived(iso, constituent_dir)
        parts["measured"] = build_measured(iso, spanext, fertility, gold_langs, conventions_dir)
        for name in PARTITIONS:
            if parts[name]:                                   # never write an empty per-language file
                (out_dir / name / f"{iso}.json").write_text(
                    json.dumps(parts[name], indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")
                written[name] += 1
        merged = merge_partitions(iso, parts)
        if len(merged) > 1:
            (out_dir / f"{iso}.json").write_text(
                json.dumps(merged, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            written["merged"] += 1

    coverage = {
        "languages_requested": len(isos),
        "languages_written": dict(written),
        "slots": {slot: {src: stats.get(f"slot:{slot}:{src}", 0) for src in ("grambank", "wals", "lang2vec", "null")}
                  for slot in typology.SLOTS},
        "existence": {g: {"present": stats.get(f"existence:{g}:present", 0),
                          "absent": stats.get(f"existence:{g}:absent", 0)} for g in _EXISTENCE_GROUPS},
        "grambank_direction_mismatch": stats.get("grambank_direction_mismatch", 0),
        "grambank_direction_table_only": stats.get("grambank_direction_table_only", 0),
        "measured_date": _MEASURED_DATE,
    }
    (out_dir / "_coverage.json").write_text(json.dumps(coverage, indent=1) + "\n", encoding="utf-8")
    (out_dir / "README.md").write_text(_README, encoding="utf-8")
    return coverage


_README = """---
license: other
license_name: mixed-per-partition
tags:
- typology
- multilingual
- bible
---

# gram-struct — per-language grammatical fact sheets

**Draft dataset card.** One JSON per language, fused from every source the lexeme-aligner already has,
split into four provenance partitions so that licensing is a directory boundary. Design and contract:
`docs/architecture.md` §2. Built by `python3 -m lexeme_aligner.gram_struct --build`; never hand-edited.

| partition | license | contents | provenance |
|---|---|---|---|
| `external/` | **CC-BY-4.0** | direction slots + existence facts | Grambank v1.0 (Skirgård et al. 2023, CC-BY-4.0; Glottolog for the Glottocode→ISO mapping, CC-BY-4.0) and WALS (Dryer & Haspelmath 2013, CLDF v2020.5, CC-BY-4.0). Every fact cites its source codes (`GB074`, `WALS:85A`). |
| `imputed/` | **CC-BY-SA-4.0** | direction slots only | lang2vec / URIEL `syntax_knn` (Littell et al. 2017, CC-BY-SA-4.0) — kept physically apart because share-alike applies to derivatives of these values. |
| `derived/` | **CC0-1.0** | our own alignment statistics (constituent-order profile) | computed from this project's alignments; no source text redistributed. |
| `measured/` | **CC0-1.0** | mechanism verdicts, dated, gold named | human-recorded after scoring against gold; never inferred. |
| `<iso>.json` | mixed (see above) | the merge of the four | convenience view; identical content, one file. |

## Contract

- A slot with `"direction": null` **and a `source`** is a fact: the source knows the codes and they
  resolve to no single side (e.g. Hindi `article`: GB022 = GB023 = 0). An **absent key means unknown**:
  no source covers this language for this slot. Consumers must gate on the difference.
- Existence facts carry `flagged` (the polarity's own meaning, from `analyze_language.RISK_RULES`) and
  `present` (the plain reading), plus `polarity`, `codes`, `known`, `matched`.
- `derived/` changes whenever the alignments are regenerated and carries `content_sha256`;
  `measured/` changes only when a human re-measures and re-dates an entry.
- No field in any file is computed from another field in the same file.
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", required=True)
    ap.add_argument("--all", action="store_true", help="every iso any source knows (default: published languages)")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)
    cov = build(a.out, all_isos=a.all)
    print(f"[gram_struct] {cov['languages_requested']} language(s) requested → written {cov['languages_written']} "
          f"under {a.out}", file=sys.stderr)
    print(json.dumps({k: cov[k] for k in ("slots", "existence", "grambank_direction_mismatch",
                                          "grambank_direction_table_only")}, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
