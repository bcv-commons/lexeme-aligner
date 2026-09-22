"""LLM alignment experiment driver — an OPT-IN, additive pass that measures what an LLM can add on top of the
statistical chain, and at what cost. Never part of the default pipeline (gapfill_align.py's mission note:
the default chain must keep working for languages with no LLM coverage at all).

The stepping stone: for a language we already publish, eflomal -> gloss -> gapfill (seeded from the
language's WHOLE published lexeme-alignments) resolves nearly every token deterministically — on MAT/MRK/
ROM/1CO only 1.4% (fra) / 3.8% (hin) of content tokens are left unaligned. So the LLM is only asked about
the residue (`gap`, `gap-seeded`, `lexeme-grouped`), to re-check what the aligner was unsure of (`verify`,
eflomal pairs below score 0.9 — a far bigger pool), or, as a reference point, to do the whole verse (`full`).

Reuses, re-implements nothing: `run_pilot.build_corpus` (verse-range pooling — `t_idx` must match the
published positions), `gapfill.load_covered/load_priors` (the taken pool + gap set), `residual_align.
build_residual/light_target_forms` (the "only what's unaligned" candidate positions),
`reverse_align_check.load_lexeme_vocab_scored` (whole-language known renderings), `align_files.tag_files`.

Output is `align_llm_<out_tag>_<BOOK>.jsonl` in gapfill's record shape (`method="llm"`, `prior=llm_<strategy>`),
so `score_gapfill --method llm` and `benchmark --method llm` score it with no new evaluation code; plus a cost
ledger `llm_usage_<out_tag>.json`. Design: internal-docs/llm-align-experiment-plan.md.

    # $0 self-check of the whole loop (the mock answers with gapfill's own fills):
    python3 -m lexeme_aligner.llm_align --iso hinirv --publish-iso hin --usj-dir pipeline/work/ingest-cache/usj-hinirv \\
        --book MAT --strategy gap-seeded --provider mock
    # what would it cost / what does the model see:
    python3 -m lexeme_aligner.llm_align ... --strategy gap-seeded --model claude-sonnet-5 --dry-run
    # a real cell, then score it:
    python3 -m lexeme_aligner.llm_align ... --nt --strategy gap-seeded --provider anthropic --model claude-sonnet-5 --batch
    python3 -m lexeme_aligner.llm_align --report --iso hinirv --publish-iso hin --out-tag hinirv.gap-seeded.sonnet5 --gold-iso hin
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import LEX_ROOT, LLM_CACHE, OUT, PRIOR_PACK, RESOURCES
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.llm_prompt import (
    ALIGNED, PROMPT_VERSION, SCHEMA_FULL_PACKED, SEEDED, STRATEGIES, Decision, FullDecision, Packet,
    derive_score, derive_score_full, normalize, normalize_full, prior_for, raw_from_lexeme, raw_from_packed,
    raw_from_verify, raw_from_verse, render_prefix, render_suffix, schema_for, seed_renderings)
from lexeme_aligner.llm_providers import (
    Job, PRICES, Provider, ProviderError, ResponseCache, Usage, cache_key, load_prices, make_provider,
    supports_effort)
from lexeme_aligner.refs import encode
from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS, VerseRec

_CONVENTIONS_DIR = Path("config/llm_conventions")
_REPAIR_LINE = ("\n\nYour previous answer was not valid JSON for the required schema. "
                "Return only the JSON object.")


# --- inputs -------------------------------------------------------------------------------------------
@dataclass
class Inputs:
    """Everything the packet builder needs, already loaded — so tests can hand it a synthetic corpus."""
    recs: list[VerseRec]
    covered_h: dict[int, set[int]]                                   # ref -> h_idx already aligned
    taken_t: dict[int, set[int]]                                     # ref -> target positions already consumed
    spans: dict[int, dict[int, list[int]]]                           # ref -> h_idx -> t_idx of the covered pairs
    low_conf: dict[int, dict[int, tuple[list[int], float]]]          # ref -> h_idx -> (t_idx, score) eflomal < verify_below
    candidates: dict[int, set[int]]                                  # ref -> positions build_residual keeps
    is_function: Callable[[str], bool]                               # target function-word predicate
    vocab: dict[str, dict[str, tuple[int, float]]] = field(default_factory=dict)
    lex_pos: dict[str, str] = field(default_factory=dict)
    lex_translit: dict[str, str] = field(default_factory=dict)
    others: dict[tuple[int, int], list[int]] = field(default_factory=dict)   # (ref,h) -> span another method picked
    light_lexemes: set[str] = field(default_factory=set)
    label: str = ""
    lang_name: str = ""


def read_pairs(iso: str, out_dir: Path, methods, min_score: float = 0.0):
    """Yield (method, ref, pair) for every content pair with a non-empty target — the generic jsonl reader."""
    for m in methods:
        for fp in tag_files(out_dir, m, iso):
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    for p in rec["pairs"]:
                        if (p.get("content") and p.get("t_idx") and (p.get("target") or "").strip()
                                and (p.get("score") or 0) >= min_score):
                            yield m, rec["ref"], p


def scan_spans(iso: str, out_dir: Path, methods, min_score: float = 0.0, verify_below: float = 0.9):
    """(spans, low_conf) from the covering methods — first method in `methods` wins a contested token."""
    spans: dict[int, dict[int, list[int]]] = collections.defaultdict(dict)
    low: dict[int, dict[int, tuple[list[int], float]]] = collections.defaultdict(dict)
    for m, ref, p in read_pairs(iso, out_dir, methods, min_score):
        spans[ref].setdefault(p["h_idx"], sorted(p["t_idx"]))
        if m == "eflomal" and (p.get("score") or 0) < verify_below:
            low[ref][p["h_idx"]] = (sorted(p["t_idx"]), float(p["score"]))
    return dict(spans), dict(low)


def scan_others(iso: str, out_dir: Path, methods) -> dict[tuple[int, int], list[int]]:
    out: dict[tuple[int, int], list[int]] = {}
    for _m, ref, p in read_pairs(iso, out_dir, methods):
        out.setdefault((ref, p["h_idx"]), sorted(p["t_idx"]))
    return out


def load_inputs(a) -> Inputs:
    """The heavy load — spine, corpus, taken pool, residual candidates, whole-language vocab, priors."""
    from lexeme_aligner.gapfill import load_covered, load_priors
    from lexeme_aligner.residual_align import build_residual, light_target_forms
    from lexeme_aligner.reverse_align_check import load_lexeme_vocab_scored
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.target_stopwords import StopwordFilter, _load_light_lexemes
    from lexeme_aligner.versification import remapper

    heb = HebrewSource()
    recs = build_corpus(a.books, a.usj_dir, heb, remap=remapper(a.iso, str(a.usj_dir)))
    stopwords = StopwordFilter(a.publish_iso, str(a.usj_dir))
    lex_pos, lex_translit = load_priors(PRIOR_PACK)
    methods = tuple(m.strip() for m in a.methods.split(","))
    covered_h, taken_t, _anch, _ss, _tp = load_covered(a.iso, a.out, methods, a.explained_min_score, lex_pos)
    light = _load_light_lexemes()
    light_forms = light_target_forms(a.iso, a.out, methods, light)
    res = build_residual(recs, covered_h, taken_t, stopwords, light_forms)
    candidates = {encode(r.book, r.ch, r.v): set(r.orig) for r in res}
    spans, low = scan_spans(a.iso, a.out, methods, a.explained_min_score, a.verify_below)
    vocab: dict = {}
    if a.strategy in SEEDED:
        try:
            vocab = load_lexeme_vocab_scored(a.publish_iso)
        except SystemExit as e:
            print(f"[llm] whole-language vocab unavailable ({e}) — seeds will be empty", file=sys.stderr)
    agree = ("eflomal", "gloss", "gapfill") if a.strategy == "full" else ("gapfill", "residual")
    return Inputs(recs, covered_h, taken_t, spans, low, candidates, stopwords.is_function, vocab, lex_pos,
                  lex_translit, scan_others(a.iso, a.out, agree), light, f"{a.publish_iso}, edition {a.iso}",
                  a.lang_name)


# --- packets ------------------------------------------------------------------------------------------
def _content(r: VerseRec) -> set[int]:
    return {t.idx for t in r.heb if t.strong and t.is_content}


def _meta(lexemes, inp: Inputs) -> dict[str, dict]:
    return {lx: {"pos": inp.lex_pos.get(lx), "translit": inp.lex_translit.get(lx)} for lx in lexemes if lx}


def _verse_packet(strategy: str, r: VerseRec, inp: Inputs, decide: list[int], allowed, soft, taken,
                  resolved, proposed=None, top_k: int = 6) -> Packet:
    toks_by_idx = {t.idx: t for t in r.heb}
    # `full` decides function words too (the two-sided partition needs them), but a function word's "known
    # renderings" are formulaic and not worth the tokens — restrict SEEDS/meta to CONTENT lexemes there,
    # same as every other strategy already does implicitly (their `decide` is content-only to begin with).
    seed_scope = ([h for h in decide if toks_by_idx[h].strong and toks_by_idx[h].is_content]
                  if strategy == "full" else decide)
    lexemes = sorted({toks_by_idx[h].lexeme for h in seed_scope if toks_by_idx[h].lexeme})
    seeds = ({lx: seed_renderings(lx, inp.vocab, top_k) for lx in lexemes}
             if strategy in SEEDED else {})
    return Packet(strategy, encode(r.book, r.ch, r.v), r.book, r.ch, r.v, inp.label, list(r.toks), r.heb,
                  sorted(decide), sorted(allowed), sorted(soft), sorted(taken), resolved, proposed or {},
                  seeds, _meta(lexemes, inp))


def _even_packs(items: list, cap: int) -> list[list]:
    """`items` split into as few, as evenly-sized groups as `cap` allows (75 items at cap 50 -> 38+37, not
    the lopsided 50+25 a fixed-size cut leaves) — so every packed call is about as much work as every other,
    and the size actually sent is the size that was sized for. `cap` <= 1 is the identity split (one item
    per group), so callers don't need a separate unpacked code path."""
    if cap <= 1 or not items:
        return [[x] for x in items]
    count = -(-len(items) // cap)                 # ceil division
    base, extra = divmod(len(items), count)
    groups, start = [], 0
    for i in range(count):
        size = base + (1 if i < extra else 0)
        groups.append(items[start:start + size])
        start += size
    return groups


def build_packets(strategy: str, inp: Inputs, *, group_size: int = 12, top_k: int = 6, pack_size: int = 1
                  ) -> tuple[list[Packet], dict[int, Packet], dict]:
    """(packets to send, base verse packet per ref, stats). The base packet per ref (decide = every token the
    strategy owns in that verse) is what responses are validated against, including for `lexeme-grouped`
    where one verse can be answered across several calls, and for a packed `full` call (`pack_size > 1`)
    where several verses are answered by ONE call — `base` stays ref-keyed either way, only what gets SENT
    changes."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    stats = collections.Counter()
    base: dict[int, Packet] = {}
    for r in inp.recs:
        ref = encode(r.book, r.ch, r.v)
        content = _content(r)
        if not r.toks or not content:
            continue
        stats["verses_in_scope"] += 1
        stats["content_tokens"] += len(content)
        n = len(r.toks)
        allpos = set(range(n))
        if strategy == "full":
            # Two-sided partition (the ASV-shaped contract): EVERY source token is decided — content and
            # function words both, unlike every other strategy — and NOTHING is pre-taken; the base chain's
            # own union span per h_idx is carried in `resolved` purely as an EVIDENCE hint (see
            # render_full_verse_suffix), never as a fixed/taken state.
            decide_all = sorted(t.idx for t in r.heb)
            func = {j for j, w in enumerate(r.toks) if inp.is_function(w)}
            evidence = dict(inp.spans.get(ref, {}))
            base[ref] = _verse_packet(strategy, r, inp, decide_all, allpos - func, func, set(), evidence,
                                      top_k=top_k)
        elif strategy == "verify":
            low = {h: v for h, v in inp.low_conf.get(ref, {}).items() if h in content}
            if not low:
                continue
            own = {j for ts, _s in low.values() for j in ts}
            taken = set(inp.taken_t.get(ref, ())) - own
            func = {j for j, w in enumerate(r.toks) if inp.is_function(w)} - own
            allowed = (allpos - taken - func) | own
            resolved = {h: ts for h, ts in inp.spans.get(ref, {}).items() if h not in low}
            base[ref] = _verse_packet(strategy, r, inp, sorted(low), allowed, allpos - taken - allowed, taken,
                                      resolved, proposed=low, top_k=top_k)
        else:                                                         # gap, gap-seeded, lexeme-grouped
            gap = content - inp.covered_h.get(ref, set())
            if not gap:
                continue
            stats["gap_tokens"] += len(gap)
            cand = inp.candidates.get(ref, set())
            if not cand:
                stats["gap_tokens_no_candidates"] += len(gap)         # nothing plain is available: not worth a call
                continue
            taken = set(inp.taken_t.get(ref, ()))
            resolved = {h: ts for h, ts in inp.spans.get(ref, {}).items() if h not in gap}
            base[ref] = _verse_packet(strategy, r, inp, sorted(gap), cand, allpos - cand - taken, taken,
                                      resolved, top_k=top_k)
    stats["verses_sent"] = len(base)
    stats["tokens_to_decide"] = sum(len(p.decide) for p in base.values())
    ordered = [base[k] for k in sorted(base)]
    if strategy == "full" and pack_size > 1:
        packs = _even_packs(ordered, pack_size)
        stats["packs"] = len(packs)
        return ([Packet("full", 0, "", 0, 0, inp.label, [], [], [], [], [], [], members=pack)
                 for pack in packs],
                base, dict(stats))
    if strategy != "lexeme-grouped":
        return ordered, base, dict(stats)

    # lexeme-grouped: bucket every unaligned occurrence by lexeme, biggest buckets first, chunk by verse count
    buckets: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    for p in ordered:
        by_idx = {t.idx: t for t in p.heb}
        for h in p.decide:
            if by_idx[h].lexeme:
                buckets[by_idx[h].lexeme].append((p.ref, h))
    groups: list[Packet] = []
    for lx in sorted(buckets, key=lambda k: (-len(buckets[k]), k)):
        occ = buckets[lx]
        per_verse: dict[int, list[int]] = collections.OrderedDict()
        for ref, h in occ:
            per_verse.setdefault(ref, []).append(h)
        refs = list(per_verse)
        for i in range(0, len(refs), group_size):
            members = [dataclasses.replace(base[ref], decide=sorted(per_verse[ref]), seeds={}, meta={})
                       for ref in refs[i:i + group_size]]
            groups.append(Packet("lexeme-grouped", 0, "", 0, 0, inp.label, [], [], [], [], [], [], lexeme=lx,
                                 seeds={lx: seed_renderings(lx, inp.vocab, top_k)}, meta=_meta([lx], inp),
                                 members=members))
    stats["lexemes"] = len(buckets)
    return groups, base, dict(stats)


# --- execution ----------------------------------------------------------------------------------------
@dataclass
class Ledger:
    usage: Usage = field(default_factory=lambda: Usage(billing=""))
    cached_usage: Usage = field(default_factory=lambda: Usage(billing=""))   # what the cache hits ORIGINALLY cost
    calls: int = 0
    local_hits: int = 0
    errors: int = 0
    repaired_calls: int = 0
    decisions: collections.Counter = field(default_factory=collections.Counter)

    def add(self, u: Usage, *, hit: bool = False) -> None:
        if hit:
            self.local_hits += 1
            self.cached_usage = self.cached_usage + u
        else:
            self.calls += 1
            self.usage = self.usage + u


def max_tokens_for(p: Packet) -> int:
    base = 8192 if p.strategy in ("full", "lexeme-grouped") else 4096
    if p.strategy == "full" and p.members:                 # packed: N verses' worth of JSON in one response
        return min(base * len(p.members), 64000)
    return base


def execute(packets: list[Packet], provider: Provider, cache: ResponseCache, prefix: str, schema: dict,
            ledger: Ledger, *, max_usd: float, batch: bool, log=print) -> list[tuple[Packet, dict | None, str | None]]:
    """Send every packet (cache first). Returns (packet, response|None, error|None) in packet order.
    Budget: enforced between slices of `provider.concurrency` calls, and — for --batch, where cost only
    arrives at the end — up front from the caller's estimate. One retry, with a repair line, per unparseable
    answer. Several consecutive API failures abort the run (a bad request would otherwise fail every call)."""
    results: list[tuple[Packet, dict | None, str | None]] = [(p, None, "not run") for p in packets]
    todo: list[tuple[int, Job]] = []
    for i, p in enumerate(packets):
        suffix = render_suffix(p)
        key = cache_key(provider.name, provider.model, provider.effort, prefix, suffix, schema)
        hit = cache.get(key) if provider.cacheable else None
        if hit:
            ledger.add(hit[1], hit=True)
            results[i] = (p, hit[0], None)
        else:
            todo.append((i, Job(key, prefix, suffix, schema, max_tokens_for(p))))
    if not todo:
        return results
    step = len(todo) if batch else max(1, provider.concurrency * 2)
    consecutive_api = 0
    for s in range(0, len(todo), step):
        if ledger.usage.cost_usd >= max_usd:
            for i, _j in todo[s:]:
                results[i] = (packets[i], None, f"budget: --max-usd {max_usd} reached")
            log(f"[llm] budget reached (${ledger.usage.cost_usd:.2f} >= ${max_usd:.2f}) — stopping; "
                f"{len(todo) - s} call(s) not made", file=sys.stderr)
            break
        chunk = todo[s:s + step]
        done = provider.complete_many([j for _i, j in chunk])
        retry: list[tuple[int, Job]] = []
        for i, j in chunk:
            resp, usage, err = done[j.key]
            ledger.add(usage)
            if err and err.startswith("invalid") and not batch:
                retry.append((i, Job(j.key, j.prefix, j.suffix + _REPAIR_LINE, j.schema, j.max_tokens)))
                continue
            _finish(results, cache, provider, i, packets[i], j, resp, usage, err, ledger)
            consecutive_api = consecutive_api + 1 if (err and err.startswith("api")) else 0
        if retry:
            ledger.repaired_calls += len(retry)
            again = provider.complete_many([j for _i, j in retry])
            for i, j in retry:
                resp, usage, err = again[j.key]
                ledger.add(usage)
                _finish(results, cache, provider, i, packets[i], j, resp, usage, err, ledger)
        if consecutive_api >= 5:
            raise SystemExit(f"[llm] {consecutive_api} consecutive API failures — aborting (first error: "
                             f"{next(e for _p, _r, e in results if e and e.startswith('api'))})")
        log(f"[llm] {min(s + step, len(todo))}/{len(todo)} call(s) · ${ledger.usage.cost_usd:.3f}", file=sys.stderr)
    return results


def _finish(results, cache, provider, i, packet, job, resp, usage, err, ledger) -> None:
    if err or resp is None:
        ledger.errors += 1
        results[i] = (packet, None, err or "empty response")
        return
    if provider.cacheable:
        cache.put(job.key, resp, usage, {"model": provider.model, "provider": provider.name})
    results[i] = (packet, resp, None)


# --- turning answers into output ----------------------------------------------------------------------
def resolve(results, base: dict[int, Packet], strategy: str, *, allow_scattered: bool = False
            ) -> tuple[dict[int, list], collections.Counter, dict[int, dict]]:
    """Per verse: gather every raw answer (in call order), validate against the base packet, return the
    Decisions (or, for `full`, FullDecisions) plus a decision tally and — `full` only — per-verse two-sided
    partition stats (`target_unclaimed`, `added`; empty dict for every other strategy)."""
    raw: dict[int, list[dict]] = collections.defaultdict(list)
    failed: set[int] = set()
    for p, resp, err in results:
        if resp is None:
            failed |= {m.ref for m in p.members} if p.members else {p.ref}
            continue
        if strategy == "lexeme-grouped":
            for ref, items in raw_from_lexeme(resp).items():
                raw[ref].extend(items)
        elif strategy == "full" and p.members:                  # a packed call: several verses, one response
            for ref, items in raw_from_packed(resp).items():
                raw[ref].extend(items)
        elif strategy == "verify":
            raw[p.ref].extend(raw_from_verify(resp, p))
        else:
            raw[p.ref].extend(raw_from_verse(resp))
    out: dict[int, list] = {}
    full_stats: dict[int, dict] = {}
    tally: collections.Counter = collections.Counter()
    for ref, bp in base.items():
        if strategy == "full":
            decisions, packet_repairs, stats = normalize_full(raw.get(ref, []), bp,
                                                               allow_scattered=allow_scattered)
            full_stats[ref] = stats
            tally["target_unclaimed"] += stats["target_unclaimed"]
            tally["added"] += stats["added"]
        else:
            decisions, packet_repairs = normalize(raw.get(ref, []), bp, allow_scattered=allow_scattered)
        if ref in failed:
            for d in decisions:
                if d.status == "invalid":
                    d.repairs = ["call failed"]
        out[ref] = decisions
        tally["dropped_ids"] += len(packet_repairs)
        for d in decisions:
            tally[d.status] += 1
            tally["repaired"] += bool(d.repairs) and d.status != "invalid"
            tally["conflict_dropped"] += any("already claimed" in x for x in d.repairs)
    return out, tally, full_stats


def _full_pairs_skipped_added(ds: list[FullDecision], bp: Packet, by_idx: dict[int, "HebToken"],
                              ref: int, inp: Inputs) -> tuple[list[dict], list[dict], list[dict]]:
    """`full`'s two-sided decisions -> (pairs, skipped, added). A `noncompositional` group's single
    FullDecision becomes one pair PER source id it covers, all sharing the group's span (the same rule
    `normalize`'s noncompositional handling already used) — `group` on the pair names its siblings."""
    pairs, skipped, added = [], [], []
    for d in ds:
        if d.is_pair:
            for h in d.h_idx:
                t = by_idx[h]
                agrees = inp.others.get((ref, h)) == d.t_idx
                pair = {"h_idx": t.idx, "lexeme": t.lexeme, "strong": t.strong, "lemma": t.lemma,
                        "stem": t.stem, "surface": t.surface, "gloss_en": t.gloss_en, "sense": t.sense,
                        "target": " ".join(bp.toks[j] for j in d.t_idx if j < len(bp.toks)),
                        "t_idx": list(d.t_idx), "score": derive_score_full(agrees), "method": "llm",
                        "content": bool(t.strong and t.is_content), "prior": "llm_full", "note": d.note,
                        "status": d.status}
                if t.lexeme in inp.light_lexemes:
                    pair["light"] = True
                if len(d.h_idx) > 1:
                    pair["group"] = [x for x in d.h_idx if x != h]
                if d.repairs:
                    pair["repairs"] = d.repairs
                pairs.append(pair)
        elif d.h_idx:                                          # unrepresented / invalid: source-only
            for h in d.h_idx:
                t = by_idx[h]
                skipped.append({k: v for k, v in (("h_idx", h), ("strong", t.strong), ("lexeme", t.lexeme),
                                                  ("status", d.status), ("note", d.note),
                                                  ("repairs", d.repairs)) if v not in ("", [], None)})
        else:                                                   # added: target-only, no source id at all
            added.append({"t_idx": list(d.t_idx),
                          "target": " ".join(bp.toks[j] for j in d.t_idx if j < len(bp.toks)),
                          "note": d.note})
    return pairs, skipped, added


def to_records(decisions: dict[int, list], base: dict[int, Packet], strategy: str, inp: Inputs,
               run_meta: dict, full_stats: dict[int, dict] | None = None) -> dict[str, list[dict]]:
    """{BOOK: [verse record, ...]} in gapfill's shape, plus `llm` provenance, `llm_skipped` and — `full` only
    — `llm_added` (target-only entries) and `llm_unclaimed_t` (positions no entry claimed at all)."""
    full_stats = full_stats or {}
    by_book: dict[str, list[dict]] = collections.defaultdict(list)
    for ref, ds in decisions.items():
        bp = base[ref]
        by_idx = {t.idx: t for t in bp.heb}
        added: list[dict] = []
        if strategy == "full":
            pairs, skipped, added = _full_pairs_skipped_added(ds, bp, by_idx, ref, inp)
        else:
            pairs, skipped = [], []
            for d in ds:
                if d.is_pair:
                    t = by_idx[d.h_idx]
                    pair = {"h_idx": t.idx, "lexeme": t.lexeme, "strong": t.strong, "lemma": t.lemma,
                            "stem": t.stem, "surface": t.surface, "gloss_en": t.gloss_en, "sense": t.sense,
                            "target": " ".join(bp.toks[j] for j in d.t_idx), "t_idx": list(d.t_idx),
                            "score": derive_score(d, inp.others.get((ref, d.h_idx)) == d.t_idx),
                            "method": "llm", "content": True, "prior": prior_for(strategy, d),
                            "note": d.note, "status": d.status}
                    if t.lexeme in inp.light_lexemes:
                        pair["light"] = True
                    if d.repairs:
                        pair["repairs"] = d.repairs
                    pairs.append(pair)
                else:
                    t = by_idx[d.h_idx]
                    skipped.append({k: v for k, v in (("h_idx", d.h_idx), ("strong", t.strong),
                                                      ("lexeme", t.lexeme), ("status", d.status),
                                                      ("note", d.note), ("repairs", d.repairs))
                                    if v not in ("", [], None)})
        if pairs or skipped or added:
            rec = {"ref": ref, "book": bp.book, "chapter": bp.ch, "verse": bp.v,
                   "pairs": pairs, "llm_skipped": skipped, "llm": run_meta}
            if added:
                rec["llm_added"] = added
            unclaimed = full_stats.get(ref, {}).get("unclaimed_t_idx")
            if unclaimed:
                rec["llm_unclaimed_t"] = unclaimed
            by_book[bp.book].append(rec)
    for recs in by_book.values():
        recs.sort(key=lambda x: (x["chapter"], x["verse"]))
    return by_book


def write_outputs(by_book: dict[str, list[dict]], out_dir: Path, out_tag: str) -> list[Path]:
    for fp in tag_files(out_dir, "llm", out_tag):
        fp.unlink()
    written = []
    for book, recs in by_book.items():
        fp = out_dir / f"align_llm_{out_tag}_{book}.jsonl"
        with fp.open("w", encoding="utf-8") as fh:
            for x in recs:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
        written.append(fp)
    return written


# --- estimate / dry run -------------------------------------------------------------------------------
def _est_tokens(text: str) -> int:
    """Deliberately PESSIMISTIC char-based estimate (an under-estimate would let --max-usd be blown). Calibrated
    on one real `claude -p` call (Hindi MAT 1:3): 3,893 input tokens for ~6.7k chars, i.e. ~1.7 chars/token
    including that route's own scaffolding — so chars/2.5 still slightly under-counts the CLI route but is
    close for the API route. `--count-tokens` gives the exact figure (needs ANTHROPIC_API_KEY)."""
    return int(len(text) / 2.5) + 1


def estimate(packets: list[Packet], prefix: str, model: str, prices, *, batch: bool) -> dict:
    pr = prices.get(model)
    prefix_t = _est_tokens(prefix)
    suffix_t = sum(_est_tokens(render_suffix(p)) for p in packets)
    think = 100 if supports_effort(model) else 0
    out_t = sum(60 + 25 * p.n_decide + think for p in packets)
    calls = len(packets)
    cost = None
    if pr and calls:
        cost = (pr.cost(suffix_t, out_t, prefix_t * max(0, calls - 1), prefix_t, batch=batch))
    return {"calls": calls, "prefix_tokens": prefix_t, "suffix_tokens": suffix_t, "output_tokens": out_t,
            "est_cost_usd": cost}


def model_short(model: str) -> str:
    return re.sub(r"^claude-", "", model).replace("-", "")


def lang_name_for(publish_iso: str) -> str:
    try:
        m = json.loads((LEX_ROOT / "manifest.json").read_text(encoding="utf-8"))
        return m["languages"][publish_iso].get("language") or publish_iso
    except (OSError, KeyError, ValueError):
        return publish_iso


def gold_guard(publish_iso: str, usj_dir: Path) -> None:
    """Refuse to score against the wrong edition. Gold is built from ONE specific translation; most gold
    languages have several ingested, and picking the wrong one reads as a quality problem instead of a setup
    error (measured 2026-08-31: spa gap-fill precision 10.9% on spa_bes vs 54.8% on the gold's own spa_r09)."""
    from lexeme_aligner.contest_rule import gold_edition, gold_usj_dir
    want = gold_usj_dir(publish_iso)
    if want is None:
        if gold_edition(publish_iso):
            print(f"[llm] note: gold edition '{gold_edition(publish_iso)}' for {publish_iso} is not ingested",
                  file=sys.stderr)
        return
    if Path(usj_dir).resolve() != Path(want).resolve():
        raise SystemExit(f"[llm] --usj-dir {usj_dir} is not the gold edition for '{publish_iso}' ({want}). "
                         f"Scoring one Bible against another Bible's gold reads as a quality problem, not a setup "
                         f"error (spa: 10.9% vs 54.8%). Pass the gold edition, or --no-gold-check.")


# --- CLI ----------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="base-chain edition TAG whose align_<method>_<iso>_*.jsonl are read")
    ap.add_argument("--publish-iso", default=None, help="the language's published iso (vocab, stopwords, prompt); "
                                                        "default: --iso")
    ap.add_argument("--usj-dir", type=Path, default=None)
    ap.add_argument("--ot", action="store_true"); ap.add_argument("--nt", action="store_true")
    ap.add_argument("--all", action="store_true"); ap.add_argument("--book", action="append")
    ap.add_argument("--strategy", choices=STRATEGIES, default="gap-seeded")
    ap.add_argument("--provider", choices=["anthropic", "cli", "mock"], default="anthropic")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--effort", choices=["low", "medium", "high"], default="low")
    ap.add_argument("--batch", action="store_true", help="Message Batches API (50%% off, asynchronous; anthropic only)")
    ap.add_argument("--resume-batch", default=None, help="reattach to an already-submitted batch id")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out-tag", default=None, help="output tag (default: <iso>.<strategy>.<model>[.cli])")
    ap.add_argument("--methods", default="eflomal,gloss", help="the methods that define 'covered'")
    ap.add_argument("--explained-min-score", type=float, default=0.0)
    ap.add_argument("--group-size", type=int, default=12, help="lexeme-grouped: verses per call")
    ap.add_argument("--pack-size", type=int, default=1,
                    help="full: verses per call, wrapped in one {\"results\": [...]} response (1 = unpacked, "
                         "current default behaviour)")
    ap.add_argument("--top-k", type=int, default=6, help="seed renderings per lexeme")
    ap.add_argument("--verify-below", type=float, default=0.9, help="verify: eflomal pairs scoring below this")
    ap.add_argument("--limit", type=int, default=None, help="send at most N calls")
    ap.add_argument("--ref", type=int, default=None, help="only this verse ref (BBCCCVVV)")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt + an estimate; call nothing")
    ap.add_argument("--count-tokens", action="store_true", help="exact input tokens for the first 20 calls (API key)")
    ap.add_argument("--max-usd", type=float, default=5.0, help="hard spend stop for this run")
    ap.add_argument("--prices", type=Path, default=None, help="JSON overriding the assumed list prices")
    ap.add_argument("--allow-scattered", action="store_true")
    ap.add_argument("--no-gold-check", action="store_true")
    ap.add_argument("--no-fallbacks", action="store_true", help="skip server-side refusal fallbacks (opus/fable)")
    ap.add_argument("--lang-name", default=None)
    ap.add_argument("--conventions", type=Path, default=None, help="per-language conventions .md for the prompt")
    ap.add_argument("--report", action="store_true", help="score an existing --out-tag and append to llm_report.md")
    ap.add_argument("--gold", choices=["clear", "gbt", "lexicon"], default="clear")
    ap.add_argument("--gold-iso", default=None)
    ap.add_argument("--out", type=Path, default=OUT)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    a.publish_iso = a.publish_iso or a.iso
    a.lang_name = a.lang_name or lang_name_for(a.publish_iso)
    a.out_tag = a.out_tag or (f"{a.iso}.{a.strategy}.{model_short(a.model) if a.provider != 'mock' else 'mock'}"
                              + (".cli" if a.provider == "cli" else ""))
    if a.report:
        from lexeme_aligner.llm_report import report
        return report(a)
    if not a.usj_dir:
        raise SystemExit("[llm] --usj-dir is required")
    a.books = (OT_BOOKS + NT_BOOKS if a.all else OT_BOOKS if a.ot else NT_BOOKS if a.nt
               else [b.upper() for b in (a.book or [])])
    if not a.books:
        raise SystemExit("[llm] give --book X (repeatable), --nt, --ot or --all")
    if not a.no_gold_check:
        gold_guard(a.publish_iso, a.usj_dir)

    prices = load_prices(a.prices)
    inp = load_inputs(a)
    packets, base, stats = build_packets(a.strategy, inp, group_size=a.group_size, top_k=a.top_k,
                                         pack_size=a.pack_size)
    if a.ref is not None:
        if a.strategy == "lexeme-grouped" or (a.strategy == "full" and a.pack_size > 1):
            for g in packets:
                g.members = [m for m in g.members if m.ref == a.ref]
            packets = [g for g in packets if g.members]
        else:
            packets = [p for p in packets if p.ref == a.ref]
        base = {r: p for r, p in base.items() if r == a.ref}
    if a.limit is not None:
        packets = packets[:a.limit]
        keep = {m.ref for p in packets for m in p.members} | {p.ref for p in packets if not p.members}
        base = {r: p for r, p in base.items() if r in keep}
    if a.strategy == "lexeme-grouped":
        # a verse is answered across several calls, one per lexeme; after --ref/--limit only some of them
        # are sent, so the tokens whose groups were dropped must not be counted as missing answers
        sent: dict[int, set[int]] = collections.defaultdict(set)
        for g in packets:
            for m in g.members:
                sent[m.ref] |= set(m.decide)
        base = {r: dataclasses.replace(p, decide=sorted(sent[r])) for r, p in base.items() if r in sent}
    conventions = None
    conv = a.conventions or (_CONVENTIONS_DIR / f"{a.publish_iso}.md")
    if Path(conv).is_file():
        conventions = Path(conv).read_text(encoding="utf-8")
    prefix = render_prefix(a.publish_iso, a.lang_name, a.strategy, conventions)
    schema = SCHEMA_FULL_PACKED if a.strategy == "full" and a.pack_size > 1 else schema_for(a.strategy)
    prefix_sha = hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:8]
    est = estimate(packets, prefix, a.model, prices, batch=a.batch)
    print(f"[llm] {a.strategy} · {a.iso}→{a.out_tag}: {stats.get('verses_in_scope', 0)} verses in scope, "
          f"{stats.get('tokens_to_decide', 0)} token(s) to decide in {len(base)} verse(s), {len(packets)} call(s)"
          + (f" · {stats.get('gap_tokens_no_candidates', 0)} gap token(s) skipped (no available position)"
             if stats.get("gap_tokens_no_candidates") else "")
          + f"\n[llm] prefix {est['prefix_tokens']} tok (est.), prompt sha {prefix_sha}; "
            f"est. total ≈ {est['suffix_tokens']} in + {est['output_tokens']} out"
          + (f" → ${est['est_cost_usd']:.2f} at assumed {a.model} prices" if est["est_cost_usd"] is not None
             else " (no price for this model)"), file=sys.stderr)

    if a.dry_run or not packets:
        if packets:
            print("\n===== SYSTEM PREFIX =====\n" + prefix + "\n===== FIRST USER MESSAGE =====\n"
                  + render_suffix(packets[0]))
        return 0
    if a.count_tokens:
        from lexeme_aligner.llm_providers import AnthropicProvider
        prov = AnthropicProvider(a.model, a.effort, prices)
        sample = packets[:20]
        exact = [prov.count_tokens(prefix, render_suffix(p)) for p in sample]
        print(f"[llm] exact input tokens, first {len(sample)} call(s): mean {sum(exact)/len(exact):.0f} "
              f"(prefix ≈ {prov.count_tokens(prefix, '.')}) → ×{len(packets)} ≈ {int(sum(exact)/len(exact) * len(packets))}",
              file=sys.stderr)
        return 0
    if a.batch and est["est_cost_usd"] is not None and est["est_cost_usd"] > a.max_usd:
        raise SystemExit(f"[llm] estimated ${est['est_cost_usd']:.2f} exceeds --max-usd {a.max_usd} "
                         f"(a batch reports its cost only at the end) — raise --max-usd or narrow the scope")

    provider = make_provider(a.provider, a.model, a.effort, prices=prices,
                             oracle=lambda ref, h: inp.others.get((ref, h)), batch=a.batch,
                             resume_batch=a.resume_batch, state_dir=LLM_CACHE, concurrency=a.concurrency,
                             max_budget_usd=a.max_usd, fallbacks=False if a.no_fallbacks else None)
    if a.provider == "anthropic" and getattr(provider, "fallbacks", False):
        print(f"[llm] server-side refusal fallbacks ON for {a.model} (--no-fallbacks to disable)", file=sys.stderr)
    cache = ResponseCache(LLM_CACHE)
    ledger = Ledger()
    run_id = uuid.uuid4().hex[:12]
    t0 = time.time()
    results: list = []
    try:
        results = execute(packets, provider, cache, prefix, schema, ledger, max_usd=a.max_usd, batch=a.batch)
    except KeyboardInterrupt:
        print("\n[llm] interrupted — writing what finished", file=sys.stderr)
    wall = time.time() - t0

    decisions, tally, full_stats = resolve(results, base, a.strategy, allow_scattered=a.allow_scattered)
    run_meta = {"model": a.model if a.provider != "mock" else "mock", "provider": a.provider, "strategy": a.strategy,
                "prompt_sha8": prefix_sha, "run_id": run_id}
    by_book = to_records(decisions, base, a.strategy, inp, run_meta, full_stats)
    written = write_outputs(by_book, a.out, a.out_tag)
    n_pairs = sum(len(r["pairs"]) for rs in by_book.values() for r in rs)
    ledger.decisions = tally
    doc = {"run_id": run_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "iso": a.iso, "out_tag": a.out_tag,
           "publish_iso": a.publish_iso, "books": a.books, "strategy": a.strategy, "provider": a.provider,
           "model": a.model, "effort": a.effort, "batch": a.batch, "pack_size": a.pack_size,
           "prompt_version": PROMPT_VERSION,
           "prompt_sha8": prefix_sha, "verses_in_scope": stats.get("verses_in_scope", 0),
           "content_tokens": stats.get("content_tokens", 0), "verses_sent": len(base),
           "tokens_to_decide": stats.get("tokens_to_decide", 0),
           "tokens_skipped_no_candidates": stats.get("gap_tokens_no_candidates", 0), "calls": ledger.calls,
           "packets": len(packets), "local_cache_hits": ledger.local_hits, "errors": ledger.errors,
           "repaired_calls": ledger.repaired_calls, "usage": ledger.usage.to_dict(),
           "cost_usd": round(ledger.usage.cost_usd, 6), "cost_usd_if_uncached": round(ledger.usage.cost_usd_if_uncached, 6),
           "usage_incl_cache": (ledger.usage + ledger.cached_usage).to_dict(),
           "cell_cost_usd": round((ledger.usage + ledger.cached_usage).cost_usd, 6),
           "wall_s": round(wall, 1), "pairs_written": n_pairs, "decisions": dict(tally),
           "prices": dataclasses.asdict(prices[a.model]) if a.model in prices else None}
    (a.out / f"llm_usage_{a.out_tag}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    with (a.out / "llm_runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
    print(f"[llm] wrote {n_pairs} pair(s) → align_llm_{a.out_tag}_*.jsonl ({len(written)} book file(s)) · "
          f"{ledger.calls} call(s), {ledger.local_hits} cache hit(s), {ledger.errors} error(s) · "
          f"${ledger.usage.cost_usd:.3f} (${ledger.usage.cost_usd_if_uncached:.3f} without prompt caching) · "
          f"decisions {dict(tally)}", file=sys.stderr)
    return 0 if not ledger.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
