"""Source-side navigation map (plan internal-docs/aim1-three-track-evaluation-plan.md §2.6c, M1).

A per-content-token chart of the Hebrew/Greek source — computed ONCE from data that already exists in the
spine and config, shared by every target language, edition-independent. It answers "what KIND of source
token is this?" so a later run can decide where a mechanism applies and where a known hazard sits.

FORMAT — `<out-dir>/<BOOK>.json`, position-parallel to `publish/compact-alignments/_index/<BOOK>_lexemes.json`:
the SAME verse keys ("BOOK C:V") in the SAME order, and within a verse the SAME content-token ordinals
(raw UNPOOLED spine verse; `tok.strong and tok.is_content`, spine order — exactly
`compact_align.build_source_lexemes`' notion, so `srcOrd` indexes both files identically). Per verse:

    {"tags": [[<class>, ...], ...],   # one sorted list per content token, [] when untagged
     "flags": [<verse flag>, ...]}    # sorted; omitted classes simply absent

CLASSES (each derived ONLY from an existing column/config — nothing is invented):
  construct_head         HebToken.state == "construct"                       (OT; BHSA/MACULA state)
  construct_rectum       HebToken.rela == "rec"                              (OT; the governed dependent)
  construct_chain_member in a construct_group but neither head nor rectum    (OT; middle/other members)
  name                   prior-pack pos == "name" for the token's lexeme
  name_list              part of a run of >= NAME_LIST_MIN consecutive `name` CONTENT tokens in the verse
                         (non-content tokens — conjunctions, particles — between names are skipped, so
                         "X and Y and Z" counts; a genealogy with a content noun between names does not)
  light                  lexeme in config/light_lexemes.json
  assimilated_article    idx-1 is an `after_idx` in spine_assimilated_articles (the definite article
                         hidden inside the preceding preposition; same rule span_extension.compute_definite uses)
  case_genitive / case_dative   HebToken.case_ (NT; Greek case ending carries the relation)
  expected_fertility=<n> the R1 cross-language table (config/fertility/lexeme_targets.json) predicts a
                         span of n words: only anchors passing the SAME gate fertility_priors applies
                         (multi_langs >= 3 and f >= 2)
VERSE FLAGS: `name_dense` (>= NAME_LIST_MIN `name` content tokens anywhere in the verse).

DELIBERATELY NOT EMITTED
  elided_copula          plan §2.6c lists it, but no existing column identifies a verbless nominal clause
                         reliably (BHSA `function`/Greek `role` mark predicates, not the ABSENCE of a
                         copula); a heuristic would be a fake chart — omitted until it can be derived.
  bridged / pooled_range edition-dependent (a property of a target edition's verse markers, not the source).
  superscription         Psalm-title tokens are already forced `is_content=False` by HebrewSource, so they
                         never receive an ordinal here at all — consistent with the compact-alignments index.

    python3 -m lexeme_aligner.eval.source_map --all --out-dir pipeline/work/source-map
    python3 -m lexeme_aligner.eval.source_map --books RUT JON
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

NAME_LIST_MIN = 3
FERTILITY_MIN_LANGS = 3      # same gate as fertility_priors.build_fertility_priors(min_langs=3)
FERTILITY_MIN_F = 2
DEFAULT_OUT = Path("pipeline/work/source-map")
_LIGHT_FILE = Path("config/light_lexemes.json")
_TARGETS_FILE = Path("config/fertility/lexeme_targets.json")
SCHEMA_VERSION = 1

CLASS_VOCABULARY = ["assimilated_article", "case_dative", "case_genitive", "construct_chain_member",
                    "construct_head", "construct_rectum", "expected_fertility=<n>", "light", "name",
                    "name_list"]
FLAG_VOCABULARY = ["name_dense"]


@dataclass
class MapContext:
    lex_pos: dict[str, str] = field(default_factory=dict)
    light: set[str] = field(default_factory=set)
    targets: dict[str, dict] = field(default_factory=dict)


def load_context(prior_pack: Path | None = None, light_path: Path | None = None,
                 targets_path: Path | None = None) -> MapContext:
    """All three inputs are optional data files; a missing one just yields fewer classes, never an error
    (same absence convention as `target_stopwords._load_light_lexemes` / `fertility_targets.load_targets`)."""
    from lexeme_aligner.config import PRIOR_PACK
    from lexeme_aligner.fertility_targets import load_targets
    from lexeme_aligner.gapfill import load_priors
    from lexeme_aligner.target_stopwords import _load_light_lexemes
    lex_pos, _ = load_priors(prior_pack or PRIOR_PACK)
    return MapContext(lex_pos=lex_pos,
                      light=_load_light_lexemes(light_path or _LIGHT_FILE),
                      targets=load_targets(targets_path or _TARGETS_FILE))


def _fertility_tag(strong: str | None, targets: dict[str, dict]) -> str | None:
    t = targets.get(strong) if strong else None
    if not t or t.get("multi_langs", 0) < FERTILITY_MIN_LANGS or int(t.get("f", 1)) < FERTILITY_MIN_F:
        return None
    return f"expected_fertility={int(t['f'])}"


def classify_verse(toks: list, ctx: MapContext, assimilated_after: set[int] | frozenset[int] = frozenset()
                   ) -> tuple[list[tuple[object, list[str]]], list[str]]:
    """([(token, sorted_tags)] for the verse's CONTENT tokens in spine order, sorted verse flags).
    `toks` is a HebrewSource.verse_tokens() list (or any objects with the same attributes)."""
    content = [t for t in toks if t.strong and t.is_content]
    tags: list[set[str]] = [set() for _ in content]
    is_name = [ctx.lex_pos.get(t.lexeme) == "name" for t in content]
    for i, t in enumerate(content):
        s = tags[i]
        if getattr(t, "state", None) == "construct":
            s.add("construct_head")
        if getattr(t, "rela", None) == "rec":
            s.add("construct_rectum")
        if (getattr(t, "construct_group", None) and getattr(t, "state", None) != "construct"
                and getattr(t, "rela", None) != "rec"):
            s.add("construct_chain_member")
        if is_name[i]:
            s.add("name")
        if t.lexeme in ctx.light:
            s.add("light")
        if (t.idx - 1) in assimilated_after:
            s.add("assimilated_article")
        case_ = getattr(t, "case_", None)
        if case_ in ("genitive", "dative"):
            s.add(f"case_{case_}")
        ft = _fertility_tag(t.strong, ctx.targets)
        if ft:
            s.add(ft)
    run = 0
    for i in range(len(content) + 1):
        if i < len(content) and is_name[i]:
            run += 1
            continue
        if run >= NAME_LIST_MIN:
            for j in range(i - run, i):
                tags[j].add("name_list")
        run = 0
    flags = ["name_dense"] if sum(is_name) >= NAME_LIST_MIN else []
    return [(t, sorted(s)) for t, s in zip(content, tags)], flags


def build_book_map(heb, book: str, ctx: MapContext) -> dict[str, dict]:
    """{"BOOK C:V": {"tags": [[...], ...], "flags": [...]}} — verse order = HebrewSource order, identical
    to `compact_align.build_source_lexemes`' key order."""
    out: dict[str, dict] = {}
    for ch in heb.chapters(book):
        for v in heb.verses(book, ch):
            toks = heb.verse_tokens(book, ch, v)
            assim = heb.assimilated_after_idx(book, ch, v) if getattr(heb, "has_assimilated_articles", False) \
                else frozenset()
            classified, flags = classify_verse(toks, ctx, assim)
            out[f"{book} {ch}:{v}"] = {"tags": [tg for _t, tg in classified], "flags": flags}
    return out


def write_book_map(heb, book: str, ctx: MapContext, out_dir: Path = DEFAULT_OUT) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / f"{book}.json"
    fp.write_text(json.dumps(build_book_map(heb, book, ctx), ensure_ascii=False, separators=(",", ":")) + "\n",
                  encoding="utf-8")
    return fp


def load_source_map(book: str, root: Path = DEFAULT_OUT) -> dict[str, dict]:
    return json.loads((Path(root) / f"{book}.json").read_text(encoding="utf-8"))


def class_counts(book_map: dict[str, dict]) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for verse in book_map.values():
        for tags in verse["tags"]:
            for tg in tags:
                c[tg.split("=")[0] if tg.startswith("expected_fertility") else tg] += 1
        for fl in verse["flags"]:
            c["flag:" + fl] += 1
    return c


def main(argv=None) -> int:
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--books", nargs="+")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    books = OT_BOOKS + NT_BOOKS if a.all else a.books
    heb, ctx = HebrewSource(), load_context()
    total: collections.Counter = collections.Counter()
    for book in books:
        fp = write_book_map(heb, book, ctx, a.out_dir)
        total += class_counts(json.loads(fp.read_text(encoding="utf-8")))
    meta = {"schema_version": SCHEMA_VERSION, "classes": CLASS_VOCABULARY, "verse_flags": FLAG_VOCABULARY,
            "name_list_min": NAME_LIST_MIN,
            "fertility_gate": {"min_langs": FERTILITY_MIN_LANGS, "min_f": FERTILITY_MIN_F},
            "not_emitted": ["elided_copula", "bridged", "pooled_range"],
            "books": len(books), "class_counts": dict(sorted(total.items()))}
    (a.out_dir / "_meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[source_map] {len(books)} book(s) → {a.out_dir}", file=sys.stderr)
    for k, n in sorted(total.items()):
        print(f"  {k:28s} {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
