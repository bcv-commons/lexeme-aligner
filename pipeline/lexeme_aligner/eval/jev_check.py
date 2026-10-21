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
import collections
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
LANG = {"hin": "Hindi", "eng": "English", "heb": "Modern Hebrew", "ell": "Modern Greek"}
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


def _words(p: le.Proposal, ts: list[int]) -> str:
    return " ".join(p.toks[j] for j in ts)


MAX_SPAN = 4


def _spans(n: int, max_len: int) -> list[list[int]]:
    return [list(range(i, i + k)) for k in range(1, max_len + 1) for i in range(0, n - k + 1)]


def _span_id(ts: list[int]) -> str:
    return f"t{ts[0]}" if len(ts) == 1 else f"t{ts[0]}-{ts[-1]}"


def build_request(p: le.Proposal, lex_pos: dict, lang: str, conventions: str, design: str = "v1") -> dict:
    src_lang = "Hebrew" if p.ref < 40_000_000 else "Greek"
    owner_of = {j: h for h, s in p.owner.items() for j in s}
    state = {
        "reference": f"{p.book} {p.ch}:{p.v}",
        "source_language": src_lang, "translation_language": lang,
        "conventions": conventions,
        "translation_text": " ".join(p.toks),
        "source_words": [{"id": f"h{t.idx}", "word": t.surface, "lemma": t.lemma, "strong": t.strong,
                          "part_of_speech": lex_pos.get(t.lexeme), "grammar": _gram_tag(t).strip(" []") or None,
                          "gloss": t.gloss_en, "proposed_rendering": _words(p, p.owner.get(t.idx, [])) or None}
                         for t in p.heb],
        "translation_words": [{"id": f"t{j}", "word": w, "proposed_source": f"h{owner_of[j]}" if j in owner_of else None}
                              for j, w in enumerate(p.toks)],
    }
    tw = {f"t{j}": w for j, w in enumerate(p.toks)}
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
        t_ask = sorted(set(t_ask) | set(p.stop_t))
        for t in p.heb:                                  # a flagged FUNCTION source word: which word renders or carries it?
            q = qs.get(f"h{t.idx}")
            if q is None or t.is_content:
                continue
            gloss = f", gloss {t.gloss_en!r}" if t.gloss_en else ""
            q["criteria"] = {"none": "the translation leaves it out: no separate word and no prefix or suffix carries it", **tw}
            q["instructions"] = (f"Source function word h{t.idx} ({t.surface}{gloss}) in `source_words`: which word of `translation_words` "
                                 f"renders it, either as a separate word or as a prefix or suffix written inside that word"
                                 f"{INSIDE_EXAMPLES.get(lang, '')}? Follow `conventions`.")
    for j in t_ask:
        crit = {f"h{t.idx}": f"{t.surface}" + (f" ({t.gloss_en})" if t.gloss_en else "") for t in p.heb}
        crit["none"] = "it renders no source word: supplied by the translator for style or grammar, or part of an added explanation"
        qs[f"t{j}"] = {"type": "choice", "criteria": crit, "instructions":
            f"Translation word t{j} ({p.toks[j]!r}) in `translation_words`: which word of `source_words` does it render, by its "
            f"meaning or by the grammatical function it carries? Follow `conventions`."}
    return {"model": MODEL, "state": state, "questions": qs}


def ask_split(body: dict, key: str, max_parts: int = 8) -> dict:
    """`ask`, but a request the API refuses as too large (HTTP 400 max_tokens_exceeded: a long verse with many v4 questions) is
    asked again as 2, 4, ... requests with the same state and a share of the questions each; the answers are merged. A verse that
    fits is asked exactly as before (same body, same cache file)."""
    res = ask(body, key)
    parts = 2
    while "_error" in res and "max_tokens_exceeded" in res["_error"] and parts <= max_parts:
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
                f"Source word h{h} ({t.surface}{gloss}) in `source_words` is rendered by translation word t{j} ({p.toks[j]!r}). "
                f"Is translation word t{k} ({p.toks[k]!r}) also part of the rendering of h{h}, under `conventions` (for example an "
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
           design: str = "v1") -> tuple[list[le.Edit], collections.Counter]:
    """Answers -> llm_edit Edits (applied in ascending confidence, so a surer answer wins a contested target word)."""
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
    return [e for _, e in sorted(h_ed, key=lambda x: x[0])] + [e for _, e in sorted(t_ed, key=lambda x: x[0])], c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True); ap.add_argument("--publish-iso", required=True); ap.add_argument("--book", required=True)
    ap.add_argument("--chapter", type=int, action="append"); ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--design", default="v1", choices=["v1", "v2", "v3", "v4"])
    a = ap.parse_args()
    from lexeme_aligner.gapfill import load_priors
    usj = Path(f"pipeline/work/ingest-cache/usj-{a.iso}")
    signals = le.SIGNALS + (("uncovered-fn",) if a.design == "v4" else ())
    props, _ = le.build_proposals(a.iso, a.publish_iso, usj, [a.book], signals=signals)
    if a.chapter:
        props = [p for p in props if p.ch in set(a.chapter)]
    props = props[: a.limit] if a.limit else props
    lex_pos, _ = load_priors(PRIOR_PACK)
    lang = LANG.get(a.publish_iso, a.publish_iso)
    conv_fp = le._CONVENTIONS_DIR / f"{a.publish_iso}.md"
    conventions = _CONV + f"\n\nLanguage conventions ({lang}):\n" + (conv_fp.read_text(encoding="utf-8") if conv_fp.exists() else _GENERIC_CONVENTIONS)
    key = api_key()
    jobs = [(p, build_request(p, lex_pos, lang, conventions, "v1" if a.design == "v3" else a.design)) for p in props]
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
    summary = {"tag": tag, "model": MODEL, "seconds": round(time.monotonic() - t0, 1), "usage": dict(usage), "cells": {}}
    by_ref = {p.ref: p for p in props}
    for theta in THRESHOLDS:
        finals, tally = {}, collections.Counter()
        for ref, ans in answers.items():
            edits, c = decide(by_ref[ref], ans, theta, widths.get(ref, {}) if a.design == "v3" else None, a.design)
            tally.update(c)
            inside: dict[int, int] = {}
            owner, changed, cnt = le.apply_edits(by_ref[ref], edits, inside)
            finals[ref] = (owner, changed, inside)
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
