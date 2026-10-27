"""TypeSafe Jev as a supplier for llm_edit's check step (Test A 2026-10-20; produced the published layer
ell_GreVamvas+llm+jev-v4 and the local HEBM95+llm+jev-v4, design v4; moved here from pipeline/work/jev-testA/ 2026-10-21).

Same proposal, same doubt mask, same conventions as `llm_edit --mode check3`; only the question format differs. One request per
verse (state = the verse, both sides, with the proposal; questions = one Choice per flagged id, asked in parallel):
  h<n> (flagged source word): which target word is its main rendering? options: `proposal` (keep its proposed words), `none`
       (no target word of its own), or any single target word t<j>.
  t<j> (flagged unowned target word): which source word does it render? options: h<n> ..., or `none` (renders nothing).
Answers are cached per request (sha of the body) under <work>/responses/, so re-scoring at another threshold never re-bills.
Cells: align_llm_jevA.<tag>.<BOOK>.jev-<θ>_<BOOK>.jsonl in pipeline/work/out (an answer is applied when its probability >= θ).

    .venv/bin/python -m lexeme_aligner.eval.jev_check --iso hinirv --publish-iso hin --book JON [--chapter 22] [--limit 3] [--design v4]

Then the layer: `fullalign_build --iso <tag> --publish-iso <iso> --usj-dir pipeline/work/ingest-cache/usj-<tag>
--llm-cell 'jevA.<tag>.{BOOK}.v4.jev-0' --llm-name jev-v4` (θ = 0: every answer is applied). Needs TYPESAFE_API_KEY (env or .env).
"""
from __future__ import annotations

import argparse
import collections, re
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lexeme_aligner.config import OUT, PRIOR_PACK
from lexeme_aligner.eval import llm_edit as le
from lexeme_aligner.eval.llm_prompt import _GENERIC_CONVENTIONS, _gram_tag

WORK = Path("pipeline/work/jev-testA")                # answer cache (responses/<sha>.json) + per-book summaries; kept at its test-A path
URL = "https://api.typesafe.ai/v1/systemone"
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
THRESHOLDS = (0.0, 0.5, 0.7, 0.9)
if os.environ.get("JEV_BASELINE_CELL"):          # experiments: also write the proposal unchanged (θ > 1), for a no-Jev baseline
    THRESHOLDS = THRESHOLDS + (1.01,)
LANG = {"hin": "Hindi", "eng": "English", "heb": "Modern Hebrew", "ell": "Modern Greek", "kvg": "Kuni-Boazi", "zin": "Zinza"}
# v4 function-word question: what "written inside a word" looks like in this translation language (heb text is unchanged, so its cache holds)
INSIDE_EXAMPLES = {"Modern Hebrew": " (for example ה for an article, ב/ל/מ/כ for a preposition, ו for and)",
                   "Modern Greek": " (for example a verb ending that carries a subject pronoun)"}
_CONV = le._CONTRACT_V3[le._CONTRACT_V3.index("This dataset's convention"):].replace("`=`", "`none`")


def api_key() -> str:
    if os.environ.get("TYPESAFE_API_KEY", "").startswith("apikey_"):
        return os.environ["TYPESAFE_API_KEY"]
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        if k.strip() in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY") and v.strip().lstrip("=").startswith("apikey_"):
            return v.strip().lstrip("=").strip()
    raise SystemExit("no TypeSafe key (apikey_...) in .env")


def _shown(p: le.Proposal, j: int) -> str:
    """Target word j as Jev sees it: as written, tone and vowel marks kept (since 2026-10-27; the token strips combining marks). Every
    lookup stays on `p.toks`. JEV_STRIPPED=1 shows the tokens as before, to rebuild older cells from the answer cache."""
    w = p.written
    return w[j] if w and not os.environ.get("JEV_STRIPPED") else p.toks[j]


def _words(p: le.Proposal, ts: list[int]) -> str:
    return " ".join(_shown(p, j) for j in ts)


MAX_SPAN = 4


def _spans(n: int, max_len: int) -> list[list[int]]:
    return [list(range(i, i + k)) for k in range(1, max_len + 1) for i in range(0, n - k + 1)]


def _span_id(ts: list[int]) -> str:
    return f"t{ts[0]}" if len(ts) == 1 else f"t{ts[0]}-{ts[-1]}"


def skip_owned(p: le.Proposal, skip_pairs: set | None) -> set[int]:
    """Target function words NOT asked in v4 (2026-10-26, cost): already owned by a source word that is not itself doubtful, as a
    (lowercased word, Strong's) pair Jev kept >= 97% of the time on books of the same edition (`learn_skip_pairs`). Measured on the
    KJV (train MAT+LUK, test MRK+JHN): 65% of these questions skipped, 24 of 1,261 changes lost."""
    if not skip_pairs:
        return set()
    owner_of = {j: h for h, s in p.owner.items() for j in s}
    strong = {t.idx: t.strong for t in p.heb}
    return {j for j in p.stop_t if j in owner_of and j not in p.doubt_t and owner_of[j] not in p.doubt_h
            and (p.toks[j].lower(), strong.get(owner_of[j])) in skip_pairs}


def learn_skip_pairs(tag: str, iso: str, books: list[str], tags: dict | None, min_n: int = 20, min_keep: float = 0.97) -> dict:
    """Per (target function word, owner Strong's): how often the finished Jev cell (θ = 0) kept the proposal's owner."""
    from lexeme_aligner.config import OUT
    n, kept = collections.Counter(), collections.Counter()
    usj = Path(f"pipeline/work/ingest-cache/usj-{tag}")
    for book in books:
        cell = OUT / f"align_llm_jevA.{tag}.{book}.v4.jev-0_{book}.jsonl"
        final = {}
        for r in map(json.loads, cell.open(encoding="utf-8")):
            final[(r["chapter"], r["verse"])] = {j: q["h_idx"] for q in r["pairs"] for j in q["t_idx"]}
        props, _ = le.build_proposals(tag, iso, usj, [book], signals=le.SIGNALS + ("uncovered-fn",), strong_tags=tags)
        for p in props:
            f = final.get((p.ch, p.v))
            if f is None:
                continue
            owner_of = {j: h for h, sp in p.owner.items() for j in sp}
            strong = {t.idx: t.strong for t in p.heb}
            for j in set(p.stop_t) - set(p.doubt_t):
                if j in owner_of:
                    pair = (p.toks[j].lower(), strong.get(owner_of[j]))
                    n[pair] += 1
                    kept[pair] += f.get(j) == owner_of[j]
    safe = sorted([w, st, n[(w, st)], kept[(w, st)]] for (w, st) in n if n[(w, st)] >= min_n and kept[(w, st)] / n[(w, st)] >= min_keep)
    return {"edition": tag, "learned_from": books, "rule": {"min_n": min_n, "min_keep": min_keep}, "pairs": safe}


def _hint(hints: dict | None, side: str, testament: str, key: str, min_n: int = 20) -> str:
    """One sentence with the measured default for this function word (derive_conventions profile), or ''."""
    from lexeme_aligner.eval.derive_conventions import source_hint, target_hint
    e = ((hints or {}).get(side, {}).get(testament, {}) or {}).get(key)
    if not e or e["n"] < min_n:
        return ""
    text = target_hint(e) if side == "target" else source_hint(e)
    return f" Measured on a hand-made alignment of this language, it is usually: {text}." if text else ""


def le_strip(w: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", w) if not unicodedata.combining(c))


_ID_OPTION = re.compile(r"[th]\d+(-\d+)?")
LEAN_NOTE = ("\n\nAn option named t<n> (or t<n>-<m>, consecutive words) is the word with that id in `translation_words`; an option "
             "named h<n> is the word with that id in `source_words`.")


def _written(p: le.Proposal, units: list | None) -> str:
    """The verse as written: the target words, or for a parts view each written word once (its parts are units of one word)."""
    if not units:
        return " ".join(_shown(p, j) for j in range(len(p.toks)))
    out, seen = [], set()
    for w, u in zip((_shown(p, j) for j in range(len(p.toks))), units):
        if u["in_word"] is None:
            out.append(w)
        elif u["word_index"] not in seen:
            seen.add(u["word_index"]); out.append(u["in_word"])
    return " ".join(out)


def build_request(p: le.Proposal, lex_pos: dict, lang: str, conventions: str, design: str = "v1",
                  skip_pairs: set | None = None, hints: dict | None = None, units: list | None = None, lean: str | None = None) -> dict:
    """`units` (a parts view, `target_parts.unit_words`): `translation_text` is the verse as written and every translation word that
    is a part of a written word carries `in_word` and `part`; positions and every lookup stay on `p.toks`."""
    units = units if units and len(units) == len(p.toks) else None
    src_lang = "Hebrew" if p.ref < 40_000_000 else "Greek"
    testament = "OT" if p.ref < 40_000_000 else "NT"
    owner_of = {j: h for h, s in p.owner.items() for j in s}
    state = {
        "reference": f"{p.book} {p.ch}:{p.v}",
        "source_language": src_lang, "translation_language": lang,
        "conventions": conventions,
        "translation_text": _written(p, units),
        "source_words": [{"id": f"h{t.idx}", "word": t.surface, "lemma": t.lemma, "strong": t.strong,
                          "part_of_speech": lex_pos.get(t.lexeme), "grammar": _gram_tag(t).strip(" []") or None,
                          "gloss": t.gloss_en, "proposed_rendering": _words(p, p.owner.get(t.idx, [])) or None}
                         for t in p.heb],
        "translation_words": [{"id": f"t{j}", "word": w, "proposed_source": f"h{owner_of[j]}" if j in owner_of else None,
                               **({"in_word": units[j]["in_word"], "part": units[j]["part"]} if units and units[j]["in_word"] else {})}
                              for j, w in enumerate(_shown(p, j) for j in range(len(p.toks)))],
    }
    tw = {f"t{j}": _shown(p, j) for j in range(len(p.toks))}
    qs = {}
    for t in p.heb:
        if t.idx not in p.doubt_h:
            continue
        crit = {}
        own = p.owner.get(t.idx, [])
        if own:
            crit["proposal"] = f"its proposed rendering is right as a whole: {_words(p, own)!r}"
        gloss = f", gloss {t.gloss_en!r}" if t.gloss_en else ""
        if design == "v1":
            crit["none"] = "no word of the translation renders it on its own (it is left untranslated, or folded into another word's rendering)"
            crit.update(tw)
            qs[f"h{t.idx}"] = {"type": "choice", "criteria": crit, "instructions":
                f"Source word h{t.idx} ({t.surface}{gloss}) in `source_words`: which word of `translation_words` is its main rendering "
                f"in this verse? Follow `conventions`."}
            continue
        # v2: the whole rendering as one choice among every run of 1..MAX_SPAN consecutive words; "is it rendered at all" asked apart
        max_len = MAX_SPAN
        while 1 + len(_spans(len(p.toks), max_len)) > 254 and max_len > 1:
            max_len -= 1
        for s in _spans(len(p.toks), max_len):
            if s != own:
                crit[_span_id(s)] = _words(p, s)
        qs[f"h{t.idx}"] = {"type": "choice", "criteria": crit, "instructions":
            f"Assume source word h{t.idx} ({t.surface}{gloss}) in `source_words` is rendered in this verse. Which run of consecutive "
            f"words of `translation_words` is its complete rendering, including the function words that belong to it under `conventions` "
            f"and nothing that renders another source word?"}
        qs[f"r{t.idx}"] = {"type": "noul", "instructions":
            f"Does any word of `translation_words` render source word h{t.idx} ({t.surface}{gloss}) in this verse, by its meaning or its "
            f"grammatical function, following `conventions`? Answer no when it is left untranslated or folded into the rendering of "
            f"another source word."}
    t_ask = sorted(p.doubt_t)
    if design == "v4":                                   # every target function word, owned or not
        t_ask = sorted(set(t_ask) | (set(p.stop_t) - skip_owned(p, skip_pairs)))
        for t in p.heb:                                  # a flagged FUNCTION source word: which word renders or carries it?
            q = qs.get(f"h{t.idx}")
            if q is None or t.is_content:
                continue
            gloss = f", gloss {t.gloss_en!r}" if t.gloss_en else ""
            if not os.environ.get("JEV_COMPAT_R"):       # (JEV_COMPAT_R: the body as before 2026-10-26, to rebuild older cells from cache)
                qs.pop(f"r{t.idx}", None)                # decide() never reads it for a function word (2026-10-26: 9% of questions)
            q["criteria"] = {"none": "the translation leaves it out: no separate word and no prefix or suffix carries it", **tw}
            q["instructions"] = (f"Source function word h{t.idx} ({t.surface}{gloss}) in `source_words`: which word of `translation_words` "
                                 f"renders it, either as a separate word or as a prefix or suffix written inside that word"
                                 f"{INSIDE_EXAMPLES.get(lang, '')}? Follow `conventions`."
                                 + _hint(hints, "source", testament,
                                         f"{t.lemma} (suffix)" if testament == "OT" and t.lemma == "הוּא" else t.lemma))
    for j in t_ask:
        crit = {f"h{t.idx}": f"{t.surface}" + (f" ({t.gloss_en})" if t.gloss_en else "") for t in p.heb}
        crit["none"] = "it renders no source word: supplied by the translator for style or grammar, or part of an added explanation"
        qs[f"t{j}"] = {"type": "choice", "criteria": crit, "instructions":
            f"Translation word t{j} ({_shown(p, j)!r}) in `translation_words`: which word of `source_words` does it render, by its "
            f"meaning or by the grammatical function it carries? Follow `conventions`."
            + (_hint(hints, "target", testament, p.toks[j].lower()) if j in p.stop_t else "")}
    if lean:                                             # 2026-10-26: options are billed per question, the state once per request.
        gl = {f"h{t.idx}": (t.gloss_en or le_strip(t.surface)) for t in p.heb}
        for q in qs.values():                            # ids: every word option a bare id; src: a source-word option its gloss only
            if "criteria" in q:                          # (the word itself is in `source_words`), target-word options unchanged
                q["criteria"] = {k: (None if lean == "ids" and _ID_OPTION.fullmatch(k) else
                                     gl.get(k, v) if lean == "src" and k in gl else v) for k, v in q["criteria"].items()}
        state["conventions"] += LEAN_NOTE
    return {"model": MODEL, "state": state, "questions": qs}


def ask_split(body: dict, key: str, max_parts: int = 8) -> dict:
    """`ask`, but a request the API refuses as too large (HTTP 400 max_tokens_exceeded: a long verse with many v4 questions) is
    asked again as 2, 4, ... requests with the same state and a share of the questions each; the answers are merged. A verse that
    fits is asked exactly as before (same body, same cache file)."""
    res = ask(body, key)
    parts = 2
    while ("_error" in res and parts <= max_parts and ("max_tokens_exceeded" in res["_error"]   # cache-only: the verse may have been
           or (os.environ.get("JEV_CACHE_ONLY") and "answer cache" in res["_error"]))):          # asked in parts (only those cached)
        ids = list(body["questions"])
        size = -(-len(ids) // parts)
        subs = [ask({**body, "questions": {q: body["questions"][q] for q in ids[i:i + size]}}, key) for i in range(0, len(ids), size)]
        bad = next((r for r in subs if "_error" in r), None)
        if bad is None:
            merged = {"answers": {}, "usage": collections.Counter(), "model": subs[0].get("model"), "_split": len(subs),
                      "_cached": all(r.get("_cached") for r in subs)}
            for r in subs:
                merged["answers"].update(r.get("answers", {}))
                if not r.get("_cached"):
                    merged["usage"].update(r.get("usage", {}))
            merged["usage"] = dict(merged["usage"])
            return merged
        res = bad
        parts *= 2
    return res


def ask(body: dict, key: str) -> dict:
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    fp = WORK / "responses" / (hashlib.sha256(raw).hexdigest()[:24] + ".json")
    if fp.exists():
        return {**json.loads(fp.read_text(encoding="utf-8")), "_cached": True}
    if os.environ.get("JEV_CACHE_ONLY"):                 # rebuild cells from cached answers only: a request not in the cache is
        return {"_error": "not in the answer cache (JEV_CACHE_ONLY)"}               # an error, never a billed call
    req = urllib.request.Request(URL, data=raw, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(4):
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read().decode())
            res["_seconds"] = round(time.monotonic() - t0, 2)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
            return res
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace").replace(key, "[KEY]")[:400]
            if e.code in (429, 500, 502, 503, 504, 529) and attempt < 3:      # 529 = TypeSafe overloaded
                time.sleep(5 * (attempt + 1))
                continue
            return {"_error": f"HTTP {e.code}: {msg}"}
        except Exception as e:  # noqa: BLE001
            if attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            return {"_error": f"{type(e).__name__}: {e}"}
    return {"_error": "retries exhausted"}


WINDOW = 3
CHUNK_Q = 40


def width_requests(p: le.Proposal, state: dict, answers: dict) -> list[dict]:
    """v3 stage 2: for every flagged source word whose stage-1 answer is a single target word (the HEAD), one Noul per neighbour
    within WINDOW on each side: does it belong to the same rendering? Chunked to CHUNK_Q questions per request."""
    qs = {}
    by_idx = {t.idx: t for t in p.heb}
    for qid, a in answers.items():
        if qid[0] != "h" or not (a.get("choice") or "").startswith("t"):
            continue
        h, j = int(qid[1:]), int(a["choice"][1:])
        t = by_idx[h]
        gloss = f", gloss {t.gloss_en!r}" if t.gloss_en else ""
        for k in range(max(0, j - WINDOW), min(len(p.toks), j + WINDOW + 1)):
            if k == j:
                continue
            qs[f"w{h}_{k}"] = {"type": "noul", "instructions":
                f"Source word h{h} ({t.surface}{gloss}) in `source_words` is rendered by translation word t{j} ({_shown(p, j)!r}). "
                f"Is translation word t{k} ({_shown(p, k)!r}) also part of the rendering of h{h}, under `conventions` (for example an "
                f"article, auxiliary, postposition, preposition or particle that carries h{h}'s own meaning or grammatical function), "
                f"rather than rendering another source word or nothing?"}
    items = sorted(qs.items())
    return [{"model": MODEL, "state": state, "questions": dict(items[i:i + CHUNK_Q])} for i in range(0, len(items), CHUNK_Q)]


def widen(j: int, h: int, width: dict, n: int) -> list[int]:
    """Grow outward from the head while the neighbour's 'belongs' probability is >= 0.5; stop at the first no on each side."""
    span = [j]
    for step in (-1, 1):
        k = j + step
        while 0 <= k < n and abs(k - j) <= WINDOW and (width.get(f"w{h}_{k}") or {}).get("noul", 0.0) >= 0.5:
            span.append(k)
            k += step
    return sorted(span)


def decide(p: le.Proposal, answers: dict, theta: float, width: dict | None = None,
           design: str = "v1", scored: bool = False) -> tuple[list, collections.Counter]:
    """Answers -> llm_edit Edits (applied in ascending confidence, so a surer answer wins a contested target word).
    `scored`: return ([(prob, Edit)] of the source questions, [(prob, Edit)] of the target questions, counts) unsorted instead, for a
    caller that merges the edits of several requests (jev_clauses)."""
    c: collections.Counter = collections.Counter()
    h_ed, t_ed = [], []
    content = {t.idx for t in p.heb if t.is_content}
    if design == "v4":                                   # function source words: a word owned by another source word -> carried inside
        owner_of = {j: h for h, s in p.owner.items() for j in s}
        rest = {}
        for qid, a in answers.items():
            n = int(qid[1:]) if qid[0] == "h" else None
            if n is None or n in content:
                rest[qid] = a
                continue
            choice = a.get("choice")
            prob = (a.get("probabilities") or {}).get(choice, a.get("confidence") or 0.0)
            if prob < theta:
                c["below threshold (proposal kept)"] += 1
            elif choice == "none":
                h_ed.append((prob, le.Edit("none", h=n))); c["fn none"] += 1
            elif choice and choice[0] == "t":
                j = int(choice[1:])
                if j in p.owner.get(n, []):
                    c["fn keep"] += 1
                elif j in owner_of:
                    h_ed.append((prob, le.Edit("inside", h=n, t=[j]))); c["fn inside"] += 1
                else:
                    h_ed.append((prob, le.Edit("assign", h=n, t=[j]))); c["fn own word"] += 1
        answers = rest
    for qid, a in answers.items():
        if qid[0] == "r":                                       # v2 companion: consumed with its h question
            continue
        r = answers.get(f"r{qid[1:]}") if qid[0] == "h" else None
        if r is not None:                                       # v2: not rendered at all -> none; else the chosen run
            if r.get("noul", 1.0) < 0.5:
                if 1 - r["noul"] >= theta:
                    h_ed.append((1 - r["noul"], le.Edit("none", h=int(qid[1:])))); c["h none"] += 1
                else:
                    c["below threshold (proposal kept)"] += 1
                continue
            choice = a.get("choice")
            prob = (a.get("probabilities") or {}).get(choice, a.get("confidence") or 0.0)
            if prob < theta:
                c["below threshold (proposal kept)"] += 1
            elif choice == "proposal":
                c["h keep"] += 1
            else:
                ts = le._positions(choice)
                h_ed.append((prob, le.Edit("assign", h=int(qid[1:]), t=ts))); c["h span"] += 1
            continue
        choice = a.get("choice")
        prob = (a.get("probabilities") or {}).get(choice, a.get("confidence") or 0.0)
        if prob < theta:
            c["below threshold (proposal kept)"] += 1
            continue
        n = int(qid[1:])
        if qid[0] == "h":
            if choice == "proposal":
                c["h keep"] += 1
            elif choice == "none":
                h_ed.append((prob, le.Edit("none", h=n))); c["h none"] += 1
            elif choice and choice[0] == "t" and width is not None:      # v3: head + measured width
                span = widen(int(choice[1:]), n, width, len(p.toks))
                if span == p.owner.get(n, []):
                    c["h keep (same span)"] += 1
                else:
                    h_ed.append((prob, le.Edit("assign", h=n, t=span))); c["h span"] += 1
            elif choice and choice[0] == "t":
                j = int(choice[1:])
                if j in p.owner.get(n, []):
                    c["h keep (picked a word of the proposal)"] += 1
                else:
                    h_ed.append((prob, le.Edit("assign", h=n, t=[j]))); c["h move"] += 1
        else:
            if choice == "none":
                t_ed.append((prob, le.Edit("added", t=[n]))); c["t none"] += 1
            elif choice and choice[0] == "h":
                t_ed.append((prob, le.Edit("add", h=int(choice[1:]), t=[n]))); c["t attach"] += 1
    if scored:
        return h_ed, t_ed, c
    return [e for _, e in sorted(h_ed, key=lambda x: x[0])] + [e for _, e in sorted(t_ed, key=lambda x: x[0])], c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True); ap.add_argument("--publish-iso", required=True); ap.add_argument("--book", required=True)
    ap.add_argument("--chapter", type=int, action="append"); ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--design", default="v1", choices=["v1", "v2", "v3", "v4"])
    ap.add_argument("--tags", type=Path, help="the edition's own Strong's tagging per target word (llm_edit.apply_strong_tags)")
    ap.add_argument("--conventions", default=None, help="config/llm_conventions/<name>.md (default: the publish iso)")
    ap.add_argument("--skip-pairs", type=Path, help="v4: owned function words not asked (learn_skip_pairs output)")
    ap.add_argument("--learn-pairs", help="comma-separated finished books: write the skip pairs to --pairs-out and stop")
    ap.add_argument("--pairs-out", type=Path)
    ap.add_argument("--hints", type=Path, help="derive_conventions --json profile: a measured default in each function-word question")
    ap.add_argument("--variant", help="suffix for the cell names, so experiment arms do not overwrite each other")
    ap.add_argument("--lean", choices=["ids", "src"], help="ids: word options as bare ids (-38%% input tokens on BSB RUT, but F1 -.02); "
                                                           "src: source-word options as their gloss only. A different arm: use --variant")
    ap.add_argument("--usj-dir", type=Path, help="the text the proposals were made on (default: the ingest cache); a parts view for --parts")
    ap.add_argument("--parts", type=Path, help="the split file the --usj-dir parts view was written from: the verse is sent as written "
                                               "and each part carries in_word / part (target_parts.unit_words); positions unchanged")
    a = ap.parse_args()
    if a.learn_pairs:
        tags0 = json.loads(a.tags.read_text(encoding="utf-8")) if a.tags else None
        res = learn_skip_pairs(a.iso, a.publish_iso, a.learn_pairs.split(","), tags0)
        a.pairs_out.write_text(json.dumps(res, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
        print(f"[jev] {len(res['pairs'])} skip pairs -> {a.pairs_out}", file=sys.stderr)
        return 0
    skip_pairs = ({(w, st) for w, st, *_ in json.loads(a.skip_pairs.read_text(encoding="utf-8"))["pairs"]}
                  if a.skip_pairs else None)
    from lexeme_aligner.gapfill import load_priors
    usj = a.usj_dir or Path(f"pipeline/work/ingest-cache/usj-{a.iso}")
    signals = le.SIGNALS + (("uncovered-fn",) if a.design == "v4" else ())
    tags = json.loads(a.tags.read_text(encoding="utf-8")) if a.tags else None
    props, _ = le.build_proposals(a.iso, a.publish_iso, usj, [a.book], signals=signals, strong_tags=tags)
    if a.chapter:
        props = [p for p in props if p.ch in set(a.chapter)]
    props = props[: a.limit] if a.limit else props
    lex_pos, _ = load_priors(PRIOR_PACK)
    lang = LANG.get(a.publish_iso, a.publish_iso)
    conv_fp = (Path(a.conventions) if a.conventions and a.conventions.endswith(".md")          # a file path (experiments)
               else le._CONVENTIONS_DIR / f"{a.conventions or a.publish_iso}.md")
    conventions = _CONV + f"\n\nLanguage conventions ({lang}):\n" + (conv_fp.read_text(encoding="utf-8") if conv_fp.exists() else _GENERIC_CONVENTIONS)
    key = "" if os.environ.get("JEV_CACHE_ONLY") else api_key()
    hints = json.loads(a.hints.read_text(encoding="utf-8")) if a.hints else None
    units = {}
    if a.parts:
        from lexeme_aligner import target_parts as tp
        units = tp.unit_words(usj, tp.load_parts(a.parts))
    jobs = [(p, build_request(p, lex_pos, lang, conventions, "v1" if a.design == "v3" else a.design, skip_pairs, hints,
                              units.get((p.book, p.ch, p.v)), a.lean))
            for p in props]
    jobs = [(p, b) for p, b in jobs if b["questions"] and p.toks]   # no target text (a verse the translation omits): nothing to ask
    t0 = time.monotonic()
    with ThreadPoolExecutor(a.workers) as ex:
        results = list(ex.map(lambda pb: ask_split(pb[1], key), jobs))
    usage = collections.Counter()
    answers: dict[int, dict] = {}
    answered_by: dict[int, str] = {}                     # the version that actually answered (jev-latest resolves to e.g. jev-1.13.0)
    for (p, body), res in zip(jobs, results):
        if "_error" in res:
            usage["verses failed"] += 1
            print(f"[jev] {p.book} {p.ch}:{p.v}: {res['_error']}", file=sys.stderr)
            continue
        answers[p.ref] = res.get("answers", {})
        answered_by[p.ref] = res.get("model", MODEL)
        usage["questions"] += len(body["questions"]); usage["verses"] += 1
        if not res.get("_cached"):
            u = res.get("usage", {})
            usage["input_tokens"] += u.get("input_tokens", 0); usage["output_tokens"] += u.get("output_tokens", 0)
            usage["requests billed"] += 1
    widths: dict[int, dict] = {}
    if a.design == "v3":
        stage2 = [(p.ref, b) for p, body in jobs if p.ref in answers
                  for b in width_requests(p, body["state"], answers[p.ref])]
        with ThreadPoolExecutor(a.workers) as ex:
            res2 = list(ex.map(lambda rb: ask(rb[1], key), stage2))
        for (ref, body), res in zip(stage2, res2):
            if "_error" in res:
                usage["width requests failed"] += 1
                print(f"[jev] width {ref}: {res['_error']}", file=sys.stderr)
                continue
            widths.setdefault(ref, {}).update(res.get("answers", {}))
            usage["width questions"] += len(body["questions"])
            if not res.get("_cached"):
                u = res.get("usage", {})
                usage["input_tokens"] += u.get("input_tokens", 0); usage["output_tokens"] += u.get("output_tokens", 0)
                usage["requests billed"] += 1
    tag = f"jevA.{a.iso}.{a.book}" + ("" if a.design == "v1" else f".{a.design}")
    if a.variant:                                        # a named experiment arm (e.g. a conventions variant): its own cells
        tag += f".{a.variant}"
    summary = {"tag": tag, "model": MODEL, "seconds": round(time.monotonic() - t0, 1), "usage": dict(usage), "cells": {}}
    by_ref = {p.ref: p for p in props}
    for theta in THRESHOLDS:
        finals, tally = {}, collections.Counter()
        for ref, ans in answers.items():
            edits, c = decide(by_ref[ref], ans, theta, widths.get(ref, {}) if a.design == "v3" else None, a.design)
            tally.update(c)
            inside: dict[int, int] = {}
            owner, changed, cnt = le.apply_edits(by_ref[ref], edits, inside)
            owned = {j for s in owner.values() for j in s}
            none_t = {e.t[0] for e in edits if e.kind == "added"} - owned - set(inside.values())   # "renders no source word"
            finals[ref] = (owner, changed, inside, none_t)
            tally["inside links"] += len(inside)
            tally["source tokens changed"] += len(changed)
        cell = f"{tag}.jev-{theta:g}"
        recs = le.to_records(props, finals, {"provider": "typesafe", "model": MODEL, "theta": theta, "design": a.design})
        for book_recs in recs.values():
            for r in book_recs:
                r["llm"] = {**r["llm"], "answered_by": answered_by.get(r["ref"])}    # None = no Jev answer, the proposal stands
        le.write_records(recs, cell, Path(OUT))
        summary["cells"][cell] = dict(tally)
    (WORK / f"{tag}.summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
