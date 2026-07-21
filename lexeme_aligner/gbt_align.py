"""Extract many-to-many source(hbo+grc)<->target OCCURRENCE alignment from globalbibletools/data's
id-suffix addressing scheme — the "occurrence layer" sitting on top of our own lexeme-level alignment
(see internal-docs/gbt-alignment-handover.md). One row per positionally-linked group.

Classification (validated by a full sweep of English, whole Bible, 448,269 source words, 2026-07-17;
CROSS-CHECKED against our own independently-built eflomal+gloss+merge French alignment — see the
`suffix_pending` note below for a correction the cross-check forced on the first-pass reading):
  - 1:1      — source id has exactly one plain target entry with real content.
  - 1:many   — source id has a plain entry AND >=1 suffix-addressed continuation slot appended
               after it (id "<base>-<offset>" means real target id = base+offset) whose computed
               real id does NOT match any other real source word in the verse — a true extra
               target-side slot for that ONE source concept. Rare (110 of 1,329 suffix entries in
               the English sweep) and in practice always null-content (a structural marker that
               the target segmentation needed an extra slot here, no recoverable text).
  - suffix_pending — a suffix slot's computed real id COINCIDES with a genuine, otherwise-untagged
               source word (Matt 1:13's genealogy: the article at id 309 is glossed "-", and
               309-01/-02 land exactly on ids 310/311, two separately-lemmatized Greek words for
               "Eliakim") AND that suffix's own gloss is null (1,196 of 1,201 such "recovered"
               cases). FIRST-PASS READING (WRONG, kept here as a lesson): this looked like gbt's
               own explicit "no rendering" marker. CROSS-CHECK (right): joined these ids against
               our own independently-built French merged-alignment output (eflomal+gloss+contest-
               rule) — 93.7% of English's 890 `suffix_pending` ids, and 93.2% of French's own 799,
               get a HIGH-CONFIDENCE alignment from our pipeline (mean score 0.905, e.g. Ἐλιακίμ ->
               "Éliakim Éliakim", Μαρίαν -> "Marie"). An authoritative "no target text" marker would
               not be independently alignable that cleanly, that often. This is gloss-project
               INCOMPLETENESS routed through the suffix-addressing mechanism (an id was created,
               content just hasn't been supplied yet) — the same kind of gap as `unglossed` below,
               distinguished only by HOW the placeholder id was created, not by editorial intent.
               A same-real_id collision with an ALREADY-plain-glossed id is separate junk (304
               cases, always null) and is discarded outright, never emitted as a row.
  - unglossed — a PLAIN (non-suffix) target entry whose gloss is null — provenance-distinct from
               `suffix_pending` above via an explicit `from_suffix` flag tracked through
               resolution, NOT inferrable from id shape alone (a resolved-suffix hit and a genuine
               plain hit both land on the same real id once folded). Same gloss-project-
               completeness gap as `suffix_pending`, just via a plain id instead of a computed one.
               English (99.7% glossed) has ZERO of these — its remaining uncovered words all route
               through the suffix mechanism instead; French (72.6% glossed) has ~121,000.
  - (resolved 1:1) — the remaining handful (5 of 1,201 "recovered" cases: Eliakim x2, Achim x2,
               Maria x1) carry REAL content and are folded into an ordinary 1:1 row at their real
               id — the anchor's id-range is just being reused as an address space for an
               otherwise-unreachable id, not expressing a real many:many span.
  - many:1   — one or more TRAILING source ids in a verse have NO target entry at all (neither
               plain nor suffix) and are immediately preceded by a source id that DOES have a
               non-null plain gloss. This IS a deliberate absorption convention, not incompleteness
               (unlike `suffix_pending`/`unglossed` above) — the missing id's content already rides
               on the preceding gloss (Hebrew construct-state compound names, e.g. "עֵֽין־ גֶּֽדִי"
               (Ein + Gedi) tagged only at the first word as "Engedi"). 83.5% of gap-runs in the
               English sweep. CROSS-CHECK FINDING (actionable): joined French's 163 many:1 groups
               against our own merged alignment — we group them into one matching span only 1/163
               times; the rest we split the two source words onto unrelated targets (Sela + Ham-
               machlekoth, one Strong's H5555 repeated, gbt says "le Rocher des Séparations" one
               span -> our pipeline separately assigned "pourquoi"/"appela", neither correct). This
               is a genuine, gbt-independent quality signal: our eflomal+gloss+merge pipeline does
               NOT currently detect Hebrew construct-state compound-name spans at all.
  - dropped  — a source id has no target entry and no same-verse predecessor with a gloss to
               absorb into (typically a verse-initial elided function word, e.g. a dropped καὶ
               at the start of a sentence spanning a verse boundary). No positional signal at all
               is recoverable from this source. 16.5% of gap-runs.

`verse_ref` is an int matching lexeme_aligner.refs.encode() exactly — gbt's own verse "id" uses the
identical BBCCCVVV scheme with the same book-number table, so rows here join directly against
`ref` in align_<method>_<iso>_*.jsonl without any conversion.

    python3 -m lexeme_aligner.gbt_align --lang fra
    python3 -m lexeme_aligner.gbt_align --lang fra --book MAT   # single book, for spot-checks
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_DATA_DIR = Path("data/gbt")
_OUT_DIR = Path("resources/occurrence_align")

# gbt's own 2-digit book-number prefix (e.g. "40-Mat.json") matches refs.BOOK_NUMBERS values —
# build the reverse map once so book filenames can be filtered by 3-letter code if requested.
_BOOK_CODE_BY_NUM = {
    1: "GEN", 2: "EXO", 3: "LEV", 4: "NUM", 5: "DEU", 6: "JOS", 7: "JDG", 8: "RUT", 9: "1SA",
    10: "2SA", 11: "1KI", 12: "2KI", 13: "1CH", 14: "2CH", 15: "EZR", 16: "NEH", 17: "EST",
    18: "JOB", 19: "PSA", 20: "PRO", 21: "ECC", 22: "SNG", 23: "ISA", 24: "JER", 25: "LAM",
    26: "EZK", 27: "DAN", 28: "HOS", 29: "JOL", 30: "AMO", 31: "OBA", 32: "JON", 33: "MIC",
    34: "NAM", 35: "HAB", 36: "ZEP", 37: "HAG", 38: "ZEC", 39: "MAL", 40: "MAT", 41: "MRK",
    42: "LUK", 43: "JHN", 44: "ACT", 45: "ROM", 46: "1CO", 47: "2CO", 48: "GAL", 49: "EPH",
    50: "PHP", 51: "COL", 52: "1TH", 53: "2TH", 54: "1TI", 55: "2TI", 56: "TIT", 57: "PHM",
    58: "HEB", 59: "JAS", 60: "1PE", 61: "2PE", 62: "1JN", 63: "2JN", 64: "3JN", 65: "JUD",
    66: "REV",
}


def _book_num(filename: str) -> int:
    return int(filename.split("-", 1)[0])


def extract_verse(source_words: list[dict], target_words: list[dict]) -> list[dict]:
    """One verse's source (hbo+grc) + target-language words -> normalized occurrence groups.
    Two-phase: (1) build id groups via the suffix/absorption rules, (2) classify each group's
    `kind` from its final source:target shape — keeps the grouping logic and the labeling
    decision independent, so a `many:many` shape (unexpected; not seen in the English sweep) is
    surfaced rather than silently forced into one of the four known buckets."""
    source_ids = [int(w["id"]) for w in source_words]
    source_id_set = set(source_ids)
    plain: dict[int, str | None] = {}
    from_suffix: set[int] = set()                          # provenance: ids folded in via the suffix scheme
    raw_suffix: list[tuple[int, int, str | None]] = []      # (anchor, real_id, gloss), file order
    for w in target_words:
        wid = w["id"]
        gloss = w.get("gloss")
        if "-" in wid:
            base, suf = wid.split("-")
            raw_suffix.append((int(base), int(base) + int(suf), gloss))
        else:
            plain[int(wid)] = gloss

    # Resolve each suffix slot: if its computed real id names another genuine source word in
    # this verse, fold it into `plain` (an ordinary hit, classified 1:1 or `elided` below)
    # rather than the anchor's group. If that real id ALREADY has its own plain entry, the
    # suffix slot is redundant junk (verified: always null-content, never carries real text) —
    # discard it outright. Only a real_id with NO matching source word at all is a true 1:many
    # extension of the anchor.
    suffix_children: dict[int, list[tuple[int, str | None]]] = collections.defaultdict(list)
    for anchor, real_id, gloss in raw_suffix:
        if real_id in source_id_set:
            if real_id not in plain:
                plain[real_id] = gloss
                from_suffix.add(real_id)
            # else: real_id already plain-glossed — redundant suffix marker, discard
        else:
            suffix_children[anchor].append((real_id, gloss))
    suffix_covered = {rid for children in suffix_children.values() for rid, _ in children}

    groups: list[dict] = []
    i, n = 0, len(source_ids)
    while i < n:
        sid = source_ids[i]
        if sid in plain:
            children = suffix_children.get(sid, [])
            t_ids = [sid] + [rid for rid, _ in children]
            t_gloss = [plain[sid]] + [g for _, g in children]
            groups.append({"source_ids": [sid], "target_ids": t_ids, "target_gloss": t_gloss,
                           "from_suffix": sid in from_suffix})
            i += 1
        elif sid in suffix_covered:
            i += 1                                   # already represented as an earlier group's target slot
        else:
            run = [sid]
            j = i + 1
            while j < n and source_ids[j] not in plain and source_ids[j] not in suffix_covered:
                run.append(source_ids[j])
                j += 1
            prev = groups[-1] if groups else None
            if (prev and prev["source_ids"][-1] == run[0] - 1
                    and prev["target_gloss"] and prev["target_gloss"][-1] is not None):
                prev["source_ids"].extend(run)        # many:1 absorption into the immediate predecessor
            else:
                groups.append({"source_ids": run, "target_ids": [], "target_gloss": []})
            i = j

    text_by_id = {int(w["id"]): w["text"] for w in source_words}
    strong_by_id = {int(w["id"]): w["lemma"] for w in source_words}
    rows = []
    for g in groups:
        ns, nt = len(g["source_ids"]), len(g["target_ids"])
        is_null_1_1 = (ns, nt) == (1, 1) and g["target_gloss"][0] is None
        kind = ("dropped" if nt == 0
                else "suffix_pending" if is_null_1_1 and g.get("from_suffix")
                else "unglossed" if is_null_1_1
                else "1:1" if (ns, nt) == (1, 1)
                else "1:many" if ns == 1 else "many:1" if nt == 1 else "many:many")
        rows.append({
            "source_ids": g["source_ids"],
            "source_text": [text_by_id[s] for s in g["source_ids"]],
            "source_strong": [strong_by_id[s] for s in g["source_ids"]],
            "target_ids": g["target_ids"],
            "target_gloss": g["target_gloss"],
            "kind": kind,
        })
    return rows


def extract_book(hbo_book: dict, target_book: dict) -> list[dict]:
    target_by_verse = {v["id"]: v["words"] for ch in target_book["chapters"] for v in ch["verses"]}
    rows = []
    for ch in hbo_book["chapters"]:
        for v in ch["verses"]:
            target_words = target_by_verse.get(v["id"], [])
            for row in extract_verse(v["words"], target_words):
                row["verse_ref"] = int(v["id"])
                rows.append(row)
    return rows


def extract_lang(lang: str, data_dir: Path = _DATA_DIR, book_filter: str | None = None) -> list[dict]:
    hbo_dir = data_dir / "hbo+grc"
    lang_dir = data_dir / lang
    if not hbo_dir.exists() or not lang_dir.exists():
        raise SystemExit(f"[gbt_align] missing data dir(s): {hbo_dir} / {lang_dir} "
                          f"(see PROVENANCE.txt — data/gbt/ is out-of-band, gitignored)")
    rows = []
    for hbo_fp in sorted(hbo_dir.glob("*.json")):
        if book_filter and _BOOK_CODE_BY_NUM.get(_book_num(hbo_fp.name)) != book_filter.upper():
            continue
        target_fp = lang_dir / hbo_fp.name
        if not target_fp.exists():
            continue
        hbo_book = json.loads(hbo_fp.read_text(encoding="utf-8"))
        target_book = json.loads(target_fp.read_text(encoding="utf-8"))
        for row in extract_book(hbo_book, target_book):
            row["lang"] = lang
            row["source"] = "gbt"
            rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", required=True, help="target language code (gbt's own dir name, e.g. fra/spa/eng)")
    ap.add_argument("--book", default=None, help="restrict to one book (3-letter code, e.g. MAT) — for spot-checks")
    ap.add_argument("--data-dir", type=Path, default=_DATA_DIR)
    ap.add_argument("--out-dir", type=Path, default=_OUT_DIR)
    args = ap.parse_args()

    rows = extract_lang(args.lang, args.data_dir, args.book)
    kind_counts = collections.Counter(r["kind"] for r in rows)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_fp = args.out_dir / f"gbt_{args.lang}.jsonl"
    with out_fp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_source_words = sum(len(r["source_ids"]) for r in rows)
    print(f"[gbt_align] {args.lang}: {len(rows)} groups, {n_source_words} source words -> {out_fp}\n"
          f"  kind breakdown: {dict(kind_counts)}", file=sys.stderr)
    if kind_counts.get("many:many"):
        print(f"  NOTE: {kind_counts['many:many']} many:many groups found — unexpected shape, "
              f"not seen in the validation sweep; inspect before trusting downstream.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
