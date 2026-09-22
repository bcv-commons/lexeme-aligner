"""Providers, usage/cost accounting and the on-disk response cache for the opt-in LLM-alignment
experiment (`llm_align.py`).

This is the ONLY module that touches the network or an optional dependency, and it imports `anthropic`
lazily — `--provider mock`, `--dry-run`, and the whole test-suite run with nothing installed.

Three routes behind one interface, `Provider.complete(prefix, suffix, schema, *, max_tokens)`:
  * AnthropicProvider — the Messages API: prompt caching (`cache_control` on the stable prefix),
                        schema-constrained output (`output_config.format`), and the Message Batches API
                        (50% off, asynchronous) for `--batch`.
  * ClaudeCliProvider — `claude -p` as a subprocess, billed to the person's Claude subscription instead of an
                        API key. Same prompts and schemas. Design notes below.
  * MockProvider      — answers from an oracle (existing gapfill/residual fills) by reading the packet text
                        back, exactly as a model would; $0, deterministic, exercises the whole write -> score
                        loop.

Cost accounting is per call (`Usage`) and rolls up into the run ledger. Prices are ASSUMPTIONS (list prices
as last read from Anthropic's model table) — override with `--prices file.json`; they exist to turn reported
token counts into a comparable number, not to predict an invoice.

CLI-route design notes (each one verified against `claude --help` and the ASV project's driver, which ran
~1,100 chapters through this route):
  * `--safe-mode`, NOT `--bare`: `--bare` reads Anthropic auth "strictly [from] ANTHROPIC_API_KEY ... (OAuth
    and keychain are never read)", i.e. it would silently defeat the subscription route. `--safe-mode`
    disables CLAUDE.md/skills/hooks/MCP/plugins but keeps auth.
  * ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN are STRIPPED from the subprocess environment. If either is
    present Claude Code bills the API instead of the subscription — silently, at several times the rate —
    and `make`'s LOAD_ENV puts the key in this process's environment for the API route.
  * The prompt travels on stdin, never argv (Linux caps one argv entry at 128 KiB).
  * The envelope is read as `structured_output` (dict) else the fenced/plain JSON in `result`;
    `usage` + `total_cost_usd` give the accounting; `is_error` means failure even on exit 0.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from lexeme_aligner.llm_prompt import (
    PROMPT_VERSION, SCHEMA_FULL, SCHEMA_FULL_PACKED, SCHEMA_LEXEME, SCHEMA_LEXEME_VERIFY, SCHEMA_VERIFY)

# --- pricing / usage ----------------------------------------------------------------------------------


@dataclass
class Price:
    """USD per million tokens. Cache reads/writes and batch are multipliers on the input/output rate."""
    input: float
    output: float
    cache_read_mult: float = 0.1
    cache_write_mult: float = 1.25
    batch_mult: float = 0.5

    def cost(self, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int,
             *, batch: bool = False, cached: bool = True) -> float:
        """`cached=False` prices the same call as if no prompt cache existed (all input at the full rate) —
        the number the ledger's `cost_usd_if_uncached` reports, to show what caching saved."""
        if cached:
            inp = (input_tokens + cache_read * self.cache_read_mult + cache_write * self.cache_write_mult)
        else:
            inp = input_tokens + cache_read + cache_write
        total = (inp * self.input + output_tokens * self.output) / 1e6
        return total * (self.batch_mult if batch else 1.0)


# ASSUMPTIONS — list prices for Anthropic first-party as last read (2026-06-24 model table). Verify against
# the pricing page before spending; override with --prices.
PRICES: dict[str, Price] = {
    "claude-opus-5": Price(5.0, 25.0),
    "claude-sonnet-5": Price(2.0, 10.0),
    "claude-haiku-4-5": Price(1.0, 5.0),
    "claude-fable-5-1": Price(10.0, 50.0, cache_read_mult=0.025),
}


def load_prices(path: Path | None) -> dict[str, Price]:
    """PRICES, overridden/extended by a JSON file `{"model": {"input": 2, "output": 10, ...}}`."""
    prices = dict(PRICES)
    if path:
        for model, spec in json.loads(Path(path).read_text(encoding="utf-8")).items():
            prices[model] = Price(**spec)
    return prices


@dataclass
class Usage:
    input_tokens: int = 0            # uncached input only (Anthropic's `usage.input_tokens`)
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0
    cost_usd_if_uncached: float = 0.0
    wall_s: float = 0.0
    from_local_cache: bool = False
    billing: str = "api"             # api | batch | subscription | none

    def __add__(self, o: "Usage") -> "Usage":
        # "" (an empty accumulator) and "none" (a call that cost nothing) are neutral: adding them must not
        # turn a run of pure subscription/api calls into "mixed"
        neutral = ("", "none")
        billing = (o.billing if self.billing in neutral else self.billing if o.billing in neutral
                   else self.billing if self.billing == o.billing else "mixed")
        return Usage(self.input_tokens + o.input_tokens, self.output_tokens + o.output_tokens,
                     self.cache_read + o.cache_read, self.cache_write + o.cache_write,
                     self.cost_usd + o.cost_usd, self.cost_usd_if_uncached + o.cost_usd_if_uncached,
                     self.wall_s + o.wall_s, self.from_local_cache and o.from_local_cache, billing)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "Usage":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    @classmethod
    def priced(cls, price: Price | None, input_tokens: int, output_tokens: int, cache_read: int,
               cache_write: int, wall_s: float = 0.0, billing: str = "api") -> "Usage":
        batch = billing == "batch"
        return cls(input_tokens, output_tokens, cache_read, cache_write,
                   price.cost(input_tokens, output_tokens, cache_read, cache_write, batch=batch) if price else 0.0,
                   price.cost(input_tokens, output_tokens, cache_read, cache_write, batch=batch, cached=False)
                   if price else 0.0, wall_s, False, billing)


# --- request identity + on-disk cache -----------------------------------------------------------------


def cache_key(provider: str, model: str, effort: str, prefix: str, suffix: str, schema: dict) -> str:
    """sha256 of everything that can change the answer. Includes the route (`provider`): a `claude -p` answer
    and an API answer for the same prompt are separate data points, and a mock answer must never be served
    as a real one."""
    h = hashlib.sha256()
    for part in (provider, model, effort, PROMPT_VERSION, prefix, suffix,
                 json.dumps(schema, sort_keys=True, ensure_ascii=False)):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class ResponseCache:
    """`<dir>/<k[:2]>/<k>.json` — one file per request. A hit costs $0 and is flagged `from_local_cache`,
    so re-running or re-scoring a finished cell never re-spends."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> tuple[dict, Usage] | None:
        fp = self._path(key)
        if not fp.exists():
            return None
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
            return doc["response"], Usage.from_dict(doc.get("usage", {}))
        except (OSError, ValueError, KeyError):
            return None

    def put(self, key: str, response: dict, usage: Usage, meta: dict | None = None) -> None:
        fp = self._path(key)
        fp.parent.mkdir(parents=True, exist_ok=True)
        tmp = fp.with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": key, "response": response, "usage": usage.to_dict(),
                                   "meta": meta or {}, "ts": time.time()}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, fp)


# --- provider interface -------------------------------------------------------------------------------


class ProviderError(Exception):
    """A call failed in a way the caller should count, not crash on. `kind`: `invalid` (unparseable answer —
    worth one retry with a repair line), `refusal`, `truncated`, `api`, `budget`."""

    def __init__(self, message: str, kind: str = "api", usage: "Usage | None" = None):
        super().__init__(message)
        self.kind = kind
        self.usage = usage               # what a failed-but-billed call still cost (an unparseable answer is paid for)


@dataclass
class Job:
    key: str
    prefix: str
    suffix: str
    schema: dict
    max_tokens: int


class Provider:
    name = "base"
    model = ""
    effort = "low"
    cacheable = True                 # False for the mock: its answers must never enter the shared cache
    concurrency = 4

    def complete(self, prefix: str, suffix: str, schema: dict, *, max_tokens: int) -> tuple[dict, Usage]:
        raise NotImplementedError

    def complete_many(self, jobs: list[Job]) -> dict[str, tuple[dict | None, Usage, str | None]]:
        """key -> (response|None, usage, error|None). Default: `complete()` over a thread pool. Order of
        completion is irrelevant — results are keyed by the job's cache key."""
        def one(j: Job):
            try:
                resp, usage = self.complete(j.prefix, j.suffix, j.schema, max_tokens=j.max_tokens)
                return j.key, (resp, usage, None)
            except ProviderError as e:
                return j.key, (None, e.usage or Usage(billing="none"), f"{e.kind}: {e}")
        out: dict = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.concurrency)) as pool:
            for key, res in pool.map(one, jobs):
                out[key] = res
        return out


# --- mock ---------------------------------------------------------------------------------------------

_REF_LINE = re.compile(r"^REF (\d+)", re.M)
_DECIDE_LINE = re.compile(r"^DECIDE: (.+)$", re.M)
_BLOCK = re.compile(r"^\[REF (\d+) ", re.M)
_BLOCK_DECIDE = re.compile(r"^\s+DECIDE h(\d+)\b", re.M)
_LV_OCC = re.compile(r"^\[REF (\d+)[^\]]*\]\s+h(\d+)\s+PROPOSED", re.M)
_H = re.compile(r"h(\d+)")


class MockProvider(Provider):
    """Answers exactly as a model would see the task — from the packet TEXT — but from an oracle:
    `oracle(ref, h_idx) -> list[int] | None` (e.g. gapfill/residual's own fills). Deterministic and free.

    By construction its precision on the judgeable subset equals the oracle's, so `score_gapfill --method
    llm` against it is a self-check of the whole write -> validate -> score loop before any money moves."""
    name = "mock"
    cacheable = False

    def __init__(self, oracle: Callable[[int, int], list[int] | None] | None = None, model: str = "mock"):
        self.oracle = oracle or (lambda ref, h: None)
        self.model = model

    def complete(self, prefix: str, suffix: str, schema: dict, *, max_tokens: int) -> tuple[dict, Usage]:
        if schema == SCHEMA_LEXEME:
            return self._lexeme(suffix), Usage(billing="none")
        if schema == SCHEMA_FULL_PACKED:
            results = [self._full_verse(block) for block in suffix.split("\n---\n")]
            return {"results": results}, Usage(billing="none")
        if schema == SCHEMA_LEXEME_VERIFY:
            lexeme = re.search(r"^LEXEME (\S+)", suffix, re.M).group(1)
            verdicts = [{"ref": int(ref), "h_idx": int(h), "status": "confirmed", "t_idx": [],
                        "note": "mock: proposal kept"} for ref, h in _LV_OCC.findall(suffix)]
            return {"lexeme": lexeme, "verdicts": verdicts}, Usage(billing="none")
        ref = int(_REF_LINE.search(suffix).group(1))
        m = _DECIDE_LINE.search(suffix)
        hs = [int(x) for x in _H.findall(m.group(1))] if m else []
        if schema == SCHEMA_VERIFY:
            return {"ref": ref, "verdicts": [{"h_idx": h, "status": "confirmed", "t_idx": [],
                                              "note": "mock: proposal kept"} for h in hs]}, Usage(billing="none")
        if schema == SCHEMA_FULL:
            return self._full_verse(suffix), Usage(billing="none")
        alignments = []
        for h in hs:
            span = self.oracle(ref, h)
            alignments.append({"h_idx": h, "t_idx": list(span or []),
                               "status": "aligned" if span else "unrepresented",
                               "note": "mock:oracle" if span else "mock:no oracle answer"})
        return {"ref": ref, "alignments": alignments}, Usage(billing="none")

    def _full_verse(self, block: str) -> dict:
        """One `full`-shaped item from one verse's rendered block — shared by a single-verse `SCHEMA_FULL`
        call and each member of a packed `SCHEMA_FULL_PACKED` call (`block` is one `\\n---\\n`-separated
        chunk of the packed suffix, textually identical to a standalone `full` suffix)."""
        ref = int(_REF_LINE.search(block).group(1))
        m = _DECIDE_LINE.search(block)
        hs = [int(x) for x in _H.findall(m.group(1))] if m else []
        alignments = []
        for h in hs:
            span = self.oracle(ref, h)
            alignments.append({"h_idx": [h], "h_head": h, "t_idx": list(span or []),
                               "t_head": (span[-1] if span else None),
                               "status": "aligned" if span else "unrepresented",
                               "note": "mock:oracle" if span else "mock:no oracle answer"})
        return {"ref": ref, "alignments": alignments, "review_notes": []}

    def _lexeme(self, suffix: str) -> dict:
        lexeme = re.search(r"^LEXEME (\S+)", suffix, re.M).group(1)
        verses = []
        for blk in re.split(r"(?m)^(?=\[REF )", suffix)[1:]:
            ref = int(_BLOCK.search(blk).group(1))
            for h in (int(x) for x in _BLOCK_DECIDE.findall(blk)):
                span = self.oracle(ref, h)
                verses.append({"ref": ref, "h_idx": h, "t_idx": list(span or []),
                               "status": "aligned" if span else "unrepresented",
                               "note": "mock:oracle" if span else "mock:no oracle answer"})
        return {"lexeme": lexeme, "verses": verses}


# --- Anthropic Messages API ---------------------------------------------------------------------------

_FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}      # server-side refusal fallbacks are the documented default here
_EFFORT_PREFIXES = ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-5",
                    "claude-sonnet-4-6", "claude-fable")     # Haiku 4.5 takes neither adaptive thinking nor `effort`


def supports_effort(model: str) -> bool:
    return model.startswith(_EFFORT_PREFIXES)


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model: str, effort: str = "low", prices: dict[str, Price] | None = None, *,
                 batch: bool = False, resume_batch: str | None = None, state_dir: Path | None = None,
                 fallbacks: bool | None = None, concurrency: int = 4, client=None, poll_s: float = 30.0):
        if client is None:
            try:
                import anthropic
            except ImportError:
                raise SystemExit("[llm] the Anthropic route needs the SDK: pip install -e '.[llm]'")
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise SystemExit("[llm] ANTHROPIC_API_KEY is not set (add it to .env; `make` loads it). "
                                 "Use --provider cli to bill your Claude subscription instead.")
            client = anthropic.Anthropic(api_key=key, max_retries=4)
        self.client = client
        self.model, self.effort = model, effort
        self.prices = prices or PRICES
        self.batch, self.resume_batch, self.state_dir = batch, resume_batch, state_dir
        # Refusal fallbacks are rejected by the Batches API, so they apply to the sequential route only.
        self.fallbacks = (model in _FALLBACK_MODELS) if fallbacks is None else fallbacks
        self.concurrency, self.poll_s = concurrency, poll_s

    # -- request shape ------------------------------------------------------------------------------
    def params(self, prefix: str, suffix: str, schema: dict, max_tokens: int) -> dict:
        p = {"model": self.model, "max_tokens": max_tokens,
             "system": [{"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}}],
             "messages": [{"role": "user", "content": suffix}],
             "output_config": {"format": {"type": "json_schema", "schema": schema}}}
        if supports_effort(self.model):
            p["thinking"] = {"type": "adaptive"}
            p["output_config"]["effort"] = self.effort
        return p

    def _usage(self, resp, wall_s: float, billing: str) -> Usage:
        u = resp.usage
        return Usage.priced(self.prices.get(self.model), u.input_tokens or 0, u.output_tokens or 0,
                            getattr(u, "cache_read_input_tokens", 0) or 0,
                            getattr(u, "cache_creation_input_tokens", 0) or 0, wall_s, billing)

    @staticmethod
    def _parse(resp) -> dict:
        if resp.stop_reason == "refusal":
            raise ProviderError(f"model refused ({getattr(resp, 'stop_details', None)})", "refusal")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("output hit max_tokens", "truncated")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise ProviderError("no text block in the response", "invalid")
        try:
            return json.loads(text)
        except ValueError as e:
            raise ProviderError(f"answer is not valid JSON ({e})", "invalid") from e

    # -- sequential ---------------------------------------------------------------------------------
    def complete(self, prefix, suffix, schema, *, max_tokens):
        t0 = time.time()
        spent = Usage(billing="api")
        for attempt in (1, 2):
            try:
                params = self.params(prefix, suffix, schema, max_tokens)
                if self.fallbacks and not self.batch:
                    resp = self.client.beta.messages.create(
                        **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
                else:
                    resp = self.client.messages.create(**params)
            except Exception as e:                          # SDK errors: retried by the SDK, then surfaced here
                raise ProviderError(f"{type(e).__name__}: {e}", "api") from e
            spent = spent + self._usage(resp, time.time() - t0, "api")
            try:
                return self._parse(resp), spent
            except ProviderError as e:
                if e.kind == "truncated" and attempt == 1:
                    max_tokens *= 2                         # one retry with more room; `spent` keeps BOTH attempts
                    continue
                e.usage = spent                             # the caller still owes this call's cost to the ledger
                raise
        raise ProviderError("unreachable", "api")

    def count_tokens(self, prefix: str, suffix: str) -> int:
        """Exact input tokens for one request (no generation cost) — for --count-tokens."""
        r = self.client.messages.count_tokens(
            model=self.model, system=[{"type": "text", "text": prefix}],
            messages=[{"role": "user", "content": suffix}])
        return r.input_tokens

    # -- Message Batches ----------------------------------------------------------------------------
    def complete_many(self, jobs):
        if not self.batch:
            return super().complete_many(jobs)
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request
        state = (Path(self.state_dir) / "batches") if self.state_dir else None
        if self.resume_batch:
            batch_id = self.resume_batch
        else:
            reqs = [Request(custom_id=j.key, params=MessageCreateParamsNonStreaming(
                **self.params(j.prefix, j.suffix, j.schema, j.max_tokens))) for j in jobs]
            batch_id = self.client.messages.batches.create(requests=reqs).id
            if state:
                state.mkdir(parents=True, exist_ok=True)
                (state / f"{batch_id}.json").write_text(
                    json.dumps({"batch_id": batch_id, "model": self.model, "n": len(jobs),
                                "keys": [j.key for j in jobs], "ts": time.time()}), encoding="utf-8")
            print(f"[llm] submitted batch {batch_id} ({len(jobs)} request(s)) — resume with "
                  f"--resume-batch {batch_id}", file=sys.stderr)
        t0 = time.time()
        while True:
            b = self.client.messages.batches.retrieve(batch_id)
            if b.processing_status == "ended":
                break
            print(f"[llm] batch {batch_id}: {b.request_counts.processing} processing, "
                  f"{b.request_counts.succeeded} done", file=sys.stderr)
            time.sleep(self.poll_s)
        out: dict = {}
        wall = time.time() - t0
        for r in self.client.messages.batches.results(batch_id):
            kind = r.result.type
            if kind == "succeeded":
                msg = r.result.message
                try:
                    out[r.custom_id] = (self._parse(msg), self._usage(msg, wall, "batch"), None)
                except ProviderError as e:
                    out[r.custom_id] = (None, self._usage(msg, wall, "batch"), f"{e.kind}: {e}")
            else:
                err = getattr(getattr(r.result, "error", None), "type", kind)
                out[r.custom_id] = (None, Usage(billing="none"), f"api: batch result {kind} ({err})")
        return out


# --- Claude Code CLI ----------------------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


class ClaudeCliProvider(Provider):
    """`claude -p` with tools off and every customization disabled. Billed to the subscription. The first
    real run should confirm the envelope fields (`structured_output`, `usage`, `total_cost_usd`); if the
    model returns fenced JSON in `result` instead, `_decode` handles that too."""
    name = "cli"

    def __init__(self, model: str, effort: str = "low", *, max_budget_usd: float = 1.0, claude_bin: str = "claude",
                 timeout: float = 900.0, runner: Callable = subprocess.run, concurrency: int = 2,
                 prices: dict[str, Price] | None = None):
        self.model, self.effort = model, effort
        self.max_budget_usd, self.claude_bin, self.timeout = max_budget_usd, claude_bin, timeout
        self.runner, self.concurrency = runner, concurrency
        self.prices = prices or {}

    @staticmethod
    def sanitized_env(extra: dict[str, str] | None = None) -> dict[str, str]:
        """The environment WITHOUT the means to bill the API (see the module docstring)."""
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        env.update(extra or {})
        return env

    def command(self, system_file: str, schema: dict) -> list[str]:
        return [self.claude_bin, "-p", "--safe-mode", "--no-session-persistence",
                "--tools", "", "--system-prompt-file", system_file,
                "--json-schema", json.dumps(schema), "--output-format", "json",
                "--model", self.model, "--effort", self.effort,
                "--max-budget-usd", f"{self.max_budget_usd:.2f}"]

    @staticmethod
    def _decode(envelope: dict) -> dict:
        so = envelope.get("structured_output")
        if isinstance(so, dict):
            return so
        text = _FENCE.sub("", str(envelope.get("result") or "")).strip()
        try:
            obj = json.loads(text)
        except ValueError as e:
            raise ProviderError(f"no structured_output and `result` is not JSON ({e})", "invalid") from e
        if not isinstance(obj, dict):
            raise ProviderError("answer is not a JSON object", "invalid")
        return obj

    def complete(self, prefix, suffix, schema, *, max_tokens):
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="llm-cli-") as td:      # empty cwd: no project files to discover
            sysfile = Path(td) / "system.md"
            sysfile.write_text(prefix, encoding="utf-8")
            try:
                proc = self.runner(self.command(str(sysfile), schema), input=suffix, capture_output=True, text=True,
                                   cwd=td, timeout=self.timeout,
                                   env=self.sanitized_env({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_tokens)}))
            except FileNotFoundError as e:
                raise ProviderError(f"`{self.claude_bin}` not found on PATH", "api") from e
            except subprocess.TimeoutExpired as e:
                raise ProviderError(f"claude -p timed out after {self.timeout:.0f}s", "api") from e
        try:
            envelope = json.loads((proc.stdout or "").strip())
        except ValueError as e:
            raise ProviderError(f"claude -p produced no JSON envelope (exit {proc.returncode}): "
                                f"{(proc.stderr or proc.stdout or '')[-300:]}", "api") from e
        if proc.returncode != 0 or envelope.get("is_error"):
            raise ProviderError(f"claude -p failed: {str(envelope.get('result') or proc.stderr)[-300:]}", "api")
        u = envelope.get("usage") or {}
        usage = Usage(u.get("input_tokens", 0) or 0, u.get("output_tokens", 0) or 0,
                      u.get("cache_read_input_tokens", 0) or 0, u.get("cache_creation_input_tokens", 0) or 0,
                      float(envelope.get("total_cost_usd") or 0.0), 0.0, time.time() - t0, False, "subscription")
        # `total_cost_usd` already reflects whatever cache discount the CLI's own billing applies — real
        # money, not an artifact of this route hiding caching. `cost_usd_if_uncached` is a SEPARATE
        # diagnostic (what the same tokens would have cost with no cache at all), priced the same way
        # AnthropicProvider prices it; previously hardcoded equal to `cost_usd` here, which made every CLI
        # cell LOOK like caching bought nothing, when the cache_read/cache_write tokens above show it did.
        pr = self.prices.get(self.model)
        usage.cost_usd_if_uncached = (pr.cost(usage.input_tokens, usage.output_tokens, usage.cache_read,
                                              usage.cache_write, cached=False) if pr else usage.cost_usd)
        return self._decode(envelope), usage


def make_provider(name: str, model: str, effort: str, *, prices: dict[str, Price] | None = None,
                  oracle: Callable[[int, int], list[int] | None] | None = None, batch: bool = False,
                  resume_batch: str | None = None, state_dir: Path | None = None, concurrency: int = 4,
                  max_budget_usd: float = 1.0, fallbacks: bool | None = None) -> Provider:
    if name == "mock":
        return MockProvider(oracle)
    if name == "anthropic":
        return AnthropicProvider(model, effort, prices, batch=batch, resume_batch=resume_batch,
                                 state_dir=state_dir, concurrency=concurrency, fallbacks=fallbacks)
    if name == "cli":
        if batch:
            raise SystemExit("[llm] --batch is an Anthropic-API feature; the CLI route has no batch mode")
        return ClaudeCliProvider(model, effort, max_budget_usd=max_budget_usd, concurrency=min(concurrency, 2),
                                 prices=prices)
    raise SystemExit(f"[llm] unknown provider {name!r} (anthropic | cli | mock)")
