"""Shared chunked-commit HF publisher — factored out of compact_align_batch.py so every dataset here
that needs to publish MANY files at once (not export_lex.py's one-language-at-a-time case, which is
small enough per call not to need this) uses the exact same, tested mechanism.

HF's commit-rate limit is 128/hour PER REPO, not per file — `upload_file()`-per-file (or one commit per
language, looped across e.g. 199 languages) blows past that trivially. The fix: bundle many files into
FEW commits via `CommitOperationAdd` + `create_commit()`, exactly as the Hub's own 429 error message
recommends ("upload entire folders at once"). Different repos have INDEPENDENT rate-limit budgets, so
publishing several datasets to their own repos in the same session never competes for the same 128.

    from lexeme_aligner.hf_bulk_publish import publish_chunked
    publish_chunked(root, repo_id, files, create=True, dry_run=False, chunk_size=500, label="my-dataset")
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path


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
                    chunk_size: int = 500, label: str = "dataset", detect_deletions: bool = True) -> None:
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

    # deletions first (their own small commit — no reason to entangle with the add/update chunking)
    n_del_commits = 0
    if stale:
        del_chunks = [stale[i:i + chunk_size] for i in range(0, len(stale), chunk_size)]
        n_del_commits = len(del_chunks)
        for n, chunk in enumerate(del_chunks, 1):
            ops = [CommitOperationDelete(path_in_repo=rel) for rel in chunk]
            msg = f"{label}: remove {len(chunk)} stale file(s) (batch {n}/{len(del_chunks)})"
            _retry_transient(
                lambda: api.create_commit(repo_id=repo_id, repo_type="dataset", operations=ops,
                                          commit_message=msg),
                f"create_commit (delete batch {n}/{len(del_chunks)})")
            for rel in chunk:
                del repo_state[rel]
            state_fp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"[publish] delete batch {n}/{len(del_chunks)} removed ({len(chunk)} file(s))",
                  file=sys.stderr)

    chunks = [changed[i:i + chunk_size] for i in range(0, len(changed), chunk_size)]
    print(f"[publish] {len(changed)} file(s) changed, {len(stale)} removed ({skipped} unchanged, "
          f"cache-skipped) → {len(chunks)} add/update commit(s) of up to {chunk_size} file(s)",
          file=sys.stderr)
    for n, chunk in enumerate(chunks, 1):
        ops = [CommitOperationAdd(path_in_repo=rel, path_or_fileobj=str(root / rel)) for rel in chunk]
        msg = f"{label}: batch {n}/{len(chunks)} ({len(chunk)} file(s))"
        try:
            _retry_transient(
                lambda: api.create_commit(repo_id=repo_id, repo_type="dataset", operations=ops,
                                          commit_message=msg),
                f"create_commit (batch {n}/{len(chunks)})")
        except HfHubHTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                state_fp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                raise SystemExit(
                    f"[publish] HIT HF COMMIT RATE LIMIT (429) after {n - 1}/{len(chunks)} batch(es) — "
                    f"HF caps commits at 128/hour per repo. Nothing was corrupted; "
                    f".publish_state.json records what actually succeeded, so re-running the same "
                    f"command later resumes from here. Wait ~1 hour, then re-run."
                ) from e
            raise
        for rel in chunk:
            repo_state[rel] = digests[rel]
        state_fp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[publish] batch {n}/{len(chunks)} pushed ({len(chunk)} file(s))", file=sys.stderr)
    print(f"[publish] done — {len(changed)} file(s) pushed, {len(stale)} removed, in "
          f"{len(chunks) + n_del_commits} commit(s) to {repo_id}", file=sys.stderr)
