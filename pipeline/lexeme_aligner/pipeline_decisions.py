"""`config/pipeline_decisions.json` — a per-LANGUAGE, catalog-wide, PUBLISHED ledger of which of our own
derived-analytic mechanisms were applied to a language's alignment, and where the evidence for that
decision came from. Companion to (and NOT a replacement for) `compact_align.py`'s own `.meta.json`
sidecar's `rule` channel:

  - `rule` (compact-alignments' own per-position sidecar) answers "WHICH SPECIFIC MECHANISM produced or
    widened THIS ONE SPAN, in this one verse" — e.g. `"13:name_after"`. It is dense/sparse per-token
    data, scales with corpus volume (books x editions), and lives inside that one dataset only.
  - `pipeline_decisions.json` (this module) answers "was that mechanism even ENABLED for this language
    at all, and on what basis" — e.g. `hin.relation_trigger = {"value": true, "source": "measured vs
    Clear gold, ..."}`. It is one small object per language, scales with the number of MECHANISMS we've
    built (not corpus volume), and is published IDENTICALLY alongside every dataset a client might
    download (lexeme-alignments / aligned_mwe / senses_attested / compact-alignments) — see
    `_PUBLISH_ROOTS` below — so a client working with just one of those repos never has to fetch a
    different one to interpret it.

Two kinds of decision get folded into one shape here, `{"value": ..., "source": ..., ...}` (generalizing
`typology.py`'s own per-slot `{direction, source, confidence}` pattern):

  1. ALWAYS-ON mechanisms (`always_on_mechanisms`) — `span_extension.py`'s base RISK_RULES path
     (case_marking / articles / possession_affix / tam_auxiliary / subject_indexing). These need NO
     opt-in CLI flag at all; they fire whenever `analyze_language.analyze()`'s own empirical audit finds
     a genuine under-production anomaly for this language's ACTUAL run AND a direction resolves
     (Grambank directly, or gram_struct's fallback when `typology_fallback` is on for this language).
     This is exactly the gap found while building the RUT 1:1 example (2026-09-28): the "name_after"
     rule label visible on a real Hindi span comes from THIS path, which had no ledger entry at all
     before — only the opt-in flags did.
  2. OPT-IN mechanisms (`opt_in_mechanisms`) — the CLI-flag-gated, "measure once, remember it in one
     line" verdicts already recorded in `config/spanext_flags.json` / `config/fertility_flags.json`.
     This module reads those files (via their own existing loaders, `span_extension.load_spanext_flags`
     / `fertility_priors.load_fertility_flags`) rather than re-parsing them, so there is exactly one
     source of truth for each verdict; this ledger only carries the structured value + a short pointer
     back to the full prose reasoning in those files, not a duplicate of the reasoning itself.
  3. `target_normalization` (R10, 2026-09-28) — the target-side stemming decision, self-derived (no
     config file at all — see `target_morph.should_stem`), included here as the third kind alongside 1/2.

    python3 -m lexeme_aligner.pipeline_decisions --tag hinirv --publish-iso hin --usj-dir pipeline/work/ingest-cache/usj-hinirv
    # a pooled multi-edition language: one --tag / --usj-dir pair per pooled edition
    python3 -m lexeme_aligner.pipeline_decisions --tag arb_vdv --tag arbasv --tag arbnav --tag arbwtc --publish-iso arb \\
        --usj-dir pipeline/work/ingest-cache/usj-arb_vdv --usj-dir pipeline/work/ingest-cache/usj-arbasv \\
        --usj-dir pipeline/work/ingest-cache/usj-arbnav --usj-dir pipeline/work/ingest-cache/usj-arbwtc
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lexeme_aligner.analyze_language import analyze, load_grambank
from lexeme_aligner.config import OUT, PRIOR_PACK
from lexeme_aligner.fertility_priors import load_fertility_flags
from lexeme_aligner.span_extension import (DIRECTION_FEATURES, base_mechanisms_enabled, direction_for,
                                            load_spanext_flags,
                                           possession_direction_for)
from lexeme_aligner.target_morph import should_stem

_DECISIONS_FILE = Path("config/pipeline_decisions.json")
# Every dataset a client might independently download — the ledger is published identically into
# whichever of these already exist on disk (a fresh checkout only has the ones actually built so far).
_PUBLISH_ROOTS = [Path("publish/lexeme-alignments"), Path("publish/aligned_mwe"),
                  Path("publish/senses_attested"), Path("publish/compact-alignments")]

_DOC = ("Per-language pipeline configuration decisions, with provenance. Per-token attribution for a "
       "SPECIFIC span lives in compact-alignments' own <BOOK>_<hash>.meta.json 'rule' channel instead "
       "-- this file explains WHY a mechanism was enabled at all, not which position it touched. See "
       "pipeline_decisions.py's own module docstring (in the lexeme-aligner source repo) for the full "
       "three-layer relationship. 'always-on' entries need no opt-in flag; 'opt-in' entries mirror "
       "config/spanext_flags.json / config/fertility_flags.json (this repo's own internal verdict "
       "files, which carry the full prose reasoning this ledger only summarizes). LOOKUP: keyed by the "
       "bare published iso, the same segment already in every other dataset's own file path/manifest "
       "nesting for that language -- no dataset publishes a separate pointer field to this file, since "
       "the key you need is already the one you're indexing that dataset by. `target_normalization` is "
       "the one entry with NO per-token representation anywhere (stemming is a language-wide eflomal "
       "training setting, constant across every position of an edition's alignment) -- this file is "
       "the ONLY place it is ever visible.")

_OPT_IN_SPANEXT_KEYS = ("relation_trigger", "definite_trigger", "typology_fallback",
                        "typology_fallback_articles", "typed_gate", "phrase_window_gate", "name_guard")


def _direction(risk: str, grambank: dict, publish_iso: str, typology_fallback: bool) -> str | None:
    """Direction lookup for the always-on RISK_RULES mechanisms that have a before/after placement
    concept at all — case_marking/articles (a before_id/after_id Grambank pair) and possession_affix
    (GB065's own ternary). tam_auxiliary/subject_indexing are pure existence checks with no direction."""
    iso_for_fallback = publish_iso if typology_fallback else None
    if risk in DIRECTION_FEATURES:
        return direction_for(grambank, DIRECTION_FEATURES[risk], iso=iso_for_fallback)
    if risk == "possession_affix":
        return possession_direction_for(grambank, iso=iso_for_fallback)
    return None


def always_on_mechanisms(tags: str | list[str], publish_iso: str, out_dir: Path = OUT,
                         prior_pack: Path = PRIOR_PACK, method: str = "eflomal") -> dict:
    """One entry per RISK_RULES risk key `analyze_language.analyze()` found genuinely firing for THIS
    language's ACTUAL run (real findings against this language's own empirical multiword rate, not a
    static per-language lookup — see that function's own docstring). `tags`: every pooled edition
    currently on disk for this language, NOT one arbitrarily chosen representative (2026-09-28 fix —
    `analyze`/`multiword_rates` sum counts across all of them; see those functions' own docstrings for
    why a single-tag pick was both unstable and a worse statistical estimate). `source` distinguishes a
    real Grambank match (`"grambank:GB072,GB074"`) from gram_struct's own fallback
    (`"gram_struct:<slot>"`, only reachable when `typology_fallback` is recorded `true` for this
    language in config/spanext_flags.json — the SAME setting `span_extension.extend_spans` consults)."""
    flags = load_spanext_flags(publish_iso)
    typology_fallback = bool(flags.get("typology_fallback"))
    report = analyze(tags, publish_iso, out_dir, prior_pack, method=method, use_typology=typology_fallback)
    grambank = load_grambank(publish_iso) or {}
    by_risk: dict[str, dict] = {}
    for f in report.get("findings", []):
        entry = by_risk.setdefault(f["risk"], {"value": True, "pos": []})
        entry["pos"].append(f["pos"])
        ids = f.get("grambank_ids") or []
        if ids and all(i.startswith("typology:") for i in ids):
            entry["source"] = f"gram_struct:{ids[0].split(':', 1)[1]}"
        elif ids:
            entry["source"] = "grambank:" + ",".join(ids)
    # E6 flip (2026-09-29): per-EDITION `base_mechanisms: false` in config/spanext_flags.json. The mechanism
    # is still "found firing" by the audit, but span_extension.extend_spans skips those editions, so the
    # ledger must say so: value false when EVERY pooled edition is gated off, else value true plus the
    # list of gated editions (a reader can then tell which editions carry no spanext layer).
    from lexeme_aligner.article_bound import is_bound
    if "articles" in by_risk and is_bound(publish_iso):
        by_risk["articles"]["value"] = False
        by_risk["articles"]["gated_source"] = ("derived (article_bound): the definite article is fused into the "
                                               "noun, so there is no free word to append")
    tag_list = [tags] if isinstance(tags, str) else list(tags)
    gated = sorted(t for t in tag_list
                   if not base_mechanisms_enabled(load_spanext_flags(publish_iso, tag=t)))
    for risk, entry in by_risk.items():
        entry["pos"] = sorted(set(entry["pos"]))
        d = _direction(risk, grambank, publish_iso, typology_fallback)
        if d:
            entry["direction"] = d
        if gated:
            entry["gated_off_editions"] = gated
            if len(gated) == len(tag_list):
                entry["value"] = False
                entry["gated_source"] = "cross-edition verification (E6) — see config/spanext_flags.json"
    return by_risk


def opt_in_mechanisms(publish_iso: str) -> dict:
    """The CLI-flag-gated mechanisms, projected straight from their own already-published verdict
    files — see span_extension.load_spanext_flags / fertility_priors.load_fertility_flags for the
    source of truth and the FULL prose reasoning; this only carries the structured value plus a short
    pointer back to it, never a duplicate of the reasoning itself. A key absent here means "no verdict
    recorded yet" (never measured), same absence convention those files already use — NOT "measured
    and off," which is instead an explicit `{"value": false, ...}` entry."""
    out: dict[str, dict] = {}
    sx = load_spanext_flags(publish_iso)
    for key in _OPT_IN_SPANEXT_KEYS:
        if key in sx:
            out[key] = {"value": sx[key], "source": "measured vs gold — see config/spanext_flags.json"}
    fert = load_fertility_flags(publish_iso)
    if "enabled" in fert:
        out["fertility_priors"] = {"value": fert["enabled"],
                                   "source": "measured vs gold — see config/fertility_flags.json"}
    if "lexeme_targets" in fert:
        out["lexeme_targets_fertility"] = {"value": fert["lexeme_targets"],
                                           "source": "measured vs gold — see config/fertility_flags.json"}
    return out


def target_normalization_decision(publish_iso: str, usj_dirs: str | Path | list[str | Path] | None = None
                                 ) -> dict:
    """R10 (2026-09-28): the target-side stemming decision — self-derived, no config file at all (see
    target_morph.should_stem / STEM_RATIO_THRESHOLD's own docstring for the real calibration). `usj_dirs`
    may be every pooled edition's own directory — `target_morph._learn` pools their text together
    rather than computing from one arbitrarily-chosen edition (same 2026-09-28 fix as
    `always_on_mechanisms`); only actually consulted when the language's shared `target_morph` cache
    doesn't have `n_tokens` yet (an already-`n_tokens`-bearing cache is read as-is, whichever edition(s)
    originally built it)."""
    use_stem, ratio = should_stem(publish_iso, usj_dir=usj_dirs)
    entry = {"value": "stem" if use_stem else "surface"}
    entry["source"] = (f"self-derived (target_morph.py tokens/type={ratio:.2f})" if ratio is not None
                       else "unavailable (no target_morph cache and no usj_dir given)")
    # 2026-09-29: run_pilot decides per EDITION (it passes one edition's usj_dir), this ledger decides on the
    # POOLED text. Measured over all 235 multi-edition languages: 40 of 629 editions (33 languages, 6.4%) get
    # the opposite decision from the pooled one. Record those editions so the ledger describes what was run.
    if isinstance(usj_dirs, (list, tuple)) and len(usj_dirs) > 1 and ratio is not None:
        over = {}
        for d in usj_dirs:
            ed_stem, ed_ratio = should_stem(publish_iso, usj_dir=d)
            if ed_ratio is not None and ed_stem != use_stem:
                over[Path(d).name.removeprefix("usj-")] = "stem" if ed_stem else "surface"
        if over:
            entry["edition_overrides"] = dict(sorted(over.items()))
    return entry


def build_decisions(tags: str | list[str], publish_iso: str,
                    usj_dirs: str | Path | list[str | Path] | None = None, out_dir: Path = OUT,
                    prior_pack: Path = PRIOR_PACK, method: str = "eflomal") -> dict:
    """One language's full ledger entry — always-on mechanisms + opt-in mechanisms + the stemming
    decision. See this module's own docstring for why these three sources together are what a client
    needs to fully explain a compact-alignments 'rule' sidecar entry. `tags`/`usj_dirs`: every pooled
    edition currently on disk for this language (a bare string/path is also accepted for a
    single-edition language, kept as a convenience, not a different code path)."""
    doc: dict = {}
    doc.update(always_on_mechanisms(tags, publish_iso, out_dir, prior_pack, method))
    doc.update(opt_in_mechanisms(publish_iso))
    doc["target_normalization"] = target_normalization_decision(publish_iso, usj_dirs)
    return doc


def write_decisions(iso_entries: dict[str, dict], config_path: Path = _DECISIONS_FILE,
                    publish_roots: list[Path] = _PUBLISH_ROOTS) -> None:
    """Merges `iso_entries` into the canonical, deterministic (sorted-keys, timestamp-free) config file
    — same diffability convention export_lex/compact_align's own update_manifest already use — then
    copies the IDENTICAL content into every dataset root that already exists on disk. Copying here
    (rather than leaving each publish script to remember its own copy) is deliberate: config/
    contest_rule.json's own copy into publish/lexeme-alignments/contest_rule.json already drifted out
    of sync silently (no generator, someone placed it once by hand) — this function is the fix for that
    failure mode being repeated with a second file."""
    doc = {"_doc": _DOC}
    if config_path.exists():
        doc.update({k: v for k, v in json.loads(config_path.read_text(encoding="utf-8")).items()
                   if k != "_doc"})
    doc.update(iso_entries)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    config_path.write_text(text, encoding="utf-8")
    for root in publish_roots:
        if root.exists():
            (root / "pipeline_decisions.json").write_text(text, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", dest="tags", action="append", required=True,
                    help="internal alignment TAG (align_<method>_<tag>_*.jsonl) the always-on RISK_RULES "
                         "audit reads — repeatable, pass one --tag per pooled edition of this language "
                         "so the audit is a real pooled aggregate, not one arbitrarily-chosen edition")
    ap.add_argument("--publish-iso", required=True, help="bare published iso — the ledger's own key")
    ap.add_argument("--usj-dir", dest="usj_dirs", type=Path, action="append", default=None,
                    help="repeatable, one per pooled edition — only needed if target_morph has no "
                         "cache yet for this language (an existing cache is read as-is)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK)
    ap.add_argument("--method", default="eflomal")
    ap.add_argument("--config-path", type=Path, default=_DECISIONS_FILE)
    ap.add_argument("--no-publish", action="store_true",
                    help="write only --config-path, skip copying into publish/*/pipeline_decisions.json "
                         "— use for a one-off look/test so a single-language run doesn't touch the real "
                         "publish tree by accident")
    a = ap.parse_args(argv)
    entry = build_decisions(a.tags, a.publish_iso, usj_dirs=a.usj_dirs, out_dir=a.out,
                            prior_pack=a.prior_pack, method=a.method)
    write_decisions({a.publish_iso: entry}, config_path=a.config_path,
                    publish_roots=[] if a.no_publish else _PUBLISH_ROOTS)
    print(f"[pipeline_decisions] {a.publish_iso}: {sorted(entry)} -> {a.config_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
