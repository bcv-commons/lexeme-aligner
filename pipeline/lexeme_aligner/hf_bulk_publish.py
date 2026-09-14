"""Shared chunked-commit HF publisher — factored out of compact_align_batch.py so every dataset here
that needs to publish MANY files at once (not export_lex.py's one-language-at-a-time case, which is
small enough per call not to need this) uses the exact same, tested mechanism.

HF's commit-rate limit is 128/hour PER REPO, not per file — `upload_file()`-per-file (or one commit per
language, looped across e.g. 199 languages) blows past that trivially. The fix: bundle many files into
FEW commits via `CommitOperationAdd` + `create_commit()`, exactly as the Hub's own 429 error message
recommends ("upload entire folders at once"). Different repos have INDEPENDENT rate-limit budgets, so
publishing several datasets to their own repos in the same session never competes for the same 128.

Chunking alone isn't enough once a single run needs MORE than ~128 commits (a full-catalog resweep's
compact-alignments push: 690 commits at the default chunk size) — 2026-09-14: the original version of
this function just fired commits as fast as the network allowed until the Hub returned a real 429, then
gave up with a "wait ~1 hour, re-run" message. That's a design that REQUIRES a human (or a supervising
script) to notice the failure and manually retry, repeatedly, over several hours — for something that
should just finish unattended overnight. `_RateLimiter` below fixes this properly: a sliding-window
self-pace that sleeps BEFORE a commit would exceed a safe threshold, so the run never actually hits the
429 wall at all — it just keeps going, slower when it needs to, until done. The 429 handler is kept as a
last-resort safety net (e.g. a concurrent process burning the same repo's quota), not the primary
mechanism anymore.

    from lexeme_aligner.hf_bulk_publish import publish_chunked
    publish_chunked(root, repo_id, files, create=True, dry_run=False, label="my-dataset")
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from lexeme_aligner.config import HF_CHUNK_SIZE

# Headroom under HF's real 128/hour/repo cap — leaves room for clock skew between our wall-clock
# window and the Hub's own accounting, plus any other process/session touching the same repo.
_MAX_COMMITS_PER_HOUR = 110
# Target: a run should fit in ONE hour's worth of commits with room to spare, so pacing (above) is a
# rare safety net rather than the normal path. Below this many commits at the base chunk_size, leave
# chunk_size untouched — no reason to bundle a small everyday publish (e.g. one language's worth of
# files) into artificially huge commits.
_TARGET_MAX_COMMITS = 90
# Sanity ceiling regardless of how many files there are — these files are small (~10-150KB each,
# measured on compact-alignments), so even this is only tens of MB per commit, but an unbounded chunk
# size for a hypothetically enormous future dataset would make a single failed op re-upload a lot of
# needless work (create_commit is all-or-nothing) and could make a single commit slow to assemble.
_MAX_CHUNK_SIZE = 5000


def _adaptive_chunk_size(n_items: int, base: int) -> int:
    """`base` (HF_CHUNK_SIZE, the small/default size) unless that many items would need more than
    `_TARGET_MAX_COMMITS` commits — then scale up so the whole run fits comfortably inside one hour's
    commit budget instead of needing `_RateLimiter` to pace it across several. Bigger commits, less
    reliance on pacing to actually avoid HF's rate limit — the two mechanisms complement each other:
    this shrinks how OFTEN a big job needs to pace at all; `_RateLimiter` is the backstop for whatever
    still doesn't fit (or a base rate that's already large for other reasons)."""
    if n_items <= 0 or -(-n_items // base) <= _TARGET_MAX_COMMITS:
        return base
    return min(-(-n_items // _TARGET_MAX_COMMITS), _MAX_CHUNK_SIZE)


def _rate_state_path(root: Path) -> Path:
    return root / ".publish_rate.json"


class _RateLimiter:
    """Self-paces `create_commit()` calls against ONE repo to stay under `_MAX_COMMITS_PER_HOUR`,
    sliding-window (not a fixed per-minute quota — a burst early in the hour is fine as long as the
    trailing-60-minute count stays under the cap).

    Persists its commit timestamps to `.publish_rate.json` (wall-clock `time.time()`, not
    `time.monotonic()` — needs to survive a process restart) next to `.publish_state.json`, keyed by
    repo_id: an in-memory-only version would forget everything on restart, so a run that gets killed
    right after firing a burst of commits and is immediately restarted would blindly race straight
    into another 429 — the exact failure mode this class exists to avoid in the first place. Loading
    real recent history on construction means ANY restart (crash, a fresh CLI invocation minutes
    later, whatever) paces correctly from its very first commit, not just within one long-running
    process."""

    def __init__(self, root: Path, repo_id: str, max_per_hour: int = _MAX_COMMITS_PER_HOUR):
        self.max_per_hour = max_per_hour
        self._path = _rate_state_path(root)
        self._repo_id = repo_id
        state = json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}
        self._all = {k: v for k, v in state.items()}
        now = time.time()
        self._timestamps: list[float] = [t for t in self._all.get(repo_id, []) if now - t < 3600.0]

    def _save(self) -> None:
        self._all[self._repo_id] = self._timestamps
        self._path.write_text(json.dumps(self._all, indent=2), encoding="utf-8")

    def wait_for_slot(self) -> None:
        window = 3600.0
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < window]
        if len(self._timestamps) >= self.max_per_hour:
            sleep_for = window - (now - self._timestamps[0]) + 1
            if sleep_for > 0:
                print(f"[publish] pacing: {len(self._timestamps)} commit(s) in the trailing hour "
                      f"(cap {self.max_per_hour}) — sleeping {sleep_for:.0f}s to stay under HF's rate "
                      f"limit", file=sys.stderr)
                time.sleep(sleep_for)
            now = time.time()
            self._timestamps = [t for t in self._timestamps if now - t < window]
        self._timestamps.append(now)
        self._save()


def _sha256_file(fp: Path) -> str:
    return hashlib.sha256(fp.read_bytes()).hexdigest()


def _publish_state_path(root: Path) -> Path:
    return root / ".publish_state.json"


def _retry_transient(fn, what: str, attempts: int = 3, base_delay: float = 3.0):
    """Retry transient network errors only; HfHubHTTPError (auth/rate-limit) propagates immediately —
    those need a human decision (re-login, wait out the hourly quota), not a few quick retries that
    would just re-trigger the same failure."""
    from huggingface_hub.errors import HfHubHTTPError
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except HfHubHTTPError:
            raise
        except Exception as e:
            last_exc = e
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"[publish] {what} failed ({type(e).__name__}: {e}) — retrying in {delay:.0f}s "
                     f"({attempt}/{attempts})", file=sys.stderr)
                time.sleep(delay)
    raise last_exc


def publish_chunked(root: Path, repo_id: str, files: list[str], create: bool, dry_run: bool,
                    chunk_size: int = HF_CHUNK_SIZE, label: str = "dataset",
                    detect_deletions: bool = True) -> None:
    """Push `files` (paths relative to `root`) to a HF dataset repo, in `chunk_size`-op commits.
    `.publish_state.json` (local, git-ignored, one per dataset root) caches the sha256 last successfully
    pushed per (repo, path) — a re-run only pushes what actually changed, and resumes after an
    interruption instead of re-uploading everything (state is saved after EVERY chunk succeeds, not just
    at the end, so a crash mid-run loses at most one chunk's progress).

    ALSO deletes (when `detect_deletions=True`, the default): any path this function has previously
    pushed (per `.publish_state.json`) that is no longer in `files` gets a `CommitOperationDelete` —
    otherwise a locally-removed file (e.g. a format redesign that drops a file kind) stays live on HF
    forever, since `CommitOperationAdd` alone only ever adds/updates, never removes. Only paths THIS
    function put there are ever candidates for deletion — anything HF added itself (`.gitattributes`)
    or that was never in the state cache is left alone, so this can't accidentally delete something
    outside its own bookkeeping.

    Pass `detect_deletions=False` when `files` is a DELIBERATE PARTIAL SCOPE (e.g. one language out of
    a whole catalog) rather than the full current state of `root` — otherwise every other language's
    already-published files (present in `.publish_state.json` but absent from this call's `files`,
    simply because they weren't part of this scoped call) would be misread as locally-removed and
    queued for deletion on HF."""
    print(f"[publish] {len(files)} file(s) under {root} → dataset '{repo_id}'", file=sys.stderr)
    if dry_run:
        print("[publish] dry-run — nothing pushed", file=sys.stderr)
        return
    try:
        from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi
        from huggingface_hub.errors import HfHubHTTPError
    except ImportError:
        raise SystemExit("[publish] needs huggingface_hub — pip install -e '.[publish]'")
    api = HfApi()
    try:
        _retry_transient(api.whoami, "whoami() check")
    except Exception as e:
        if isinstance(e, HfHubHTTPError) and e.response is not None and e.response.status_code in (401, 403):
            raise SystemExit("[publish] not authenticated — run `huggingface-cli login` or set HF_TOKEN") from e
        raise SystemExit(f"[publish] whoami() check failed ({type(e).__name__}: {e}) — likely transient "
                         f"(network/API blip); safe to just re-run") from e
    if create:
        api.create_repo(repo_id, repo_type="dataset", exist_ok=True)

    state_fp = _publish_state_path(root)
    state = json.loads(state_fp.read_text(encoding="utf-8")) if state_fp.exists() else {}
    repo_state = state.setdefault(repo_id, {})

    current = set(files)
    digests = {rel: _sha256_file(root / rel) for rel in files}
    changed = [rel for rel in files if repo_state.get(rel) != digests[rel]]
    stale = sorted(rel for rel in repo_state if rel not in current) if detect_deletions else []
    skipped = len(files) - len(changed)
    if not changed and not stale:
        print(f"[publish] 0 file(s) changed, 0 removed ({skipped} unchanged, cache-skipped) — no commit "
              f"needed for {repo_id}", file=sys.stderr)
        return

    limiter = _RateLimiter(root, repo_id)

    # deletions first (their own small commit — no reason to entangle with the add/update chunking)
    n_del_commits = 0
    if stale:
        del_chunk_size = _adaptive_chunk_size(len(stale), chunk_size)
        del_chunks = [stale[i:i + del_chunk_size] for i in range(0, len(stale), del_chunk_size)]
        n_del_commits = len(del_chunks)
        for n, chunk in enumerate(del_chunks, 1):
            ops = [CommitOperationDelete(path_in_repo=rel) for rel in chunk]
            msg = f"{label}: remove {len(chunk)} stale file(s) (batch {n}/{len(del_chunks)})"
            limiter.wait_for_slot()
            _retry_transient(
                lambda: api.create_commit(repo_id=repo_id, repo_type="dataset", operations=ops,
                                          commit_message=msg),
                f"create_commit (delete batch {n}/{len(del_chunks)})")
            for rel in chunk:
                del repo_state[rel]
            state_fp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"[publish] delete batch {n}/{len(del_chunks)} removed ({len(chunk)} file(s))",
                  file=sys.stderr)

    add_chunk_size = _adaptive_chunk_size(len(changed), chunk_size)
    chunks = [changed[i:i + add_chunk_size] for i in range(0, len(changed), add_chunk_size)]
    print(f"[publish] {len(changed)} file(s) changed, {len(stale)} removed ({skipped} unchanged, "
          f"cache-skipped) → {len(chunks)} add/update commit(s) of up to {add_chunk_size} file(s)"
          + (f" (scaled up from the {chunk_size} default to stay under ~{_TARGET_MAX_COMMITS} "
             f"commits)" if add_chunk_size > chunk_size else ""),
          file=sys.stderr)
    for n, chunk in enumerate(chunks, 1):
        ops = [CommitOperationAdd(path_in_repo=rel, path_or_fileobj=str(root / rel)) for rel in chunk]
        msg = f"{label}: batch {n}/{len(chunks)} ({len(chunk)} file(s))"
        limiter.wait_for_slot()
        try:
            _retry_transient(
                lambda: api.create_commit(repo_id=repo_id, repo_type="dataset", operations=ops,
                                          commit_message=msg),
                f"create_commit (batch {n}/{len(chunks)})")
        except HfHubHTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                # Shouldn't happen given the proactive pacing above — a real 429 here means something
                # OTHER than this run is also burning this repo's quota (a concurrent process/session).
                # Still recoverable the same way: state is saved after every successful chunk, so
                # re-running the same command later resumes from exactly here.
                state_fp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                raise SystemExit(
                    f"[publish] HIT HF COMMIT RATE LIMIT (429) after {n - 1}/{len(chunks)} batch(es) "
                    f"DESPITE self-pacing — likely another process/session sharing this repo's quota. "
                    f".publish_state.json records what actually succeeded; re-running later resumes "
                    f"from here. Wait ~1 hour, then re-run."
                ) from e
            raise
        for rel in chunk:
            repo_state[rel] = digests[rel]
        state_fp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[publish] batch {n}/{len(chunks)} pushed ({len(chunk)} file(s))", file=sys.stderr)
    print(f"[publish] done — {len(changed)} file(s) pushed, {len(stale)} removed, in "
          f"{len(chunks) + n_del_commits} commit(s) to {repo_id}", file=sys.stderr)
